"""
Download and cache FLCAC data packages via the public API

FLCAC API docs: https://www.lcacommons.gov/lca-commons-api-guide
"""
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from urllib.parse import urlencode, urljoin

import requests
import yaml
from dotenv import dotenv_values

from ldpm.utils import PATHS, find_file


yaml.SafeDumper.add_representer(
    defaultdict, yaml.representer.Representer.represent_dict)

log = logging.getLogger(__name__)


def _nested_dict() -> defaultdict:
    return defaultdict(_nested_dict)

@dataclass
class APIClientSession:
    """Container for managing requests sessions and configs"""
    session: requests.Session = field(default_factory=requests.Session)
    headers: dict = field(default_factory=dict)
    timeout: float = 10.0
    url_base: str = field(init=False)
    api_key: str | None = field(init=False)
    
    def __post_init__(self) -> None:
        self.session.headers.update(self.headers)
        
        # attempt to read .env secrets, with .env.example as fallback
        try:
            env_file_path = find_file('.env')
        except FileNotFoundError:
            env_file_path = find_file('.env.example')

        secrets = dotenv_values(env_file_path)

        if secrets['API_KEY']:
            self.api_key = secrets['API_KEY']
            self.url_base = 'https://api.nal.usda.gov/FederalLCACommonsapi/'
        else:
            self.api_key = None
            self.url_base = 'https://www.lcacommons.gov/lca-collaboration/ws/public/'
         
    def get(self, endpoint: str, params: dict| None = None, **kwargs) -> bytes:
        """Submit GET request to FLCAC API"""
        url = self._build_get_url(url_path=endpoint, params=params)
        response = self.session.get(url, timeout=self.timeout, **kwargs)
        response.raise_for_status()
        return response
        
    def _build_get_url(self, url_path: str, params: dict | None = None) -> str:
        """Build a valid GET request URL for the FLCAC API"""
        url_base_path = urljoin(self.url_base, url_path.strip('/'))
        if not (params or self.api_key):
            return url_base_path
        else:
            if params is None:
                params = {}
            if self.api_key:
                params.update({'api_key': self.api_key})
            query = urlencode(params)
            return f'{url_base_path}?{query}'

    def compile_dpkg_release_index(self) -> None:
        """
        Fetch all available dpkgs and release versionss thereof via FLCAC API, 
        then write metadata to ./config/data_packages.yaml
        """
        response = self.get(endpoint='repository')  # list public dpkg repos
        metadata_dpkgs = response.json()
        
        index = _nested_dict()
        for dpkg in sorted(metadata_dpkgs, 
                           key=lambda d: (d['group'].lower(), d['name'].lower())):
            group, name = dpkg['group'], dpkg['name']
            if not dpkg['hasReleases']:
                log.warning(f'Data package has no releases: {dpkg["name"]}')
                index[dpkg['group']][dpkg['name']] = ''
            else:
                response = self.get(f'history/{group}/{name}')  # list all releases
                dpkg_releases = response.json()
                for release in dpkg_releases:  # already ordered newest-to-oldest
                    info = release['releaseInfo']
                    index[dpkg['group']][dpkg['name']][info['version']] = info['commitId']
        
        with (PATHS.config / 'data_packages.yaml').open('w') as _file:
            yaml.safe_dump(index, _file, sort_keys=False, indent=4)


SESSION = APIClientSession()

# compile and load index of available dpkgs
if not (PATHS.config / 'data_packages.yaml').exists():
    SESSION.compile_dpkg_release_index()
    # TODO: trigger re-compilation if API has new release(s), ignoring preexistence of YAML
    # ???: implement as set of DataPackage objs?
with (PATHS.config / 'data_packages.yaml').open() as _file:
    INDEX_DPKG = yaml.safe_load(_file)   

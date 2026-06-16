"""
Download and cache FLCAC data packages via the public API; deduplicate by UUID;
overwrite .Ref attributes to link across dpkgs; then merge into a single .ZIP

# Glossary
- build: the database (DB) being assembled from a manifest
- data package (dpkg): a collection of olca-schema data objects stored in an FLCAC repo
- dependency: a dpkg specified as a component of the build 
- duplicate: an object with multiple instances across dpkgs, as identified by UUID
- manifest: a TOML containing build metadata and dependencies—the structured list 
            of specifiers (`<alias> = "<version>"`) that define which dpkgs to
            fetch and integrate into the build

FLCAC API docs: https://www.lcacommons.gov/lca-commons-api-guide

Rules for deduplicating same-UUID objects:
    1. Import dict of {original/parent dpkg:uuid_child} pairs from deduplicate.yaml;
       ignore all instances of uuid_child that appear in other dpkgs
       > Note: FEDEFL is parent for all elem. flows, and USEEIO for all tech. flows it contains
    2. Then, defer to order of entries in manifest TOML's [dependencies] list
"""

import json
import logging
import sys
import tomllib
import zipfile as zf
from collections import defaultdict
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode, urljoin

import requests
import yaml
from dotenv import dotenv_values
from platformdirs import user_data_dir


yaml.SafeDumper.add_representer(
    defaultdict, yaml.representer.Representer.represent_dict)

PATH_SCRIPT = Path(__file__).parent
PATH_CONFIG = PATH_SCRIPT / 'config'

PATH_CACHE = Path(user_data_dir(appauthor='FLCAC', appname='uslci+'))  # ~/FLCAC/uslci+
PATH_CACHE.mkdir(parents=True, exist_ok=True)

PATH_OUT = PATH_SCRIPT / 'output'
PATH_OUT.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    filename=(PATH_OUT / 'app.log'),
    format = "%(asctime)s [%(levelname)s] %(name)s:%(lineno)s %(message)s",
    # datefmt='%H:%M:%S',
    datefmt='%y-%m-%d %H:%M:%S',
    encoding='utf-8',
    level=logging.DEBUG,
    )
log = logging.getLogger(__name__)


# %% initialize other globals
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

        secrets = dotenv_values(PATH_SCRIPT / '.env')
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
        
        with (PATH_CONFIG / 'data_packages.yaml').open('w') as _file:
            yaml.safe_dump(index, _file, sort_keys=False, indent=4)


SESSION = APIClientSession()

# compile and load index of available dpkgs
if not (PATH_CONFIG / 'data_packages.yaml').exists():
    SESSION.compile_dpkg_release_index()
    # TODO: trigger re-compilation if API has new release(s), ignoring preexistence of YAML
with (PATH_CONFIG / 'data_packages.yaml').open() as _file:
    INDEX_DPKG = yaml.safe_load(_file)

# TODO: migrate INDEX_DPKG compilation & I/O to its own module
    # then import singleton instance above
    # ???: implement as set of DataPackage objs?

# TODO: same module-singleton pattern for APIClientSession


# %%
@dataclass(frozen=True)  # unsafe_hash=True allows hashing w/o immutability
class DataPackage:
    name: str
    version: str
    group: str = field(init=False, repr=False)
    path_zip: Path = field(init=False, repr=False)
    commitId: float = field(init=False, repr=False)
    
    def __post_init__(self) -> None:        
        try:
            groups = [group for group, dpkgs in INDEX_DPKG.items()
                      if self.name in dpkgs.keys()]
            _group, = groups
            dependent_attributes = {'group': _group}
        except ValueError:
            if len(groups) > 1:
                log.exception(f'Multiple groups contain a dpkg named "{self.name}";'
                              ' contact FLCAC admin to fix.')
            raise
        _version = self.version
        if _version == "*":  # replace w/ latest version
            _version = next(iter(INDEX_DPKG[_group][self.name]))
            dependent_attributes.update({'version': _version})
        dependent_attributes.update({
            'path_zip': PATH_CACHE / f'{self.name}--v{_version}.zip',
            'commitId': INDEX_DPKG[_group][self.name][_version],
            })
        for attr, value in dependent_attributes.items():
            object.__setattr__(self, attr, value)

    def __str__(self) -> str:
        return f'{self.name}--v{self.version}'
        
    @property
    def zipfile(self, mode: str = 'r') -> zf.ZipFile:
        return zf.ZipFile(self.path_zip, mode)
    
    def iter_zip_jsons(self, dedup: dict = None) -> (zf.ZipInfo, bytes):
        """
        Generate an iterable over the JSON-LD files within the dpkg .zip archive, 
        ignoring non-JSON, binary, and root-level files.
        
        Args:
            dedup: deduplication index, as compiled by compile_dedup_and_ref_indices()
        """
        if not dedup:
            dedup = {}
        objs_ignore = dedup.get(self.name, {})
        with self.zipfile as z:
            for metadata in z.infolist():
                file_rpath = metadata.filename  # str, relative file path w/i .zip
                if (not file_rpath.endswith('.json')
                    or file_rpath.startswith('bin/')
                    or file_rpath in objs_ignore
                    or file_rpath in ['categories.json', 'openlca.json']
                    ): continue
                with z.open(file_rpath, 'r') as file_stream:
                    yield metadata, file_stream
    
    def fetch(self) -> None:
        """Check local cache for a data package; download if absent"""
        if self.path_zip.exists():
            log.info(f'Data package already cached:\n\t{self.path_zip.as_posix()}')
        else:
            try:
                # ask server to generate unique token, indexing dpkg for download
                token = (SESSION.get(f'download/json/prepare/{self.group}/{self.name}',
                                     params={'commitId': self.commitId})
                                .content.decode().strip())
                # download dpkg via the token
                dpkg_bytes = SESSION.get(f'download/json/{token}').content
            except Exception as e:
                log.exception(f'Bad download of {self.name}: {e}')
                raise
            with self.path_zip.open('xb') as f:
                f.write(dpkg_bytes)
                log.info(f'Downloaded and cached:\n\t{self.path_zip.as_posix()}')
            

@dataclass(frozen=True)
class Manifest:
    build_name: str
    build_version: str
    dependencies: set[DataPackage] = field(default_factory=set)
    dependencies_indirect: set[DataPackage] = field(default_factory=set, init=False)
    build_datetime: datetime = field(default_factory=datetime.now)
    _core_dpkg_names: set = field(
        init=False, 
        repr=False, 
        default_factory=lambda: {
            'elementary_flow_list',  # elem. flows
            'Fed_Commons_core_database',  #  non-process objs.
            'USEEIO_v2',  # tech. flows
        })

    @classmethod
    def from_toml(cls, file_name: str = 'manifest.toml'):  # -> Manifest
        try:
            file_paths = PATH_SCRIPT.rglob(file_name)
            file_path, = file_paths
        except ValueError:
            if len(file_paths) == 0:
                log.exception(f'No "{file_name}" file detected in the repo.')
            elif len(file_paths) > 1:
                log.exception(f'Multiple "{file_name}" files detected in the repo; '
                             'please use unique manifest TOML file names.')
            raise
        with (file_path).open('rb') as f:
            _manifest = tomllib.load(f)
        return cls(
            build_name=_manifest['build']['name'],
            build_version=_manifest['build']['version'],
            dependencies={DataPackage(name, version) for name, version 
                          in _manifest['dependencies'].items()},
            )
    
    def __post_init__(self) -> None:
        dpkgs_available = {name: tuple(version for version in version_subdict.keys())
                           for name_subdict in INDEX_DPKG.values() 
                           for name, version_subdict in name_subdict.items()}
        for dpkg in self.dependencies:
            if dpkg.name not in dpkgs_available:
                error_msg = f'No data package named "{self.name}" is available, per data_packages.yaml.'
                log.error(error_msg)
                raise KeyError(error_msg)
            
            # if dpkg.version == "*":  # replace w/ latest version
            #     dpkg.version = dpkgs_available[dpkg.name][0]
                
            elif dpkg.version not in dpkgs_available[dpkg.name]:
                error_msg = f'Version "{self.version}" of data package "{self.name}" unavailable.'
                log.error(error_msg)
                raise KeyError(error_msg)
        """
        An 'indirect dependency' contains objects used across most/all other 
        dpkgs on the FLCAC; including them in Manifest.dependencies is optional; 
        and omission from Manifest defaults to using the latest version
        """
        dpkgs_indirect = {DataPackage(name, dpkgs_available[name][0])
                          for name in self._core_dpkg_names}
        dependencies_indirect = dpkgs_indirect - self.dependencies
        object.__setattr__(self, 'dependencies_indirect', dependencies_indirect)

    @property
    def dependencies_all(self) -> set[DataPackage]:
        return self.dependencies | self.dependencies_indirect
    
    # @property
    # def index_dependencies_core(self) -> dict(str, DataPackage):
    #     """The set of core build dependencies, indexed by .name"""
    #     return {dpkg for dpkg in self.dependencies_all
    #             if dpkg.name in self._core_dpkg_names}
    
    def __str__(self) -> str:
        dpkgs_as_str = lambda dpkgs: ',\n\t'.join(str(dpkg) for dpkg in dpkgs)
        str_deps_direct = dpkgs_as_str(self.dependencies)
        str_deps_indirect = dpkgs_as_str(self.dependencies_indirect)
        return (f'Build: {self.build_name}, v{self.build_version}\n'
                f'Dependencies, direct:\n\t{str_deps_direct}\n'
                f'Dependencies, indirect:\n\t{str_deps_indirect}')
    
    def fetch_dependencies(self) -> None:
        # TODO: add command-line progress bar
        for dpkg in self.dependencies_all: 
            dpkg.fetch()


def compile_dedup_and_ref_indices(manifest: Manifest) -> (dict, dict):
    """
    Import deduplicate.yaml and transform it from  
    (1) a dict of {parent/original dpkg: UUIDs duplicated in other dpkgs}, to
    (2) a dict of {other dpkg: UUIDs to ignore, as .zip-relative file paths}
    
    Import update_Refs.yaml, drop top-level keys, and convert process UUIDs to 
    .zip-relative file paths
    """
    with (PATH_CONFIG / "deduplicate.yaml").open() as f:
        dedup_orig = yaml.safe_load(f)
    
    dpkgs_build = {dpkg.name for dpkg in manifest.dependencies}
    if dpkgs_build > dedup_orig.keys():
        log.warning('One or more manifest dependencies lack deduplicate.yaml entries:'
                    f'\n{dpkgs_build - dedup_orig.keys()}')    
    dedup = defaultdict(set)
    for dpkg, uuids_by_type in dedup_orig.items():
        for _type, uuids in uuids_by_type.items():
            if uuids is not None:
                for dpkg_other in (dedup_orig.keys() - {dpkg}):
                    dedup[dpkg_other].update(
                        {f'{_type}/{uuid}.json' for uuid in uuids})
    
    with (PATH_CONFIG / "update_Refs.yaml").open() as f:
        ref_updates = {}
        for dpkg, sub_dict in yaml.safe_load(f).items():
            if dpkg in dpkgs_build:
                ref_updates |= sub_dict
        ref_updates = {f'processes/{k}.json': v for k, v in ref_updates.items()}
    return dict(dedup), ref_updates


def extract_values(obj: dict | list, target_key: str) -> list:
    """
    Recursively extract values from every instance of 'target_key' in a dict
    """
    values = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key == target_key:
                values.append(value)
            values.extend(extract_values(value, target_key))
    elif isinstance(obj, list):
        for item in obj:
            values.extend(extract_values(item, target_key))
    return values


def update_refs(data: zf.ZipExtFile, process_ref_updates: dict) -> str:
    """
    Alter exchange.flow and/or .defaultProvider linkages according to the
    instructions for that process, as embedded in update_Refs.yaml.
    
    Args:
        data: a file pointer for an olca.Process JSON file
    Returns:
        olca.Process with updated Ref links, as single-line JSON (string)
    """
    # TODO: reorganize update_Refs.yaml to {Exchange.flow.@id: {flow.@id: ..., processes: [process.@id, ...]}}
        # to minimize duplication (and anchor/alias editing) across N processes requiring same updates
        # Then invert on import to {process.@id: {Exchange.flow.@id: {<alterations>}}
    # !!!: consider olca-schema objs for cleaner .attribute indexing
    process = json.load(data)
    for exchange_flow_uuid, updates in process_ref_updates.items():
        # update N exchanges using same flow, or a unique exchange if 'use_internalId'
        # if isinstance(updates, dict):
        target_exchanges = [exchange for exchange in process['exchanges']
                            if (exchange['flow']['@id'] == exchange_flow_uuid
                                and exchange['isInput'])]
        for exchange in target_exchanges:
            if isinstance(updates, list) and extract_values(updates, 'use_internalId'):
                update, = [u for u in updates 
                           if u['use_internalId'] == exchange['internalId']]
            else:
                update = updates
            if 'flow.@id' in update:
                flow = exchange['flow']
                del flow['name'], flow['category']
                flow['@id'] = update['flow.@id']
            if 'defaultProvider' in update:
                if 'defaultProvider' in exchange:
                    provider = exchange['defaultProvider']
                    del provider['name'], provider['category']
                    provider['@id'] = update['defaultProvider']
                else:  # add a provider where absent
                    exchange.update(
                        {'defaultProvider': {'@id': update['defaultProvider']}})
    return json.dumps(process,
                      separators=(',', ':'),  # no trailing whitespace
                      ensure_ascii=False)

# %%
def build_db(
    manifest: Manifest,
    dedup: dict,
    ref_updates: dict,
):
    """
    Merge the set of objects from dpkg dependencies listed in the manifest TOML 
    into USLCI+, while (1) avoiding duplicated-UUID collissions, and (2) 
    overwriting Ref pointers embedded in select objects to facilitate inter-dpkg
    linking and seamless DB import + usage in openLCA Desktop.
    """
    manifest.fetch_dependencies()
    
    PATH_OUT_ZIP = PATH_OUT / f'{manifest.build_name}_v{manifest.build_version}.zip'
        
    dedup, ref_updates = compile_dedup_and_ref_indices(manifest)
    
    # compile relative file paths of bridge processes to ignore:
    bridges_drop = [f'processes/{uuid}.json' for uuid in 
                    extract_values(ref_updates, 'drop_bridge')]
    
    # substitute FEDEFL & USEEIO flows for duplicates appearing in other dpkgs
    # wherever one or both are used as indirect dependencies
    def _get_dpkg_flows(dpkg: DataPackage) -> dict:
        with dpkg.zipfile as z:
            return {metadata.filename: metadata for metadata in z.infolist()
                    if metadata.filename.startswith('flows/') 
                    and metadata.filename.endswith('.json')}

            # return {f for f in z.namelist() 
            #         if f.startswith('flows/') and f.endswith('.json')}
    
    flows_fedefl_elem, flows_useeio_tech = (dict(), dict())
    for dpkg in manifest.dependencies_indirect:
        match dpkg.name:
            case 'elementary_flow_list':
                flows_fedefl_elem = _get_dpkg_flows(dpkg)
                zip_fedefl = dpkg.zipfile
            case 'USEEIO_v2':
                flows_useeio_tech = _get_dpkg_flows(dpkg)
                zip_useeio = dpkg.zipfile
        
    if flows_useeio_tech and flows_fedefl_elem:
        for flow in flows_fedefl_elem.keys():
            flows_useeio_tech.pop(flow, None)
    
    # TODO: ensure flows_fedefl_elem always deduplicate other dpkgs if FEDEFL
        # is specified as a direct dependency
    with (
        zf.ZipFile(PATH_OUT_ZIP, 'w') as zip_build,
        zip_fedefl if flows_fedefl_elem else nullcontext(),
        zip_useeio if flows_useeio_tech else nullcontext(),
    ):
        files_written = set()
        for dpkg in manifest.dependencies:
            # copy JSONs from dpkg to build .ZIP
            for metadata, file_stream in dpkg.iter_zip_jsons(dedup=dedup):
                file_rpath = metadata.filename  # str, relative file path w/i .zip
                if file_rpath in files_written:
                    if not file_rpath.startswith(('processes/', 'flows/')):
                        log.debug('Duplicate @type not addressed by deduplicate.yaml'
                                  f'\n\t{dpkg.name}\n\t{file_rpath}')
                    else:
                        log.warning('Duplicate not yet addressed by deduplicate.yaml:'
                                    f'\n\t{dpkg.name}\n\t{file_rpath}')
                # On-the-fly deduplication of objects from core dpkgs:
                elif file_rpath in flows_fedefl_elem:
                    zip_build.writestr(flows_fedefl_elem[file_rpath], 
                                       zip_fedefl.read(file_rpath))
                    files_written.add(file_rpath)
                elif file_rpath in flows_useeio_tech:
                    zip_build.writestr(flows_useeio_tech[file_rpath], 
                                       zip_useeio.read(file_rpath))
                    files_written.add(file_rpath)
                elif file_rpath in bridges_drop:
                    log.debug(f'Dropped bridge:\n\t{dpkg.name}\n\t{file_rpath}')
                    pass
                else:
                    try:
                        if file_rpath in ref_updates:
                            data = update_refs(file_stream, ref_updates[file_rpath])
                        else:
                            data = file_stream.read()
                        zip_build.writestr(metadata, data)
                        files_written.add(file_rpath)
                    except:
                        log.exception(f'Bad write for {file_rpath} from {dpkg.name}')
                        raise
                    
    log.info(f'\nWrote combined DB: {PATH_OUT_ZIP}')
        

def main() -> int:    
    manifest = Manifest.from_toml()
    dedup, ref_updates = compile_dedup_and_ref_indices(manifest)
    build_db(manifest, dedup, ref_updates)
    return 0


# %%
if __name__ == '__main__':
    sys.exit(main())
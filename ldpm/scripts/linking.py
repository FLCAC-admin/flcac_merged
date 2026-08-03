"""
Compile bridge processes into YAML template for update_Refs.yaml

FLCAC API docs: https://www.lcacommons.gov/lca-commons-api-guide
"""
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote

import requests
import yaml


yaml.SafeDumper.add_representer(
    defaultdict, yaml.representer.Representer.represent_dict)

PATH_SCRIPT = Path(__file__).parent

# Base URLs of FLCAC API & whole-dpkg-download endpoints
BASE_URL = 'https://www.lcacommons.gov/lca-collaboration/ws/public'
BASE_URL_SEARCH = f'{BASE_URL}/search?'
BASE_URL_OBJ_DOWNLOAD = f'{BASE_URL}/browse' # /{group}/{repo}/{type}/{refId}

SESSION = requests.Session()

# %%
def _nested_dict():
    return defaultdict(_nested_dict)


def get_request_FLCAC_API(url):
    r = SESSION.get(url)
    r.raise_for_status()
    return r.json()


def get_FLCAC_object(
    object_id: str,
    olca_type: str,
    repo: str,
    group: str,
) -> dict:
    # ~/browse/NIST/Building_Systems/PROCESS/f3929bea-4d5f-4691-9aef-aa95392ba015
    url = f'{BASE_URL_OBJ_DOWNLOAD}/{group}/{repo}/{olca_type}/{object_id}'
    _object = get_request_FLCAC_API(url)
    return _object


def search_FLCAC(
    query: str,
    pages: int = 1,
    page_size: int = 100,
    olca_type: str | None = None,
    repository_id: str | None = None,
    group: str | None = None,
) -> dict:
    """
    Full-text search of FLCAC API for data objects
    
    docs: https://usda-ree-ars.github.io/lca-api-doc/#/Search/searchDatasets
    """
    parameters = [
        f'query={quote(query)}', 
        f'page={pages}', 
        f'pageSize={page_size}',
        ]
    if olca_type:
        parameters.append(f'type={olca_type}')
    if repository_id:
        parameters.append(f'repositoryId={repository_id}')
    if group:
        parameters.append(f'group={group}')
    
    url = BASE_URL_SEARCH + '&'.join(parameters)
    content = get_request_FLCAC_API(url)
    info = content['resultInfo']
    if (info['totalCount'] > info['pageSize'] or
        info['pageCount'] > pages):
        print(f'WARNING: {info["totalCount"]} objects returned across {info["pageCount"]} pages',
              f'but only {page_size} (page_size) * {pages} (pages) = {page_size * pages} were requested.')
    object_metadata = content['data']
    return object_metadata


def assemble_bridge_dict(
    query: str,
    group: str,
    repository_id: str | None = None,
    **kwargs
) -> dict:
    """
    For a given group or dpkg/repo therein, find the available bridge processes
    therein, and assemble an incomplete YAML template for update_Refs.yaml:
    {dpkg: {process.@id: {input_exchange.flow.@id: ''}}}
    """
    process_metadata = search_FLCAC(query, **{**kwargs, 'olca_type': 'PROCESS'})
    
    update_refs = _nested_dict()
    for process_meta in process_metadata:
        # version_target, = [v for v in process_meta['versions'] if 'group']
        _path, = [v['repos'][0]['path'] for v in process_meta['versions']
                 if v['repos'][0]['group'] == group]
        group, dpkg = _path.split('/')
        # _, dpkg = process_meta['versions'][0]['repos'][0]['path'].split('/')
        process_id = process_meta['refId']
        if repository_id and (not repository_id == dpkg):
            print(f'Process belongs to a different dpkg than requested:'
                  f'\n\tProcess:  {process_id}'
                  f'\n\tParent dpkg: {dpkg}'
                  f'\n\tTarget dpkg: {repository_id}')
            pass
        else:
            process = get_FLCAC_object(process_id, 'PROCESS', dpkg, group)
        
        for exchange in process['exchanges']:
            if exchange['isInput'] and exchange['flow']['flowType'] == 'PRODUCT_FLOW':
                flow_id = exchange['flow']['@id']
                if 'to USEEIO' in process['name']:
                    update_refs[dpkg][process_id][flow_id] = '<choose USEEIO flow>'
                else:
                    update_refs[dpkg][process_id][flow_id] = None
            # TODO: waste flow handling
    return update_refs


def write_bridges_to_yaml(
    _dict: dict,
    file_name: str = 'update_Refs_template.yaml',
):
    with (PATH_SCRIPT / file_name).open('w') as _file:
        yaml.safe_dump(_dict, _file, sort_keys=False, indent=2)


def main() -> int:
    query_NIST = '"Building Systems to"|"Construction Materials to"'
    parameters_NIST = {
        'olca_type': 'PROCESS',
        'group': 'NIST',
        }
    bridges_NIST = assemble_bridge_dict(query_NIST, **parameters_NIST)
    write_bridges_to_yaml(bridges_NIST)
    return 0
    

# %%
if __name__ == '__main__':
    sys.exit(main())



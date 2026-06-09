"""
Download and cache FLCAC data packages via the public API, deduplicate by UUID, 
then merge into a single .ZIP

# Glossary
- build: the database (DB) being assembled from a manifest 
    > now: USLCI+ via DATA_PACKAGES_BUILD; later: arbitrary DB
- data package (dpkg): a collection of olca-schema data objects stored in an FLCAC repo
- dependency: a dpkg specified as a component of the build 
- duplicate: the same object appears in >1 dependency, 
             as identified by UUID or other matching criteria
- manifest: the structured list of dependency specifiers {alias, version, etc.}, 
            where each entry is used to uniquely identify and locate/fetch a dpkg

FLCAC API docs: https://www.lcacommons.gov/lca-commons-api-guide

Rules for deduplicating same-UUID objects:
    1. Import dict of {original/parent dpkg:uuid_child} pairs from deduplicate.yaml
      then ignore all instances of uuid_child that appear in other dpkgs
      > Note: FEDEFL is parent for all elem. Flows; USEEIO for all tech. Flows it contains
    2. Then, defer to order of entries in DATA_PACKAGES_BUILD
"""

import json
import logging
import sys
import tomllib
import zipfile as zf
from collections import defaultdict
from pathlib import Path

import requests
import yaml
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
    format = "%(asctime)s [%(levelname)8s] %(name)s:%(lineno)s %(message)s",
    datefmt='%H:%M:%S',
    encoding='utf-8',
    level=logging.DEBUG,
    )
log = logging.getLogger(__name__)

# Base URLs of FLCAC API & whole-dpkg-download endpoints
BASE_URL = 'https://www.lcacommons.gov/lca-collaboration/ws/public'
BASE_URL_DOWNLOAD = f'{BASE_URL}/download/json'

SESSION = requests.Session()

# TODO: support optional specification of version identifier in each dpkg tuple,
    # where omission defaults to latest release
DATA_PACKAGES_CORE: set(tuple(str, str)) = {
    ('Federal_LCA_Commons', 'Fed_Commons_core_database'),
    ('Federal_LCA_Commons', 'elementary_flow_list'),
    ('US_Environmental_Protection_Agency', 'USEEIO_v2'),
}

# Choose data packages to download: (group, dpkg), as specified in repo URL
DATA_PACKAGES_BUILD: set(tuple(str, str)) = {
    ('National_Renewable_Energy_Laboratory', 'USLCI_Database_Public'),
    ('Federal_LCA_Commons', 'US_electricity_baseline'),
    ('US_Forest_Service_Forest_Products_Lab', 'Woody_biomass'),
    ('US_Environmental_Protection_Agency', 'USEEIO_v2'),
}


# %%
def _nested_dict():
    return defaultdict(_nested_dict)


def compile_dpkg_release_index():
    """
    Fetch all available dpkgs and releases thereof via FLCAC API, then write 
    metadata to ./config/data_packages.yaml
    """
    url = f'{BASE_URL}/repository'  # list public dpkg repos
    r = SESSION.get(url)
    r.raise_for_status()
    metadata_dpkgs = r.json()
    
    index = _nested_dict()
    for dpkg in sorted(metadata_dpkgs, 
                       key=lambda d: (d['group'].lower(), d['name'].lower())):
        group, name = dpkg['group'], dpkg['name']
        if not dpkg['hasReleases']:
            log.warning(f'Data package has no releases: {dpkg["name"]}')
            index[dpkg['group']][dpkg['name']] = ''
        else:
            url = f'{BASE_URL}/history/{group}/{name}'  # list all releases
            r = SESSION.get(url)
            r.raise_for_status()
            dpkg_releases = r.json()
            for release in dpkg_releases:  # already ordered newest-to-oldest
                info = release['releaseInfo']
                index[dpkg['group']][dpkg['name']][info['version']] = info['commitId']
    
    with (PATH_CONFIG / 'data_packages.yaml').open('w') as _file:
        yaml.safe_dump(index, _file, sort_keys=False, indent=4)


# compile and load index of available dpkgs
if not (PATH_CONFIG / 'data_packages.yaml').exists():
    compile_dpkg_release_index()

with (PATH_CONFIG / 'data_packages.yaml').open() as _file:
    INDEX_DPKG = yaml.safe_load(_file)


# %%
def import_manifest(
        file_path: Path = PATH_CONFIG / 'manifest.toml',
        ) -> dict:
    with (file_path).open('rb') as f:
        manifest = tomllib.load(f)
        # TODO: validate manifest against available versions in data_packages.yaml
        # TODO: define DATA_PACKAGES_BUILD via manifest and DATA_PACKAGES_CORE via index
        # TODO: accept optional commitID query param for download endpoint
          # where dependency (dpkg, version) gets commitId value from dpkg index
          # ?commitId=abc...123
        return manifest


def prepare_download_token(
        group: str, 
        dpkg: str,
        commitId: str | None = None,
        ) -> str:
    """ 
    Ask the server to prepare a data package for download, for which it 
    provides a unique token to get the content.
    """
    if not commitId:
        url = f'{BASE_URL_DOWNLOAD}/prepare/{group}/{dpkg}'
    else:
        url = f'{BASE_URL_DOWNLOAD}/prepare/{group}/{dpkg}?commitId={commitId}'
    r = SESSION.get(url)
    r.raise_for_status()
    token = r.json()
    if not token:
        log.error(f'Empty token for {group}/{dpkg}; response={r.text[:200]}')
        raise RuntimeError
    return token


def download_dpkg(
        group: str, 
        dpkg: str,
        commitId: str | None = None,
                  ):
    """
    Download and cache the prepared data package via the token.
    
    Args:
        group: alias for FLCAC group that owns the data package, lcacommons.gov URL fragment
        dpkg: data package name
    Returns:
    """
    if not commitId:
        # TODO: match {group, dpkg} to latest version in INDEX
        path_zip = PATH_CACHE / f'{dpkg}.zip'  
        # TODO: instead, use latest release version
    else:
        # TODO: match {group, dpkg, commitId} to a version in INDEX
        # version = INDEX[group][dpkg]
        version = 'foo'
        path_zip = PATH_CACHE / f'{dpkg}--{version}.zip'  

    if not path_zip.exists():
        log.info(f'Preparing {group}/{dpkg}')
        try:    
            token = prepare_download_token(group, dpkg)
            r = SESSION.get(f'{BASE_URL_DOWNLOAD}/{token}')
            r.raise_for_status()
            log.info(f'\tDownloaded: {len(r.content):,} bytes')
        except Exception as e:
            log.exception(f'\tERROR downloading {group}/{dpkg}: {e}')
            raise
        with path_zip.open('xb') as f:
            f.write(r.content)
            log.info(f'Downloaded and cached:\n\t{path_zip.as_posix()}')
    else:
        log.info(f'Data package already cached:\n\t{path_zip.as_posix()}')


def compile_dedup_and_ref_indices() -> (dict, dict):
    """
    Import deduplicate.yaml and transform it from  
    (1) a dict of {parent/original dpkg: UUIDs duplicated in other dpkgs}, to
    (2) a dict of {dpkg: .zip-relative file paths of JSONs to ignore in dpkg}
    """
    with (PATH_CONFIG / "deduplicate.yaml").open() as f:
        dedup_orig = yaml.safe_load(f)
    
    dpkgs_build = {dpkg for (group, dpkg) in DATA_PACKAGES_BUILD}    
    if not dedup_orig.keys() <= dpkgs_build:
        log.error('One or more data packages listed as top-level key in'
                  'deduplicate.yaml is not available in DATA_PACKAGES_BUILD:\n'
                  f'{dedup_orig.keys() - dpkgs_build}')
        raise KeyError
    else:
        dedup = defaultdict(set)
        for dpkg, uuids_by_type in dedup_orig.items():
            for _type, uuids in uuids_by_type.items():
                for dpkg_other in (dedup_orig.keys() - {dpkg}):
                    if uuids is not None:
                        dedup[dpkg_other].update(
                            {f'{_type}/{uuid}.json' for uuid in uuids})
    with (PATH_CONFIG / "update_Refs.yaml").open() as f:
        ref_updates = {}
        for dpkg, sub_dict in yaml.safe_load(f).items():
            if dpkg in dpkgs_build:
                ref_updates |= sub_dict
        ref_updates = {f'processes/{k}.json': v for k, v in ref_updates.items()}
    return dict(dedup), ref_updates


def iter_dpkg_zip_jsons(dpkg: str, 
                        dedup: dict = {},
) -> (zf.ZipInfo, bytes):
    """
    Generate an iterable over the JSON-LD items within a dpkg .zip archive, 
    ignoring non-JSON, binary, and root-level files.
    
    Args:
        dpkg: data package name
        dedup: deduplication index, as compiled by compile_dedup_and_ref_indices()
    Returns:
        Iterable of JSON (file metadata, contents) tuples, to be conditionally 
        mergeed into output .zip by build_uslci_plus()
    """
    objs_ignore = dedup.get(dpkg, {})
    with zf.ZipFile(PATH_CACHE / f'{dpkg}.zip', 'r') as z:
        # zip_path = zf.Path(z)
        for metadata in z.infolist():
            # file_path = zip_path / metadata.filename
            file_path = metadata.filename  # str, file path within .zip
            if (not file_path.endswith('.json')
                or file_path.startswith('bin/')
                or file_path in objs_ignore
                or file_path in ['categories.json', 'openlca.json']
                ): continue
            with z.open(file_path, 'r') as content:
                yield metadata, content


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


def update_refs(data: bytes,
                process_ref_updates: dict,
) -> str:
    """
    Alter exchange.flow and/or .defaultProvider linkages according to the
    instructions for that process, as embedded in update_Refs.yaml.
    
    Args:
        data: bytes of a process JSON
    Returns:
        process with updated Ref links, as single-line JSON 
    """
    # TODO: reorganize update_Refs.yaml to {Exchange.flow.@id: {flow.@id: ..., processes: [process.@id, ...]}}
        # to minimize duplication (and anchor/alias editing) across N processes requiring same updates
        # Then invert on import to {process.@id: {Exchange.flow.@id: {<alterations>}}
    # !!!: consider olca-schema objs for cleaner .attribute indexing
    process = json.loads(data)
    for exchange_flow_id_old, updates in process_ref_updates.items():
        # update N exchanges using same flow, or a unique exchange if 'use_internalId'
        # if isinstance(updates, dict):
        target_exchanges = [exchange for exchange in process['exchanges']
                            if (exchange['flow']['@id'] == exchange_flow_id_old
                                and exchange['isInput'])]
        for exchange in target_exchanges:
            if isinstance(updates, list) and extract_values(updates, 'use_internalId'):
                update, = [u for u in updates 
                           if u['use_internalId'] == exchange['internalId']]
            else:
                update = updates
            if 'flow.@id' in update:
                flow_old = exchange['flow']
                del flow_old['name'], flow_old['category']
                flow_old['@id'] = update['flow.@id']
            if 'defaultProvider' in update:
                if 'defaultProvider' in exchange:
                    provider_old = exchange['defaultProvider']
                    del provider_old['name'], provider_old['category']
                    provider_old['@id'] = update['defaultProvider']
                else:
                    exchange.update(
                        {'defaultProvider': {'@id': update['defaultProvider']}})
    return json.dumps(process,
                      separators=(',', ':'),  # no trailing whitespace
                      ensure_ascii=False)


def build_uslci_plus(dedup: dict,
                     ref_updates: dict,
                     dpkg_list: set(tuple(str, str)) = DATA_PACKAGES_BUILD, 
                     ):
    """
    Merge the set of objects from feedstock dpkgs listed in DATA_PACKAGES_BUILD 
    into USLCI+, while (1) avoiding duplicated-UUID collissions, and (2) 
    overwriting Ref pointers embedded in select objects to facilitate inter-dpkg
    linking and seamless DB import + usage in openLCA Desktop.
    """
    # TODO: check if zip-to-zip copy w/o recompression implemented in latest Python
    # TODO: (later) intersect dpkg flows with FEDEFL elem. and USEEIO tech.
        # for each match, skip dpkg file and instead write from FEDEFL or USEEIO
    flows_fedefl = {f for f in zf.ZipFile(PATH_CACHE / 'elementary_flow_list.zip').namelist()
                    if f.startswith('flows/') and f.endswith('.json')}
    flows_useeio = ({f for f in zf.ZipFile(PATH_CACHE / 'USEEIO_v2.zip').namelist()
                     if f.startswith('flows/') and f.endswith('.json')}
                    - flows_fedefl)
    # compile relative file paths of bridge processes to ignore:
    bridges_drop = [f'processes/{uuid}.json' for uuid in 
                    extract_values(ref_updates, 'drop_bridge')]
    with zf.ZipFile(PATH_OUT_ZIP, 'w') as z:  # compression=zf.ZIP_DEFLATED
        files_written = set()
        for group, dpkg in DATA_PACKAGES_BUILD:
            # copy JSONs from {dpkg}.zip to uslci+.zip
            for metadata, content in iter_dpkg_zip_jsons(dpkg, dedup):
                file_rpath = metadata.filename  # relative file path w/i .zip
                if file_rpath in files_written:
                    if not file_rpath.startswith(('processes/', 'flows/')):
                        log.info('Duplicate @type not yet addressed by deduplicate.yaml:'
                                 f'\n\t{dpkg}\n\t{file_rpath}')
                    elif file_rpath in flows_fedefl:
                        pass  # TODO: get FEDEFL elem. flow
                    elif file_rpath in flows_useeio:
                        pass  # TODO: get USEEIO tech. flow
                    else:
                        log.warning('Duplicate not yet addressed by deduplicate.yaml:'
                                    f'\n\t{dpkg}\n\t{file_rpath}')
                elif file_rpath in bridges_drop:
                    log.info(f'Dropped bridge:\n\t{dpkg}\n\t{file_rpath}')
                    pass
                else:
                    data: bytes = content.read()
                    if file_rpath in ref_updates:
                        data: str = update_refs(data, ref_updates[file_rpath])
                    z.writestr(metadata, data)
                    files_written.add(file_rpath)
    
    log.info(f'Wrote combined package: {PATH_OUT_ZIP}')
        

def main() -> int:
    for group, dpkg in (DATA_PACKAGES_BUILD | DATA_PACKAGES_CORE):
        download_dpkg(group, dpkg)
    dedup, ref_updates = compile_dedup_and_ref_indices()
    build_uslci_plus(dedup, ref_updates)
    return 0


# %%
if __name__ == '__main__':
    sys.exit(main())
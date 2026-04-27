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

import sys
import zipfile as zf
from collections import defaultdict
from pathlib import Path
from typing import Set, Tuple

import requests
import yaml
from platformdirs import user_data_dir


PATH_SCRIPT = Path(__file__).parent
# PATH_OUT = PATH_SCRIPT / 'output'
PATH_OUT_ZIP = PATH_SCRIPT / 'USLCI+.zip'
PATH_CACHE = Path(user_data_dir(appauthor='FLCAC', appname='uslci+'))  # ~/FLCAC/uslci+
PATH_CACHE.mkdir(parents=True, exist_ok=True)


SESSION = requests.Session()

# Base URL of FLCAC API endpoint to download whole dpkgs
BASE_URL = 'https://www.lcacommons.gov/lca-collaboration/ws/public/download/json'


DATA_PACKAGES_CORE: Set[Tuple[str, str]] = {
    ('Federal_LCA_Commons', 'Fed_Commons_core_database'),
    ('Federal_LCA_Commons', 'elementary_flow_list'),
    ('US_Environmental_Protection_Agency', 'USEEIO_v2'),
}

# Choose data packages to download: (group, dpkg), as specified in repo URL
# TODO: updat FLCAC API to 
DATA_PACKAGES_BUILD: Set[Tuple[str, str]] = {
    ('US_Environmental_Protection_Agency', 'USEEIO_v2'),
    ('National_Renewable_Energy_Laboratory', 'USLCI_Database_Public'),
    ('Federal_LCA_Commons', 'US_electricity_baseline'),
    ('US_Forest_Service_Forest_Products_Lab', 'Woody_biomass'),
    # ('US_Environmental_Protection_Agency', 'Construction_and_demolition_2022_update_2'),
    # ('US_Environmental_Protection_Agency', 'Heavy_equipment_operation'),
    # ('National_Energy_Technology_Lab', 'Coal_extraction'),
    # ('Federal_Highway_Administration', 'mtu_pavement'),
    # ('NIST', 'construction_materials'),
    # ('NIST', 'Building_Systems'),
    # ('Argonne_National_Lab', 'Concrete'),
    # # deprecated
    # ('CORRIM', 'Forestry_and_forest_products'),
}


# %%
def prepare_download_token(group: str, dpkg: str) -> str:
    """ 
    Ask the server to prepare a data package for download, for which it 
    provides a unique token to get the content.
    """
    url = f'{BASE_URL}/prepare/{group}/{dpkg}'
    r = SESSION.get(url)
    r.raise_for_status()
    token = r.content.decode().strip()
    if not token:
        raise RuntimeError(f'Empty token for {group}/{dpkg}; response={r.text[:200]}')
    return token


def download_dpkg(group: str, dpkg: str) -> Path:
    """
    Download and cache the prepared data package via the token.
    """
    path_zip = PATH_CACHE / f'{dpkg}.zip'
    if not path_zip.exists():
        print(f'Preparing {group}/{dpkg}')
        try:    
            token = prepare_download_token(group, dpkg)
            print(f'\tToken: {token}')
            r = SESSION.get(f'{BASE_URL}/{token}')
            r.raise_for_status()
            print(f'\tDownloaded: {len(r.content):,} bytes')
        except Exception as e:
            msg = f'\tERROR downloading {group}/{dpkg}: {e}'
            print(msg)
        with path_zip.open('xb') as f:
            f.write(r.content)
            print(f'\tCached: {path_zip.as_posix()}')
    else:
        print(f'Data package "{dpkg}" already cached:\n\t',
              path_zip.as_posix())


def compile_deduplication_index() -> defaultdict:
    """
    Import deduplicate.yaml and transform it from  
    (1) a dict of {parent/original dpkg: UUIDs duplicated in other dpkgs}, to
    (2) a dict of {dpkg: .zip-relative file paths of JSONs to ignore in dpkg}
    """
    with (PATH_SCRIPT / "deduplicate.yaml").open() as f:
        dedup_orig = yaml.safe_load(f)
    
    dpkgs_build = {dpkg for (group, dpkg) in DATA_PACKAGES_BUILD}    
    if not dedup_orig.keys() <= dpkgs_build:
        print(f'ERROR: one or more data packages listed as a top-level key '
              'in deduplicate.yaml is not available in DATA_PACKAGEs_BUILD:\n'
              f'{dedup_orig.keys() - dpkgs_build}')
        return None
    else:
        dedup = defaultdict(set)
        for dpkg, uuids_by_type in dedup_orig.items():
            for _type, uuids in uuids_by_type.items():
                for dpkg_other in (dedup_orig.keys() - {dpkg}):
                    if uuids is not None:
                        dedup[dpkg_other].update(
                            {f'{_type}/{uuid}.json' for uuid in uuids})
        return dedup
        ## alternatively, invert dedup_orig into {dpkg: {_type: [uuids_to_ignore]}}
        # dedup = defaultdict(lambda: defaultdict(set))
        # for dpkg, objs_type in dedup_orig.items():
        #     for _type, objs_type_dpkg in objs_type.items():
        #         for dpkg_other in (dedup_orig.keys() - {dpkg}):
        #             if objs_type_dpkg is not None:
        #                 dedup[dpkg_other][_type].update(objs_type_dpkg)
        # return dedup
        ## and then, separately convert to .zip-relative-path strings
        # objs_ignore = [f'{_type}/{uuid}.json' 
        #                for _type, uuids in dedup[dpkg].items() 
        #                for uuid in uuids]


def iter_dpkg_zip_jsons(dpkg: str, dedup: dict) -> (zf.ZipInfo, str):
    """
    Generate an iterable over the JSON-LD items within a dpkg .zip archive, 
    ignoring non-JSON, binary, and root-level files.
    """
    objs_ignore = dedup[dpkg]
    with zf.ZipFile(PATH_CACHE / f'{dpkg}.zip', 'r') as z:
        # z_path = zf.Path(z)
        for metadata in z.infolist():
            # file_path = z_path / metadata.filename
            file_path = metadata.filename  # str, file path within .zip
            if (not file_path.endswith('.json')
                or file_path.startswith('bin/')
                or file_path in objs_ignore
                or file_path in ['categories.json', 'openlca.json']
                ): continue
            with z.open(file_path) as data:
                yield metadata, data


def update_refs():
    """
    Import update_Refs.yaml and apply its alterations on the JSON objects
    already deduplicated and copied into USLCI+.zip
    """
    with (PATH_SCRIPT / "update_Refs.yaml").open() as f:
        ref_updates = yaml.safe_load(f)
    # TODO
    with zf.ZipFile(PATH_OUT_ZIP, 'a') as z:
        z_path = zf.Path(z)
        objs_to_update = [file_path for file_path in z_path.glob('**/*.json')
                          if file_path.stem in ref_updates.keys()]
        for file_path in objs_to_update:  # z.infolist():
            with z.open(file_path, 'a') as file_data:
                # TODO: 
                    # read obj into memory, index via ref_updates[file_path.stem]
                    # or, compile regex substitution and apply 
                pass


def build_uslci_plus(dedup: dict,
                     dpkg_list: Set[Tuple[str, str]] = DATA_PACKAGES_BUILD, 
                     ):
    """
    Merge the set of objects from feedstock dpkgs listed in DATA_PACKAGES_BUILD 
    into USLCI+, while (1) avoiding duplicated-UUID collissions, and (2) 
    overwriting .Ref pointers embedded in select objects to facilitate inter-dpkg
    linking and seamless DB import + usage in openLCA Desktop.
    """
    # TODO: (later) intersect** dpkg flows with FEDEFL elem. and USEEIO tech.
        # for each match, skip dpkg write and instead write from FEDEFL or USEEIO to USLCI+.zip
        # **using set logic on .zip-relative file paths (unless k-v {_type: uuid} pairs are essential)
    # TODO: (later) parallelize copying tasks via `multiprocessing`
    # TODO: check if zip-to-zip copy w/o recompression is implemented in latest base Python
    flows_fedefl = {f for f in zf.ZipFile(PATH_CACHE / 'elementary_flow_list.zip').namelist()
                    if f.startswith('flows/') and f.endswith('.json')}
    flows_useeio = ({f for f in zf.ZipFile(PATH_CACHE / 'USEEIO_v2.zip').namelist()
                     if f.startswith('flows/') and f.endswith('.json')}
                    - flows_fedefl)
    with zf.ZipFile(PATH_OUT_ZIP, 'w') as zip_target:  # compression=zf.ZIP_DEFLATED
        files_written = set()
        for group, dpkg in DATA_PACKAGES_BUILD:
            # copy JSONs from {dpkg}.zip to uslci+.zip
            for metadata, data in iter_dpkg_zip_jsons(dpkg, dedup):
                if metadata.filename in files_written:
                    if not metadata.filename.startswith(('processes/', 'flows/')):
                        print('INFO duplicate @type not yet addressed by deduplicate.yaml:',
                              f'\n\t{dpkg} - {metadata.filename}')
                    elif metadata.filename in flows_fedefl:
                        pass
                    elif metadata.filename in flows_useeio:
                        # print('INFO USEEIO flow not yet addressed by deduplicate.yaml:',
                        #       f'\n\t{dpkg} - {metadata.filename}')
                        pass
                    else:
                        print('WARNING duplicate not yet addressed by deduplicate.yaml:',
                              f'\n\t{dpkg} - {metadata.filename}')
                else:
                    zip_target.writestr(metadata, data.read())
                    files_written.add(metadata.filename)
            # TODO: update refs
            # update_refs(dpkg)
        print(f'\nWrote combined package: {PATH_OUT_ZIP}')
        

def main() -> int:
    for group, dpkg in (DATA_PACKAGES_BUILD | DATA_PACKAGES_CORE):
        download_dpkg(group, dpkg)
    dedup = compile_deduplication_index()
    build_uslci_plus(dedup)
    return 0
    

# %%
if __name__ == '__main__':
    sys.exit(main())
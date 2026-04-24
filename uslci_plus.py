"""
Download FLCAC data packages (dpkg) via public API, deduplicate by UUID, 
then merge into a single .ZIP

API docs: https://www.lcacommons.gov/lca-commons-api-guide

- Uses the openLCA Collaboration Server API pattern:
  1) prepare a JSON-LD package -> returns a token
  2) download zip with that token

- Rules for deduplicating same-UUID objects:
    1. Import list of original parent:child_uuid pairs from deduplicate.yaml
      then drop all non-parent instances of child_uuid
      > Note: FEDEFL is parent for all elem. Flows; USEEIO for all tech. Flows it contains
    2. Then, defer to order of entries in DATA_PACKAGES_BUILD

# Glossary
- build: the database (DB) being assembled from a manifest 
    > now: USLCI+ via DATA_PACKAGES_BUILD; later: arbitrary DB 
- dependency: a dpkg specified as a component of the build 
- duplicate: the same object appears in >1 dependency, 
             as identified by UUID or other matching criteria
- manifest: the structured list of dependency specifiers {alias, version, etc.}, 
            where each entry is used to uniquely identify and locate/fetch a dpkg

# TODO
- (later) avoid decompress/recompress during copy - https://github.com/python/cpython/pull/125718
- (later) parallelize copying tasks via multiprocessing
"""
import io
import json
import shutil
import sys
import zipfile as zf
from pathlib import Path
from typing import Dict, List, Set, Tuple

import requests
import yaml
from platformdirs import user_data_dir

PATH_SCRIPT = Path(__file__).parent
PATH_OUT = PATH_SCRIPT / 'output'
PATH_OUT_ZIP = PATH_OUT / 'combined_jsonld.zip'
PATH_CACHE = Path(user_data_dir(appauthor='FLCAC', appname='uslci+'))
PATH_CACHE.mkdir(parents=True, exist_ok=True)


SESSION = requests.Session()

DEVELOPER_MODE = True
# BUG: using `True` causes unicode escape sequences for ASCII characters in the
    # raw JSON-LD files to be lost across json.loads and json.dumps
# TODO: screen for duplicates via in-memory json.loads dicts, then use file_path 
    # pointers to write original bytes to final .ZIP

# Base URL to download whole dpkgs via API
BASE_URL = 'https://www.lcacommons.gov/lca-collaboration/ws/public/download/json'


DATA_PACKAGES_CORE: Set[Tuple[str, str]] = {
    ('Federal_LCA_Commons', 'Fed_Commons_core_database'),
    ('Federal_LCA_Commons', 'elementary_flow_list'),
    ('US_Environmental_Protection_Agency', 'USEEIO_v2'),
}

# Choose data packages to download: (group, dpkg), as specified in repo URL
# TODO: updat FLCAC API to 
DATA_PACKAGES_BUILD: Set[Tuple[str, str]] = {
    ('National_Renewable_Energy_Laboratory', 'USLCI_Database_Public'),
    ('Federal_LCA_Commons', 'US_electricity_baseline'),
    ('US_Forest_Service_Forest_Products_Lab', 'Woody_biomass'),
    ('US_Environmental_Protection_Agency', 'USEEIO_v2'),
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


# def iter_zip_jsons(path_zip: Path):
#     """
#     Stream dpkg zip contents
#     """
#     with zf.Path(path_zip) as z:
        
#     # with zf.ZipFile(path_zip, 'r') as z:
#     #     for file_path in z.namelist():
#     #         if (not file_path.endswith('.json')
#     #             or file_path.startswith('bin/')
#     #             or file_path in ['categories.json', 'openlca.json']):
#     #             continue
#     #         data = z.read(file_path)
#     #         # try:
#     #         #     obj = json.loads(data.decode('utf-8'))
#     #         # except Exception:
#     #         #     print(f'WARNING non-UTF-8 text in JSON: {file_path}')
#     #         #     obj = json.loads(data.decode('latin-1'))
#     #         yield file_path, data
#             yield file_path



# %%
for group, dpkg in (DATA_PACKAGES_BUILD | DATA_PACKAGES_CORE):
    path_zip = download_dpkg(group, dpkg)

with (PATH_SCRIPT / "deduplicate.yaml").open() as f:
    dedup = yaml.safe_load(f)

# %%
def iter_zip_jsons(path_zip: Path) -> zf.ZipInfo:
    """
    Iterate over items within .zip dpkg, ignoring non-JSON, binary, and 
    root-level files
    """
    # with zf.Path(path_zip) as z:
    #     for file_path in z.glob('**/*.json'):
    #         yield file_path
    with zf.ZipFile(path_zip, 'r') as z:
        # for file_path in z.namelist():  
        for item in z.infolist():
            file_path = item.filename
            if (not file_path.endswith('.json')
                or file_path.startswith('bin/')
                or file_path in ['categories.json', 'openlca.json']):
                continue
            yield item
            # data = z.read(file_path)
            # try:
            #     obj = json.loads(data.decode('utf-8'))
            # except Exception:
            #     print(f'WARNING non-UTF-8 text in JSON: {file_path}')
            #     obj = json.loads(data.decode('latin-1'))
            # yield file_path, data
            
def clone_file_across_zips(path_zip_source, path_zip_target):
    """
    Copy a file from within a source .zip into a destination .zip, 
    while preserving that file's original metadata
    """
    # TODO: integrate dict_skip, so as to minimize entering/exiting 'with' buffers
    with zf.ZipFile(path_zip_target, 'a') as zip_target:
        # for path_zip_source in iterable
        with zf.ZipFile(path_zip_source, 'r') as zip_source:         
            # Iterate through each file's metadata object (ZipInfo)
            for info in zip_source.infolist():
                # Read the file's raw content
                with zip_source.open(info.filename) as file_data:
                    # Write content into the new ZIP using the original ZipInfo
                    zip_target.writestr(info, file_data.read())
                    shutil.copyfileobj(1, 2) # faster but drops metadata




# zip-to-zip copying
with zf.ZipFile(PATH_OUT_ZIP, 'w') as zip_target:
    files_written = set()
    for group, dpkg in DATA_PACKAGES_BUILD:
        path_zip = PATH_CACHE / f'{dpkg}.zip'
        with zf.ZipFile(path_zip, 'r') as zip_source:
            for item in zip_source.infolist():
                zip_target.writestr(item, zip_source.read(item.filename))
                # with zip_source.open(item) as file_source:
                #     zip_target.writestr(item, file_source.read())
            # # TODO: benchmark shutil.copyfileobj against zf.ZipFile.writestr
            # with zip_source.open(item) as file_source:
            #     with zip_target.open(item, 'w') as file_target:
            #         shutil.copyfileobj(file_source, file_target)

          
with zf.ZipFile(PATH_OUT_ZIP, 'w', compression=zf.ZIP_DEFLATED) as zout:
    files_written = set()
    for group, dpkg, path_zip in dpkg_zips:
        for file_path, data in iter_zip_jsons(path_zip):
            if file_path in files_written:
                print('WARNING duplicate not yet addressed by deduplicate.yaml:',
                      f'\n{file_path}')
            else:
                zout.writestr(file_path, data)
                files_written.add(file_path)

    
# TODO: expand dedup to include {elementary_flow_list: {flow: all}} and 
    # {USEEIO_v2: {flow: all except FEDEFL_flows}}

# TODO: by active-for-write dpkg, compile sets of UUIDs by @type to skip writing
    # by dpkg (in DATA_PACKAGES_BUILD) and @type (zip subdir), 
        # compile flow and process set(uuids in dedup[all - dpkg_current][@type])
        # to skip writing to zout
    # If duplicate arises not caught by dedup, flag it and skip write

# ???: for dedup write logic, iterate over relative Paths (~ / <@type> / *.json), 
  # or substring matching on string fpaths (if 'flows' in 'flows/*.json': ...)?


# for group, dpkg, path_zip in dpkg_zips:
    # with zf.Path(PATH_CACHE / 'elementary_flow_list.zip') as z:
    # dedup['elementary_flow_list']['flow'] 
    
# path_zip = PATH_CACHE / 'elementary_flow_list.zip'
# temp = [x for x in zf.ZipFile(path_zip).infolist()]
# temp = [x for x in zf.Path(path_zip).glob('**/*.json')]
# f = temp[1]
# f.parent.name == 'flows'

# %% 2) de-duplicate objects
merged: Dict[str, dict] = {}
for (group, dpkg, content) in dpkg_zips:
    added, replaced, seen = (0, 0, 0)
    for file_path, data in iter_zip_jsons(content):
        obj = json.loads(data.decode('utf-8'))
        # obj = json.loads(data)
        if not (isinstance(obj, dict) and obj.get('@id')):
            print(f'INFO non-olca obj in {dpkg}: {file_path}')
            continue
        seen += 1
        if file_path not in merged:
            merged[file_path] = obj
            added += 1
        # else:
        #     original = keep_original_entity(merged[file_path], obj)
        #     if original is not merged[file_path]:
        #         print(f'Replacing duplicate {obj.get('@type')} with original from {dpkg}')
        #         merged[file_path] = original
        #         replaced += 1
    print(f'Merged {group}/{dpkg}: seen={seen:,} added={added:,} replaced={replaced:,}')

print(f'\nTotal distinct entities after merge: {len(merged):,}')
# %% 3) write merged set of objects to new .ZIP
with zf.ZipFile(PATH_OUT_ZIP, 'w', compression=zf.ZIP_DEFLATED) as zout:
    for file_path, obj in merged.items():
        data = json.dumps(obj, separators=(',', ':'))
                           # indent=2, sort_keys=True,  # long-format JSON
        if file_path == 'sources/3a3c9163-5178-373d-b547-714ad35f00db.json':
            with open('test.json', 'w') as f:
                json.dump(obj, f, separators=(',',':'), ensure_ascii=True)
        zout.writestr(file_path, data)
print(f'\nWrote combined package: {PATH_OUT_ZIP}')


# %%

def iter_json_members(zip_bytes: bytes):
    """Yield (file_path, obj) for each *.json entry inside the JSON-LD ZIP."""
    # index JSONs via zf.ZipFile().namelist() or zf.Path.rglob()
    with zf.ZipFile(io.BytesIO(zip_bytes)) as z:
        for file_path in z.namelist():
            if (not file_path.endswith('.json')
                or file_path.startswith('bin/')
                or file_path in ['categories.json', 'openlca.json']):
                continue
            data = z.read(file_path)
            # try:
            #     obj = json.loads(data.decode('utf-8'))
            # except Exception:
            #     print(f'WARNING non-UTF-8 text in JSON: {file_path}')
            #     obj = json.loads(data.decode('latin-1'))
            yield file_path, data
    # zip_paths = zf.Path(io.BytesIO(zip_bytes)).rglob('*.json')
    # return [(file_path, json.loads(file_path.read_text(encoding='utf-8')))
    #         for file_path in zip_paths]
    # note: file_path.at yields same str form as z.namelist() entries

# %% OLD
def main() -> int:
    # %% 1) download each data package as .zip (bytes) of JSON-LD
    dpkg_zips: List[Tuple[str, str, bytes]] = []
    for group, dpkg in DATA_PACKAGES_BUILD:
        try:
            print(f'Preparing {group}/{dpkg}')
            token = prepare_download_token(group, dpkg)
            print(f'  token: {token}')
            content = download_dpkg(token, dpkg)
            print(f'  downloaded: {len(content):,} bytes')
            dpkg_zips.append((group, dpkg, content))
        except Exception as e:
            msg = f'ERROR downloading {group}/{dpkg}: {e}'
            print(msg)
            # return 2
# %%    
    if not DEVELOPER_MODE:
        # skip the in-memory bytes --> dict (inspectable) --> bytes steps
        with zf.ZipFile(PATH_OUT_ZIP, 'w', compression=zf.ZIP_DEFLATED) as zout:
            files_written = set()
            for group, dpkg, content in dpkg_zips:
                for file_path, data in iter_json_members(content):
                    if file_path not in files_written:
                        zout.writestr(file_path, data)
                        files_written.add(file_path)
    else:
    # %% 2) de-duplicate objects
        merged: Dict[str, dict] = {}
        for (group, dpkg, content) in dpkg_zips:
            added, replaced, seen = (0, 0, 0)
            for file_path, data in iter_json_members(content):
                obj = json.loads(data.decode('utf-8'))
                # obj = json.loads(data)
                if not (isinstance(obj, dict) and obj.get('@id')):
                    print(f'INFO non-olca obj in {dpkg}: {file_path}')
                    continue
                seen += 1
                if file_path not in merged:
                    merged[file_path] = obj
                    added += 1
                # else:
                #     original = keep_original_entity(merged[file_path], obj)
                #     if original is not merged[file_path]:
                #         print(f'Replacing duplicate {obj.get('@type')} with original from {dpkg}')
                #         merged[file_path] = original
                #         replaced += 1
            print(f'Merged {group}/{dpkg}: seen={seen:,} added={added:,} replaced={replaced:,}')
        
        print(f'\nTotal distinct entities after merge: {len(merged):,}')
        # %% 3) write merged set of objects to new .ZIP
        with zf.ZipFile(PATH_OUT_ZIP, 'w', compression=zf.ZIP_DEFLATED) as zout:
            for file_path, obj in merged.items():
                data = json.dumps(obj, separators=(',', ':'))
                                   # indent=2, sort_keys=True,  # long-format JSON
                if file_path == 'sources/3a3c9163-5178-373d-b547-714ad35f00db.json':
                    with open('test.json', 'w') as f:
                        json.dump(obj, f, separators=(',',':'), ensure_ascii=True)
                zout.writestr(file_path, data)
    print(f'\nWrote combined package: {PATH_OUT_ZIP}')
    return 0

# TODO: write JSON index of objects w/i dpkgs after de-dup
    # {dpkg_name: [uuid, ...], ...} or 
    # {dpkg_name: {@type: [uuid, ...], ...}, ...}
# %%
if __name__ == '__main__':
    sys.exit(main())
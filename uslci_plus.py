"""
Download FLCAC data packages (dpkg) via public API, then merge into single .ZIP

API docs: https://www.lcacommons.gov/lca-commons-api-guide

- Uses the openLCA Collaboration Server API pattern:
  1) prepare a JSON-LD package -> returns a token
  2) download zip with that token

- Merging rule for duplicate entities (same '@id'):
    * for now, defer to order of entries in DATA_PACKAGES:
        - in general, FEDEFL > electricity baseline > USLCI > others
    * DO NOT rely on comparisons of .version or .lastChanged
"""

import io
import json
import sys
import zipfile
from pathlib import Path
from typing import Dict, List, Tuple

import requests
# from platformdirs import user_cache_dir   
    # TODO: cache downloads to avoid repeat fetching


DEVELOPER_MODE = False
# BUG: using `True` causes unicode escape sequences for ASCII characters in the
    # raw JSON-LD files to be lost across json.loads and json.dumps
    # TODO: screen for duplicates via in-memory json.loads dicts, then use file_path 
    # pointers to write original bytes to final .ZIP via 

# Base URL to download whole dpkgs via API
BASE_URL = "https://www.lcacommons.gov/lca-collaboration/ws/public/download/json"

# Choose data packages to download: (group, dpkg), as specified in repo URL
# NOTE: list order determines precedence of same-uuid object de-duplication
DATA_PACKAGES: List[Tuple[str, str]] = [
    # ("Federal_LCA_Commons", "elementary_flow_list"),   # FEDEFL full
    ("National_Renewable_Energy_Laboratory", "USLCI_Database_Public"),
    ("Federal_LCA_Commons", "US_electricity_baseline"),
    ("US_Forest_Service_Forest_Products_Lab", "Woody_biomass"),
]  

# Output combined JSON-LD package (ZIP)
PATH_SCRIPT = Path(__file__).parent
OUTPUT_ZIP = PATH_SCRIPT / "combined_jsonld.zip"

SESSION = requests.Session()

# %%
def prepare_download_token(group: str, dpkg: str) -> str:
    """ 
    Ask the server to prepare a data package for download, for which it 
    provides a unique token to get the content.
    """
    url = f"{BASE_URL}/prepare/{group}/{dpkg}"
    r = SESSION.get(url)
    r.raise_for_status()
    token = r.content.decode().strip()
    if not token:
        raise RuntimeError(f"Empty token for {group}/{dpkg}; response={r.text[:200]}")
    return token


def download_dpkg_by_token(token: str) -> bytes:
    """Download the prepared data package via the token."""
    url = f"{BASE_URL}/{token}"
    r = SESSION.get(url)
    r.raise_for_status()
    return r.content


def iter_json_members(zip_bytes: bytes):
    """Yield (file_path, obj) for each *.json entry inside the JSON-LD ZIP."""
    # index JSONs via zipfile.ZipFile().namelist() or zipfile.Path.rglob()
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for file_path in zf.namelist():
            if not file_path.endswith(".json"):
                continue
            data = zf.read(file_path)
            # try:
            #     obj = json.loads(data.decode("utf-8"))
            # except Exception:
            #     print(f"WARNING non-UTF-8 text in JSON: {file_path}")
            #     obj = json.loads(data.decode("latin-1"))
            yield file_path, data
    # zip_paths = zipfile.Path(io.BytesIO(zip_bytes)).rglob("*.json")
    # return [(file_path, json.loads(file_path.read_text(encoding="utf-8")))
    #         for file_path in zip_paths]
    # note: file_path.at yields same str form as zf.namelist() entries


def main() -> int:
    # %% 1) download each data package as .zip (bytes) of JSON-LD
    dpkg_zips: List[Tuple[str, str, bytes]] = []
    for group, dpkg in DATA_PACKAGES:
        try:
            print(f"Preparing {group}/{dpkg}")
            token = prepare_download_token(group, dpkg)
            print(f"  token: {token}")
            content = download_dpkg_by_token(token)
            print(f"  downloaded: {len(content):,} bytes")
            dpkg_zips.append((group, dpkg, content))
        except Exception as e:
            msg = f"ERROR downloading {group}/{dpkg}: {e}"
            print(msg)
            return 2
    
    if not DEVELOPER_MODE:
        # skip the in-memory bytes --> dict (inspectable) --> bytes steps
        with zipfile.ZipFile(OUTPUT_ZIP, "w", compression=zipfile.ZIP_DEFLATED) as zout:
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
                obj = json.loads(data.decode("utf-8"))
                # obj = json.loads(data)
                if not (isinstance(obj, dict) and obj.get("@id")):
                    print(f"INFO non-olca obj in {dpkg}: {file_path}")
                    continue
                seen += 1
                if file_path not in merged:
                    merged[file_path] = obj
                    added += 1
                # else:
                #     original = keep_original_entity(merged[file_path], obj)
                #     if original is not merged[file_path]:
                #         print(f"Replacing duplicate {obj.get('@type')} with original from {dpkg}")
                #         merged[file_path] = original
                #         replaced += 1
            print(f"Merged {group}/{dpkg}: seen={seen:,} added={added:,} replaced={replaced:,}")
        
        print(f"\nTotal distinct entities after merge: {len(merged):,}")
        # %% 3) write merged set of objects to new .ZIP
        with zipfile.ZipFile(OUTPUT_ZIP, "w", compression=zipfile.ZIP_DEFLATED) as zout:
            for file_path, obj in merged.items():
                data = json.dumps(obj, separators=(',', ':'))
                                   # indent=2, sort_keys=True,  # long-format JSON
                if file_path == 'sources/3a3c9163-5178-373d-b547-714ad35f00db.json':
                    with open("test.json", "w") as f:
                        json.dump(obj, f, separators=(',',':'), ensure_ascii=True)
                zout.writestr(file_path, data)
    print(f"\nWrote combined package: {OUTPUT_ZIP}")
    return 0

# TODO: write JSON index of objects w/i dpkgs after de-dup
    # {dpkg_name: [uuid, ...], ...} or 
    # {dpkg_name: {@type: [uuid, ...], ...}, ...}
# %%
if __name__ == "__main__":
    sys.exit(main())
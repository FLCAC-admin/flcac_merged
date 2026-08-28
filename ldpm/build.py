"""
Fetch FLCAC data packages (dpkg); deduplicate by UUID; link across dpkgs by
overwriting .Ref attributes; then merge into a single olca-schema .ZIP

Rules for deduplicating same-UUID objects:
    1. Import deduplicate.yaml dict of {original/parent dpkg:uuid_child} pairs,
       and ignore all instances of uuid_child that appear in other non-parent dpkgs
       > Implicit rules omitted from deduplicate.yaml: 
           - FEDEFL is parent for all elem. flows
           - USEEIO is parent for all tech. flows which it contains
    2. If duplicate UUIDs arise w/o any specified parent, defer to the order 
       of the dpkg entries in manifest's [dependencies] table (i.e., first takes priority)
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

import yaml

from ldpm.flcac_api import SESSION, INDEX_DPKG
from ldpm.linking import prepare_provider_links
from ldpm.utils import PATHS, find_file, format_log_msg

log = logging.getLogger(__name__)


@dataclass(frozen=True)  # unsafe_hash=True allows hashing w/o immutability
class DataPackage:
    name: str
    version: str
    excluded_types: tuple[str] = field(default_factory=tuple, repr=False)
    group: str = field(init=False, repr=False)
    path_zip: Path = field(init=False, repr=False)
    commitId: float = field(init=False, repr=False)
    
    def __eq__(self, other):
        if not isinstance(other, DataPackage):
            return NotImplemented
        return self.name == other.name and self.version == other.version
    
    def __str__(self) -> str:
        return f'{self.name}--v{self.version}'

    def __post_init__(self) -> None:
        # validate olca .ZIP-package type folder exclusions:
        valid_types = {
            'actors', 'currencies', 'dq_systems', 'epds', 'flows', 'flow_properties', 
            'lcia_categories', 'lcia_methods', 'locations', 'parameters', 'processes', 
            'product_systems','projects', 'results', 'social_indicators', 'sources', 
            'unit_groups',
            }
        invalid_types = []
        for _type in self.excluded_types:
            if _type not in valid_types:
                invalid_types.append(_type)
        if invalid_types:
            msg = format_log_msg(
                [f'Invalid excluded type(s) for {self.name}:',
                 f'\t- {"\n\t\t- ".join(invalid_types)}',
                 'Please use only "folder" type labels of Zip packages in olca-schema docs:'
                 '\thttps://greendelta.github.io/olca-schema/#zip-packages'])
            raise ValueError(msg)
        # infer group from name
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
        # replace "*" wildcard w/ latest version
        _version = self.version
        if _version == "*":  
            _version = next(iter(INDEX_DPKG[_group][self.name]))
            dependent_attributes |= {'version': _version}
        # set post-init attributes on frozen instance
        dependent_attributes |= {'path_zip': PATHS.cache / f'{self.name}--v{_version}.zip',
                                 'commitId': INDEX_DPKG[_group][self.name][_version],
                                }
        for attr, value in dependent_attributes.items():
            object.__setattr__(self, attr, value)
        
    @property
    def zipfile(self, mode: str = 'r') -> zf.ZipFile:
        return zf.ZipFile(self.path_zip, mode)
    
    def iter_zip_jsons(self, dedup: dict = None, subdir: str = None) -> (zf.ZipInfo, bytes):
        """
        Generate an iterable over the JSON-LD files within the dpkg .zip archive, 
        ignoring non-JSON, binary, and root-level files.
        
        Args:
            dedup: deduplication index, as compiled by get_dedup_config()
            subdir: filter to only return files w/i a subdirectory of the .zip (e.g., 'processes') 
        """
        if dedup is None:
            dedup = {}
        objs_ignore = dedup.get(self.name, set()) | {'categories.json', 'openlca.json'}
        dirs_ignore = ('bin/', *self.excluded_types)
        with self.zipfile as z:
            for metadata in z.infolist():
                file_rpath = metadata.filename  # str, relative file path w/i .zip
                if (not file_rpath.endswith('.json')
                    or file_rpath.startswith(dirs_ignore)
                    or file_rpath in objs_ignore
                    ): continue
                if subdir:
                    if not file_rpath.startswith(subdir): continue
                with z.open(file_rpath, 'r') as file_stream:
                    yield (metadata, file_stream)
    
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
class Build:
    name: str
    version: str
    path_file: Path = field(repr=False)
    dependencies: set[DataPackage] = field(default_factory=set)
    dependencies_indirect: set[DataPackage] = field(init=False, default_factory=set)
    created_at: str = field(default_factory=datetime.now)

    @classmethod
    def from_manifest_toml(cls, file_name: str = 'manifest.toml'):  # -> Build
        path_file = find_file(file_name)
        with (path_file).open('rb') as f:
            manifest = tomllib.load(f)
        dependencies: set[DataPackage] = set()
        for name, spec in manifest['dependencies'].items():
            match spec:
                case str():  # expects SemVer string or "*" wildcard
                    version = spec
                    excluded_types = tuple()
                case dict():
                    version = spec['version']
                    match spec.get('exclude', []):
                        case str() as _type:
                            excluded_types = tuple([_type])
                        case list() as _types:
                            excluded_types = tuple(_types)
            dependencies.add(DataPackage(name, version, excluded_types))
        # for name, spec in manifest['dependencies'].items():
            # match spec:
                # case str():  # expects SemVer string or "*" wildcard
                    # dependencies.add(DataPackage(name, version=spec))
                # case dict():
                    # spec.get('exclude', [])
                    # excluded_types = tuple()
                    # dependencies.add(DataPackage(name, 
                                                 # version=spec['version'],
                                                 # excluded_types=excluded_types))
        return cls(name=manifest['build']['name'], 
                   version=manifest['build']['version'],
                   path_file=path_file,
                   dependencies=dependencies)

    def __post_init__(self) -> None:
        dpkgs_available = {name: tuple(version for version in version_subdict.keys())
                           for name_subdict in INDEX_DPKG.values() 
                           for name, version_subdict in name_subdict.items()}
        for dpkg in self.dependencies:
            if dpkg.name not in dpkgs_available:
                error_msg = f'No data package named "{self.name}" is available, per data_packages.yaml.'
                log.error(error_msg)
                raise KeyError(error_msg)
            elif dpkg.version not in dpkgs_available[dpkg.name]:
                error_msg = f'Version "{self.version}" of data package "{self.name}" unavailable.'
                log.error(error_msg)
                raise KeyError(error_msg)
        """
        An 'indirect dependency' contains objects used across most/all other 
        dpkgs on the FLCAC; including them in Build.dependencies is optional; 
        and omission from Build defaults to using the latest version
        """
        names_indirect_dependencies = (
            'elementary_flow_list',  # elem. flows
             'Fed_Commons_core_database',  #  non-process objs.
             'USEEIO_v2',  # tech. flows
            )
        dpkgs_indirect = {DataPackage(name, dpkgs_available[name][0])
                          for name in names_indirect_dependencies}
        dependencies_indirect = dpkgs_indirect - self.dependencies
        object.__setattr__(self, 'dependencies_indirect', dependencies_indirect)
    
    def __str__(self) -> str:
        dpkgs_as_str = lambda dpkgs: ',\n\t'.join(str(dpkg) for dpkg in dpkgs)
        str_deps_direct = dpkgs_as_str(self.dependencies)
        str_deps_indirect = dpkgs_as_str(self.dependencies_indirect)
        return (f'Build: {self.name}, v{self.version}\n'
                f'Dependencies, direct:\n\t{str_deps_direct}\n'
                f'Dependencies, indirect:\n\t{str_deps_indirect}'
                f'Created: {self.created_at.strftime("%y-%m-%d %T")}')
    
    @property
    def dependencies_all(self) -> set[DataPackage]:
        return self.dependencies | self.dependencies_indirect
    
    def fetch_dependencies(self) -> None:
        # TODO: add command-line progress bar
        for dpkg in self.dependencies_all: 
            dpkg.fetch()


def get_dedup_config(build: Build) -> dict:
    """
    Import deduplicate.yaml and invert it from
    (1) {parent_dpkg: [UUIDs duplicated in other_dpkg(s)]}, to
    (2) {other_dpkg: file_rpath} to ignore, for each other_dpkg in the build,
        where file_rpath is a .zip-internal relative file path
    """
    with (PATHS.config / 'deduplicate.yaml').open() as file:
        dedup_orig = yaml.safe_load(file)
    dpkgs_build = {dpkg.name for dpkg in build.dependencies}
    if dpkgs_build > dedup_orig.keys():
        log.debug('One or more dependencies lack deduplicate.yaml entries:'
                  f'\n{dpkgs_build - dedup_orig.keys()}')    
    dedup = defaultdict(set)
    for dpkg_name, uuids_by_type in dedup_orig.items():
        for _type, uuids in uuids_by_type.items():
            if uuids is not None:
                for dpkg_other in (dpkgs_build - {dpkg_name}):
                    dedup[dpkg_other] |= {f'{_type}/{uuid}.json' for uuid in uuids}
                    # dedup[dpkg_other].update({f'{_type}/{uuid}.json' for uuid in uuids})
    return dict(dedup)


def update_provider_links(process_file: zf.ZipExtFile, process_links: dict) -> str:
    """
    For a single olca.Process JSON file, alter `exchange.defaultProvider.@id` 
    field/s via instructions complied from provider_links.yaml and linking.py
    
    Args:
        process_file: file pointer for olca.Process JSON
        process_links: sub-dict accessed via links_rpath_all[file_rpath]
    Returns:
        olca.Process with updated exchange.defaultProvider links, as single-line JSON
    """
    process = json.load(process_file)
    # reindex on exchange.internalId; exchange.flow.@id keys are for readability
    exchange_updates = {exchange_id: provider_id
                        for sub_dict in process_links.values() 
                        for exchange_id, provider_id in sub_dict.items()}
    # target exchange pointers for in-place alteration
    target_exchanges = {exchange['internalId']: exchange
                        for exchange in process['exchanges'] 
                        if exchange['internalId'] in exchange_updates}
    for exchange_id, exchange in target_exchanges.items():
        exchange['defaultProvider'] = {'@id': exchange_updates[exchange_id]}
    return json.dumps(process,
                      separators=(',', ':'),  # no trailing whitespace
                      ensure_ascii=False)


# %%
def build_db(
    build: Build,
):
    """
    Merge the set of objects from dpkg dependencies listed in the manifest TOML 
    into the build, while (1) avoiding duplicated-UUID collissions, and (2) 
    overwriting Ref pointers embedded in select objects to facilitate inter-dpkg
    linking and seamless DB import + usage in openLCA Desktop.
    """
    build.fetch_dependencies()
    
    PATH_OUT_ZIP = PATHS.output / f'{build.name}_v{build.version}.zip'
        
    dedup = get_dedup_config(build)
    provider_updates = prepare_provider_links(build)
    # TODO: orchestrate dropping of "pass-through" bridge processes in linking.py
        # compile relative file paths of bridge processes to ignore:
        # bridges_drop = [f'processes/{uuid}.json' for uuid in _new_linking_function()]
    
    # substitute FEDEFL & USEEIO flows for duplicates appearing in other dpkgs
    # wherever one or both are used as indirect dependencies
    def _get_dpkg_flows(dpkg: DataPackage) -> dict:
        with dpkg.zipfile as z:
            return {metadata.filename: metadata for metadata in z.infolist()
                    if metadata.filename.startswith('flows/') 
                    and metadata.filename.endswith('.json')}
            # return {f for f in z.namelist() 
            #         if f.startswith('flows/') and f.endswith('.json')}
    
    indirect_flows_fedefl_elem, indirect_flows_useeio_tech = ({}, {})
    for dpkg in build.dependencies_indirect:
        match dpkg.name:
            case 'elementary_flow_list':
                indirect_flows_fedefl_elem = _get_dpkg_flows(dpkg)
                zip_fedefl = dpkg.zipfile
            case 'USEEIO_v2':
                indirect_flows_useeio_tech = _get_dpkg_flows(dpkg)
                zip_useeio = dpkg.zipfile
        
    if indirect_flows_useeio_tech and indirect_flows_fedefl_elem:
        for flow in indirect_flows_fedefl_elem.keys():
            indirect_flows_useeio_tech.pop(flow, None)
    
    # TODO: ensure flows_fedefl_elem always deduplicate other dpkgs if FEDEFL
        # is specified as a direct dependency
    with (zf.ZipFile(PATH_OUT_ZIP, 'w') as zip_build,
          zip_fedefl if indirect_flows_fedefl_elem else nullcontext(),
          zip_useeio if indirect_flows_useeio_tech else nullcontext()):
        files_written = set()
        for dpkg in build.dependencies:
            # copy JSONs from dpkg to build .ZIP
            for metadata, file_stream in dpkg.iter_zip_jsons(dedup=dedup):
                file_rpath = metadata.filename
                # On-the-fly deduplication of already-written UUIDs:
                if file_rpath in files_written:
                    if not file_rpath.startswith(('processes/', 'flows/')):
                        log.debug('Duplicate @type not addressed by deduplicate.yaml'
                                  f'\n\t{dpkg.name}\n\t{file_rpath}')
                    elif (file_rpath in indirect_flows_fedefl_elem or
                          file_rpath in indirect_flows_useeio_tech):
                        pass
                    else:
                        log.warning('Duplicate not yet addressed by deduplicate.yaml:'
                                    f'\n\t{dpkg.name}\n\t{file_rpath}')
                # On-the-fly deduplication of objects from indirect dpkgs:
                elif file_rpath in indirect_flows_fedefl_elem:
                    zip_build.writestr(indirect_flows_fedefl_elem[file_rpath], 
                                       zip_fedefl.read(file_rpath))
                    files_written.add(file_rpath)
                elif file_rpath in indirect_flows_useeio_tech:
                    zip_build.writestr(indirect_flows_useeio_tech[file_rpath], 
                                       zip_useeio.read(file_rpath))
                    files_written.add(file_rpath)
                # elif file_rpath in bridges_drop:
                #     log.debug(f'Dropped bridge:\n\t{dpkg.name}\n\t{file_rpath}')
                #     pass
                else:
                    try:
                        if file_rpath in provider_updates:
                            data = update_provider_links(file_stream, provider_updates[file_rpath])
                        else:
                            data = file_stream.read()
                        zip_build.writestr(metadata, data)
                        files_written.add(file_rpath)
                    except:
                        log.exception(f'Bad write for {file_rpath} from {dpkg.name}')
                        raise
    # TODO: write build metadata to PATHS.output as TOML or YAML
    print(f'\nWrote combined DB: {PATH_OUT_ZIP}')
        
# %%
def main() -> int:    
    build = Build.from_manifest_toml()
    build_db(build)
    return 0


if __name__ == '__main__':
    sys.exit(main())
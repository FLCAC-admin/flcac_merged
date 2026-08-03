"""
Scripting to support compilation (current: manual, later: programmatic) of a
YAML index of "original" objects by parent data package (dpkg), of the form:

    {dpkg_name/alias:
        [UUID_to_keep,
        …],
    …}

to configure de-duplication before linking dpkgs.

Alternatively, return a YAML of UUIDs to drop 
    (issue: less extensible/does not guard against more dups added)


ToDo:
1. Use set logic to compare feedstock objs. (A) to `USLCI + Q1.zip` (B)
    - Intersection (A & B): which objs exist in both?
    - Symmetric difference (A ^ B): which objs exist in A or B but no both?
    - Differences (A - B, B - A): which objs are only present in A, or only in B?
    
2. Assemble list (or dict) of duplicates within feedstock dpkgs
    - Use set logic on file names to identify duplicated UUIDs

    - Read JSON into memory; by @type, find duplicate .name values 
        > i.e., potential additional duplicates, missed by UUID screening

    - Address: Process, Flow (tech),
        > tech Flow:
            A. Only 1 dpkg contains provider for flow: dpkg is parent
            B. No providers: confirm whether CUTOFF; flag/log if not
            C. (backlog) Multiple flow providers across dpkgs: 
                    - Flow should exist in both, 
                      but need to replace all Refs to one w/ the other
        > Process: flag; THIS SHOULD NOT HAPPEN
        
    - Later: Source, Product System
    - Ignore: 
    	> elementary flows: will all from FEDEFL
    	> {Location, Actor, Currency, Unit Group, Flow Property}: 
            consolidate into "FLCAC Core"

# Glossary
- build: the database (DB) being assembled from a manifest (now: USLCI+, later: arbitrary)
- dependency: a dpkg specified as a component of the build 
- duplicate: the same object appears in >1 dependency, 
             as identified by UUID or other matching criteria
- manifest: the structured list of dependency specifiers {alias, version, etc.}, 
            where each entry is used to uniquely identify and locate/fetch a dpkg

# Backlog
[ ] confirm whether a tech Flow has provider(s) only in one dpkg:
    # if True, list Flow under that dpkg (i.e., as its parent) in deduplicate.yaml
    # Options:
        # 1. (local, streamed) regex on Process JSON
            > for each dpkg containing Flow, search Process files therein for
            `"isQuantitativeReference":true` within single set of curly brackets
            in "exchanges": [{...}, {...}, ...] list
            `{"@type":"Flow","@id":"<UUID>",.*},"unit":{.*},"flowProperty":{.*},"isQuantitativeReference":true}`
        # 2. (local, in-memory) JSONata query
        # 3. (remote) FLCAC API

[ ] for all duplicate Process and Flow objs, confirm that *.name is identical
    if not, (1) log warning, and (2) keep the obj from USLCI 
    > (alt: or w/ a more recent .lastChange)
    [ ](later) expand to other .<attribute> fields
    
[ ] if a duplicate Process has "bridge" in Process.name, extract the "; X to Y"
    dpkg aliases; use partial string matching/mapping to keep the Process that
    exists in dpkg X; else, log warning
 
[ ] Pull & operate over all (non-deprecated, latest) FLCAC dpkgs, including USEEIO
  [ ] if a duplicate tech Flow has no provider Process among build dependencies,
      but has one or more providers in a single non-dependency dpkg (e.g., USEEIO)
      then (1) if Flow.name 
      compare the non-dep. Flow to those in the deps. and log/flag any diffs,
      and (2) keep the non-dep. Flow 

[ ] screen whole FLCAC for duplicates:
    - Across all .@type
        [ ] {same .@id different .name, same .name different .@id}
    - Flow, key fields: {.flowType, .category, .flowProperties, .cas, .formula}
        [ ] same .@id, different .name and/or key fields
        [ ] same .name, different .@id and/or key fields
    - Process, key fields: {.processType, .location, .exchanges, .category, .allocationFactors, .defaultAllocationMethod}
        [ ] same .@id, different .name and/or key fields
        [ ] same .name, different .@id and/or key fields

"""

import itertools as it
# import json
from pathlib import Path

PATH_SCRIPT = Path(__file__).parent
PATH_DPKG = PATH_SCRIPT.parents[2] / 'DPM' / 'dpkgs'

dpkg_dir = {
    'uslci': 'USLCI_Database_Public__v1.2026-03.0', 
    'woody': 'Woody_biomass__v1.2025-08.0', 
    'elec': 'US_electricity_baseline__v1.2025-06.0',
    'useeio': 'USEEIO_v2__v1.2022-06.0',
    'fedefl': 'elementary_flow_list__v1.2024-12.1',
    'pvmt': 'mtu_pavement__v1.2025-08.0',
    'uslci+': 'USLCI+__v1.2026-03.0',
    'heqpt': 'Heavy_equipment_operation__v1.2025-07.0',
    'cdd': 'Construction_and_demolition_2022_update_2__v1.2025-07.0',
    'cmats': 'construction_materials__v1.2026-07.0',
    'bsys': 'Building_Systems__v1.2025-09.0',
    'coal': 'Coal_extraction__v1.2025-07.0',
    'concrete': 'Concrete__v1.2025-02.0',
    'corrim': 'Forestry_and_forest_products_v1.2019-12.0',
    }

# %% summarize UUID duplication in dependencies
dpkg_build = {'uslci', 'woody', 'elec', 'useeio', 'uslci+'}
types_of_interest = ['flows',
                     'processes',
                     # 'locations',
                     # 'sources',
                     # 'actors',
                     # 'unit_groups',
                     ]
# flows_uslci = {file.name for file in 
#                (PATH_DPKG / dpkg_dir['uslci'] / 'flows').glob('*.json')}
flows_fedefl = {file.name for file in 
                (PATH_DPKG / dpkg_dir['fedefl'] / 'flows').glob('*.json')}
files_to_ignore = {'openlca.json', 'categories.json'} | flows_fedefl

objs = {
    dpkg:
        {_type: set(
            [file.stem for file in 
             (PATH_DPKG / dpkg_dir[dpkg]).glob(f'{_type}/*.json')
             if file.name not in files_to_ignore])
        for _type in types_of_interest}
    for dpkg in dpkg_build}

dup_count = {
    dpkg:
        {_type: 
            {'N': len(objs[dpkg][_type])} |
            {f'vs_{dpkg_other}': len(objs[dpkg][_type] & objs[dpkg_other][_type])
             for dpkg_other in (dpkg_build - set([dpkg]))}
         for _type in types_of_interest}
    for dpkg in dpkg_build}

  
# %% Identify duplicates for manual inspection
# dpkg_pairs = set(it.combinations(dpkg_build - {'uslci+', 'useeio'}, 2))
dpkg_pairs = set(it.combinations(dpkg_build - {'uslci+'}, 2))

dups = {f'{dpkg1}_{dpkg2}': 
           {_type: objs[dpkg1][_type] & objs[dpkg2][_type]
            # for _type in ['flows']}
            for _type in types_of_interest}
        for (dpkg1, dpkg2) in dpkg_pairs}
   

# %% Compile parent dpkg of single-provider duplicate flows

# files = {stem: [file for file in PATH_DPKG.glob(f'**/{stem}.json')]
#          for stem in dups['woody_uslci']['flows']}

# read in dedup_ignore = deduplicate_manual.yaml
# override logic for objs present
# write out deduplicate.yaml

# If flow has no provider, attribute to USLCI for now 
    # affects USEEIO tech flows, plus tech and waste CUTOFFs

## Jsonata
# process.Exchange.isQuantitativeReference=True -> get .Exchange.flow.@id
# `"isQuantitativeReference": true` --> 

# also, any (.@type, .name) dups w/ different UUIDs?


# %% Screen USLCI+ for Missing or Extraneous Objects

# # Find feedstock dpkg objs not present in USLCI+
# diff_feedstock = {
#     dpkg:
#         {_type: 
#             {'N': len(objs[dpkg][_type])} |
#             {'not_in_uslci+': len(objs[dpkg][_type] - objs['uslci+'][_type])}
#          for _type in types_of_interest}
#     for dpkg in (dpkg_build - set(['uslci+']))}

# # Find USLCI+ objs not present in feedstocks
# diff_build = {
#     f'uslci+_vs_{dpkg}':
#         {_type: 
#             {'N': len(objs['uslci+'][_type])} |
#             {f'N_{dpkg}': len(objs[dpkg][_type]),
#              f'not_in_{dpkg}': len(objs['uslci+'][_type] - objs[dpkg][_type]),
#              'check': ((len(objs["uslci+"][_type] - objs[dpkg][_type])) == 
#                        (len(objs["uslci+"][_type]) - len(objs[dpkg][_type])))
#              }
#          for _type in types_of_interest}
#     for dpkg in (dpkg_build - set(['uslci+']))}
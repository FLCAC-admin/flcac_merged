# USLCI+ build script
The following guidance uses the [`pixi`](https://pixi.prefix.dev/) environment 
manager installed on a Windows machine, but the script works across
platforms and with any preferred Python package/environment manager.

Additionally, after cloning the repo locally, copy+paste a data.gov API key (registration [here](https://api.data.gov/signup/)) into the `API_KEY` field of the .env file.

## Getting Started
```bash
# Clone the repo locally and `cd` into it
git clone https://github.com/FLCAC-admin/uslci_plus.git && cd uslci_plus

# Add an API key to .env

# Build the USLCI+.zip archive
pixi run python uslci_plus.py
```

## Developer Setup
```bash
# Clone the repo locally and `cd` into it
git clone https://github.com/FLCAC-admin/uslci_plus.git && cd uslci_plus

# Add an API key to .env

# Install and activate the dev environment, then open the IDE
pixi shell -e dev spyder
```

For more `pixi` commands, check out the [Basic usage of Pixi](https://pixi.prefix.dev/latest/getting_started/) docs.

# Glossary
- **build**: the database (DB) assembled from a manifest, minted into an `olca-schema` [.ZIP package](greendelta.github.io/olca-schema/#zip-packages)
- **data package (dpkg)**: a collection of [`olca-schema`](https://greendelta.github.io/olca-schema/) data objects, typically stored in an [FLCAC repo](https://www.lcacommons.gov/lca-collaboration/)
- **dependency**: a data package, specified by alias and version (e.g., (`<alias> = "<version>"`),  integrated into a build
- **duplicate**: an `olca-schema` object with multiple instances across dpkgs, as identified by UUID
- **manifest**: a TOML recipe (e.g., the default [USLCI+ manifest](ldpm/config/manifest.toml)) for a build containing metadata and dependencies—the list of dpkg specifiers  to integrate into the build
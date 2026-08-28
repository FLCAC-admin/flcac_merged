# `FLCAC_Merged` build script
The following guidance uses the [`pixi`](https://pixi.prefix.dev/) environment 
manager installed on a Windows machine, but the script works across
platforms and with any preferred Python package/environment manager.

Additionally, after cloning the repo locally, copy+paste a data.gov API key (registration [here](https://api.data.gov/signup/)) into the `API_KEY` field of the .env.example file and rename it as `.env`.

## Getting Started
To build `FLCAC_Merged` using the [default manifest](ldpm/config/manifest.toml):
```console
# Clone the repo locally and `cd` into it
git clone https://github.com/FLCAC-admin/flcac_merged.git && cd flcac_merged

# Add an API key to your .env file

# Build the FLCAC_Merged_vX.Y.Z.zip archive
pixi run flcac_merged

# Or
pixi run python -m ldpm

# Or 
pixi shell
flcac_merged
```

## Developer Setup
```console
# Clone the repo locally and `cd` into it
git clone https://github.com/FLCAC-admin/flcac_merged.git && cd flcac_merged

# Add an API key to your .env file

# Install and activate the dev environment, then open an IDE (e.g., spyder)
pixi shell -e dev spyder
```

For more `pixi` commands, check out the [Basic usage of Pixi](https://pixi.prefix.dev/latest/getting_started/) docs.

# Glossary
- **build**: the database (DB) assembled from a manifest, minted into an `olca-schema` [.ZIP package](greendelta.github.io/olca-schema/#zip-packages)
- **data package (dpkg)**: a collection of [`olca-schema`](https://greendelta.github.io/olca-schema/) data objects, typically stored in an [FLCAC repo](https://www.lcacommons.gov/lca-collaboration/)
- **dependency**: a data package integrated into a build, as specified by name and version (e.g., (`<name> = "<version>"`)
- **duplicate**: an `olca-schema` object with multiple instances across dpkgs, as identified by UUID
- **manifest**: a TOML recipe (e.g., the default [FLCAC_Merged manifest](ldpm/config/manifest.toml)) for a build, containing metadata and dependencies
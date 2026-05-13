# USLCI+ build script
The following guidance uses the [`pixi`](https://pixi.prefix.dev/) environment 
manager installed on a Windows machine, but the script works across
platforms and with any preferred Python package/environment manager.

## Getting Started
```bash
# Clone the repo locally and `cd` into it
git clone https://github.com/FLCAC-admin/uslci_plus.git && cd uslci_plus
# Build the USLCI+.zip archive
pixi run python uslci_plus.py
```

## Developer Setup
```bash
# Clone the repo locally and `cd` into it
git clone https://github.com/FLCAC-admin/uslci_plus.git && cd uslci_plus
# Install and activate the dev environment, then open the IDE
pixi shell -e dev spyder
```

For more `pixi` commands, check out the [Basic usage of Pixi](https://pixi.prefix.dev/latest/getting_started/) docs.
# ldpm
LCA Data Package Manager (LDPM) utility for knitting packages of [olca-schema](https://greendelta.github.io/olca-schema/) 
data objects into cohesive LCA databases.

## Installation

You can install `ldpm` via `pip`'s [VCS install syntax](https://pip.pypa.io/en/stable/topics/vcs-support/):

```console
$ pip install git+https://github.com/FLCAC-admin/uslci_plus.git
```

For editable installs, `pip` can also work, or try [pixi](https://pixi.prefix.dev):

```console
git clone https://github.com/FLCAC-admin/uslci_plus.git && cd uslci_plus
pixi install
```

## Usage

### Getting Started

To build USLCI+ using the [default manifest](config/manifest.toml):

```console
pixi shell
python uslci_plus
```

### Custom Manifests

<!-- ToDo: add spec -->

## License

Distributed under the terms of the [MIT license](../License), 
`ldpm` is free and open source software.

## Issues

If you encounter any problems, please file an [issue][Issue Tracker] 
and include a detailed description and/or steps to reproduce the problem.

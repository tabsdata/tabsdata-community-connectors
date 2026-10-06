# Tabsdata community connectors

Community connectors add support for systems that are not built into [Tabsdata](https://docs.tabsdata.com).

Each connector lives in its own folder and Python package. Tabsdata discovers installed connectors through the `tabsdatak.connectors` entry point, the same way it loads built-in connectors.

| Connector | Folder | Source | Destination | Package |
| --- | --- | --- | --- | --- |
| MotherDuck | [`motherduck/`](motherduck/) | yes | yes | `tabsdata-conn-motherduck` |
| Apache Doris | [`doris/`](doris/) | no | yes | `tabsdata-conn-doris` |

These connectors require Tabsdata 2.1 or later.

## Install a connector

Install the connector both where you run `tdk` and in the Tabsdata server function environment.

First, install it in your local `tdk` environment:

```bash
pip install "tabsdata-conn-motherduck @ git+https://github.com/tabsdata/tabsdata-community-connectors.git#subdirectory=motherduck"
```

Then install it in the server function environment:

```bash
echo "tabsdata-conn-motherduck @ git+https://github.com/tabsdata/tabsdata-community-connectors.git#subdirectory=motherduck" > requirements-fn.txt

tdkserver venv update --instance tabsdata --name fn --requirements requirements-fn.txt --yes
tdkserver start --instance tabsdata --yes
```

`venv update` stops the server, so it needs to be started again afterward.

For another connector, replace `motherduck` with that connector's folder and package name.

Each connector's README includes its connection setup, configuration options, and errors.

## Connector layout

```text
<connector>/
  pyproject.toml
  README.md
  src/tabsdata_<name>/
    __init__.py
    _plugin.py
    error.py
```

`pyproject.toml` contains the package metadata and `tabsdatak.connectors` entry point.

Inside `src/tabsdata_<name>/`:

- `__init__.py` defines the connection and source or destination configuration classes.
- `_plugin.py` contains the connector implementation and its `SrcDef` or `DestDef`.
- `error.py` defines connector error codes.
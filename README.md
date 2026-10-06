# Tabsdata community connectors

Connectors for [Tabsdata](https://docs.tabsdata.com) that aren't built in. Each
folder is its own Python package. Tabsdata discovers it through the
`tabsdatak.connectors` entry point, the same way it discovers its built-in
connectors.

| Connector | Folder | Source | Destination | Package |
| --- | --- | --- | --- | --- |
| MotherDuck | [`motherduck/`](motherduck/) | yes | yes | `tabsdata-conn-motherduck` |
| Apache Doris | [`doris/`](doris/) | no | yes | `tabsdata-conn-doris` |

All connectors require Tabsdata 2.1.

## Installing a connector

A connector has to be installed in two places:

1. Where you run `tdk`, so it can validate connections and register functions:

   ```bash
   pip install "tabsdata-conn-motherduck @ git+https://github.com/tabsdata/tabsdata-community-connectors.git#subdirectory=motherduck"
   ```

2. In the server's function environment, where the functions run. `venv update`
   stops the server, so start it again afterwards:

   ```bash
   echo "tabsdata-conn-motherduck @ git+https://github.com/tabsdata/tabsdata-community-connectors.git#subdirectory=motherduck" > requirements-fn.txt
   tdkserver venv update --instance tabsdata --name fn --requirements requirements-fn.txt --yes
   tdkserver start --instance tabsdata --yes
   ```

Swap `motherduck` for the connector's folder and package name. Each connector's
README covers its connection, configuration and errors.

## Layout of a connector

```
<connector>/
  pyproject.toml          package metadata and the tabsdatak.connectors entry points
  README.md
  src/tabsdata_<name>/
    __init__.py           connection and source/destination config classes
    _plugin.py            the plugin that reads or writes, and its SrcDef/DestDef
    error.py              error codes
```

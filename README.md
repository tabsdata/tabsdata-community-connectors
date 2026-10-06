# Tabsdata community connectors

Community connectors add support for systems that are not built into [Tabsdata](https://docs.tabsdata.com).

Each connector lives in its own folder and Python package. Tabsdata discovers installed connectors through the `tabsdatak.connectors` entry point, the same way it loads built-in connectors.

| Connector | Folder | Source | Destination | Package |
| --- | --- | --- | --- | --- |
| MotherDuck | [`motherduck/`](motherduck/) | yes | yes | `tabsdata-conn-motherduck` |
| Apache Doris | [`doris/`](doris/) | yes | yes | `tabsdata-conn-doris` |

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
## Connector layout

```text
<connector>/
  pyproject.toml
  README.md
  marketplace.toml
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

## Docs site listing

Every connector is listed on the [Community Connectors](https://docs.tabsdata.com/community-connectors) page of the Tabsdata docs, and its README becomes the connector's page there.

An optional `marketplace.toml` in the connector folder sets how the connector is listed. It is separate from `pyproject.toml`, so listing changes never touch the package:

```toml
name = "MotherDuck"
summary = "Reads tables from MotherDuck into a Tabsdata publisher and writes subscriber tables back to MotherDuck."
icon = "icon.svg"
```

| Key | Used for | When it's left out |
| --- | --- | --- |
| `name` | the connector's name on its card | the `system` of its `SrcDef` or `DestDef` |
| `summary` | the page description shown in search results | the first paragraph of the README |
| `icon` | the image on the card and the page, an `.svg`, `.png`, `.jpg` or `.webp` path relative to the connector folder | a two-letter monogram of the name |

A square SVG reads best, since the icon is shown at 40 and 52 pixels.
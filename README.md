# Tabsdata community connectors

Community connectors add support for systems that are not built into [Tabsdata](https://docs.tabsdata.com).

Each connector lives in its own folder and Python package. Tabsdata discovers installed connectors through the `tabsdatak.connectors` entry point, the same way it loads built-in connectors.

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

Connectors in this repo arelisted on the [Community Connectors](https://docs.tabsdata.com/community-connectors) page of the Tabsdata docs.

An optional `marketplace.toml` in the connector folder sets how the connector is listed. 

```toml
name = "MotherDuck"
summary = "Reads tables from MotherDuck into a Tabsdata publisher and writes subscriber tables back to MotherDuck."
icon = "icon.svg"
```

| Key | Used for | When it's left out |
| --- | --- | --- |
| `name` | the connector's name on its card | 
| `summary` | the page description shown in search results | 
| `icon` | the image on the card and the page, an `.svg`, `.png`, `.jpg` or `.webp` path relative to the connector folder | 

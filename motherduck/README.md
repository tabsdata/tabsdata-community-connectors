# MotherDuck connector

Reads tables from [MotherDuck](https://motherduck.com) into a Tabsdata publisher
and writes subscriber tables back to MotherDuck.

| Role | Connection | Config | Registered as |
| --- | --- | --- | --- |
| Source | `MotherDuckSrcConn` | `MotherDuckSrc` | `motherduck-in` |
| Destination | `MotherDuckDestConn` | `MotherDuckDest` | `motherduck-out` |

Requires Tabsdata 2.1 and `duckdb` 1.3 or later.

## Install

Install the connector where you run `tdk`, so it can validate the connection and
register functions:

```bash
pip install "tabsdata-conn-motherduck @ git+https://github.com/tabsdata/tabsdata-community-connectors.git#subdirectory=motherduck"
```

Then install it in the server's function environment, where the functions run.
`venv update` stops the server, so start it again afterwards:

```bash
echo "tabsdata-conn-motherduck @ git+https://github.com/tabsdata/tabsdata-community-connectors.git#subdirectory=motherduck" > requirements-fn.txt
tdkserver venv update --instance tabsdata --name fn --requirements requirements-fn.txt --yes
tdkserver start --instance tabsdata --yes
```

## Connections

The connection is registered on the collection, with the token stored as a
secret. `tdk` resolves `$secret:MOTHERDUCK__TOKEN` from the environment variable
of the same name.

Source connection. The token only needs read access, and the database has to
exist:

```yaml
kind: connectionDef
apiVersion: '1.0'
type: tabsdata_motherduck:MotherDuckSrcConn
spec:
  token: $secret:MOTHERDUCK__TOKEN
  database: str:my_db
```

Destination connection. The token needs write access, and the database is
created if it doesn't exist:

```yaml
kind: connectionDef
apiVersion: '1.0'
type: tabsdata_motherduck:MotherDuckDestConn
spec:
  token: $secret:MOTHERDUCK__TOKEN
  database: str:my_db
```

Source and destination connections are separate types, the same as the built-in
connectors, so a read-only token can't be registered for a destination.

```bash
tdk collection create -P my_project --name md_landing --group sources --conn-file md_src.yaml
```

## Source

`MotherDuckSrc` runs each query in `queries` against the connection's database
and publishes each result as one table, in the order of `output_tables`. The
result streams from MotherDuck into a local parquet file, so a large table is
never held in memory.

```python
from tabsdatak.api import TrxCtx, publisher
from tabsdata_motherduck import MotherDuckSrc


@publisher(
    source=MotherDuckSrc(queries=["SELECT * FROM customers", "SELECT * FROM orders"]),
    output_tables=["customers", "orders"],
)
def from_motherduck(customers, orders):
    return customers, orders
```

### Incremental reads

A query can reference DuckDB bind parameters as `$name`. `initial_values` gives
each parameter its value on the first run. On later runs the parameter takes the
value the publisher stored with `ctx.set_attr(name, value)`, so a query can read
only the rows added since the last run:

```python
@publisher(
    source=MotherDuckSrc(
        queries=["SELECT * FROM orders WHERE order_id > $last_id ORDER BY order_id"],
        initial_values={"last_id": 0},
    ),
    output_tables=["new_orders"],
)
def new_orders(orders, ctx: TrxCtx):
    row = orders.max_for("order_id")
    if row is not None and row["order_id"] is not None:
        ctx.set_attr("last_id", int(row["order_id"]))
    return orders
```

Each query is only sent the parameters it references, so one `initial_values`
dict can serve several queries. A query that references a parameter missing from
`initial_values` fails.

## Destination

`MotherDuckDest` writes each input table to the table at the same position in
`tables`. Table names are plain identifiers: letters, digits and underscores, not
starting with a digit.

```python
from tabsdatak.api import subscriber
from tabsdata_motherduck import MotherDuckDest


@subscriber(
    input_tables=["gold/dim_customer", "gold/fct_orders"],
    destination=MotherDuckDest(tables=["dim_customer", "fct_orders"]),
)
def to_motherduck(dim_customer, fct_orders):
    return dim_customer, fct_orders
```

| `if_table_exists` | What happens |
| --- | --- |
| `replace` (default) | each table is recreated from the new data |
| `append` | the rows are inserted, matched to the table's columns by name; the table is created if it doesn't exist |

DuckDB reads the parquet files Tabsdata hands the subscriber and bulk loads them,
instead of sending insert statements. All tables load in one transaction, so a
failure on any table rolls back every table in the run. A table the subscriber
returns `None` for is skipped.

## Errors

| Code | Raised when |
| --- | --- |
| `MOTHERDUCK_1`, `MOTHERDUCK_2` | the destination connection or `MotherDuckDest` fails validation |
| `MOTHERDUCK_3` | the subscriber returns a different number of tables than `tables` lists |
| `MOTHERDUCK_4` | writing a table fails |
| `MOTHERDUCK_5`, `MOTHERDUCK_6` | the source connection or `MotherDuckSrc` fails validation, for example an empty `queries` list |
| `MOTHERDUCK_7` | connecting to MotherDuck or switching to the database fails |
| `MOTHERDUCK_8` | a source query fails |

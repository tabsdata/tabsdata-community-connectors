# MotherDuck connector

The MotherDuck connector lets Tabsdata read data from [MotherDuck](https://motherduck.com) with publisher functions and write data to MotherDuck with subscriber functions.

## Installing the connector

The connector package must be installed in two places:

1. Wherever you run `tdk`, so Tabsdata can validate connections and register functions.
2. In the Tabsdata server's function environment, so the connector is available when functions run.

### Step 1: Install the package where `tdk` runs

```bash
pip install "tabsdata-conn-motherduck @ git+https://github.com/tabsdata/tabsdata-community-connectors.git#subdirectory=motherduck"
```

### Step 2: Add the package to the server's function environment

Add the connector to a `requirements.txt` file:

```text
tabsdata-conn-motherduck @ git+https://github.com/tabsdata/tabsdata-community-connectors.git#subdirectory=motherduck
```

Then update the function and agent environments and restart the server:

```bash
tdkserver venv update --instance <instance> --name fn --requirements requirements.txt
tdkserver venv update --instance <instance> --name ag --requirements requirements.txt
tdkserver start --instance <instance>
```

> **Warning:** `tdkserver venv update` overwrites the existing package requirements for the function environment. Include every package the environment still needs in `requirements.txt`, not just this connector.

## Publisher

Use `MotherDuckSrc` in a publisher function to run queries against MotherDuck and publish the results as Tabsdata tables.

### Step 1: Generate the connection document

```bash
tdk connection template --type motherduck-in --file conn-motherduck-in.yaml
```

### Step 2: Configure the connection

Set the MotherDuck token and the database to query:

```yaml
kind: connectionDef
apiVersion: '1.0'
type: tabsdata_motherduck:MotherDuckSrcConn
spec:
  token: secret:MOTHERDUCK__TOKEN
  database: str:my_db
```

The `secret:` prefix tells Tabsdata to store the token as a secret and remove it from the stored connection document. The `str:` prefix keeps a value as plain text, so the database name stays visible in the connection document.

### Step 3: Attach the connection to a collection

```bash
tdk collection update --name md_landing --conn-file conn-motherduck-in.yaml
```

To create a new collection with the connection instead, pass the same `--conn-file` to `tdk collection create`.

### Step 4: Create a publisher

Configure `MotherDuckSrc` as the publisher's `source` and list the `queries` to run.

```python
from tabsdatak.api import TableFrameSpec, publisher
from tabsdata_motherduck import MotherDuckSrc


@publisher(
    source=MotherDuckSrc(
        queries=[
            "SELECT * FROM customers",
            "SELECT * FROM orders",
        ]
    ),
    output_tables=["customers", "orders"],
)
def pub_motherduck(
    customers: TableFrameSpec, orders: TableFrameSpec
) -> tuple[TableFrameSpec, TableFrameSpec]:
    return customers, orders
```

Register the function:

```bash
tdk fn register --coll md_landing --path pub_motherduck.py::pub_motherduck
```

### Connection parameters

#### `token`

A MotherDuck access token. For publishers, it only needs read access.

#### `database`

The MotherDuck database the queries run against.

### Publisher parameters

#### `queries`

The SQL queries to run against MotherDuck.

Each query represents one source slot and maps positionally to an argument in the publisher function.

#### `initial_values`

Starting values for bind parameters, written as `$name` in a query.

The first run uses the starting value. After that, the parameter uses whatever the publisher saved with `ctx.set_attr(name, value)`.

You can use this to read only the rows added since the last run:

```python
from tabsdatak.api import TableFrameSpec, TrxCtx, publisher
from tabsdata_motherduck import MotherDuckSrc


@publisher(
    source=MotherDuckSrc(
        queries=[
            "SELECT * FROM orders "
            "WHERE order_id > $last_id "
            "ORDER BY order_id"
        ],
        initial_values={"last_id": 0},
    ),
    output_tables=["new_orders"],
)
def new_orders(orders: TableFrameSpec, ctx: TrxCtx) -> TableFrameSpec:
    row = orders.max_for("order_id")

    if row is not None and row["order_id"] is not None:
        ctx.set_attr("last_id", int(row["order_id"]))

    return orders
```

## Subscriber

Use `MotherDuckDest` in a subscriber function to write Tabsdata tables to MotherDuck.

### Step 1: Generate the connection document

```bash
tdk connection template --type motherduck-out --file conn-motherduck-out.yaml
```

### Step 2: Configure the connection

Set the MotherDuck token and the database to write to:

```yaml
kind: connectionDef
apiVersion: '1.0'
type: tabsdata_motherduck:MotherDuckDestConn
spec:
  token: secret:MOTHERDUCK__TOKEN
  database: str:my_db
```

### Step 3: Attach the connection to a collection

```bash
tdk collection update --name md_export --conn-file conn-motherduck-out.yaml
```

To create a new collection with the connection instead, pass the same `--conn-file` to `tdk collection create`.

### Step 4: Create a subscriber

Configure `MotherDuckDest` as the subscriber's `destination`.

Each table in `tables` maps positionally to a value returned by the subscriber function.

```python
from tabsdatak.api import TableFrameSpec, subscriber
from tabsdata_motherduck import MotherDuckDest


@subscriber(
    input_tables=[
        "md_landing/customers",
        "md_landing/orders",
    ],
    destination=MotherDuckDest(
        tables=[
            "customers",
            "orders",
        ]
    ),
)
def sub_motherduck(
    customers: TableFrameSpec, orders: TableFrameSpec
) -> tuple[TableFrameSpec, TableFrameSpec]:
    return customers, orders
```

Register the function:

```bash
tdk fn register --coll md_export --path sub_motherduck.py::sub_motherduck
```

### Connection parameters

#### `token`

A MotherDuck access token. For subscribers, it needs write access.

#### `database`

The MotherDuck database to write to. It's created if it doesn't exist.

### Subscriber parameters

#### `tables`

The MotherDuck tables to write to.

Each table represents one destination slot and maps positionally to a value returned by the subscriber function.

Table names can use letters, digits, and underscores, and can't start with a digit.

All tables in a run are written in one transaction. If any table fails, none of them are written.

#### `if_table_exists`

What to do when a table already exists.

`"replace"` is the default. It drops the table and recreates it with the new data:

```python
destination=MotherDuckDest(
    tables=["orders"],
    if_table_exists="replace",
)
```

`"append"` adds the new rows to the existing table, matching columns by name. The table is created if it doesn't exist:

```python
destination=MotherDuckDest(
    tables=["orders"],
    if_table_exists="append",
)
```

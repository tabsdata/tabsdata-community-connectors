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

Then update the function environment and restart the server:

```bash
tdkserver venv update --name fn --requirements requirements.txt
tdkserver start
```

> **Warning:** `tdkserver venv update` overwrites the existing package requirements for the function environment. Include every package the environment still needs in `requirements.txt`, not just this connector.

## Publisher

Use `MotherDuckSrc` in a publisher function to run queries against MotherDuck and publish the results as Tabsdata tables.

### Step 1: Generate the connection document

```bash
tdk connection template --type motherduck-in --file conn-motherduck-in.yaml
```

### Step 2: Configure the connection

Set the MotherDuck API token and the database the publisher will query:

```yaml
kind: connectionDef
apiVersion: '1.0'
type: tabsdata_motherduck:MotherDuckSrcConn
spec:
  token: $secret:MOTHERDUCK__TOKEN
  database: str:my_db
```

`tdk` resolves `$secret:MOTHERDUCK__TOKEN` from the environment variable with the same name and stores it as a secret.

### Step 3: Attach the connection to a collection

```bash
tdk collection update --name md_landing --conn-file conn-motherduck-in.yaml
```

To create a new collection with the connection instead, pass the same `--conn-file` to `tdk collection create`.

### Step 4: Create a publisher

Configure `MotherDuckSrc` as the publisher's `source`. Each query maps positionally to an output table and function argument.

```python
from tabsdatak.api import publisher
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
def pub_motherduck(customers, orders):
    return customers, orders
```

Register the function:

```bash
tdk fn register --coll md_landing --path pub_motherduck.py::pub_motherduck
```

### Connection parameters

#### `token`

The MotherDuck access token.

For publishers, the token only needs read access.

#### `database`

The MotherDuck database the queries run against.

The database must already exist.

### Publisher parameters

#### `queries`

Defines the SQL queries the publisher runs against MotherDuck.

Each query represents one source slot and maps positionally to an argument in the publisher function.

For example:

```python
@publisher(
    source=MotherDuckSrc(
        queries=[
            "SELECT * FROM customers",
            "SELECT * FROM orders",
        ]
    ),
    output_tables=["customers", "orders"],
)
def pub_motherduck(customers, orders):
    return customers, orders
```

#### `initial_values`

Defines initial values for DuckDB bind parameters written as `$name` inside a query.

The initial value is used on the first run. On later runs, the parameter uses the value stored by the publisher with `ctx.set_attr(name, value)`.

This can be used to incrementally read rows added since the previous run:

```python
from tabsdatak.api import TrxCtx, publisher
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
def new_orders(orders, ctx: TrxCtx):
    row = orders.max_for("order_id")

    if row is not None and row["order_id"] is not None:
        ctx.set_attr("last_id", int(row["order_id"]))

    return orders
```

Each query receives only the parameters it references.

A query that references a parameter that is missing from `initial_values` fails.

## Subscriber

Use `MotherDuckDest` in a subscriber function to write Tabsdata tables into MotherDuck.

### Step 1: Generate the connection document

```bash
tdk connection template --type motherduck-out --file conn-motherduck-out.yaml
```

### Step 2: Configure the connection

Set the MotherDuck API token and destination database:

```yaml
kind: connectionDef
apiVersion: '1.0'
type: tabsdata_motherduck:MotherDuckDestConn
spec:
  token: $secret:MOTHERDUCK__TOKEN
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
from tabsdatak.api import subscriber
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
def sub_motherduck(customers, orders):
    return customers, orders
```

Register the function:

```bash
tdk fn register --coll md_export --path sub_motherduck.py::sub_motherduck
```

### Connection parameters

#### `token`

The MotherDuck access token.

For subscribers, the token needs write access.

#### `database`

The MotherDuck database the subscriber writes to.

The database is created if it does not already exist.

### Subscriber parameters

#### `tables`

Defines the destination tables the subscriber writes to in MotherDuck.

Each table represents one destination slot and maps positionally to a value returned by the subscriber function.

Table names can contain letters, digits, and underscores, but cannot start with a digit.

All tables in a subscriber run are written in one transaction. If writing any table fails, all table writes from that run are rolled back.

#### `if_table_exists`

Controls what happens when a destination table already exists.

`"replace"` is the default and recreates the table from the new data:

```python
destination=MotherDuckDest(
    tables=["orders"],
    if_table_exists="replace",
)
```

`"append"` inserts the new rows into the existing table and matches columns by name. If the table does not exist, it is created automatically:

```python
destination=MotherDuckDest(
    tables=["orders"],
    if_table_exists="append",
)
```
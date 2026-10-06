# MotherDuck connector

The MotherDuck connector lets Tabsdata run queries against [MotherDuck](https://motherduck.com) and write tables into MotherDuck.

The `MotherDuckSrc` connector can be used by a publisher function to read data from MotherDuck into a Tabsdata table.

The `MotherDuckDest` connector can be used by a subscriber function to write data from a Tabsdata table into MotherDuck.

## Installing the connector

The connector package must be installed in two places:

1. Wherever `tdk` runs locally, so Tabsdata can validate connections and register functions.
2. In the server's function environment, so the connector is available when those functions execute.

### Step 1: Install the package where tdk runs

```bash
pip install "tabsdata-conn-motherduck @ git+https://github.com/tabsdata/tabsdata-community-connectors.git#subdirectory=motherduck"
```

### Step 2: Add the package to the server's function environment

Add this line to a `requirements.txt`:

```text
tabsdata-conn-motherduck @ git+https://github.com/tabsdata/tabsdata-community-connectors.git#subdirectory=motherduck
```

### Step 3: Update the server's function environment

```bash
tdkserver venv update --name fn --requirements requirements.txt
tdkserver start
```

`tdkserver venv update` stops the server, so `tdkserver start` starts it again.

> **Warning:** `tdkserver venv update` overwrites Tabsdata's existing list of package requirements. Include every package the environment still needs in `requirements.txt`, not just this one.

## Using the connector

### Step 4: Generate the connection documents

Run `tdk connection types` to confirm that `tdk` can discover `motherduck-in` and `motherduck-out`, then generate the template for each connection you need:

```bash
# for a publisher
tdk connection template --type motherduck-in --file conn-motherduck-in.yaml

# for a subscriber
tdk connection template --type motherduck-out --file conn-motherduck-out.yaml
```

### Step 5: Fill out the connection documents

The `spec` fields are described under [Publisher](#publisher) and [Subscriber](#subscriber) below. A completed publisher connection looks like this:

```yaml
kind: connectionDef
apiVersion: '1.0'
type: tabsdata_motherduck:MotherDuckSrcConn
spec:
  token: $secret:MOTHERDUCK__TOKEN
  database: str:my_db
```

`tdk` resolves `$secret:MOTHERDUCK__TOKEN` from the environment variable of the same name and stores it as a secret.

### Step 6: Attach the connections to collections

```bash
tdk collection update --name md_landing --conn-file conn-motherduck-in.yaml
```

To create a new collection with the connection instead, pass the same `--conn-file` to `tdk collection create`.

### Step 7: Use the connector in a function

```python
from tabsdatak.api import publisher
from tabsdata_motherduck import MotherDuckSrc


@publisher(
    source=MotherDuckSrc(queries=["SELECT * FROM customers", "SELECT * FROM orders"]),
    output_tables=["customers", "orders"],
)
def pub_motherduck(customers, orders):
    return customers, orders
```

Register it into Tabsdata:

```bash
tdk fn register --coll md_landing --path pub_motherduck.py::pub_motherduck
```

## Publisher

The `MotherDuckSrc` connector can be used by a publisher function to read data from MotherDuck into a Tabsdata table.

### Connection

MotherDuck publishers use `MotherDuckSrcConn` to define the token and database the publisher's queries run against.

```yaml
kind: connectionDef
apiVersion: '1.0'
type: tabsdata_motherduck:MotherDuckSrcConn
spec:
  token: $secret:MOTHERDUCK__TOKEN
  database: str:my_db
```

### Connection Config Parameters

#### `token`

The MotherDuck access token. It only needs read access.

#### `database`

The MotherDuck database the queries run against. The database has to exist.

### Publisher

Configure `MotherDuckSrc` as the `source` of a publisher function to select the queries that Tabsdata runs against MotherDuck.

```python
from tabsdatak.api import publisher
from tabsdata_motherduck import MotherDuckSrc


@publisher(
    source=MotherDuckSrc(queries=["SELECT * FROM orders"]),
    output_tables=["orders"],
)
def publish_orders(orders):
    return orders
```

### Function Config Parameters

#### `queries`

Defines the SQL queries the publisher runs against MotherDuck.

Each element in `queries` represents one source slot and maps positionally to an argument in the publisher function.

#### `initial_values`

Values for the DuckDB bind parameters, written `$name`, inside `queries`. A value is used on the first run only. On later runs the parameter takes the value the publisher stored with `ctx.set_attr(name, value)`, so a query can read only the rows added since the last run:

```python
from tabsdatak.api import TrxCtx, publisher
from tabsdata_motherduck import MotherDuckSrc


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

Each query is sent only the parameters it references. A query that references a parameter missing from `initial_values` fails.

## Subscriber

The `MotherDuckDest` connector can be used by a subscriber function to write data from a Tabsdata table into MotherDuck.

### Connection

MotherDuck subscribers use `MotherDuckDestConn` to define the token and database the subscriber writes to.

```yaml
kind: connectionDef
apiVersion: '1.0'
type: tabsdata_motherduck:MotherDuckDestConn
spec:
  token: $secret:MOTHERDUCK__TOKEN
  database: str:my_db
```

### Connection Config Parameters

#### `token`

The MotherDuck access token. It needs write access.

#### `database`

The MotherDuck database the subscriber writes to. The database is created if it doesn't exist.

### Subscriber

Configure `MotherDuckDest` as the `destination` of a subscriber function to define which tables Tabsdata writes in MotherDuck.

```python
from tabsdatak.api import subscriber
from tabsdata_motherduck import MotherDuckDest


@subscriber(
    input_tables=["orders"],
    destination=MotherDuckDest(tables=["orders"]),
)
def write_orders(orders):
    return orders
```

### Function Config Parameters

#### `tables`

Defines the destination tables the subscriber writes to in MotherDuck.

Each element in `tables` represents one destination slot and maps positionally to a value returned by the subscriber function. Table names can contain letters, digits and underscores, and can't start with a digit. All tables are written in one transaction, so a failure on any table rolls back every table in the run.

#### `if_table_exists`

Controls what happens when the destination table already exists. `"replace"` (the default) recreates the table from the new data. `"append"` inserts the rows, matched to the table's columns by name, and creates the table if it doesn't exist.

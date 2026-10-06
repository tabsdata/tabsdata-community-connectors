# Apache Doris connector

The Apache Doris connector lets Tabsdata read data from [Apache Doris](https://doris.apache.org) with publisher functions and write data to Doris with subscriber functions.

The connector requires Apache Doris 2.1 or later, with its MySQL protocol port (9030) reachable from the Tabsdata server.

## Installing the connector

The connector package must be installed in two places:

1. Wherever you run `tdk`, so Tabsdata can validate connections and register functions.
2. In the Tabsdata server's function environment, so the connector is available when functions run.

### Step 1: Install the package where `tdk` runs

```bash
pip install "tabsdata-conn-doris @ git+https://github.com/tabsdata/tabsdata-community-connectors.git#subdirectory=doris"
```

### Step 2: Add the package to the server's function environment

Add the connector to a `requirements.txt` file:

```text
tabsdata-conn-doris @ git+https://github.com/tabsdata/tabsdata-community-connectors.git#subdirectory=doris
```

Then update the function environment and restart the server:

```bash
tdkserver venv update --name fn --requirements requirements.txt
tdkserver start
```

> **Warning:** `tdkserver venv update` overwrites the existing package requirements for the function environment. Include every package the environment still needs in `requirements.txt`, not just this connector.

## Publisher

Use `DorisSrc` in a publisher function to run queries against Doris and publish the results as Tabsdata tables.

### Step 1: Generate the connection document

```bash
tdk connection template --type doris-bulk-in --file conn-doris-in.yaml
```

### Step 2: Configure the connection

Set the Doris frontend address, the credentials, and the database the publisher will query:

```yaml
kind: connectionDef
apiVersion: '1.0'
type: tabsdata_doris:DorisSrcConn
spec:
  host: str:127.0.0.1
  port: str:9030
  credentials:
    kind: userPasswordCredentials
    apiVersion: '1.0'
    type: tabsdatak.conn.common.types:UserPassword
    spec:
      user: secret:DORIS__USER
      password: secret:DORIS__PASSWORD
  database: str:my_db
```

If a `secret:` prefix is provided, Tabsdata stores the resolved user and password as secrets and removes them from the stored connection document. The `str:` prefix marks a value as plaintext, so the host, port and database name remain visible in the connection document.

### Step 3: Attach the connection to a collection

```bash
tdk collection update --name doris_landing --conn-file conn-doris-in.yaml
```

To create a new collection with the connection instead, pass the same `--conn-file` to `tdk collection create`.

### Step 4: Create a publisher

Configure `DorisSrc` as the publisher's `source` and provide `queries` to execute against your Doris database.

```python
from tabsdatak.api import publisher
from tabsdata_doris import DorisSrc


@publisher(
    source=DorisSrc(
        queries=[
            "SELECT * FROM customers",
            "SELECT * FROM orders",
        ]
    ),
    output_tables=["customers", "orders"],
)
def pub_doris(customers, orders):
    return customers, orders
```

Register the function:

```bash
tdk fn register --coll doris_landing --path pub_doris.py::pub_doris
```

### Connection parameters

#### `host`

The address of the Doris frontend.

#### `port`

The frontend's MySQL protocol port.

Defaults to `9030`.

#### `arrow_flight_port`

The frontend's `arrow_flight_sql_port`.

Only needed for `read_method="arrow_flight"`.

#### `credentials`

The Doris user and password.

For publishers, the user only needs `SELECT`.

#### `database`

The Doris database the queries run against.

#### `staging`

An S3 bucket that Doris exports query results to.

Only needed for `read_method="s3"`. See [S3 staging](#s3-staging).

### Publisher parameters

#### `queries`

Defines the SQL queries the publisher runs against Doris.

Each query represents one source slot and maps positionally to an argument in the publisher function.

For example:

```python
@publisher(
    source=DorisSrc(
        queries=[
            "SELECT * FROM customers",
            "SELECT * FROM orders",
        ]
    ),
    output_tables=["customers", "orders"],
)
def pub_doris(customers, orders):
    return customers, orders
```

#### `initial_values`

Defines initial values for bind parameters written as `:name` inside a query.

The initial value is used on the first run. On later runs, the parameter uses the value stored by the publisher with `ctx.set_attr(name, value)`.

This can be used to incrementally read rows added since the previous run:

```python
from tabsdatak.api import TrxCtx, publisher
from tabsdata_doris import DorisSrc


@publisher(
    source=DorisSrc(
        queries=[
            "SELECT * FROM orders "
            "WHERE order_id > :last_id "
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

Each `:name` is replaced with its value as an escaped SQL literal before the query is sent. A `:name` inside a string literal, a quoted identifier or a comment is left alone.

#### `read_method`

Controls how query results are read out of Doris.

`"mysql"` is the default and streams the results over the MySQL protocol. It works against any Doris, but converts every value row by row, so it is the slowest method. Column types are mapped to the closest Tabsdata type: `BOOLEAN` becomes 0 or 1, `DECIMAL` gets precision 38, and `LARGEINT`, `JSON`, `ARRAY`, `MAP` and `STRUCT` become text.

```python
source=DorisSrc(
    queries=["SELECT * FROM orders"],
    read_method="mysql",
)
```

`"arrow_flight"` fetches the results from the Doris backends as Arrow batches over [Arrow Flight SQL](https://doris.apache.org/docs/db-connect/arrow-flight-sql-connect), and column types arrive exactly as Doris stores them. Arrow Flight SQL is off by default: set `arrow_flight_sql_port` in both `fe.conf` and `be.conf`, restart Doris, and set `arrow_flight_port` on the connection. The backends have to be reachable from the Tabsdata server at the address the frontend reports for them.

```python
source=DorisSrc(
    queries=["SELECT * FROM orders"],
    read_method="arrow_flight",
)
```

`"s3"` has the Doris backends export the results to the `staging` bucket as parquet with `SELECT ... INTO OUTFILE`, then downloads the files. It suits the largest results, since every backend writes its part of the export in parallel:

```python
source=DorisSrc(
    queries=["SELECT * FROM orders"],
    read_method="s3",
)
```

## Subscriber

Use `DorisDest` in a subscriber function to load Tabsdata tables into Doris.

### Step 1: Generate the connection document

```bash
tdk connection template --type doris-bulk-out --file conn-doris-out.yaml
```

### Step 2: Configure the connection

Set the Doris frontend address, the credentials, and the destination database:

```yaml
kind: connectionDef
apiVersion: '1.0'
type: tabsdata_doris:DorisDestConn
spec:
  host: str:127.0.0.1
  port: str:9030
  http_port: str:8030
  credentials:
    kind: userPasswordCredentials
    apiVersion: '1.0'
    type: tabsdatak.conn.common.types:UserPassword
    spec:
      user: secret:DORIS__USER
      password: secret:DORIS__PASSWORD
  database: str:my_db
```

### Step 3: Attach the connection to a collection

```bash
tdk collection update --name doris_export --conn-file conn-doris-out.yaml
```

To create a new collection with the connection instead, pass the same `--conn-file` to `tdk collection create`.

### Step 4: Create a subscriber

Configure `DorisDest` as the subscriber's `destination`.

Each table in `tables` maps positionally to a value returned by the subscriber function.

```python
from tabsdatak.api import subscriber
from tabsdata_doris import DorisDest


@subscriber(
    input_tables=[
        "doris_landing/customers",
        "doris_landing/orders",
    ],
    destination=DorisDest(
        tables=[
            "customers",
            "orders",
        ]
    ),
)
def sub_doris(customers, orders):
    return customers, orders
```

Register the function:

```bash
tdk fn register --coll doris_export --path sub_doris.py::sub_doris
```

### Connection parameters

#### `host`

The address of the Doris frontend.

#### `port`

The frontend's MySQL protocol port, used to look up table columns, truncate tables and run S3 loads.

Defaults to `9030`.

#### `http_port`

The port that takes stream loads.

Defaults to `8030`, the frontend, which redirects each load to a backend. On Docker Desktop the backend's internal address isn't reachable from your machine, so use the backend's own port, `8040`, instead.

#### `credentials`

The Doris user and password.

#### `database`

The Doris database the subscriber loads into.

#### `staging`

An S3 bucket that files are staged in before Doris loads them.

Only needed for `load_method="s3"`. See [S3 staging](#s3-staging).

### Subscriber parameters

#### `tables`

Defines the destination tables the subscriber loads into in Doris.

Each table represents one destination slot and maps positionally to a value returned by the subscriber function.

The tables must already exist, since the key model, partitioning and indexes can't be inferred from the data. Columns are matched to the table's columns by name, and a column the table doesn't have fails the load.

Each table loads on its own as one Doris transaction, and strict mode fails the whole load on any row that doesn't fit the table. If a later table fails, the tables loaded before it stay loaded.

#### `if_table_exists`

Controls what happens to the rows already in a destination table.

`"append"` is the default and adds the new rows. A retried load of the same data is skipped instead of writing duplicate rows:

```python
destination=DorisDest(
    tables=["orders"],
    if_table_exists="append",
)
```

`"replace"` truncates the table, then loads. A failed load leaves the table empty until the next run:

```python
destination=DorisDest(
    tables=["orders"],
    if_table_exists="replace",
)
```

#### `load_method`

Controls how data is loaded into Doris.

`"stream_load"` is the default and sends each file straight to Doris [stream load](https://doris.apache.org/docs/data-operate/import/import-way/stream-load-manual) over HTTP. It suits small, frequent batches:

```python
destination=DorisDest(
    tables=["orders"],
    load_method="stream_load",
)
```

`"s3"` uploads each file to the `staging` bucket, then has Doris read it back with its `S3()` table function. It suits large loads, since every Doris backend reads the staged file in parallel:

```python
destination=DorisDest(
    tables=["orders"],
    load_method="s3",
)
```

## S3 staging

`read_method="s3"` and `load_method="s3"` both need a `staging` location on the connection, and the bucket has to be reachable from the Doris backends:

```yaml
  staging:
    kind: dorisS3Location
    apiVersion: '1.0'
    type: tabsdata_doris:S3Location
    spec:
      bucket: str:my-bucket
      region: str:us-east-1
      credentials:
        kind: awsAccessSecretKeyCredentials
        apiVersion: '1.0'
        type: tabsdatak.conn.common.types:AwsAccessSecretKey
        spec:
          access_key_id: secret:DORIS__S3_ACCESS_KEY
          secret_access_key: secret:DORIS__S3_SECRET_KEY
      base_path: str:/doris-staging
```

Files are written under `base_path`, with a new folder for every function run, and are left in the bucket afterwards. Add a lifecycle rule to the bucket that expires them.

The access key needs `s3:PutObject`, `s3:GetObject` and `s3:ListBucket` on the bucket.

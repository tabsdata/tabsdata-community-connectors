# Apache Doris connector

The Apache Doris connector lets Tabsdata read data from [Apache Doris](https://doris.apache.org) with publisher functions and write data to Doris with subscriber functions.

It needs Apache Doris 2.1 or later, and the Tabsdata server has to be able to reach the Doris MySQL protocol port (9030).

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

Then update the function and agent environments and restart the server:

```bash
tdkserver venv update --instance <instance> --name fn --requirements requirements.txt
tdkserver venv update --instance <instance> --name ag --requirements requirements.txt
tdkserver start --instance <instance>
```

> **Warning:** `tdkserver venv update` overwrites the existing package requirements for the function environment. Include every package the environment still needs in `requirements.txt`, not just this connector.

## Publisher

Use `DorisSrc` in a publisher function to run queries against Doris and publish the results as Tabsdata tables.

### Step 1: Generate the connection document

```bash
tdk connection template --type doris-bulk-in --file conn-doris-in.yaml
```

### Step 2: Configure the connection

Set the Doris frontend address, the credentials, and the database to query:

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

The `secret:` prefix tells Tabsdata to store the user and password as secrets and remove them from the stored connection document. The `str:` prefix keeps a value as plain text, so the host, port, and database name stay visible in the connection document.

### Step 3: Attach the connection to a collection

```bash
tdk collection update --name doris_landing --conn-file conn-doris-in.yaml
```

To create a new collection with the connection instead, pass the same `--conn-file` to `tdk collection create`.

### Step 4: Create a publisher

Configure `DorisSrc` as the publisher's `source` and list the `queries` to run.

```python
from tabsdatak.api import TableFrameSpec, publisher
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
def pub_doris(
    customers: TableFrameSpec, orders: TableFrameSpec
) -> tuple[TableFrameSpec, TableFrameSpec]:
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

The frontend's MySQL protocol port. Defaults to `9030`.

#### `arrow_flight_port`

The frontend's `arrow_flight_sql_port`. Only needed for `read_method="arrow_flight"`.

#### `credentials`

The Doris user and password. For publishers, the user only needs `SELECT`.

#### `database`

The Doris database the queries run against.

#### `staging`

An S3 bucket Doris exports query results to. Only needed for `read_method="s3"`. See [S3 staging](#s3-staging).

### Publisher parameters

#### `queries`

The SQL queries to run against Doris.

Each query represents one source slot and maps positionally to an argument in the publisher function.

#### `initial_values`

Starting values for bind parameters, written as `:name` in a query.

The first run uses the starting value. After that, the parameter uses whatever the publisher saved with `ctx.set_attr(name, value)`.

You can use this to read only the rows added since the last run:

```python
from tabsdatak.api import TableFrameSpec, TrxCtx, publisher
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
def new_orders(orders: TableFrameSpec, ctx: TrxCtx) -> TableFrameSpec:
    row = orders.max_for("order_id")

    if row is not None and row["order_id"] is not None:
        ctx.set_attr("last_id", int(row["order_id"]))

    return orders
```

Each `:name` is swapped for its value as an escaped SQL literal before the query runs. A `:name` inside a string, a quoted identifier, or a comment is left as is.

#### `read_method`

How query results are read from Doris.

`"mysql"` is the default. It streams results over the MySQL protocol and works with any Doris setup, but it's the slowest method because it converts every value row by row. Column types map to the closest Tabsdata type: `BOOLEAN` becomes 0 or 1, `DECIMAL` gets precision 38, and `LARGEINT`, `JSON`, `ARRAY`, `MAP`, and `STRUCT` become text.

```python
source=DorisSrc(
    queries=["SELECT * FROM orders"],
    read_method="mysql",
)
```

`"arrow_flight"` reads results from the Doris backends as Arrow batches over [Arrow Flight SQL](https://doris.apache.org/docs/db-connect/arrow-flight-sql-connect), and keeps the exact Doris column types. Arrow Flight SQL is off by default. To turn it on, set `arrow_flight_sql_port` in both `fe.conf` and `be.conf`, restart Doris, and set `arrow_flight_port` on the connection. The Tabsdata server has to be able to reach the backends at the address the frontend reports for them.

```python
source=DorisSrc(
    queries=["SELECT * FROM orders"],
    read_method="arrow_flight",
)
```

`"s3"` has the Doris backends export results to the `staging` bucket as parquet with `SELECT ... INTO OUTFILE`, then downloads the files. Use it for the largest results, since every backend writes its part of the export in parallel.

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

Set the Doris frontend address, the credentials, and the database to load into:

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
from tabsdatak.api import TableFrameSpec, subscriber
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
def sub_doris(
    customers: TableFrameSpec, orders: TableFrameSpec
) -> tuple[TableFrameSpec, TableFrameSpec]:
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

The frontend's MySQL protocol port. It's used to look up table columns, truncate tables, and run S3 loads. Defaults to `9030`.

#### `http_port`

The port that accepts stream loads. Defaults to `8030`, the frontend, which redirects each load to a backend.

On Docker Desktop, your machine can't reach the backend's internal address, so use the backend's own port, `8040`, instead.

#### `credentials`

The Doris user and password.

#### `database`

The Doris database to load into.

#### `staging`

An S3 bucket where files are staged before Doris loads them. Only needed for `load_method="s3"`. See [S3 staging](#s3-staging).

### Subscriber parameters

#### `tables`

The Doris tables to load into.

Each table represents one destination slot and maps positionally to a value returned by the subscriber function.

The tables must already exist, because the key model, partitioning, and indexes can't be worked out from the data. Columns are matched by name, and a column the table doesn't have fails the load.

Each table loads separately as its own Doris transaction. Strict mode fails the whole load if any row doesn't fit the table. If a later table fails, the tables loaded before it stay loaded.

#### `if_table_exists`

What to do with the rows already in a table.

`"append"` is the default. It adds the new rows. If the same data is loaded again on a retry, it's skipped instead of creating duplicates:

```python
destination=DorisDest(
    tables=["orders"],
    if_table_exists="append",
)
```

`"replace"` empties the table, then loads. If the load fails, the table stays empty until the next run:

```python
destination=DorisDest(
    tables=["orders"],
    if_table_exists="replace",
)
```

#### `load_method`

How data is loaded into Doris.

`"stream_load"` is the default. It sends each file straight to Doris [stream load](https://doris.apache.org/docs/data-operate/import/import-way/stream-load-manual) over HTTP. Use it for small, frequent batches.

```python
destination=DorisDest(
    tables=["orders"],
    load_method="stream_load",
)
```

`"s3"` uploads each file to the `staging` bucket, then has Doris read it with its `S3()` table function. Use it for large loads, since every Doris backend reads the file in parallel.

```python
destination=DorisDest(
    tables=["orders"],
    load_method="s3",
)
```

## S3 staging

`read_method="s3"` and `load_method="s3"` both need a `staging` location on the connection. The Doris backends have to be able to reach the bucket.

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

Files are written under `base_path`, in a new folder for each function run, and aren't deleted afterwards. Add a lifecycle rule to the bucket to expire them.

The access key needs `s3:PutObject`, `s3:GetObject`, and `s3:ListBucket` on the bucket.

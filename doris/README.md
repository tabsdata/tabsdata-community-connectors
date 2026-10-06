# Apache Doris connector

The Apache Doris connector lets Tabsdata run queries against [Apache Doris](https://doris.apache.org) and load tables into Doris.

The `DorisSrc` connector can be used by a publisher function to read data from Doris into a Tabsdata table.

The `DorisDest` connector can be used by a subscriber function to write data from a Tabsdata table into Doris.

The connector needs Apache Doris 2.1 or later, with its MySQL protocol port (9030) reachable from the Tabsdata server.

## Installing the connector

The connector package must be installed in two places:

1. Wherever `tdk` runs locally, so Tabsdata can validate connections and register functions.
2. In the server's function environment, so the connector is available when those functions execute.

### Step 1: Install the package where tdk runs

```bash
pip install "tabsdata-conn-doris @ git+https://github.com/tabsdata/tabsdata-community-connectors.git#subdirectory=doris"
```

### Step 2: Add the package to the server's function environment

Add this line to a `requirements.txt`:

```text
tabsdata-conn-doris @ git+https://github.com/tabsdata/tabsdata-community-connectors.git#subdirectory=doris
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

Run `tdk connection types` to confirm that `tdk` can discover `doris-bulk-in` and `doris-bulk-out`, then generate the template for each connection you need:

```bash
# for a publisher
tdk connection template --type doris-bulk-in --file conn-doris-in.yaml

# for a subscriber
tdk connection template --type doris-bulk-out --file conn-doris-out.yaml
```

### Step 5: Fill out the connection documents

The `spec` fields are described under [Publisher](#publisher) and [Subscriber](#subscriber) below. A completed publisher connection looks like this:

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
      user: $secret:DORIS__USER
      password: $secret:DORIS__PASSWORD
  database: str:my_db
```

`tdk` resolves each `$secret:NAME` from the environment variable of the same name and stores it as a secret.

### Step 6: Attach the connections to collections

```bash
tdk collection update --name doris_landing --conn-file conn-doris-in.yaml
```

To create a new collection with the connection instead, pass the same `--conn-file` to `tdk collection create`.

### Step 7: Use the connector in a function

```python
from tabsdatak.api import publisher
from tabsdata_doris import DorisSrc


@publisher(
    source=DorisSrc(queries=["SELECT * FROM log_events", "SELECT * FROM checkout_requests"]),
    output_tables=["log_events", "checkout_requests"],
)
def pub_doris(log_events, checkout_requests):
    return log_events, checkout_requests
```

Register it into Tabsdata:

```bash
tdk fn register --coll doris_landing --path pub_doris.py::pub_doris
```

## Publisher

The `DorisSrc` connector can be used by a publisher function to read data from Doris into a Tabsdata table.

### Connection

Doris publishers use `DorisSrcConn` to define the frontend address, credentials and database the publisher's queries run against.

```yaml
kind: connectionDef
apiVersion: '1.0'
type: tabsdata_doris:DorisSrcConn
spec:
  host: str:127.0.0.1
  port: str:9030
  arrow_flight_port: str:8070
  credentials:
    kind: userPasswordCredentials
    apiVersion: '1.0'
    type: tabsdatak.conn.common.types:UserPassword
    spec:
      user: $secret:DORIS__USER
      password: $secret:DORIS__PASSWORD
  database: str:my_db
```

### Connection Config Parameters

#### `host`

The address of the Doris frontend.

#### `port`

The frontend's MySQL protocol port. Defaults to `9030`.

#### `arrow_flight_port`

The frontend's `arrow_flight_sql_port`. Only needed for `read_method="arrow_flight"`.

#### `credentials`

The Doris user and password. The user only needs `SELECT`.

#### `database`

The Doris database the queries run against.

#### `staging`

An S3 bucket that Doris exports query results to. Only needed for `read_method="s3"`. See [S3 staging](#s3-staging).

### Publisher

Configure `DorisSrc` as the `source` of a publisher function to select the queries that Tabsdata runs against Doris.

```python
from tabsdatak.api import publisher
from tabsdata_doris import DorisSrc


@publisher(
    source=DorisSrc(queries=["SELECT * FROM orders"], read_method="mysql"),
    output_tables=["orders"],
)
def publish_orders(orders):
    return orders
```

### Function Config Parameters

#### `queries`

Defines the SQL queries the publisher runs against Doris.

Each element in `queries` represents one source slot and maps positionally to an argument in the publisher function.

#### `initial_values`

Values for the bind parameters, written `:name`, inside `queries`. A value is used on the first run only. On later runs the parameter takes the value the publisher stored with `ctx.set_attr(name, value)`, so a query can read only the rows added since the last run:

```python
from tabsdatak.api import TrxCtx, publisher
from tabsdata_doris import DorisSrc


@publisher(
    source=DorisSrc(
        queries=["SELECT * FROM log_events WHERE event_time > :since ORDER BY event_time"],
        initial_values={"since": "1970-01-01 00:00:00"},
    ),
    output_tables=["new_log_events"],
)
def new_log_events(log_events, ctx: TrxCtx):
    row = log_events.max_for("event_time")
    if row is not None and row["event_time"] is not None:
        ctx.set_attr("since", str(row["event_time"]))
    return log_events
```

Each `:name` is replaced with its value as an escaped SQL literal before the query is sent, so all three read methods run the same SQL. A `:name` inside a string literal, a quoted identifier or a comment is left alone.

#### `read_method`

Controls how the query results get out of Doris.

`"mysql"` (the default) streams the results over the MySQL protocol and works against any Doris. Every value is converted row by row, so it is the slowest method, and column types are mapped to the closest Tabsdata type: `BOOLEAN` becomes 0 or 1, `DECIMAL` gets precision 38, and `LARGEINT`, `JSON`, `ARRAY`, `MAP` and `STRUCT` become text.

`"arrow_flight"` fetches the results from the Doris backends as Arrow batches over [Arrow Flight SQL](https://doris.apache.org/docs/db-connect/arrow-flight-sql-connect). Column types arrive exactly as Doris stores them. Arrow Flight SQL is off by default: set `arrow_flight_sql_port` in both `fe.conf` and `be.conf`, restart Doris, and set `arrow_flight_port` on the connection. The backends have to be reachable from the Tabsdata server at the address the frontend reports for them.

`"s3"` has the Doris backends export the results to the `staging` bucket as parquet with `SELECT ... INTO OUTFILE`, then downloads the files. It suits the largest results, since every backend writes its part of the export in parallel.

## Subscriber

The `DorisDest` connector can be used by a subscriber function to write data from a Tabsdata table into Doris.

### Connection

Doris subscribers use `DorisDestConn` to define the frontend address, credentials and database the subscriber loads into.

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
      user: $secret:DORIS__USER
      password: $secret:DORIS__PASSWORD
  database: str:my_db
```

### Connection Config Parameters

#### `host`

The address of the Doris frontend.

#### `port`

The frontend's MySQL protocol port, used to look up table columns, truncate tables and run S3 loads. Defaults to `9030`.

#### `http_port`

The port that takes stream loads. Defaults to `8030`, the frontend, which redirects each load to a backend. On Docker Desktop the backend's internal address isn't reachable from your machine, so use the backend's own port, `8040`, instead.

#### `credentials`

The Doris user and password.

#### `database`

The Doris database the subscriber loads into.

#### `staging`

An S3 bucket that files are staged in before Doris loads them. Only needed for `load_method="s3"`. See [S3 staging](#s3-staging).

### Subscriber

Configure `DorisDest` as the `destination` of a subscriber function to define which tables Tabsdata loads in Doris.

```python
from tabsdatak.api import subscriber
from tabsdata_doris import DorisDest


@subscriber(
    input_tables=["orders"],
    destination=DorisDest(tables=["orders"], if_table_exists="append", load_method="stream_load"),
)
def write_orders(orders):
    return orders
```

### Function Config Parameters

#### `tables`

Defines the destination tables the subscriber loads into in Doris.

Each element in `tables` represents one destination slot and maps positionally to a value returned by the subscriber function. The tables have to exist already, since the key model, partitioning and indexes can't be inferred from the data. Columns are matched to the table's columns by name, and a column the table doesn't have fails the load.

Each table loads on its own as one Doris transaction, and strict mode fails the whole load on any row that doesn't fit the table. A failure after some tables loaded leaves those tables loaded.

#### `if_table_exists`

Controls what happens to the rows already in the table. `"append"` (the default) adds the new rows. A retried load of the same data is skipped instead of writing duplicate rows. `"replace"` truncates the table, then loads, so a failed load leaves the table empty until the next run.

#### `load_method`

Controls how the data gets into Doris.

`"stream_load"` (the default) sends each file straight to Doris [stream load](https://doris.apache.org/docs/data-operate/import/import-way/stream-load-manual) over HTTP. It suits small, frequent batches.

`"s3"` uploads each file to the `staging` bucket, then has Doris read it back with its `S3()` table function. It suits large loads, since every Doris backend reads the staged file in parallel.

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
          access_key_id: $secret:DORIS__S3_ACCESS_KEY
          secret_access_key: $secret:DORIS__S3_SECRET_KEY
      base_path: str:/doris-staging
```

Files are written under `base_path`, with a new folder for every function run, and are left in the bucket afterwards. Add a lifecycle rule to the bucket that expires them. The access key needs `s3:PutObject`, `s3:GetObject` and `s3:ListBucket` on the bucket.

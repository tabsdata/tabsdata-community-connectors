# Apache Doris connector

Reads [Apache Doris](https://doris.apache.org) query results into a Tabsdata
publisher and loads subscriber tables into existing Doris tables. It follows the
structure of the built-in StarRocks connector: connections, a source, a
destination, plugins and error codes.

| Role | Connection | Config | Registered as |
| --- | --- | --- | --- |
| Source | `DorisSrcConn` | `DorisSrc` | `doris-bulk-in` |
| Destination | `DorisDestConn` | `DorisDest` | `doris-bulk-out` |

Requires Tabsdata 2.1 or later and Apache Doris 2.1 or later, with its MySQL port
(9030) reachable from the Tabsdata server. The destination also needs a stream
load HTTP port.

## Install

Install the connector where you run `tdk`, so it can validate the connection and
register functions:

```bash
pip install "tabsdata-conn-doris @ git+https://github.com/tabsdata/tabsdata-community-connectors.git#subdirectory=doris"
```

Then install it in the server's function environment, where the functions run.
`venv update` stops the server, so start it again afterwards:

```bash
echo "tabsdata-conn-doris @ git+https://github.com/tabsdata/tabsdata-community-connectors.git#subdirectory=doris" > requirements-fn.txt
tdkserver venv update --instance tabsdata --name fn --requirements requirements-fn.txt --yes
tdkserver start --instance tabsdata --yes
```

## Source connection

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

| Field | Default | What it's for |
| --- | --- | --- |
| `host` | | the Doris frontend |
| `port` | `9030` | the MySQL protocol port, used by the `mysql` and `s3` read methods |
| `arrow_flight_port` | none | the frontend's `arrow_flight_sql_port`, needed only for `read_method="arrow_flight"` |
| `credentials` | | user and password; the user only needs `SELECT` |
| `database` | | the database the queries run against |
| `staging` | none | an `S3Location`, needed only for `read_method="s3"` (see [S3 staging](#s3-staging)) |

Source and destination connections are separate types, the same as the built-in
connectors, so a read-only user can't be registered for a destination.

## Source

`DorisSrc` runs each query in `queries` and publishes each result as one table,
in the order of `output_tables`:

```python
from tabsdatak.api import publisher
from tabsdata_doris import DorisSrc


@publisher(
    source=DorisSrc(queries=["SELECT * FROM log_events", "SELECT * FROM checkout_requests"]),
    output_tables=["log_events", "checkout_requests"],
)
def from_doris(log_events, checkout_requests):
    return log_events, checkout_requests
```

Each publisher picks one of three read methods with `read_method`:

| `read_method` | How the data gets out | Needs |
| --- | --- | --- |
| `mysql` (default) | the result is streamed over the MySQL protocol in batches of 65,536 rows | nothing extra |
| `arrow_flight` | the frontend plans the query and the result is fetched from the backends as Arrow batches over [Arrow Flight SQL](https://doris.apache.org/docs/db-connect/arrow-flight-sql-connect) | `arrow_flight_port` on the connection |
| `s3` | `SELECT ... INTO OUTFILE` has the backends export the result as parquet to S3, then the files are downloaded | a `staging` bucket on the connection that the Doris backends can write to |

`mysql` works against any Doris and suits small and medium results. Every value
is converted row by row, so it is the slowest method.

`arrow_flight` suits large results. Nothing is converted row by row, and the
column types arrive exactly as Doris stores them. Arrow Flight SQL is off by
default: set `arrow_flight_sql_port` in both `fe.conf` and `be.conf`, then
restart Doris. The client fetches from each backend at the address the frontend
reports for it, so the backends have to be reachable from the Tabsdata server.
That's the same problem as stream loads on Docker Desktop.

`s3` suits the largest results, since every backend writes its part of the
export in parallel. A result can come back as several parquet files, which all
go to the same table.

Over `mysql`, column types are mapped from the types Doris reports:

| Doris type | Tabsdata type |
| --- | --- |
| `TINYINT` to `BIGINT`, `BOOLEAN` | integer (`BOOLEAN` becomes 0 or 1) |
| `FLOAT`, `DOUBLE` | float |
| `DECIMAL` | decimal with precision 38 and the column's scale |
| `DATE` | date |
| `DATETIME` | datetime, microseconds |
| anything else, including `LARGEINT`, `JSON`, `ARRAY`, `MAP` and `STRUCT` | text |

Use `arrow_flight` when a publisher needs the exact Doris types.

### Incremental reads

A query can reference bind parameters as `:name`, the same syntax as the
built-in SQL sources. `initial_values` gives each parameter its value on the
first run. On later runs the parameter takes the value the publisher stored with
`ctx.set_attr(name, value)`, so a query can read only the rows added since the
last run:

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

Arrow Flight SQL and `INTO OUTFILE` don't take query parameters. So that all
three read methods run the same SQL, each `:name` is replaced with its value as
an escaped SQL literal before the query is sent. A `:name` inside a string
literal, a quoted identifier or a comment is left alone, and so is a `:name`
missing from `initial_values`, which Doris then rejects as a syntax error.

## Destination connection

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

`tdk` resolves each `$secret:NAME` from the environment variable of the same
name and stores it as a secret.

| Field | Default | What it's for |
| --- | --- | --- |
| `host` | | the Doris frontend |
| `port` | `9030` | the MySQL protocol port, used to look up table columns, truncate tables and run S3 loads |
| `http_port` | `8030` | the port that takes stream loads |
| `credentials` | | user and password |
| `database` | | the target database |
| `staging` | none | an `S3Location`, needed only for `load_method="s3"` (see [S3 staging](#s3-staging)) |

The frontend (8030) redirects each stream load to a backend. On Docker Desktop
the backend's internal address isn't reachable from your machine, so point
`http_port` at the backend's own port, 8040. On a real cluster use the
frontend's 8030.

## Destination

```python
from tabsdatak.api import subscriber
from tabsdata_doris import DorisDest


@subscriber(
    input_tables=["logs_silver/new_log_events@NEW"],
    destination=DorisDest(tables=["log_events"], if_table_exists="append", load_method="stream_load"),
)
def to_doris(new_log_events):
    return new_log_events
```

Each subscriber picks one of two load methods with `load_method`:

| `load_method` | How the data gets in | Needs |
| --- | --- | --- |
| `stream_load` (default) | each parquet file is sent straight to Doris [stream load](https://doris.apache.org/docs/data-operate/import/import-way/stream-load-manual) over HTTP | the Doris HTTP port |
| `s3` | the file is uploaded to S3 with the Tabsdata transporter, then `INSERT INTO ... SELECT ... FROM S3(...)` reads it back | a `staging` bucket on the connection that the Doris backends can reach |

Stream load suits small, frequent batches. S3 staging suits large loads, since
every Doris backend reads the staged file in parallel.

Rules that apply to both methods:

- The target tables must exist. The key model, partitioning and inverted indexes
  can't be inferred from the data.
- Parquet columns are matched to table columns by name. A column the table
  doesn't have fails the load, instead of being dropped.
- A load commits as one Doris transaction, and strict mode fails the whole load
  on any row that doesn't fit the table.
- Each table loads on its own. A failure after some tables loaded leaves those
  tables loaded.
- `append` (default) labels each load with a hash of the file. If the same file
  is retried, Doris sees the same label and skips the load instead of writing
  duplicate rows.
- `replace` truncates the table, then loads. A failed load leaves the table
  empty until the next run.
- A table with no rows is skipped, since Doris rejects an empty load.

## S3 staging

`load_method="s3"` and `read_method="s3"` both need a `staging` location, on
the `DorisDestConn` or the `DorisSrcConn`. The block is the same on both:

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

For `read_method="s3"`:

- Doris exports each query's result under
  `<base_path>/<upload_id>/query_<index>/`, with one `upload_id` per publisher
  run. Every parquet file under that prefix is then downloaded with the same
  key pair, which needs `s3:ListBucket` and `s3:GetObject` as well as the
  `s3:PutObject` the backends need to export.
- An empty result can export no file. In that case the query's columns are read
  over the MySQL protocol, so the table is published empty instead of missing.
- Exported files are left in the bucket, so add a lifecycle rule that expires them.
- The S3 keys are part of the `INTO OUTFILE` statement sent to Doris.

For `load_method="s3"`, the method replicates the StarRocks connector's staging:

- Files are uploaded under `<base_path>/<upload_id>/<table>/<file>`, with one
  `upload_id` per subscriber run.
- The upload goes through the same transporter.
- Staged files are left in the bucket, so add a lifecycle rule that expires them.
- The S3 keys are part of the `INSERT` statement sent to Doris, the same way the
  StarRocks connector passes them to `FILES()`.

It differs from StarRocks in three ways:

- It reads with Doris's `S3()` table function instead of StarRocks's `FILES()`.
- The insert carries a load label.
- Only S3 is supported, not Azure or GCS.

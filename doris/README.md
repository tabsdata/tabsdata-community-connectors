# Apache Doris connector

Loads Tabsdata subscriber tables into existing [Apache Doris](https://doris.apache.org)
tables. It follows the structure of the built-in StarRocks connector: a
connection, a destination, a plugin and error codes.

| Role | Connection | Config | Registered as |
| --- | --- | --- | --- |
| Destination | `DorisDestConn` | `DorisDest` | `doris-bulk-out` |

Requires Tabsdata 2.1 or later and Apache Doris 2.1 or later, with its MySQL port (9030)
and a stream load HTTP port reachable from the Tabsdata server.

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

## Connection

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
| `staging` | none | an `S3Location`, needed only for `load_method="s3"` |

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

### S3 staging

Add a `staging` location to the connection:

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

The S3 method replicates the StarRocks connector's staging:

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

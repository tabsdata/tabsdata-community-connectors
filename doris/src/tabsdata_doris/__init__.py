#
# Copyright 2026 Tabsdata Inc.
#

"""apache doris source and destination connector for tabsdata, built on the same
structure as the starrocks connector

the source runs sql queries against doris for a publisher, one output table per
query, with three read methods. mysql streams the result over the mysql
protocol, arrow_flight fetches it as arrow batches over arrow flight sql and s3
has doris export it to a staging bucket with SELECT ... INTO OUTFILE that the
plugin downloads:

    @publisher(
        source=DorisSrc(
            queries=["SELECT * FROM log_events WHERE event_time > :since"],
            initial_values={"since": "2026-09-30 00:00:00"},
            read_method="arrow_flight",
        ),
        output_tables=["log_events"],
    )
    def from_doris(log_events):
        return log_events

the destination has two load methods, stream load sends each parquet file
straight to doris over http and s3 stages it in a bucket that doris reads back
with the S3() table function. the address, ports, credentials, database and
optional s3 staging location are stored in the connection on the subscriber's
collection and the subscriber specifies the target tables and the load method:

    @subscriber(
        input_tables=["logs_silver/new_log_events@NEW"],
        destination=DorisDest(tables=["log_events"], if_table_exists="append", load_method="stream_load"),
    )
    def to_doris(new_log_events):
        return new_log_events
"""

from abc import ABC
from typing import Annotated, Literal, TypeAlias

import tabsdatak._api as _api
from tabsdatak.api import BasicDictSpec, Dest, Src, StrOrSecretSpec
from tabsdatak.conn.common.types import (
    AwsAccessSecretKey,
    AwsRegionSpec,
    CfgSpec,
    CloudRelativePathSpec,
    HostSpec,
    PortSpec,
    S3BucketSpec,
    UserPassword,
    _convert,
)
from tabsdatak.spi import Conn
from tabsdata_doris.error import DorisErrorCode

# doris identifiers for the database and table names so they can be used in the
# sql and the stream load url without quoting
IDENTIFIER_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]*$"
IDENTIFIER_VALIDATION = _api.ValueConstraints(_api.Rule.matches(IDENTIFIER_PATTERN, DorisErrorCode.DORIS_8))

DatabaseSpec: TypeAlias = Annotated[StrOrSecretSpec, IDENTIFIER_VALIDATION]
TableNameSpec: TypeAlias = Annotated[str, IDENTIFIER_VALIDATION]

# a source needs at least one query since each query is one output table
QueriesSpec: TypeAlias = Annotated[list[str], _api.NON_EMPTY_LIST_FIELD]


class CloudLocation(_api.DescriptorBase, ABC):
    """abstract base for the staging location of the s3 load and read methods,
    same shape as the starrocks connector's CloudLocation so more stores can be
    added as subclasses
    """

    __kind__ = "dorisCloudLocation"


@_api.dataclass(DorisErrorCode.DORIS_9, kw_only=True)
class S3Location(CloudLocation):
    """s3 staging area for the s3 load and read methods. to load, the plugin
    uploads each parquet file under base_path with the tabsdata transporter and
    doris reads it back with the S3() table function. to read, doris exports
    each query result under base_path with SELECT ... INTO OUTFILE and the
    plugin downloads it. either way the bucket has to be reachable from the
    doris backends
    """

    __kind__ = "dorisS3Location"
    bucket: S3BucketSpec
    region: AwsRegionSpec
    credentials: AwsAccessSecretKey
    base_path: CloudRelativePathSpec = "/"
    cfg: CfgSpec | None = None

    def _bucket(self) -> str:
        return _convert(self.bucket, str)

    def _region(self) -> str:
        return _convert(self.region, str)

    def _base_path(self) -> str:
        return _convert(self.base_path, str)


@_api.dataclass(DorisErrorCode.DORIS_14, kw_only=True)
class DorisSrcConn(Conn):
    """connection for DorisSrc that stores the frontend host, the mysql protocol
    port, the frontend's arrow flight sql port for read_method="arrow_flight",
    the credentials, the database the queries run against and an optional s3
    staging location for read_method="s3". a separate class from DorisDestConn
    like the built in connectors, so a read only user can't end up on a
    destination
    """

    host: HostSpec
    port: PortSpec = "9030"
    arrow_flight_port: PortSpec | None = None
    credentials: UserPassword
    database: DatabaseSpec
    staging: CloudLocation | None = None

    def _host(self) -> str:
        return _convert(self.host, str)

    def _port(self) -> int:
        return _convert(self.port, int)

    def _arrow_flight_port(self) -> int | None:
        return None if self.arrow_flight_port is None else _convert(self.arrow_flight_port, int)

    def _database(self) -> str:
        return _convert(self.database, str)

    def _user(self) -> str:
        return _convert(self.credentials.user, str)

    def _password(self) -> str:
        return _convert(self.credentials.password, str)


@_api.dataclass(DorisErrorCode.DORIS_15, kw_only=True)
class DorisSrc(Src):
    """source that runs each query against doris and publishes the result as one
    table, tables map to the publisher's output tables in order. initial_values
    seeds the :name bind parameters in the queries on the first run, later runs
    bind the value the publisher stored with ctx.set_attr(name, value) so a
    query can pick up where the last run stopped. read_method defaults to mysql,
    arrow_flight needs arrow_flight_port on the connection and the
    adbc-driver-flightsql package, s3 needs a staging location on the
    connection and suits large results since every backend writes its part of
    the export in parallel
    """

    queries: QueriesSpec
    initial_values: BasicDictSpec | None = None
    read_method: Literal["mysql", "arrow_flight", "s3"] = "mysql"


@_api.dataclass(DorisErrorCode.DORIS_1, kw_only=True)
class DorisDestConn(Conn):
    """connection for DorisDest that stores the frontend host, the mysql protocol
    port used to look up table columns, truncate tables and run s3 loads, the
    http port that takes stream loads, the credentials, the target database and
    an optional s3 staging location, on docker desktop point http_port at the
    backend's 8040 since the frontend redirects stream loads to a backend
    address the host can't reach
    """

    host: HostSpec
    port: PortSpec = "9030"
    http_port: PortSpec = "8030"
    credentials: UserPassword
    database: DatabaseSpec
    staging: CloudLocation | None = None

    def _host(self) -> str:
        return _convert(self.host, str)

    def _port(self) -> int:
        return _convert(self.port, int)

    def _http_port(self) -> int:
        return _convert(self.http_port, int)

    def _database(self) -> str:
        return _convert(self.database, str)

    def _user(self) -> str:
        return _convert(self.credentials.user, str)

    def _password(self) -> str:
        return _convert(self.credentials.password, str)


@_api.dataclass(DorisErrorCode.DORIS_2, kw_only=True)
class DorisDest(Dest):
    """destination that loads subscriber tables into existing doris tables,
    tables map to the subscriber's input tables in order and have to be created
    first since the key model, partitioning and inverted indexes can't be
    inferred from the data. if_table_exists defaults to append, replace
    truncates each table before loading it. load_method defaults to stream_load,
    s3 needs a staging location on the connection and suits large loads since
    every backend reads the staged file in parallel
    """

    tables: list[TableNameSpec]
    if_table_exists: Literal["append", "replace"] = "append"
    load_method: Literal["stream_load", "s3"] = "stream_load"

#
# Copyright 2026 Tabsdata Inc.
#

from tabsdatak.conn.common.error import ConnException, ConnInitException
from tabsdatak.error import ErrorCode, ErrorDef


class DorisRuntimeException(ConnException):
    """raised when a write to doris fails"""


class DorisErrorCode(ErrorCode):
    DORIS_1 = ErrorDef(ConnInitException, "DorisDestConn validation failed")
    DORIS_2 = ErrorDef(ConnInitException, "DorisDest validation failed")
    DORIS_3 = ErrorDef(
        DorisRuntimeException,
        "Doris received {slots} table(s) for {tables} target table(s)",
    )
    DORIS_4 = ErrorDef(
        DorisRuntimeException,
        "Doris connection failed (host={host}, port={port})",
    )
    DORIS_5 = ErrorDef(
        DorisRuntimeException,
        "Doris stream load into {table} failed with status {status}: {message} {error_url}",
    )
    DORIS_6 = ErrorDef(
        DorisRuntimeException,
        "Doris table {database}.{table} does not exist, create it first",
    )
    DORIS_7 = ErrorDef(
        DorisRuntimeException,
        "Doris table {database}.{table} has no column(s) {columns} that the subscriber returned",
    )
    DORIS_8 = ErrorDef(
        ConnInitException,
        "not a valid Doris identifier (letters, digits and underscores, not starting with a digit): {value!r}",
    )
    DORIS_9 = ErrorDef(ConnInitException, "Cannot create S3Location")
    DORIS_10 = ErrorDef(
        DorisRuntimeException,
        "DorisDest load_method 's3' needs a staging location on the collection's DorisDestConn",
    )
    DORIS_11 = ErrorDef(DorisRuntimeException, "upload of the staged parquet to {uri} failed")
    DORIS_12 = ErrorDef(DorisRuntimeException, "Doris S3 load into {table} failed: {message}")
    DORIS_13 = ErrorDef(DorisRuntimeException, "Doris staging location type is not supported: {kind}")

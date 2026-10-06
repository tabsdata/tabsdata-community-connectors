#
# Copyright 2026 Tabsdata Inc.
#

"""motherduck source and destination connector for tabsdata

the source runs sql queries against motherduck for a publisher, one output
table per query. the token and database are stored in the connection on the
publisher's collection:

    @publisher(
        source=MotherDuckSrc(
            queries=["SELECT * FROM cases WHERE case_id > $last_id ORDER BY case_id"],
            initial_values={"last_id": 0},
        ),
        output_tables=["cases"],
    )
    def from_motherduck(cases: TableFrameSpec, ctx: TrxCtx):
        row = cases.max_for("case_id")
        if row is not None and row["case_id"] is not None:
            ctx.set_attr("last_id", int(row["case_id"]))
        return cases

the destination writes subscriber tables to motherduck, the token and database
are stored in the connection on the subscriber's collection and the subscriber
specifies the target tables:

    @subscriber(
        input_tables=["gold/dim_patient", "gold/fct_anesthesia_case"],
        destination=MotherDuckDest(tables=["dim_patient", "fct_anesthesia_case"]),
    )
    def to_motherduck(dim_patient, fct_anesthesia_case):
        return dim_patient, fct_anesthesia_case
"""

from typing import Annotated, Literal

from pydantic import StringConstraints

import tabsdatak._api as _api
from tabsdatak.api import BasicDictSpec, Dest, Secret, Src, StrOrSecretSpec
from tabsdatak.spi import Conn
from tabsdata_motherduck.error import MotherDuckErrorCode

# restricts table names to plain identifiers so they can be used in the sql
# without quoting
TableName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")]

# a source needs at least one query since each query is one output table
Queries = Annotated[list[str], _api.NON_EMPTY_LIST_FIELD]


# helper function that returns the value of a plain string or a secret
def resolve(spec: StrOrSecretSpec) -> str:
    return spec.value() if isinstance(spec, Secret) else spec


@_api.dataclass(MotherDuckErrorCode.MOTHERDUCK_5, kw_only=True)
class MotherDuckSrcConn(Conn):
    """connection for MotherDuckSrc that stores the motherduck token and the
    database the queries run against, the token only needs read access and the
    database has to exist. a separate class from MotherDuckDestConn like the
    built in connectors, so a read only token can't end up on a destination
    """

    token: StrOrSecretSpec
    database: StrOrSecretSpec


@_api.dataclass(MotherDuckErrorCode.MOTHERDUCK_6, kw_only=True)
class MotherDuckSrc(Src):
    """source that runs each query against motherduck and publishes the result
    as one table, tables map to the publisher's output tables in order.
    initial_values seeds the $name bind parameters in the queries on the first
    run, later runs bind the value the publisher stored with
    ctx.set_attr(name, value) so a query can pick up where the last run stopped
    """

    queries: Queries
    initial_values: BasicDictSpec | None = None


@_api.dataclass(MotherDuckErrorCode.MOTHERDUCK_1, kw_only=True)
class MotherDuckDestConn(Conn):
    """connection for MotherDuckDest that stores the motherduck token and target
    database, the token needs write access and the database is created if it
    doesn't exist
    """

    token: StrOrSecretSpec
    database: StrOrSecretSpec


@_api.dataclass(MotherDuckErrorCode.MOTHERDUCK_2, kw_only=True)
class MotherDuckDest(Dest):
    """destination that writes subscriber tables to motherduck, tables map to the
    subscriber's input tables in order. if_table_exists defaults to replace which
    recreates each table, append inserts the rows and creates the table if needed
    """

    tables: list[TableName]
    if_table_exists: Literal["append", "replace"] = "replace"

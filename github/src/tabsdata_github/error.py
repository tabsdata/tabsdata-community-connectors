#
# Copyright 2026 Tabsdata Inc.
#

from tabsdatak.error import ApiInitException, ApiRuntimeException, ErrorCode, ErrorDef


class GitHubRuntimeException(ApiRuntimeException):
    """raised when a read from the github api fails"""


class GitHubErrorCode(ErrorCode):
    GITHUB_1 = ErrorDef(ApiInitException, "GitHubSrcConn validation failed")
    GITHUB_2 = ErrorDef(ApiInitException, "GitHubSrc validation failed")
    GITHUB_3 = ErrorDef(
        GitHubRuntimeException,
        "GitHub rate limit reached until {reset}, set a token on the connection to raise the limit",
    )
    GITHUB_4 = ErrorDef(GitHubRuntimeException, "GitHub request for {repository} failed with status {status}")
    GITHUB_5 = ErrorDef(ApiInitException, "GitHub table validation failed")

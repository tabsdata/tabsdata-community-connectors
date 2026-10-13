#
# Copyright 2026 Tabsdata Inc.
#

from tabsdatak.error import ApiInitException, ApiRuntimeException, ErrorCode, ErrorDef


class SlackRuntimeException(ApiRuntimeException):
    """raised when a read from or a post to slack fails"""


class SlackErrorCode(ErrorCode):
    SLACK_1 = ErrorDef(ApiInitException, "SlackSrcConn validation failed")
    SLACK_2 = ErrorDef(ApiInitException, "SlackSrc validation failed")
    SLACK_3 = ErrorDef(ApiInitException, "SlackDestConn validation failed")
    SLACK_4 = ErrorDef(ApiInitException, "SlackDest validation failed")
    SLACK_5 = ErrorDef(ApiInitException, "Message validation failed")
    SLACK_6 = ErrorDef(SlackRuntimeException, "Slack {method} failed: {error}")
    SLACK_7 = ErrorDef(SlackRuntimeException, "Slack channel {channel} not found")
    SLACK_8 = ErrorDef(SlackRuntimeException, "Slack received {slots} table(s) for {messages} message(s)")
    SLACK_9 = ErrorDef(SlackRuntimeException, "Columns {columns} aren't in the table posted to {channel}")

# Slack connector

The Slack connector lets Tabsdata read channel messages from [Slack](https://slack.com) with publisher functions and post tables to channels with subscriber functions.

## Installing the connector

The connector package must be installed in two places:

1. Wherever you run `tdk`, so Tabsdata can validate connections and register functions.
2. In the Tabsdata server's function environment, so the connector is available when functions run.

### Step 1: Install the package where `tdk` runs

```bash
pip install "tabsdata-conn-slack @ git+https://github.com/tabsdata/tabsdata-community-connectors.git#subdirectory=slack"
```

### Step 2: Add the package to the server's function environment

Add the connector to a `requirements.txt` file:

```text
tabsdata-conn-slack @ git+https://github.com/tabsdata/tabsdata-community-connectors.git#subdirectory=slack
```

Then update the function and agent environments and restart the server:

```bash
tdkserver venv update --instance <instance> --name fn --requirements requirements.txt
tdkserver venv update --instance <instance> --name ag --requirements requirements.txt
tdkserver start --instance <instance>
```

> **Warning:** `tdkserver venv update` overwrites the existing package requirements for the function environment. Include every package the environment still needs in `requirements.txt`, not just this connector.

## Setting up a Slack app

The connector uses a Slack app's bot token.

1. Create an app at [api.slack.com/apps](https://api.slack.com/apps), from scratch, in your workspace.
2. Under **OAuth & Permissions**, add these bot token scopes:

   | Scope | Needed for |
   | --- | --- |
   | `channels:history` | Publishers reading public channels |
   | `groups:history` | Publishers reading private channels |
   | `chat:write` | Subscribers posting messages |
   | `channels:read` | Using channel names instead of IDs |
   | `groups:read` | Using private channel names instead of IDs |

3. Install the app to your workspace and copy the **Bot User OAuth Token**, which starts with `xoxb-`.
4. Invite the app to each channel it reads from or posts to, for example with `/invite @your-app`.

## Publisher

Use `SlackSrc` in a publisher function to read channel messages and publish them as Tabsdata tables.

### Step 1: Generate the connection document

```bash
tdk connection template --type slack-in --file conn-slack-in.yaml
```

### Step 2: Configure the connection

Set the bot token:

```yaml
kind: connectionDef
apiVersion: '1.0'
type: tabsdata_slack:SlackSrcConn
spec:
  token: secret:SLACK__BOT_TOKEN
```

The `secret:` prefix tells Tabsdata to store the token as a secret and remove it from the stored connection document.

### Step 3: Attach the connection to a collection

```bash
tdk collection update --name slack_landing --conn-file conn-slack-in.yaml
```

To create a new collection with the connection instead, pass the same `--conn-file` to `tdk collection create`.

### Step 4: Create a publisher

Configure `SlackSrc` as the publisher's `source`, list the `channels` to read, and set `since` to the date the first run starts from.

```python
from tabsdatak.api import TableFrameSpec, publisher
from tabsdata_slack import SlackSrc


@publisher(
    source=SlackSrc(
        channels=["support", "C0123ABCDE"],
        since="2026-10-01T00:00:00Z",
    ),
    output_tables=["support_messages", "team_messages"],
)
def pub_slack(
    support_messages: TableFrameSpec, team_messages: TableFrameSpec
) -> tuple[TableFrameSpec, TableFrameSpec]:
    return support_messages, team_messages
```

Register the function:

```bash
tdk fn register --coll slack_landing --path pub_slack.py::pub_slack
```

### Publisher parameters

#### `channels`

The channels to read, by name or by ID. A name can start with `#`.

Each channel represents one source slot and maps positionally to an argument in the publisher function.

#### `since`

The date the first run starts from, as an ISO-8601 timestamp.

After that, each channel picks up after the newest message of its last run. If nothing was posted, the table gets no new data.

Only messages posted in the channel itself are read, not thread replies. Edits and deletes made after a message was read aren't picked up. On Slack's free plan, only the last 90 days of messages can be read.

### Columns

| Column | Type | Description |
| --- | --- | --- |
| `ts` | String | Message ID, Slack's timestamp of the message |
| `channel` | String | Channel ID |
| `user_id` | String | ID of the user who posted the message |
| `bot_id` | String | ID of the bot that posted the message, if a bot posted it |
| `subtype` | String | Message subtype, such as `bot_message` or `channel_join`. Null for regular messages |
| `text` | String | Message text |
| `thread_ts` | String | `ts` of the thread the message starts, if it has replies |
| `reply_count` | Int64 | Number of thread replies |
| `posted_at` | Datetime (UTC) | When the message was posted |
| `edited_at` | Datetime (UTC) | When the message was last edited |
| `raw` | String | Full API response for the message as JSON |

## Subscriber

Use `SlackDest` in a subscriber function to post Tabsdata tables to channels. Each table is posted as one message with its row count and its first rows.

### Step 1: Generate the connection document

```bash
tdk connection template --type slack-out --file conn-slack-out.yaml
```

### Step 2: Configure the connection

Set the bot token:

```yaml
kind: connectionDef
apiVersion: '1.0'
type: tabsdata_slack:SlackDestConn
spec:
  token: secret:SLACK__BOT_TOKEN
```

### Step 3: Attach the connection to a collection

```bash
tdk collection update --name slack_export --conn-file conn-slack-out.yaml
```

To create a new collection with the connection instead, pass the same `--conn-file` to `tdk collection create`.

### Step 4: Create a subscriber

Configure `SlackDest` as the subscriber's `destination`.

Each message in `messages` maps positionally to a value returned by the subscriber function.

```python
from tabsdatak.api import TableFrameSpec, subscriber
from tabsdata_slack import Message, SlackDest


@subscriber(
    input_tables=["gold/failed_checks"],
    destination=SlackDest(
        messages=[Message("alerts", title="Failed checks", max_rows=5)],
    ),
)
def sub_slack(failed_checks: TableFrameSpec) -> TableFrameSpec:
    return failed_checks
```

Register the function:

```bash
tdk fn register --coll slack_export --path sub_slack.py::sub_slack
```

A message looks like this:

```text
*Failed checks*
12 rows
check     column  failed
--------  ------  ------
not_null  email   9
unique    id      3
_first 5 rows shown_
```

### Subscriber parameters

#### `messages`

The messages to post. Each `Message` takes the channel, by name or by ID, and these options:

| Option | Default | Description |
| --- | --- | --- |
| `title` | None | Bold line at the top of the message |
| `max_rows` | `10` | How many rows to show, up to 100. `0` posts only the row count |
| `columns` | all | Which columns to show |
| `skip_empty` | `True` | Don't post anything when the table has no rows |

Each message represents one destination slot and maps positionally to a value returned by the subscriber function.

Values longer than 40 characters are cut short.

## Running the tests

```bash
pip install -e "slack[test]"
pytest slack/tests
```

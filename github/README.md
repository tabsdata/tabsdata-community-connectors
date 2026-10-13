# GitHub connector

The GitHub connector lets Tabsdata read issues, pull requests, issue comments, commits, and releases from [GitHub](https://github.com) repositories with publisher functions.

## Installing the connector

The connector package must be installed in two places:

1. Wherever you run `tdk`, so Tabsdata can validate connections and register functions.
2. In the Tabsdata server's function environment, so the connector is available when functions run.

### Step 1: Install the package where `tdk` runs

```bash
pip install "tabsdata-conn-github @ git+https://github.com/tabsdata/tabsdata-community-connectors.git#subdirectory=github"
```

### Step 2: Add the package to the server's function environment

Add the connector to a `requirements.txt` file:

```text
tabsdata-conn-github @ git+https://github.com/tabsdata/tabsdata-community-connectors.git#subdirectory=github
```

Then update the function and agent environments and restart the server:

```bash
tdkserver venv update --instance <instance> --name fn --requirements requirements.txt
tdkserver venv update --instance <instance> --name ag --requirements requirements.txt
tdkserver start --instance <instance>
```

> **Warning:** `tdkserver venv update` overwrites the existing package requirements for the function environment. Include every package the environment still needs in `requirements.txt`, not just this connector.

## Publisher

Use `GitHubSrc` in a publisher function to read data from GitHub and publish it as Tabsdata tables.

### Step 1: Generate the connection document

```bash
tdk connection template --type github-in --file conn-github-in.yaml
```

### Step 2: Configure the connection

Set the GitHub token:

```yaml
kind: connectionDef
apiVersion: '1.0'
type: tabsdata_github:GitHubSrcConn
spec:
  token: secret:GITHUB__TOKEN
```

The `secret:` prefix tells Tabsdata to store the token as a secret and remove it from the stored connection document.

The token is optional. To connect without one, remove the `token` line.

### Step 3: Attach the connection to a collection

```bash
tdk collection update --name github_landing --conn-file conn-github-in.yaml
```

To create a new collection with the connection instead, pass the same `--conn-file` to `tdk collection create`.

### Step 4: Create a publisher

Configure `GitHubSrc` as the publisher's `source`, list the `tables` to read, and set `since` to the date the first run starts from.

```python
from tabsdatak.api import TableFrameSpec, publisher
from tabsdata_github import Commits, GitHubSrc, IssueComments, Issues, PullRequests, Releases


@publisher(
    source=GitHubSrc(
        since="2026-10-01T00:00:00Z",
        tables=[
            Issues("pola-rs/polars"),
            PullRequests("pola-rs/polars"),
            IssueComments("pola-rs/polars"),
            Commits("pola-rs/polars", branch="main"),
            Releases("pola-rs/polars"),
        ],
    ),
    output_tables=["issues", "pull_requests", "issue_comments", "commits", "releases"],
)
def pub_github(
    issues: TableFrameSpec,
    pull_requests: TableFrameSpec,
    issue_comments: TableFrameSpec,
    commits: TableFrameSpec,
    releases: TableFrameSpec,
) -> tuple[TableFrameSpec, TableFrameSpec, TableFrameSpec, TableFrameSpec, TableFrameSpec]:
    return issues, pull_requests, issue_comments, commits, releases
```

Register the function:

```bash
tdk fn register --coll github_landing --path pub_github.py::pub_github
```

### Connection parameters

#### `token`

A GitHub personal access token.

Without a token, GitHub allows 60 requests per hour. With a token, it allows 5,000.

For public repositories, a fine-grained token with no extra permissions works. For private repositories, the token needs read access to the data you're pulling.

#### `api_url`

The GitHub API URL. Defaults to `https://api.github.com`.

For GitHub Enterprise Server, use `https://<host>/api/v3`.

#### `timeout`

How many seconds to wait for a response from GitHub. Defaults to `30.0`.

### Publisher parameters

#### `tables`

Defines what the publisher reads from GitHub. Each entry is one type of data from one repository, written as `owner/name`.

Each table represents one source slot and maps positionally to an argument in the publisher function.

You can read from several repositories in the same publisher. Each table can only be listed once per repository. For `Commits`, that's once per branch.

#### `since`

The date the first run starts from, as an ISO-8601 timestamp.

After that, each table picks up from where its last run ended. If nothing changed, the table gets no new data.

The last item from one run is read again at the start of the next run, so expect one repeated row per table per run.

#### `max_pages`

How many pages of 100 items each table reads per run. Defaults to `20`.

If there is more data than that, the rest is read on the next run.

## Tables

### `Issues`

```python
Issues("pola-rs/polars")
```

Reads issues that were opened or updated since the last run. Pull requests are not included.

Use `label_columns` to turn labels into columns. Each column gets the first matching label the issue has, or null if it has none:

```python
Issues(
    "pola-rs/polars",
    label_columns={
        "kind": ["bug", "enhancement", "documentation"],
        "priority": ["P-low", "P-medium", "P-high"],
    },
)
```

Label column names can't be the same as an existing column name.

| Column | Type | Description |
| --- | --- | --- |
| `number` | Int64 | Issue number |
| `title` | String | Issue title |
| `body` | String | Issue body in Markdown |
| `state` | String | `open` or `closed` |
| `state_reason` | String | Why the issue was closed or reopened |
| `author` | String | GitHub username of whoever opened the issue |
| `author_association` | String | Author's role in the repository, such as `MEMBER` or `CONTRIBUTOR` |
| `labels` | List(String) | Label names |
| `comments` | Int64 | Number of comments |
| `created_at` | Datetime (UTC) | When the issue was opened |
| `updated_at` | Datetime (UTC) | When the issue was last updated |
| `closed_at` | Datetime (UTC) | When the issue was closed |
| `url` | String | Link to the issue |
| `raw` | String | Full API response for the issue as JSON |

### `PullRequests`

```python
PullRequests("pola-rs/polars")
```

Reads pull requests that were opened or updated since the last run. Supports `label_columns` the same way as `Issues`.

Has the same columns as `Issues` except `state_reason`, plus:

| Column | Type | Description |
| --- | --- | --- |
| `draft` | Boolean | Whether the pull request is a draft |
| `merged_at` | Datetime (UTC) | When the pull request was merged. Null if it's open or was closed without merging |

### `IssueComments`

```python
IssueComments("pola-rs/polars")
```

Reads comments on issues and pull requests that were posted or edited since the last run. Code review comments left on a pull request's changes are not included.

| Column | Type | Description |
| --- | --- | --- |
| `id` | Int64 | Comment ID |
| `issue_number` | Int64 | Number of the issue or pull request the comment is on |
| `body` | String | Comment text in Markdown |
| `author` | String | GitHub username of the commenter |
| `author_association` | String | Commenter's role in the repository |
| `created_at` | Datetime (UTC) | When the comment was posted |
| `updated_at` | Datetime (UTC) | When the comment was last edited |
| `url` | String | Link to the comment |
| `raw` | String | Full API response for the comment as JSON |

### `Commits`

```python
Commits("pola-rs/polars", branch="main")
```

Reads commits made since the last run, oldest first. Set `branch` to read a specific branch. If it's left out, the repository's default branch is used.

| Column | Type | Description |
| --- | --- | --- |
| `sha` | String | Commit SHA |
| `message` | String | Commit message |
| `author` | String | GitHub username of the author. Null if the commit isn't linked to a GitHub account |
| `author_name` | String | Author name from git |
| `authored_at` | Datetime (UTC) | When the commit was authored |
| `committer_name` | String | Committer name from git |
| `committed_at` | Datetime (UTC) | When the commit was committed |
| `parents` | List(String) | Parent commit SHAs. Merge commits have more than one |
| `url` | String | Link to the commit |
| `raw` | String | Full API response for the commit as JSON |

### `Releases`

```python
Releases("pola-rs/polars")
```

Reads every release in the repository, newest first. Unlike the other tables, releases are reloaded in full each run and `since` is ignored.

| Column | Type | Description |
| --- | --- | --- |
| `id` | Int64 | Release ID |
| `tag_name` | String | Git tag |
| `name` | String | Release title |
| `body` | String | Release notes in Markdown |
| `draft` | Boolean | Whether the release is a draft |
| `prerelease` | Boolean | Whether the release is a pre-release |
| `target` | String | Branch or commit the tag points to |
| `author` | String | GitHub username of whoever created the release |
| `created_at` | Datetime (UTC) | When the release was created |
| `published_at` | Datetime (UTC) | When the release was published |
| `url` | String | Link to the release |
| `raw` | String | Full API response for the release as JSON |

## Running the tests

```bash
pip install -e "github[test]"
pytest github/tests
```

# Google Sheets connector

The Google Sheets connector lets Tabsdata read worksheets from [Google Sheets](https://www.google.com/sheets/about/) with publisher functions and write tables to worksheets with subscriber functions.

## Installing the connector

The connector package must be installed in two places:

1. Wherever you run `tdk`, so Tabsdata can validate connections and register functions.
2. In the Tabsdata server's function environment, so the connector is available when functions run.

### Step 1: Install the package where `tdk` runs

```bash
pip install "tabsdata-conn-googlesheets @ git+https://github.com/tabsdata/tabsdata-community-connectors.git#subdirectory=googlesheets"
```

### Step 2: Add the package to the server's function environment

Add the connector to a `requirements.txt` file:

```text
tabsdata-conn-googlesheets @ git+https://github.com/tabsdata/tabsdata-community-connectors.git#subdirectory=googlesheets
```

Then update the function and agent environments and restart the server:

```bash
tdkserver venv update --instance <instance> --name fn --requirements requirements.txt
tdkserver venv update --instance <instance> --name ag --requirements requirements.txt
tdkserver start --instance <instance>
```

> **Warning:** `tdkserver venv update` overwrites the existing package requirements for the function environment. Include every package the environment still needs in `requirements.txt`, not just this connector.

## Setting up Google access

The connector signs in with a Google Cloud service account.

1. In the [Google Cloud console](https://console.cloud.google.com), create a project or pick an existing one.
2. Enable the **Google Sheets API** for the project.
3. Create a service account under **IAM & Admin > Service Accounts**, then create a JSON key for it and download it.
4. Share each spreadsheet with the service account's email, the `client_email` in the JSON key. Give it **Viewer** access for publishers and **Editor** access for subscribers.

## Publisher

Use `GoogleSheetsSrc` in a publisher function to read worksheets and publish them as Tabsdata tables.

### Step 1: Generate the connection document

```bash
tdk connection template --type googlesheets-in --file conn-googlesheets-in.yaml
```

### Step 2: Configure the connection

Set the service account key:

```yaml
kind: connectionDef
apiVersion: '1.0'
type: tabsdata_googlesheets:GoogleSheetsSrcConn
spec:
  credentials:
    kind: gcpJsonCredentials
    apiVersion: '1.0'
    type: tabsdatak.conn.common.types:GCPJson
    spec:
      json: secret:GOOGLE__SERVICE_ACCOUNT_KEY
```

The `secret:` prefix tells Tabsdata to store the key as a secret and remove it from the stored connection document.

### Step 3: Attach the connection to a collection

```bash
tdk collection update --name sheets_landing --conn-file conn-googlesheets-in.yaml
```

To create a new collection with the connection instead, pass the same `--conn-file` to `tdk collection create`.

### Step 4: Create a publisher

Configure `GoogleSheetsSrc` as the publisher's `source` and list the `sheets` to read.

```python
from tabsdatak.api import TableFrameSpec, publisher
from tabsdata_googlesheets import GoogleSheetsSrc, Sheet


@publisher(
    source=GoogleSheetsSrc(
        sheets=[
            Sheet("1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789", "Visitors"),
            Sheet("https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789/edit", "Booths"),
        ]
    ),
    output_tables=["visitors", "booths"],
)
def pub_sheets(visitors: TableFrameSpec, booths: TableFrameSpec) -> tuple[TableFrameSpec, TableFrameSpec]:
    return visitors, booths
```

Register the function:

```bash
tdk fn register --coll sheets_landing --path pub_sheets.py::pub_sheets
```

### Publisher parameters

#### `sheets`

The worksheets to read. Each `Sheet` takes the spreadsheet, as its ID or its URL, and the worksheet, the name of the tab.

Each sheet represents one source slot and maps positionally to an argument in the publisher function.

Set `range` to read part of a worksheet, in A1 notation:

```python
Sheet("1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789", "Visitors", range="A1:D500")
```

Every column is read as text, the way it's shown in the sheet. Cast columns in a transformer if you need other types. Blank cells are null.

Sheets are read in full on every run. An empty worksheet gives the table no new data.

#### `header`

Whether the first row holds the column names. Defaults to `True`.

Blank names become `column_<n>`, and repeated names get a `_<n>` suffix. With `header=False`, every row is data and the columns are named `column_1`, `column_2`, and so on.

## Subscriber

Use `GoogleSheetsDest` in a subscriber function to write Tabsdata tables to worksheets.

### Step 1: Generate the connection document

```bash
tdk connection template --type googlesheets-out --file conn-googlesheets-out.yaml
```

### Step 2: Configure the connection

Set the service account key:

```yaml
kind: connectionDef
apiVersion: '1.0'
type: tabsdata_googlesheets:GoogleSheetsDestConn
spec:
  credentials:
    kind: gcpJsonCredentials
    apiVersion: '1.0'
    type: tabsdatak.conn.common.types:GCPJson
    spec:
      json: secret:GOOGLE__SERVICE_ACCOUNT_KEY
```

### Step 3: Attach the connection to a collection

```bash
tdk collection update --name sheets_export --conn-file conn-googlesheets-out.yaml
```

To create a new collection with the connection instead, pass the same `--conn-file` to `tdk collection create`.

### Step 4: Create a subscriber

Configure `GoogleSheetsDest` as the subscriber's `destination`.

Each sheet in `sheets` maps positionally to a value returned by the subscriber function.

```python
from tabsdatak.api import TableFrameSpec, subscriber
from tabsdata_googlesheets import GoogleSheetsDest, Sheet


@subscriber(
    input_tables=["gold/daily_totals"],
    destination=GoogleSheetsDest(
        sheets=[Sheet("1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789", "Totals")],
    ),
)
def sub_sheets(daily_totals: TableFrameSpec) -> TableFrameSpec:
    return daily_totals
```

Register the function:

```bash
tdk fn register --coll sheets_export --path sub_sheets.py::sub_sheets
```

### Subscriber parameters

#### `sheets`

The worksheets to write to. The worksheet is created if it doesn't exist. Each worksheet can only be listed once.

Each sheet represents one destination slot and maps positionally to a value returned by the subscriber function.

Values are written as they are, so text that starts with `=` stays text instead of becoming a formula. Numbers and booleans are written as numbers and booleans, nulls as blank cells, dates and times as ISO text, and lists and structs as JSON. Text longer than 50,000 characters, the most a Google Sheets cell holds, is cut short.

Each worksheet is written separately. If a later worksheet fails, the ones written before it keep their new data.

#### `if_sheet_exists`

What to do with the data already in a worksheet.

`"replace"` is the default. It clears the worksheet, then writes a header row and the rows:

```python
destination=GoogleSheetsDest(
    sheets=[Sheet("1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789", "Totals")],
    if_sheet_exists="replace",
)
```

`"append"` adds the rows under the existing ones and matches columns by the header row. Columns the header doesn't have fail the write. If the worksheet is empty, the header row is written first:

```python
destination=GoogleSheetsDest(
    sheets=[Sheet("1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789", "Totals")],
    if_sheet_exists="append",
)
```

## Running the tests

```bash
pip install -e "googlesheets[test]"
pytest googlesheets/tests
```

import os, sys, json, csv, glob, gspread
from google.oauth2.service_account import Credentials

SHEET_ID = os.environ["SHEET_ID"]
CREDS = Credentials.from_service_account_info(
    json.loads(os.environ["GOOGLE_CREDENTIALS_JSON"]),
    scopes=["https://www.googleapis.com/auth/spreadsheets"],
)
gc = gspread.authorize(CREDS)
sh = gc.open_by_key(SHEET_ID)

def upsert(tab: str, rows: list[list[str]]):
    try:
        ws = sh.worksheet(tab)
    except Exception:
        ws = sh.add_worksheet(title=tab, rows=max(100, len(rows)+10), cols=max(20, len(rows[0]) if rows else 10))
    ws.clear()
    if rows:
        ws.update("A1", rows, value_input_option="RAW")

files = sorted(glob.glob("*.csv"))
if not files:
    print("no csv files found")
    sys.exit(0)

all_rows, header = [], None
for f in files:
    with open(f, encoding="utf-8") as r:
        rd = list(csv.reader(r))
        if not rd: continue
        if header is None: header = rd[0]
        tab = os.path.splitext(os.path.basename(f))[0]
        upsert(tab, rd)
        all_rows += rd[1:]

if header:
    upsert("raw", [header] + all_rows)
print(f"Updated {len(files)} tabs + raw")


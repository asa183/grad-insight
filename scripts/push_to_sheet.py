import os, sys, json, csv, glob, gspread, os.path
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
        if not rd:
            continue
        hdr = rd[0]
        body = rd[1:] if len(rd) > 1 else []
        if header is None:
            header = hdr

        # タブ名はCSVの内容から「大学名-研究科」を優先。無ければファイル名。
        def col_index(name: str):
            try:
                return hdr.index(name)
            except ValueError:
                return None

        tab = os.path.splitext(os.path.basename(f))[0]
        idx_uni = col_index("大学名")
        idx_grad = col_index("研究科")
        if idx_uni is not None and idx_grad is not None and body:
            uni = (body[0][idx_uni] or "").strip()
            grad = (body[0][idx_grad] or "").strip()
            if uni or grad:
                tab = f"{uni}-{grad}".strip("-")

        upsert(tab, [hdr] + body)
        all_rows += body

if header:
    upsert("raw", [header] + all_rows)
print(f"Updated {len(files)} tabs + raw")

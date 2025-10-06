import os, json, gspread
from google.oauth2.service_account import Credentials

SHEET_ID = os.environ["SHEET_ID"]
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
CREDS = Credentials.from_service_account_info(
    json.loads(os.environ["GOOGLE_CREDENTIALS_JSON"]), scopes=SCOPES
)
gc = gspread.authorize(CREDS)
sh = gc.open_by_key(SHEET_ID)

def rows(tab):
    try:
        return sh.worksheet(tab).get_all_records()
    except Exception:
        return []

def split(s):
    return [x.strip() for x in str(s).split("|") if x and x.strip()]

targets_rows = rows("targets")
pages_rows   = rows("pages")

# id -> pages[]
pages_map = {}
for p in pages_rows:
    if str(p.get("enabled","TRUE")).upper() in ("FALSE","0","NO"):
        continue
    pid = p["id"]
    pages_map.setdefault(pid, []).append({
        "url": p["url"],
        "anchors": split(p.get("anchors","")),
        "page_type": (p.get("page_type") or "table").lower(),
        "selectors": {
            "table_selector": p.get("table_selector",""),
            "name_cell_idx": int(p.get("name_cell_idx") or 0),
            "theme_cell_idx": int(p.get("theme_cell_idx") or 1),
            "card_selector": p.get("card_selector",""),
            "name_selector": p.get("name_selector",""),
            "theme_selector": p.get("theme_selector",""),
            "link_selector": p.get("link_selector",""),
            "theme_split": p.get("theme_split") or r"[、，,/／・\n]+",
        },
        "dynamic": str(p.get("dynamic","false")).lower() in ("1","true","yes"),
    })

items = []
for t in targets_rows:
    if str(t.get("enabled","TRUE")).upper() in ("FALSE","0","NO"):
        continue
    tid = t["id"]
    items.append({
        "id": tid,
        "university": t["university"],
        "graduate_school": t["graduate_school"],
        "major": t["major"],
        "expected_count_total": int(t.get("expected_count_total") or 0),
        "pages": pages_map.get(tid, []),
        "enabled": True,
    })

out = "config/targets_flat.json"
os.makedirs("config", exist_ok=True)
with open(out, "w", encoding="utf-8") as f:
    json.dump(items, f, ensure_ascii=False, indent=2)
print(f"wrote {out} items={len(items)}")


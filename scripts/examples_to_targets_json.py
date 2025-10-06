import os, json, gspread, urllib.parse
from google.oauth2.service_account import Credentials

SHEET_ID = os.environ["SHEET_ID"]
CREDS = Credentials.from_service_account_info(
    json.loads(os.environ["GOOGLE_CREDENTIALS_JSON"]),
    scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
)
gc = gspread.authorize(CREDS)
ws = gc.open_by_key(SHEET_ID).worksheet("examples")

def is_enabled(v):
    s = str(v).strip().lower()
    return s in ("有効", "true", "1", "yes")

def slug_from_url(url: str) -> str:
    try:
        u = urllib.parse.urlparse(url)
        host = (u.hostname or "").split(".")
        host_slug = "-".join(host[-3:]) if len(host) >= 3 else (u.hostname or "site")
        path = (u.path or "/").strip("/").replace("/", "-")
        return (host_slug + ("-" + path if path else "")).strip("-") or "target"
    except Exception:
        return "target"

rows = ws.get_all_records()

# URLごとにグルーピング
by_url = {}
for r in rows:
    if not is_enabled(r.get("有効", "有効")):
        continue
    url = r.get("研究科URL", "") or r.get("出典URL", "")
    if not url:
        continue
    by_url.setdefault(url, []).append(r)

items = []
for url, group in by_url.items():
    rep = group[0]
    items.append({
        "id": slug_from_url(url),
        "url": url,
        "university": rep.get("大学名", ""),
        "graduate_school": rep.get("研究科", ""),
        "major": rep.get("専攻名", "") or "",
        "tag": rep.get("タグ（JP）", "") or "",
        "note": rep.get("備考", "") or "",
        "enabled": True,
    })

os.makedirs("config", exist_ok=True)
out = "config/examples_targets.json"
with open(out, "w", encoding="utf-8") as f:
    json.dump(items, f, ensure_ascii=False, indent=2)
print(f"wrote {out} items={len(items)}")


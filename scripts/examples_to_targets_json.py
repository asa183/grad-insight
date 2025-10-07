import os, json, gspread, re, unicodedata
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
    return s in ("true", "1", "yes", "有効", "y")

def truthy(v):
    s = str(v).strip().lower()
    return s in ("true", "1", "yes", "y", "有効")

def slugify_name_grad(univ: str, grad: str) -> str:
    s = f"{univ} {grad}".strip()
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"[\s\u3000]+", "-", s)
    s = re.sub(r"[^0-9A-Za-z\-\u3040-\u30FF\u4E00-\u9FFF]", "", s)
    return s.lower().strip("-") or "target"

rows = ws.get_all_records()

items = []
for r in rows:
    if not is_enabled(r.get("有効", "TRUE")):
        continue
    url = r.get("研究科URL", "") or r.get("出典URL", "")
    if not url:
        continue

    univ = r.get("大学名", "").strip()
    grad = r.get("研究科", "").strip()
    tid = slugify_name_grad(univ, grad)

    page_type = (r.get("ページ種別", "") or "").strip().lower() or None
    selectors = {}
    # list 前提のCSS
    if r.get("抽出単位（list用）"):
        selectors["item_selector"] = r.get("抽出単位（list用）").strip()
    if r.get("研究室名称（JP）の場所（CSS）"):
        selectors["lab_selector"] = r.get("研究室名称（JP）の場所（CSS）").strip()
    if r.get("教授名（JP）の場所（CSS）"):
        selectors["name_selector"] = r.get("教授名（JP）の場所（CSS）").strip()
    if r.get("研究テーマ（JP）の場所（CSS）"):
        selectors["theme_selector"] = r.get("研究テーマ（JP）の場所（CSS）").strip()
    if r.get("リンク（JP）の場所（CSS）"):
        selectors["link_selector"] = r.get("リンク（JP）の場所（CSS）").strip()
    if r.get("タグ（JP）の場所（CSS）"):
        selectors["tag_selector"] = r.get("タグ（JP）の場所（CSS）").strip()

    item = {
        "id": tid,
        "url": url,
        "university": univ,
        "graduate_school": grad,
        "major": "",
        "enabled": True,
    }
    if page_type:
        item["page_type"] = page_type
    if selectors:
        item["selectors"] = selectors
    if truthy(r.get("動的取得", "")):
        item["dynamic"] = True

    items.append(item)

os.makedirs("config", exist_ok=True)
out = "config/examples_targets.json"
with open(out, "w", encoding="utf-8") as f:
    json.dump(items, f, ensure_ascii=False, indent=2)
print(f"wrote {out} items={len(items)}")

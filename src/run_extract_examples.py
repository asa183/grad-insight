import json, csv, sys, datetime
from pathlib import Path
from .fetch import fetch_html
from .parse_examples import extract_auto

HEADER = [
    "大学名","研究科","研究科URL","研究室名称（JP）",
    "教授名（JP）","研究テーマ（JP）","リンク（JP）",
    "タグ（JP）","有効","備考",
]

def run_target(t: dict):
    html = fetch_html(t["url"]) if t.get("url") else ""
    rows = extract_auto(html) if html else []

    meta = {
        "大学名": t.get("university", ""),
        "研究科": t.get("graduate_school", ""),
        "研究科URL": t.get("url", ""),
        "タグ（JP）": t.get("tag", ""),
        "備考": t.get("note", ""),
    }

    out_rows = []
    for r in rows:
        out_rows.append({
            **meta,
            "研究室名称（JP）": "",
            "教授名（JP）": r.get("教授名（JP）") or r.get("教授名（JP)", ""),
            "研究テーマ（JP）": r.get("研究テーマ（JP）", ""),
            "リンク（JP）": r.get("リンク（JP）", ""),
            "有効": "有効",
        })

    out = Path(f"{t['id']}.csv")
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=HEADER)
        w.writeheader(); w.writerows(out_rows)
    print(f"Wrote {out} rows={len(out_rows)}")

if __name__ == "__main__":
    cfg_path = sys.argv[1] if len(sys.argv) > 1 else "config/examples_targets.json"
    items = json.loads(Path(cfg_path).read_text(encoding="utf-8"))
    for t in items:
        if t.get("enabled", True):
            run_target(t)


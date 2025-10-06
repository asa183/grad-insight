from __future__ import annotations
import csv, json, datetime, sys, os
from pathlib import Path
from collections import defaultdict

from .fetch import fetch_html, fetch_dynamic_html
from .parse import parse_table, parse_cards, parse_list

COLUMNS = [
    "大学名","研究科","専攻名","氏名（漢字）",
    "研究テーマ（スラッシュ区切り）","個人ページURL","出典URL","取得日時",
]

def extract_by_type(html: str, page_type: str, selectors: dict):
    pt = (page_type or "table").lower()
    if pt == "table":
        return [{"name": n, "theme": t, "link": u} for n,t,u in parse_table(html, {"selectors": selectors})]
    if pt == "cards":
        return [{"name": n, "theme": t, "link": u} for n,t,u in parse_cards(html, {"selectors": selectors})]
    return [{"name": n, "theme": t, "link": u} for n,t,u in parse_list(html, {"selectors": selectors})]

def run_target(t: dict) -> list[dict]:
    uni, grad, major = t["university"], t["graduate_school"], t["major"]
    merged: dict[str, dict] = {}

    def merge(name: str, theme: str, url: str, source: str, today: str):
        if name not in merged:
            merged[name] = {
                "大学名": uni, "研究科": grad, "専攻名": major,
                "氏名（漢字）": name,
                "研究テーマ（スラッシュ区切り）": theme or "",
                "個人ページURL": url or "",
                "出典URL": source, "取得日時": today,
            }
            return
        # テーマ結合（重複回避）
        a = merged[name]["研究テーマ（スラッシュ区切り）"]
        b = theme or ""
        if b:
            parts = [x.strip() for x in (a+" / "+b).split("/") if x.strip()] if a else [p.strip() for p in b.split("/") if p.strip()]
            seen, out = set(), []
            for p in parts:
                if p not in seen:
                    seen.add(p); out.append(p)
            merged[name]["研究テーマ（スラッシュ区切り）"] = " / ".join(out[:12])
        if not merged[name]["個人ページURL"] and url:
            merged[name]["個人ページURL"] = url

    today = datetime.date.today().isoformat()
    pages = t.get("pages") or [{
        "url": t.get("url"),
        "page_type": t.get("page_type", "table"),
        "selectors": t.get("selectors", {}),
        "dynamic": False,
    }]

    for p in pages:
        url = p["url"]
        html = fetch_dynamic_html(url) if p.get("dynamic") else fetch_html(url)
        rows = extract_by_type(html, p.get("page_type", "table"), p.get("selectors", {}))
        for r in rows:
            merge(r.get("name", ""), r.get("theme", ""), r.get("link", ""), url, today)

    return list(merged.values())

def main():
    # args: config_path [target_id]
    cfg_path = sys.argv[1] if len(sys.argv) > 1 else "config/targets_flat.json"
    target_id = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("TARGET_ID")
    items = json.loads(Path(cfg_path).read_text(encoding="utf-8"))
    targets = [x for x in items if x.get("enabled", True)]
    if target_id:
        targets = [x for x in targets if x.get("id") == target_id]
        if not targets:
            print(f"target not found: {target_id}", file=sys.stderr)
            sys.exit(2)

    for t in targets:
        rows = run_target(t)
        outpath = Path(f"{t['id']}.csv")
        with outpath.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=COLUMNS)
            w.writeheader(); w.writerows(rows)
        print(f"wrote {len(rows)} rows -> {outpath}")

if __name__ == "__main__":
    main()

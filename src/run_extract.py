from __future__ import annotations
import csv, json, datetime, sys, os
from pathlib import Path
from collections import defaultdict

from .fetch import fetch_html, fetch_dynamic_html
from .parse import parse_table, parse_cards, parse_list
from .html_utils import safe_select_text_soup, safe_select_href_soup, is_effective_selector
from .normalize import normalize_name
from bs4 import BeautifulSoup

COLUMNS = [
    "大学名","研究科","専攻名","氏名（漢字）",
    "研究テーマ（スラッシュ区切り）","個人ページURL","出典URL","取得日時",
    "研究室名称（JP）","タグ（JP）",
]

DEFAULT_ITEM_SELECTORS = ['li', '.member', '.teacher', '.card']


def extract_by_type(html: str, page_type: str, selectors: dict):
    pt = (page_type or "table").lower()
    if pt == "table":
        return [{"name": n, "theme": t, "link": u} for n,t,u in parse_table(html, {"selectors": selectors})]
    if pt == "cards":
        return [{"name": n, "theme": t, "link": u} for n,t,u in parse_cards(html, {"selectors": selectors})]
    return [{"name": n, "theme": t, "link": u} for n,t,u in parse_list(html, {"selectors": selectors})]


def guess_item_selector(soup: BeautifulSoup) -> str | None:
    for sel in DEFAULT_ITEM_SELECTORS:
        if soup.select_one(sel):
            return sel
    return None


def extract_list_page(html: str, base_url: str, selectors: dict) -> list[dict]:
    soup = BeautifulSoup(html, 'lxml')

    item_sel = selectors.get('item_selector')
    if not is_effective_selector(item_sel):
        item_sel = guess_item_selector(soup)
    items = soup.select(item_sel) if item_sel else []

    rows: list[dict] = []
    if not items:
        # Last resort: page-level extraction
        name = normalize_name(safe_select_text_soup(soup, selectors.get('name_selector'))) or ""
        theme = safe_select_text_soup(soup, selectors.get('theme_selector')) or ""
        link = safe_select_href_soup(soup, selectors.get('link_selector'), base_url) or ""
        lab = safe_select_text_soup(soup, selectors.get('lab_selector')) or ""
        tag = safe_select_text_soup(soup, selectors.get('tag_selector')) or ""
        if any([name, theme, link, lab, tag]):
            rows.append(dict(lab=lab, name=name, theme=theme, link=link, tag=tag))
        return rows

    for it in items:
        lab = safe_select_text_soup(it, selectors.get('lab_selector')) or ""
        name = normalize_name(safe_select_text_soup(it, selectors.get('name_selector'))) or ""
        theme = safe_select_text_soup(it, selectors.get('theme_selector')) or ""
        link = safe_select_href_soup(it, selectors.get('link_selector'), base_url) or ""
        tag = safe_select_text_soup(it, selectors.get('tag_selector')) or ""
        rows.append(dict(lab=lab, name=name, theme=theme, link=link, tag=tag))
    return rows


def run_target(t: dict) -> list[dict]:
    uni, grad, major = t.get("university", ""), t.get("graduate_school", ""), t.get("major", "")
    merged: dict[str, dict] = {}

    def merge(name: str, theme: str, url: str, source: str, today: str, lab: str = "", tag: str = ""):
        key = name or f"{lab}:{url}"
        if key not in merged:
            merged[key] = {
                "大学名": uni, "研究科": grad, "専攻名": major,
                "氏名（漢字）": name or "",
                "研究テーマ（スラッシュ区切り）": theme or "",
                "個人ページURL": url or "",
                "出典URL": source, "取得日時": today,
                "研究室名称（JP）": lab or "",
                "タグ（JP）": tag or "",
            }
            return
        # テーマ結合（重複回避）
        a = merged[key]["研究テーマ（スラッシュ区切り）"]
        b = theme or ""
        if b:
            parts = [x.strip() for x in (a+" / "+b).split("/") if x.strip()] if a else [p.strip() for p in b.split("/") if p.strip()]
            seen, out = set(), []
            for p in parts:
                if p not in seen:
                    seen.add(p); out.append(p)
            merged[key]["研究テーマ（スラッシュ区切り）"] = " / ".join(out[:12])
        if not merged[key]["個人ページURL"] and url:
            merged[key]["個人ページURL"] = url
        if not merged[key]["研究室名称（JP）"] and lab:
            merged[key]["研究室名称（JP）"] = lab
        if not merged[key]["タグ（JP）"] and tag:
            merged[key]["タグ（JP）"] = tag

    # Selectors-driven path for Examples
    if t.get("selectors") or (t.get("page_type") == "list"):
        url = t.get("url", "")
        if not url:
            return []
        html = fetch_dynamic_html(url) if t.get("dynamic") else fetch_html(url)
        rows = extract_list_page(html, url, t.get("selectors", {}))
        today = datetime.date.today().isoformat()
        empty_fields = 0
        for r in rows:
            name = r.get("name", "") or ""
            theme = r.get("theme", "") or ""
            link = r.get("link", "") or ""
            lab = r.get("lab", "") or ""
            tag = r.get("tag", "") or ""
            empty_fields += int(name == "") + int(theme == "") + int(link == "")
            merge(name, theme, link, url, today, lab=lab, tag=tag)
        print(f"INFO examples id={t.get('id','')} url={url} page_type=list item_selector={t.get('selectors',{}).get('item_selector','')} count={len(rows)} empty_fields={empty_fields}")
        if not rows:
            print(f"WARN examples id={t.get('id','')}: no items or fields extracted")
        return list(merged.values())

    # Default path (table/cards/list auto)
    today = datetime.date.today().isoformat()
    pages = t.get("pages") or [{
        "url": t.get("url"),
        "page_type": t.get("page_type", "table"),
        "selectors": t.get("selectors", {}),
        "dynamic": False,
    }]

    for p in pages:
        url = p["url"]
        if not url:
            continue
        html = fetch_dynamic_html(url) if p.get("dynamic") else fetch_html(url)
        rows = extract_by_type(html, p.get("page_type", "table"), p.get("selectors", {}))
        for r in rows:
            merge(normalize_name(r.get("name", "")) or "", r.get("theme", ""), r.get("link", ""), url, today)

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

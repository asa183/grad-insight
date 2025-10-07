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
    "研究テーマ（スラッシュ区切り）","個人ページURL","出典URL","取得日時","run_id",
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
        # No page-level fallback here (per requirements)
        return rows

    for it in items:
        lab = safe_select_text_soup(it, selectors.get('lab_selector')) or ""
        name = safe_select_text_soup(it, selectors.get('name_selector')) or ""
        theme = safe_select_text_soup(it, selectors.get('theme_selector')) or ""
        link = safe_select_href_soup(it, selectors.get('link_selector'), base_url) or ""
        tag = safe_select_text_soup(it, selectors.get('tag_selector')) or ""
        rows.append(dict(lab=lab, name=name, theme=theme, link=link, tag=tag))
    return rows


def _score_row(r: dict) -> int:
    return int(bool(r.get('name'))) + int(bool(r.get('theme'))) + int(bool(r.get('link'))) + int(bool(r.get('lab'))) + int(bool(r.get('tag')))


def run_target(t: dict) -> list[dict]:
    uni, grad, major = t.get("university", ""), t.get("graduate_school", ""), t.get("major", "")
    merged: dict[str, dict] = {}

    def merge(name: str, theme: str, url: str, source: str, today: str, run_id: str, lab: str = "", tag: str = ""):
        key = name or f"{lab}:{url}"
        if key not in merged:
            merged[key] = {
                "大学名": uni, "研究科": grad, "専攻名": major,
                "氏名（漢字）": name or "",
                "研究テーマ（スラッシュ区切り）": theme or "",
                "個人ページURL": url or "",
                "出典URL": source, "取得日時": today, "run_id": run_id,
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

    # Examples row path (prefer fixed -> CSS -> fallback -> empty)
    if "fixed" in t:
        url = t.get("url", "")
        f = t.get("fixed", {})
        sel = t.get("selectors", {})
        # 欠けがあれば取得（CSSが空でもフォールバック許容）
        need_any = any(not (f.get(k) or "") for k in ("lab","name","theme","link","tag"))
        rows_css: list[dict] = []
        rows_fb: list[dict] = []
        html = ""
        if need_any and url:
            html = fetch_dynamic_html(url) if t.get("dynamic") else fetch_html(url)
            # CSS/list抽出（指定がある場合）
            if sel.get('item_selector') or any(sel.get(x) for x in ('lab_selector','name_selector','theme_selector','link_selector','tag_selector')):
                rows_css = extract_list_page(html, url, sel)
            # フォールバック抽出（list/cards/tr）
            try:
                rows_fb_list = [{"lab":"","name": r.get("name",""), "theme": r.get("theme",""), "link": r.get("link",""), "tag":""} for r in parse_list(html, {"selectors": {}})]
            except Exception:
                rows_fb_list = []
            try:
                rows_fb_cards = [{"lab":"","name": r.get("name",""), "theme": r.get("theme",""), "link": r.get("link",""), "tag":""} for r in parse_cards(html, {"selectors": {}})]
            except Exception:
                rows_fb_cards = []
            try:
                rows_fb_tr = [{"lab":"","name": r.get("name",""), "theme": r.get("theme",""), "link": r.get("link",""), "tag":""} for r in parse_list(html, {"selectors": {"item_selector": "tr"}})]
            except Exception:
                rows_fb_tr = []
            rows_fb = rows_fb_list + rows_fb_cards + rows_fb_tr
        # 最良候補を選択
        candidates: list[tuple[str,int,dict]] = []
        for r in rows_css:
            candidates.append(("css", _score_row(r), r))
        for r in rows_fb:
            candidates.append(("fallback", _score_row(r), r))
        cand_src, _, cand = (None, 0, {})
        if candidates:
            cand_src, _, cand = max(candidates, key=lambda x: x[1])
        # fixed優先で補完
        name_val = f.get("name") or cand.get("name", "")
        if not f.get("name") and name_val and os.environ.get("EXAMPLES_NORMALIZE_NAME", "0") in ("1","true","TRUE"):
            name_val = normalize_name(name_val) or name_val
        theme_val = f.get("theme") or cand.get("theme", "")
        link_val = f.get("link") or cand.get("link", "")
        lab_val = f.get("lab") or cand.get("lab", "")
        tag_val = f.get("tag") or cand.get("tag", "")
        today = datetime.date.today().isoformat()
        run_id = os.environ.get("GITHUB_RUN_ID") or os.environ.get("RUN_ID") or today.replace("-", "")
        src_used = "fixed" if any([f.get("name"), f.get("theme"), f.get("link"), f.get("lab"), f.get("tag")]) else (cand_src or "none")
        css_count = len(rows_css) if (need_any and url) else 0
        fb_count = len(rows_fb) if (need_any and url) else 0
        print(f"INFO examples id={t.get('id','')} src={src_used} dynamic={bool(t.get('dynamic'))} fetched_items_css={css_count} fetched_items_fb={fb_count}")
        if need_any and url and not rows_css and any(sel.values()):
            print(f"WARN examples id={t.get('id','')}: selectors provided but no items extracted")
        merge(name_val or "", theme_val or "", link_val or "", url, today, run_id, lab=lab_val or "", tag=tag_val or "")
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
        run_id = os.environ.get("GITHUB_RUN_ID") or os.environ.get("RUN_ID") or today.replace("-", "")
        for r in rows:
            name_v = r.get("name", "")
            name_v = normalize_name(name_v) or name_v
            merge(name_v, r.get("theme", ""), r.get("link", ""), url, today, run_id)

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

from __future__ import annotations
import csv, json, datetime, sys, os
from pathlib import Path
from collections import defaultdict

from .fetch import fetch_html, fetch_dynamic_html
from .parse import parse_table, parse_cards, parse_list
from .html_utils import safe_select_text_soup, safe_select_href_soup, is_effective_selector
from .ocr_utils import enumerate_dom_items, run_ocr, extract_from_ocr_text, make_evidence_html, save_evidence
from .normalize import normalize_name
from bs4 import BeautifulSoup

COLUMNS = [
    "大学名","研究科","専攻名","氏名（漢字）",
    "研究テーマ（スラッシュ区切り）","個人ページURL","出典URL","取得日時","run_id",
    "研究室名称（JP）","タグ（JP）","evidence_path",
]

DEFAULT_ITEM_SELECTORS = ['li', '.member', '.teacher', '.card', '.profile', 'article', '.entry', '.list-item', '.list-group-item', '.facultyList li']


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

    if "fixed" in t:
        url = t.get("url", "")
        f0 = t.get("fixed", {})
        sel = t.get("selectors", {})
        # placeholder guard for fixed
        def _fx(v: str | None) -> str:
            s = (v or "").strip()
            return "" if s in {"name","theme","link","名前","テーマ","リンク"} else s
        f = {"lab": _fx(f0.get("lab")), "name": _fx(f0.get("name")), "theme": _fx(f0.get("theme")), "link": _fx(f0.get("link")), "tag": _fx(f0.get("tag"))}

        need_any = any(not (f.get(k) or "") for k in ("lab","name","theme","link","tag"))
        rows_css: list[dict] = []
        rows_fb: list[dict] = []
        html = ""
        dom_items = []
        # Always fetch/scan page info when URL is provided, even if fixed values are complete.
        # Purpose: CSS empty should still trigger fallback extraction to gather page info.
        if url:
            html = fetch_dynamic_html(url) if t.get("dynamic") else fetch_html(url)
            # DOM enumeration first
            try:
                item_selectors = []
                if sel.get('item_selector'):
                    item_selectors.append(sel.get('item_selector'))
                item_selectors += DEFAULT_ITEM_SELECTORS
                from urllib.parse import urlparse
                host = (urlparse(url).hostname or "")
                if host == "www2.fish.hokudai.ac.jp":
                    item_selectors = [".facultyList li", ".member", ".teacher", ".card"] + item_selectors
                if host == "www.agr.hokudai.ac.jp":
                    item_selectors = [".profile", ".card", "article", ".entry", "li"] + item_selectors
                dom_items = enumerate_dom_items(url, item_selectors, dynamic=bool(t.get("dynamic")))
                print(f"INFO examples id={t.get('id','')}: dom_items={len(dom_items)} selectors_tried={len(item_selectors)}")
            except Exception:
                dom_items = []
            if not dom_items:
                if sel.get('item_selector') or any(sel.get(x) for x in ('lab_selector','name_selector','theme_selector','link_selector','tag_selector')):
                    rows_css = extract_list_page(html, url, sel)
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
        # build base rows
        if dom_items:
            base_rows = [{"name": "", "theme": "", "link": "", "_html": it.get("html",""), "_shot": it.get("screenshot_path",""), "_seq": it.get("seq","1")} for it in dom_items]
        else:
            base_rows = rows_css if rows_css else rows_fb
        rows_out = 0
        for br in base_rows:
            name_base = br.get("name", "")
            theme_base = br.get("theme", "")
            link_base = br.get("link", "")
            lab_base = br.get("lab", "")
            tag_base = br.get("tag", "")

            # per-item OCR/CSS
            ocr_values = {"name": "", "theme": "", "link": ""}
            css_values = {"name": "", "theme": "", "link": ""}
            ev_path_item = ""
            if br.get("_shot"):
                text, ok = run_ocr(br["_shot"])  # best-effort
                if ok and text:
                    cand_i = extract_from_ocr_text(text)
                    ocr_values["name"] = cand_i.get("name") or ""
                    ocr_values["theme"] = cand_i.get("theme") or ""
                    ocr_values["link"] = cand_i.get("link") or ""
                    try:
                        orig, hi = make_evidence_html(br.get("_html",""), ocr_values)
                        run_id_local = os.environ.get("GITHUB_RUN_ID") or os.environ.get("RUN_ID") or datetime.date.today().isoformat().replace("-", "")
                        ev_path_item = save_evidence(uni, grad, run_id_local, int(br.get("_seq","1")), orig, hi, text, "", url)
                    except Exception:
                        ev_path_item = ""
            if br.get("_html") and (sel.get('name_selector') or sel.get('theme_selector') or sel.get('link_selector')):
                try:
                    from bs4 import BeautifulSoup as BS
                    frag = BS(br["_html"], "lxml")
                    css_values["name"] = safe_select_text_soup(frag, sel.get('name_selector')) or ""
                    css_values["theme"] = safe_select_text_soup(frag, sel.get('theme_selector')) or ""
                    css_values["link"] = safe_select_href_soup(frag, sel.get('link_selector'), url) or ""
                except Exception:
                    pass

            # precedence fixed > OCR > CSS > base > empty
            name_val = f.get("name") or (ocr_values["name"] or css_values["name"]) or name_base
            if not f.get("name") and name_val and os.environ.get("EXAMPLES_NORMALIZE_NAME", "0") in ("1","true","TRUE"):
                name_val = normalize_name(name_val) or name_val
            theme_val = f.get("theme") or (ocr_values["theme"] or css_values["theme"]) or theme_base
            if not f.get("theme") and theme_val:
                try:
                    from .normalize import normalize_themes
                    theme_val = normalize_themes(theme_val, r"[、，,/／・\n]+", None, 12) or theme_val
                except Exception:
                    pass
            link_val = f.get("link") or (ocr_values["link"] or css_values["link"]) or link_base
            lab_val = f.get("lab") or lab_base
            tag_val = f.get("tag") or tag_base

            today = datetime.date.today().isoformat()
            run_id = os.environ.get("GITHUB_RUN_ID") or os.environ.get("RUN_ID") or today.replace("-", "")
            merge(name_val or "", theme_val or "", link_val or "", url, today, run_id, lab=lab_val or "", tag=tag_val or "")
            key = (name_val or "") or f"{lab_val or ''}:{url}"
            if ev_path_item and key in merged:
                merged[key]["evidence_path"] = ev_path_item
            rows_out += 1

        print(f"INFO examples id={t.get('id','')} dom_items={len(dom_items)} src={'dom-ocr' if dom_items else ('css' if rows_css else ('fallback' if rows_fb else 'none'))} dynamic={bool(t.get('dynamic'))} fetched_items_css={len(rows_css)} fetched_items_fb={len(rows_fb)} rows_out={rows_out}")
        if (need_any and url and not rows_css and any(sel.values()) and not dom_items):
            print(f"WARN examples id={t.get('id','')}: selectors provided but no items extracted")
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

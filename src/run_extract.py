from __future__ import annotations
import csv, json, datetime, sys, os
from pathlib import Path
from collections import defaultdict

from .fetch import fetch_html, fetch_dynamic_html
from .parse import parse_table, parse_cards, parse_list
from .html_utils import safe_select_text_soup, safe_select_href_soup, is_effective_selector
from .ocr_utils import enumerate_dom_items, run_ocr, extract_from_ocr_text, make_evidence_html, save_evidence, has_playwright, has_ocr
from .normalize import normalize_name
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import hashlib
import time
import re

COLUMNS = [
    "大学名","研究科","専攻名","氏名（漢字）",
    "研究テーマ（スラッシュ区切り）","個人ページURL","出典URL","取得日時","run_id",
    "研究室名称（JP）","タグ（JP）","evidence_path",
]

DEFAULT_ITEM_SELECTORS = ['li', '.member', '.teacher', '.card', '.profile', '.item-faculty', '.faculty-member', 'article', '.entry', '.list-item', '.list-group-item', '.facultyList li']


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


def _compute_row_key(name: str, link: str, lab: str, html_frag: str, seq: str | None) -> str:
    n = (name or "").strip()
    u = (link or "").strip()
    l = (lab or "").strip()
    if u:
        return f"link:{u}"
    if n and l:
        return f"name+lab:{n}|{l}"
    if n:
        return f"name:{n}"
    if html_frag:
        h = hashlib.sha1(html_frag.encode("utf-8", errors="ignore")).hexdigest()[:12]
        return f"frag:{h}"
    return f"anon:{seq or ''}"


_JAPANESE_CHAR_RE = re.compile(r"[\u4E00-\u9FFF\u3040-\u30FF]")
_LATIN_NAME_RE = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ'\-.]+(?: [A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ'\-.]+){0,3}$")
_TITLE_TOKENS = {
    # English/General
    "professor","associate","assistant","lecturer","instructor","adjunct","visiting","emeritus","senior","junior",
    "prof","dr","mr","mrs","ms","phd","m.sc","msc","bsc","dphil","postdoc","postdoctoral","researcher","fellow","scholar","faculty","staff",
    # Japanese
    "教授","准教授","助教","講師","先生","名誉教授","客員教授","特任教授","特任講師","特任助教","招へい教員","招聘教授",
    "研究員","共同研究員","研究支援員","研究助手","助手","学振特別研究員","特別研究員",
    "職員","教職員","事務職員","技術職員","技術補佐員","事務補佐員",
}

def _strip_titles(s: str) -> str:
    t = (s or "").strip()
    t = re.sub(r"\s+", " ", t)
    parts = [p for p in re.split(r"[|／/｜]| - | – | — |,", t) if p]
    if parts:
        t = parts[0].strip()
    toks = t.replace(".", " ").split()
    toks = [w for w in toks if w.lower() not in _TITLE_TOKENS]
    return " ".join(toks).strip()

def clean_person_name(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return ""
    s = re.sub(r"\s+", " ", s)
    if len(s) > 64:
        return ""
    if any(x in s for x in ("http://","https://","@")):
        return ""
    cand = _strip_titles(s)
    cand = re.sub(r"\s*\(.*?\)\s*", "", cand).strip()
    if _JAPANESE_CHAR_RE.search(cand):
        # Japanese-like if it contains JP chars and not too many delimiters
        if len(cand) >= 2:
            return cand
        return ""
    # Latin-like
    if _LATIN_NAME_RE.match(cand):
        return cand
    # Heuristic: 2-4 words, letters/hyphen/apostrophe only (allow middle dot)
    ws = cand.replace("・", " ").split()
    if 1 <= len(ws) <= 4 and all(re.match(r"^[A-Za-zÀ-ÖØ-öø-ÿ'\-]+$", w) for w in ws):
        return cand
    return ""

_INDIVIDUAL_URL_HINTS = (
    "/faculty-member/","/r/lab/","/faculty/","/teacher/","/member/","/members/",
    "/people/","/person/","/persons/","/profile","/profiles/","/researcher","/researchers/",
    "/staff/","/directory/","/facultylist","/faculty-list",
)

def looks_individual_link(link: str, page_url: str) -> bool:
    u = (link or "").strip()
    if not u:
        return False
    if u == page_url:
        return False
    try:
        from urllib.parse import urlparse
        lp = urlparse(u)
        pu = urlparse(page_url)
        root = f"{pu.scheme}://{pu.netloc}/"
        if u == root:
            return False
        path = lp.path or ""
        # Hokkaido fish: /faculty-member/<slug>
        if "/faculty-member/" in path:
            rest = path.split("/faculty-member/")[-1]
            first = rest.split("/")[0] if rest else ""
            if not first:
                return False
            if first.startswith("genre") or "genre_" in rest:
                return False
            return True
        # Hokkaido agr: /r/lab/<lab>[#anchor]
        if "/r/lab/" in path:
            return True
        # People/Profile directories of other sites
        if re.search(r"/(people|person|persons|profiles?|researcher|researchers|staff)/[A-Za-z0-9\-_.]+", path, flags=re.IGNORECASE):
            return True
    except Exception:
        # best-effort fallback
        return any(h in u for h in _INDIVIDUAL_URL_HINTS)
    # hint-based fallback
    return any(h in u for h in _INDIVIDUAL_URL_HINTS)

_TITLE_WORDS = [
    # Japanese titles
    "教授","准教授","助教","講師","名誉教授","特任教授","客員教授","特任講師","特任助教","招聘教授","招へい教員",
    "研究員","共同研究員","特別研究員","研究支援員","研究助手","助手","教職員","職員","技術職員","事務職員","技術補佐員","事務補佐員",
    # English titles
    "Professor","Associate Professor","Assistant Professor","Adjunct Professor","Visiting Professor","Professor Emeritus",
    "Lecturer","Senior Lecturer","Instructor","Research Fellow","Researcher","Senior Researcher","Postdoctoral Researcher","Postdoc","Visiting Scholar",
]

def find_name_by_title(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return ""
    # Normalize spaces
    t = re.sub(r"\s+", " ", t)
    # Patterns: Name + Title, Title + Name
    name_charset = r"[A-Za-zÀ-ÖØ-öø-ÿ'\-\u4E00-\u9FFF\u3040-\u30FF・]{1,30}"
    title_join = "|".join(re.escape(w) for w in _TITLE_WORDS)
    patterns = [
        rf"({name_charset})[ 　]*?(?:{title_join})",
        rf"(?:{title_join})[ 　]*?({name_charset})",
    ]
    for pat in patterns:
        m = re.search(pat, t, flags=re.IGNORECASE)
        if m:
            cand = m.group(1).strip()
            cand = clean_person_name(cand)
            if cand:
                return cand
    return ""


def run_target(t: dict) -> list[dict]:
    uni, grad, major = t.get("university", ""), t.get("graduate_school", ""), t.get("major", "")
    merged: dict[str, dict] = {}

    def merge(name: str, theme: str, url: str, source: str, today: str, run_id: str, lab: str = "", tag: str = "", row_key: str | None = None):
        # Strictly use provided row_key for de-duplication to avoid over-merge
        key = row_key or f"anon:{hashlib.sha1((name+theme+lab+(url or '')).encode('utf-8', errors='ignore')).hexdigest()[:10]}"
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
        # metrics & diagnostics
        drops = {"no_name_link": 0, "dup_key": 0, "not_person": 0}
        merge_count = {"link": 0, "name+lab": 0, "name": 0, "frag": 0, "anon": 0}
        ts_fetch_start = time.time()
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
                    item_selectors = [
                        "main li:has(a[href*='/faculty-member/'])",
                        "main .facultyList li",
                        "main .item-faculty",
                        "main .faculty-member",
                        ".facultyList li",
                        ".member",
                        ".teacher",
                        ".card",
                    ] + item_selectors
                if host == "www.agr.hokudai.ac.jp":
                    item_selectors = [
                        "main li:has(a[href*='/r/lab/'])",
                        "main .profile",
                        "main article",
                        "main .entry",
                        "main .item-faculty",
                        "main .faculty-member",
                        ".profile",
                        ".card",
                        "article",
                        ".entry",
                        "li",
                    ] + item_selectors
                # limits from env
                import os as _os
                max_items_env = int(_os.environ.get("EX_ENUM_MAX_ITEMS", "80") or 80)
                max_shots_env = int(_os.environ.get("EX_ENUM_MAX_SHOTS", "8") or 8)
                dom_items = enumerate_dom_items(
                    url,
                    item_selectors,
                    dynamic=bool(t.get("dynamic")),
                    max_items=max_items_env,
                    max_screenshots=max_shots_env,
                )
                print(f"INFO examples id={t.get('id','')}: dom_items={len(dom_items)} selectors_tried={len(item_selectors)} max_items={max_items_env} max_shots={max_shots_env}")
            except Exception:
                dom_items = []
        ts_fetch_end = time.time()
        if not dom_items:
            pt = (t.get("page_type") or "").lower()
            if pt == "cards" or sel.get('card_selector'):
                try:
                    rows_css_cards = [{"lab":"","name": r.get("name",""), "theme": r.get("theme",""), "link": urljoin(url, r.get("link","")) if r.get("link") else "", "tag":""} for r in parse_cards(html, {"selectors": sel})]
                except Exception:
                    rows_css_cards = []
                rows_css = rows_css_cards
            elif sel.get('item_selector') or any(sel.get(x) for x in ('lab_selector','name_selector','theme_selector','link_selector','tag_selector')):
                rows_css = extract_list_page(html, url, sel)
            try:
                rows_fb_list = [{"lab":"","name": r.get("name",""), "theme": r.get("theme",""), "link": urljoin(url, r.get("link","")) if r.get("link") else "", "tag":""} for r in parse_list(html, {"selectors": {}})]
            except Exception:
                rows_fb_list = []
            try:
                rows_fb_cards = [{"lab":"","name": r.get("name",""), "theme": r.get("theme",""), "link": urljoin(url, r.get("link","")) if r.get("link") else "", "tag":""} for r in parse_cards(html, {"selectors": {}})]
            except Exception:
                rows_fb_cards = []
            try:
                rows_fb_tr = [{"lab":"","name": r.get("name",""), "theme": r.get("theme",""), "link": urljoin(url, r.get("link","")) if r.get("link") else "", "tag":""} for r in parse_list(html, {"selectors": {"item_selector": "tr"}})]
            except Exception:
                rows_fb_tr = []
            rows_fb = rows_fb_list + rows_fb_cards + rows_fb_tr
        # build base rows
        if dom_items:
            base_rows = [{"name": "", "theme": "", "link": "", "_html": it.get("html",""), "_shot": it.get("screenshot_path",""), "_seq": it.get("seq","1")} for it in dom_items]
        else:
            base_rows = rows_css if rows_css else rows_fb
        rows_out = 0
        before_rows = len(base_rows)
        # Mode selection: default bulk unless explicitly set to single
        mode_env = (os.environ.get("EXAMPLES_MODE") or os.environ.get("EXTRACT_MODE") or "bulk").lower()
        single_mode = (mode_env == "single") and bool(f.get("name") or f.get("link"))
        # Merge breakdown counters already initialized
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
            # Extract from fragment even without selectors (generic fallback allowed)
            if br.get("_html"):
                try:
                    from bs4 import BeautifulSoup as BS
                    frag = BS(br["_html"], "lxml")
                    # Prefer supplied selectors first
                    name_css = safe_select_text_soup(frag, sel.get('name_selector')) or ""
                    theme_css = safe_select_text_soup(frag, sel.get('theme_selector')) or ""
                    link_css = safe_select_href_soup(frag, sel.get('link_selector'), url) or ""
                    link_anchor_text = ""
                    # Generic fallbacks
                    if not name_css:
                        for s2 in (".name", ".teacher-name", ".ttl", ".title", ".heading", "[class*='name']", "strong", "h3", "h2"):
                            v = safe_select_text_soup(frag, s2)
                            if v:
                                name_css = v; break
                        if not name_css:
                            txt = frag.get_text(" ", strip=True) or ""
                            parts = txt.split()
                            name_css = " ".join(parts[:2]) if parts else ""
                    if not link_css:
                        # Prefer individual-like links before generic anchors; keep anchor text if available
                        for lsel in ("a[href*='/faculty-member/']", "a[href*='/r/lab/']", "a[href*='/faculty']", "a[href*='/teacher']", "a[href*='/member']"):
                            a = frag.select_one(lsel)
                            if a and a.has_attr("href"):
                                try:
                                    link_css = urljoin(url, a.get("href") or "")
                                except Exception:
                                    link_css = a.get("href") or ""
                                link_anchor_text = a.get_text(" ", strip=True) or link_anchor_text
                                break
                    if not link_css:
                        link_css = safe_select_href_soup(frag, None, url) or ""
                    if not theme_css:
                        for s2 in (".desc", ".description", ".research", ".field", ".keyword", ".content", ".text", "p", "li"):
                            v = safe_select_text_soup(frag, s2)
                            if v:
                                theme_css = v; break
                    # If name is still empty, try anchor text as a fallback for name
                    if not name_css and link_anchor_text:
                        name_css = link_anchor_text
                    # Title-based extraction (e.g., "教授", "Associate Professor")
                    if not name_css:
                        frag_text = frag.get_text(" ", strip=True)
                        nb = find_name_by_title(frag_text)
                        if nb:
                            name_css = nb
                    css_values["name"] = name_css
                    css_values["theme"] = theme_css
                    css_values["link"] = link_css
                except Exception:
                    pass

            # precedence: bulk -> OCR/CSS/base -> fixed (補完のみ) / single -> fixed を優先
            if single_mode:
                name_val = f.get("name") or (ocr_values["name"] or css_values["name"]) or name_base
                theme_val = f.get("theme") or (ocr_values["theme"] or css_values["theme"]) or theme_base
                link_val = f.get("link") or (ocr_values["link"] or css_values["link"]) or link_base
                lab_val = f.get("lab") or lab_base
                tag_val = f.get("tag") or tag_base
            else:
                name_val = (ocr_values["name"] or css_values["name"]) or name_base or f.get("name") or ""
                theme_val = (ocr_values["theme"] or css_values["theme"]) or theme_base or f.get("theme") or ""
                link_val = (ocr_values["link"] or css_values["link"]) or link_base or f.get("link") or ""
                lab_val = lab_base or f.get("lab") or ""
                tag_val = tag_base or f.get("tag") or ""
            # Person name cleaning (loose, multilingual)
            cleaned_name = clean_person_name(name_val)
            if cleaned_name:
                name_val = cleaned_name
            if not f.get("name") and name_val and os.environ.get("EXAMPLES_NORMALIZE_NAME", "0") in ("1","true","TRUE"):
                name_val = normalize_name(name_val) or name_val
            if not f.get("theme") and theme_val:
                try:
                    from .normalize import normalize_themes
                    theme_val = normalize_themes(theme_val, r"[、，,/／・\n]+", None, 12) or theme_val
                except Exception:
                    pass
            if link_val:
                try:
                    link_val = urljoin(url, link_val)
                except Exception:
                    pass
            # lab/tag は補完優先（既存維持）
            if single_mode:
                pass

            today = datetime.date.today().isoformat()
            run_id = os.environ.get("GITHUB_RUN_ID") or os.environ.get("RUN_ID") or today.replace("-", "")
            # Compute stable row key (avoid collapsing into 1 row)
            # Use link in key only if it looks like an individual page, otherwise fall back to name/frag
            link_for_key = link_val or ""
            try:
                from urllib.parse import urlparse
                pu = urlparse(url)
                root = f"{pu.scheme}://{pu.netloc}/"
                if (not any(x in (link_for_key or "") for x in ("/faculty-member/", "/r/lab/"))) or (link_for_key in (url, root)):
                    link_for_key = ""
            except Exception:
                pass
            row_key = _compute_row_key(name_val or "", link_for_key or "", lab_val or "", br.get("_html", ""), br.get("_seq"))
            # Require at least a name or link in bulk mode to retain the row
            if not single_mode:
                if not (name_val or link_val):
                    drops["no_name_link"] += 1
                    continue
                # If name doesn't look like a person and link not individual-like, drop
                if not clean_person_name(name_val) and not looks_individual_link(link_val, url):
                    drops["not_person"] += 1
                    continue
            # If single mode, filter rows by fixed name/link when available
            if single_mode:
                matched = True
                if f.get("link"):
                    fx_link = f.get("link") or ""
                    matched = bool(link_val) and (link_val == fx_link or link_val.startswith(fx_link) or fx_link.startswith(link_val))
                if matched and f.get("name"):
                    try:
                        fx_name = f.get("name") or ""
                        norm_fx = normalize_name(fx_name) or fx_name
                        norm_cur = normalize_name(name_val or "") or (name_val or "")
                        matched = norm_fx and norm_cur and (norm_fx == norm_cur or norm_fx in norm_cur or norm_cur in norm_fx)
                    except Exception:
                        matched = (f.get("name") in (name_val or ""))
                if not matched:
                    continue
            dup_before = (row_key in merged)
            merge(name_val or "", theme_val or "", link_val or "", url, today, run_id, lab=lab_val or "", tag=tag_val or "", row_key=row_key)
            key = row_key
            # Merge breakdown
            if key.startswith("link:"):
                merge_count["link"] += 1
            elif key.startswith("name+lab:"):
                merge_count["name+lab"] += 1
            elif key.startswith("name:"):
                merge_count["name"] += 1
            elif key.startswith("frag:"):
                merge_count["frag"] += 1
            else:
                merge_count["anon"] += 1
            if ev_path_item and key in merged:
                merged[key]["evidence_path"] = ev_path_item
            rows_out += 1
            if dup_before:
                drops["dup_key"] += 1

        print(
            f"INFO examples id={t.get('id','')} dom_items={len(dom_items)} src={'dom-ocr' if dom_items else ('css' if rows_css else ('fallback' if rows_fb else 'none'))} "
            f"dynamic={bool(t.get('dynamic'))} fetched_items_css={len(rows_css)} fetched_items_fb={len(rows_fb)} rows_out={rows_out} "
            f"unique_rows={len(merged)} before_rows={before_rows} mode={'single' if single_mode else 'bulk'} "
            f"merge_keys=link:{merge_count['link']},name+lab:{merge_count['name+lab']},name:{merge_count['name']},frag:{merge_count['frag']},anon:{merge_count['anon']}"
        )
        if (need_any and url and not rows_css and any(sel.values()) and not dom_items):
            print(f"WARN examples id={t.get('id','')}: selectors provided but no items extracted")
        # Structured log output per target
        try:
            os.makedirs("logs", exist_ok=True)
            run_id_global = os.environ.get("GITHUB_RUN_ID") or os.environ.get("RUN_ID") or datetime.date.today().isoformat().replace("-", "")
            from urllib.parse import urlparse
            host = (urlparse(url).hostname or "")
            # limits/env
            limits = {
                "EX_ENUM_MAX_ITEMS": int(os.environ.get("EX_ENUM_MAX_ITEMS", "0") or 0),
                "EX_ENUM_MAX_SHOTS": int(os.environ.get("EX_ENUM_MAX_SHOTS", "0") or 0),
                "EX_ENUM_TIMEOUT_MS": int(os.environ.get("EX_ENUM_TIMEOUT_MS", "0") or 0),
                "EX_NAV_TIMEOUT_MS": int(os.environ.get("EX_NAV_TIMEOUT_MS", "0") or 0),
                "EX_ACTION_TIMEOUT_MS": int(os.environ.get("EX_ACTION_TIMEOUT_MS", "0") or 0),
            }
            log_obj = {
                "meta": {
                    "run_id": run_id_global,
                    "target_id": t.get("id"),
                    "url": url,
                    "host": host,
                    "mode": "single" if single_mode else "bulk",
                    "capabilities": {"playwright": has_playwright(), "ocr": has_ocr()},
                },
                "counts": {
                    "dom_items": len(dom_items),
                    "rows_out": rows_out,
                    "unique_rows": len(merged),
                    "fetched_items_css": len(rows_css),
                    "fetched_items_fb": len(rows_fb),
                    "before_rows": before_rows,
                },
                "merge_keys": merge_count,
                "drops": drops,
                "limits": limits,
                "timings": {"fetch_total_ms": int((ts_fetch_end - ts_fetch_start) * 1000)},
                "first_row_preview": next(iter(merged.values())) if merged else {},
            }
            # status/code
            code = "OK"
            if len(dom_items) == 0 and rows_out == 0:
                code = "DOM_ENUM_ZERO"
            elif rows_out == 0:
                code = "EXTRACT_ZERO"
            else:
                ratio = (len(merged) / max(1, before_rows)) if before_rows else 1.0
                if ratio < 0.8:
                    code = "MERGE_COLLAPSE"
            log_obj["status"] = ("ERROR" if code in ("DOM_ENUM_ZERO", "EXTRACT_ZERO") else ("WARN" if code == "MERGE_COLLAPSE" else "OK"))
            log_obj["code"] = code
            out_json = Path("logs") / f"{t.get('id','target')}.json"
            out_json.write_text(json.dumps(log_obj, ensure_ascii=False, indent=2), encoding="utf-8")
            # optional row-level CSV (sampled)
            if (os.environ.get("DEBUG_ARTIFACTS") or "0") not in ("", "0", "false", "False"):
                sample_n = int(os.environ.get("DEBUG_SAMPLE_N", "50") or 50)
                rows_vals = list(merged.values())
                sample = rows_vals[:sample_n]
                out_csv = Path("logs") / f"{t.get('id','target')}_rows.csv"
                with out_csv.open("w", newline="", encoding="utf-8") as rf:
                    w = csv.DictWriter(rf, fieldnames=COLUMNS)
                    w.writeheader(); w.writerows(sample)
        except Exception:
            pass
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
            link_v = r.get("link", "")
            if link_v:
                try:
                    link_v = urljoin(url, link_v)
                except Exception:
                    pass
            # Skip rows with no name and no link
            if not (name_v or link_v):
                continue
            row_key = _compute_row_key(name_v or "", link_v or "", "", "", None)
            merge(name_v, r.get("theme", ""), link_v, url, today, run_id, row_key=row_key)

    return list(merged.values())

def main():
    # args: config_path [target_id]
    cfg_path = sys.argv[1] if len(sys.argv) > 1 else "config/targets_flat.json"
    target_id = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("TARGET_ID")
    items = json.loads(Path(cfg_path).read_text(encoding="utf-8"))
    # Capability flags for diagnostics
    print(f"INFO capabilities: PLAYWRIGHT_AVAILABLE={has_playwright()} OCR_AVAILABLE={has_ocr()}")
    targets = [x for x in items if x.get("enabled", True)]
    if target_id:
        targets = [x for x in targets if x.get("id") == target_id]
        if not targets:
            print(f"target not found: {target_id}", file=sys.stderr)
            sys.exit(2)

    total_rows = 0
    for t in targets:
        rows = run_target(t)
        total_rows += len(rows)
        outpath = Path(f"{t['id']}.csv")
        with outpath.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=COLUMNS)
            w.writeheader(); w.writerows(rows)
        print(f"wrote {len(rows)} rows -> {outpath}")
    if total_rows == 0:
        print("ERROR: no rows extracted across all targets", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()

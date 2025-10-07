from __future__ import annotations
import os, re, datetime
from typing import Optional, Tuple, Dict, List
from .normalize import normalize_name
import time as _time

def _has_module(name: str) -> bool:
    try:
        __import__(name)
        return True
    except Exception:
        return False

def has_playwright() -> bool:
    return _has_module("playwright")

def has_ocr() -> bool:
    return _has_module("pytesseract") and _has_module("PIL")


def try_render_screenshot(url: str, dynamic: bool = False, wait_ms: int = 1500) -> Optional[str]:
    if not _has_module("playwright"):
        return None
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 2000})
            page.goto(url, wait_until="networkidle")
            if wait_ms:
                import time
                time.sleep(wait_ms/1000)
            path = os.path.join("evidence", "_screenshots")
            os.makedirs(path, exist_ok=True)
            out = os.path.join(path, "shot_" + str(int(datetime.datetime.now().timestamp())) + ".png")
            page.screenshot(path=out, full_page=True)
            browser.close()
            return out
    except Exception:
        return None

def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except Exception:
        return default

def enumerate_dom_items(
    url: str,
    item_selectors: List[str],
    dynamic: bool = False,
    max_items: int = 80,
    max_screenshots: int = 10,
    nav_timeout_ms: int | None = None,
    action_timeout_ms: int | None = None,
    overall_timeout_ms: int | None = None,
) -> List[Dict[str, str]]:
    """Enumerate DOM items by selectors, capturing outerHTML and per-item screenshots.

    Returns a list of dicts: {html, screenshot_path, seq}.
    If Playwright (Python) is unavailable, returns an empty list gracefully.
    """
    if not _has_module("playwright"):
        return []
    # resolve limits from env if not provided
    if max_items is None:
        max_items = _env_int("EX_ENUM_MAX_ITEMS", 80)
    if max_screenshots is None:
        max_screenshots = _env_int("EX_ENUM_MAX_SHOTS", 8)
    if nav_timeout_ms is None:
        nav_timeout_ms = _env_int("EX_NAV_TIMEOUT_MS", 15000)
    if action_timeout_ms is None:
        action_timeout_ms = _env_int("EX_ACTION_TIMEOUT_MS", 5000)
    if overall_timeout_ms is None:
        overall_timeout_ms = _env_int("EX_ENUM_TIMEOUT_MS", 120000)

    items: List[Dict[str, str]] = []
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
        from time import sleep
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 2000})
            try:
                page.set_default_navigation_timeout(nav_timeout_ms)
                page.set_default_timeout(action_timeout_ms)
            except Exception:
                pass
            page.goto(url, wait_until="networkidle")
            if dynamic:
                sleep(1.0)
            out_dir = os.path.join("evidence", "_screenshots")
            os.makedirs(out_dir, exist_ok=True)

            seen_html = set()
            seq = 1
            shots = 0
            start_ts = _time.time()
            for sel in item_selectors or []:
                try:
                    handles = page.query_selector_all(sel)
                except Exception:
                    handles = []
                # light progress
                try:
                    print(f"INFO enum: sel={sel} found={len(handles)} collected={len(items)} shots={shots}")
                except Exception:
                    pass
                for h in handles:
                    if len(items) >= max_items:
                        break
                    if (_time.time() - start_ts) * 1000 >= overall_timeout_ms:
                        break
                    # Choose target element: escalate narrow name-part elements to closest container block
                    target = h
                    try:
                        cls = (h.get_attribute("class") or "").lower()
                    except Exception:
                        cls = ""
                    try:
                        inner_txt = h.evaluate("el => (el.innerText||'').trim()") or ""
                    except Exception:
                        inner_txt = ""
                    try:
                        has_nameish_class = any(k in cls for k in ("family", "given", "surname", "first", "last", "name"))
                    except Exception:
                        has_nameish_class = False
                    is_narrow = False
                    try:
                        box = h.bounding_box() or {"width": 0, "height": 0}
                        is_small_box = box.get("width", 0) < 160 or box.get("height", 0) < 50
                    except Exception:
                        is_small_box = False
                    if (len(inner_txt) <= 4) or has_nameish_class or is_small_box:
                        try:
                            target = h.evaluate_handle("el => el.closest('li, article, .card, .member, .teacher, .profile, .faculty-member, .item-faculty, .entry') || el")
                        except Exception:
                            target = h
                    try:
                        html = target.evaluate("el => el.outerHTML") or ""
                    except Exception:
                        html = ""
                    if not html or html in seen_html:
                        continue
                    seen_html.add(html)
                    # Save per-item screenshot (best-effort)
                    shot_path = ""
                    if shots < max_screenshots:
                        shot_name = f"item_{int(datetime.datetime.now().timestamp())}_{seq}.png"
                        shot_path_tmp = os.path.join(out_dir, shot_name)
                        try:
                            target.screenshot(path=shot_path_tmp, timeout=action_timeout_ms)
                            shot_path = shot_path_tmp
                            shots += 1
                        except Exception:
                            shot_path = ""
                    items.append({"html": html, "screenshot_path": shot_path, "seq": str(seq)})
                    seq += 1
                if len(items) >= max_items:
                    break
                if (_time.time() - start_ts) * 1000 >= overall_timeout_ms:
                    break
            browser.close()
    except Exception:
        return []
    return items


def run_ocr(image_path: str) -> Tuple[str, bool]:
    if not (_has_module("pytesseract") and _has_module("PIL")):
        return "", False
    try:
        from PIL import Image  # type: ignore
        import pytesseract  # type: ignore
        text = pytesseract.image_to_string(Image.open(image_path), lang="jpn+eng")
        return text, True
    except Exception:
        return "", False


NAME_RE = re.compile(r"[一-龥々〆ヵヶ]{1,4}[\u3000 ・ ]+[一-龥々〆ヵヶ]{1,6}")


def extract_from_ocr_text(text: str) -> Dict[str, str]:
    out = {"name": "", "theme": "", "link": ""}
    if not text:
        return out
    m = NAME_RE.search(text)
    if m:
        n = m.group(0).replace("\u3000", " ").replace("・", " ").strip()
        out["name"] = normalize_name(n) or n
    if not out["name"]:
        for ln in text.splitlines():
            n2 = normalize_name(ln)
            if n2:
                out["name"] = n2
                break
    for ln in text.splitlines():
        l = ln.strip()
        if not l:
            continue
        if re.search(r"(研究|専門|テーマ|キーワード|Research|Interests)", l):
            out["theme"] = re.sub(r"\s+", " ", l)
            break
    m2 = re.search(r"https?://[\w\-\./#?=&%]+", text)
    if m2:
        out["link"] = m2.group(0)
    return out


def make_evidence_html(page_html: str, match_texts: Dict[str, str]):
    frag = page_html or ""
    highlighted = frag
    for k in ("name", "theme", "link"):
        v = match_texts.get(k) or ""
        if not v:
            continue
        try:
            highlighted = highlighted.replace(v, f"<mark>{v}</mark>")
        except Exception:
            pass
    tpl = '<!doctype html><meta charset="utf-8"><style>body{font-family:sans-serif;line-height:1.6} mark{background:#ff0}</style><body>{}</body>'
    return tpl.format(frag), tpl.format(highlighted)


def escape_html(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def save_evidence(univ: str, grad: str, run_id: str, seq: int, original_html: str, highlighted_html: str,
                  ocr_text_raw: str, normalized_text: str, source_url: str) -> str:
    folder = f"evidence/{univ}_{grad}"
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"run_{run_id}_{seq}.html")
    body = [
        '<!doctype html><meta charset="utf-8">',
        '<style>body{font-family:sans-serif;line-height:1.6} mark{background:#ff0}</style>',
        f"<h3>Evidence (run_id={run_id})</h3>",
        f"<p><b>Source:</b> <a href='{source_url}'>{source_url}</a></p>",
        f"<details><summary>OCR Raw Text</summary><pre>{escape_html(ocr_text_raw)}</pre></details>",
        f"<details><summary>Normalized Text</summary><pre>{escape_html(normalized_text)}</pre></details>",
        "<h4>Original Fragment</h4>", original_html,
        "<h4>Highlighted Fragment</h4>", highlighted_html,
    ]
    with open(path, "w", encoding="utf-8") as f:
        f.write("".join(body))
    return path

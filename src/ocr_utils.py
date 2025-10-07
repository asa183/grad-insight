from __future__ import annotations
import os, re, datetime
from typing import Optional, Tuple, Dict

def _has_module(name: str) -> bool:
    try:
        __import__(name)
        return True
    except Exception:
        return False


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


NAME_RE = re.compile(r"[一-龥々〆ヵヶ]{1,4}[\u3000 ]+[一-龥々〆ヵヶ]{1,6}")


def extract_from_ocr_text(text: str) -> Dict[str, str]:
    out = {"name": "", "theme": "", "link": ""}
    if not text:
        return out
    m = NAME_RE.search(text)
    if m:
        out["name"] = m.group(0).strip()
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


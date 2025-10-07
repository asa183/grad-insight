from __future__ import annotations
from bs4 import BeautifulSoup
from typing import Any
import re
from urllib.parse import urljoin

# Placeholders that should never be interpreted as CSS selectors
PLACEHOLDER_LITERALS = {"name", "theme", "link", "名前", "テーマ", "リンク", "href", "alt"}


def compress_ws(s: str | None) -> str:
    if not s:
        return ""
    return re.sub(r"\s+", " ", s).strip()


def is_effective_selector(selector: str | None) -> bool:
    if not selector:
        return False
    s = selector.strip()
    if not s or s in PLACEHOLDER_LITERALS:
        return False
    return True


def split_selector_attr(selector: str | None) -> tuple[str | None, str | None]:
    if not is_effective_selector(selector):
        return None, None
    s = selector.strip()
    if "@" in s:
        css, attr = s.rsplit("@", 1)
        css = css.strip() or None
        attr = attr.strip() or None
        if not css or not attr:
            return None, None
        if attr in PLACEHOLDER_LITERALS:
            return css, None
        return css, attr
    return s, None


def safe_select_text_soup(root: BeautifulSoup | Any, selector: str | None) -> str:
    css, attr = split_selector_attr(selector)
    if not css:
        return ""
    el = root.select_one(css)
    if not el:
        return ""
    if attr:
        return compress_ws(el.get(attr) or "")
    return compress_ws(el.get_text(separator=" "))


def safe_select_href_soup(root: BeautifulSoup | Any, selector: str | None, base_url: str) -> str:
    css, attr = split_selector_attr(selector)
    if css:
        a = root.select_one(css)
        if a:
            if attr:
                val = a.get(attr)
                if not val:
                    return ""
                if attr.lower() in {"href", "src", "data-href", "data-url"}:
                    return urljoin(base_url, val)
                return compress_ws(val)
            if a.has_attr("href"):
                return urljoin(base_url, a.get("href") or "")
    # Fallback: first link within root
    a = root.select_one("a[href]")
    if a and a.has_attr("href"):
        return urljoin(base_url, a.get("href") or "")
    return ""

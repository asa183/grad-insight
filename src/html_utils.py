from __future__ import annotations
from bs4 import BeautifulSoup
from typing import Any
import re
from urllib.parse import urljoin

# Placeholders that should never be interpreted as CSS selectors
PLACEHOLDER_LITERALS = {"name", "theme", "link", "名前", "テーマ", "リンク"}


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


def safe_select_text_soup(root: BeautifulSoup | Any, selector: str | None) -> str:
    if not is_effective_selector(selector):
        return ""
    el = root.select_one(selector)
    if not el:
        return ""
    return compress_ws(el.get_text(separator=" "))


def safe_select_href_soup(root: BeautifulSoup | Any, selector: str | None, base_url: str) -> str:
    if is_effective_selector(selector):
        a = root.select_one(selector)
        if a and a.has_attr("href"):
            return urljoin(base_url, a.get("href") or "")
    # Fallback: first link within root
    a = root.select_one("a[href]")
    if a and a.has_attr("href"):
        return urljoin(base_url, a.get("href") or "")
    return ""


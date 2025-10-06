from __future__ import annotations
from bs4 import BeautifulSoup
from typing import Any
import re
from .normalize import normalize_name, normalize_themes

def _table_with_headers(soup: BeautifulSoup, table_selector: str | None, header_keywords: list[str] | None) -> Any:
    if table_selector:
        for t in soup.select(table_selector):
            hdr = " ".join(th.get_text(" ", strip=True) for th in t.find_all("th"))
            if not header_keywords or all(k in hdr for k in header_keywords):
                return t
    # フォールバック: 全tableからキーワード一致
    for t in soup.find_all("table"):
        hdr = " ".join(th.get_text(" ", strip=True) for th in t.find_all("th"))
        if header_keywords and all(k in hdr for k in header_keywords):
            return t
    return None

def parse_table(html: str, meta: dict) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    sels = meta.get("selectors", {})
    table = _table_with_headers(
        soup,
        table_selector=sels.get("table_selector"),
        header_keywords=sels.get("header_keywords"),
    )
    if not table:
        return []

    name_idx = int(sels.get("name_cell_idx", 0))
    theme_idx = int(sels.get("theme_cell_idx", 1))
    cleanup = sels.get("name_cleanup_regex")

    rules = meta.get("split_rules", {})
    split_pattern = rules.get("theme_split", r"[、，,/／・\n]+")
    exclude_re = rules.get("theme_exclude")
    max_topics = int(rules.get("max_topics", 12))

    recs: list[dict] = []
    for tr in table.find_all("tr"):
        cells = tr.find_all(["td", "th"])
        if len(cells) <= max(name_idx, theme_idx):
            continue
        name_text = cells[name_idx].get_text("\n", strip=True)
        nm = normalize_name(name_text, cleanup)
        if not nm:
            continue
        theme_raw = cells[theme_idx].get_text("\n", strip=True)
        theme = normalize_themes(theme_raw, split_pattern, exclude_re, max_topics)
        if theme is None:
            theme = ""
        # リンク（任意）
        a = cells[name_idx].find("a")
        link = a.get("href") if a else ""
        recs.append({"name": nm, "theme": theme, "link": link})
    return recs

def parse_cards(html: str, meta: dict) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    sels = meta.get("selectors", {})
    card_sel = sels.get("card_selector")
    name_sel = sels.get("name_selector")
    theme_sel = sels.get("theme_selector")
    link_sel = sels.get("link_selector")
    cleanup = sels.get("name_cleanup_regex")

    rules = meta.get("split_rules", {})
    split_pattern = rules.get("theme_split", r"[、，,/／・\n]+")
    exclude_re = rules.get("theme_exclude")
    max_topics = int(rules.get("max_topics", 12))

    recs: list[dict] = []
    for card in soup.select(card_sel or ".card, .profile, .teacher"):
        name_text = card.select_one(name_sel).get_text(" ", strip=True) if name_sel and card.select_one(name_sel) else card.get_text(" ", strip=True)
        nm = normalize_name(name_text, cleanup)
        if not nm:
            continue
        theme_node = card.select_one(theme_sel) if theme_sel else None
        theme_raw = theme_node.get_text("\n", strip=True) if theme_node else ""
        theme = normalize_themes(theme_raw, split_pattern, exclude_re, max_topics)
        link = ""
        if link_sel and card.select_one(link_sel):
            link = card.select_one(link_sel).get("href") or ""
        recs.append({"name": nm, "theme": theme, "link": link})
    return recs

def parse_list(html: str, meta: dict) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    sels = meta.get("selectors", {})
    item_sel = sels.get("item_selector") or "li, .item"
    name_sel = sels.get("name_selector")
    theme_sel = sels.get("theme_selector")
    link_sel = sels.get("link_selector")
    cleanup = sels.get("name_cleanup_regex")

    rules = meta.get("split_rules", {})
    split_pattern = rules.get("theme_split", r"[、，,/／・\n]+")
    exclude_re = rules.get("theme_exclude")
    max_topics = int(rules.get("max_topics", 12))

    recs: list[dict] = []
    for it in soup.select(item_sel):
        node = it.select_one(name_sel) if name_sel else it
        name_text = node.get_text(" ", strip=True)
        nm = normalize_name(name_text, cleanup)
        if not nm:
            continue
        theme_node = it.select_one(theme_sel) if theme_sel else None
        theme_raw = theme_node.get_text("\n", strip=True) if theme_node else ""
        theme = normalize_themes(theme_raw, split_pattern, exclude_re, max_topics)
        link = ""
        if link_sel and it.select_one(link_sel):
            link = it.select_one(link_sel).get("href") or ""
        recs.append({"name": nm, "theme": theme, "link": link})
    return recs


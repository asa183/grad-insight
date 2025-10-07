from __future__ import annotations
from bs4 import BeautifulSoup
from typing import Any
import re
from .normalize import normalize_name, normalize_themes
from .html_utils import select_text_all

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

    # フォールバック候補（カード系も幅広く）
    fallback_cards = ".card, .profile, .profile-card, .facultyCard, .teacher, .member, .item-faculty, .faculty-member, article, .entry"
    fallback_name = [name_sel] if name_sel else [".name", ".teacher-name", ".ttl", ".title", ".heading", ".[class*='name']"]
    fallback_theme = [theme_sel] if theme_sel else [".desc", ".description", ".research", ".field", ".keyword", ".content", ".text", "p", "li"]

    recs: list[dict] = []
    for card in soup.select(card_sel or fallback_cards):
        # name
        name_text = ""
        for sel in fallback_name:
            if not sel:
                continue
            name_text = select_text_all(card, sel)
            if name_text:
                break
        if not name_text:
            name_text = card.get_text(" ", strip=True)
        nm = normalize_name(name_text, cleanup)
        if not nm and name_text:
            nm2 = normalize_name(card.get_text(" ", strip=True), cleanup)
            if nm2:
                nm = nm2
        if not nm:
            continue
        # theme
        theme_node = None
        for sel in fallback_theme:
            if not sel:
                continue
            theme_node = card.select_one(sel)
            if theme_node:
                break
        theme_raw = theme_node.get_text("\n", strip=True) if theme_node else ""
        theme = normalize_themes(theme_raw, split_pattern, exclude_re, max_topics)
        # link
        link = ""
        link_node = card.select_one(link_sel) if link_sel else (card.select_one("a[href]") or None)
        if link_node:
            link = link_node.get("href") or ""
        recs.append({"name": nm, "theme": theme, "link": link})
    return recs

def parse_list(html: str, meta: dict) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    sels = meta.get("selectors", {})
    item_sel = sels.get("item_selector") or "li, .member, .teacher, .card, .item, tr, .profile, article, .entry, .list-item, .list-group-item, .facultyList li"
    name_sel = sels.get("name_selector")
    theme_sel = sels.get("theme_selector")
    link_sel = sels.get("link_selector")
    cleanup = sels.get("name_cleanup_regex")

    rules = meta.get("split_rules", {})
    split_pattern = rules.get("theme_split", r"[、，,/／・\n]+")
    exclude_re = rules.get("theme_exclude")
    max_topics = int(rules.get("max_topics", 12))

    # フォールバック候補（リスト系も幅広く）
    fallback_name = [name_sel] if name_sel else [".name", ".teacher-name", ".ttl", ".title", ".heading", ".[class*='name']"]
    fallback_theme = [theme_sel] if theme_sel else [".desc", ".description", ".research", ".field", ".keyword", ".content", ".text", "p", "li"]

    recs: list[dict] = []
    for it in soup.select(item_sel):
        # name
        name_text = ""
        for sel in fallback_name:
            if not sel:
                continue
            name_text = select_text_all(it, sel)
            if name_text:
                break
        if not name_text:
            name_text = it.get_text(" ", strip=True)
        nm = normalize_name(name_text, cleanup)
        if not nm and name_text:
            nm2 = normalize_name(it.get_text(" ", strip=True), cleanup)
            if nm2:
                nm = nm2
        if not nm:
            continue
        # theme
        theme_node = None
        for sel in fallback_theme:
            if not sel:
                continue
            theme_node = it.select_one(sel)
            if theme_node:
                break
        theme_raw = theme_node.get_text("\n", strip=True) if theme_node else ""
        theme = normalize_themes(theme_raw, split_pattern, exclude_re, max_topics)
        # link
        link = ""
        link_node = it.select_one(link_sel) if link_sel else (it.select_one("a[href]") or None)
        if link_node:
            link = link_node.get("href") or ""
        recs.append({"name": nm, "theme": theme, "link": link})
    return recs

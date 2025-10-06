from __future__ import annotations
import contextlib
import time
import requests

UA = {"User-Agent": "GradInsightBot/1.0 (+https://example.org)"}

def fetch_html(url: str, timeout: int = 30) -> str:
    r = requests.get(url, headers=UA, timeout=timeout)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or r.encoding
    # 軽いノイズ除去（Word由来など）
    return r.text.replace("MsoNormalTable", "").replace("Normal 0 0", "")

def fetch_dynamic_html(url: str, wait_ms: int = 1500) -> str:
    # Playwright が無い場合は通常fetchにフォールバック
    with contextlib.suppress(ImportError):
        from playwright.sync_api import sync_playwright  # type: ignore
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.goto(url, wait_until="networkidle")
            if wait_ms:
                time.sleep(wait_ms / 1000)
            html = page.content()
            browser.close()
            return html
    return fetch_html(url)

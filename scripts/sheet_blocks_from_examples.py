#!/usr/bin/env python3
from __future__ import annotations
import argparse, os, json, sys, time
from pathlib import Path

# Ensure repository root is on sys.path so that `src` can be imported on CI
try:
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
except Exception:
    pass
from typing import List, Dict
import requests

from google.oauth2.service_account import Credentials
import gspread

from src.html_blockify import blockify_html, json_dumps_safe


UA = {"User-Agent": "GradInsightBlockify/1.0 (+https://github.com/asa183/grad-insight)"}


def truthy(v: str | None) -> bool:
    s = str(v or "").strip().lower()
    return s in ("true", "1", "yes", "y", "on", "有効")


def slugify_page(univ: str, grad: str) -> str:
    import re
    s = f"{univ} {grad}".strip()
    s = re.sub(r"[\u3000\s]+", "-", s)
    s = re.sub(r"[^0-9A-Za-z\-\u3040-\u30FF\u4E00-\u9FFF]", "", s)
    return s.lower().strip("-") or "page"


def fetch_html(url: str, timeout: int = 10, retries: int = 2) -> tuple[str, str]:
    last_err = None
    for i in range(retries + 1):
        try:
            t0 = time.time()
            r = requests.get(url, headers=UA, timeout=timeout)
            elapsed_ms = int((time.time() - t0) * 1000)
            ctype = r.headers.get("Content-Type", "")
            if r.status_code >= 400:
                print(f"FETCH status={r.status_code} elapsed={elapsed_ms}ms url={url}")
                return "", ctype
            if "text/html" not in ctype:
                print(f"FETCH status={r.status_code} elapsed={elapsed_ms}ms url={url} WARN non-html content-type={ctype}")
                return "", ctype
            r.encoding = r.apparent_encoding or r.encoding
            print(f"FETCH status={r.status_code} elapsed={elapsed_ms}ms url={url}")
            return r.text or "", ctype
        except Exception as e:
            last_err = e
    if last_err:
        print(f"FETCH ERROR url={url} err={last_err}")
    return "", ""


def ensure_worksheet(sh, title: str):
    # If exists, return; else create (with fallback suffix -2, -3 ...)
    try:
        return sh.worksheet(title)
    except Exception:
        pass
    try:
        return sh.add_worksheet(title=title, rows=100, cols=20)
    except Exception:
        # try with suffixes
        for i in range(2, 10):
            t2 = f"{title}-{i}"
            try:
                return sh.add_worksheet(title=t2, rows=100, cols=20)
            except Exception:
                continue
    raise RuntimeError(f"Failed to create worksheet for title={title}")


def write_blocks(ws, header: List[str], rows: List[List[str]]):
    try:
        ws.clear()
    except Exception:
        pass
    if not rows:
        return
    ws.update("A1", [header] + rows, value_input_option="RAW")


def run(sheet_id: str, examples_name: str, max_blocks: int):
    # Auth
    creds = Credentials.from_service_account_info(
        json.loads(os.environ["GOOGLE_CREDENTIALS_JSON"]),
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(sheet_id)
    ws_ex = sh.worksheet(examples_name)
    rows = ws_ex.get_all_records()

    run_id = os.environ.get("GITHUB_RUN_ID") or time.strftime("%Y%m%d%H%M%S")
    sha = os.environ.get("GITHUB_SHA", "")[:7]
    run_id_full = f"{run_id}-{sha}" if sha else run_id

    header = [
        "run_id","university","graduate_school","source_url","page_id",
        "block_id","tag","depth","group_id","path","has_img","text","links_json",
    ]

    for r in rows:
        if not truthy(r.get("有効")):
            continue
        univ = (r.get("大学名", "") or "").strip()
        grad = (r.get("研究科", "") or "").strip()
        url = (r.get("研究科URL", "") or r.get("出典URL", "") or "").strip()
        if not url:
            continue
        page_id = slugify_page(univ, grad)
        print(f"START id={page_id} university={univ} graduate_school={grad} url={url}")
        html, ctype = fetch_html(url)
        if not html:
            print(f"WARN skip: empty or non-html url={url}")
            continue
        t0 = time.time()
        # Golden example fields (Examples sheet may have columns with fullwidth brackets)
        def col(*names: str) -> str:
            for nm in names:
                v = r.get(nm)
                if v:
                    return str(v)
            return ""
        golden = {
            "name": col("教授名（JP）", "教授名（JP}"),
            "theme": col("研究テーマ（JP）", "研究テーマ（JP}"),
            "link": col("リンク（JP）", "リンク（JP}"),
        }
        prefer_role = os.environ.get('PREFER_ROLE','').lower() in ('1','true','yes')
        blocks = blockify_html(url, html, max_blocks=max_blocks, golden=golden, prefer_role=prefer_role)
        elapsed_ms = int((time.time() - t0) * 1000)
        print(f"PARSE blocks_total={len(blocks)} blocks_kept={len(blocks)} elapsed={elapsed_ms}ms")
        # Sheet title
        title = f"{univ}-{grad}-blocks".strip("-")
        ws_out = ensure_worksheet(sh, title)
        out_rows: List[List[str]] = []
        for b in blocks:
            out_rows.append([
                run_id_full,
                univ,
                grad,
                url,
                page_id,
                b.get("block_id",""),
                b.get("tag",""),
                b.get("depth",""),
                b.get("group_id",""),
                b.get("path",""),
                b.get("has_img","FALSE"),
                (b.get("text","") or "")[:45000],
                b.get("links_json","[]"),
            ])
        write_blocks(ws_out, header, out_rows)
        print(f"WRITE sheet=\"{ws_out.title}\" rows={len(out_rows)}")


def main():
    ap = argparse.ArgumentParser(description="Blockify pages from Examples sheet and write to the same workbook")
    ap.add_argument("--sheet-id", dest="sheet_id", default=os.environ.get("SHEET_ID"), help="Google Sheet ID")
    ap.add_argument("--examples-name", dest="examples_name", default="examples", help="Examples worksheet name")
    ap.add_argument("--max-blocks", dest="max_blocks", type=int, default=300, help="Max blocks per page")
    args = ap.parse_args()
    if not args.sheet_id:
        print("ERROR: sheet-id is required via --sheet-id or SHEET_ID env", file=sys.stderr)
        sys.exit(2)
    run(args.sheet_id, args.examples_name, args.max_blocks)


if __name__ == "__main__":
    main()

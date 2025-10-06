import csv, re, datetime, sys
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

UNIV="慶應義塾大学"; GRAD="商学研究科"
MAJOR="商業学分野 Commercial science (Marketing)"
SRC="https://www.fbc.keio.ac.jp/graduate/shougyou.html"
OUT="keio_marketing_scrape.csv"

NAME_RE=re.compile(r"([一-龥々〆ヵヶ]{1,4})[ \u3000]?([一-龥々〆ヵヶ]{1,4})")

def normalize_themes(s:str)->str:
    # 記号除去・改行→スラッシュ / 英数字や冗長語を抑制
    s=re.sub(r"[（）\(\)\[\]【】]"," ",s)
    parts=re.split(r"[、，,/／・\n\r\t]+",s)
    parts=[p.strip(" 　") for p in parts if p.strip(" 　")]
    # ノイズ抑制（英数字や長すぎる要素は除外）
    cleaned=[]
    for p in parts:
        if len(p)>30: continue
        if re.search(r"^[A-Za-z0-9]+$", p): continue
        cleaned.append(p)
    # 重複除去（順序保持）
    seen=set(); uniq=[]
    for p in cleaned:
        if p not in seen:
            seen.add(p); uniq.append(p)
    return " / ".join(uniq[:12])

def fetch_html(url:str)->str:
    r=requests.get(url, timeout=20)
    r.raise_for_status()
    r.encoding=r.apparent_encoding or r.encoding
    return r.text

def find_target_table(soup:BeautifulSoup):
    # ヘッダに「担当者」「専門分野」「主要著作」を含むテーブルを探す
    for table in soup.find_all(["table"]):
        headers=[(th.get_text(" ", strip=True)) for th in table.find_all("th")]
        header_text=" ".join(headers)
        if all(k in header_text for k in ["担当者","専門分野","主要著作"]):
            return table
    # セクション見出しから近傍のテーブル
    h = soup.find(lambda tag: tag.name in ["h2","h3","h4"] and "教員紹介" in tag.get_text())
    if h:
        nxt=h.find_next("table")
        if nxt: return nxt
    return None

def extract_records(table, base_url:str):
    recs=[]
    for tr in table.find_all("tr"):
        tds=tr.find_all(["td","th"])  # 柔軟に
        if len(tds)<3: 
            continue
        # 1列目: 氏名（漢字）、リンク
        cell0_text=tds[0].get_text("\n", strip=True)
        # タイトル語を除去してから氏名抽出
        cleaned=re.sub(r"(担当者|教授|准教授|特任教授|助教)", " ", cell0_text)
        cleaned=re.sub(r"\s+", " ", cleaned)
        m=NAME_RE.search(cleaned)
        if not m:
            continue
        g1, g2 = m.group(1), m.group(2)
        # 名前分割の補正（例: 白井美由里 → 白井 美由里）
        if len(g1)>=3 and len(g2)<=2:
            name=f"{g1[:2]} {g1[2:]}{g2}"
        else:
            name=f"{g1} {g2}"
        a=tds[0].find("a")
        url=""
        if a and a.get("href"):
            url=urljoin(base_url, a.get("href"))
        # 2列目: 専門分野
        theme_raw=tds[1].get_text("\n", strip=True)
        theme=normalize_themes(theme_raw)
        recs.append({
            "大学名":UNIV,
            "研究科":GRAD,
            "専攻名":MAJOR,
            "氏名（漢字）":name,
            "研究テーマ（スラッシュ区切り）":theme,
            "個人ページURL":url,
            "出典URL":SRC,
            "取得日時":datetime.date.today().isoformat(),
        })
    # 重複名マージ（テーマ結合）
    merged={}
    for r in recs:
        k=r["氏名（漢字）"]
        if k not in merged:
            merged[k]=r
        else:
            a=merged[k]["研究テーマ（スラッシュ区切り）"]
            b=r["研究テーマ（スラッシュ区切り）"]
            if b and b not in a:
                merged[k]["研究テーマ（スラッシュ区切り）"] = " / ".join([x for x in [a,b] if x])
            if not merged[k]["個人ページURL"] and r["個人ページURL"]:
                merged[k]["個人ページURL"]=r["個人ページURL"]
    return list(merged.values())

def main():
    html=fetch_html(SRC)
    soup=BeautifulSoup(html, "lxml")
    table=find_target_table(soup)
    if not table:
        print("対象テーブルが見つかりませんでした", file=sys.stderr)
        sys.exit(2)
    recs=extract_records(table, SRC)
    cols=["大学名","研究科","専攻名","氏名（漢字）","研究テーマ（スラッシュ区切り）","個人ページURL","出典URL","取得日時"]
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w=csv.DictWriter(f, fieldnames=cols)
        w.writeheader(); w.writerows(recs)
    print(f"書き出し: {OUT} 行数={len(recs)}")

if __name__ == "__main__":
    main()

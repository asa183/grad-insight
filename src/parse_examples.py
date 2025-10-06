from bs4 import BeautifulSoup
import re

NAME_RE = re.compile(r"[一-龥々〆ヵヶ]{1,4}[\u3000 ]+[一-龥々〆ヵヶ]{1,4}")
ROLE_RE = re.compile(r"(教授|准教授|特任教授|助教|担当者)")

def norm_name(s: str) -> str:
    s = (s or "")
    s = ROLE_RE.sub("", s)
    s = re.sub(r"\s+", " ", s).strip()
    m = NAME_RE.search(s)
    if m:
        return m.group(0)
    m2 = re.match(r"([一-龥々〆ヵヶ]{2,4})([一-龥々〆ヵヶ]{2,4})$", s)
    return f"{m2.group(1)} {m2.group(2)}" if m2 else s

def norm_theme(s: str) -> str:
    if not s:
        return ""
    s = re.sub(r"[（）\(\)\[\]【】]", " ", s)
    parts = [p.strip() for p in re.split(r"[、，,/／・\n]+", s) if p.strip()]
    parts = [p for p in parts if not re.search(r"(Journal|Vol\.|pp\.|書房)", p)]
    out, seen = [], set()
    for p in parts:
        if p not in seen:
            seen.add(p); out.append(p)
    return " / ".join(out[:12])

def _table_candidates(soup: BeautifulSoup):
    c = []
    for t in soup.select("table"):
        ths = " ".join(th.get_text(" ", strip=True) for th in t.select("th"))
        score = 0
        if re.search(r"(専門|研究|担当)", ths):
            score += 2
        if len(t.select("tr")) >= 5:
            score += 1
        c.append((score, t))
    return [t for score, t in sorted(c, key=lambda x: -x[0]) if score > 0]

def extract_auto(html: str):
    soup = BeautifulSoup(html, "lxml")
    rows = []

    # 1) table優先
    for table in _table_candidates(soup):
        trs = table.select("tr")
        if not trs:
            continue
        maxcols = max(len(tr.find_all(["td", "th"])) for tr in trs)
        counts = []
        for ci in range(maxcols):
            col = [
                tr.find_all(["td", "th"])[ci].get_text(" ", strip=True)
                if len(tr.find_all(["td", "th"])) > ci
                else ""
                for tr in trs
            ]
            name_hits = sum(1 for v in col if NAME_RE.search(v))
            theme_hits = sum(1 for v in col if re.search(r"(専門|研究|マーケ|消費|サイエン)", v))
            counts.append((ci, name_hits, theme_hits))
        name_idx = max(counts, key=lambda x: x[1])[0]
        theme_idx = max(counts, key=lambda x: x[2])[0]
        for tr in trs:
            tds = tr.find_all(["td", "th"])
            if len(tds) <= max(name_idx, theme_idx):
                continue
            name = norm_name(tds[name_idx].get_text(" ", strip=True))
            if not NAME_RE.search(name):
                continue
            theme = norm_theme(tds[theme_idx].get_text(" ", strip=True))
            url = ""
            a = tds[name_idx].select_one("a[href]")
            if a and a.get("href"):
                url = a["href"]
            rows.append({"教授名（JP)": name, "教授名（JP）": name, "研究テーマ（JP）": theme, "リンク（JP）": url})
        if rows:
            return rows

    # 2) cards / list 推定
    cards = soup.select(".card, .profile, .teacher, .member, .list, ul, ol")
    for block in cards:
        items = block.select(".card, .profile, .teacher, li, .member") or [block]
        for it in items:
            text = it.get_text(" ", strip=True)
            nm = NAME_RE.search(text)
            if not nm:
                continue
            name = norm_name(nm.group(0))
            theme_el = None
            for sel in [".field", ".expertise", ".desc", ".tags", "p", "li"]:
                el = it.select_one(sel)
                if el and re.search(r"(専門|研究|マーケ|消費|統計|サイエン)", el.get_text()):
                    theme_el = el
                    break
            theme = norm_theme(theme_el.get_text(" ", strip=True) if theme_el else "")
            url = ""
            a = it.select_one("a[href]")
            if a and a.get("href"):
                url = a["href"]
            rows.append({"教授名（JP)": name, "教授名（JP）": name, "研究テーマ（JP）": theme, "リンク（JP）": url})
    return rows


import re

NAME_RE = re.compile(r"([一-龥々〆ヵヶ]{1,4})[ \u3000]+([一-龥々〆ヵヶ]{1,6})")

def normalize_name(text: str, cleanup_regex: str | None = None) -> str | None:
    s = text or ""
    if cleanup_regex:
        s = re.sub(cleanup_regex, " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    m = NAME_RE.search(s)
    if not m:
        # スペース無し 4–6 連続漢字 → 2+残りの素朴分割
        m2 = re.search(r"([一-龥々〆ヵヶ]{2,3})([一-龥々〆ヵヶ]{2,4})", s)
        if not m2:
            return None
        return f"{m2.group(1)} {m2.group(2)}".strip()
    g1, g2 = m.group(1), m.group(2)
    # 白井美由里 → 白井 美由里 の補正
    if len(g1) >= 3 and len(g2) <= 2:
        return f"{g1[:2]} {g1[2:]}{g2}".strip()
    return f"{g1} {g2}".strip()

def normalize_themes(s: str, split_pattern: str, exclude_re: str | None = None, max_topics: int = 12) -> str:
    s = re.sub(r"[（）\(\)\[\]【】]", " ", s or "")
    parts = re.split(split_pattern, s)
    out: list[str] = []
    for p in parts:
        p = p.strip(" 　")
        if not p:
            continue
        if exclude_re and re.search(exclude_re, p):
            continue
        if len(p) > 30:
            continue
        out.append(p)
    # 重複除去（順序保持）
    seen, uniq = set(), []
    for p in out:
        if p not in seen:
            seen.add(p); uniq.append(p)
    return " / ".join(uniq[:max_topics])


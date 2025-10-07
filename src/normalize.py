import re

JP = r"[一-龥々〆ヵヶ]"
NAME_RE = re.compile(rf"({JP}{{1,4}})[ \u3000]+({JP}{{1,6}})")
TITLE_RE = re.compile(
    r"(教授|准教授|助教|講師|助教授|特任教授|特任准教授|特任講師|非常勤講師|客員教授|客員准教授|客員講師|名誉教授|研究員|特別研究員|助手|主任)")

def normalize_name(text: str, cleanup_regex: str | None = None) -> str | None:
    s = text or ""
    # normalize spaces and middle dot
    s = s.replace("\u3000", " ").replace("・", " ")
    # remove titles (twice for safety)
    s = TITLE_RE.sub(" ", s)
    if cleanup_regex:
        s = re.sub(cleanup_regex, " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = TITLE_RE.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    # remove bracketed phrases
    s = re.sub(r"[（\(【\[][^)】\]]+[）\)】\]]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    m = NAME_RE.search(s)
    if not m:
        # collect JP blocks (2-4) and use first two
        blocks = re.findall(rf"{JP}{{2,4}}", s)
        if len(blocks) >= 2:
            return f"{blocks[0]} {blocks[1]}".strip()
        # fallback: continuous 4–6 kanji split 2+rest
        m2 = re.search(rf"({JP}{{2,3}})({JP}{{2,4}})", s)
        if m2:
            return f"{m2.group(1)} {m2.group(2)}".strip()
        return None
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

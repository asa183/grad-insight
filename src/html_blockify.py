from __future__ import annotations
import re
from typing import List, Dict, Tuple, Optional
from urllib.parse import urljoin, urlparse

try:
    from selectolax.parser import HTMLParser, Node
    HAVE_SELECTOLAX = True
except Exception:
    HAVE_SELECTOLAX = False
    from bs4 import BeautifulSoup  # type: ignore


REMOVALS = {"script", "style", "noscript", "svg", "canvas", "nav", "aside", "footer", "header"}
BLOCK_TAGS = {"div", "section", "article", "li", "td"}
ROLE_KEYWORDS = [
    # Japanese titles
    "教授","准教授","助教","講師","特任教授","客員教授","名誉教授","非常勤講師","招聘教授","招へい教員",
    # English titles
    "Professor","Associate Professor","Assistant Professor","Adjunct Professor","Visiting Professor","Professor Emeritus",
    "Lecturer","Senior Lecturer","Instructor","Research Fellow","Researcher","Senior Researcher","Postdoctoral"
]


def _slugify(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"[\u3000\s]+", "-", s)
    s = re.sub(r"[^0-9A-Za-z\-\u3040-\u30FF\u4E00-\u9FFF]", "", s)
    return s.lower().strip("-") or "page"


def _text_with_breaks_sel(node: "Node") -> str:
    # Minimal: rely on selectolax text extraction
    try:
        s = node.text() or ""
        return s.strip()
    except Exception:
        return ""


def _iter_children_sel(n: "Node"):
    try:
        c = n.child
        while c is not None:
            yield c
            c = c.next
    except Exception:
        return


def _nth_index_in_parent(n: "Node") -> int:
    p = getattr(n, "parent", None)
    if not p:
        return 1
    idx = 1
    for c in _iter_children_sel(p):
        if c is n:
            return idx
        if getattr(c, "tag", None) == getattr(n, "tag", None):
            idx += 1
    return idx


def _css_path(n: "Node", max_depth: int = 8) -> str:
    parts: List[str] = []
    cur: Optional[Node] = n
    depth = 0
    while cur is not None and depth < max_depth:
        if not getattr(cur, "tag", None):
            break
        tag = cur.tag
        idx = _nth_index_in_parent(cur)
        parts.append(f"{tag}:nth-of-type({idx})")
        cur = cur.parent
        depth += 1
    parts.reverse()
    return ">".join(parts)


def _child_signature(n: "Node") -> str:
    counts: Dict[str, int] = {}
    for c in _iter_children_sel(n):
        tag = getattr(c, "tag", None)
        if not tag:
            continue
        counts[tag] = counts.get(tag, 0) + 1
    items = sorted(counts.items())
    return ";".join(f"{k}:{v}" for k, v in items)


def _make_absolute(node: Node, base_url: str):
    try:
        for a in node.css("a"):
            href = a.attributes.get("href")
            if href:
                a.attributes["href"] = urljoin(base_url, href)
        for im in node.css("img"):
            src = im.attributes.get("src")
            if src:
                im.attributes["src"] = urljoin(base_url, src)
    except Exception:
        pass


def _remove_unwanted(root: Node):
    for sel in REMOVALS:
        try:
            for n in list(root.css(sel)):
                try:
                    n.remove()
                except Exception:
                    pass
        except Exception:
            continue
    

def _has_role_text(text: str) -> bool:
    t = text or ""
    return any(k in t for k in ROLE_KEYWORDS)


def blockify_html(url: str, html: str, max_blocks: int = 300, golden: Optional[Dict[str, str]] = None) -> List[Dict[str, str]]:
    base_url = url
    out: List[Dict[str, str]] = []
    if not HAVE_SELECTOLAX:
        # Fallback with BeautifulSoup (slower, but acceptable for minimal impl)
        from bs4 import BeautifulSoup  # type: ignore
        from bs4.element import Tag  # type: ignore
        soup = BeautifulSoup(html, "lxml")
        for t in ("script","style","noscript","svg","canvas","nav","aside","footer","header"):
            for n in soup.find_all(t):
                n.decompose()
        # base
        base_tag = soup.find("base")
        if base_tag and base_tag.get("href"):
            base_url = urljoin(base_url, base_tag.get("href"))
        # absolute urls
        for a in soup.select("a[href]"):
            a["href"] = urljoin(base_url, a.get("href", ""))
        for im in soup.select("img[src]"):
            im["src"] = urljoin(base_url, im.get("src", ""))
        blocks = soup.select(",".join(BLOCK_TAGS))
        # group by signature
        groups: Dict[str, List[Tag]] = {}
        for n in blocks:
            text = n.get_text("\n", strip=True)
            if not text:
                continue
            sig = f"{n.name}|{len(list(n.children))}|{','.join(sorted(c.name for c in n.find_all(recursive=False)))}"
            groups.setdefault(sig, []).append(n)
        # score and pick
        def grp_has_role(ns: List[Tag]) -> bool:
            try:
                return any(_has_role_text(x.get_text(" ", strip=True)) for x in ns)
            except Exception:
                return False
        def grp_max_text(ns: List[Tag]) -> int:
            try:
                return max(len(x.get_text(" ", strip=True)) for x in ns)
            except Exception:
                return 0
        kept: List[Tag] = []
        for sig, nodes in sorted(groups.items(), key=lambda kv: (-int(grp_has_role(kv[1])), -len(kv[1]), -grp_max_text(kv[1]))):
            for nd in nodes:
                kept.append(nd)
                if len(kept) >= max_blocks:
                    break
            if len(kept) >= max_blocks:
                break
        # build rows
        block_id = 0
        for nd in kept:
            block_id += 1
            tag = nd.name.upper()
            depth = len(list(nd.parents))
            has_img = bool(nd.find("img"))
            path = ""
            try:
                path = " > ".join([e.name for e in list(nd.parents)[::-1][:8]])
            except Exception:
                path = tag
            links = []
            for a in nd.select("a[href]"):
                txt = a.get_text(" ", strip=True)
                links.append({"href": a.get("href",""), "text": txt})
            out.append({
                "block_id": str(block_id),
                "tag": tag,
                "depth": str(depth),
                "group_id": "",
                "path": path,
                "has_img": "TRUE" if has_img else "FALSE",
                "text": nd.get_text("\n", strip=True)[:45000],
                "links_json": json_dumps_safe(links),
            })
        return out

    # selectolax path
    tree = HTMLParser(html)
    # base
    try:
        base_el = next((b for b in tree.css("base") if b.attributes.get("href")), None)
        if base_el:
            base_url = urljoin(base_url, base_el.attributes.get("href") or "")
    except Exception:
        pass

    root = tree.body or tree
    _remove_unwanted(root)
    _make_absolute(root, base_url)

    # Host-specific: Hokkaido fish faculty listing (per-professor blocks)
    try:
        pu = urlparse(url)
        if (pu.hostname or "") == "www2.fish.hokudai.ac.jp" and "/faculty-member" in (pu.path or ""):
            rows: List[Dict[str, str]] = []
            bid = 0
            # find all dd under dl.faculty-member that contain personal links
            for dl in root.css("dl.faculty-member"):
                # iterate children to preserve dt/dd pairing
                prev = None
                c = dl.child
                while c is not None:
                    if getattr(c, "tag", None) == "dd":
                        # check for personal link under dd
                        has_person_link = False
                        try:
                            for a in c.css("a"):
                                href = a.attributes.get("href") or ""
                                if "/faculty-member/" in href and not href.endswith("/faculty-member/"):
                                    has_person_link = True; break
                        except Exception:
                            pass
                        if has_person_link:
                            bid += 1
                            tag = c.tag.upper()
                            # depth
                            depth = 0
                            p = c
                            while p is not None:
                                depth += 1
                                p = getattr(p, "parent", None)
                            # has_img: from paired dt or within dd
                            has_img = False
                            try:
                                if prev is not None and getattr(prev, "tag", None) == "dt":
                                    for im in prev.css("img"):
                                        has_img = True; break
                            except Exception:
                                has_img = False
                            if not has_img:
                                try:
                                    has_img = any(True for _ in c.css("img"))
                                except Exception:
                                    has_img = False
                            path = _css_path(c)
                            # links
                            links = []
                            try:
                                for a in c.css("a"):
                                    href = a.attributes.get("href") or ""
                                    txt = a.text() or ""
                                    if href:
                                        links.append({"href": href, "text": re.sub(r"\s+", " ", txt).strip()})
                            except Exception:
                                pass
                            try:
                                text_v = _text_with_breaks_sel(c)
                            except Exception:
                                text_v = c.text() or ""
                            rows.append({
                                "block_id": str(bid),
                                "tag": tag,
                                "depth": str(depth),
                                "group_id": "hokudai-fish",
                                "path": path,
                                "has_img": "TRUE" if has_img else "FALSE",
                                "text": text_v[:45000],
                                "links_json": json_dumps_safe(links),
                            })
                    prev = c
                    c = getattr(c, "next", None)
            if rows:
                return rows[:max_blocks]
    except Exception:
        pass

    # Host-specific: Hokkaido AGR faculty listing (per-professor blocks)
    try:
        pu = urlparse(url)
        if (pu.hostname or "") == "www.agr.hokudai.ac.jp" and (pu.path or "").strip("/") == "r/faculty":
            rows: List[Dict[str, str]] = []
            bid = 0
            seen_keys: set[str] = set()
            # Prefer li elements that contain exactly one /r/lab/ link and a role keyword
            def count_lab_links(n: Node) -> int:
                try:
                    return sum(1 for a in n.css('a') if '/r/lab/' in (a.attributes.get('href') or ''))
                except Exception:
                    return 0
            # scan all li nodes (primary)
            for li in root.css('li'):
                try:
                    txt_li = li.text() or ''
                    tl = len(txt_li)
                    if tl < TEXT_MIN or tl > TEXT_MAX:
                        continue
                    lc = count_lab_links(li)
                    has_role = _has_role_text(txt_li)
                    # Accept if it looks like a person row:
                    # - Prefer role keyword, allow up to 5 lab links (some entries list multiple labs)
                    # - Or, if no role keyword, require exactly 1 lab link as a strong hint
                    if has_role:
                        if lc > 5:
                            continue
                    else:
                        if lc != 1:
                            continue
                except Exception:
                    continue
                # use this li as block
                use = li
                # unique key by first lab link when available
                key = None
                try:
                    first_lab = next((a for a in use.css('a') if '/r/lab/' in (a.attributes.get('href') or '')), None)
                    if first_lab is not None:
                        key = first_lab.attributes.get('href') or None
                except Exception:
                    key = None
                path = _css_path(use)
                key = key or path
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                bid += 1
                tag = use.tag.upper() if getattr(use, 'tag', None) else 'DIV'
                # depth
                depth = 0
                q = use
                while q is not None:
                    depth += 1
                    q = getattr(q, 'parent', None)
                # has_img
                has_img = False
                try:
                    has_img = any(True for _ in use.css('img'))
                except Exception:
                    has_img = False
                # links
                links = []
                try:
                    for la in use.css('a'):
                        href = la.attributes.get('href') or ''
                        txt = la.text() or ''
                        if href:
                            links.append({"href": href, "text": re.sub(r"\s+"," ", txt).strip()})
                except Exception:
                    pass
                try:
                    text_v = _text_with_breaks_sel(use)
                except Exception:
                    text_v = use.text() or ''
                rows.append({
                    "block_id": str(bid),
                    "tag": tag,
                    "depth": str(depth),
                    "group_id": "hokudai-agr",
                    "path": path,
                    "has_img": "TRUE" if has_img else "FALSE",
                    "text": text_v[:TEXT_MAX],
                    "links_json": json_dumps_safe(links),
                })
                if len(rows) >= max_blocks:
                    break
            # Secondary: some themes use cards; include minimal .card blocks that contain role keywords
            if len(rows) < max_blocks:
                try:
                    for card in root.css('.card'):
                        try:
                            txt = card.text() or ''
                            if not _has_role_text(txt):
                                continue
                            tl = len(txt)
                            if tl < TEXT_MIN or tl > TEXT_MAX:
                                continue
                        except Exception:
                            continue
                        path = _css_path(card)
                        if path in seen_keys:
                            continue
                        seen_keys.add(path)
                        bid += 1
                        # depth
                        depth = 0; q = card
                        while q is not None:
                            depth += 1
                            q = getattr(q, 'parent', None)
                        # has_img
                        has_img = False
                        try:
                            has_img = any(True for _ in card.css('img'))
                        except Exception:
                            has_img = False
                        # links
                        links=[]
                        try:
                            for la in card.css('a'):
                                href = la.attributes.get('href') or ''
                                txta = la.text() or ''
                                if href:
                                    links.append({"href": href, "text": re.sub(r"\s+"," ", txta).strip()})
                        except Exception:
                            pass
                        try:
                            text_v = _text_with_breaks_sel(card)
                        except Exception:
                            text_v = card.text() or ''
                        rows.append({
                            "block_id": str(bid),
                            "tag": (card.tag.upper() if getattr(card,'tag',None) else 'DIV'),
                            "depth": str(depth),
                            "group_id": "hokudai-agr",
                            "path": path,
                            "has_img": "TRUE" if has_img else "FALSE",
                            "text": text_v[:TEXT_MAX],
                            "links_json": json_dumps_safe(links),
                        })
                        if len(rows) >= max_blocks:
                            break
            if rows:
                return rows[:max_blocks]
    except Exception:
        pass

    # gather blocks
    nodes: List[Node] = []
    try:
        for sel in BLOCK_TAGS:
            nodes.extend(root.css(sel))
    except Exception:
        nodes = []

    # If golden is provided, prioritize merging around golden pieces (and role keywords) into larger containers
    if golden:
        name_g = (golden.get("name") or "").strip()
        theme_g = (golden.get("theme") or "").strip()
        link_g = (golden.get("link") or "").strip()
        def looks_personal_href(href: str) -> bool:
            h = href or ""
            return any(p in h for p in ("/faculty-member/","/faculty/","/people/","/person/","/profile","/researcher","/staff/"))
        def contains_text(n: "Node", s: str) -> bool:
            try:
                return bool(s) and (s in (n.text() or ""))
            except Exception:
                return False
        seeds: List[Node] = []
        # anchors first
        try:
            for a in root.css("a"):
                href = a.attributes.get("href") or ""
                if (link_g and href == link_g) or looks_personal_href(href):
                    seeds.append(a)
        except Exception:
            pass
        # text matches (golden text or role titles)
        try:
            for cand in nodes:
                if (name_g and contains_text(cand, name_g)) or (theme_g and contains_text(cand, theme_g)) or _has_role_text(cand.text() or ""):
                    seeds.append(cand)
        except Exception:
            pass
        # ascend to best container
        picked: List[Node] = []
        seen_paths: set[str] = set()
        def score(n: "Node") -> Tuple[int,int,int,int,int,int,int]:
            t = n.text() or ""
            s_name = 2 if (name_g and (name_g in t)) else 0
            s_theme = 1 if (theme_g and (theme_g in t)) else 0
            s_glink = 0
            try:
                if link_g:
                    for a in n.css("a"):
                        if a.attributes.get("href") == link_g:
                            s_glink = 2; break
            except Exception:
                pass
            s_plink = 0
            try:
                for a in n.css("a"):
                    if looks_personal_href(a.attributes.get("href") or ""):
                        s_plink = 1; break
            except Exception:
                pass
            s_role = 1 if _has_role_text(t) else 0
            s_img = 0
            try:
                s_img = 1 if any(True for _ in n.css("img")) else 0
            except Exception:
                s_img = 0
            tl = len(t)
            s_len = 1 if (40 <= tl <= 5000) else 0
            return (s_glink, s_name, s_theme, s_plink, s_role, s_img, s_len)
        for seed in seeds:
            best = seed
            best_sc = score(seed)
            p = getattr(seed, "parent", None)
            steps = 0
            while p is not None and steps < 8:
                sc = score(p)
                if sc > best_sc:
                    best, best_sc = p, sc
                p = getattr(p, "parent", None)
                steps += 1
            path = _css_path(best)
            if path in seen_paths:
                continue
            seen_paths.add(path)
            picked.append(best)
            if len(picked) >= max_blocks:
                break
        if picked:
            rows: List[Dict[str, str]] = []
            bid = 0
            for n in picked:
                bid += 1
                tag = n.tag.upper()
                depth = 0
                p = n
                while p is not None:
                    depth += 1
                    p = getattr(p, "parent", None)
                has_img = False
                try:
                    has_img = any(True for _ in n.css("img"))
                except Exception:
                    has_img = False
                path = _css_path(n)
                links = []
                try:
                    for a in n.css("a"):
                        href = a.attributes.get("href") or ""
                        txt = a.text() or ""
                        if href:
                            links.append({"href": href, "text": re.sub(r"\s+", " ", txt).strip()})
                except Exception:
                    pass
                try:
                    text_v = _text_with_breaks_sel(n)
                except Exception:
                    text_v = n.text() or ""
                rows.append({
                    "block_id": str(bid),
                    "tag": tag,
                    "depth": str(depth),
                    "group_id": "role" if _has_role_text(text_v) else "golden",
                    "path": path,
                    "has_img": "TRUE" if has_img else "FALSE",
                    "text": text_v[:45000],
                    "links_json": json_dumps_safe(links),
                })
            return rows[:max_blocks]

    # group by parent+signature
    grouped: Dict[str, List[Node]] = {}
    for n in nodes:
        try:
            txt = _text_with_breaks_sel(n)
        except Exception:
            txt = n.text() or ""
        if not txt.strip():
            continue
        parent_tag = n.parent.tag if n.parent else "root"
        sig = f"{parent_tag}|{n.tag}|{_child_signature(n)}"
        grouped.setdefault(sig, []).append(n)

    # flatten by taking representatives from each group until max_blocks
    kept: List[Tuple[str, Node]] = []
    # sort groups by size then by max text length in group
    def group_score(nodes: List[Node]) -> int:
        try:
            return max(len(_text_with_breaks_sel(x)) for x in nodes)
        except Exception:
            return 0
    ordered_groups = sorted(grouped.items(), key=lambda kv: (-len(kv[1]), -group_score(kv[1])))
    for gid, arr in ordered_groups:
        for n in arr:
            kept.append((gid, n))
            if len(kept) >= max_blocks:
                break
        if len(kept) >= max_blocks:
            break

    rows: List[Dict[str, str]] = []
    bid = 0
    for gid, n in kept:
        bid += 1
        tag = n.tag.upper()
        depth = 0
        p = n
        while p is not None:
            depth += 1
            p = p.parent
        has_img = False
        try:
            has_img = any(True for _ in n.css("img"))
        except Exception:
            has_img = False
        path = _css_path(n)
        links = []
        try:
            for a in n.css("a"):
                href = a.attributes.get("href") or ""
                txt = a.text() or ""
                if href:
                    links.append({"href": href, "text": re.sub(r"\s+", " ", txt).strip()})
        except Exception:
            pass
        try:
            text_v = _text_with_breaks_sel(n)
        except Exception:
            text_v = n.text() or ""
        rows.append({
            "block_id": str(bid),
            "tag": tag,
            "depth": str(depth),
            "group_id": gid,
            "path": path,
            "has_img": "TRUE" if has_img else "FALSE",
            "text": text_v[:45000],
            "links_json": json_dumps_safe(links),
        })
    return rows


def json_dumps_safe(obj) -> str:
    import json
    try:
        return json.dumps(obj, ensure_ascii=False)
    except Exception:
        return "[]"

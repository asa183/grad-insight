from __future__ import annotations
import re
from typing import List, Dict, Tuple, Optional
from urllib.parse import urljoin

try:
    from selectolax.parser import HTMLParser, Node
    HAVE_SELECTOLAX = True
except Exception:
    HAVE_SELECTOLAX = False
    from bs4 import BeautifulSoup  # type: ignore


REMOVALS = {"script", "style", "noscript", "svg", "canvas", "nav", "aside", "footer", "header"}
BLOCK_TAGS = {"div", "section", "article", "li", "td"}


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


def blockify_html(url: str, html: str, max_blocks: int = 300) -> List[Dict[str, str]]:
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
        kept: List[Tag] = []
        for sig, nodes in sorted(groups.items(), key=lambda kv: -len(kv[1])):
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

    # gather blocks
    nodes: List[Node] = []
    try:
        for sel in BLOCK_TAGS:
            nodes.extend(root.css(sel))
    except Exception:
        nodes = []

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

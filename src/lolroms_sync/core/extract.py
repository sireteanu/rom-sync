"""HTML link extraction (selectolax / lexbor)."""
from __future__ import annotations

from urllib.parse import unquote, urljoin

from selectolax.lexbor import LexborHTMLParser

from .config import Config
from .constants import SECTIONING_TAGS, STOP_TAGS
from .sync import FileInfo, find_size, parse_date
from .urls import UrlRules
from .util import extension


class LinkExtractor:
    """Pulls listing links and file links from a page, skipping header/nav/sortbar/back."""

    def __init__(self, cfg: Config, rules: UrlRules):
        self.cfg = cfg
        self.rules = rules
        self.ignore_classes = cfg.ignore_classes
        self.nav_tokens = cfg.nav_tokens if cfg.ignore_nav else frozenset()
        self.ignore_nav = cfg.ignore_nav

    def _is_region(self, node) -> bool:
        attrs = node.attributes
        cls, ident = attrs.get("class"), attrs.get("id")
        if cls or ident:
            tokens = set(cls.lower().split()) if cls else set()
            if ident:
                tokens.add(ident.lower())
            if tokens & self.ignore_classes or tokens & self.nav_tokens:
                return True
        if not self.ignore_nav:
            return False
        tag = node.tag
        if tag == "nav":
            return True
        if tag == "header":   # a <header> inside an article/section is content, not site chrome
            p = node.parent
            while p is not None and p.tag not in STOP_TAGS:
                if p.tag in SECTIONING_TAGS:
                    return False
                p = p.parent
            return True
        role = attrs.get("role")
        return bool(role) and role.lower() in ("navigation", "banner")

    def _ignored(self, node, cache: dict) -> bool:
        path, n, result = [], node, False
        while n is not None and n.tag not in STOP_TAGS:
            hit = cache.get(n.mem_id)
            if hit is not None:
                result = hit
                break
            path.append(n)
            n = n.parent
        for n in reversed(path):            # top-most uncached ancestor first
            if not result:
                result = self._is_region(n)
            cache[n.mem_id] = result
        return result

    @staticmethod
    def _text_without(node, skip) -> str:
        """Text of `node`, leaving out the subtree `skip` (the file link / its cell)."""
        parts = []
        child = node.child
        while child is not None:
            if skip is not None and child.mem_id == skip.mem_id:
                pass
            elif child.tag == "-text":
                parts.append(child.text_content or "")
            elif child.tag in ("td", "th", "span", "div", "small", "time", "p", "em", "b", "i", "strong",
                               "font", "code", "dd", "dt", "li", "label"):
                parts.append(LinkExtractor._text_without(child, skip))
            child = child.next
        return " | ".join(p.strip() for p in parts if p and p.strip())

    @staticmethod
    def _row_info(a, ext: str, rows: dict) -> FileInfo:
        """Date and size shown next to a file link, from the row that holds it:
        a table row (name | date | size cells), or a block row such as lolroms'
        <div class="row"><a ...>name</a><span class="file-meta">2025-11-17 • 3.54 KB</span></div>.
        The link's own text is never used - names often contain dates like "(1984-01-04)"."""
        skip, n, row = a, a.parent, None
        for _ in range(6):
            if n is None or n.tag in STOP_TAGS:
                break
            if n.tag in ("td", "th"):
                skip = n                                  # the name cell
            elif n.tag in ("tr", "li", "dt", "dd"):
                row = n
                break
            elif n.tag in ("div", "p", "span", "section", "article"):
                links = len(n.css("a[href]"))
                if links == 1 and n.child is not None and n.child.next is not None:
                    row = n                                # block holding just this link + its details
                    break
                if links > 1:
                    break                                  # a list of links with no per-file block
            n = n.parent
        key = (row.mem_id if row is not None else a.mem_id, skip.mem_id)
        if key not in rows:
            if row is not None:
                text = LinkExtractor._text_without(row, skip)
            else:                                          # details as loose siblings after the link
                bits, sib = [], a.next
                while sib is not None and sib.tag not in ("a", "br", "tr", "li", "div"):
                    bits.append(sib.text_content if sib.tag == "-text" else sib.text(strip=True))
                    sib = sib.next
                text = " | ".join(b.strip() for b in bits if b and b.strip())
            rows[key] = (parse_date(text), find_size(text))
        date, size = rows[key]
        return FileInfo(ext, date, size)

    def extract(self, body: bytes, page_url: str):
        """-> (listing links in scope, {absolute file_url: FileInfo}, number of ignored links)"""
        tree = LexborHTMLParser(body)
        base = page_url
        b = tree.css_first("base[href]")
        if b is not None and b.attributes.get("href"):
            base = urljoin(page_url, b.attributes["href"])
        nofollow = any((m.attributes.get("name") or "").lower() == "robots"
                       and "nofollow" in (m.attributes.get("content") or "").lower()
                       for m in tree.css("meta[name]"))
        cache: dict = {}
        rows: dict = {}
        links: dict = {}
        files: dict = {}
        skipped = 0
        rules = self.rules
        exts = self.cfg.download_exts
        for a in tree.css("a[href]"):
            href = (a.attributes.get("href") or "").strip()
            if not href or href.startswith(("#", "javascript:", "mailto:", "tel:", "data:")):
                continue
            if self._ignored(a, cache):
                skipped += 1
                continue
            href = href.split("#", 1)[0]
            ext = extension(unquote(href))               # fast path: ...Game.7z / ?file=...Game.7z
            if ext in exts:
                files[urljoin(base, href)] = self._row_info(a, ext, rows)
                continue
            absu = urljoin(base, href)
            ext = rules.file_ext(absu)
            if ext or "download" in a.attributes:
                files[absu] = self._row_info(a, ext or "(none)", rows)
                continue
            if nofollow:
                continue
            c = rules.canonical(absu)
            if c and c not in links and rules.in_scope(c) and rules.crawlable(c):
                links[c] = None
        return list(links), files, skipped

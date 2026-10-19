"""URL rules: canonical form, crawl scope, file detection, naming."""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from .config import Config
from .constants import SKIP_EXTENSIONS, TRACKING_PARAMS
from .util import _fold, extension, local_folder, safe_segment


class UrlRules:
    """Canonical URLs, scope, file detection and naming."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        raw = urlsplit(cfg.start_url)
        self.host = (raw.hostname or "").lower()
        start = self.canonical(cfg.start_url)
        if not start:
            raise ValueError(f"Invalid start URL: {cfg.start_url}")
        self.start = start
        s = urlsplit(start)
        path = s.path or "/"
        self.scope_path = path if path.endswith("/") else path.rsplit("/", 1)[0] + "/"
        self.scope_params = {k.lower(): _fold(v) for k, v in parse_qsl(s.query, keep_blank_values=True)}
        self.name_param = next(iter(self.scope_params), None)
        if self.name_param is None and cfg.keep_params:
            self.name_param = sorted(cfg.keep_params)[0]

    def canonical(self, url: str) -> Optional[str]:
        try:
            p = urlsplit(url)
            port = p.port
        except ValueError:
            return None
        if p.scheme not in ("http", "https"):
            return None
        host = (p.hostname or "").lower()
        if not host:
            return None
        default = 443 if p.scheme == "https" else 80
        netloc = host if port in (None, default) else f"{host}:{port}"
        path = re.sub(r"/{2,}", "/", p.path or "/")
        query = ""
        if p.query:
            keep = self.cfg.keep_params
            pairs = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
                     if k.lower() not in TRACKING_PARAMS and (keep is None or k.lower() in keep)]
            query = urlencode(sorted(pairs))
        return urlunsplit((p.scheme, netloc, path, query, ""))

    def same_site(self, url: str) -> bool:
        host = (urlsplit(url).hostname or "").lower()
        if host == self.host:
            return True
        strip = lambda h: h[4:] if h.startswith("www.") else h   # noqa: E731
        if self.cfg.include_subdomains:
            base = strip(self.host)
            return host == base or host.endswith("." + base)
        return strip(host) == strip(self.host)

    def in_scope(self, url: str) -> bool:
        if not self.same_site(url):
            return False
        if not self.cfg.scope:
            return True
        p = urlsplit(url)
        if not (p.path or "/").startswith(self.scope_path):
            return False
        if self.scope_params:
            q = {k.lower(): v for k, v in parse_qsl(p.query, keep_blank_values=True)}
            for key, want in self.scope_params.items():
                have = q.get(key)
                if have is None:
                    return False
                have = _fold(have)
                if not (have == want or have.startswith(want + "/")):
                    return False
        return True

    def file_ext(self, url: str) -> str:
        """Extension if the URL points at a downloadable file (path or any query value)."""
        p = urlsplit(url)
        exts = self.cfg.download_exts
        e = extension(unquote(p.path))
        if e in exts:
            return e
        if p.query:
            for _, v in parse_qsl(p.query, keep_blank_values=True):
                e = extension(v)
                if e in exts:
                    return e
        return ""

    def rel_path(self, url: str) -> str:
        """Where a file is saved, relative to the download folder.
        ?file=Atari%2F5200%2FGame.7z -> Atari/5200/Game.7z ; /roms/nes/Game.zip -> roms/nes/Game.zip"""
        p = urlsplit(url)
        source = None
        for _, v in parse_qsl(p.query, keep_blank_values=True):
            if extension(v) in self.cfg.download_exts:
                source = v
                break
        if source is None:
            source = unquote(p.path)
        parts = [s for s in source.replace("\\", "/").split("/") if s.strip() not in ("", ".", "..")]
        # folders lose the set's date stamp ("Archimedes (20231029-220453)" -> "Archimedes")
        parts = [safe_segment(local_folder(s)) for s in parts[:-1]] + [safe_segment(s) for s in parts[-1:]]
        return "/".join(parts) or "download.bin"

    def crawlable(self, url: str) -> bool:
        return extension(unquote(urlsplit(url).path)) not in SKIP_EXTENSIONS

    def dir_key(self, url: str) -> str:
        """Query-addressed listings (?dir=Atari%2F800) are their own directory; otherwise the
        path directory: http://h/nes/mario.html -> http://h/nes/"""
        p = urlsplit(url)
        if p.query:
            return url
        path = p.path or "/"
        d = path if path.endswith("/") else path.rsplit("/", 1)[0] + "/"
        return urlunsplit((p.scheme, p.netloc, d, "", ""))

    def children(self, page: str, links) -> int:
        """How many of `links` sit below `page` (its subfolders)."""
        if self.name_param:
            base = _fold(self.name(page))
            prefix = base + "/" if base else ""
            return sum(1 for l in links if _fold(self.name(l)).startswith(prefix)
                       and _fold(self.name(l)) != base)
        base = urlsplit(page).path.rsplit("/", 1)[0] + "/"
        return sum(1 for l in links if urlsplit(l).path.startswith(base) and l != page)

    def name(self, url: str) -> str:
        p = urlsplit(url)
        if self.name_param and p.query:
            for k, v in parse_qsl(p.query, keep_blank_values=True):
                if k.lower() == self.name_param:
                    return v
        return unquote(p.path + (("?" + p.query) if p.query else ""))

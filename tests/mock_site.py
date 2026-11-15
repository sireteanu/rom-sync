"""In-process mock of a lolroms-style directory-listing site, for tests.

Structure mirrors the real site (checked in a browser, November 2026):
  /Atari/5200                     listing page: <nav> header bar, .path breadcrumb, .sortbar
                                  (?sort=name), and div rows: name link + date ("2025-11-17")
  /Atari/5200/Game%20%28USA%29.7z file download: Range, If-Range, Last-Modified/ETag, 304
  anything else (an old ?dir= link, a folder the site doesn't have): the root listing, 200

Modes (combine with commas):
  throttle    every 4th listing request answers 429 + Retry-After: 1
  drop        the first two downloads of Big.7z are cut off mid-transfer
  slowfile    Big.7z is streamed slowly (for Ctrl+C / progress tests)
  limitfile   adds Atari/5200/Limited.7z, which always answers with an HTML "limit" page
  nometa      listing rows show no date (like a plain index page)
  sizes       listing rows also show the size, as lolroms did until November 2026
              ("2025-11-17 \u2022 3.54 KB")
  tables      render listings as an HTML table (name | date | size) instead of lolroms' div rows
  bulk        adds Atari/Bulk with 60 slow-ish files (for parallel-download tests)
  brokenfile  adds Atari/5200/Broken.7z, which always answers 502 (the site itself is fine)
  dated       adds Acorn/Archimedes (20231029-220453), a folder named after its set's date like
              many real lolroms folders, and a top-level one, Watara - Supervision (20250625-093232)
  feed        serves /rss.xml like lolroms: the `feed_cap` most recently changed files, newest
              first (modify() and add_file() add entries; it starts with old entries elsewhere)
  fallback    no effect any more: a folder the site doesn't have always answers 200 with the
              root listing, like lolroms since November 2026
Attributes you can change while a test runs:
  max_conc    downloads allowed at once; more get 503 + Retry-After: 1 (None = unlimited)
  daily_limit after this many completed downloads every file answers with an HTML limit page
  down        an HTTP status (e.g. 522): the whole site answers with it, like Cloudflare when the
              server behind it is down (None = up)
  down_after  set `down` to 522 once this many downloads have completed (None = never)
"""
from __future__ import annotations

import calendar
import hashlib
import socket
import threading
import time
import urllib.parse as up
from email.utils import formatdate, parsedate_to_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE_TREE = {
    "Atari": ["2600", "5200", "800", "Lynx"],
    "Atari/800": ["Flux"],
    "Atari/Lynx": [],
    "Acorn": [],
    "Arcade": [],
}
BASE_FILES = {
    "Atari/2600": ["Adventure (USA).7z", "Pitfall! (USA).7z", "Combat: Tank <Plus>.7z"],
    "Atari/5200": ["Galaxian (USA).7z", "Big.7z", "Readme.txt", "Disk Image.iso"],
    "Atari/800/Flux": ["Flux A.zip", "CON.7z"],
    "Acorn": ["Elite.7z"],
    "Arcade": ["Pac.7z"],
}
# root listing; like the real site, Arcade is only linked from the header bar (not the listing)
TOP = ["Acorn", "ACT - Apricot PC Xi", "Atari"]
HEADER_DIRS = ["Arcade"]
EXTS = (".7z", ".zip", ".iso", ".txt", ".rar", ".dat")
BIG = 3_000_000
SMALL = 40_000
BULK = 120_000
DEFAULT_DATE = "2025-11-17"


def _q(s: str) -> str:
    """A site path as lolroms writes it in its links: every segment percent-encoded."""
    return up.quote(s, safe="/")


def _feed_q(s: str) -> str:
    """lolroms' feed writes links less strictly: spaces encoded, ( ) , ' left as they are."""
    return up.quote(s, safe="/(),'!")


def human_size(n: int) -> str:
    """lolroms style: '942 B', '3.54 KB', '2.86 MB'."""
    if n < 1024:
        return f"{n} B"
    for unit in ("KB", "MB", "GB"):
        n /= 1024
        if n < 1024:
            return f"{n:.2f} {unit}"
    return f"{n / 1024:.2f} TB"


class MockSite:
    def __init__(self, modes: str = ""):
        self.modes = {m.strip() for m in modes.split(",") if m.strip()}
        self.tree = {k: list(v) for k, v in BASE_TREE.items()}
        self.files = {k: list(v) for k, v in BASE_FILES.items()}
        if "limitfile" in self.modes:
            self.files["Atari/5200"].append("Limited.7z")
        if "brokenfile" in self.modes:
            self.files["Atari/5200"].append("Broken.7z")
        self.top = list(TOP)
        if "dated" in self.modes:
            self.tree["Acorn"].append("Archimedes (20231029-220453)")
            self.tree["Acorn/Archimedes (20231029-220453)"] = []
            self.files["Acorn/Archimedes (20231029-220453)"] = ["Zarch.7z"]
            self.top.append("Watara - Supervision (20250625-093232)")
            self.tree["Watara - Supervision (20250625-093232)"] = []
            self.files["Watara - Supervision (20250625-093232)"] = ["Alien.7z"]
        if "bulk" in self.modes:
            self.tree["Atari"].append("Bulk")
            self.tree["Atari/Bulk"] = []
            self.files["Atari/Bulk"] = [f"Bulk {i:02d}.7z" for i in range(60)]
        self.versions: dict = {}           # file path -> (version, date, extra bytes)
        self.feed_cap = 100
        # (file path, epoch) newest last; old entries elsewhere so the feed reaches back far
        self.feed = [("Arcade/Pac.7z", time.time() - 30 * 86400)] if "feed" in self.modes else []
        self.max_conc = None
        self.daily_limit = None
        self.down = None
        self.down_after = None
        self.active = 0
        self.peak_active = 0
        self.completed = 0
        self.rejected = 0                  # downloads turned away by max_conc
        self.lm_shift_days = 0             # make Last-Modified disagree with the listing date
        self.slow_all = False              # stream every file slowly (demos)
        self.requests: list = []           # (path, headers dict)
        self.file_paths: list = []         # the same, for file downloads only
        self.heads: list = []              # HEAD requests (asking a file's size)
        self._lock = threading.Lock()
        self._count = 0
        self._drops: dict = {}
        site = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _head(self, code, ctype, length, extra=()):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(length))
                for k, v in extra:
                    self.send_header(k, v)
                self.end_headers()

            def _send(self, code, body: bytes, ctype="text/html; charset=utf-8", extra=()):
                self._head(code, ctype, len(body), extra)
                self.wfile.write(body)

            def do_GET(self):  # noqa: N802
                u = up.urlparse(self.path)
                path = up.unquote(u.path).strip("/")
                is_file = path.lower().endswith(EXTS) and "/" in path or path in site.all_files()
                with site._lock:
                    site.requests.append((self.path, dict(self.headers)))
                    if is_file:
                        site.file_paths.append((self.path, dict(self.headers)))
                    site._count += 1
                    n = site._count
                if site.down_after is not None and site.completed >= site.down_after and is_file:
                    site.down, site.down_after = 522, None
                if site.down:
                    return self._send(site.down, b"<html><head><title>Connection timed out</title></head>"
                                                 b"<body>The web server is not responding.</body></html>")
                if u.path == "/robots.txt":
                    return self._send(404, b"")
                if u.path == "/rss.xml":
                    if "feed" not in site.modes:
                        return self._send(404, b"not found")
                    return self._send(200, site.rss(), "application/rss+xml")
                if is_file:
                    if path not in site.all_files():
                        return self._send(404, b"not found")
                    return self._file(path)
                if "throttle" in site.modes and n % 4 == 0 and path:
                    return self._send(429, b"slow down", extra=[("Retry-After", "1")])
                if path in site.tree or path in site.files or path in HEADER_DIRS:
                    return self._send(200, site.listing(path).encode())
                # lolroms answers anything else (old ?dir= links, folders it doesn't have) with
                # its root listing
                return self._send(200, site.listing("").encode())

            def do_HEAD(self):  # noqa: N802
                """lolroms answers HEAD for files with Content-Length (no Last-Modified / ETag on the
                real site; the mock keeps sending them for the conditional-GET tests)."""
                path = up.unquote(up.urlparse(self.path).path).strip("/")
                with site._lock:
                    site.heads.append(self.path)
                if site.down:
                    return self._head(site.down, "text/html", 0)
                if path not in site.all_files():
                    return self._head(200, "text/html; charset=utf-8", 0)
                if site.daily_limit is not None and site.completed >= site.daily_limit:
                    return self._head(200, "text/html; charset=utf-8", 0)
                self._head(200, "application/octet-stream", len(site.content(path)),
                           [("Accept-Ranges", "bytes")])

            def _limit_page(self):
                self._send(200, b"<html><head><title>Daily download limit reached</title>"
                                b"</head><body>come back tomorrow</body></html>")

            def _file(self, f):
                if f.endswith("Broken.7z"):
                    return self._send(502, b"bad gateway")
                if f.endswith("Limited.7z"):
                    return self._limit_page()
                if site.daily_limit is not None and site.completed >= site.daily_limit:
                    return self._limit_page()
                with site._lock:
                    if site.max_conc is not None and site.active >= site.max_conc:
                        busy = True
                    else:
                        busy = False
                        site.active += 1
                        site.peak_active = max(site.peak_active, site.active)
                if busy:
                    with site._lock:
                        site.rejected += 1
                    return self._send(503, b"too many connections", "text/plain", [("Retry-After", "1")])
                self._counted = False
                try:
                    self._serve(f)
                finally:
                    if not self._counted:
                        with site._lock:
                            site.active -= 1

            def _serve(self, f):
                data = site.content(f)
                lm, etag = site.last_modified(f), site.etag(f)
                validators = [("Last-Modified", lm), ("ETag", etag)]
                ims, inm = self.headers.get("If-Modified-Since"), self.headers.get("If-None-Match")
                if inm is not None or ims is not None:
                    unchanged = (inm == etag) if inm is not None else (
                        parsedate_to_datetime(ims).timestamp() >= parsedate_to_datetime(lm).timestamp())
                    if unchanged:
                        self._head(304, "application/x-7z-compressed", 0, validators)
                        return
                start = 0
                rng = self.headers.get("Range")
                if_range = self.headers.get("If-Range")
                if rng and (if_range is None or if_range in (lm, etag)):
                    start = int(rng.split("=")[1].split("-")[0])
                    if start >= len(data):
                        return self._head(416, "text/plain", 0)
                    self._head(206, "application/x-7z-compressed", len(data) - start,
                               [("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")] + validators)
                else:
                    self._head(200, "application/x-7z-compressed", len(data), validators)
                chunk = data[start:]
                if "drop" in site.modes and f.endswith("Big.7z") and site._drops.get(f, 0) < 2:
                    site._drops[f] = site._drops.get(f, 0) + 1
                    self.wfile.write(chunk[: len(chunk) // 3])
                    self.wfile.flush()
                    self.close_connection = True
                    try:
                        self.connection.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    return
                slow = ("slowfile" in site.modes and f.endswith("Big.7z")) or "/Bulk/" in f or site.slow_all
                step = (65536 if f.endswith("Big.7z") else 20000) if slow else len(chunk) or 1
                try:
                    for i in range(0, len(chunk), step):
                        last = i + step >= len(chunk)
                        if last:                          # count it finished before the final bytes
                            with site._lock:              # go out, so the client can never see a
                                site.active -= 1          # slot as busy that it has already freed
                                site.completed += 1
                                self._counted = True
                        self.wfile.write(chunk[i:i + step])
                        self.wfile.flush()
                        if slow and not last:
                            time.sleep(0.05)
                except OSError:
                    return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    # ---- site content ----
    def _version(self, path):
        return self.versions.get(path, (0, DEFAULT_DATE, 0))

    def content(self, path: str) -> bytes:
        """Deterministic file body with a real archive header (changes when modified)."""
        version, _, extra = self._version(path)
        size = (BIG if path.endswith("Big.7z") else BULK if "/Bulk/" in path else SMALL) + extra
        head = b"PK\x03\x04" if path.endswith(".zip") else b"7z\xbc\xaf\x27\x1c"
        seed = hashlib.sha256(f"{path}#{version}".encode()).digest()
        return head + (seed * (size // 32 + 1))[: size - len(head)]

    def last_modified(self, path: str) -> str:
        version, date, _ = self._version(path)
        epoch = calendar.timegm(time.strptime(date, "%Y-%m-%d")) + 3600 * 9 + version * 60
        epoch += self.lm_shift_days * 86400
        return formatdate(epoch, usegmt=True)

    def etag(self, path: str) -> str:
        version, _, extra = self._version(path)
        return f'"{version}-{extra}"'

    def rename_dir(self, old: str, new: str) -> None:
        """The site renames a folder (and everything below it), e.g. drops its set date."""
        parent = old.rsplit("/", 1)[0] if "/" in old else ""
        oname, nname = old.rsplit("/", 1)[-1], new.rsplit("/", 1)[-1]
        if parent:
            self.tree[parent] = [nname if n == oname else n for n in self.tree[parent]]
        else:
            self.top = [nname if n == oname else n for n in self.top]
        for table in (self.tree, self.files):
            for k in [k for k in table if k == old or k.startswith(old + "/")]:
                table[new + k[len(old):]] = table.pop(k)

    def modify(self, path: str, date: str, grow: int = 0) -> None:
        """Simulate the site replacing a file (new content, date, optionally new size)."""
        version, _, extra = self._version(path)
        self.versions[path] = (version + 1, date, extra + grow)
        self.feed.append((path, time.time()))

    def add_file(self, folder: str, name: str) -> None:
        """A new file appears on the site (and in its list of recent changes)."""
        self.files.setdefault(folder, []).append(name)
        self.feed.append((f"{folder}/{name}", time.time()))

    def rss(self) -> bytes:
        items = sorted(self.feed, key=lambda e: e[1], reverse=True)[: self.feed_cap]
        out = "".join(
            f"<item><title>{f.rsplit('/', 1)[-1].rsplit('.', 1)[0]}</title><link>https://lolroms.com/{_feed_q(f)}</link>"
            f"<pubDate>{formatdate(t, usegmt=True)}</pubDate><description>{f}</description></item>"
            for f, t in items).replace("&", "&amp;")
        return (f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>LoLROMs Files'
                f'</title><link>https://lolroms.com/</link><description>{self.feed_cap} most recently modified '
                f'files</description><lastBuildDate>{formatdate(time.time(), usegmt=True)}</lastBuildDate>'
                f'{out}</channel></rss>').encode()

    def all_files(self) -> set:
        return {f"{d}/{f}" if d else f for d, fs in self.files.items() for f in fs}

    def listing(self, d: str) -> str:
        """lolroms' real markup (checked against the live site, November 2026):
        <div class="path"><a href="/">Home</a> / <a href="/Nintendo">Nintendo</a> / ...</div>
        <div class="sortbar"><a href="?sort=name" class="active">Name</a>...</div>
        <div class="list"><div class="row"><a href="/Nintendo/Virtual%20Boy/T-En">T-En</a></div>
        <div class="row"><a href="/Nintendo/Virtual%20Boy/3-D%20Tetris%20%28USA%29.7z">3-D Tetris
        (USA)</a><span class="file-meta">2025-11-24</span></div>...</div>
        The root page has no breadcrumb and no sort bar. Mode "tables" renders the rows as an HTML
        table instead; mode "sizes" adds the size to the date like lolroms did until November 2026."""
        meta = "nometa" not in self.modes
        tables = "tables" in self.modes
        rows = ""
        for k in (self.top if d == "" else self.tree.get(d, [])):
            href = "/" + _q(d + "/" + k if d else k)
            rows += (f'<tr><td><a href="{href}">{k}</a></td><td></td><td></td></tr>' if tables
                     else f'<div class="row"><a href="{href}">{k}</a></div>')
        for f in self.files.get(d, []):
            path = f"{d}/{f}" if d else f
            date, size = self._version(path)[1], human_size(len(self.content(path)))
            text = date + (f" \u2022 {size}" if "sizes" in self.modes else "")
            name = f.rsplit(".", 1)[0]
            if tables:
                cells = (f"<td>{date}</td><td>{size if 'sizes' in self.modes else ''}</td>") if meta else ""
                rows += f'<tr><td><a href="/{_q(path)}">{name}</a></td>{cells}</tr>'
            else:
                info = f'<span class="file-meta">{text}</span>' if meta else ""
                rows += f'<div class="row"><a href="/{_q(path)}">{name}</a>{info}</div>'
        body = (f"<table><tr><th>Name</th><th>Date</th><th>Size</th></tr>{rows}</table>" if tables
                else f'<div class="list">{rows}</div>')
        crumbs = sortbar = ""
        if d:
            parts = d.split("/")
            crumbs = '<div class="path"><a href="/">Home</a>' + "".join(
                f' / <a href="/{_q("/".join(parts[:i + 1]))}">{parts[i]}</a>' for i in range(len(parts))) + "</div>"
            sortbar = ('<div class="sortbar"><a href="?sort=name" class="active">Name</a>'
                       '<a href="?sort=date">Date</a><a href="?sort=size">Size</a></div>')
        # the header bar links some sections with their names unencoded (/Pokéhacks), and has a
        # file (the site's bulk download script) and the feed
        head = "".join(f'<a href="/{t}" class="nav-item"><img src="/i.png"><span>{t}</span></a>'
                       for t in HEADER_DIRS)
        return f"""<html lang="en"><head><meta charset="utf-8"><title>LoLSuite | ROMs</title></head>
<body>
<nav>
<a href="/" class="nav-item"><img src="/home.png"><span>Home</span></a>
{head}
<a href="/bulk_download.py" download="" class="nav-item"><img src="/py.png"><span>Bulk</span></a>
<a href="/rss.xml" class="nav-item"><img src="/rss.png"><span>RSS</span></a>
<form class="search-form" method="get"><input type="search" name="q" value=""><button type="submit">Search</button></form>
</nav>
<main>
<div class="container">
{crumbs}{sortbar}{body}</div>
</main>
</body></html>"""

    # ---- lifecycle ----
    def start(self) -> "MockSite":
        self._thread.start()
        return self

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    # ---- helpers for assertions ----
    def paths(self):
        return [p for p, _ in self.requests]

    def listing_reads(self):
        """Folders whose listing was requested ("Atari/2600"; the root page is left out)."""
        files = {q for q, _ in self.file_paths}
        return [up.unquote(up.urlparse(q).path).strip("/") for q, _ in self.requests
                if q not in files and up.urlparse(q).path not in ("/", "/rss.xml", "/robots.txt")]

    def file_requests(self):
        """[(path, Range header)] for every file request."""
        return [(p, h.get("Range")) for p, h in self.file_paths]

    def file_headers(self):
        return list(self.file_paths)

    def reset_log(self):
        self.requests.clear()
        self.file_paths.clear()


def content(path: str) -> bytes:
    """Original (unmodified) content of a file - kept for older tests."""
    return MockSite().content(path)

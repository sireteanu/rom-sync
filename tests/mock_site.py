"""In-process mock of a lolroms-style directory-listing site, for tests.

Structure mirrors the real site:
  /?dir=Atari%2F5200              listing page: sidebar of top-level dirs, nav, .sortbar, .back,
                                  and a table of  name | date (YYYY-MM-DD) | size ("3.54 KB")
  /?file=Atari%2F5200%2FGame.7z   file download: Range, If-Range, Last-Modified/ETag, 304

Modes (combine with commas):
  throttle    every 4th listing request answers 429 + Retry-After: 1
  drop        the first two downloads of Big.7z are cut off mid-transfer
  slowfile    Big.7z is streamed slowly (for Ctrl+C / progress tests)
  limitfile   adds Atari/5200/Limited.7z, which always answers with an HTML "limit" page
  nometa      listing rows show no date / size (like a plain index page)
  tables      render listings as an HTML table (name | date | size) instead of lolroms' div rows
  bulk        adds Atari/Bulk with 60 slow-ish files (for parallel-download tests)
  brokenfile  adds Atari/5200/Broken.7z, which always answers 502 (the site itself is fine)
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
BIG = 3_000_000
SMALL = 40_000
BULK = 120_000
DEFAULT_DATE = "2025-11-17"


def _q(s: str) -> str:
    return up.quote_plus(s)


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
        if "bulk" in self.modes:
            self.tree["Atari"].append("Bulk")
            self.tree["Atari/Bulk"] = []
            self.files["Atari/Bulk"] = [f"Bulk {i:02d}.7z" for i in range(60)]
        self.versions: dict = {}           # file path -> (version, date, extra bytes)
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
                qs = up.parse_qs(u.query)
                with site._lock:
                    site.requests.append((self.path, dict(self.headers)))
                    site._count += 1
                    n = site._count
                if site.down_after is not None and site.completed >= site.down_after and "file" in qs:
                    site.down, site.down_after = 522, None
                if site.down:
                    return self._send(site.down, b"<html><head><title>Connection timed out</title></head>"
                                                 b"<body>The web server is not responding.</body></html>")
                if u.path == "/robots.txt":
                    return self._send(404, b"")
                if "file" in qs:
                    return self._file(qs["file"][0])
                if "dir" in qs:
                    if "throttle" in site.modes and n % 4 == 0:
                        return self._send(429, b"slow down", extra=[("Retry-After", "1")])
                    d = qs["dir"][0]
                    if d in site.tree or d in site.files:
                        return self._send(200, site.listing(d).encode())
                    return self._send(404, b"not found")
                if u.path == "/":
                    return self._send(200, site.listing("").encode())
                return self._send(404, b"")

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

    def modify(self, path: str, date: str, grow: int = 0) -> None:
        """Simulate the site replacing a file (new content, date, optionally new size)."""
        version, _, extra = self._version(path)
        self.versions[path] = (version + 1, date, extra + grow)

    def listing(self, d: str) -> str:
        """lolroms' real markup (checked against the live site, Oct 2026):
        <div class="list"><div class="row"><a href="?file=...">📄 name</a>
        <span class="file-meta">2025-11-17 • 3.54 KB</span></div>...</div>
        Mode "tables" renders the same rows as an HTML table instead."""
        parent = d.rsplit("/", 1)[0] if "/" in d else ""
        meta = "nometa" not in self.modes
        tables = "tables" in self.modes
        rows = ""
        for k in (TOP if d == "" else self.tree.get(d, [])):
            href = f"?dir={_q(d + '/' + k if d else k)}"
            rows += (f'<tr><td><a href="/{href}">{k}</a></td><td></td><td></td></tr>' if tables
                     else f'<div class="row"><a href="{href}">\U0001F4C1 {k}</a></div>')
        for f in self.files.get(d, []):
            path = f"{d}/{f}"
            date, size = self._version(path)[1], human_size(len(self.content(path)))
            name = f.rsplit(".", 1)[0]
            if tables:
                cells = f"<td>{date}</td><td>{size}</td>" if meta else ""
                rows += f'<tr><td><a href="/?file={_q(path)}">{name}</a></td>{cells}</tr>'
            else:
                info = f'<span class="file-meta">{date} \u2022 {size}</span>' if meta else ""
                rows += f'<div class="row"><a href="?file={_q(path)}">\U0001F4C4 {name}</a>{info}</div>'
        body = (f"<table><tr><th>Name</th><th>Date</th><th>Size</th></tr>{rows}</table>" if tables
                else f'<div class="list">{rows}</div>')
        head = "".join(f'<a href="/?dir={_q(t)}"><img src="/i.webp">{t}</a>' for t in HEADER_DIRS)
        return f"""<!doctype html><html><head><title>LoLROMs</title></head><body>
<header class="topbar"><a href="/">LoLROMs</a>{head}
<a href="https://discord.gg/x">Discord</a></header>
<nav><a href="/?dir=Atari%2F2600">nav link</a></nav>
<div class="container">
<div class="back"><a href="?dir={_q(parent)}">\u2b05 Back</a></div><div class="sortbar"><a class="active" href="?dir={_q(d)}&sort=name">Name</a><a href="?dir={_q(d)}&sort=date">Date</a><a href="?dir={_q(d)}&sort=size">Size</a></div>{body}
</div><a href="/?search=x">search</a></body></html>"""

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

    def file_requests(self):
        """[(path, Range header)] for every file request."""
        return [(p, h.get("Range")) for p, h in self.requests if "file=" in p]

    def file_headers(self):
        return [(p, h) for p, h in self.requests if "file=" in p]

    def reset_log(self):
        self.requests.clear()


def content(path: str) -> bytes:
    """Original (unmodified) content of a file - kept for older tests."""
    return MockSite().content(path)

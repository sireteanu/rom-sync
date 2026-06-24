"""File types, page-structure rules and protocol constants."""
from __future__ import annotations

import re

from . import __version__

ARCHIVE_EXTENSIONS = frozenset({
    ".zip", ".7z", ".rar", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".zst", ".cab", ".lzh",
    ".lha", ".arj", ".ace",
})

DOWNLOAD_EXTENSIONS = ARCHIVE_EXTENSIONS | frozenset({
    # disc / disk images
    ".iso", ".img", ".bin", ".cue", ".chd", ".cso", ".mdf", ".mds", ".nrg", ".ccd", ".gdi",
    ".cdi", ".vhd", ".vmdk", ".adf", ".dsk", ".d64", ".tap", ".tzx", ".st", ".msa", ".atr", ".xex",
    # installers / packages
    ".exe", ".msi", ".dmg", ".pkg", ".apk", ".deb", ".rpm", ".appimage",
    # console ROM / image formats
    ".nes", ".fds", ".sfc", ".smc", ".gba", ".gbc", ".gb", ".nds", ".3ds", ".cia", ".n64",
    ".z64", ".v64", ".nsp", ".xci", ".wbfs", ".rvz", ".gcm", ".wad", ".pbp", ".gen", ".md",
    ".smd", ".sms", ".gg", ".32x", ".pce", ".a26", ".a52", ".a78", ".lnx", ".j64", ".jag",
    ".ws", ".wsc", ".ngp", ".ngc", ".vb", ".col", ".int", ".min", ".sv", ".vec",
    # audio collections / dats
    ".flac", ".dat",
})

# First bytes of a valid file, used to reject error pages saved under an archive name.
MAGIC = {
    ".7z": (b"7z\xbc\xaf\x27\x1c",),
    ".zip": (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"),
    ".rar": (b"Rar!\x1a\x07",),
    ".gz": (b"\x1f\x8b",), ".tgz": (b"\x1f\x8b",),
    ".bz2": (b"BZh",), ".xz": (b"\xfd7zXZ\x00",), ".zst": (b"\x28\xb5\x2f\xfd",),
}

# Never crawled as pages.
SKIP_EXTENSIONS = frozenset({
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".bmp", ".avif",
    ".css", ".js", ".mjs", ".json", ".xml", ".txt", ".rss", ".atom", ".map",
    ".mp3", ".mp4", ".avi", ".mkv", ".mov", ".wav", ".ogg", ".webm", ".m4a",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".pdf",
})

TRACKING_PARAMS = frozenset({"utm_source", "utm_medium", "utm_campaign", "utm_term",
                             "utm_content", "fbclid", "gclid", "ref", "sessionid", "phpsessid"})

# class / id names (exact token match) that mark site header / navigation blocks
NAV_TOKENS = frozenset({"nav", "navbar", "navigation", "navbar-nav", "main-nav", "top-nav",
                        "topnav", "site-nav", "menu", "main-menu", "site-header", "header",
                        "page-header", "masthead", "topbar", "top-bar"})
# class / id names whose contents are always ignored
DEFAULT_IGNORE_CLASSES = frozenset({"sortbar", "back"})
SECTIONING_TAGS = frozenset({"article", "section", "main", "aside"})
STOP_TAGS = frozenset({"body", "html", "-document"})

RETRY_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524})
THROTTLE_STATUSES = frozenset({429, 503})
MAX_BODY = 25_000_000
CHUNK = 256 * 1024
DEFAULT_UA = f"Mozilla/5.0 (compatible; DirCrawler/{__version__})"

_EXT_RE = re.compile(r"(\.[a-z0-9]{1,10})(?:\.\d{1,3})?$")      # ".7z", also ".7z.001"
_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = frozenset({"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
                       *(f"lpt{i}" for i in range(1, 10))})

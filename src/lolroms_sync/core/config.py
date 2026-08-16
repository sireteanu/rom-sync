"""Settings for one scan or sync (built by the scanner and the engine)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .constants import (DEFAULT_IGNORE_CLASSES, DEFAULT_UA,
                        DOWNLOAD_EXTENSIONS, NAV_TOKENS)


@dataclass
class Config:
    start_url: str
    workers: int = 8
    delay: float = 0.1
    timeout: float = 15.0
    retries: int = 2
    slow_secs: float = 4.0
    adaptive: bool = True
    http2: bool = True
    user_agent: str = DEFAULT_UA
    scope: bool = True
    keep_params: Optional[frozenset] = None      # None -> keep every query parameter
    include_subdomains: bool = False
    ignore_robots: bool = False
    ignore_nav: bool = True
    nav_tokens: frozenset = NAV_TOKENS
    ignore_classes: frozenset = DEFAULT_IGNORE_CLASSES
    download_exts: frozenset = DOWNLOAD_EXTENSIONS
    # downloading
    download_dir: Optional[str] = None
    check_remote: bool = False        # ask the server about every file already in the folder
    min_free: int = 2 * 1024 ** 3
    file_mode: Optional[int] = None       # e.g. 0o777: permissions for saved files and their folders
    overwrite: bool = False
    verify: bool = True

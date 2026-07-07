"""Run configuration."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .constants import (ARCHIVE_EXTENSIONS, DEFAULT_IGNORE_CLASSES, DEFAULT_UA,
                        DOWNLOAD_EXTENSIONS, NAV_TOKENS)


@dataclass
class Config:
    start_url: str
    output: str = "directories.csv"
    state_path: str = ""
    resume: bool = False
    fresh: bool = False
    workers: int = 8
    delay: float = 0.1
    timeout: float = 15.0
    retries: int = 2
    slow_secs: float = 4.0
    adaptive: bool = True
    http2: bool = True
    user_agent: str = DEFAULT_UA
    max_pages: int = 0
    max_depth: Optional[int] = None
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
    list_files: bool = False
    dl_types: Optional[frozenset] = ARCHIVE_EXTENSIONS   # None -> every recognised file type
    dl_auto: bool = True              # find and use the most parallel downloads the server allows
    dl_workers: int = 2               # fixed count when dl_auto is False; start value otherwise
    dl_max: int = 32                  # hard upper bound for auto mode
    dl_ramp_secs: float = 3.0
    dl_probe_secs: float = 120.0
    dl_confirm_secs: float = 30.0
    check_remote: bool = False        # ask the server about every file already in the folder
    max_rate: int = 0
    min_free: int = 2 * 1024 ** 3
    file_mode: Optional[int] = None       # e.g. 0o777: permissions for saved files and their folders
    overwrite: bool = False
    verify: bool = True

"""Command-line interface: python -m dir_crawler / dir-crawler."""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from typing import Optional
from urllib.parse import parse_qsl, urlsplit

from rich.console import Console
from rich.live import Live
from rich.logging import RichHandler

from . import __version__
from .config import Config
from .constants import ARCHIVE_EXTENSIONS, DEFAULT_IGNORE_CLASSES, DEFAULT_UA, DOWNLOAD_EXTENSIONS, NAV_TOKENS
from .crawler import HAVE_H2, Crawler
from .state import State
from .urls import UrlRules
from .util import fmt_bytes, parse_size

log = logging.getLogger("dircrawl")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="dir-crawler",
        description="List - and optionally download - the archive files of a directory-listing site.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='examples:\n'
               '  python dir-crawler.py "https://lolroms.com/?dir=Atari"\n'
               '  python dir-crawler.py "https://lolroms.com/?dir=Atari" --download D:\\Roms\n'
               '  python dir-crawler.py "https://lolroms.com/?dir=Atari" --download D:\\Roms --resume\n'
               '  python dir-crawler.py "https://lolroms.com/?dir=Atari" --download D:\\Roms --watch 6h')
    ap.add_argument("url", help="start URL - only this listing and its subfolders are crawled")
    ap.add_argument("-o", "--output", default="directories.csv",
                    help="CSV output; a .txt list is written beside it (default: directories.csv)")
    g = ap.add_argument_group("downloading")
    g.add_argument("--download", metavar="FOLDER",
                   help="download the archive files into FOLDER, keeping the site's folder structure")
    g.add_argument("--list-files", action="store_true",
                   help="list every archive file in <output>_files.csv without downloading")
    g.add_argument("--types", default="archives",
                   help="which files: 'archives' (zip, 7z, rar, tar, gz ... - default), 'all' "
                        "(every recognised file type incl. iso, chd, roms), or a list like .7z,.zip")
    g.add_argument("--dl-workers", default="auto",
                   help="files downloaded at once: 'auto' (default) finds and keeps using the most the "
                        "server allows, re-checking periodically; or a fixed number")
    g.add_argument("--dl-max", type=int, default=32,
                   help="upper bound for automatic parallel downloads (default 32)")
    g.add_argument("--check-remote", action="store_true",
                   help="ask the server about every file already in the folder (exact but one request "
                        "per file); normally the listing's date and size are enough")
    g.add_argument("--watch", type=parse_duration, default=None, metavar="INTERVAL",
                   help="keep the folder in sync: repeat the whole run every INTERVAL (e.g. 30m, 6h, 1d)")
    for name in ("--dl-ramp-secs", "--dl-probe-secs", "--dl-confirm-secs"):   # tuning / tests
        g.add_argument(name, type=float, default=None, help=argparse.SUPPRESS)
    g.add_argument("--max-rate", type=parse_size, default=0,
                   help="total download speed limit, e.g. 5M or 800K (default: unlimited)")
    g.add_argument("--min-free", type=parse_size, default=parse_size("2G"),
                   help="pause downloads if free disk space would drop below this (default 2G)")
    g.add_argument("--overwrite", action="store_true",
                   help="re-download every file, even ones that are up to date")
    g.add_argument("--no-verify", action="store_true",
                   help="don't check archive headers after downloading")
    g = ap.add_argument_group("resume")
    g.add_argument("--resume", action="store_true", help="continue an interrupted crawl / download")
    g.add_argument("--fresh", action="store_true", help="discard an unfinished crawl and start over")
    g.add_argument("--state", default=None, help="progress file (default: <output>.state.db)")
    g = ap.add_argument_group("speed")
    g.add_argument("--workers", type=int, default=8, help="max parallel page requests (default 8)")
    g.add_argument("--delay", type=float, default=0.1,
                   help="min seconds between page requests (default 0.1)")
    g.add_argument("--timeout", type=float, default=15, help="per-request timeout, seconds (default 15)")
    g.add_argument("--retries", type=int, default=2, help="retries per failed page (default 2)")
    g.add_argument("--slow", type=float, default=4.0,
                   help="responses slower than this (s) make the crawler ease off (default 4)")
    g.add_argument("--fixed-speed", action="store_true",
                   help="disable adaptive speed control (keep --workers/--delay fixed)")
    g.add_argument("--http1", action="store_true", help="disable HTTP/2")
    g = ap.add_argument_group("what to crawl")
    g.add_argument("--keep-params", default=None,
                   help="query parameters that identify a listing (default: those in the start "
                        "URL, e.g. 'dir'); 'all' keeps every parameter")
    g.add_argument("--no-scope", action="store_true",
                   help="follow links anywhere on the site, not just under the start URL")
    g.add_argument("--max-pages", type=int, default=0, help="stop after N pages (default: no limit)")
    g.add_argument("--max-depth", type=int, default=None, help="max folder depth below the start URL")
    g.add_argument("--download-ext", default="",
                   help="extra file extensions to recognise, comma-separated (e.g. .pdf,.mp4)")
    g.add_argument("--ignore-class", default="",
                   help="extra class/id names to ignore (always ignored: sortbar, back)")
    g.add_argument("--nav-selector", default="", help="extra class/id names to treat as navigation")
    g.add_argument("--include-nav", action="store_true", help="also follow header/nav links")
    g.add_argument("--include-subdomains", action="store_true")
    g.add_argument("--ignore-robots", action="store_true",
                   help="ignore robots.txt (only on sites you run)")
    g.add_argument("--user-agent", default=DEFAULT_UA)
    g = ap.add_argument_group("logging")
    g.add_argument("-v", "--verbose", action="store_true", help="debug detail (skipped links etc.)")
    g.add_argument("-q", "--quiet", action="store_true", help="only warnings and the summary")
    g.add_argument("--plain", action="store_true", help="plain log lines, no live status panel")
    g.add_argument("--log-file", default=None, help="also write the log to this file")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return ap


def parse_duration(text: str) -> float:
    import re
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([smhd]?)\s*", (text or "").lower())
    if not m:
        raise argparse.ArgumentTypeError(f"not a duration: {text!r} (examples: 30m, 6h, 1d)")
    return float(m.group(1)) * {"": 60, "s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2)]


def setup_logging(console: Console, rich_ui: bool, level: int, log_file: Optional[str]) -> None:
    log.setLevel(logging.DEBUG)
    log.propagate = False
    for old in list(log.handlers):          # main() may run more than once (tests)
        log.removeHandler(old)
        old.close()
    if rich_ui:
        h = RichHandler(console=console, show_path=False, markup=False, rich_tracebacks=False,
                        log_time_format="%H:%M:%S", omit_repeated_times=False)
    else:
        class Fmt(logging.Formatter):
            def format(self, rec):
                s = super().format(rec)
                return s if rec.levelno < logging.WARNING else s.replace(" ", f" {rec.levelname}: ", 1)
        h = logging.StreamHandler(sys.stderr)
        h.setFormatter(Fmt("%(asctime)s %(message)s", "%H:%M:%S"))
    h.setLevel(level)
    log.addHandler(h)
    if log_file:
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S"))
        fh.setLevel(logging.DEBUG if level == logging.DEBUG else logging.INFO)
        log.addHandler(fh)


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    split = lambda s: frozenset(t.strip().lower().lstrip(".#") for t in (s or "").split(",") if t.strip())  # noqa: E731

    url = args.url if "://" in args.url else "https://" + args.url
    start_q = urlsplit(url).query
    if args.keep_params is None:
        keep = frozenset(k.lower() for k, _ in parse_qsl(start_q, keep_blank_values=True)) or None
    elif args.keep_params.strip().lower() == "all":
        keep = None
    else:
        keep = split(args.keep_params)

    extra_ext = frozenset("." + e for e in split(args.download_ext))
    recognised = DOWNLOAD_EXTENSIONS | extra_ext
    t = (args.types or "archives").strip().lower()
    if t == "archives":
        dl_types = ARCHIVE_EXTENSIONS | extra_ext
    elif t == "all":
        dl_types = None
    else:
        dl_types = frozenset("." + e for e in split(t))
        recognised = recognised | dl_types

    stem = os.path.splitext(args.output)[0]
    state_path = args.state or stem + ".state.db"

    console = Console(stderr=True)
    rich_ui = console.is_terminal and not args.plain
    level = logging.WARNING if args.quiet else (logging.DEBUG if args.verbose else logging.INFO)
    setup_logging(console, rich_ui, level, args.log_file)

    cfg = Config(
        start_url=url, output=args.output, state_path=state_path, resume=args.resume,
        fresh=args.fresh, workers=max(1, args.workers), delay=max(0.0, args.delay),
        timeout=args.timeout, retries=max(0, args.retries), slow_secs=args.slow,
        adaptive=not args.fixed_speed, http2=not args.http1, user_agent=args.user_agent,
        max_pages=args.max_pages, max_depth=args.max_depth, scope=not args.no_scope,
        keep_params=keep, include_subdomains=args.include_subdomains,
        ignore_robots=args.ignore_robots, ignore_nav=not args.include_nav,
        nav_tokens=NAV_TOKENS | split(args.nav_selector),
        ignore_classes=DEFAULT_IGNORE_CLASSES | split(args.ignore_class),
        download_exts=recognised,
        download_dir=args.download, list_files=args.list_files or bool(args.download),
        dl_types=dl_types, max_rate=args.max_rate, check_remote=args.check_remote,
        min_free=args.min_free, overwrite=args.overwrite, verify=not args.no_verify)
    w = str(args.dl_workers).strip().lower()
    if w == "auto":
        cfg.dl_auto, cfg.dl_workers = True, 2
    elif w.isdigit() and int(w) > 0:
        cfg.dl_auto, cfg.dl_workers = False, int(w)
    else:
        log.error("--dl-workers must be 'auto' or a number")
        return 2
    cfg.dl_max = max(1, args.dl_max)
    for name in ("ramp", "probe", "confirm"):
        v = getattr(args, f"dl_{name}_secs")
        if v is not None:
            setattr(cfg, f"dl_{name}_secs", v)

    if cfg.download_dir:
        try:
            os.makedirs(cfg.download_dir, exist_ok=True)
        except OSError as e:
            log.error("Can't create download folder %s: %s", cfg.download_dir, e)
            return 2

    # protect an unfinished crawl from being overwritten by accident
    prev = State.peek(state_path)
    if args.resume:
        if prev is None:
            log.error("No saved progress found at %s - run without --resume to start.", state_path)
            return 2
        if prev[0]:
            try:
                same = UrlRules(cfg).start == prev[0]
            except ValueError:
                same = False
            if not same:
                log.error("Saved progress is for %s, not %s. Use the same URL, or --fresh / "
                          "a different -o to start a new crawl.", prev[0], url)
                return 2
    elif prev and (prev[2] or prev[3]) and not args.fresh:
        log.error("Found an unfinished crawl of %s (%d pages done, %d pages and %d downloads "
                  "still queued) in %s.\n  Continue it:   add --resume\n  Start over:    add --fresh",
                  prev[0], prev[1], prev[2], prev[3], state_path)
        return 2

    cycle = 0
    while True:
        rc = run_once(cfg, args, console, rich_ui, keep, dl_types, url)
        if rc is None or args.watch is None:
            return 0 if rc in (None, 0) else rc
        cycle += 1
        cfg.resume = False                       # every later cycle re-reads the whole site
        nxt = time.strftime("%H:%M", time.localtime(time.time() + args.watch))
        log.info("Sync %d finished. Next check at %s (every %s). Press Ctrl+C to stop.",
                 cycle, nxt, fmt_duration(args.watch))
        try:
            time.sleep(args.watch)
        except KeyboardInterrupt:
            log.info("Stopped.")
            return 0


def fmt_duration(secs: float) -> str:
    for unit, n in (("d", 86400), ("h", 3600), ("m", 60)):
        if secs >= n and secs % n == 0:
            return f"{int(secs // n)}{unit}"
    return f"{int(secs)}s"


def run_once(cfg, args, console, rich_ui, keep, dl_types, url):
    """One crawl/sync. -> 0 when it ran to the end, None if interrupted, or an exit code."""
    try:
        crawler = Crawler(cfg, console)
    except ValueError as e:
        log.error("%s", e)
        return 2
    crawler.interactive = rich_ui and not args.quiet

    r = crawler.rules
    scope = ("whole site" if not cfg.scope else
             ", ".join(f"{k}={r.name(r.start) if k == r.name_param else v} (and subfolders)"
                       for k, v in r.scope_params.items()) or f"path {r.scope_path}")
    log.info("Crawling %s", r.start)
    log.info("Scope: %s | listing params kept: %s | workers %d, delay %.2fs, HTTP/%s",
             scope, ", ".join(sorted(keep)) if keep else "all", cfg.workers, cfg.delay,
             "2 when available" if (cfg.http2 and HAVE_H2) else "1.1")
    if cfg.download_dir:
        log.info("Syncing %s into %s - only new, incomplete or changed files are downloaded%s",
                 "all recognised files" if dl_types is None else ", ".join(sorted(dl_types)),
                 os.path.abspath(cfg.download_dir),
                 f", max {fmt_bytes(cfg.max_rate)}/s" if cfg.max_rate else "")

    interrupted = False
    try:
        if crawler.interactive:
            with Live(crawler.status_panel(), console=console, auto_refresh=False,
                      transient=True) as live:
                crawler.live = live
                asyncio.run(crawler.run())
        else:
            asyncio.run(crawler.run())
    except KeyboardInterrupt:
        interrupted = True
        log.warning("Interrupted - progress saved (partial downloads are kept as .part files). "
                    "Run the same command with --resume to continue.")
    finally:
        crawler.live = None
        crawler.finish()
    return None if interrupted else 0


if __name__ == "__main__":
    sys.exit(main())

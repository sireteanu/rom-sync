# Rom Sync

Rom Sync keeps a local copy of the [lolroms.com](https://lolroms.com/) folders you choose, and
keeps it up to date. It reads the whole site into a catalog (without downloading files), shows
every folder as a tree with tick boxes, and each sync downloads only what is new, incomplete or
changed. It can also present the synced folders to [RomM](https://github.com/rommapp/romm) under
RomM's platform names without moving a single file.

It runs as a web page: in Docker (made for Unraid) or on Windows.

- **New here?** Read the [user guide](docs/user-guide.md).
- **What changed?** See the [changelog](CHANGELOG.md) and the
  [releases](https://github.com/sireteanu/rom-sync/releases). The running version is shown next to
  the title in the web page.
- **Working on the code (people or AI assistants)?** This README is the full reference: every
  behaviour, setting, file and endpoint is described below. Coding rules and invariants are in
  [CLAUDE.md](CLAUDE.md).

---

## Contents

1. [Features](#features)
2. [Install](#install)
3. [How it works](#how-it-works)
4. [RomM integration](#romm-integration)
5. [Settings reference](#settings-reference)
6. [Command-line options and environment variables](#command-line-options-and-environment-variables)
7. [Files on disk](#files-on-disk)
8. [HTTP API](#http-api)
9. [Code map](#code-map)
10. [Development, tests and releases](#development-tests-and-releases)
11. [The `dir-crawler` command-line tool](#the-dir-crawler-command-line-tool)
12. [Facts about lolroms.com this relies on](#facts-about-lolromscom-this-relies-on)

---

## Features

| Area | What it does |
|---|---|
| Catalog | **Scan site** reads every listing page of lolroms.com (about 330 folders, 117,000+ files) into a local database. File links are never requested while scanning. Sections linked only from the site's header bar (Arcade, Pokéhacks, Audio [FLAC], fixdats) are included. |
| Folder tree | Every folder with file count, total size, newest file date and how much is synced. Columns sort by clicking their header (ascending → descending → name order). Search box finds folders by name. |
| Selection | Tri-state tick boxes. Ticking a folder includes everything below it, including folders the site adds later. A folder with files of its own *and* subfolders shows a **Files in this folder** row, so its own files can be ticked without its subfolders. Ticks are saved immediately. |
| Sync | Downloads only what's missing, half-finished (`.part`, resumed) or changed on the site (date or size in the listing differs). Unchanged files cost no network request. |
| Dates | Each saved file gets the date the website lists for it (12:00 local time). After a sync, each folder gets the date of its newest file. |
| Permissions | Downloaded files and new folders get mode **777** by default (setting). |
| Speed | Parallel downloads ramp up until the server pushes back, settle one below, re-probe every 2 minutes and remember the limit. Optional speed cap and free-space guard. |
| Daily limit | If lolroms starts sending its "download limit" web page instead of files, downloads pause and continue at the next sync. |
| Progress | Activity panel (counts, speed, parallel downloads), **This sync** box (total size, files left, time remaining), **Downloading** box (one progress bar per file), live log. |
| Schedule | Optional automatic sync every N hours and a full site scan every N days. |
| Reliability | A sync cut off by a restart, crash or reboot continues by itself at the next start. Power menu: **Restart** / **Shut down**. Starting a second copy just opens the running one. |
| RomM | **RomM Mapper** maps every folder to a RomM platform (built-in rules, editable per folder from a dropdown, including "exclude"). Generates RomM's `config.yml`, and either keeps one folder link per platform (one RomM mapping) or writes one Docker mount per platform. Choices are saved instantly and backed up next to the catalog. |
| Interface | Dark (default) or light theme, works on a phone, optional password (HTTP Basic), no external resources (works on a LAN without internet for the page itself). |

---

## Install

### Unraid (Docker Compose Manager) - recommended

The image is built on the server from this GitHub repository; no image registry is involved.

1. Install **Docker Compose Manager** from Apps.
2. Docker tab → Compose → **Add New Stack** `rom-sync` → Edit Stack → Compose File → paste
   [`unraid/docker-compose.yml`](unraid/docker-compose.yml) and adjust the paths:
   ```yaml
   services:
     rom-sync:
       build:
         context: https://github.com/sireteanu/rom-sync.git#main
       pull_policy: build            # every "Compose Up" rebuilds from the latest code
       image: rom-sync:latest
       container_name: rom-sync
       ports:
         - "8484:8484"               # change the first number for another port
       volumes:
         - /path/to/appdata/rom-sync/config:/config   # catalog, settings, ticks, log
         - /path/to/roms:/romm               # RomM's roms folder (see RomM below)
       environment:
         - PUID=99
         - PGID=100
         - UMASK=000
         - TZ=America/New_York
         - LOLROMS_DATA=/romm/lolroms                   # downloads go here
         # - LOLROMS_PASSWORD=change-me
       labels:
         net.unraid.docker.webui: "http://[IP]:[PORT:8484]/"
       restart: on-failure           # restarts after a crash, stays off after "Shut down"
   ```
   If you don't use RomM, a plain `- /path/to/roms:/data` mapping (and no `LOLROMS_DATA`) is
   enough.
3. **Compose Up**, then turn on **Autostart** for the stack. Open `http://SERVER:8484/`.
4. To update: click **Compose Up** again (it fetches the latest code and rebuilds). Your catalog,
   ticks and downloads are kept.

Alternatives: [`docker/unraid-update.sh`](docker/unraid-update.sh) does the same with plain
`docker build` / `docker run`; [`docker-compose.yml`](docker-compose.yml) builds from a local
checkout; [`unraid/lolroms-sync.xml`](unraid/lolroms-sync.xml) is a Docker-tab template for an
image published to a registry (the `Docker image` workflow can publish one to ghcr.io / Docker Hub
when started by hand).

### Windows

1. Install Python 3.9+ (tick "Add python.exe to PATH").
2. Double-click **`start.bat`**. The first start creates `.venv` and installs the dependencies; the
   browser then opens `http://localhost:8484/`.
3. Set the download folder in Settings, or start with `start.bat --data D:\Roms`.

Catalog and settings live in `config\` next to `start.bat`; the log is `config\rom-sync.log`.
`run.py` is an alternative launcher that keeps its window open on errors.

---

## How it works

### Scanning (building the catalog)

- A **full scan** starts at `https://lolroms.com/` and follows `?dir=` folder links breadth-first
  with up to 8 parallel requests, pacing itself (it halves parallelism and doubles the delay on
  429/503/timeouts or slow answers and recovers gradually). It respects `robots.txt`.
- Every listing row gives the file's name, link, **date** and **size** (see
  [facts](#facts-about-lolromscom-this-relies-on)). Files are recognised from the link alone
  (`?file=Folder%2FName.7z`); they are never requested during a scan.
- Only the file types chosen in Settings are counted (default **archives**: zip, 7z, rar, tar, gz…;
  "everything" adds iso, chd, ROM images…).
- After a complete scan, files and folders the site no longer lists are removed from the catalog
  (only below folders that were read successfully). Your downloaded files are never deleted.
- A folder that couldn't be read is shown in red with the error; it doesn't stop the scan.

### Selection rules

- A tick is a rule on a folder and applies to everything below it, including folders added to the
  site later. Unticking a subfolder adds an exception.
- **Files in this folder** (shown as the first row of a folder that has files *and* subfolders) is
  a rule for only the files directly in that folder (stored as `"<folder>/."`).
- A box shows `-` (partial) when some things below it differ. Clicking a `-` box ticks everything.
  Ticking every subfolder (and its own files, if any) one by one turns the folder into a full tick.
- The header strip shows the total size of the selection, how much of it is synced and how much
  is left to download.

### Syncing

For every selected file (after re-reading the selected folders' listings, unless that setting is
off), `Downloader.plan()` compares the listing with the disk and with a manifest of what was
downloaded before:

| On disk | Action |
|---|---|
| Missing | **New**: download |
| `.part` file, or smaller than recorded | **Resume** from where it stopped (HTTP Range) |
| Listing date or size differs from when it was saved | **Update**: download again; the old copy is replaced only when the new one is complete |
| Matches | **Skip**, no request |
| Present but not in the manifest (copied in by hand) | **Adopt** if its size fits the listing, otherwise resume/update |

- Files are written as `<name>.part` and renamed when complete. A web page received instead of a
  file is rejected, and archive headers are checked (7z/zip/rar…).
- **Dates**: lolroms sends no `Last-Modified` header, so the listing date is the only date; files
  get it at 12:00 local time (`TZ`). Already-present files are re-dated without downloading. After
  each sync, every folder that was touched (and its parents) gets the date of the newest item in
  it. If the server refuses to change dates, the log says so once.
- **Permissions**: with the File permissions setting (default `777`), each saved or checked file is
  `chmod`-ed, and new folders get the same mode plus the "enter" bit where reading is allowed.
- **Parallel downloads** (`DownloadGate`): start at 2 (or the limit remembered for the site), add one
  while every slot is busy, and on push-back (HTTP 429/503, refused connections) set the ceiling one
  below; a level that worked before needs two push-backs within 10 s to be lowered; every 2 minutes
  it tries one more. Pushed-back files go back in the queue without using their retries. Setting
  "Fixed number" disables this.
- **Daily limit**: when the site answers with an HTML page instead of a file while nothing else is
  downloading, downloads pause ("Paused: …") and stay queued for the next sync.
- **Free space**: downloads pause before the disk would drop below "Keep free on disk".
- **Interrupted syncs**: while a sync runs, `interrupted_job` is stored; if the app stops without the
  sync ending (restart, crash, reboot, Shut down), the sync starts again at the next start and
  resumes `.part` files. Stop (button) and a normal finish clear it.
- **Schedule**: with "Sync automatically every N hours", the next run is N hours after the last sync
  finished; it becomes a scan+sync when the last full scan is older than "Full site scan every N days".

### Interface

- Header: title with **version** (links to its release notes), job pill, **Scan site**, **Sync
  selected**, **Stop**, **RomM Mapper**, power menu (**Restart**, **Shut down**), theme toggle, Settings.
- Storage strip, folder tree, Activity (job line, This sync box, counters), Log, Downloading.
- The page polls `/api/status` every second while busy, every 4 s when idle.

---

## RomM integration

RomM expects one folder per platform, named by its *slug* (`snes`, `psx`…), directly inside its
roms folder. lolroms nests platforms (`Nintendo/Super Famicom/Satellaview`) and keeps variants in
subfolders (`T-En`, `MSU-1`, `Color`…). Rom Sync bridges this without moving files.

### Mapping folders to platforms (RomM Mapper button)

The dialog lists every folder that has files. Each folder resolves to one of: **platform** (a RomM
slug), **merge** (its games join the platform above), **bios:slug** (firmware), **skip**,
**exclude** (skipped, and its folder name is always written to RomM's `exclude.roms.multi_file.names`),
or **no platform**:

1. Your own choice, picked from the folder's dropdown (Default, merge, skip, exclude, any RomM
   platform, firmware for any platform). Saved immediately (`POST /api/romm/map`); stored in the
   catalog's `romm` setting and mirrored to `<config>/romm-mapper.json`, which is restored
   automatically if the catalog ever loses them (reset or replaced).
2. Built-in rules for lolroms' tree (`RULES` in `romm.py`), e.g. `Nintendo/Super Famicom` → `snes`,
   `…/Satellaview` → `satellaview`, `Arcade/MAME` → `arcade`, `Arcade/FBNeo` → skip,
   `Nintendo/amiibo` → skip.
3. Otherwise inherited: a subfolder of a platform is merged (option "Include subfolders such as
   T-En…"), except names that are flux/raw captures or not games (`Flux`, `A2R`, `Kryoflux`, `CDN`,
   `Saves`, `Video`…) which are skipped, and names containing `BIOS`, which become firmware.

Arcade boards without a RomM slug (Naomi, Atomiswave, Chihiro…) become custom platforms with
`system.versions` pointing them at `arcade`. Several folders mapping to one slug (e.g.
`APF/Imagination Machine` + `APF/MP-1000` → `apf`) share their nearest common folder. The
**RomM platforms list** (a reference panel, separate from the output tabs) lists all 459 RomM slugs with names and makers (vendored from RomM's
`platform_slugs.py` and metadata tables, October 2026).

### How RomM sees the folders

- **Platform links in one folder** (recommended): Rom Sync keeps relative folder links next to its
  download folder, e.g. in `/path/to/roms`:
  `snes -> lolroms/Nintendo/Super Famicom`, `satellaview -> lolroms/Nintendo/Super Famicom/Satellaview`.
  Map that one folder as RomM's roms folder:
  `- /path/to/roms:/romm/library/roms`. Links are created/updated after every sync and
  with **Create / update links now**. Only links pointing into the download folder are ever changed
  or removed; real folders and other links are left alone (reported as skipped). The download
  folder must be inside the links folder (default links folder: its parent).
  Firmware isn't linked (RomM's bios folder is separate).
- **A Docker mount per platform**: the dialog writes read-only bind mounts for RomM's container
  (docker-compose `volumes:`), including firmware to `bios/<slug>`.

### The generated `config.yml`

Merge it into RomM's `config.yml` and rescan in RomM:

```yaml
exclude:
  platforms: ["lolroms"]                  # links mode: the download folder isn't a platform
  roms:
    multi_file:
      names: ["Satellaview", "MSU-1", …]  # subfolders that are another platform / not games
filesystem:
  structure:
    default: 'roms/{platform}/{game}'
    firmware: 'bios/{platform}'
    "snes":
      - "roms/{platform}/{game}"
      - "roms/{platform}/{category}/{game}"
system:
  versions:
    "naomi": "arcade"
```

- **Subfolders in config.yml** (option): `{category}` levels (default) - one template per depth
  (`{category}`, `{subcategory}`, …), so subfolders the site adds later are picked up - or exact
  folder names (`roms/{platform}/T-En/{game}`).
- RomM rules this relies on (RomM `backend/config/config_manager.py`, October 2026): platforms are
  the folders directly in the default template's platform folder; per-platform templates must keep
  that folder and end in `{game}`; any other braced name is a wildcard level; `multi_file.names` is
  one global, case-insensitive list added to RomM's defaults and is applied at wildcard levels and
  at `{game}`, but literal levels are always followed; a folder read as a multi-file game by one
  template but descended by another is dropped; symlinked folders are followed.
- Because of the global exclude list, a needed folder whose name is excluded elsewhere gets an extra
  exact-name template, and a platform where a wildcard would read unwanted files falls back to
  exact names.
- RomM can't be pointed at a nested folder by `config.yml` alone: a per-platform template must use
  `{platform}` (or the platform's own key) as the platform folder, and all platform folders must sit
  directly in one folder - so `"snes": "roms/Super Famicom/{game}"` is refused. The links (or mounts)
  provide those folders.
- Options: **RomM folder** - RomM's roms folder on the server (e.g. `/path/to/roms`); Rom Sync's
  download folder on the server is derived from it (e.g. `/path/to/roms/lolroms`, from where the
  download folder sits inside the links folder). RomM's library inside its container is
  `/romm/library`. Only folders with synced files (default on).
- Command line: `python -m lolroms_sync.romm --config config [compose|config]`.

---

## Settings reference

Stored in the catalog (`settings` table); changed in the Settings dialog or `POST /api/settings`.

| Key | Default | Meaning |
|---|---|---|
| `data_dir` | from `--data` | Download folder (locked to `LOLROMS_DATA` in Docker) |
| `types` | `archives` | `archives`, `all`, or a list like `.7z,.zip` |
| `parallel` | `auto` | `auto` or a fixed number of parallel downloads |
| `parallel_max` | `16` | Upper limit for `auto` |
| `max_rate` | `""` | Overall speed limit, e.g. `10M` (empty = unlimited) |
| `min_free` | `2G` | Pause downloads before free disk space drops below this |
| `file_mode` | `777` | Permissions for downloaded files and new folders (octal; empty = system default) |
| `sync_every_hours` | `0` | Automatic sync interval (0 = off) |
| `full_scan_every_days` | `7` | With the schedule on: full scan when the last one is older |
| `refresh_before_sync` | `true` | Re-read the ticked folders' listings before each sync |
| `resume_interrupted` | `true` | Continue an unfinished sync when the app starts |
| `check_remote` | `false` | Ask the server about every existing file (exact, one request per file) |
| `romm` | `{}` | RomM Mapper: `romm_folder`, `only_synced`, `variants`, `layout` (`category`/`folders`), `method` (`links`/`mounts`), `links_dir`, `map` (your folder choices). Older `host_path` (download folder on the server) is still read. Mirrored to `<config>/romm-mapper.json` |
| `interrupted_job` | `""` | Internal: set while a sync runs |

---

## Command-line options and environment variables

`python -m lolroms_sync` (also `start.bat`, `run.py`, or `lolroms-sync` when installed):

| Option | Environment variable | Default | Meaning |
|---|---|---|---|
| `--data DIR` | `LOLROMS_DATA` | `downloads` (`/data` in Docker) | Download folder |
| `--config DIR` | `LOLROMS_CONFIG` | `config` (`/config` in Docker) | Catalog, settings, log |
| `--host` | `LOLROMS_HOST` | `127.0.0.1` (`0.0.0.0` in Docker) | Listen address |
| `--port` | `LOLROMS_PORT` | `8484` | Port |
| `--password` | `LOLROMS_PASSWORD` | none | Ask for this password (HTTP Basic, any user name); `/api/health` stays open |
| `--lock-data` | `LOLROMS_DATA_LOCKED=1` | off (on in Docker) | Download folder can't be changed in the page |
| `--no-browser` | `LOLROMS_NO_BROWSER=1` | off (on in Docker) | Don't open a browser tab |
| `--site URL` | `LOLROMS_SITE` | `https://lolroms.com/` | Hidden; for tests |

Docker only: `PUID`/`PGID` (default 99/100 = Unraid nobody/users; the entrypoint drops to this user
with `setpriv`), `UMASK` (default `000`), `TZ` (file dates and schedule). If the port is busy and
answers like Rom Sync, a new start just opens the running copy; otherwise it explains the conflict.

---

## Files on disk

| Where | What |
|---|---|
| `<config>/catalog.db` | SQLite (WAL): `dirs`, `files` (listing date/size, local state), `selection` (rules), `settings`, `scans`, `history` |
| `<config>/rom-sync.log` | Log (rotating, 2 MB × 3) |
| `<config>/romm-mapper.json` | Backup of the RomM Mapper settings and folder choices (restored if the catalog loses them) |
| `<data>/<site path>` | Synced files, same folder structure as the site (`Nintendo/Super Famicom/…`) |
| `<data>/.dir_crawler.db` | Manifest: what the listing said for each saved file, Last-Modified/ETag, per-site parallel limit |
| `<data>/…/*.part` | Unfinished downloads (resumed later) |
| links folder (RomM links mode) | `<slug>` → `lolroms/<path>` relative symlinks |

Copy `catalog.db` to move your scan, ticks and RomM choices to another machine; copy the download
folder including `.dir_crawler.db` to avoid downloading again (files are adopted otherwise).

---

## HTTP API

All JSON; the page uses nothing else.

| Method & path | Purpose |
|---|---|
| `GET /api/status?log_since=N` | Version, busy, job, catalog totals, selection totals, scan / sync progress (incl. `bytes_planned`, `bytes_left`, `files_left`, `eta`, `active` downloads), next run, new log lines |
| `GET /api/tree?path=P` | Folder node and its children (counts, sizes, newest, synced, `state`, `own_*`, `own_state`) |
| `GET /api/search?q=` | Folders matching a name |
| `GET /api/files?path=P` | Files directly in a folder with status |
| `POST /api/select` `{path, include}` | Tick/untick a folder; `path` `"<folder>/."` = its own files only |
| `POST /api/selection/clear` | Untick everything |
| `POST /api/scan` `{root}` · `POST /api/sync` · `POST /api/stop` | Jobs (409 when busy) |
| `GET/POST /api/settings` | Read / change settings (400 with a message on invalid values) |
| `GET /api/history` | Last jobs |
| `GET /api/romm` · `POST /api/romm/options` · `POST /api/romm/map {path, action}` · `POST /api/romm/links` | RomM dialog data, options, folder choices, create/update links |
| `POST /api/restart` · `POST /api/shutdown` | Restart (re-execs the program) / end it |
| `GET /api/health` | `{"ok": true}` (Docker health check; no password) |

---

## Code map

```
src/lolroms_sync/            the web app
  __main__.py   options / env vars, port check, log file, uvicorn, restart (re-exec) / shutdown
  web.py        FastAPI routes and Basic auth
  engine.py     one job at a time (scan | sync | scan+sync), progress, ETA, LogBuffer, scheduler,
                interrupted-sync resume, folder dating and RomM links after a sync
  catalog.py    SQLite catalog; tree() with recursive totals (cached); selection rules
                (effective / effective_files / state_of / select / _fold)
  scanner.py    reads listing pages into the catalog; header_dirs() for header-only sections; prune
  romm.py       RomM mapping (RULES, Mapping, templates, compose/unraid/config.yml, apply_links)
  romm_slugs.py RomM platform slugs with display names (vendored)
  static/       index.html, app.css, app.js - no build step, no CDN
src/dir_crawler/             the engine shared with the CLI
  download.py   Downloader: plan() + download(), .part/resume, dates, permissions, run_item()
  sync.py       FileInfo, Manifest, plan(), site_timestamp(), stamp_file(), stamp_folders()
  extract.py    LinkExtractor (links + per-file date/size from listing rows)
  limits.py     Limiter (page pacing), DownloadGate (auto parallel downloads), RateLimit
  urls.py / config.py / constants.py / util.py / state.py / crawler.py / cli.py
tests/          pytest against tests/mock_site.py (a local copy of lolroms' markup); never the real site
docker/         entrypoint.sh (PUID/PGID/UMASK), unraid-update.sh
unraid/         docker-compose.yml (Compose Manager), lolroms-sync.xml (Docker-tab template)
.github/workflows/  release.yml (GitHub release per version), docker.yml (registry image, manual)
start.bat, run.py, publish.bat, dir-crawler.py   launchers / helpers
```

---

## Development, tests and releases

```
pip install -e ".[dev]"
python -m pytest            # ~90 tests, ~2 min, all against the in-process mock site
python -m pyflakes src tests
python run.py --data downloads --config config
```

- Tests and development never contact the real lolroms.com. Real-site quirks are reproduced in
  `tests/mock_site.py` first (its markup copies the live site's).
- Every user-visible change gets a test, a line in `CHANGELOG.md`, and updates to this README and
  the [user guide](docs/user-guide.md).
- **Versions**: every change to the app is its own version. Small changes bump the last number
  (1.2.6 → 1.2.7); the middle number only for significant changes (decided by the owner). Bump
  `pyproject.toml` and `src/lolroms_sync/__init__.py`, add `## [x.y.z] - date` to the changelog,
  push to `main`: the `Release` workflow publishes GitHub release `vx.y.z` with that section as its
  text. Published releases are never edited.
- Unraid picks up `main` on **Compose Up**. On Windows, `publish.bat` commits, pulls and pushes.

---

## The `dir-crawler` command-line tool

The original tool, sharing the engine: `python dir-crawler.py URL [options]`. It lists the folders
of a lolroms URL that contain archives (`directories.csv`/`.txt`), with `--list-files` every file,
and with `--download FOLDER` keeps that folder in sync the same way the web app does. Useful
options: `--types`, `--dl-workers auto|N`, `--dl-max`, `--watch 6h`, `--check-remote`,
`--overwrite`, `--max-rate`, `--min-free`, `--resume`/`--fresh`, `--workers`, `--delay`,
`--max-pages`, `--max-depth`, `--plain`, `--log-file`. `python dir-crawler.py --help` lists all.

---

## Facts about lolroms.com this relies on

Verified in a browser (October 2026). If the site changes, these are the first things to check.

- Folders: `https://lolroms.com/?dir=Nintendo%2FSuper+Famicom`; files: `?file=Folder%2FName.7z` (the
  extension is in the query value). `&sort=name|date|size` variants are the same listing.
- Listing row: `<div class="row"><a href="?file=…">📄 Name</a><span class="file-meta">2025-11-22 • 1.16 KB</span></div>`.
  The link text is never used (names contain dates).
- Ignored regions: `.sortbar`, `.back` ("⬅ Back"), `<nav>`, header bars - except that a full scan
  reads one-level `?dir=` links from the root page's header (Arcade, Pokéhacks, Audio [FLAC], fixdats).
- File responses: `Content-Length`, `Accept-Ranges: bytes`, `Content-Disposition`, served through
  Cloudflare; **no `Last-Modified` or `ETag`**.
- When the daily download limit is reached the site serves an HTML page instead of the file.

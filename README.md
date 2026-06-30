# Rom Sync

A web interface for keeping a local copy of the [lolroms.com](https://lolroms.com/) folders you
care about. It reads the whole site into a catalog, shows every folder as a tree with file counts
and sizes, and lets you tick the folders to keep. Each sync then downloads only what's new,
incomplete or changed.

- **Whole-site catalog:** a scan reads every listing page and never downloads files, so it's
  quick and light on the site.
- **Tick folders:** ticking a folder includes everything below it, and folders the site adds
  later too. Untick a subfolder to leave it out.
- **Real sync:** missing files are downloaded, half-finished ones continue where they stopped,
  and files the site changed are replaced. Unchanged files cost no requests.
- **Dates match the website:** each saved file gets the date lolroms shows for it, so you can
  sort by date in Explorer or on the share to see what's new.
- **As fast as the server allows:** parallel downloads ramp up until the server pushes back, settle
  one below that, re-test the limit every 2 minutes, and remember it. If the site starts sending its
  "download limit" page, downloads pause and continue on the next sync.
- **Scheduled syncs:** for example every 6 hours, with a full site scan every few days to pick up
  new folders.
- **Survives restarts:** your ticks are saved as you click, and a sync cut off by a restart, crash
  or reboot carries on by itself when the app starts again. The header has a Restart button.
- **RomM library:** shows your synced folders to [RomM](https://github.com/rommapp/romm) under
  its platform names through read-only Docker mounts, plus a matching `config.yml`, without moving
  or copying anything (see below).
- Dark interface (with a light option), sortable columns, time remaining, a live log, and an
  optional password.

## Windows

1. Install [Python 3.9+](https://www.python.org/downloads/) and tick "Add python.exe to PATH".
2. Double-click **`start.bat`**. The first start sets everything up, which takes about a minute.
   Your browser then opens **http://localhost:8484/**.
3. Click **Scan site**, tick folders, then click **Sync selected**. Set the download folder
   (for example `F:\Roms`) under Settings, or start with `start.bat --data F:\Roms`.

The catalog and settings are kept in the `config` folder next to `start.bat`. Close the window to
stop. A sync that's cut off continues by itself the next time Rom Sync starts.

**If it won't start:** run `start.bat` (not `run.py`) so the window stays open on an error, and look
at `config\rom-sync.log`. If Rom Sync is already running, starting it again just opens the page;
if another program uses port 8484, start with `start.bat --port 8485`.

To reach it from other devices on your network, use `start.bat --host 0.0.0.0`; setting a password
with `--password ...` is a good idea then.

## Unraid (Docker)

The container serves the same interface on port 8484 and runs as `nobody:users` (99:100), so the
files it writes behave like the rest of your shares.

| Container path | Use |
|---|---|
| `/data` | Where synced files go (for example `/path/to/roms`) |
| `/config` | Catalog, settings and selection (for example `/mnt/user/appdata/lolroms-sync`) |

| Variable | Default | |
|---|---|---|
| `PUID` / `PGID` | 99 / 100 | Owner of the downloaded files |
| `UMASK` | 000 | Permissions of new files |
| `TZ` | (none) | Time zone for file dates and the schedule, for example `America/Toronto` |
| `LOLROMS_PASSWORD` | (none) | Ask for a password in the browser |

### Publishing changes

`publish.bat` (Windows) saves every change in the folder, pulls anything new from GitHub, pushes,
and creates a GitHub release `v<version>` the first time a version from `pyproject.toml` is pushed
(release notes are generated from the commits). Then click **Compose Up** on Unraid.

### Docker image for Unraid

The code lives on GitHub; the image is built on the Unraid server itself, so no image registry
(Docker Hub, ghcr.io) is involved.

1. Copy `docker/unraid-update.sh` to `/path/to/appdata/rom-sync/` on the server. If the GitHub
   repository is private, also put a read-only GitHub token in `/path/to/appdata/rom-sync/github-token`
   (fine-grained token, this repository only, *Contents: Read*).
2. In the Unraid terminal: `bash /path/to/appdata/rom-sync/unraid-update.sh`. It downloads the code
   from GitHub, builds the image, and starts the `rom-sync` container on port 8484 with
   `/path/to/appdata/rom-sync/config` as `/config` and `/path/to/roms` as `/data`
   (change `DATA=`, `PORT=` ... at the top of the script).
3. To update after pushing changes to GitHub, run the same command again (the *User Scripts* plugin
   can give it a button). Catalog, ticks and downloads are kept.

The GitHub workflow in `.github/workflows/docker.yml` can still publish an image to ghcr.io / Docker
Hub, but only when started by hand.

`docker-compose.yml` does the same for any Docker host: run `docker compose up -d --build`.

## RomM

RomM wants one folder per platform named by its slug (`roms/snes`), while lolroms nests them
(`Nintendo/Super Famicom/Satellaview`) and keeps translations and add-ons in subfolders. Click
**RomM** in the header: every folder gets a RomM platform from built-in rules for lolroms' tree
(you can change any of them: a slug, `merge`, `bios:<slug>` or `skip`), and the dialog writes:

- **Docker compose / Unraid** - read-only bind mounts such as
  `/path/to/roms/Nintendo/Super Famicom -> /romm/library/roms/snes`. For Unraid, paste the line
  into the RomM container's *Extra Parameters* (Advanced View).
- **config.yml** - `filesystem.structure` templates that pull subfolders into their platform
  (`roms/{platform}/T-En/{game}`), `exclude.roms.multi_file.names` so subfolders that are another
  platform or not games aren't read as games, and `system.versions` for arcade boards RomM has no
  platform for. Merge it into RomM's `config.yml`, restart RomM and scan.

Nothing is moved, so syncing keeps working. Copy the output again after ticking new folders.
The same output from the command line: `python -m lolroms_sync.romm --config config --host-path /path/to/roms`.

## How syncing decides

For every file in the ticked folders:

| On disk | What happens |
|---|---|
| Missing | Downloaded |
| A `.part` file, or smaller than it should be | Continued from where it stopped |
| The site shows a different date or size than when it was saved | Downloaded again; the old copy is replaced only when the new one is complete |
| Matches | Skipped, with no request |

What the site showed for each file is recorded in `.dir_crawler.db` inside the download folder.
Files you put there yourself are checked against the site's size and date, then adopted.

## Command-line tool

The original command-line tool is included and shares the same engine:

```
python dir-crawler.py "https://lolroms.com/?dir=Atari" --download F:\Roms
```

Run `python dir-crawler.py --help` to see every option.

## Develop

```
pip install -e ".[dev]"
python -m pytest           # about 90 s; everything runs against an in-process mock of lolroms
python run.py --data ./downloads
```

See `CLAUDE.md` for the architecture and the rules that keep it working.

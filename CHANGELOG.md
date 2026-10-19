# Changelog

What changed in each Rom Sync release. The section for a version becomes the text of its GitHub
release. Newest first.

## [Unreleased]

## [1.4.0] - 2026-10-19

The Folder map release: your own folder names on disk, and no more re-ticking or re-mapping when
lolroms moves a folder to a newer set date.

### New
- **Folders renamed to a newer set date are followed.** When lolroms renames
  "Archimedes (20231029-220453)" to "Archimedes (20261001-120000)", your ticks move to the new
  folder, and Folder map rules naming the old folder follow it too. A sync notices this by itself:
  if a ticked folder can't be read, it re-reads the folder above it to find the renamed one, so you
  don't have to wait for the next full scan. Files already downloaded aren't fetched again when the
  folder is mapped to a fixed name.
- **The arrow on a dated folder suggests a rule with `(*)`** (`Acorn/Archimedes (*)` →
  `Acorn/Archimedes`), which keeps matching whatever date the site uses next.
- Builds on the **Folder map** from 1.3.10 (☰ → Folder map, or the arrow on a folder's row).

## [1.3.10] - 2026-10-19

### New
- **Folder map**: save a site folder, and everything in it, under a folder name of your choice.
  Open it from the menu (☰ → Folder map) or with the arrow button on a folder's row. Use `*` for
  any text, so `Acorn/Archimedes (*)` → `Acorn/Archimedes` keeps working when lolroms renames the
  folder to a newer date. Several site folders can share one local folder. Mapped folders show
  where they're saved in the tree.

### Changed
- Folder names are no longer shortened automatically (the date-stamp removal from 1.3.7 is gone):
  without a Folder map rule, folders are saved under exactly the site's names again. Use a Folder
  map rule for the folders you want named differently.

## [1.3.9] - 2026-10-19

### Fixed
- **After an update the page could stay empty** ("Lost contact with Rom Sync", no folders, no log)
  because the browser kept running the previous version's script. The page now loads its script and
  styles by version, so each update fetches them fresh. If you see this on an older version, press
  Ctrl+F5 once.

## [1.3.8] - 2026-10-19

### Improved
- **Clearer log at start**: Rom Sync now writes which catalog and download folder it uses, how many
  folders, files and ticks the catalog holds, when the last full scan ran, and what's in the
  download folder. If the catalog is new or empty while the download folder already has files, the
  log warns that the config folder mapping probably points at the wrong place.
- Every scan or sync that starts is logged, with whether you started it or the schedule did.

## [1.3.7] - 2026-10-19

### Changed
- Folders the site names after their set's date, like "Archimedes (20231029-220453)", are saved
  without the date ("Archimedes"). Folder names on disk no longer change each time the site moves
  to a newer set. File names are left as they are. Existing catalogs are updated at the first
  start; files already in a plain-named folder are recognised by size instead of downloaded again.

## [1.3.6] - 2026-10-16

### Improved
- **A hidden page stops asking for updates**, so a forgotten browser tab no longer keeps Rom Sync
  and your disks busy. It catches up as soon as you come back to it.
- **Smaller replies**: the folder tree, search results and file lists are sent compressed, which
  makes the page quicker on a phone or over a VPN.

## [1.3.5] - 2026-10-12

### Changed
- **The RomM Mapper moved to ROM Mapper**, a separate app that can map Rom Sync's downloads and other
  folders to RomM. The RomM Mapper button and its links are gone from Rom Sync; the platform links it
  made stay where they are.
- Your RomM Mapper choices are kept in `romm-mapper.json` in the config folder - copy it to ROM
  Mapper to keep them.

## [1.3.4] - 2026-09-15

### Improved
- **Faster start of a sync, and the page stays responsive meanwhile.** Before downloading, a sync
  checks every selected file on disk. That now takes 2 disk lookups per file instead of 6, and
  files that already have the right permissions and date aren't touched at all - which matters
  most on an Unraid array or a network share. The check also runs in the background, so the web
  page no longer freezes for a few seconds at the start of a sync of a large selection.
- **Clean up** checks the download folder in the background too, so the page doesn't pause after
  each sync or when you open Clean up.

## [1.3.3] - 2026-09-12

### Changed
- **Phones**: the Folders / Activity tabs are gone. Activity, Log and Downloading now sit under the
  folder tree - scroll down to see them. The tree keeps to about two thirds of the screen so they're
  always a short scroll away.

## [1.3.2] - 2026-09-12

### Improved
- **The site going down no longer fails your sync.** When a file fails three times and lolroms.com
  itself isn't answering (Cloudflare errors, timeouts, refused connections), Rom Sync waits for the
  site instead of marking files as failed. The pill shows *Waiting for site*, the Activity panel
  says since when and when it checks next (every 30 seconds at first, up to every 10 minutes), and
  downloads carry on by themselves - resuming half-finished files - when it's back.
- New setting **When the site is down, wait up to … hours** (default 12). After that the sync
  pauses with nothing marked as failed, and the next sync continues.
- A single file that keeps failing while the site works is still reported as failed after three tries.

## [1.3.1] - 2026-08-29

### New
- **See the files behind each number**: in Activity, click **New**, **Updated**, **Resumed**,
  **Up to date** or **Failed** to list those files from the current or last sync (failed ones say why).
- **Activity log** (button at the top): every file added, updated, deleted by Clean up or failed,
  grouped by day with counts and sizes. Open a day to see its files. Kept for a year.

### Changed
- Settings, the light/dark switch, Restart and Shut down now share one menu button (☰) at the top
  right.

## [1.3.0] - 2026-08-24

A clean-up release: Rom Sync is now only the web app, and it can tidy the download folder.

### New
- **Clean up**: after each sync, Rom Sync checks the download folder for files that aren't part of
  the sync any more - removed from the site, in folders you unticked, or half-finished downloads you
  no longer need - and the Activity panel says how many there are. **Review** (or **Clean up…**)
  lists them by group; tick what should go, click **Delete selected…** and confirm. Nothing is ever
  deleted without that confirmation, files in folders Rom Sync couldn't read are never listed, and
  files you only unticked aren't ticked for deletion at first.

### Changed
- **RomM Mapper uses platform links only.** The "Docker mount per platform" choice is gone; the
  **RomM compose** tab now shows the single line that maps the links folder as RomM's roms folder.
  Links are created the first time you click **Create / update links now**, and from then on kept
  up to date after every sync (**Update the links after every sync**). If you used links before,
  nothing changes for you.
- The `dir-crawler` command-line tool is removed; its engine lives on inside Rom Sync. One less
  dependency (`rich`).

### Improved
- Updating on Unraid is faster: **Compose Up** only reinstalls Rom Sync's dependencies when they
  change, not on every new version.
- The page asks the server for less work while idle (selection totals are reused until something
  changes).
- Every push to GitHub now runs the full test suite automatically.

## [1.2.10] - 2026-08-01

### Improved
- **RomM Mapper dropdowns are simpler**: each folder shows its RomM platform or **exclude**, and that's
  all you choose from. "Default", "merge", "skip" and the firmware list are gone: a subfolder simply
  shows its parent's platform (for example T-En under Super Famicom shows `snes`), BIOS folders are
  handled automatically, and picking what a folder would be anyway leaves it on the built-in choice.
- **RomM folder** now means where Rom Sync's downloads appear inside RomM, and defaults to
  `/romm/library/roms/lolroms`.

### Fixed
- **Phones**: the Activity, Log and Downloading panels couldn't be reached under the folder tree.
  Small screens now have **Folders** / **Activity** tabs at the bottom (with a dot while something
  is running).

## [1.2.9] - 2026-07-30

### New
- **RomM Mapper**: the RomM button and dialog are renamed. Each folder's choice is now a dropdown:
  Default, merge, skip, **exclude** (new - leaves the folder out and adds its name to RomM's
  exclude rules in `config.yml`), any RomM platform, or firmware for any platform.
- A single **RomM folder** setting (RomM's roms folder on your server) replaces "Download folder on
  the server" and "RomM library in its container"; the download folder's place in it is worked out
  automatically. Your earlier setting keeps working.

### Improved
- The folder list shows every folder (no filter or search box needed), and the **RomM platforms
  list** is now a separate reference panel, set apart from the RomM compose / config.yml output.

### Fixed
- **RomM Mapper choices are never lost**: each choice is saved the moment it's made (including when
  the dialog is closed with Esc), the list no longer resets what you were doing when it refreshes,
  and a backup (`romm-mapper.json` in the config folder) restores them if the catalog is ever reset
  or replaced.

## [1.2.8] - 2026-07-26

### Changed
- The RomM dialog no longer has a **RomM on Unraid** tab; use the **RomM compose** tab (RomM's
  compose file).

## [1.2.7] - 2026-07-21

### New
- **RomM platform links**: in the RomM dialog, choose **Platform links in one folder**. Rom Sync
  then keeps one folder link per RomM platform next to its download folder - for example
  `snes` -> `lolroms/Nintendo/Super Famicom` - and updates them after every sync. Map that one folder
  as RomM's roms folder (for example `/path/to/roms`); no per-platform Docker mounts are
  needed. The generated `config.yml` tells RomM to skip the download folder itself. Links are
  relative, so they work inside RomM's container; real folders and links Rom Sync didn't make are
  never touched.

### Changed
- The Unraid compose file now maps `/path/to/roms` to `/romm` and downloads into
  `/romm/lolroms`, so the platform links can sit next to the downloads.

## [1.2.6] - 2026-07-18

### Fixed
- **Files directly in a folder can be ticked on their own.** A folder that has both files and
  subfolders (like Nintendo/Super Famicom) now shows a **Files in this folder** row first. Tick it to
  sync those files without ticking every subfolder. Before, ticking only some subfolders left the
  folder's own files out, with no way to add them except ticking the whole folder.
- Ticking a folder's files and all of its subfolders one by one now shows the folder as fully
  ticked.

## [1.2.5] - 2026-07-16

### New
- **RomM `{category}` support**: the generated `config.yml` now uses RomM's `{category}` and
  `{subcategory}` folder levels (for example `roms/{platform}/{category}/{game}`) instead of listing
  every subfolder by name. Subfolders the site adds later are picked up without changing the config.
  Subfolders that are another platform or not games are still kept out. Where a wildcard would read
  the wrong folders, that platform keeps exact folder names automatically.
- RomM dialog option **Subfolders in config.yml**: `{category}` levels (default) or exact folder
  names (the previous behaviour).

## [1.2.4] - 2026-07-11

### New
- The version you're running is shown next to "Rom Sync" at the top of the page. Click it to see
  what changed in that version.

## [1.2.3] - 2026-07-08

### New
- **File permissions** setting (Settings): downloaded files and the folders Rom Sync creates get
  these permissions. The default is **777**, so everyone on the share can read and change them.
  Leave it empty to keep the system default. Files you already have are updated at the next sync.

### Fixed
- **Folders now show the website's dates too**: after a sync, each folder gets the date of the
  newest file inside it, instead of the day it was downloaded.
- If the server refuses to change file dates, the log now says so (once) instead of failing
  silently.

## [1.2.2] - 2026-07-05

### New
- **Shut down**: the restart button is now a power menu with **Restart** and **Shut down**.
  Shutting down stops Rom Sync until you start it again (start.bat, or Start / Compose Up on
  Unraid); a sync that was running continues at the next start.

### Changed
- The Unraid compose file now uses `restart: on-failure`, so the container still restarts after a
  crash but stays off after **Shut down**. Turn on **Autostart** for the stack in Compose Manager
  so Rom Sync starts with the server.

## [1.2.1] - 2026-07-04

### New
- **Downloading** box under the log: every file being downloaded right now, with its folder, a
  progress bar, percentage, size, speed and time left. It appears while a sync runs.

### Improved
- The Activity box stays short during big syncs (the file list moved to the new box), and a paused
  sync's reason is shown there on its own line.
- "Parallel" no longer shows command-line wording ("at --dl-max" now reads "at your upper limit").

## [1.2.0] - 2026-07-01

### New
- **RomM library** (RomM button): maps every lolroms folder to a RomM platform with built-in rules
  you can change, and writes read-only mounts for RomM's container (docker compose or Unraid Extra
  Parameters) plus a matching `config.yml`. Nothing is moved, so syncing keeps working. Includes a
  searchable list of every RomM platform with names and makers.
- **Restart button** in the header. A sync that was running continues afterwards.
- **Interrupted syncs continue by themselves** after a restart, crash or reboot (Settings can turn
  this off). Folder ticks were already saved as you click.
- **Runs on Unraid** with Compose Manager: the image is built on the server straight from GitHub
  (`unraid/docker-compose.yml`). `publish.bat` sends changes from Windows to GitHub.

### Improved
- Starting Rom Sync while it already runs opens the running copy instead of failing silently; a port
  used by another program is explained.
- Everything is also logged to `rom-sync.log` in the config folder.
- `run.py` keeps its window open on errors and uses the project's own Python when needed.

## [1.1.0] - 2026-06-22

### New
- **Arcade** support: Arcade, Pokéhacks, Audio [FLAC] and fixdats, which lolroms only links from
  its header bar, are now part of a full scan. Run **Scan site** once to pick them up.
- **Sortable columns**: click Folder, Files, Size, Updated or Synced for ascending, descending, or
  back to name order.
- **This sync** box under Activity: total size of the running sync, progress, files left and time
  remaining.

### Fixed
- Ticking every subfolder of a folder now shows a full tick on the folder instead of "-", and
  clicking a "-" box ticks the whole folder.

### Changed
- Renamed to **Rom Sync**.

## [1.0.0] - 2026-06-22

- First release: scans the whole of lolroms.com into a catalog, shows it as a folder tree with tick
  boxes, and keeps the ticked folders in sync - only new, incomplete or changed files are
  downloaded, file dates match the website, and parallel downloads tune themselves to what the
  server allows. Dark interface, scheduled syncs, optional password, Windows and Docker.

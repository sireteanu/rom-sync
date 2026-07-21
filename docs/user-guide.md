# Rom Sync user guide

This guide walks you through using Rom Sync day to day: choosing what to download, keeping it up
to date, and showing it in RomM. For the technical reference, see the [README](../README.md).

- [1. Install and open it](#1-install-and-open-it)
- [2. Scan the site](#2-scan-the-site)
- [3. Choose what to keep](#3-choose-what-to-keep)
- [4. Sync](#4-sync)
- [5. Watching progress](#5-watching-progress)
- [6. Settings](#6-settings)
- [7. Keep it up to date automatically](#7-keep-it-up-to-date-automatically)
- [8. Restart, shut down and update](#8-restart-shut-down-and-update)
- [9. Show your games in RomM](#9-show-your-games-in-romm)
- [10. Troubleshooting](#10-troubleshooting)

---

## 1. Install and open it

**On Unraid** (recommended), follow [Install → Unraid](../README.md#unraid-docker-compose-manager---recommended)
in the README: add a Compose Manager stack, paste the compose file, click **Compose Up**, and turn on
**Autostart**. Then open `http://YOUR-SERVER:8484/` (or click the container's icon → **WebUI**).

**On Windows**, double-click `start.bat`. The first start takes a minute; then your browser opens
`http://localhost:8484/`.

At the top of the page you'll see **Rom Sync** and the version you're running (for example
`v1.2.7`). Click the version to read what changed in it.

## 2. Scan the site

The first time, the page asks you to scan. Click **Scan site**. Rom Sync reads every folder
listing on lolroms.com - **no files are downloaded** - and builds a catalog of every folder and file
with its date and size. It takes a few minutes; you can watch it in the Activity panel.

Scan again whenever you want to see new folders (or let the schedule do it, see section 7). A
folder that couldn't be read is shown in red; hover over it to see why.

## 3. Choose what to keep

The tree shows every folder with:

| Column | Meaning |
|---|---|
| Folder | Click the name or the arrow to open it |
| Files / Size | Everything below this folder |
| Updated | The newest file's date on the website |
| Synced | How much of it you already have |

Click a column header to sort by it; click again for the other direction, a third time for name
order. The search box finds folders by name. The ☰ button at the end of a row lists the files in
that folder.

**Tick boxes**

- ✅ **Tick a folder** to keep everything in it, including subfolders - and any folders the site
  adds there later.
- **Untick a subfolder** inside a ticked folder to leave just that part out. The folder then shows
  **–** (partly ticked).
- **Click a – box** to tick the whole folder again.
- **Files in this folder**: when a folder has games of its own *and* subfolders (for example
  *Nintendo → Super Famicom*), the first row inside it is *Files in this folder*. Tick it to keep
  those games without ticking every subfolder. For example: tick *Files in this folder*, *SA-1* and
  *Satellaview*, and leave *MSU-1* unticked.
- Ticking every part of a folder one by one turns it into a full tick automatically.

The strip at the top shows how big your selection is, how much is already synced and how much is
left to download. Your ticks are saved instantly - restarting doesn't lose them. **Clear selection**
unticks everything (downloaded files stay where they are).

## 4. Sync

Click **Sync selected**. Rom Sync:

1. re-reads the ticked folders on the website (to catch changes),
2. works out which files are **new**, **unfinished** or **changed**,
3. downloads only those. Files you already have and that haven't changed are skipped without
   contacting the site.

Run it again any time - it only ever fetches what's missing or changed. If you copied files into
the download folder yourself, Rom Sync recognises them (by size) and doesn't download them again.

Every downloaded file gets the **date shown on the website**, so you can sort by date in your file
manager to see what's new; folders get the date of their newest file. Files get permissions **777**
(everyone can read and change them) unless you change that in Settings.

**Stop** ends the current scan or sync; unfinished files continue next time.

## 5. Watching progress

The right-hand side shows:

- **Activity** - what's running. During a sync, the **This sync** box shows the total size of the
  sync, a progress bar, how many files are left, the time remaining and roughly when it will finish.
  Below it: counts of new, updated, resumed, up-to-date and failed files, the speed, and how many
  downloads run in parallel.
- **Log** - everything that happens, newest at the bottom.
- **Downloading** - every file being downloaded right now, with its own progress bar, percentage,
  speed and time left.

Rom Sync finds by itself how many downloads the site allows at once, and slows down when the site
asks it to. If the site's **daily download limit** is reached, the sync pauses ("Paused: …") and
the rest is downloaded at the next sync.

## 6. Settings

Click the gear icon.

| Setting | What it does |
|---|---|
| Download folder | Where files go (fixed in Docker - set by the container's folder mapping) |
| File types | Archives (zip, 7z, rar…), everything the site lists, or only the extensions you name |
| Parallel downloads | *As many as the server allows* (recommended, with an upper limit) or a fixed number |
| Speed limit | e.g. `10M` for 10 MB/s; empty = unlimited |
| Keep free on disk | Downloads pause before free space drops below this (default 2G) |
| File permissions | For downloaded files and new folders; default 777; empty = system default |
| Sync automatically every … hours | 0 = off |
| Full site scan every … days | Picks up new folders (used with the automatic sync) |
| Re-read the ticked folders before each sync | Recommended; catches files the site changed |
| Continue an unfinished sync when the app starts again | After a restart, crash or reboot |
| Ask the server about every file I already have | Exact but slow (one request per file); rarely needed |

## 7. Keep it up to date automatically

Set **Sync automatically every** to, say, `6` hours and **Full site scan every** to `7` days. The
Activity panel shows when the next automatic sync will run. New files on the site arrive by
themselves; new folders arrive too if their parent folder is ticked.

## 8. Restart, shut down and update

- **Power button** (top right): **Restart** brings Rom Sync back in a few seconds; **Shut down**
  stops it until you start it again (Unraid: Docker tab → rom-sync → Start, or Compose Up; Windows:
  `start.bat`). A sync that was running continues when it starts again.
- **Updating on Unraid**: Docker tab → Compose → rom-sync → **Compose Up**. It downloads the newest
  version from GitHub and rebuilds; your catalog, ticks and files stay. Check the version next to
  the title afterwards.
- **Theme**: the sun/moon button switches between dark and light.

## 9. Show your games in RomM

Rom Sync can show your synced folders to [RomM](https://github.com/rommapp/romm) under RomM's
platform names (`snes`, `psx`, …) without moving or copying anything. Click **RomM** in the header.

**Step 1 - check the platforms.** The table lists every folder and the RomM platform it becomes.
Most are filled in already. *Needs a platform* shows folders that have none; type one (the **RomM
platforms** tab lists all of them with full names - click a name to copy it), or type:

- `merge` - put this folder's games into the platform above it (translations, hacks, add-ons),
- `bios:psx` - this folder holds firmware for that platform,
- `skip` - leave it out of RomM.

Clear a box to go back to the default.

**Step 2 - choose how RomM sees the folders.**

*Platform links in one folder* (recommended). Rom Sync puts one link per platform next to its
download folder - for example `/path/to/roms/snes` pointing to
`lolroms/Nintendo/Super Famicom` - and keeps them up to date after every sync. In RomM's compose
file, map that folder as RomM's roms folder:

```yaml
      - /path/to/roms:/romm/library/roms
```

Click **Create / update links now** the first time. (Rom Sync's own compose file must map
`/path/to/roms` and download into its `lolroms` subfolder - the example compose file
does.)

*A Docker mount per platform*: copy the **RomM compose** (or **RomM on Unraid**) tab into RomM's
container settings instead.

**Step 3 - the config file.** Copy the **config.yml** tab and merge it into RomM's `config.yml`
(in RomM's config folder), keeping your other settings. It tells RomM which subfolders belong to a
platform (using `{category}` levels, so new subfolders are picked up), which subfolders to ignore,
and to ignore the `lolroms` folder itself.

**Step 4** - restart RomM and scan its library.

Set **Download folder on the server** to where the downloads are as the server sees them (for
example `/path/to/roms/lolroms`), so the generated paths are right.

## 10. Troubleshooting

**The page doesn't open / Rom Sync won't start.** On Windows run `start.bat` (the window stays open
on errors) and look at `config\rom-sync.log`. If it says the port is in use, another program uses
8484: start with `start.bat --port 8485`. On Unraid, check the container's log in the Docker tab.

**Files show today's date instead of the website's.** Run a sync: files already there are re-dated.
If the log says "Could not set file dates", Rom Sync isn't allowed to change those files - usually
because they were copied in as another user. On Unraid run *Tools → New Permissions* on the share
(or `chown -R nobody:users <folder>`), then sync again. When copying with FileZilla, turn on
*Transfer → Preserve timestamps*.

**A folder's own files don't download.** Open the folder and tick **Files in this folder** (only
ticking its subfolders leaves its own files out).

**"Paused: …" during a sync.** The site's daily limit was reached; the next sync continues.

**A RomM platform is missing.** Make sure the folder has synced files, has a platform in the RomM
dialog, the links are up to date (Create / update links now), and RomM's `config.yml` has the new
version of the generated section. Then rescan in RomM.

**Something else?** The log (right side, and `rom-sync.log` in the config folder) says what went
wrong. Open an issue on GitHub with the relevant log lines.

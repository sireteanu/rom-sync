"""RomM mapping: turn the lolroms folder tree into RomM platforms without moving any files.

RomM wants one folder per platform, named by its slug, under <library>/roms/. lolroms nests
platforms (Nintendo/Super Famicom/Satellaview) and keeps variants in subfolders (T-En, MSU-1).
Instead of moving anything, we generate:

* Docker bind mounts that show each lolroms folder to RomM under its slug
  (Nintendo/Super Famicom -> /romm/library/roms/snes, read-only), and
* a config.yml with `filesystem.structure` templates that pull variant subfolders into the
  platform (roms/{platform}/T-En/{game}), `exclude.roms.multi_file.names` so subfolders that are
  their own platform (Satellaview) or skipped (Flux dumps) aren't read as games, and
  `system.versions` for platforms RomM has no slug for (Naomi -> arcade).

When several folders map to one slug (APF/Imagination Machine + APF/MP-1000 -> apf), their
nearest common folder is mounted and each one gets its own template.

Per folder an action decides what happens: a RomM slug, "merge" (into the platform above),
"bios:<slug>" (firmware), "skip", or nothing (the built-in rule, else inherited).
RomM rules this relies on (backend/config/config_manager.py, Oct 2026): platforms are the
folders in the default template's platform dir; per-platform templates must keep that dir and
end in {game}; literal levels are followed even if excluded; a folder read as a multi-file game
by one template but descended by another is dropped; multi_file.names adds to the defaults.
"""
from __future__ import annotations

import difflib
import json
import os
import posixpath
import re
from typing import Optional

from .romm_slugs import PLATFORMS, SLUGS  # noqa: F401 (PLATFORMS is used by web.py)

DEFAULT_OPTIONS = {
    "host_path": "",              # where the download folder is on the Docker host, e.g. /path/to/roms
    "library": "/romm/library",  # RomM's library folder inside its container
    "only_synced": True,          # only folders that have files on disk (a mount of a missing folder
                                  # makes Docker create an empty one)
    "variants": True,             # pull unmapped subfolders (T-En, hacks ...) into their platform
    "method": "mounts",           # how RomM sees the folders: "mounts" = a Docker mount per platform,
                                  # "links" = platform folder links next to the download folder
    "links_dir": "",              # where the links go (path inside Rom Sync); "" = the download
                                  # folder's parent, e.g. /romm when downloads are /romm/lolroms
    "layout": "category",         # config.yml subfolder levels: "category" = {category} wildcards
                                  # (new subfolders are picked up), "folders" = exact folder names
    "map": {},                    # your own actions: {lolroms path: action}
}

# Built-in rules for lolroms' tree (folder names as of Oct 2026). Subfolders without a rule
# follow their parent: merged into its platform, or skipped / firmware by name (see below).
RULES = {
    # Arcade (only linked from the site's header bar)
    "Arcade/MAME": "arcade", "Arcade/FBNeo": "skip", "Arcade/HBMAME": "skip",
    "Arcade/Naomi": "naomi", "Arcade/Naomi 2": "naomi2", "Arcade/Atomiswave": "atomiswave",
    "Arcade/Chihiro": "chihiro", "Arcade/Triforce": "triforce", "Arcade/Lindbergh": "lindbergh",
    "Arcade/RingEdge": "ringedge", "Arcade/RingEdge 2": "ringedge2", "Arcade/RingWide": "ringwide",
    "Arcade/System 246": "system246", "Arcade/System 573": "system573", "Arcade/Aleck64": "aleck64",
    "Arcade/Prologue 21": "skip", "Arcade/PC Based (Nintendo)": "skip",
    "Audio [FLAC]": "skip", "fixdats": "skip",
    "Acorn/Archimedes": "acorn-archimedes", "Acorn/Risc PC": "acorn-archimedes", "Acorn/Atom": "atom",
    "Acorn/RISC OS - Flash Media": "skip",
    "Amstrad - CPC": "acpc", "Analogue Pocket": "skip",
    "APF/Imagination Machine": "apf", "APF/MP-1000": "apf",
    "Apple/II": "appleii", "Apple/II Plus": "appleii", "Apple/IIe": "appleii",
    "Apple/IIGS": "apple-iigs", "Apple/Macintosh": "mac", "Apple/PiPP!N": "apple-pippin",
    "Arduboy": "arduboy",
    "Atari/2600": "atari2600", "Atari/5200": "atari5200", "Atari/7800": "atari7800",
    "Atari/800": "atari8bit", "Atari/Jaguar": "jaguar", "Atari/Jaguar/CD": "atari-jaguar-cd",
    "Atari/Lynx": "lynx", "Atari/ST": "atari-st",
    "Bally - Astrocade": "astrocade",
    "Bandai/WonderSwan": "wonderswan", "Bandai/WonderSwan Color": "wonderswan-color",
    "Benesse/Pocket Challenge V2": "pocket-challenge-v2", "Benesse/Pocket Challenge W": "pocket-challenge-w",
    "Bit Corporation - Gamate": "gamate",
    "CASIO/Loopy": "casio-loopy", "CASIO/Loopy/Little Endian": "skip",   # same games, other byte order
    "CASIO/PV-1000": "casio-pv-1000",
    "ColecoVision": "colecovision",
    "Commodore/64": "c64", "Commodore/64/Headerless": "skip", "Commodore/Amiga": "amiga",
    "Commodore/Amiga CD": "amiga-cd", "Commodore/Amiga CD32": "amiga-cd32",
    "Commodore/Amiga CDTV": "commodore-cdtv", "Commodore/Plus-4": "c-plus-4",
    "Commodore/VIC-20": "vic-20", "Commodore/WinUAE [BIOS]": "bios:amiga",
    "Emerson - Arcadia 2001": "arcadia-2001", "Entex - Adventure Vision": "adventure-vision",
    "Epoch/Game Pocket Computer": "epoch-game-pocket-computer",
    "Epoch/Super Cassette Vision": "epoch-super-cassette-vision",
    "Evercade": "evercade", "Fairchild - Channel F": "fairchild-channel-f",
    "Fujitsu/FM Towns": "fm-towns", "Fujitsu/FM-7": "fm-7", "Fujitsu/FM-77AV (T-En)": "fm-7",
    "Funtech - Super Acan": "super-acan", "GCE - Vectrex": "vectrex",
    "GamePark/GP2X": "gp2x", "GamePark/GP32": "gp32",
    "Hartung - Game Master": "hartung", "Hitachi - S1": "hitachi-s1",
    "IBM - PC & Compatibles": "skip",                                  # disk-image preservation dumps
    "Interton - VC 4000": "interton-vc-4000",
    "LeapFrog/Explorer": "leapfrog-explorer",
    "LeapFrog/Leapster Learning Game System": "leapster",
    "Magnavox - Odyssey 2": "odyssey-2",
    "Mattel/HyperScan": "hyperscan", "Mattel/Intellivision": "intellivision",
    "Microsoft/MSX": "msx", "Microsoft/MSX2": "msx2", "Microsoft/MSX Turbo-R (T-En)": "msx-turbo",
    "Microsoft/Xbox": "xbox", "Microsoft/Xbox 360": "xbox360",
    "NEC/PC-Engine/TurboGrafx-16": "tg16", "NEC/PC-Engine/T-En": "tg16",
    "NEC/PC-Engine/SuperGrafx": "supergrafx", "NEC/PC-Engine CD": "turbografx-cd",
    "NEC/PC-FX": "pc-fx", "NEC/PC6001 (T-En)": "pc-6001", "NEC/PC8 Series": "pc-8800-series",
    "NEC/PC98": "pc-9800-series",
    "Nintendo/3DS": "3ds", "Nintendo/3DS/New 3DS": "new-nintendo-3ds",
    "Nintendo/DS": "nds", "Nintendo/DS/DSi": "nintendo-dsi",
    "Nintendo/Famicom": "nes", "Nintendo/Famicom/Disk System": "fds",
    "Nintendo/Famicom/BASIC (Tapes)": "skip", "Nintendo/Famicom/Network System": "skip",
    "Nintendo/Famicom/StudyBox": "skip",
    "Nintendo/Game & Watch": "g-and-w", "Nintendo/Game Boy": "gb", "Nintendo/Game Boy Color": "gbc",
    "Nintendo/Game Boy Advance": "gba",
    "Nintendo/Game Boy Advance/e-Reader": "e-reader-slash-card-e-reader",
    "Nintendo/GameCube": "ngc", "Nintendo/Nintendo 64": "n64", "Nintendo/Nintendo 64/64DD": "64dd",
    "Nintendo/Pokémon Mini": "pokemon-mini",
    "Nintendo/Super Famicom": "snes", "Nintendo/Super Famicom/Satellaview": "satellaview",
    "Nintendo/Super Famicom/SuFami Turbo": "sufami-turbo",
    "Nintendo/Virtual Boy": "virtualboy", "Nintendo/Wii": "wii", "Nintendo/Wii U": "wiiu",
    "Nintendo/amiibo": "skip",
    "Ouya": "ouya", "Panasonic - 3DO": "3do", "Panasonic - 3DO/M2": "panasonic-m2",
    "Philips/CD-i": "philips-cd-i", "Philips/Videopac+": "videopac-g7400",
    "Playdate": "playdate", "RCA - Studio II": "rca-studio-ii",
    "SEGA/Beena": "beena", "SEGA/DreamCast": "dc", "SEGA/Game Gear": "gamegear",
    "SEGA/Genesis": "genesis", "SEGA/Genesis/32X": "sega32", "SEGA/Genesis/Mega CD": "segacd",
    "SEGA/Master System": "sms", "SEGA/Pico": "sega-pico", "SEGA/SG-1000": "sg1000",
    "SEGA/Saturn": "saturn",
    "SNK/NeoGeo CD": "neo-geo-cd", "SNK/NeoGeo Pocket": "neo-geo-pocket",
    "SNK/NeoGeo Pocket Color": "neo-geo-pocket-color",
    "SONY/PlayStation": "psx", "SONY/PlayStation/Datel - Cheat Device Updates": "skip",
    "SONY/PlayStation 2": "ps2", "SONY/PlayStation 3 (T-En)": "ps3",
    "SONY/PlayStation Portable": "psp", "SONY/PlayStation Portable/Minis": "psp-minis",
    "Sharp/MZ-2200": "sharp-mz-2200", "Sharp/MZ-700": "sharp-mz-80k7008001500", "Sharp/X1": "x1",
    "Sharp/X68000": "sharp-x68000", "Sharp/Zaurus": "sharp-zaurus",
    "Sinclair - ZX Spectrum +3": "zxs", "Texas Instruments - 99-4A": "ti-994a",
    "VM Labs - NUON": "nuon", "VTech/CreatiVision": "creativision",
    "VTech/V.Flash & V.Smile Pro": "vflash", "VTech/V.Smile": "vsmile",
    "Watara - Supervision": "supervision", "Welback - Mega Duck": "mega-duck-slash-cougar-boy",
    "Zeebo": "zeebo",
}

# platforms RomM has no slug for: the RomM platform whose metadata they should use (system.versions)
VERSIONS = {"naomi": "arcade", "naomi2": "arcade", "atomiswave": "arcade", "chihiro": "arcade",
            "triforce": "arcade", "lindbergh": "arcade", "ringedge": "arcade", "ringedge2": "arcade",
            "ringwide": "arcade", "system246": "arcade", "system573": "arcade", "aleck64": "arcade"}

# subfolders skipped by name: raw disk/flux captures and things that are not games
SKIP_NAMES = re.compile(r"^(flux|fluxdumps|a2r|kryoflux|scp|ipf|bitstreams?|greaseweazle|waveform|wav|"
                        r"cdn|saves|encrypted|video|play-yan|loosefilesarchive|datel\b.*)$", re.I)
BIOS_NAMES = re.compile(r"\bbios\b", re.I)
DATED = re.compile(r"\(\d{4}-\d{2}-\d{2}\)")

PLATFORM, MERGE, BIOS, SKIP, UNMAPPED = "platform", "merge", "bios", "skip", "unmapped"


def parent_of(path: str) -> str:
    return path.rsplit("/", 1)[0] if "/" in path else ""


def name_of(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def common_dir(paths) -> str:
    parts = [p.split("/") for p in paths]
    out = []
    for seg in zip(*parts):
        if len(set(seg)) != 1:
            break
        out.append(seg[0])
    return "/".join(out)


def rel(path: str, base: str) -> str:
    return path[len(base):].lstrip("/") if base else path


def check_action(action: str) -> str:
    """Normalise a user-entered action; raises ValueError on nonsense."""
    a = (action or "").strip()
    low = a.lower()
    if low in ("", "auto", "default"):
        return ""
    if low in ("skip", "merge"):
        return low
    if low.startswith("bios:") or low.startswith("firmware:"):
        slug = low.split(":", 1)[1].strip()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]*", slug):
            raise ValueError("firmware needs a platform: bios:<slug>, e.g. bios:psx")
        return "bios:" + slug
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]*", low):
        raise ValueError("a RomM platform is a slug such as snes or psx (lowercase, no spaces)")
    return low


_SUGGEST = {s.replace("-", " "): s for s in SLUGS}


def suggest(name: str) -> Optional[str]:
    n = name.lower()
    n = n.split(" - ", 1)[-1]                       # "Watara - Supervision" -> "supervision"
    n = re.sub(r"\(.*?\)|\[.*?\]", "", n).strip()
    m = difflib.get_close_matches(n, list(_SUGGEST), n=1, cutoff=0.9)
    return _SUGGEST[m[0]] if m else None


class Mapping:
    """Resolve every catalog folder to a RomM role, then render mounts and config.yml.

    `folders` is {path: (files directly in it, of which on disk)}; every path's ancestors
    must be present (the catalog guarantees that)."""

    def __init__(self, folders: dict, options: Optional[dict] = None, data_dir: str = ""):
        self.folders = folders
        self.opt = dict(DEFAULT_OPTIONS, **(options or {}))
        self.data_dir = os.path.abspath(data_dir) if data_dir else ""
        self.user = {k: v for k, v in (self.opt.get("map") or {}).items() if v}
        self.res: dict = {}          # path -> dict(kind, slug, source)
        self._resolve()
        self._group()

    # ------------------------------------------------------------------ roles
    def _resolve(self):
        for path in sorted(self.folders, key=lambda p: (p.count("/"), p)):
            if not path:
                continue
            up = self.res.get(parent_of(path))
            action, source = self.user.get(path), "yours"
            if not action:
                action, source = RULES.get(path, ""), "rule"
            r = {"kind": UNMAPPED, "slug": "", "source": source if action else "auto"}
            if action == "skip":
                r["kind"] = SKIP
            elif action == "merge":
                if up and up["kind"] in (PLATFORM, MERGE):
                    r.update(kind=MERGE, slug=up["slug"])
            elif action.startswith("bios:"):
                r.update(kind=BIOS, slug=action[5:])
            elif action:
                r.update(kind=PLATFORM, slug=action)
            elif up and up["kind"] in (PLATFORM, MERGE):
                name = name_of(path)
                if BIOS_NAMES.search(name):
                    r.update(kind=BIOS, slug=up["slug"])
                elif SKIP_NAMES.match(name) or not self.opt["variants"]:
                    r["kind"] = SKIP
                else:
                    r.update(kind=MERGE, slug=up["slug"])
            elif up and up["kind"] in (SKIP, BIOS):
                r.update(kind=up["kind"], slug=up["slug"])
            self.res[path] = r

    def _counts(self, path):
        files, have = self.folders.get(path, (0, 0))
        return files, have

    def _uses(self, path) -> bool:
        files, have = self._counts(path)
        return bool(have if self.opt["only_synced"] else files)

    # ------------------------------------------------------------------ platforms
    def _group(self):
        games, fw = {}, {}
        for path, r in self.res.items():
            if r["kind"] in (PLATFORM, MERGE) and self._uses(path):
                games.setdefault(r["slug"], []).append(path)
            elif r["kind"] == BIOS and self._uses(path):
                fw.setdefault(r["slug"], []).append(path)
        wild = self.opt.get("layout", "category") == "category"
        self.platforms = []
        for slug in sorted(games):
            paths = sorted(games[slug])
            mount = common_dir(paths)
            used = set(paths)
            # every folder on the way from the mount to a used folder is a grouping level
            grouping = set()
            for p in paths:
                while len(p) > len(mount):
                    grouping.add(p)
                    p = parent_of(p)
            # folders RomM looks inside: the used ones, and (with {category} wildcards, which match
            # any subfolder) the mount and every grouping level. Their other subfolders are excluded.
            looked_in = used | ((grouping | {mount}) if wild else set())
            exclude = set()
            for d in looked_in:
                for child in self.children(d):
                    if child not in used and child not in grouping:
                        exclude.add(name_of(child))
            self.platforms.append({"slug": slug, "mount": mount, "folders": paths, "grouping": grouping,
                                   "exclude": sorted(exclude),
                                   "files": sum(self._counts(p)[1 if self.opt["only_synced"] else 0]
                                                for p in paths)})
        # exclude.roms.multi_file.names is one list for all platforms (case-insensitive), and a
        # wildcard level skips those names - so a folder we need whose name is excluded somewhere
        # gets an exact-name template instead (RomM always follows literal levels).
        excluded = {n.lower() for p in self.platforms for n in p["exclude"]}
        for p in self.platforms:
            p["templates"] = self._templates(p, wild, excluded)
            del p["grouping"]
        self.firmware = []
        for slug in sorted(fw):
            paths = sorted(fw[slug])
            self.firmware.append({"slug": slug, "mount": paths[0], "folders": paths[:1],
                                  "ignored": paths[1:]})

    def _templates(self, plat: dict, wild: bool, excluded: set) -> list:
        mount, paths = plat["mount"], plat["folders"]
        literal = ["roms/{platform}/" + (rel(p, mount) + "/" if rel(p, mount) else "") + "{game}"
                   for p in paths]
        if not wild:
            return literal
        depth = {p: (rel(p, mount).count("/") + 1 if rel(p, mount) else 0) for p in paths}
        depths = set(depth.values())
        # a wildcard level reads every folder at that depth: if one of them holds files of its own
        # but isn't wanted (only a way down to wanted folders), exact names are the only safe choice
        for g in plat["grouping"] - set(paths):
            gd = rel(g, mount).count("/") + 1
            if gd in depths and self._counts(g)[1 if self.opt["only_synced"] else 0]:
                return literal
        names = ["{category}", "{subcategory}"]
        out = []
        for d in sorted(depths):
            levels = [names[i] if i < len(names) else "{level%d}" % (i + 1) for i in range(d)]
            out.append("/".join(["roms/{platform}", *levels, "{game}"]))
        for p, t in zip(paths, literal):
            if any(part.lower() in excluded for part in rel(p, mount).split("/") if part):
                out.append(t)
        return out

    def children(self, path):
        if not hasattr(self, "_kids"):
            self._kids = {}
            for p in self.folders:
                if p:
                    self._kids.setdefault(parent_of(p), []).append(p)
        return self._kids.get(path, [])

    # ------------------------------------------------------------------ report
    def rows(self) -> list:
        """One row per folder that holds files (or has a rule of its own), for the interface."""
        out = []
        for path in sorted(self.res, key=str.casefold):
            r = self.res[path]
            files, have = self._counts(path)
            if not files and path not in self.user and path not in RULES:
                continue
            row = {"path": path, "files": files, "synced": have, "kind": r["kind"], "slug": r["slug"],
                   "source": r["source"], "action": self.user.get(path, ""), "rule": RULES.get(path, ""),
                   "used": r["kind"] in (PLATFORM, MERGE, BIOS) and self._uses(path)}
            if r["kind"] in (PLATFORM, MERGE) and r["slug"] not in SLUGS:
                row["custom"] = True
            out.append(row)
        return out

    def warnings(self) -> list:
        w = []
        unmapped = [p for p, r in self.res.items() if r["kind"] == UNMAPPED and self._uses(p)]
        if unmapped:
            w.append(f"{len(unmapped)} folders with files have no RomM platform yet and are left out: "
                     + ", ".join(unmapped[:6]) + (" ..." if len(unmapped) > 6 else ""))
        custom = sorted({p["slug"] for p in self.platforms if p["slug"] not in SLUGS})
        unknown = [s for s in custom if s not in VERSIONS]
        if unknown:
            w.append("RomM has no platform called " + ", ".join(unknown) +
                     " - it will add them as custom platforms without metadata.")
        dated = [f for p in self.platforms + self.firmware for f in p["folders"] if DATED.search(f)]
        if dated:
            w.append("These folder names include a date and change when the site updates them, so copy "
                     "the mounts again after a scan: " + ", ".join(dated))
        for f in self.firmware:
            if f["ignored"]:
                w.append(f"Firmware for {f['slug']} comes from {f['mount']}; also found in "
                         + ", ".join(f["ignored"]) + " (left out - RomM reads one firmware folder per platform).")
        if self.links:
            if self.data_in_links() is None:
                w.append("Platform links need the download folder inside the links folder (for example "
                         "downloads in /romm/lolroms and links in /romm) - change the links folder or "
                         "the download folder.")
            if self.firmware:
                w.append("Firmware isn't linked (RomM's bios folder is separate): copy these to RomM's bios "
                         "folder: " + ", ".join(f["mount"] for f in self.firmware))
        if not self.opt["host_path"]:
            w.append("Set the download folder's path on your server (e.g. /path/to/roms) so the mounts "
                     "point at the right place.")
        return w

    # ------------------------------------------------------------------ outputs
    # ------------------------------------------------------------------ links mode
    @property
    def links(self) -> bool:
        return self.opt.get("method") == "links"

    @property
    def links_dir(self) -> str:
        d = self.opt.get("links_dir") or (os.path.dirname(self.data_dir) if self.data_dir else "")
        return os.path.abspath(d) if d else ""

    def data_in_links(self) -> Optional[str]:
        """The download folder relative to the links folder ("lolroms"), or None if it isn't
        inside it (then RomM, which only sees the links folder, couldn't follow the links)."""
        if not (self.data_dir and self.links_dir):
            return None
        r = os.path.relpath(self.data_dir, self.links_dir)
        return None if r == "." or r.startswith("..") or os.path.isabs(r) else r.replace(os.sep, "/")

    def link_targets(self) -> dict:
        """{platform slug: relative link target}, e.g. {"snes": "lolroms/Nintendo/Super Famicom"}"""
        inside = self.data_in_links()
        if inside is None:
            return {}
        return {p["slug"]: inside + ("/" + p["mount"] if p["mount"] else "") for p in self.platforms}

    def _links_host(self) -> str:
        """The links folder as the Docker host sees it (RomM's roms folder)."""
        base = (self.opt["host_path"] or "/path/to/downloads").rstrip("/\\")
        inside = self.data_in_links()
        if not inside:
            return base
        return posixpath.normpath(base + "/" + "/".join([".."] * (inside.count("/") + 1)))

    def _source(self, path: str) -> str:
        base = (self.opt["host_path"] or "/path/to/downloads").rstrip("/\\")
        return base + ("/" + path if path else "")

    def _mounts(self):
        lib = self.opt["library"].rstrip("/")
        for p in self.platforms:
            yield self._source(p["mount"]), f"{lib}/roms/{p['slug']}"
        for f in self.firmware:
            yield self._source(f["mount"]), f"{lib}/bios/{f['slug']}"

    def compose(self) -> str:
        if self.links:
            return ("    volumes:\n"
                    "      # for the RomM service (not Rom Sync): replace its .../romm/library/roms line.\n"
                    "      # Rom Sync keeps one folder link per platform in here.\n"
                    f"      - {json.dumps(self._links_host() + ':' + self.opt['library'].rstrip('/') + '/roms', ensure_ascii=False)}\n")
        lines = ["    volumes:",
                 "      # for the RomM service (not Rom Sync): Rom Sync's folders, read-only,",
                 "      # each shown to RomM under its platform name"]
        for src, dst in self._mounts():
            lines += ["      - type: bind", f"        source: {json.dumps(src, ensure_ascii=False)}",
                      f"        target: {json.dumps(dst)}", "        read_only: true"]
        return "\n".join(lines) + "\n"

    def unraid(self) -> str:
        """For the RomM container's "Extra Parameters" field (Advanced View)."""
        if self.links:
            return (f"In RomM's container settings, set the path mapped to {self.opt['library'].rstrip('/')}/roms "
                    f"to:\n{self._links_host()}\n")
        out = []
        for src, dst in self._mounts():
            if "," in src or "'" in src:
                out.append(f"-v '{src}:{dst}:ro'" if "'" not in src and ":" not in src
                           else f"# cannot mount {src!r} here - use docker compose")
            else:
                out.append(f"--mount 'type=bind,source={src},target={dst},readonly'")
        return " ".join(o for o in out if not o.startswith("#")) + "\n" + \
            "".join(o + "\n" for o in out if o.startswith("#"))

    def config_yml(self) -> str:
        q = lambda s: json.dumps(s, ensure_ascii=False)  # noqa: E731 - JSON strings are valid YAML
        lines = ["# RomM config.yml - generated by Rom Sync. Merge these sections into your config.yml",
                 "# (keep your other settings). Library: Rom Sync's folders mounted at roms/<platform>.", ""]
        names = sorted({n for p in self.platforms for n in p["exclude"]}, key=str.casefold)
        inside = self.data_in_links() if self.links else None
        if inside:
            lines += ["exclude:", "  # the download folder itself is not a platform (its platforms are linked)",
                      "  platforms:", f"    - {q(inside.split('/')[0])}"]
            if names:
                lines += ["  roms:", "    multi_file:",
                          "      # subfolders that are another platform, firmware or not games",
                          "      names:"] + [f"        - {q(n)}" for n in names]
            lines.append("")
            names = []
        if names:
            lines += ["exclude:", "  roms:", "    multi_file:",
                      "      # subfolders that are another platform, firmware or not games",
                      "      names:"] + [f"        - {q(n)}" for n in names] + [""]
        custom = [p for p in self.platforms if p["templates"] != ["roms/{platform}/{game}"]]
        if custom:
            lines += ["filesystem:", "  structure:", "    default: 'roms/{platform}/{game}'",
                      "    firmware: 'bios/{platform}'"]
            for p in custom:
                lines.append(f"    {q(p['slug'])}:")
                lines += [f"      - {q(t)}" for t in p["templates"]]
            lines.append("")
        versions = {p["slug"]: VERSIONS[p["slug"]] for p in self.platforms if p["slug"] in VERSIONS}
        if versions:
            lines += ["system:", "  versions:"] + [f"    {q(k)}: {q(v)}" for k, v in versions.items()] + [""]
        return "\n".join(lines)

    def summary(self) -> dict:
        return {"platforms": len(self.platforms), "firmware": len(self.firmware),
                "files": sum(p["files"] for p in self.platforms)}


def apply_links(m: "Mapping") -> dict:
    """Create / update / remove the platform folder links for links mode. Only links that point
    into the download folder are ever changed or removed; real folders are never touched."""
    res = {"created": [], "updated": [], "removed": [], "kept": 0, "skipped": [], "errors": []}
    inside = m.data_in_links()
    if inside is None:
        res["errors"].append("the download folder is not inside the links folder")
        return res
    links_dir = m.links_dir
    want = m.link_targets()

    def ours(target: str) -> bool:
        t = target.replace(os.sep, "/")
        return not os.path.isabs(t) and (t == inside or t.startswith(inside + "/"))

    for name, target in sorted(want.items()):
        path = os.path.join(links_dir, name)
        try:
            if os.path.islink(path):
                cur = os.readlink(path)
                if cur == target:
                    res["kept"] += 1
                    continue
                if not ours(cur):
                    res["skipped"].append(f"{name} (a link to {cur} that Rom Sync didn't make)")
                    continue
                os.remove(path)
                os.symlink(target, path, target_is_directory=True)
                res["updated"].append(name)
            elif os.path.exists(path):
                res["skipped"].append(f"{name} (a folder with that name already exists)")
            else:
                os.symlink(target, path, target_is_directory=True)
                res["created"].append(name)
        except OSError as e:
            res["errors"].append(f"{name}: {e}")
    try:
        with os.scandir(links_dir) as it:
            for e in it:
                if e.is_symlink() and e.name not in want and ours(os.readlink(e.path)):
                    try:
                        os.remove(e.path)
                        res["removed"].append(e.name)
                    except OSError as err:
                        res["errors"].append(f"{e.name}: {err}")
    except OSError as e:
        res["errors"].append(str(e))
    return res


def describe_links(res: dict) -> str:
    parts = [f"{len(res['created'])} created", f"{len(res['updated'])} updated",
             f"{len(res['removed'])} removed", f"{res['kept']} unchanged"]
    out = "RomM platform links: " + ", ".join(parts)
    if res["skipped"]:
        out += "; skipped " + "; ".join(res["skipped"])
    if res["errors"]:
        out += "; problems: " + "; ".join(res["errors"][:5])
    return out


def folders_from_catalog(catalog, types=None) -> dict:
    return {p: (n.own_files, n.own_synced) for p, n in catalog.tree(types).items()}


def options_from(settings: dict) -> dict:
    return dict(DEFAULT_OPTIONS, **(settings.get("romm") or {}))


def main(argv=None) -> int:
    """python -m lolroms_sync.romm [--config DIR] [compose|unraid|config]  - print the RomM files."""
    import argparse
    import os
    from .catalog import Catalog
    from .engine import parse_types
    ap = argparse.ArgumentParser(prog="python -m lolroms_sync.romm",
                                 description="Print RomM mounts / config.yml for the folders Rom Sync keeps.")
    ap.add_argument("what", nargs="?", choices=("compose", "unraid", "config", "all"), default="all")
    ap.add_argument("--config", default=os.environ.get("LOLROMS_CONFIG") or "config",
                    help="Rom Sync's config folder (holds catalog.db)")
    ap.add_argument("--host-path", help="the download folder's path on the Docker host (overrides the saved one)")
    a = ap.parse_args(argv)
    cat = Catalog(os.path.join(a.config, "catalog.db"))
    s = cat.settings()
    opt = options_from(s)
    if a.host_path:
        opt["host_path"] = a.host_path
    m = Mapping(folders_from_catalog(cat, parse_types(s["types"])), opt, s["data_dir"])
    parts = {"compose": m.compose, "unraid": m.unraid, "config": m.config_yml}
    for k in (parts if a.what == "all" else [a.what]):
        if a.what == "all":
            print(f"# ---------------- {k}")
        print(parts[k]())
    for w in m.warnings():
        print("# NOTE:", w)
    cat.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

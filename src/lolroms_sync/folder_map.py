"""Where each site folder is saved on disk - set in the folder tree's "Saved as" column.

Two settings in the catalog, both keyed by site folder path ("SEGA/32X"):
- `folder_places` {folder: place}: where that folder goes.
    "Archimedes"             a plain name: renamed, in its parent's folder on disk
    "Extras/Sub A"           a path: under its parent's folder on disk
    "/Sega/32X Extras/Sub A" starts with /: counted from the download folder itself
- `folder_kids` {folder: place}: where that folder's subfolders go, each under its own name
  (also subfolders the site adds later). Same forms; "/" = the download folder itself.
    {"SEGA/32X": "/"}                  SEGA/32X/Sub A -> Sub A
    {"SEGA/32X": "/Sega/32X Extras"}   SEGA/32X/Sub A -> Sega/32X Extras/Sub A
  A subfolder's own place wins over its parent's subfolder setting. Files directly in the folder
  stay with the folder.

Everything below a folder follows it. File names never change. Keys match ignoring case.
"""
from __future__ import annotations

from typing import Optional

from .core.util import safe_segment


def _segments(path: str) -> list:
    return [s.strip() for s in path.replace("\\", "/").split("/") if s.strip()]


def clean_place(value, kids: bool = False) -> str:
    """A place typed in the page -> tidy text ("" = the default). ValueError says what to fix."""
    raw = str(value or "").strip().replace("\\", "/")
    if not raw:
        return ""
    absolute = raw.startswith("/")
    segs = _segments(raw)
    if any(s in (".", "..") for s in segs):
        raise ValueError("folder names can't be . or ..")
    if any("*" in s for s in segs):
        raise ValueError("folder names can't contain *")
    if not segs:
        if absolute and kids:
            return "/"                     # subfolders straight into the download folder
        raise ValueError("enter a folder name")
    return ("/" if absolute else "") + "/".join(segs)


class FolderMap:
    def __init__(self, places: Optional[dict] = None, kids: Optional[dict] = None):
        self.places = dict(places or {})
        self.kids = dict(kids or {})
        self._places = {k.casefold(): v for k, v in self.places.items()}
        self._kids = {k.casefold(): v for k, v in self.kids.items()}

    def __bool__(self) -> bool:
        return bool(self.places or self.kids)

    @staticmethod
    def _apply(base: list, place: str) -> list:
        return _segments(place) if place.startswith("/") else base + _segments(place)

    def map_parts(self, parts: list) -> list:
        """Folder segments as on the site -> folder segments on disk (not yet made safe)."""
        out: list = []
        for i, seg in enumerate(parts):
            base = out
            kids = self._kids.get("/".join(parts[:i]).casefold())
            if kids is not None:
                base = self._apply(out, kids)
            place = self._places.get("/".join(parts[:i + 1]).casefold())
            out = self._apply(base, place) if place else base + [seg]
        return out

    def base_of(self, site_path: str) -> str:
        """The folder on disk this site folder sits in when it has no place of its own (its
        parent's place, or where its parent sends subfolders), names made safe."""
        parts = site_path.split("/") if site_path else []
        if not parts:
            return ""
        out = self.map_parts(parts[:-1])
        kids = self._kids.get("/".join(parts[:-1]).casefold())
        if kids is not None:
            out = self._apply(out, kids)
        return "/".join(safe_segment(s) for s in out)

    def as_typed(self, site_path: str, full: str) -> str:
        """A full path typed in the Saved as column ("/Apple II") -> the place to store: "" when it
        is where the folder goes anyway, a name or path below its base (so it moves along when its
        parent does), else the /path as typed."""
        segs = [safe_segment(s) for s in _segments(full)]
        typed = "/".join(segs)
        base = self.base_of(site_path)
        default = (base + "/" if base else "") + safe_segment(site_path.rsplit("/", 1)[-1])
        if typed == default:
            return ""
        if not base:
            return typed
        if typed.startswith(base + "/"):
            return typed[len(base) + 1:]
        return "/" + typed

    def disk_folder(self, site_path: str) -> str:
        """Catalog folder ("Acorn/Archimedes (20231029-220453)") -> its folder in the download
        folder, with names made safe like file paths are ("Acorn/Archimedes")."""
        if not site_path:
            return ""
        return "/".join(safe_segment(s) for s in self.map_parts(site_path.split("/")))

    def place_of(self, site_path: str) -> str:
        return self._places.get(site_path.casefold(), "")

    def kids_of(self, site_path: str) -> Optional[str]:
        return self._kids.get(site_path.casefold())

    @staticmethod
    def renamed(d: dict, rename) -> dict:
        """Re-key a setting after the site renamed folders: rename(path) -> new path."""
        return {rename(k): v for k, v in d.items()}


def from_old_settings(rules: list, names: dict, folders) -> tuple:
    """1.3.10-1.4.2 kept Folder map rules ([{site with *, local}]) and "Saved as" names. -> (places,
    kids) for the folders the catalog knows, so nothing a user set up is lost."""
    import re
    places = {k: v for k, v in (names or {}).items() if k and v}
    pats = []
    for r in rules or []:
        site, local = str(r.get("site") or ""), str(r.get("local") or "")
        if not site or not local:
            continue
        rx = [re.compile("".join(".*" if c == "*" else re.escape(c) for c in s), re.IGNORECASE)
              for s in _segments(site)]
        pats.append((rx, "/" + "/".join(_segments(local))))
    for folder in sorted(folders, key=lambda p: p.count("/")):
        parts = folder.split("/") if folder else []
        for rx, local in pats:
            if len(rx) == len(parts) and all(p.fullmatch(s) for p, s in zip(rx, parts)):
                own = places.get(folder)
                # an old name on the same folder renamed the rule's last folder
                places[folder] = (local.rsplit("/", 1)[0] + "/" + own) if own else local
                break
    return places, {}

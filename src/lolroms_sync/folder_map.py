"""Folder map: save a site folder (and everything below it) to a different folder on disk.

Two kinds, both stored in the catalog:
- Folder names (setting `folder_names`, {site folder: name}): typed in the tree's "Saved as"
  column. The folder keeps its place but gets another name on disk ("Archimedes (20231029-220453)"
  -> "Archimedes"); its subfolders follow. Names win over rules for that exact folder.
- Rules (setting `folder_map`, [{"site": "Acorn/Archimedes (*)", "local": "Acorn/Archimedes"}]):
  set in the Folder map dialog, for moving folders elsewhere or matching many with *.

- `site` is a folder path on lolroms.com; `*` matches any text inside one folder name, so a rule
  keeps working when the site renames "Archimedes (20231029-220453)" to a newer date.
- `local` is the folder inside the download folder that gets those files. Subfolders follow:
  "<site folder>/Sub/x.7z" is saved as "<local>/Sub/x.7z".
- The first rule that matches a folder wins. Matching ignores upper/lower case.
"""
from __future__ import annotations

import re
from typing import Optional

from .core.util import safe_segment


def _segments(path: str) -> list:
    return [s.strip() for s in path.replace("\\", "/").split("/") if s.strip()]


def _compile(segment: str):
    return re.compile("".join(".*" if c == "*" else re.escape(c) for c in segment), re.IGNORECASE)


class FolderMap:
    def __init__(self, rules: Optional[list] = None, names: Optional[dict] = None):
        """rules: [{"site": ..., "local": ...}] (checked with FolderMap.clean() first);
        names: {site folder: name on disk} (checked with FolderMap.clean_name())."""
        self.rules = list(rules or [])
        self.names = dict(names or {})
        self._names = {k.casefold(): v for k, v in self.names.items()}
        self._compiled = [([_compile(s) for s in _segments(r["site"])], _segments(r["local"]))
                          for r in self.rules]

    def __len__(self) -> int:
        return len(self.rules)

    @staticmethod
    def clean(rules) -> list:
        """Check rules from the page -> tidy list; ValueError says what to fix."""
        if not isinstance(rules, list):
            raise ValueError("folder map must be a list of rules")
        out, seen = [], set()
        for i, r in enumerate(rules, 1):
            if not isinstance(r, dict):
                raise ValueError(f"rule {i} is not a site folder / local folder pair")
            site = "/".join(_segments(str(r.get("site") or "")))
            local = "/".join(_segments(str(r.get("local") or "")))
            if not site and not local:
                continue                                    # an empty row in the dialog
            if not site:
                raise ValueError(f"rule {i}: enter the site folder")
            if not local:
                raise ValueError(f"rule {i} ({site}): enter the folder to save it in")
            if any(s in (".", "..") for s in _segments(site) + _segments(local)):
                raise ValueError(f"rule {i} ({site}): folder names can't be . or ..")
            if "*" in local:
                raise ValueError(f"rule {i} ({site}): * can only be used in the site folder")
            if site.casefold() in seen:
                raise ValueError(f"rule {i}: {site} is in the folder map twice")
            seen.add(site.casefold())
            out.append({"site": site, "local": local})
        return out

    @staticmethod
    def clean_name(name) -> str:
        """A name typed in the "Saved as" column -> tidy name; "" = use the site's name."""
        name = str(name or "").strip()
        if not name:
            return ""
        if "/" in name or "\\" in name:
            raise ValueError("a folder name can't contain / or \\ - use Folder map to move a folder elsewhere")
        if name in (".", ".."):
            raise ValueError("a folder name can't be . or ..")
        if "*" in name:
            raise ValueError("a folder name can't contain *")
        return name

    def _match(self, parts: list):
        """(rule index, its target, the folder segments it matched) for the first matching rule."""
        for i, (pat, target) in enumerate(self._compiled):
            if len(pat) <= len(parts) and all(p.fullmatch(s) for p, s in zip(pat, parts)):
                return i, target, len(pat)
        return None

    def map_parts(self, parts: list) -> list:
        """Folder segments as on the site -> folder segments on disk (not yet made safe)."""
        hit = self._match(parts)
        out, start = [], 0
        if hit is not None:
            _i, target, start = hit
            out = list(target)
            own = self._names.get("/".join(parts[:start]).casefold())
            if own and out:
                out[-1] = own                   # a name typed for that exact folder wins
        for i in range(start, len(parts)):
            out.append(self._names.get("/".join(parts[:i + 1]).casefold()) or parts[i])
        return out

    def disk_folder(self, site_path: str) -> str:
        """Catalog folder ("Acorn/Archimedes (20231029-220453)") -> its folder in the download
        folder, with names made safe like file paths are ("Acorn/Archimedes")."""
        if not site_path:
            return ""
        return "/".join(safe_segment(s) for s in self.map_parts(site_path.split("/")))

    def rule_at(self, site_path: str) -> Optional[dict]:
        """The rule that maps exactly this folder (not one of its parents), for the tree."""
        parts = site_path.split("/") if site_path else []
        hit = self._match(parts)
        if hit is None or hit[2] != len(parts):
            return None
        return dict(self.rules[hit[0]], index=hit[0])

"""Folder map: save a site folder (and everything below it) to a different folder on disk.

Rules are set in the page's Folder map dialog and stored in the catalog (setting `folder_map`) as
[{"site": "Acorn/Archimedes (*)", "local": "Acorn/Archimedes"}, ...].

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
    def __init__(self, rules: Optional[list] = None):
        """rules: [{"site": ..., "local": ...}] - checked with FolderMap.clean() first."""
        self.rules = list(rules or [])
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

    def _match(self, parts: list):
        """(rule index, its target, the folder segments it matched) for the first matching rule."""
        for i, (pat, target) in enumerate(self._compiled):
            if len(pat) <= len(parts) and all(p.fullmatch(s) for p, s in zip(pat, parts)):
                return i, target, len(pat)
        return None

    def map_parts(self, parts: list) -> list:
        """Folder segments as on the site -> folder segments on disk (not yet made safe)."""
        hit = self._match(parts)
        if hit is None:
            return list(parts)
        _i, target, n = hit
        return list(target) + list(parts[n:])

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

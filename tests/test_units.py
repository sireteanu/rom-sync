"""Unit tests: helpers, URL rules, link extraction."""
import re

import pytest

from lolroms_sync.core.config import Config
from lolroms_sync.core.extract import LinkExtractor
from lolroms_sync.core.urls import UrlRules
from lolroms_sync.core.util import extension, fmt_bytes, parse_size, safe_segment

START = "https://lolroms.com/?dir=Atari"


def rules(start=START, keep=frozenset({"dir"}), **kw):
    return UrlRules(Config(start_url=start, keep_params=keep, **kw))


# ---------------------------------------------------------------- util
@pytest.mark.parametrize("text, ext", [
    ("Atari/5200/Game (USA).7z", ".7z"),
    ("Game.7z.001", ".7z"),
    ("backup.tar.gz", ".gz"),
    ("Dr. Mario (USA).zip", ".zip"),
    ("Atari/800", ""),
    ("Sony - PlayStation 2.0", ".0"),
    ("Audio [FLAC]", ""),
])
def test_extension(text, ext):
    assert extension(text) == ext


@pytest.mark.parametrize("name, safe", [
    ("Combat: Tank <Plus>.7z", "Combat_ Tank _Plus_.7z"),
    ("CON.7z", "_CON.7z"),
    ("trailing dot. ", "trailing dot"),
    ("", "_"),
])
def test_safe_segment(name, safe):
    assert safe_segment(name) == safe


def test_safe_segment_keeps_extension_when_shortening():
    out = safe_segment("x" * 300 + ".7z")
    assert out.endswith(".7z") and len(out) <= 180


def test_sizes():
    assert parse_size("5M") == 5 * 1024 ** 2
    assert parse_size("800K") == 800 * 1024
    assert parse_size("2G") == 2 * 1024 ** 3
    assert fmt_bytes(1536) == "1.5 KB"


# ---------------------------------------------------------------- URL rules
def test_canonical_drops_sort_and_tracking_params():
    r = rules()
    assert r.canonical("https://lolroms.com/?dir=Atari%2F5200&sort=size") == \
        "https://lolroms.com/?dir=Atari%2F5200"
    assert r.canonical("https://LOLROMS.com:443//?dir=Atari&utm_source=x") == "https://lolroms.com/?dir=Atari"


@pytest.mark.parametrize("url, inside", [
    ("https://lolroms.com/?dir=Atari", True),
    ("https://lolroms.com/?dir=Atari%2F5200", True),
    ("https://lolroms.com/?dir=atari%2F800%2FFlux", True),
    ("https://lolroms.com/?dir=Atari+Extra", False),        # same prefix, not a subfolder
    ("https://lolroms.com/?dir=Acorn", False),
    ("https://lolroms.com/", False),                          # the site root
    ("https://other.com/?dir=Atari", False),
])
def test_scope(url, inside):
    assert rules().in_scope(url) is inside


def test_path_scope():
    r = rules("https://example.com/files/nes/", keep=None)
    assert r.in_scope("https://example.com/files/nes/zelda/")
    assert not r.in_scope("https://example.com/files/snes/")


def test_file_detection_from_query_value():
    r = rules()
    assert r.file_ext("https://lolroms.com/?file=Atari%2F5200%2FGame+%28USA%29.7z") == ".7z"
    assert r.file_ext("https://lolroms.com/roms/game.zip") == ".zip"
    assert r.file_ext("https://lolroms.com/?dir=Atari%2F800") == ""


def test_rel_path_mirrors_site_folders():
    r = rules()
    assert r.rel_path("https://lolroms.com/?file=Atari%2F5200%2FGame+%28USA%29.7z") == "Atari/5200/Game (USA).7z"
    assert r.rel_path("https://lolroms.com/?file=..%2F..%2Fetc%2Fx.7z") == "etc/x.7z"   # no escaping the folder
    assert r.rel_path("https://h.com/roms/nes/A%3AB.zip") == "roms/nes/A_B.zip"
    # without a folder map, folders keep the site's names exactly (date stamps included)
    url = "https://lolroms.com/?file=Acorn%2FRisc+PC+%28Flux%29+%2820230506-040449%29%2FSub%2FX+%2820230506-040449%29.7z"
    assert r.rel_path(url) == "Acorn/Risc PC (Flux) (20230506-040449)/Sub/X (20230506-040449).7z"
    from lolroms_sync.folder_map import FolderMap
    fm = FolderMap({"acorn/risc pc (flux) (20230506-040449)": "Risc PC: Flux"})
    assert r.rel_path(url, fm) == "Acorn/Risc PC_ Flux/Sub/X (20230506-040449).7z"   # names made safe


def test_names_and_children():
    r = rules()
    page = "https://lolroms.com/?dir=Atari%2F800"
    links = ["https://lolroms.com/?dir=Atari%2F800%2FFlux", "https://lolroms.com/?dir=Atari", page]
    assert r.name(page) == "Atari/800"
    assert r.children(page, links) == 1


# ---------------------------------------------------------------- link extraction
PAGE = b"""<html><body>
<header><a href="/?dir=Atari%2FHeader">h</a></header>
<nav><a href="/?dir=Atari%2FNav">n</a></nav>
<div class="sortbar"><a href="/?dir=Atari%2FSort">s</a></div>
<div class="back x"><a href="/?dir=Atari%2FBack">b</a></div>
<article><header><a href="/?dir=Atari%2FArticle">kept: header inside article</a></header></article>
<table>
  <tr><td><a href="/?dir=Atari%2F5200">sub</a></td></tr>
  <tr><td><a href="/?dir=Acorn">out of scope</a></td></tr>
  <tr><td><a href="/?file=Atari%2FGame.7z#x">file</a></td></tr>
  <tr><td><a href="/get?id=5" download>attr download</a></td></tr>
  <tr><td><a href="/logo.png">image</a></td></tr>
</table></body></html>"""


def test_extract_skips_chrome_and_finds_files():
    r = rules()
    links, files, skipped = LinkExtractor(r.cfg, r).extract(PAGE, START)
    assert set(links) == {"https://lolroms.com/?dir=Atari%2F5200", "https://lolroms.com/?dir=Atari%2FArticle"}
    assert {u: f.ext for u, f in files.items()} == {"https://lolroms.com/?file=Atari%2FGame.7z": ".7z",
                                                    "https://lolroms.com/get?id=5": "(none)"}
    assert skipped == 4


def test_extract_include_nav_still_ignores_sortbar_and_back():
    r = rules(ignore_nav=False)
    links, _, _ = LinkExtractor(r.cfg, r).extract(PAGE, START)
    assert "https://lolroms.com/?dir=Atari%2FNav" in links
    assert "https://lolroms.com/?dir=Atari%2FSort" not in links
    assert "https://lolroms.com/?dir=Atari%2FBack" not in links


def test_saved_as_places():
    """A folder's own place (a name, a path below its parent's place, or /path from the download
    folder) and where its subfolders go; everything below follows; keys ignore case."""
    from lolroms_sync.folder_map import FolderMap
    fm = FolderMap(
        places={"Acorn/Archimedes (2023)": "Archimedes", "SEGA/32X/Sub B": "/Sega/B only",
                "Atari/2600": "Extras/2600"},
        kids={"sega/32x": "/", "Nintendo": "/Sega/Nested"})
    assert fm.disk_folder("Acorn/Archimedes (2023)/Demos") == "Acorn/Archimedes/Demos"
    assert fm.disk_folder("SEGA/32X") == "SEGA/32X"                   # the folder itself stays put
    assert fm.disk_folder("SEGA/32X/Sub A/Deep") == "Sub A/Deep"      # its subfolders go to the base
    assert fm.disk_folder("SEGA/32X/Sub B") == "Sega/B only"          # a subfolder's own place wins
    assert fm.disk_folder("Nintendo/SNES") == "Sega/Nested/SNES"      # nested in another folder
    assert fm.disk_folder("Atari/2600/x") == "Atari/Extras/2600/x"    # a path below the parent's place
    assert fm.disk_folder("Atari/5200") == "Atari/5200"
    assert fm.kids_of("SEGA/32X") == "/" and fm.place_of("acorn/archimedes (2023)") == "Archimedes"


@pytest.mark.parametrize("value, kids, out", [
    ("  Archimedes ", False, "Archimedes"),
    ("/Sega//32X Extras/", False, "/Sega/32X Extras"),
    ("a\\b", False, "a/b"),
    ("", False, ""),
    ("/", True, "/"),
])
def test_clean_place(value, kids, out):
    from lolroms_sync.folder_map import clean_place
    assert clean_place(value, kids=kids) == out


@pytest.mark.parametrize("value, kids, error", [
    ("../x", False, "can't be . or .."),
    ("a*", False, "can't contain *"),
    ("/", False, "enter a folder name"),
])
def test_clean_place_rejects(value, kids, error):
    from lolroms_sync.folder_map import clean_place
    with pytest.raises(ValueError, match=re.escape(error)):
        clean_place(value, kids=kids)


def test_old_folder_map_rules_and_names_are_taken_over():
    from lolroms_sync.folder_map import from_old_settings
    places, kids = from_old_settings(
        [{"site": "Acorn/Archimedes (*)", "local": "Acorn/Archimedes"}, {"site": "SEGA (*)", "local": "Sega"}],
        {"Atari/2600": "VCS", "SEGA (1)": "SEGA"},
        ["", "Acorn", "Acorn/Archimedes (2023)", "Atari", "Atari/2600", "SEGA (1)", "SEGA (1)/Saturn"])
    assert places == {"Atari/2600": "VCS", "Acorn/Archimedes (2023)": "/Acorn/Archimedes", "SEGA (1)": "/SEGA"}
    assert kids == {}

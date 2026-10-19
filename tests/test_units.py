"""Unit tests: helpers, URL rules, link extraction."""
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
    # folders named after a set's date are saved without it; file names are left alone
    assert (r.rel_path("https://lolroms.com/?file=Acorn%2FRisc+PC+%28Flux%29+%2820230506-040449%29%2FX+%2820230506-040449%29.7z")
            == "Acorn/Risc PC (Flux)/X (20230506-040449).7z")


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

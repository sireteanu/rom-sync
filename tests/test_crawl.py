"""Integration tests: crawling the mock site (listing only)."""
from collections import Counter

from conftest import read_csv

ATARI_WITH_FILES = {"Atari/2600", "Atari/5200", "Atari/800/Flux"}


def test_lists_only_directories_with_files(site, run, tmp_path):
    assert run(f"{site.url}/?dir=Atari") == 0
    rows = read_csv(tmp_path / "out.csv")
    assert {r["name"] for r in rows} == ATARI_WITH_FILES
    txt = (tmp_path / "out.txt").read_text(encoding="utf-8").split()
    assert len(txt) == 3 and all(u.startswith(site.url + "/?dir=Atari") for u in txt)


def test_stays_in_scope_and_never_fetches_files(site, run):
    run(f"{site.url}/?dir=Atari")
    paths = site.paths()
    assert not site.file_requests(), "file links must never be requested when only listing"
    assert "/" not in paths, "must not escape to the site root"
    assert not [p for p in paths if "dir=" in p and "Atari" not in p], "must not leave Atari"
    dirs = Counter(p for p in paths if "dir=" in p)
    assert max(dirs.values()) == 1, f"sort variants must be fetched once: {dirs}"
    assert not [p for p in paths if "sort=" in p]


def test_survives_rate_limiting(site_factory, run, tmp_path):
    site = site_factory("throttle")
    assert run(f"{site.url}/?dir=Atari") == 0
    assert {r["name"] for r in read_csv(tmp_path / "out.csv")} == ATARI_WITH_FILES
    assert any(p for p, _ in site.requests)  # sanity


def test_max_pages_then_resume(site, run, tmp_path):
    url = f"{site.url}/?dir=Atari"
    run(url, "--max-pages", "2")
    assert len(read_csv(tmp_path / "out.csv")) < 3
    assert run(url) == 2, "an unfinished crawl must not be overwritten without --resume/--fresh"
    assert run(url, "--resume") == 0
    assert {r["name"] for r in read_csv(tmp_path / "out.csv")} == ATARI_WITH_FILES


def test_resume_refuses_a_different_url(site, run):
    run(f"{site.url}/?dir=Atari", "--max-pages", "1")
    assert run(f"{site.url}/?dir=Acorn", "--resume") == 2


def test_list_files_without_downloading(site, run, tmp_path):
    run(f"{site.url}/?dir=Atari", "--list-files")
    rows = read_csv(tmp_path / "out_files.csv")
    names = {r["file"] for r in rows}
    assert "Galaxian (USA).7z" in names and "Flux A.zip" in names
    assert "Disk Image.iso" not in names and "Readme.txt" not in names   # archives only
    assert all(r["status"] == "listed" for r in rows)
    assert not site.file_requests()


def test_types_all_includes_disc_images(site, run, tmp_path):
    run(f"{site.url}/?dir=Atari", "--list-files", "--types", "all")
    names = {r["file"] for r in read_csv(tmp_path / "out_files.csv")}
    assert "Disk Image.iso" in names


def test_reads_dates_and_sizes_from_table_layouts_too(site_factory, run, tmp_path):
    site = site_factory("tables")
    run(f"{site.url}/?dir=Atari", "--list-files")
    rows = read_csv(tmp_path / "out_files.csv")
    assert rows and all(r["site_date"] == "2025-11-17" and r["site_size"] for r in rows)


def test_reads_dates_and_sizes_from_lolroms_rows(site, run, tmp_path):
    run(f"{site.url}/?dir=Atari", "--list-files")
    rows = {r["file"]: r for r in read_csv(tmp_path / "out_files.csv")}
    assert rows["Big.7z"]["site_date"] == "2025-11-17" and rows["Big.7z"]["site_size"] == "2.86 MB"
    assert all(r["site_date"] and r["site_size"] for r in rows.values())

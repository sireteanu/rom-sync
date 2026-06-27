"""Automatic parallel downloads: find the server's limit, use it, re-check it, remember it."""
import threading

from conftest import read_csv

FAST = ["--dl-ramp-secs", "0.2", "--dl-confirm-secs", "0.5"]


def test_finds_and_uses_the_servers_limit(site_factory, run, tmp_path):
    site = site_factory("bulk")
    site.max_conc = 4
    dl = tmp_path / "roms"
    assert run(f"{site.url}/?dir=Atari", "--download", str(dl), *FAST, "--dl-probe-secs", "300") == 0
    rows = read_csv(tmp_path / "out_files.csv")
    assert all(r["status"] == "new" for r in rows), {r["status"] for r in rows}
    assert site.peak_active == 4, "should run as many downloads as the server allows"
    from dir_crawler.sync import Manifest
    m = Manifest(str(dl))
    assert m.get_meta("max_parallel:127.0.0.1") == "4"
    m.close()

    # next run starts straight at the remembered limit and never exceeds it
    again = site_factory("bulk")
    again.max_conc = 4
    run(f"{again.url}/?dir=Atari", "--download", str(dl), "--overwrite", *FAST,
        "--dl-probe-secs", "300", output="b.csv")
    assert again.rejected == 0 and again.peak_active == 4


def test_rechecks_and_rises_when_the_server_allows_more(site_factory, run, tmp_path):
    site = site_factory("bulk")
    site.max_conc = 2
    timer = threading.Timer(2.0, lambda: setattr(site, "max_conc", 6))
    timer.start()
    try:
        run(f"{site.url}/?dir=Atari", "--download", str(tmp_path / "roms"), *FAST, "--dl-probe-secs", "1")
    finally:
        timer.cancel()
    assert site.peak_active >= 5, f"peak {site.peak_active}: should have re-probed after the limit went up"
    assert all(r["status"] == "new" for r in read_csv(tmp_path / "out_files.csv"))


def test_fixed_worker_count(site_factory, run, tmp_path):
    site = site_factory("bulk")
    run(f"{site.url}/?dir=Atari", "--download", str(tmp_path / "roms"), "--dl-workers", "3")
    assert site.peak_active == 3


def test_daily_limit_pauses_and_resume_finishes(site, run, tmp_path):
    site.daily_limit = 3
    dl = tmp_path / "roms"
    url = f"{site.url}/?dir=Atari"
    assert run(url, "--download", str(dl)) == 0
    rows = read_csv(tmp_path / "out_files.csv")
    by = {}
    for r in rows:
        by[r["status"]] = by.get(r["status"], 0) + 1
    # downloads already under way when the limit hit may still finish; if the limit hits when a
    # single file is left, that file is marked failed instead of pausing (retried on --resume)
    assert by.get("new", 0) >= 3, by
    assert by.get("new", 0) + by.get("queued", 0) + by.get("failed", 0) == 7, by
    assert by.get("failed", 0) <= 1, by

    site.daily_limit = None                                  # "next day"
    assert run(url, "--download", str(dl), "--resume") == 0
    rows = read_csv(tmp_path / "out_files.csv")
    assert sum(1 for r in rows if r["status"] == "new") == 7

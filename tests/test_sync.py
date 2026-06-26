"""Sync behaviour: only new, incomplete or changed files are downloaded."""
import os
import shutil
import sys

from conftest import read_csv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from dir_crawler.sync import Manifest  # noqa: E402

URL_DIR = "?dir=Atari"
ADV = "Atari/2600/Adventure (USA).7z"
BIG = "Atari/5200/Big.7z"


def statuses(tmp_path, name="b_files.csv"):
    return {r["saved_as"]: r["status"] for r in read_csv(tmp_path / name)}


def fetched(site):
    return sorted({p.split("file=")[1] for p, _ in site.file_requests()})


def first_sync(site, run, dl):
    assert run(f"{site.url}/{URL_DIR}", "--download", str(dl)) == 0
    site.reset_log()


def test_changed_file_on_site_is_updated(site, run, tmp_path):
    dl = tmp_path / "roms"
    first_sync(site, run, dl)
    site.modify(ADV, "2026-01-05")                       # new date, same size
    assert run(f"{site.url}/{URL_DIR}", "--download", str(dl), output="b.csv") == 0
    assert fetched(site) == ["Atari%2F2600%2FAdventure+%28USA%29.7z"]
    assert (dl / ADV).read_bytes() == site.content(ADV)
    st = statuses(tmp_path)
    assert st[ADV] == "updated"
    assert sum(1 for v in st.values() if v == "up to date") == 6


def test_size_change_with_same_date_is_updated(site, run, tmp_path):
    dl = tmp_path / "roms"
    first_sync(site, run, dl)
    site.modify(ADV, "2025-11-17", grow=5000)            # same day, different size
    run(f"{site.url}/{URL_DIR}", "--download", str(dl), output="b.csv")
    assert (dl / ADV).read_bytes() == site.content(ADV)
    assert statuses(tmp_path)[ADV] == "updated"


def test_file_date_matches_site(site, run, tmp_path):
    dl = tmp_path / "roms"
    first_sync(site, run, dl)
    from email.utils import parsedate_to_datetime
    assert int(os.path.getmtime(dl / ADV)) == int(parsedate_to_datetime(site.last_modified(ADV)).timestamp())


def test_truncated_file_is_resumed_not_restarted(site, run, tmp_path):
    dl = tmp_path / "roms"
    first_sync(site, run, dl)
    with open(dl / BIG, "r+b") as f:
        f.truncate(1_000_000)
    run(f"{site.url}/{URL_DIR}", "--download", str(dl), output="b.csv")
    assert (dl / BIG).read_bytes() == site.content(BIG)
    assert [r for p, r in site.file_requests()] == ["bytes=1000000-"]
    assert statuses(tmp_path)[BIG] == "resumed"


def test_deleted_file_is_downloaded_again(site, run, tmp_path):
    dl = tmp_path / "roms"
    first_sync(site, run, dl)
    os.remove(dl / ADV)
    run(f"{site.url}/{URL_DIR}", "--download", str(dl), output="b.csv")
    assert (dl / ADV).exists() and len(site.file_requests()) == 1
    assert statuses(tmp_path)[ADV] == "new"


def test_existing_files_from_elsewhere_are_adopted(site, run, tmp_path):
    dl = tmp_path / "roms"
    first_sync(site, run, dl)
    os.remove(dl / ".dir_crawler.db")                     # e.g. copied from another PC
    for extra in ("-wal", "-shm"):
        if os.path.exists(str(dl / ".dir_crawler.db") + extra):
            os.remove(str(dl / ".dir_crawler.db") + extra)
    with open(dl / BIG, "r+b") as f:                       # one of them is incomplete
        f.truncate(2_000_000)
    run(f"{site.url}/{URL_DIR}", "--download", str(dl), output="b.csv")
    st = statuses(tmp_path)
    assert st[BIG] == "resumed"
    assert sum(1 for v in st.values() if v == "already in folder") == 6
    assert [r for p, r in site.file_requests()] == ["bytes=2000000-"]
    site.reset_log()
    run(f"{site.url}/{URL_DIR}", "--download", str(dl), output="c.csv")
    assert site.file_requests() == []
    assert set(statuses(tmp_path, "c_files.csv").values()) == {"up to date"}


def test_part_file_restarts_if_file_changed_meanwhile(site, run, tmp_path):
    dl = tmp_path / "roms"
    first_sync(site, run, dl)
    old_etag = site.etag(BIG)
    shutil.move(dl / BIG, str(dl / BIG) + ".part")           # pretend it was interrupted...
    with open(str(dl / BIG) + ".part", "r+b") as f:
        f.truncate(1_000_000)
    m = Manifest(str(dl))
    m.part_started(BIG, "", old_etag)
    m.close()
    site.modify(BIG, "2026-02-01")                            # ...and the site changed it since
    run(f"{site.url}/{URL_DIR}", "--download", str(dl), output="b.csv")
    hdrs = [h for p, h in site.file_headers() if "Big.7z" in p]
    assert hdrs[0].get("If-Range") == old_etag
    assert (dl / BIG).read_bytes() == site.content(BIG), "must restart, not append to the old version"


def test_sites_without_listing_dates_use_conditional_requests(site_factory, run, tmp_path):
    site = site_factory("nometa")
    dl = tmp_path / "roms"
    first_sync(site, run, dl)
    run(f"{site.url}/{URL_DIR}", "--download", str(dl), output="b.csv")
    hdrs = [h for _, h in site.file_headers()]
    assert len(hdrs) == 7 and all("If-None-Match" in h or "If-Modified-Since" in h for h in hdrs)
    assert set(statuses(tmp_path).values()) == {"up to date"}
    site.reset_log()
    site.modify(ADV, "2026-03-01")
    run(f"{site.url}/{URL_DIR}", "--download", str(dl), output="c.csv")
    assert (dl / ADV).read_bytes() == site.content(ADV)
    assert statuses(tmp_path, "c_files.csv")[ADV] == "updated"


def test_check_remote_asks_server_about_every_file(site, run, tmp_path):
    dl = tmp_path / "roms"
    first_sync(site, run, dl)
    run(f"{site.url}/{URL_DIR}", "--download", str(dl), "--check-remote", output="b.csv")
    assert len(site.file_requests()) == 7
    assert set(statuses(tmp_path).values()) == {"up to date"}


def local_day(path):
    import time
    return time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(path)))


def test_file_date_is_the_websites_date_even_if_server_time_differs(site, run, tmp_path):
    site.lm_shift_days = 40                     # server says a different day than the listing
    dl = tmp_path / "roms"
    run(f"{site.url}/{URL_DIR}", "--download", str(dl))
    assert local_day(dl / ADV) == "2025-11-17"
    import time
    assert time.localtime(os.path.getmtime(dl / ADV)).tm_hour == 12


def test_updated_file_gets_the_new_website_date(site, run, tmp_path):
    dl = tmp_path / "roms"
    first_sync(site, run, dl)
    site.modify(ADV, "2026-03-14")
    run(f"{site.url}/{URL_DIR}", "--download", str(dl), output="b.csv")
    assert local_day(dl / ADV) == "2026-03-14"
    assert local_day(dl / BIG) == "2025-11-17"   # untouched files keep theirs


def test_dates_of_files_already_in_folder_are_corrected_without_downloading(site, run, tmp_path):
    dl = tmp_path / "roms"
    first_sync(site, run, dl)
    for rel in (ADV, BIG):
        os.utime(dl / rel, (1_000_000_000, 1_000_000_000))   # 2001 - e.g. copied from elsewhere
    run(f"{site.url}/{URL_DIR}", "--download", str(dl), output="b.csv")
    assert site.file_requests() == []
    assert local_day(dl / ADV) == "2025-11-17" and local_day(dl / BIG) == "2025-11-17"


def test_files_saved_before_dates_were_readable_get_dated_on_next_sync(site_factory, run, tmp_path):
    """The real-world case: v3.2 couldn't read lolroms' dates, so files kept their download time."""
    dl = tmp_path / "roms"
    first = site_factory("nometa")
    run(f"{first.url}/{URL_DIR}", "--download", str(dl))
    for rel in (ADV, BIG):
        os.utime(dl / rel, (1_900_000_000, 1_900_000_000))     # "download time"
    second = site_factory()
    run(f"{second.url}/{URL_DIR}", "--download", str(dl), output="b.csv")
    assert second.file_requests() == []
    assert local_day(dl / ADV) == "2025-11-17" and local_day(dl / BIG) == "2025-11-17"
    assert set(statuses(tmp_path).values()) == {"up to date"}

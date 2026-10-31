"""The site going down during a sync: downloads wait for it instead of failing."""
import threading
import time

from conftest import EXPECTED_ATARI, file_status, on_disk, scan, select, sync, wait


def start_sync(c):
    scan(c)
    select(c, "Atari")
    assert c.post("/api/sync").status_code == 200


def test_downloads_wait_for_the_site_and_carry_on(site, app_factory):
    c = app_factory(site)
    site.down_after = 2                                    # goes down after two files
    start_sync(c)
    seen = None
    t0 = time.time()
    while time.time() - t0 < 30 and seen is None:
        st = c.get("/api/status").json()
        if st.get("sync") and st["sync"].get("outage"):
            seen = st["sync"]["outage"]
        time.sleep(0.1)
    assert seen and "522" in seen["error"] and seen["gives_up"] > seen["since"]
    time.sleep(1)
    site.down = None                                        # back after a while
    st = wait(c)
    assert on_disk(c.data) == EXPECTED_ATARI
    assert st["sync"]["failed"] == 0 and st["sync"]["new"] == 7 and not st["sync"]["paused"]
    msgs = [line["msg"] for line in st["log"]]
    assert any("isn't answering" in m for m in msgs) and any("is back" in m for m in msgs)


def test_gives_up_after_the_wait_and_the_next_sync_continues(site, app_factory):
    c = app_factory(site, settings={"outage_wait_hours": 0.0005})     # ~2 s
    site.down_after = 1
    start_sync(c)
    st = wait(c)
    assert st["sync"]["failed"] == 0, "nothing is marked failed because of the site"
    assert "has not answered" in st["sync"]["paused"]
    assert len(on_disk(c.data)) < 7
    site.down = None
    st = sync(c)
    assert on_disk(c.data) == EXPECTED_ATARI and st["sync"]["failed"] == 0


def test_a_broken_file_still_fails_after_three_retries_when_the_site_is_up(site_factory, app_factory):
    site = site_factory("brokenfile")
    c = app_factory(site)
    start_sync(c)
    st = wait(c)
    assert st["sync"]["failed"] == 1 and st["sync"]["new"] == 7
    assert file_status(c, "Atari/5200")["Broken.7z"] is None
    assert not any("isn't answering" in line["msg"] for line in st["log"])


def test_waiting_can_be_stopped(site, app_factory):
    c = app_factory(site)
    site.down_after = 0
    start_sync(c)
    t0 = time.time()
    while time.time() - t0 < 30:
        s = c.get("/api/status").json().get("sync") or {}
        if s.get("outage"):
            break
        time.sleep(0.1)
    threading.Timer(0.5, lambda: c.post("/api/stop")).start()
    st = wait(c, 20)
    assert st["job"]["phase"] == "stopped" and st["sync"]["failed"] == 0


def _log(c):
    return " ".join(l["msg"] for l in c.get("/api/status", params={"log_since": 0}).json()["log"])


def test_a_scan_waits_for_the_site_and_carries_on(site, app_factory):
    c = app_factory(site)
    site.down = 522                                         # down before the scan starts
    assert c.post("/api/scan", json={"root": ""}).status_code == 200
    time.sleep(6)                                           # past the page's own retries
    assert c.get("/api/status").json()["busy"]              # waiting, not given up
    site.down = None
    st = wait(c)
    assert st["catalog"]["last_full_scan"]["status"] == "done"
    assert st["catalog"]["dirs"] >= 8
    log = _log(c)
    assert "can't reach the site (HTTP 522) - the scan waits up to 30 minutes" in log and "reached again" in log


def test_a_scan_gives_up_without_changing_the_catalog(site, app_factory):
    from conftest import kids
    c = app_factory(site, settings={"scan_wait_minutes": 0.02})       # ~1 s
    scan(c)
    before = kids(c, "Atari")
    site.down = 522
    st = scan(c)
    assert st["catalog"]["last_full_scan"]["status"] == "site down"
    assert kids(c, "Atari") == before                                 # nothing forgotten
    assert "still can't be reached after" in _log(c)


def test_a_sync_is_skipped_when_its_refresh_gives_up(site, app_factory):
    c = app_factory(site, settings={"scan_wait_minutes": 0.02})
    scan(c)
    select(c, "Atari")
    site.down = 522
    site.reset_log()
    sync(c)
    assert site.file_requests() == []
    assert "Sync skipped: the site can't be reached" in _log(c)
    site.down = None
    sync(c)
    assert on_disk(c.data) == EXPECTED_ATARI


def test_site_trouble_tells_outages_from_page_errors():
    from lolroms_sync.scanner import site_trouble
    assert site_trouble("timed out") and site_trouble("HTTP 522") and site_trouble("ConnectError: refused")
    assert not site_trouble("HTTP 404") and not site_trouble("HTTP 429")

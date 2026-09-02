import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mock_site import MockSite  # noqa: E402

import time  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from lolroms_sync.engine import Engine  # noqa: E402
from lolroms_sync.web import create_app  # noqa: E402

# fast DownloadGate timings for tests (seconds)
TUNING = {"ramp": 0.2, "confirm": 0.5, "probe": 300, "retry": 0.05, "outage_check": 0.3}


@pytest.fixture
def site_factory():
    sites = []

    def make(modes: str = "") -> MockSite:
        s = MockSite(modes).start()
        sites.append(s)
        return s

    yield make
    for s in sites:
        s.stop()


@pytest.fixture
def site(site_factory):
    return site_factory()


@pytest.fixture
def app_factory(tmp_path):
    """app_factory(site, password=None, data_locked=False, tuning=None, name="config") -> a started
    TestClient; c.data is the download folder. Several apps can share one download folder by
    passing different config names."""
    clients = []

    def make(site, password=None, data_locked=False, tuning=None, name="config", settings=None):
        cfg, data = tmp_path / name, tmp_path / "data"
        app = create_app(lambda: Engine(str(cfg), str(data), site.url + "/", data_locked=data_locked,
                                        scan_delay=0, tuning=tuning or TUNING), password=password)
        c = TestClient(app)
        c.__enter__()
        clients.append(c)
        c.data = data
        if settings:
            assert c.post("/api/settings", json=settings).status_code == 200
        return c

    yield make
    for c in clients:
        c.__exit__(None, None, None)


EXPECTED_ATARI = {
    "Atari/2600/Adventure (USA).7z", "Atari/2600/Pitfall! (USA).7z", "Atari/2600/Combat_ Tank _Plus_.7z",
    "Atari/5200/Galaxian (USA).7z", "Atari/5200/Big.7z", "Atari/800/Flux/Flux A.zip", "Atari/800/Flux/_CON.7z",
}


def wait(c, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = c.get("/api/status").json()
        if not st["busy"]:
            return st
        time.sleep(0.1)
    raise AssertionError("job did not finish")


def scan(c, root=""):
    assert c.post("/api/scan", json={"root": root}).status_code == 200
    return wait(c)


def tree(c, path=""):
    return c.get("/api/tree", params={"path": path}).json()


def kids(c, path=""):
    return {k["name"]: k for k in tree(c, path)["children"]}


def select(c, path, include=True):
    assert c.post("/api/select", json={"path": path, "include": include}).status_code == 200


def sync(c):
    assert c.post("/api/sync").status_code == 200
    return wait(c)


def on_disk(folder):
    out = set()
    for root, _, names in os.walk(folder):
        for n in names:
            if not n.startswith(".dir_crawler.db"):
                out.add(os.path.relpath(os.path.join(root, n), folder).replace(os.sep, "/"))
    return out


def file_status(c, folder):
    """{file name: what the last sync did with it} for one folder."""
    return {f["name"]: f["local"] for f in c.get("/api/files", params={"path": folder}).json()["files"]}

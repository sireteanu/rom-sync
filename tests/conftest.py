import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mock_site import MockSite  # noqa: E402

from dir_crawler.cli import main  # noqa: E402


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
def run(tmp_path):
    """Run the CLI in-process: run(url, *args) -> exit code. Output files go to tmp_path."""
    def _run(url, *args, output="out.csv"):
        return main([url, "-o", str(tmp_path / output), "--plain", "--delay", "0", *args])
    return _run


def read_csv(path):
    import csv
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))

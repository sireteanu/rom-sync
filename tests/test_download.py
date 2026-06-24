"""Integration tests: downloading archives from the mock site."""
import os
import signal
import subprocess
import sys
import time

from conftest import ROOT, read_csv
from mock_site import content

EXPECTED = {
    "Atari/2600/Adventure (USA).7z",
    "Atari/2600/Pitfall! (USA).7z",
    "Atari/2600/Combat_ Tank _Plus_.7z",      # ':' '<' '>' made Windows-safe
    "Atari/5200/Galaxian (USA).7z",
    "Atari/5200/Big.7z",
    "Atari/800/Flux/Flux A.zip",
    "Atari/800/Flux/_CON.7z",                 # reserved Windows name
}


def files_under(folder):
    out = set()
    for root, _, names in os.walk(folder):
        for n in names:
            if n.startswith(".dir_crawler.db"):
                continue                          # the sync manifest
            out.add(os.path.relpath(os.path.join(root, n), folder).replace(os.sep, "/"))
    return out


def source_name(rel):
    return {"Atari/2600/Combat_ Tank _Plus_.7z": "Atari/2600/Combat: Tank <Plus>.7z",
            "Atari/800/Flux/_CON.7z": "Atari/800/Flux/CON.7z"}.get(rel, rel)


def test_downloads_archives_into_mirrored_folders(site, run, tmp_path):
    dl = tmp_path / "roms"
    run(f"{site.url}/?dir=Atari", "--download", str(dl))
    assert files_under(dl) == EXPECTED
    for rel in EXPECTED:
        assert (dl / rel).read_bytes() == content(source_name(rel)), rel
    rows = {r["file"]: r for r in read_csv(tmp_path / "out_files.csv")}
    assert rows["Big.7z"]["status"] == "new"
    assert rows["Big.7z"]["site_date"] == "2025-11-17" and rows["Big.7z"]["site_size"] == "2.86 MB"
    assert not any("Acorn" in p or "Arcade" in p for p in site.paths())


def test_rejects_web_page_served_instead_of_file(site_factory, run, tmp_path):
    site = site_factory("limitfile")
    dl = tmp_path / "roms"
    run(f"{site.url}/?dir=Atari", "--download", str(dl))
    assert not (dl / "Atari/5200/Limited.7z").exists()
    assert not (dl / "Atari/5200/Limited.7z.part").exists()
    row = next(r for r in read_csv(tmp_path / "out_files.csv") if r["file"] == "Limited.7z")
    assert row["status"] == "failed" and "limit" in row["error"].lower()
    assert files_under(dl) == EXPECTED, "other files still download"
    errors = read_csv(tmp_path / "out_errors.csv")
    assert any("Limited.7z" in e["url"] for e in errors)


def test_resumes_dropped_transfer_with_range(site_factory, run, tmp_path):
    site = site_factory("drop")
    dl = tmp_path / "roms"
    run(f"{site.url}/?dir=Atari", "--download", str(dl))
    assert (dl / "Atari/5200/Big.7z").read_bytes() == content("Atari/5200/Big.7z")
    ranges = [r for p, r in site.file_requests() if "Big.7z" in p]
    assert ranges[0] is None and all(r and r.startswith("bytes=") for r in ranges[1:]), ranges
    assert len(ranges) == 3


def test_skips_files_already_downloaded(site_factory, run, tmp_path):
    dl = tmp_path / "roms"
    first = site_factory()
    run(f"{first.url}/?dir=Atari", "--download", str(dl))
    second = site_factory()
    assert run(f"{second.url}/?dir=Atari", "--download", str(dl), output="b.csv") == 0
    assert second.file_requests() == [], "nothing changed, so nothing is downloaded"
    rows = read_csv(tmp_path / "b_files.csv")
    assert {r["status"] for r in rows} == {"up to date"}


def test_ctrl_c_keeps_part_file_and_resume_finishes_it(site_factory, tmp_path):
    site = site_factory("slowfile")
    dl = tmp_path / "roms"
    out = tmp_path / "out.csv"
    cmd = [sys.executable, os.path.join(ROOT, "dir-crawler.py"), f"{site.url}/?dir=Atari",
           "-o", str(out), "--download", str(dl), "--plain", "--delay", "0"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    part = dl / "Atari/5200/Big.7z.part"
    deadline = time.time() + 20
    while time.time() < deadline and not (part.exists() and part.stat().st_size > 500_000):
        time.sleep(0.1)
    assert part.exists(), "download never started"
    p.send_signal(signal.SIGINT)
    log = p.communicate(timeout=30)[0].decode("utf-8", "replace")
    assert "Interrupted" in log
    partial = part.stat().st_size
    assert 0 < partial < len(content("Atari/5200/Big.7z"))

    r = subprocess.run(cmd + ["--resume"], capture_output=True, timeout=60)
    assert r.returncode == 0, r.stderr.decode()
    assert (dl / "Atari/5200/Big.7z").read_bytes() == content("Atari/5200/Big.7z")
    resumed = [rng for path, rng in site.file_requests() if "Big.7z" in path][-1]
    assert resumed == f"bytes={partial}-"

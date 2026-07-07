"""Trust pack (Phase 4): bench runner writes reports + manifest, gates hold,
skips are honest, and the public /trust page renders the numbers."""
import json
import os

import pytest


def test_dataset_free_suites_pass_and_write_reports(tmp_path, monkeypatch):
    from bench import run as bench_run
    monkeypatch.setattr(bench_run, "REPORT_DIR", str(tmp_path))
    monkeypatch.setattr(bench_run, "MANIFEST", str(tmp_path / "latest.json"))
    results = bench_run.run_suites(["protected", "credential", "speed"])
    for name in ("protected", "credential", "speed"):
        status, report = results[name]
        assert status == "ok" and report["gate"] == "PASS", (name, report)
        assert os.path.exists(tmp_path / f"{name}.json")
    manifest = json.loads((tmp_path / "latest.json").read_text())
    assert all(manifest["suites"][n]["status"] == "ok"
               for n in ("protected", "credential", "speed"))


def test_pad_suite_skips_without_attack_data(tmp_path, monkeypatch):
    from bench import run as bench_run
    monkeypatch.setattr(bench_run, "REPORT_DIR", str(tmp_path))
    monkeypatch.setattr(bench_run, "MANIFEST", str(tmp_path / "latest.json"))
    monkeypatch.chdir(tmp_path)                      # definitely no pad_data/ here
    status, reason = bench_run.run_suites(["pad"])["pad"]
    assert status == "skipped" and "pad_data" in reason
    manifest = json.loads((tmp_path / "latest.json").read_text())
    assert manifest["suites"]["pad"]["status"] == "skipped"


def test_trust_page_renders_measured_numbers(client):
    r = client.get("/trust")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Trust Center" in html
    assert "crypto-erase" in html.lower() or "permanently unreadable" in html
    # the committed reports feed real numbers into the page
    if os.path.exists(os.path.join("static", "trust", "latest.json")):
        assert "TAR delta" in html and "Offline QR credential size" in html

"""The protected-domain accuracy gate (spec 5.2): < 1% absolute TAR delta at the
operating FAR. Must PASS for protection to stay default-on."""
import os


def test_gate_passes(tmp_path):
    from bench.protected import run
    report = run(pairs=2000, out=str(tmp_path / "report.json"))
    assert report["gate"] == "PASS"
    assert report["tar_delta_abs"] < 0.01
    assert report["max_abs_score_diff"] < 1e-4
    assert os.path.exists(tmp_path / "report.json")

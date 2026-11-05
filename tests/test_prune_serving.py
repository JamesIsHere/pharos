"""Serving keeps two versions (D133): the one CURRENT names and the one it replaced."""

from pharos.publish import prune_serving, serving_dir

RUNS = ["20261101T053007Z", "20261102T053012Z", "20261103T053011Z", "20261104T053016Z"]


def test_keeps_only_the_named_versions(tmp_path, monkeypatch):
    monkeypatch.setenv("PHAROS_TEST_MODE", "1")
    monkeypatch.setenv("PHAROS_DATA_ROOT", str(tmp_path))
    for r in RUNS:
        (serving_dir() / r).mkdir(parents=True)
        (serving_dir() / r / "observations.parquet").write_bytes(b"x")
    (serving_dir() / ".building-20261105T000000Z").mkdir()       # not a version: untouched
    (serving_dir() / "CURRENT").write_text(RUNS[-1], encoding="utf-8")
    assert prune_serving({RUNS[-1], RUNS[-2]}) == RUNS[:2]
    assert sorted(p.name for p in serving_dir().iterdir()) == [
        ".building-20261105T000000Z", RUNS[2], RUNS[3], "CURRENT"]


def test_no_serving_folder_is_a_no_op(tmp_path, monkeypatch):
    monkeypatch.setenv("PHAROS_TEST_MODE", "1")
    monkeypatch.setenv("PHAROS_DATA_ROOT", str(tmp_path))
    assert prune_serving({"20261105T000000Z"}) == []

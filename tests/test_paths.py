"""The data-root guard is a preventive control: these tests prove it refuses every
wrong root, and that its one exception (test mode) can't be used outside temp.

Every test is hermetic: .env loading is switched off and the expected root is
pointed at a throwaway folder, so the result never depends on this machine."""

import pytest

from pharos import paths
from pharos.paths import DataRootError, PROJECT_ROOT, data_root


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "load_dotenv", lambda *a, **k: None)
    monkeypatch.delenv("PHAROS_DATA_ROOT", raising=False)
    monkeypatch.delenv("PHAROS_TEST_MODE", raising=False)
    expected = tmp_path / "expected_data"
    expected.mkdir()
    monkeypatch.setattr(paths, "EXPECTED_ROOT", expected)
    return expected


def test_missing_setting_is_refused():
    with pytest.raises(DataRootError, match="not set"):
        data_root()


def test_expected_root_is_accepted(monkeypatch, isolated):
    monkeypatch.setenv("PHAROS_DATA_ROOT", str(isolated))
    assert data_root() == isolated


def test_wrong_root_is_refused(monkeypatch):
    monkeypatch.setenv("PHAROS_DATA_ROOT", str(PROJECT_ROOT / "src"))
    with pytest.raises(DataRootError, match="must be"):
        data_root()


def test_expected_root_that_does_not_exist_is_refused(monkeypatch, isolated):
    isolated.rmdir()
    monkeypatch.setenv("PHAROS_DATA_ROOT", str(isolated))
    with pytest.raises(DataRootError, match="does not exist"):
        data_root()


def test_test_mode_allows_a_temp_folder(monkeypatch, tmp_path):
    scratch = tmp_path / "scratch_root"
    scratch.mkdir()
    monkeypatch.setenv("PHAROS_TEST_MODE", "1")
    monkeypatch.setenv("PHAROS_DATA_ROOT", str(scratch))
    assert data_root() == scratch


def test_test_mode_does_not_allow_a_non_temp_folder(monkeypatch):
    monkeypatch.setenv("PHAROS_TEST_MODE", "1")
    monkeypatch.setenv("PHAROS_DATA_ROOT", str(PROJECT_ROOT / "src"))
    with pytest.raises(DataRootError, match="must be"):
        data_root()


def test_temp_folder_without_test_mode_is_refused(monkeypatch, tmp_path):
    scratch = tmp_path / "scratch_root"
    scratch.mkdir()
    monkeypatch.setenv("PHAROS_DATA_ROOT", str(scratch))
    with pytest.raises(DataRootError, match="must be"):
        data_root()

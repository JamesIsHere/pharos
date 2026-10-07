"""Where Pharos keeps data. Every data path in the codebase comes from data_root().

The data root must be <project>/data (design.md D16). Anything else fails loudly,
so a stale .env or a stray setting can never send data to another folder.
The one exception is the test suite, which sets PHAROS_TEST_MODE=1 and points
the root at a throwaway folder under the system temp directory.
"""

import os
import tempfile
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXPECTED_ROOT = PROJECT_ROOT / "data"


class DataRootError(RuntimeError):
    pass


def data_root() -> Path:
    load_dotenv(PROJECT_ROOT / ".env")

    raw = os.environ.get("PHAROS_DATA_ROOT")
    if not raw:
        raise DataRootError(
            f"PHAROS_DATA_ROOT is not set. Add it to {PROJECT_ROOT / '.env'}"
        )

    root = Path(raw).resolve()

    if root != EXPECTED_ROOT and not _is_test_root(root):
        raise DataRootError(
            f"PHAROS_DATA_ROOT is {root}, but it must be {EXPECTED_ROOT}"
        )
    if not root.is_dir():
        raise DataRootError(f"Data root {root} does not exist")

    return root


def _is_test_root(root: Path) -> bool:
    if os.environ.get("PHAROS_TEST_MODE") != "1":
        return False
    return root.is_relative_to(Path(tempfile.gettempdir()).resolve())

from __future__ import annotations

from pathlib import Path
import shutil

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def java_fixture(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    shutil.copytree(PROJECT_ROOT / "evaluation_fixtures" / "null-customer-name", root)
    return root

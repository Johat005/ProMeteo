"""Test package for ProMeteo.

The source tree is not installed in the test environment, so make ``src``
importable when the suite is run with ``python -m unittest discover -s tests``.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

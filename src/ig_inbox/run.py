"""Console entry point: `ig-inbox` (or `python -m ig_inbox.run`).

Thin wrapper around the pipeline so the packaged script name stays stable.
"""

from __future__ import annotations

import sys

from .pipeline import main as _main


def main() -> int:
    return _main()


if __name__ == "__main__":
    sys.exit(main())

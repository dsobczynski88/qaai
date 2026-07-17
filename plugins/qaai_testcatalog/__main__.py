"""Re-render the test-catalog HTML from a saved test_catalog.json.

Usage:
    python -m qaai_testcatalog logs/test-catalog/test_catalog.json [-o out.html]

Generating the catalog in the first place is done via the pytest plugin:
    uv run pytest --collect-only --test-catalog
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from qaai_testcatalog.render import write_catalog_from_json


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("json_path", help="Path to test_catalog.json")
    ap.add_argument("-o", "--output", default=None,
                    help="Output HTML path (default: test_catalog.html next to the JSON)")
    args = ap.parse_args(argv)

    src = Path(args.json_path)
    if not src.exists():
        print(f"error: {src} does not exist", file=sys.stderr)
        return 2

    out = write_catalog_from_json(src, args.output)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

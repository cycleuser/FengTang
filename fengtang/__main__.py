"""Allow running as `python -m fengtang`."""

import sys

from fengtang.cli.main import main

if __name__ == "__main__":
    sys.exit(main())

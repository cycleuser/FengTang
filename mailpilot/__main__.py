"""Allow running as `python -m mailpilot`."""

import sys

from mailpilot.cli.main import main

if __name__ == "__main__":
    sys.exit(main())

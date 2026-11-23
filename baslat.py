"""Starts the menu. Works with an installed Python and with the Windows embeddable package
(whose ._pth file keeps the current folder off sys.path)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from reqtestgen.menu import run_menu  # noqa: E402

if __name__ == "__main__":
    sys.exit(run_menu())

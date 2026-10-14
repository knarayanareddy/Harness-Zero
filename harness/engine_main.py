"""
Harness-Zero Standalone Engine Entrypoint for Binary Packaging.
"""
import sys
from pathlib import Path

# Add bundle directory to sys.path
if getattr(sys, "frozen", False):
    bundle_dir = Path(sys._MEIPASS)
    sys.path.insert(0, str(bundle_dir))

from harness.cli import main

if __name__ == "__main__":
    main()

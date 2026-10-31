"""
Harness-Zero Standalone Engine Entrypoint for Binary Packaging.
"""
import sys
from pathlib import Path

# Add bundle directory to sys.path
if getattr(sys, "frozen", False):
    bundle_dir = Path(sys._MEIPASS)
    sys.path.insert(0, str(bundle_dir))

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--harness-process-supervisor":
        from harness.sdk.process_supervisor import supervisor_main

        raise SystemExit(supervisor_main(sys.argv[2:]))
    else:
        from harness.cli import main

        main()

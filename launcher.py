"""Deprecated shim — the app now lives in the bpa_portal package.

Kept so existing muscle memory (`python launcher.py`) keeps working.
Prefer `python -m bpa_portal` or the installed `bpa-portal` command.
"""
from bpa_portal.launcher import main

if __name__ == "__main__":
    main()

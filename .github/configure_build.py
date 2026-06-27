#!/usr/bin/env python3
"""Configure the release build variant before PyInstaller runs.

Usage: python .github/configure_build.py <debug|prod> [settings_path]

The public settings ship with ``DEBUG = True`` (the debug variant). For the
production variant this flips that single line to ``DEBUG = False``, which turns
on the WhiteNoise static-file pipeline wired in gcforge/settings.py. Run from the
repository root (as the release workflow does). ``settings_path`` defaults to
``gcforge/settings.py`` and exists mainly for testing. Stdlib only.
"""
import re
import sys
from pathlib import Path

DEFAULT_SETTINGS = Path("gcforge/settings.py")


def main() -> int:
    variant = sys.argv[1] if len(sys.argv) > 1 else "debug"
    target = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_SETTINGS
    if variant not in ("debug", "prod"):
        print(f"ERROR: unknown variant {variant!r} (expected 'debug' or 'prod')",
              file=sys.stderr)
        return 2

    if variant == "debug":
        print("Build variant: debug (DEBUG=True, unchanged)")
        return 0

    text = target.read_text(encoding="utf-8")
    new_text, count = re.subn(
        r"^DEBUG = True$", "DEBUG = False", text, count=1, flags=re.MULTILINE
    )
    if count != 1:
        print(
            f"ERROR: expected exactly one 'DEBUG = True' line to flip in "
            f"{target}; found {count}.",
            file=sys.stderr,
        )
        return 1
    target.write_text(new_text, encoding="utf-8")
    print("Build variant: prod (DEBUG=False)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

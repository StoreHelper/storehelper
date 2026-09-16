"""Reject a release tag that does not identify this exact project version."""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--tag")
    mode.add_argument("--testpypi-rehearsal", action="store_true")
    args = parser.parse_args()
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["project"]
    version = project["version"]
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:(?:a|b|rc)[0-9]+|\.dev[0-9]+)?", version):
        print(f"Unsupported release version: {version!r}", file=sys.stderr)
        return 1
    if args.testpypi_rehearsal:
        if ".dev" not in version:
            print("TestPyPI rehearsal requires a unique .devN version.", file=sys.stderr)
            return 1
        print(f"Validated TestPyPI rehearsal {version}")
        return 0
    if args.tag != f"v{version}":
        print(f"Tag {args.tag!r} does not match project version v{version}", file=sys.stderr)
        return 1
    print(f"Validated {project['name']} {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Render installed service files without shell/sed/XML interpolation."""
from __future__ import annotations

import argparse
import plistlib
import re
from pathlib import Path

PLACEHOLDER = re.compile(r"\{\{([A-Z_]+)\}\}")


def render(template: Path, values: dict[str, str], *, plist: bool) -> bytes:
    def replace(text: str, *, unit: bool = False) -> str:
        def value(match: re.Match[str]) -> str:
            key = match.group(1)
            replacement = values[key]
            if any(c in replacement for c in "\r\n\0"):
                raise ValueError(f"Control characters are not supported in {key}")
            if unit:
                # WorkingDirectory/EnvironmentFile take literal paths; quotes
                # become part of them. ExecStart arguments use word unquoting.
                replacement = replacement.replace('%', '%%')
                if key in {"REPO_PATH", "ENV_PATH"}:
                    return replacement + ("/" if key == "REPO_PATH" else "")
                replacement = replacement.replace('$', '$$')
                replacement = replacement.replace("\\", "\\\\").replace('"', '\\"')
                return '"' + replacement + '"'
            return replacement
        return PLACEHOLDER.sub(value, text)

    if plist:
        def walk(obj):
            if isinstance(obj, str):
                return replace(obj)
            if isinstance(obj, list):
                return [walk(item) for item in obj]
            if isinstance(obj, dict):
                return {key: walk(item) for key, item in obj.items()}
            return obj
        return plistlib.dumps(walk(plistlib.loads(template.read_bytes())), sort_keys=False)
    return replace(template.read_text(encoding="utf-8"), unit=True).encode("utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=["plist", "systemd"])
    parser.add_argument("template", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--set", action="append", default=[])
    args = parser.parse_args()
    values = dict(pair.split("=", 1) for pair in args.set)
    args.output.write_bytes(render(args.template, values, plist=args.kind == "plist"))


if __name__ == "__main__":
    main()

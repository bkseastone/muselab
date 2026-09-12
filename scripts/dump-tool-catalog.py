#!/usr/bin/env python3
"""Print the bundled CLI catalog observed in an isolated offline probe.

The probe uses a localhost model fixture and disposable files. It never loads
provider credentials or sends a real model request. The init catalog depends
on CLI settings and environment; this snapshot is not a universal tool list.
Run after an SDK bump and review differences against docs/tool-catalog.txt.
"""
import json
from pathlib import Path
import subprocess
import sys

probe = Path(__file__).with_name("probe-checkpoint-offline.py")
result = subprocess.run(
    [sys.executable, str(probe), "--all-tools"],
    capture_output=True, text=True, timeout=90,
)
if result.returncode:
    sys.exit("isolated CLI capability probe failed; run the probe directly for its safe status")
try:
    report = json.loads(result.stdout)
    assert report["localhost_only_model"] and report["sdk_result_ok"]
    names = report["builtin_tools"]
    assert isinstance(names, list) and all(isinstance(name, str) for name in names)
except (ValueError, KeyError, AssertionError):
    sys.exit("isolated CLI capability probe returned an invalid report")
print("\n".join(sorted(name for name in names if not name.startswith("mcp__"))))

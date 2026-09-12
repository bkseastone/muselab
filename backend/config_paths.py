"""Locations of runtime-editable configuration, separate from application code.

Set selectors in the process environment (before import). Native installs keep
repo-local defaults; images put this directory inside their sessions volume.
"""
import os
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = Path(os.environ.get("MUSELAB_CONFIG_DIR") or REPO_DIR).expanduser()
ENV_PATH = Path(os.environ.get("MUSELAB_ENV_PATH") or CONFIG_DIR / ".env").expanduser()
MCP_CONFIG_PATH = CONFIG_DIR / "mcp.json"
PROVIDER_OVERRIDES_PATH = CONFIG_DIR / "provider_overrides.json"

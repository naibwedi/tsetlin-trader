"""Operational plumbing: run fingerprints and failure alerts."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import urllib.request
from importlib import metadata
from pathlib import Path


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], stderr=subprocess.DEVNULL, text=True
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unavailable"


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _version(package: str) -> str | None:
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        return None


def fingerprint(prices_path: Path) -> dict:
    """Everything needed to say exactly what produced a decision."""
    return {
        "code_commit": _git_commit(),
        "prices_sha256": _sha256(prices_path),
        "packages": {p: _version(p) for p in ("tsetlin-trader", "logic-alpha-tm", "tmu", "alpaca-py", "numpy", "pandas")},
        "config": {
            key: os.environ.get(key)
            for key in ("SIGNAL_MODEL", "SIGNAL_HISTORY_START", "SHADOW_MODELS",
                        "MAX_DRAWDOWN_PCT", "POSITION_FRACTION", "BROKER_PROVIDER")
        },
    }


def alert(message: str, url: str | None = None) -> bool:
    """POST a short message to ALERT_WEBHOOK_URL (Discord/Slack-style). Never raises."""
    url = url or os.environ.get("ALERT_WEBHOOK_URL")
    if not url:
        return False
    body = json.dumps({"content": message, "text": message}).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return 200 <= response.status < 300
    except Exception:
        return False

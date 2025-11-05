# gist_sync.py
import os
import json
import requests
from typing import Dict, Optional

GIST_ID = os.getenv("GIST_ID")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
HEADERS = {
    "Authorization": f"token {GITHUB_TOKEN}" if GITHUB_TOKEN else "",
    "Accept": "application/vnd.github.v3+json",
}

REQUEST_TIMEOUT = 12  # seconds


def _get_gist() -> Optional[Dict]:
    """Return gist JSON or None on error."""
    if not GIST_ID or not GITHUB_TOKEN:
        return None
    url = f"https://api.github.com/gists/{GIST_ID}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


def load_all_files() -> Dict[str, str]:
    """Return dict mapping filename -> content (strings)."""
    gist = _get_gist()
    if not gist:
        return {}
    files = gist.get("files", {})
    result = {}
    for name, meta in files.items():
        result[name] = meta.get("content", "") or ""
    return result


def save_file(filename: str, content: str) -> bool:
    """Patch a single file content in the gist. Returns True on success."""
    if not GIST_ID or not GITHUB_TOKEN:
        return False
    url = f"https://api.github.com/gists/{GIST_ID}"
    payload = {"files": {filename: {"content": content}}}
    try:
        r = requests.patch(url, json=payload, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        return True
    except Exception:
        return False


def save_json_dict(filename: str, data: dict) -> bool:
    return save_file(filename, json.dumps(data, ensure_ascii=False, indent=2))

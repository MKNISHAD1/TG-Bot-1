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
    "Content-Type": "application/json",
}

REQUEST_TIMEOUT = 12  # seconds


def _get_gist() -> Optional[Dict]:
    """Return gist JSON or None on error."""
    if not GIST_ID or not GITHUB_TOKEN:
        print("⚠ Missing GIST_ID or GITHUB_TOKEN environment variables.")
        return None
    url = f"https://api.github.com/gists/{GIST_ID}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"⚠ Failed to load gist: {e}")
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
        print("⚠ Missing GIST_ID or GITHUB_TOKEN environment variables.")
        return False
    url = f"https://api.github.com/gists/{GIST_ID}"
    payload = {"files": {filename: {"content": content}}}
    try:
        r = requests.patch(url, json=payload, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        if r.status_code == 200:
            print(f"✅ Saved {filename} to gist successfully.")
            return True
        else:
            print(f"❌ Failed to save {filename}: {r.status_code} - {r.text}")
            return False
    except Exception as e:
        print(f"⚠ Exception saving {filename}: {e}")
        return False


def save_json_dict(filename: str, data: dict) -> bool:
    """Save a JSON dict to one file."""
    return save_file(filename, json.dumps(data, ensure_ascii=False, indent=2))


def save_json_dicts(files_data: dict, aliases_data: dict) -> bool:
    """Save both files.json and aliases.json together."""
    if not GIST_ID or not GITHUB_TOKEN:
        print("⚠ Missing GIST_ID or GITHUB_TOKEN environment variables.")
        return False
    url = f"https://api.github.com/gists/{GIST_ID}"
    payload = {
        "files": {
            "files.json": {"content": json.dumps(files_data, ensure_ascii=False, indent=2)},
            "aliases.json": {"content": json.dumps(aliases_data, ensure_ascii=False, indent=2)},
        }
    }
    try:
        r = requests.patch(url, headers=HEADERS, data=json.dumps(payload), timeout=REQUEST_TIMEOUT)
        if r.status_code == 200:
            print("✅ Both files.json and aliases.json saved successfully.")
            return True
        else:
            print(f"❌ Gist patch failed: {r.status_code} - {r.text}")
            return False
    except Exception as e:
        print(f"⚠ Exception while saving both files: {e}")
        return False
"""Read the public Clerk setting without signing in. Responses stay in memory."""

import base64
import json
import re
import urllib.request
from http.client import HTTPException


def mode(host):
    try:
        with urllib.request.urlopen(host, timeout=5) as response:
            html = response.read(2_000_000).decode()
        found = re.search(r"pk_(?:test|live)_([A-Za-z0-9]+)", html)
        if not found:
            return "not_checked"
        encoded = found[1]
        frontend = (
            base64.b64decode(encoded + "=" * (-len(encoded) % 4)).decode().rstrip("$")
        )
        if not re.fullmatch(
            r"[a-z0-9-]+\.(?:clerk\.accounts\.dev|clerk\.com)", frontend
        ):
            return "not_checked"
        with urllib.request.urlopen(
            "https://" + frontend + "/v1/environment", timeout=5
        ) as response:
            value = json.load(response)
        setting = value.get("user_settings", {}).get("sign_up", {}).get("mode")
        return setting if setting in {"public", "restricted"} else "not_checked"
    except (OSError, ValueError, TypeError, AttributeError, HTTPException):
        return "not_checked"

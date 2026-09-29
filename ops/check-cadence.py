"""Read a data-free public summary; a database outage fails this external check too."""

import json
import os
import sys
from datetime import UTC, datetime
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def check(payload, now=None):
    if not isinstance(payload, dict) or payload.get("ok") is not True or payload.get("overdue") != []:
        raise ValueError("A cadence is overdue or status is unavailable")
    stamp = payload.get("checked_at")
    if not isinstance(stamp, str):
        raise TypeError("Status has no check time")
    checked = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    if checked.tzinfo is None or not -30 <= ((now or datetime.now(UTC)) - checked).total_seconds() <= 120:
        raise ValueError("Status is stale")


def main():
    url = os.environ.get("MDP_STATUS_URL", "")
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        print("Status URL is missing or invalid. Set MDP_STATUS_URL to the public HTTPS /health/status route.")
        return 1
    try:
        with build_opener(NoRedirect).open(url, timeout=15) as response:
            check(json.loads(response.read(16384)))
    except (URLError, OSError, ValueError, TypeError):
        print("Scheduled closes are overdue or unavailable. Open /ops and /runbooks/runners-held.")
        return 1
    print("Scheduled closes are current. Open /ops for details.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

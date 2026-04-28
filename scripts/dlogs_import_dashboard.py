#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
from urllib import request
from urllib.error import HTTPError, URLError


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DASHBOARD = ROOT / "monitoring/dlogs/grafana/dagent-worker-dashboard.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Import the dAgent dashboard into the already-running dLogs Grafana.")
    parser.add_argument("--grafana-url", default="http://127.0.0.1:3000")
    parser.add_argument("--user", default="admin")
    parser.add_argument("--password", default="admin")
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    parser.add_argument("--apply", action="store_true", help="Actually import the dashboard. Without this, only validates.")
    args = parser.parse_args()

    dashboard = json.loads(args.dashboard.read_text(encoding="utf-8"))
    payload = {
        "dashboard": dashboard,
        "overwrite": True,
        "message": "Imported from dAgent",
    }

    if not args.apply:
        print(f"validated dashboard: {args.dashboard}")
        print("To import it into Grafana, rerun with --apply.")
        return 0

    credentials = base64.b64encode(f"{args.user}:{args.password}".encode("utf-8")).decode("ascii")
    req = request.Request(
        f"{args.grafana_url.rstrip('/')}/api/dashboards/db",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        response = request.urlopen(req, timeout=15)
    except HTTPError as exc:
        print(exc.read().decode("utf-8", errors="replace"))
        raise
    except URLError as exc:
        raise SystemExit(f"failed to reach Grafana: {exc}") from exc

    print(response.read().decode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


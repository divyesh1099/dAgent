#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import hmac
from pathlib import Path
import time


def main() -> int:
    parser = argparse.ArgumentParser(description="Sign a JSON payload for dAgent worker HMAC auth.")
    parser.add_argument("payload", type=Path, help="Path to the JSON payload file")
    parser.add_argument("--secret", required=True, help="DAGENT_WORKER_HMAC_SECRET value")
    args = parser.parse_args()

    body = args.payload.read_bytes()
    timestamp = str(int(time.time()))
    signature = hmac.new(args.secret.encode("utf-8"), timestamp.encode("utf-8") + b"." + body, hashlib.sha256).hexdigest()

    print(f"X-Dagent-Timestamp: {timestamp}")
    print(f"X-Dagent-Signature: sha256={signature}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path


DEFAULT_DLOGS_ROOT = Path("/home/divyesh-nandlal-vishwakarma/Desktop/Divyesh/dLogs")
DEFAULT_TARGET_FILE = DEFAULT_DLOGS_ROOT / ".dlogs-state/prometheus/host-machine.json"
DEFAULT_ENV_DIR = Path.home() / ".config/dagent/workers"


def main() -> int:
    parser = argparse.ArgumentParser(description="Register dAgent workers as dLogs Prometheus file_sd targets.")
    parser.add_argument("--target-file", type=Path, default=DEFAULT_TARGET_FILE)
    parser.add_argument("--worker-env-dir", type=Path, default=DEFAULT_ENV_DIR)
    parser.add_argument("--target-host", default="auto", help="auto, host.docker.internal, or an explicit host/IP")
    parser.add_argument("--container", default="dlogs-prometheus", help="Prometheus container name for auto host detection")
    parser.add_argument("--apply", action="store_true", help="Write the target file. Without this, only prints the result.")
    args = parser.parse_args()

    envs = load_worker_envs(args.worker_env_dir)
    targets = build_targets(envs, args.target_host, args.container)

    existing = read_existing(args.target_file)
    merged = merge_targets(existing, targets)
    output = json.dumps(merged, indent=2) + "\n"

    if not args.apply:
        print(output, end="")
        print(f"\nDry run. To apply: {Path(__file__).name} --apply", flush=True)
        return 0

    args.target_file.parent.mkdir(parents=True, exist_ok=True)
    if args.target_file.exists():
        backup = args.target_file.with_suffix(args.target_file.suffix + f".bak.{datetime.now().strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(args.target_file, backup)
        print(f"backup: {backup}")

    args.target_file.write_text(output, encoding="utf-8")
    print(f"wrote: {args.target_file}")
    print(output, end="")
    return 0


def resolve_target_host(target_host: str, container: str, port: str) -> str:
    if target_host != "auto":
        return target_host

    candidates = candidate_hosts(container)
    for candidate in candidates:
        if prometheus_container_can_probe(container, candidate, port):
            return candidate
    return candidates[0] if candidates else "host.docker.internal"


def load_worker_envs(worker_env_dir: Path) -> list[tuple[Path, dict[str, str]]]:
    return [(env_file, parse_env(env_file)) for env_file in sorted(worker_env_dir.glob("*.env"))]


def build_targets(envs: list[tuple[Path, dict[str, str]]], target_host: str, container: str) -> list[dict[str, object]]:
    targets: list[dict[str, object]] = []
    for env_file, values in envs:
        name = values.get("DAGENT_WORKER_NAME") or env_file.stem
        port = values.get("DAGENT_WORKER_PORT")
        if not port:
            continue
        host = resolve_target_host(target_host, container, port)
        targets.append(
            {
                "targets": [f"{host}:{port}"],
                "labels": {
                    "source": "host",
                    "app": "dagent",
                    "worker": name,
                },
            }
        )
    return targets


def candidate_hosts(container: str) -> list[str]:
    candidates: list[str] = []

    try:
        lan_result = subprocess.run(["hostname", "-I"], check=True, capture_output=True, text=True, timeout=5)
    except (subprocess.SubprocessError, FileNotFoundError):
        lan_result = None
    if lan_result:
        for value in lan_result.stdout.split():
            if value.startswith(("127.", "169.254.", "172.")):
                continue
            candidates.append(value)

    host_ip_command = [
        "docker",
        "exec",
        container,
        "sh",
        "-c",
        "awk '$2 == \"host.docker.internal\" { print $1; exit }' /etc/hosts",
    ]
    try:
        result = subprocess.run(host_ip_command, check=True, capture_output=True, text=True, timeout=5)
    except (subprocess.SubprocessError, FileNotFoundError):
        result = None
    if result and result.stdout.strip():
        candidates.append(result.stdout.strip().splitlines()[0])

    candidates.append("host.docker.internal")

    route_command = ["docker", "exec", container, "sh", "-c", "ip route | awk '/default/ { print $3; exit }'"]
    try:
        route_result = subprocess.run(route_command, check=True, capture_output=True, text=True, timeout=5)
    except (subprocess.SubprocessError, FileNotFoundError):
        route_result = None
    if route_result and route_result.stdout.strip():
        candidates.append(route_result.stdout.strip().splitlines()[0])

    unique: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate and candidate not in seen:
            seen.add(candidate)
            unique.append(candidate)
    return unique


def prometheus_container_can_probe(container: str, host: str, port: str) -> bool:
    command = [
        "docker",
        "exec",
        container,
        "sh",
        "-c",
        f"wget -T 3 -qO- http://{host}:{port}/metrics | grep -q '^dagent_worker_up'",
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True, timeout=5)
    except (subprocess.SubprocessError, FileNotFoundError):
        return False
    return True


def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values


def read_existing(path: Path) -> list[dict[str, object]]:
    if not path.exists() or not path.read_text(encoding="utf-8").strip():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def merge_targets(existing: list[dict[str, object]], new: list[dict[str, object]]) -> list[dict[str, object]]:
    worker_keys = {
        str(item.get("labels", {}).get("worker"))
        for item in new
        if isinstance(item.get("labels"), dict) and item.get("labels", {}).get("app") == "dagent"
    }

    kept: list[dict[str, object]] = []
    for item in existing:
        labels = item.get("labels", {})
        if isinstance(labels, dict) and labels.get("app") == "dagent" and str(labels.get("worker")) in worker_keys:
            continue
        kept.append(item)

    merged = kept + new
    seen: set[tuple[str, ...]] = set()
    unique: list[dict[str, object]] = []
    for item in merged:
        key = tuple(str(target) for target in item.get("targets", []))
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


if __name__ == "__main__":
    raise SystemExit(main())

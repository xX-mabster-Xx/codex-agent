#!/usr/bin/env python3
"""Wait for a local TCP dependency without adding a shell/netcat dependency."""

from __future__ import annotations

import argparse
import socket
import time


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--timeout", type=float, default=60)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((args.host, args.port), timeout=2):
                return 0
        except OSError:
            time.sleep(1)
    print(f"Timed out waiting for {args.host}:{args.port}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

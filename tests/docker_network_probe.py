"""Prove that this test container cannot establish an external connection."""

from __future__ import annotations

import socket
import sys


def main() -> int:
    target = ("1.1.1.1", 80)
    try:
        with socket.create_connection(target, timeout=2):
            print("NETWORK_PROBE=FAIL external connection succeeded", flush=True)
            return 1
    except OSError as error:
        print(f"NETWORK_PROBE=PASS external connection blocked ({error.__class__.__name__})", flush=True)
        return 0


if __name__ == "__main__":
    sys.exit(main())

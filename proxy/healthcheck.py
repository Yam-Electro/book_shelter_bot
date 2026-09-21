#!/usr/bin/env python3
from __future__ import annotations

import socket
import sys

from gen_config import env


def main() -> None:
    if not env("VLESS_URL", "VPN_URL"):
        sys.exit(0)
    socket.create_connection(("127.0.0.1", 1080), 2).close()


if __name__ == "__main__":
    main()

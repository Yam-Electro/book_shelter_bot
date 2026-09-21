#!/usr/bin/env python3
from __future__ import annotations

import os
import time

from gen_config import env, main as write_config


def main() -> None:
    url = env("VLESS_URL", "VPN_URL")
    if not url:
        print("VLESS_URL is empty, VPN disabled", flush=True)
        while True:
            time.sleep(3600)
    write_config()
    os.execvp("xray", ["xray", "run", "-c", "/tmp/xray.json"])


if __name__ == "__main__":
    main()

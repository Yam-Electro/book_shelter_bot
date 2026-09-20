#!/usr/bin/env python3
from __future__ import annotations

import os

from gen_config import main as write_config


def main() -> None:
    write_config()
    os.execvp("xray", ["xray", "run", "-c", "/tmp/xray.json"])


if __name__ == "__main__":
    main()

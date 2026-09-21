#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from urllib.parse import parse_qs, unquote, urlparse


def env(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value:
            return value.strip().strip('"')
    return None


def vless_to_xray(url: str) -> dict:
    parsed = urlparse(url)
    if parsed.scheme != "vless":
        raise SystemExit("VLESS_URL must start with vless://")
    uuid = unquote(parsed.username or "")
    host = parsed.hostname
    port = parsed.port or 443
    if not uuid or not host:
        raise SystemExit("VLESS_URL is missing uuid or host")
    q = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    flow = q.get("flow", "")
    network = q.get("type", "tcp")
    if network == "tcp":
        network = "raw"
    security = q.get("security", "reality")
    stream: dict = {"network": network, "method": network, "security": security}
    pbk = q.get("pbk") or q.get("password") or ""
    if security == "reality":
        stream["realitySettings"] = {
            "fingerprint": q.get("fp", "chrome"),
            "serverName": q.get("sni") or q.get("serverName") or host,
            "password": pbk,
            "publicKey": pbk,
            "shortId": q.get("sid", ""),
            "spiderX": q.get("spx") or "",
        }
    elif security == "tls":
        stream["tlsSettings"] = {
            "serverName": q.get("sni") or host,
            "fingerprint": q.get("fp", "chrome"),
            "allowInsecure": False,
        }
    user = {"id": uuid, "encryption": q.get("encryption", "none")}
    if flow:
        user["flow"] = flow
    return {
        "log": {"loglevel": os.getenv("XRAY_LOGLEVEL", "warning")},
        "inbounds": [
            {
                "tag": "socks",
                "listen": "0.0.0.0",
                "port": int(os.getenv("SOCKS_PORT", "1080")),
                "protocol": "socks",
                "settings": {"udp": True, "auth": "noauth"},
            }
        ],
        "outbounds": [
            {
                "tag": "vless-out",
                "protocol": "vless",
                "settings": {
                    "vnext": [
                        {
                            "address": host,
                            "port": port,
                            "users": [user],
                        }
                    ]
                },
                "streamSettings": stream,
            }
        ],
    }


def main() -> None:
    url = env("VLESS_URL", "VPN_URL")
    if not url:
        raise SystemExit("VLESS_URL is not set")
    dest = sys.argv[1] if len(sys.argv) > 1 else "/tmp/xray.json"
    config = vless_to_xray(url)
    with open(dest, "w", encoding="utf-8") as fh:
        json.dump(config, fh, indent=2)
        fh.write("\n")


if __name__ == "__main__":
    main()

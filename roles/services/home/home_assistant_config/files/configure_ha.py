#!/usr/bin/env python3
"""Idempotently set Home Assistant core config (and optionally the OS hostname).

Config via env: HA_URL, HA_TOKEN, HA_CORE (JSON of desired core keys),
HA_HOSTNAME (optional). Prints {"changed": [...]} on stdout; nothing else.
Set HA_DRY_RUN=1 to report differences without writing.
"""
import os, sys, json
import websocket  # pip: websocket-client

url = os.environ["HA_URL"].rstrip("/")
token = os.environ["HA_TOKEN"]
desired = json.loads(os.environ["HA_CORE"])
hostname = os.environ.get("HA_HOSTNAME", "").strip()
dry = os.environ.get("HA_DRY_RUN") == "1"

ws = websocket.create_connection(
    url.replace("http", "ws", 1) + "/api/websocket", timeout=15)
_id = 0


def recv():
    return json.loads(ws.recv())


def call(**msg):
    """Send one command, return its result; raise on failure."""
    global _id
    _id += 1
    ws.send(json.dumps({"id": _id, **msg}))
    while True:
        r = recv()
        if r.get("id") == _id:
            if not r.get("success"):
                raise SystemExit(f"{msg['type']} failed: {r.get('error')}")
            return r.get("result")


try:
    if recv().get("type") != "auth_required":
        raise SystemExit("unexpected handshake from Home Assistant")
    ws.send(json.dumps({"type": "auth", "access_token": token}))
    auth = recv()
    if auth.get("type") != "auth_ok":
        raise SystemExit("authentication rejected (check vault_ha_token)")

    current = call(type="get_config")
    # get_config reports unit_system as a dict of units; map it back to the name
    # that config/core/update takes.
    cur_units = "metric" if current.get("unit_system", {}).get("length") == "km" else "us_customary"
    live = {**current, "unit_system": cur_units}

    diff = {k: v for k, v in desired.items() if live.get(k) != v}
    changed = sorted(diff)
    if diff and not dry:
        call(type="config/core/update", **diff)

    if hostname:
        info = call(type="supervisor/api", endpoint="/host/info", method="get")
        if info.get("hostname") != hostname:
            changed.append("hostname")
            if not dry:
                call(type="supervisor/api", endpoint="/host/options",
                     method="post", data={"hostname": hostname})

    print(json.dumps({"changed": changed}))
finally:
    ws.close()

#!/usr/bin/env python3
"""Idempotently ensure Uptime Kuma monitors exist. Config via env (JSON)."""
import os, sys, json
from uptime_kuma_api import UptimeKumaApi, MonitorType

url = os.environ["KUMA_URL"]
user = os.environ["KUMA_USER"]
pw = os.environ["KUMA_PASSWORD"]
http_mons = json.loads(os.environ.get("KUMA_HTTP", "[]"))
push_mons = json.loads(os.environ.get("KUMA_PUSH", "[]"))

api = UptimeKumaApi(url)
api.login(user, pw)
try:
    existing = {m["name"]: m for m in api.get_monitors()}
    created = []
    for m in http_mons:
        if m["name"] in existing:
            continue
        api.add_monitor(type=MonitorType.HTTP, name=m["name"], url=m["url"],
                        accepted_statuscodes=["200-299", "300-399"],
                        interval=60, maxretries=2, retryInterval=60)
        created.append(m["name"])
    for m in push_mons:
        if m["name"] in existing:
            continue
        api.add_monitor(type=MonitorType.PUSH, name=m["name"],
                        interval=int(m.get("interval", 90000)), maxretries=0)
        created.append(m["name"])
    tokens = {m["name"]: m.get("pushToken") for m in api.get_monitors() if m.get("pushToken")}
    print(json.dumps({"created": created, "push_tokens": tokens}))
finally:
    api.disconnect()

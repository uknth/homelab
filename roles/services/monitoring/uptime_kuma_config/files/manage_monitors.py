#!/usr/bin/env python3
"""Idempotently ensure Uptime Kuma monitors exist. Config via env (JSON)."""
import os, sys, json
from uptime_kuma_api import UptimeKumaApi, MonitorType, NotificationType

url = os.environ["KUMA_URL"]
user = os.environ["KUMA_USER"]
pw = os.environ["KUMA_PASSWORD"]
http_mons = json.loads(os.environ.get("KUMA_HTTP", "[]"))
push_mons = json.loads(os.environ.get("KUMA_PUSH", "[]"))
ntfy = json.loads(os.environ.get("KUMA_NTFY", "{}"))
dash_url = os.environ.get("KUMA_DASH_URL", "").rstrip("/")
# status page slug passed via KUMA_STATUSPAGE

api = UptimeKumaApi(url)
api.login(user, pw)
try:
    created = []

    # Failure alerting first: monitors must be created WITH the notification
    # attached. isDefault only auto-attaches in the web UI -- a monitor added
    # through the API gets an empty notificationIDList and then fails silently,
    # forever, with nobody notified. So resolve the id up front and pass it
    # explicitly on every add_monitor below.
    notif_ids = []
    if ntfy:
        existing_notifs = {n["name"]: n for n in api.get_notifications()}
        if ntfy["name"] not in existing_notifs:
            api.add_notification(
                name=ntfy["name"],
                type=NotificationType.NTFY,
                isDefault=True,
                applyExisting=True,
                ntfyserverurl=ntfy["url"],
                ntfytopic=ntfy["topic"],
                ntfyPriority=int(ntfy.get("priority", 5)),
                ntfyAuthenticationMethod="none",
            )
            created.append("notification:" + ntfy["name"])
            existing_notifs = {n["name"]: n for n in api.get_notifications()}
        notif_ids = [existing_notifs[ntfy["name"]]["id"]]

    existing = {m["name"]: m for m in api.get_monitors()}
    for m in http_mons:
        if m["name"] in existing:
            continue
        api.add_monitor(type=MonitorType.HTTP, name=m["name"], url=m["url"],
                        accepted_statuscodes=["200-299", "300-399"],
                        interval=60, maxretries=2, retryInterval=60,
                        notificationIDList=notif_ids)
        created.append(m["name"])
    for m in push_mons:
        if m["name"] in existing:
            continue
        api.add_monitor(type=MonitorType.PUSH, name=m["name"],
                        interval=int(m.get("interval", 90000)),
                        maxretries=int(m.get("maxretries", 0)),
                        notificationIDList=notif_ids)
        created.append(m["name"])

    # Repair pass. Both of these fix monitors created by earlier runs of this
    # role, which look perfectly healthy in the UI but can never alert.
    for m in api.get_monitors():
        fixes = {}
        if notif_ids and notif_ids[0] not in (m.get("notificationIDList") or []):
            fixes["notificationIDList"] = notif_ids
        # Kuma's ntfy provider always attaches a "view" action built from the
        # monitor's URL, and ntfy rejects the whole message with HTTP 400
        # ("parameter 'url' is required for action 'view'") when that URL is
        # empty. Push monitors have no URL by nature, so without this they fail
        # to notify forever -- silently, since the failure is only visible in
        # Kuma's container log. Point them at their own dashboard page.
        if (dash_url and str(m.get("type")).endswith("PUSH")
                and m.get("url") in (None, "", "https://")):
            fixes["url"] = "%s/dashboard/%s" % (dash_url, m["id"])
        if fixes:
            api.edit_monitor(m["id"], **fixes)
            created.append("repaired:%s:%s" % (m["name"], ",".join(sorted(fixes))))

    # Public status page for the Homepage uptimekuma widget.
    sp_slug = os.environ.get("KUMA_STATUSPAGE", "")
    if sp_slug:
        existing_sp = [p["slug"] for p in api.get_status_pages()]
        if sp_slug not in existing_sp:
            api.add_status_page(sp_slug, "Homelab")
            mons = api.get_monitors()
            api.save_status_page(sp_slug, title="Homelab", published=True,
                publicGroupList=[{"name": "Services", "monitorList": [{"id": m["id"]} for m in mons]}])
            created.append("statuspage:" + sp_slug)

    tokens = {m["name"]: m.get("pushToken") for m in api.get_monitors() if m.get("pushToken")}
    print(json.dumps({"created": created, "push_tokens": tokens}))
finally:
    api.disconnect()

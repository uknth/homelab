# Notifications — how alerts reach a phone

```
Uptime Kuma ─┐
Diun ────────┤
restic ──────┼─→ ntfy (util01)  ──→ ntfy-relay sidecar ──→ n8n /webhook/ntfy-relay
n8n jobs ────┘        │                                          └─→ Telegram
                      └─→ dashboard widgets + status bar (dash.puhome.net)
```

**ntfy remains the single bus.** Every service posts there and the dashboard
reads it; only the last hop to the phone is Telegram. Swapping transport again
means editing one workflow, not five roles.

## Why not the ntfy iOS app

It never delivered, and the cause is not in this homelab. Verified 2026-08-31:

- the server forwards correctly — `poll_request` observed landing on ntfy.sh
  three separate times, on the right topic hash
  (`sha256("https://ntfy.puhome.net/homelab-alerts")`)
- `upstream-base-url: "https://ntfy.sh"` is set (required: iOS forbids
  persistent background connections, so APNs must be triggered via ntfy.sh)
- APNs endpoints resolve and ports 5223/443 are reachable from the LAN
- the phone's APNs is healthy — other apps notify normally
- **a plain ntfy.sh topic also failed**, on a freshly reinstalled app with
  permission granted — which removes this homelab from the equation entirely

ntfy's own [known issues](https://docs.ntfy.sh/known-issues/) list "Firebase+APNS
being buggy" as a recognised cause. Every documented remedy (reinstall,
re-grant, delete and re-add the subscription) was tried.

`upstream-base-url` is deliberately **left in place**: it is correct, it costs
nothing, and if the app is ever fixed alerts will simply start flowing again.

## The relay

`ntfy subscribe --from-config` in a sidecar (`ntfy-relay`, same compose project
as ntfy) holds a connection and fires per message — push, not polling, so there
is no interval to tune and no dedupe window to get wrong. It POSTs ntfy's raw
JSON to n8n, which formats and sends.

- Topics relayed: `ntfy_relay_topics` — `homelab-alerts`, `homelab-updates`.
  **`homelab-jobs` is excluded on purpose**: every n8n run reports there, pass
  or fail. That is a log, not an alert, and it would train you to ignore the
  phone.
- Priority < 4 sends with `disable_notification`, so Diun update notices land in
  the chat without buzzing at 3am. Kuma's DOWN alerts are priority 5.
- The sidecar is a separate container so a relay crash-loop can never take the
  notification sink itself down.
- The ntfy image ships **no curl** — the relay command uses busybox `wget`.

## Setup

1. Create a bot with [@BotFather](https://t.me/BotFather) (`/newbot`) and keep
   the token.
2. Send the bot any message, then read the chat id from
   `https://api.telegram.org/bot<TOKEN>/getUpdates` → `result[0].message.chat.id`.
3. Add both to the vault as `vault_telegram_bot_token` and
   `vault_telegram_chat_id` (see HANDOFF for the safe edit procedure).
4. `ansible-playbook site.yml --tags ntfy,n8n`

Until both vault values are set, the bridge workflow and its credential are not
imported at all — the relay still runs and its POSTs simply 404, harmlessly.

## Verifying

```
docker logs ntfy-relay --tail 20                  # subscribed, no connection warnings
curl -H "Title: test" -H "Priority: 5" -d "hello" http://10.0.2.8:8091/homelab-alerts
docker logs n8n --since 2m | grep ntfy-relay      # should be silent once wired
```

A `Connection failed: ... EOF` warning right after a deploy is the sidecar
racing ntfy's startup; it retries and settles on its own.

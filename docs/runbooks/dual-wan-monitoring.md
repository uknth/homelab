# Dual-WAN monitoring — knowing *which* ISP is down

The Omada at `10.0.2.1` runs ACT Fibernet and Airtel Black in **load share**. That
is great for throughput and terrible for visibility: when one ISP dies the router
silently moves every session onto the survivor, so nothing on `10.0.2.0/24` can
tell the difference. Uptime Kuma, Beszel and Homepage all stay green — right up
until the *second* link drops and the house falls off the internet.

`ops/wanwatch` closes that gap.

```
n8n (util01, every 2 min)
  └─ SSH, forced command ─→ gw01: wan-probe.sh
       ├─ probe from 10.0.2.19 ─ Omada policy route (Only) ─→ WAN1 · ACT
       └─ probe from 10.0.2.20 ─ Omada policy route (Only) ─→ WAN2 · Airtel
            │
            ├─ push ─→ Uptime Kuma "WAN1 · ACT Fibernet"   ─┐
            ├─ push ─→ Uptime Kuma "WAN2 · Airtel Black"    ├→ ntfy (default channel)
            └─ JSON ─→ gw01:status.json
                          ↑
       Homepage → n8n /webhook/wan-status → SSH `--last` (replay, probes nothing)
```

## Why source-IP pinning

Omada policy routing can force a given source address out one specific WAN, and
its **Only** mode does so *regardless of link state or the gateway's own online
detection*. Priority mode would fall back to the healthy WAN — masking exactly
the failure we are trying to see — so Only mode is not optional here.

Pinning also catches the failure that link-state monitoring cannot: **PPPoE up,
no transit**. The interface is green, the Omada is happy, and nothing actually
reaches the internet. A probe sourced from that WAN's address notices in one
cycle.

Uptime Kuma cannot do this itself: it has no way to bind a monitor to a source
address ([louislam/uptime-kuma#4719](https://github.com/louislam/uptime-kuma/issues/4719)).
Hence the external probe that pushes *into* Kuma.

## Who owns what

| Concern | Owner | Why |
|---|---|---|
| Scheduling the probe | n8n (`Homelab — dual-WAN watch`) | every job visible in one place, same as the maintenance workflows |
| Running the probe | `gw01:/opt/homelab/wanwatch/wan-probe.sh` | needs the alias IPs, and gw01 is the network host |
| Up/down verdict, retries, history | Uptime Kuma push monitors | already exists, already graphs, already deduplicates |
| The ntfy alert | Kuma's default `ntfy-alerts` channel | attached to every monitor; nothing extra to wire |
| Dashboard tile | Homepage `customapi` → n8n `/webhook/wan-status` → `--last` | names the failed ISP; the Kuma tile only counts |

The dashboard path deliberately replays `status.json` rather than probing: the
widget polls far more often than the probe runs, and a refresh must never cost a
round of live probing or be able to trigger Kuma pushes. n8n workflow static
data was the first design and does **not** work here — on n8n 2.x's draft/publish
model the `$getWorkflowStaticData` writes from one execution are not visible to
the next.

n8n deliberately does **not** send its own per-WAN ntfy message — that would
page twice for one outage. It notifies only when the probe itself cannot run
(gw01 unreachable, script broken), on `homelab-jobs`.

## One-time setup

### 1. Omada policy routing (manual — the Omada is unmanaged)

In the controller (`10.0.2.21`):

1. **Settings → Profiles → Groups** — create two IP groups, one address each:
   - `probe-wan1` → `10.0.2.19/32`
   - `probe-wan2` → `10.0.2.20/32`
2. **Settings → Transmission → Routing → Policy Routing** — add two rules:

   | Name | Source | WAN | Mode |
   |---|---|---|---|
   | `probe-wan1` | IP group `probe-wan1` | WAN1 (ACT) | **Only** |
   | `probe-wan2` | IP group `probe-wan2` | WAN2 (Airtel) | **Only** |

   Rules are evaluated top to bottom; put both above anything broader. Load
   balancing must stay **enabled** — policy routing is layered on top of it, and
   TP-Link's own docs note it will not work otherwise.

Confirm which physical port is which ISP before assigning — getting them swapped
means the dashboard will name the wrong provider.

### 2. Deploy

```
ansible-playbook site.yml --tags wanwatch          # probe + aliases on gw01
ansible-playbook site.yml --tags uptime-kuma-config # creates the two push monitors
ansible-playbook site.yml --tags n8n               # workflow + gw01 SSH credential
```

`wanwatch` binds `10.0.2.19` / `10.0.2.20` to gw01's LAN interface (live via
`ip addr add`, persisted in `/etc/network/interfaces.d/wanwatch`) and generates
the forced-command key on util01.

### 3. Capture the push tokens

Uptime Kuma mints a push token per monitor. The `uptime-kuma-config` run prints
them:

```
ansible-playbook site.yml --tags uptime-kuma-config -v
```

Copy the two WAN tokens into `hosts/group_vars/all/vars.yml`:

```yaml
kuma_wan1_push_token: "xxxxxxxx"
kuma_wan2_push_token: "yyyyyyyy"
```

then re-run `--tags wanwatch`. Same pattern as `kuma_backup_push_token`. Until
they are set, the probe still runs and the dashboard still works — it just does
not feed Kuma, so nothing alerts.

## Verifying it actually works

Verified live 2026-08-30: `10.0.2.19` → `49.207.210.99` (RDAP: ACTFIBERNET) and
`10.0.2.20` → `122.171.22.97` (RDAP: ABTS-KK, Airtel Karnataka), confirming both
the pinning and that WAN1/WAN2 are labelled the right way round.

```
ssh ansible@10.0.2.2 /opt/homelab/wanwatch/wan-probe.sh --no-push | jq
```

The thing to check is **`public_ip` differing between the two links**. Two
different addresses prove the policy routing is really splitting the traffic.
Identical addresses mean a rule is missing, is matching the wrong source, or is
in Priority rather than Only mode — the probe would then report both links up
forever, which is worse than no monitoring at all.

To exercise the alert path without touching cables, push a synthetic failure
(twice — `maxretries: 1` means one down beat only goes pending):

```
curl -G "http://10.0.2.8:3001/api/push/<token>" \
  --data-urlencode "status=down" --data-urlencode "msg=SYNTHETIC TEST"
```

The next scheduled probe clears it and sends a recovery notification. Verified
this way 2026-08-30: Down and Up both delivered to `homelab-alerts`.

Then pull one WAN cable and confirm, within ~2 minutes:
- that link's Kuma monitor goes red and ntfy fires,
- the Homepage `Internet` tile shows it DOWN,
- the *other* link stays green (if both go red, the probe is not pinned).

## On the dashboard

Three places, all on `dash.puhome.net`:

- **System tab → Status band.** A full-width section directly under the Admin
  shortcuts, above Download Activity, holding two cards: `Internet` (per-ISP
  status + latency) and `Alerts` (ntfy volume over the last 12h). Homepage
  renders widget groups into a 2-column masonry and gives groups no id, so
  `custom.js` tags the band by its heading text and `custom.css` spans it — a
  positional CSS rule would also stretch the first group on every other tab.
- **Bottom status bar**, on every tab: one pill per WAN (green/red/amber-stale,
  with the probe and public IP on hover) plus ntfy volume and the newest alert.
- **Live toasts**: `custom.js` subscribes to ntfy's SSE stream, so a new alert
  surfaces without watching a tab. High/max priority gets a red edge.

Both browser-side sources must be HTTPS on the same scheme as the page and must
allow the `dash` origin: ntfy already sends `Access-Control-Allow-Origin: *`,
and the n8n `wan-status` webhook sets the header explicitly in its Respond node.
Plain `http://util01:5678` would be blocked as mixed content, which is why the
bar uses the `n8n.puhome.net` vhost rather than the direct port that the
server-side Homepage widget uses.

`/webhook/alerts-summary` exists because ntfy answers history as **NDJSON**,
which Homepage's `customapi` widget cannot parse — n8n aggregates it into one
JSON object. Its window is capped by ntfy's `cache-duration` (12h); asking for
longer silently under-reports rather than erroring.

## Notes and gotchas

- **`10.0.2.19` / `10.0.2.20` are outside the DHCP pool** (`10.0.2.25-254`) and
  must be used for nothing else. Each is *designed* to lose internet when its
  ISP does.
- The probe tries ICMP to `1.1.1.1` / `8.8.8.8` / `9.9.9.9`, then falls back to
  a TCP+TLS handshake before declaring a link dead — some paths filter ICMP but
  carry traffic fine. `curl -k` is deliberate: this measures reachability, not
  certificate identity.
- **Targets are IP literals on purpose.** A hostname would fold a DNS failure
  (Blocky, Pi-hole) into an ISP verdict.
- The Kuma push goes out over gw01's normal address, not a probe alias — a
  notification about a dead ISP must not have to travel over that ISP.
- Kuma push interval is 300s with `maxretries: 1`. A genuine outage is reported
  *actively* (`status=down`), so detection is not gated on that window; the
  window only catches the probe itself dying, and tolerates one skipped n8n run.
- The probe exits 0 even when every link is down — the verdict is the JSON on
  stdout. A non-zero exit means the probe could not run, which is the only thing
  n8n pages about.
- **The public-IP lookup must be an IP literal.** It was `api.ipify.org` first,
  which Pi-hole's blocklists resolve to `0.0.0.0` — so the lookup hit gw01's own
  nginx and died on a cert mismatch, reporting an empty public IP rather than an
  error. `https://1.1.1.1/cdn-cgi/trace` involves no name at all.
- **n8n's SSH node prepends `cd <dir> ;` (or `&&`) to every command**, so
  `SSH_ORIGINAL_COMMAND` is never just the flag you sent. Both `wan-probe.sh`
  and `maintenance.sh` strip that prefix before parsing. Without it the script
  exits 64 (`unknown argument: cd`) and — because the probe still "succeeds"
  from n8n's point of view when the error branch is not wired — the failure is
  quiet: no probes run, no pushes happen, and Kuma's own interval bookkeeping
  keeps the monitors looking plausible.
- **Two Kuma traps, both fixed in `uptime_kuma_config` and both silent:**
  a notification marked `isDefault` is only auto-attached to monitors created
  through the **web UI** — anything added via the API gets an empty
  `notificationIDList` and can never alert. And Kuma's ntfy provider always
  attaches a "view" action built from the monitor's URL, which ntfy rejects with
  HTTP 400 when empty — so a **push** monitor (no URL by nature) fails to notify
  forever, visible only in `docker logs uptime-kuma`. The role now creates
  monitors with the channel attached, gives push monitors a dashboard URL, and
  repairs any existing monitor missing either.
- Worth enabling the Omada controller's own WAN-down alert too, as a backstop
  that does not depend on any of this.

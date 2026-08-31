# Beszel agents on the NAS appliances (nas01 / nas02)

`nas01` (TrueNAS SCALE) and `nas02` (Synology) are **unmanaged** — Ansible doesn't
provision them — so their Beszel agents are deployed by hand. They're already
registered as systems in the hub (`beszel_register_hosts` in `beszel_hub`), so once
each agent runs, its tile goes from **down → up** automatically. No per-agent secret:
the agent just needs the hub's public **KEY** and to listen on **45876**.

Ansible-managed hosts get their agent from `roles/system/beszel_agent`
(pinned by `beszel_agent_version`, currently **0.18.8** — matching the hub).

**Hub KEY** (same for every agent — it's `vault_beszel_agent_key`):
```
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIMM6KcGi+CFcOs8WeWgb7soRbi/NfvDms9KMe00ijG4p
```
To re-derive it from the hub itself, on util01:
`sudo ssh-keygen -y -f /opt/homelab/beszel/data/id_ed25519`

> **`PORT` is deprecated.** As of 0.18.x the variable is **`LISTEN`** (a port, or
> `host:port`). `PORT` is still honoured, so existing deployments keep working, but new
> ones should use `LISTEN`.

## nas01 — TrueNAS SCALE (25.10, Docker-based)
Apps → **Discover Apps → Custom App** (install by YAML). Persists across reboots/updates
(a raw `docker run` on TrueNAS can be pruned by the middleware):
```yaml
services:
  beszel-agent:
    image: henrygd/beszel-agent:latest
    container_name: beszel-agent
    restart: unless-stopped
    network_mode: host                 # listen on the host's :45876
    environment:
      LISTEN: "45876"
      KEY: "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIMM6KcGi+CFcOs8WeWgb7soRbi/NfvDms9KMe00ijG4p"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro   # also report container stats
      # - /mnt/<pool>/.beszel:/extra-filesystems/pool:ro   # see "Extra filesystems"
```

## nas02 — Synology (native package)
**Deployed via Package Center (SynoCommunity), not Docker.** The binary lives at
`/volume1/@appstore/beszel-agent/bin/beszel-agent` and runs as the unprivileged package
user `sc-beszel-agent`. The package survives DSM updates and needs no Container Manager,
which makes it the better option here — the Docker recipe that used to be in this
runbook has been dropped.

Configure `KEY` (and `LISTEN=45876`) through the package's own settings. Verify with
`ps -ef | grep beszel` on the NAS.

## Extra filesystems — the mechanism differs by deployment
The easiest thing to get wrong: **`EXTRA_FILESYSTEMS` is binary-only.**

- **Binary / native package** — set the env var to a comma-separated list of devices,
  partitions or mount points: `EXTRA_FILESYSTEMS="/volume1"`.
- **Docker** — the env var does **nothing**. Mount a directory from the target
  filesystem under the container's `/extra-filesystems/` instead:
  ```yaml
  volumes:
    - /mnt/tank/.beszel:/extra-filesystems/tank:ro
  ```
  Create the marker directory first. Use `name__Label` (double underscore) for a custom
  display name.

Without this the agent reports whatever backs `/` — for a container that's the overlay
filesystem, which tells you nothing about the array.

### Current state (verified 2026-08-31)
- **nas02 — correct.** Root reads 2.28 GB / ~54 % (the `md0` DSM system partition) and
  the array appears as an extra filesystem: `md2`, 3662.87 GB. That 3.63 TiB is the 4 TB
  RAID1 mirror.
- **nas01 — NOT monitoring the pool.** It reports a single 458.56 GB filesystem at
  0.03 % used (the boot device) and no extra filesystems at all — the RAIDZ2 pool is
  invisible to Beszel. Fix with the `/extra-filesystems` **mount** above; the
  `EXTRA_FILESYSTEMS` env var this runbook previously recommended was never going to
  work, because nas01's agent runs in a container. Worth doing while the ASMedia→LSI HBA
  swap is still pending.

## S.M.A.R.T. data
Requires a real `smartctl` (>= 7.0) **and** raw access to the devices.

- **Docker:** the default `henrygd/beszel-agent:latest` image is scratch-based — its
  entire filesystem is `/agent` plus `usr/share/libdrm/amdgpu.ids`. There is no
  `smartctl`, so SMART silently never populates. Switch to the **`:alpine`** tag and add:
  ```yaml
  image: henrygd/beszel-agent:alpine
  devices:
    - /dev/sda:/dev/sda
    - /dev/sdb:/dev/sdb
  cap_add:
    - SYS_RAWIO      # required for S.M.A.R.T.
    - SYS_ADMIN      # required for NVMe S.M.A.R.T. only
  ```
  Use base controllers (`sda`, `nvme0`), not partitions.
- **Binary:** `smartctl` must be on the host, and the agent needs privileges to read the
  raw devices.

`SMART_DEVICES` only *overrides* autodetection (`smartctl --scan`). Format is
`"/dev/sda:sat,/dev/nvme0:nvme"`, and it **merges** with detected devices rather than
replacing them; setting it to an empty string disables SMART entirely. Related:
`SMART_INTERVAL` (default `1h`), `EXCLUDE_SMART` (comma-separated, `*` wildcards).

### nas02: array status only, not per-disk health (verified 2026-08-31)
The hub's `smart_devices` collection holds three entries for nas02 — `/dev/md0`,
`/dev/md1`, `/dev/md2` — all `type=mdraid`, `model="Linux MD RAID (raid1)"`,
`state=PASSED`. But `temp`, `hours` and `cycles` are all `0`, serial/firmware are empty,
and the physical `/dev/sda` and `/dev/sdb` are **absent**.

So the UI shows a populated SMART section that is really only *array* status. It will
catch a **degraded mirror**, but not the leading indicators of a failing drive
(reallocated sectors, temperature, power-on hours). Cause: the package runs as
unprivileged `sc-beszel-agent`, which can read `/proc/mdstat` but cannot issue SMART
commands to raw devices.

`smartctl --scan` on the NAS reports `/dev/sda -d scsi` and `/dev/sdb -d scsi`; `-d sat`
usually yields the full ATA attribute table where `-d scsi` gives only a health verdict.
Real per-disk SMART would mean running the agent with root device access, which the
SynoCommunity package does not do by default. **Open item** — DSM's own Storage Manager
still does per-disk SMART and email alerting, so this is a gap in Beszel's view, not in
monitoring overall.

## If the tile stays `down`
The hub (`util01`, 10.0.2.8) connects **to** the agent at `<nas-ip>:45876`. If it can't:
- **Synology:** DSM → Security → Firewall may block 45876 — allow it from 10.0.2.0/24.
- **TrueNAS:** confirm the app is on host networking and listening (`ss -tlnp | grep 45876`).
- Verify from util01: `nc -zv <nas-ip> 45876`.

> Read the failure carefully: **`Connection refused` is good news.** It means the SYN
> reached the host's TCP stack and nothing is listening — routing and firewalls are fine,
> only the agent is missing. A firewall block shows up as a **timeout** instead.

### Port is open but the tile is still `down`
Three distinct causes, all hit in production and all fixed:

1. **The hub caches system addresses in memory.** Changing a system's `host` (the
   `beszel_hub` role now PATCHes it when the inventory address drifts) is not enough —
   the hub keeps dialling the old IP until it is restarted. The role therefore notifies
   `Restart beszel_hub` on any address change. To do it by hand:
   `docker restart beszel` on util01.

2. **The macOS agent can die and stay dead.** `beszel-agent` on Apple Silicon crashes
   with `SIGBUS` inside `gopsutil/v4/sensors.TemperaturesWithContext`
   (`common_darwin.go:96`) while reading SMC temperature sensors — seen on ai01 under
   macOS 26.4.1. launchd's `KeepAlive` restarts the process and the port reopens, so
   **the port test passes while the hub still sees nothing**. ai01 sat "down" for five
   days this way. Fix: `sudo launchctl kickstart -k system/dev.beszel.agent`, then
   confirm the tile flips to `up`. Check for it with
   `grep -c "fatal error" /opt/beszel/agent.err.log` on the Mac.

3. **The agent is alive and serving *corrupt* JSON.** Distinct from (2) — nothing
   crashes and every surface check passes. Hit on ai01 2026-08-31 (agent 0.9.1, macOS
   26.4.1, Apple Silicon): ping, port, TCP connect, SSH handshake, agent key and agent
   version were all fine, and the process had been up a full day with no new
   `fatal error` — yet the hub's `updated` timestamp froze. Two bytes in the response
   had been zeroed:

   - `"m":" pple M4 Pro"` — the `A` (0x41) became 0x00. Escaped by Go's encoder, so
     still *valid* JSON.
   - `...,"v":"0.9.1"},` + `0x00` + `container":null}` — the opening `"` (0x22) of
     `"container"` became a raw NUL. This makes the payload **invalid** JSON.

   The hub discards every unparseable response silently — it logs nothing at default
   level. **Diagnose** by fetching the payload exactly as the hub does, from util01:
   ```bash
   sudo cp /opt/homelab/beszel/data/id_ed25519 /tmp/bzk
   sudo chown "$USER" /tmp/bzk && chmod 600 /tmp/bzk
   ssh -i /tmp/bzk -p 45876 -o StrictHostKeyChecking=no u@<agent-ip> > /tmp/agent.raw
   grep -c -P '\x00' /tmp/agent.raw     # any NUL byte means corrupt
   python3 -m json.tool < /tmp/agent.raw >/dev/null && echo "valid JSON"
   ```
   A healthy agent returns parseable JSON with no NULs. **Fix:** restart the agent
   (`sudo launchctl kickstart -k system/dev.beszel.agent`). Two zeroed bytes at string
   boundaries in a long-lived process point at memory corruption in the agent — this
   host had previously taken a `SIGBUS` in the same binary. Upgrading 0.9.1 → 0.18.8 is
   the durable fix.

## Verifying from the hub, not the UI
The hub's REST API is ground truth — a green tile does not mean the *right* things are
being reported (see nas01 above). Authenticate as the superuser
(`beszel_superuser_email` / `beszel_superuser_password`) against
`/api/collections/_superusers/auth-with-password`, then read:

| Collection | What it tells you |
|---|---|
| `systems` | `status`, `host:port`, `updated`, and `info.v` (**agent version**) |
| `system_stats` | latest `stats` — `d`/`du`/`dp` root disk, `efs` extra filesystems |
| `smart_devices` | per-device SMART: `name`, `type`, `state`, `temp`, `hours` |
| `alerts` | configured alert rules per system |

A stale `updated` while the tile still reads `up` is the tell for cause (3) above.

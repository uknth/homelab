# Maintenance

Keeping the fleet patched, containers current, and data recoverable — the recurring pain
point v3 is meant to solve. Four independent layers, all reporting into a single ntfy sink.

## 1. OS security patches — automatic

Role `system/unattended_upgrades` on every Linux host:

- `unattended-upgrades` enabled, **security pocket only**.
- Auto-install, **no automatic reboot** (reboots are layer 2's job).
- Applies to `gw01`, `cmp01`, `util01`.

## 2. Full OS upgrades — deliberate

A tagged play, run on demand:

```bash
ansible-playbook site.yml --tags patch
```

- `apt update && apt full-upgrade` across the fleet.
- Checks `/var/run/reboot-required`; reboots hosts **serially** (`serial: 1`) so DNS/proxy and
  compute never go down together.
- `gw01` reboots last and alone.

## 3. Container updates — Diun detects, n8n applies, ntfy reports

**Changed 2026-08-30 (user directive).** This section previously read "never
auto-update; Diun only notifies." Stateless services are now upgraded
automatically. The data-loss concern that motivated the original rule is
preserved as a **hold list** rather than a blanket prohibition.

```
Diun (one per docker host, watches local socket)
   │ new digest
   ├──▶ ntfy  homelab-updates          (unchanged — you still see every detection)
   └──▶ n8n webhook /webhook/diun-update
            │
            ├─ status != "update" ──▶ ntfy, no action
            └─ status == "update"
                   │
                   ▼
            ssh (forced command) ──▶ ops/maintenance : maintenance.sh --image <ref>
                   │
                   ├─ project in hold list ──▶ ntfy: held, apply manually
                   └─ otherwise ──▶ docker compose pull + up -d ──▶ ntfy: updated
                   │
                   ▼
            n8n branches on exit code ──▶ ntfy homelab-jobs (pass or fail)
```

### Hold list — never upgraded automatically

`maintenance_hold_projects` in `roles/ops/maintenance/defaults/main.yml`:

| Project | Why |
|---|---|
| `paperless` | Postgres 17 + Redis. An unattended major bump can leave an unreadable data directory |
| `authentik` | Postgres 16 + Redis, and it gates every other service |
| `n8n` | Recreating it would kill the workflow performing the upgrade |

A held project still produces a priority-4 ntfy naming the exact command to run:
`ansible-playbook site.yml --tags <project>`.

### Discovery, not configuration

Neither the host list nor the project list is hand-maintained. Hosts come from
the `docker_hosts` inventory group; projects are discovered from
`/opt/homelab/*/compose/docker-compose.yml` on each host; the image→project
mapping is resolved at run time from `docker ps`. A new service is covered the
moment it is deployed.

### How change is detected (and a trap)

`upgrade_project` compares `docker compose ps -q` (container ids) before and
after `pull` + `up -d`. It deliberately does **not** compare
`docker compose images -q`: that reports the images of *running containers*, so
it reads identically before and after a pull and every project looks "already
current". The pulled image then sits unreferenced until `--prune` deletes it,
leaving containers permanently stale while the job reports success. This bit
`bazarr` and `radarr` on 2026-08-30 before it was fixed.

`up -d` runs unconditionally because it is idempotent — it recreates only the
services whose image actually changed.

Likewise the image→project lookup asks each project `docker compose config
--images` rather than reading `docker ps --format '{{.Image}}'`. Once a tag has
moved (which happens after any pull+prune cycle) `docker ps` reports a bare
image id, and matching on it silently stops finding anything.

### Access model

n8n reaches the script over SSH with a key restricted by a **forced command** in
the `ansible` user's `authorized_keys` — that key can invoke nothing but
`maintenance.sh`, and the script whitelists its own flags. Verified: presenting
the key with `cat /etc/shadow` returns the script's usage text.

### Modes

| Command | Effect |
|---|---|
| `--image <ref>` | Upgrade whichever project runs that image (the Diun path) |
| `--upgrade-all` | Every project not held |
| `--prune` | Reclaim images + build cache, remove containers exited >30 days |
| `--all` | `--upgrade-all` then `--prune` — the weekly job |

### Schedule

`homelab-weekly-maintenance` in n8n runs `--all` on **Sundays 04:00**, after the
nightly restic window. Both workflows are defined as JSON under
`roles/services/productivity/n8n/templates/` and imported with the n8n CLI, so
they live in git rather than only in n8n's database.

### Still true

Manual application remains available and is the only path for held projects.
Watchtower is still not used — the upgrade decision is made by Ansible-managed
config, and every action is reported.

## 4. Backups — Restic → nas02 over SFTP

Role `ops/restic` on `backup_clients`, on a systemd timer (nightly ~02:30 + jitter).

**Scope is deliberately narrow (user directive 2026-08-22): only two data sets.**
Everything else on `/opt/homelab` is reproducible from this Ansible repo, so it's
rebuilt by re-running the role — not restored. Each host declares its own
`restic_paths` (+ `restic_pre_commands`) in host_vars; a host with none self-skips.

| Host | Data set | Pre-hook |
|---|---|---|
| `cmp01` | `/opt/homelab/paperless/export` | `docker exec paperless document_exporter … --delete` (engine-independent export) |
| `util01` | `/opt/homelab/syncthing/data` | — |

- **Repo:** `sftp:backup@10.0.2.3:/restic (SFTP chroot: the share root, not /volume1)/<host>` (Synology; NFS off, SFTP on),
  reached with a dedicated `keys/restic_backup_ed25519` key by the root-run job.
- **Excluded:** bulk media (`/mnt/media`) — TrueNAS snapshots handle that.
- **Retention:** `--keep-daily 7 --keep-weekly 4 --keep-monthly 6`, `restic forget --prune`.
- **Verification:** weekly `restic check --read-data-subset=5%` (Sundays); every run pings a
  dedicated **Uptime Kuma** push monitor on success (`restic_kuma_push_url`) and POSTs to
  **ntfy** `homelab-backup` on failure, so a *silent* backup failure surfaces as a red dot.

**Manual prerequisites (one-time, on the Synology `nas02`):** enable SSH; create a `backup`
user with a home dir; add `keys/restic_backup_ed25519.pub` to its `~/.ssh/authorized_keys`;
create a `/restic (SFTP chroot: the share root, not /volume1)` folder it can write. Add `vault_restic_password` to the vault.
Then `ansible-playbook site.yml --tags backup`.

Secrets: `vault_restic_password`, plus an SSH key for the `backup` user on `nas02`.

## Notification sink

Everything above publishes to **ntfy** on `util01` (one topic per concern):

| Source | ntfy topic | Trigger |
|---|---|---|
| Diun | `homelab-updates` | new image available (one Diun per docker host) |
| n8n jobs | `homelab-jobs` | every n8n run, pass or fail |
| `ops/restic` | `homelab-backup` | backup failed / weekly summary |
| Uptime Kuma | `homelab-alerts` | monitor down (incl. missed backup ping) |
| n8n / GitOps | `homelab-deploy` | deploy started / succeeded / failed |

One app on the phone, subscribed to those topics, covers the whole fleet.

## Alerting (2026-08-23)

Two layers, both pushing to the ntfy topic **`homelab-alerts`** (subscribe on your phone):

- **Uptime Kuma → ntfy** (`uptime_kuma_config`): an `ntfy-alerts` notification (default +
  applied to all monitors), so any DOWN service (`*.puhome.net`) or a silent **Backups**
  push monitor alerts. Covers service availability and, indirectly, host-down.
- **Beszel → ntfy** (`beszel_hub`): a shoutrrr `ntfy://` webhook in the user's settings +
  per-system alerts (Status/CPU/Memory/Disk, 7 hosts × 4). Covers host health — a node down,
  disk filling (relevant for nas01's degraded pool), sustained high CPU/memory. Beszel has no
  sendmail, so email alerts don't work — the ntfy webhook is the channel.

Existing per-source notifications remain: restic (`homelab-backup` + Kuma push), Diun
(`homelab-updates`), GitOps deploy (`homelab-deploy`).

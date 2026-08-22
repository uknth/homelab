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

## 3. Container updates — notify, then Ansible applies

**Never auto-update stateful containers.** A bad upstream release or an unattended Postgres
major-version bump is how you lose data.

- **Diun** on `util01` watches every image on every `docker_hosts` member and pushes to ntfy
  when a new tag/digest is available. Diun only notifies; it changes nothing.
- Updates are applied by **re-running the role**, which is idempotent:
  ```bash
  ansible-playbook site.yml --tags paperless   # pulls + recreates just that service
  ```
  Roles use `community.docker.docker_compose_v2` with `pull: always`.
- This keeps every change reviewable, diffable in git, and reversible. Watchtower-style
  auto-update is **not** used.

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
| Diun | `homelab-updates` | new image available |
| `ops/restic` | `homelab-backup` | backup failed / weekly summary |
| Uptime Kuma | `homelab-alerts` | monitor down (incl. missed backup ping) |
| n8n / GitOps | `homelab-deploy` | deploy started / succeeded / failed |

One app on the phone, subscribed to those topics, covers the whole fleet.

# Storage

How persistent data is laid out across the fleet. This is the single most important spec to
get right — the arr stack and Jellyfin are unusually sensitive to storage topology.

## The golden rule

**Service state on local NVMe; bulk media on NFS.**

- Every application database — Sonarr/Radarr/Prowlarr/Bazarr (SQLite), Jellyfin (SQLite),
  Paperless (Postgres), Kavita (SQLite) — lives on `cmp01`'s **local disk**.
- Only large media files (movies, shows, music, books, downloads) live on **NFS from `nas01`**.

SQLite over NFS corrupts under lock contention. This is the most common way a self-hosted
arr/Jellyfin stack dies, and it is silent until the database is already damaged. Never put a
`config/` directory on the NFS mount.

## Layout on `cmp01`

```
/opt/homelab/                     local NVMe — service state, git-deployable config
  <service>/config/               app config + SQLite DBs   (backed up by Restic)
  <service>/compose/              docker-compose.yml + .env (rendered by Ansible)
  paperless/{pgdata,redis}/       Paperless Postgres + Redis (local, backed up)

/mnt/media/                       single NFS mount from nas01 (see below)
  data/
    torrents/                     qBittorrent active + completed
    usenet/                       (if used later)
    media/
      movies/  shows/  music/  books/
```

`config_dir` / `data_dir` / `service_dir` variables in `group_vars` resolve under
`/opt/homelab`. Do not spread service config across the NFS mount.

## The single-mount requirement

`nas01` exports **one** dataset, mounted **once** at `/mnt/media`, with `data/` inside it.

Hardlinks and atomic (instant, no-copy) moves only work *within a single filesystem*. The arr
apps hardlink a completed download into the library instead of copying it — this only works if
the downloader's `torrents/` dir and the library's `media/` dir are under the same mount.
Splitting media into separate mounts (`/mnt/movies`, `/mnt/tv`, …) breaks hardlinking, doubles
disk usage, and turns imports into slow full copies.

## Permissions — pin a shared GID

Ownership drift between host and containers is the second-most-common arr failure. Fix it once:

- Create a `media` group with a **pinned GID** (e.g. `1500`) — same number on the TrueNAS
  dataset and on `cmp01`.
- Own the exported dataset `root:media` (or `apps:media`) with `2775` (setgid) so new files
  inherit the group.
- Set `PUID`/`PGID` **identically** in every container that touches `/mnt/media`.
- Do **not** derive UID/GID from `ansible_user` at runtime (the v2 mistake — it drifts per
  host). Pin `media_gid: 1500` and a `media_uid` in `hosts/group_vars/all/vars.yml`.

## `nas01` — TrueNAS (semi-managed)

NFS is live (`10.0.2.6:2049`). Ansible manages the export through the TrueNAS **REST API**
(`ansible.builtin.uri` against the middleware; no core module exists), idempotently:

1. Ensure the `media` dataset exists.
2. Ensure an NFS share for it exists, restricted to `10.0.2.5` (`cmp01`), with
   `maproot`/`mapall` to the `media` UID/GID.

Needs `vault_truenas_api_key` in the vault. `cmp01` mounts it via a `system/nfs_mounts` role
(`ansible.posix.mount`, `fstype: nfs`, `state: mounted`, in fstab).

## `nas02` — Synology (backup target)

- **NFS is disabled**; the box runs SMB + SSH (verified: ports 22/80/443/445/5000 open, 2049
  closed).
- Restic writes an encrypted, deduplicated repository over **SFTP** (`sftp:backup@10.0.2.3:…`).
- Requires SSH enabled on the Synology and a dedicated `backup` user. No NFS/SMB mount needed
  on the clients.

See [`maintenance.md`](maintenance.md) for the Restic policy (schedule, retention, what's
included, verification).

## What is backed up vs. snapshotted

| Data | Mechanism | Where |
|---|---|---|
| Service config + databases (`/opt/homelab`) | Restic → `nas02` over SFTP | `backup_clients` |
| Paperless documents | `document_exporter` pre-hook, then Restic | `cmp01` |
| Bulk media (`/mnt/media`) | TrueNAS snapshots + replication (not Restic — too large) | `nas01` |

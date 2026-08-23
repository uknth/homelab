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

## Permissions — align to the existing owner (uid/gid 1001)

Ownership drift between host and containers is the second-most-common arr failure. The
existing library on the NAS is **already owned by uid/gid 1001** — which is exactly the
`ansible` user on `cmp01` (the export has no `maproot`, so NFS passes the client's uid/gid
straight through). So v3 **aligns to 1001** rather than forcing a new `media` GID and chowning
500+ items:

- `media_uid: 1001` / `media_gid: 1001` in `hosts/group_vars/all/vars.yml`.
- Every container that touches the media mount runs `PUID`/`PGID` = these, so files it writes
  stay owned consistently and the arr apps can hardlink/move freely.
- These are **pinned constants**, not derived from `ansible_user` at runtime (the v2 mistake —
  it drifts per host). They just happen to equal `cmp01`'s ansible uid today.

(If the fleet ever gains a second media consumer whose ansible uid differs, switch the export
to a `mapall`/`maproot` of 1001 on the TrueNAS side so the client uid no longer has to match.)

## Layout of the export

The NFS export is the single handoff dir — one filesystem, so hardlinks/atomic moves work:

```
/mnt/media/data/            (NFS mount of nas01:/mnt/data-pool/media/data)
  downloads/                downloader output
  movies/  shows/  music/  books/   libraries (arr-managed)
  whisparr/  yt/
```

## `nas01` — TrueNAS (semi-managed via API)

NFS is live (`10.0.2.6:2049`), TrueNAS SCALE 25.10. The `media` dataset
(`data-pool/media`) and its `data` export **already exist with real data** — Ansible does
**not** create datasets. The `services/storage/truenas_nfs` role manages the *export
definition* through the TrueNAS **REST API** (`ansible.builtin.uri`; no core module exists),
idempotently (create-if-missing, update-if-drifted):

- Ensures the export for `/mnt/data-pool/media/data` exists and is enabled.
- Restricts allowed clients (`hosts`) to `10.0.2.5` (`cmp01`) — verified not to disrupt an
  established mount.

The role runs on the **control node** (`connection: local`, `playbooks/hosts/nas01.yml`)
against the API; nas01 has no ansible account. Needs `vault_truenas_api_key`.

`cmp01` mounts the export via the `system/nfs_mounts` role (`ansible.posix.mount`,
`fstype: nfs4`, `state: mounted`, persisted in fstab) at `/mnt/media/data`.

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
| Paperless documents | `document_exporter` pre-hook -> Restic → `nas02` (SFTP) | `cmp01` |
| Syncthing data (`/opt/homelab/syncthing/data`) | Restic → `nas02` (SFTP) | `util01` |
| Bulk media (`/mnt/media`) | TrueNAS snapshots (not Restic — too large) | `nas01` |

**Backup scope is deliberately narrow** (user directive 2026-08-22): only the two
data sets above are Restic'd — *not* all of `/opt/homelab`. Everything else
(service config, DBs) is reproducible from this Ansible repo, so it is rebuilt by
re-running the role, not restored from backup.

## Scratch pool for in-progress downloads (2026-08-23)

In-progress/incomplete downloads live on **`scratch-pool`** (a separate, healthy
pool on nas01), not the RAIDZ2 `data-pool` — this keeps download write-churn and
fragmentation off the main pool (and off the degraded array). **Completed**
downloads still land on `data-pool` alongside the library, so hardlinks / atomic
moves are preserved.

- `scratch-pool/incomplete` (NFS) → mounted on cmp01 at `/mnt/scratch/incomplete`
  (`system/nfs_mounts`), mounted into nzbget + qBittorrent as `/scratch`.
- **nzbget:** `InterDir=/scratch/nzbget` (in-progress) → `DestDir=/data/downloads/usenet` (complete).
- **qBittorrent:** `Session\TempPath=/scratch/qbittorrent` (in-progress) → `DefaultSavePath=/data/downloads/torrents` (complete).
- On completion the client does a one-time cross-pool copy scratch→data-pool; the
  *arr then hardlinks data-pool→library (same filesystem). Owned uid/gid 1001
  (scratch dataset chowned to 1001 to match the media convention).

**Codified (2026-08-23):** the `arr` role now enforces the client config-file
settings (nzbget Inter/DestDir + optional creds; qbit Temp/DefaultSavePath +
`AuthSubnetWhitelist`) via `tasks/download_config.yml`. LSIO apps rewrite their
conf from memory on shutdown, so the role edits **only while the container is
stopped**, and only when the conf **drifts** (idempotent + non-disruptive; verified
it self-heals a broken path and no-ops when correct). nzbget creds are enforced
only if `vault_nzbget_username`/`vault_nzbget_password` are set (else left as-is).

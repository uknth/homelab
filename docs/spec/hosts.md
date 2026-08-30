# Hosts

Narrative spec for every host. The executable source of truth for IPs/groups is
[`../../hosts/hosts.yml`](../../hosts/hosts.yml); where they conflict, the inventory wins.

Hardware and OS below were verified by direct probe on 2026-08-21.

## Managed hosts

Provisioned by Ansible under the `ansible` service account.

### `gw01` — Gateway · `10.0.2.2`

| | |
|---|---|
| Hardware | Raspberry Pi 5 |
| OS | Raspberry Pi OS (Debian 12 bookworm, aarch64) |
| Purpose | Network edge — DNS, reverse proxy, remote access |
| Runs | Blocky (local DNS), nginx (`*.puhome.net` reverse proxy), certbot (Cloudflare DNS-01 wildcard TLS), Tailscale subnet router |

Currently still running the **v2** stack (Blocky + ~37 nginx vhosts + Cockpit). v3 rebuild
replaces the nginx vhost set and rewrites the Blocky config (local-names-only + fallback).

### `cmp01` — Compute · `10.0.2.5`

| | |
|---|---|
| Hardware | 8-core / 16-thread, **62 GiB RAM**, NVIDIA RTX A4000 16 GB, 915 GB NVMe |
| OS | Debian 12 (bookworm, x86_64) |
| Purpose | All heavy self-hosted services; the fleet's only `gpu_nodes` member |
| Runs | arr stack, Jellyfin + Jellyseerr, Kavita, Paperless-ngx |

Docker 29 and the NVIDIA driver (535) are already installed. Service *state* lives on the
local NVMe; bulk media is NFS-mounted from `nas01` at `/mnt/media`. The A4000 handles Jellyfin
NVENC transcoding and Bazarr/Whisper subtitle generation (Ampere has no NVENC session cap).

> Photos (Immich) and password management (Vaultwarden) are **explicitly out of scope** for
> v3 — do not re-add them.

### `util01` — Utility / observability · `10.0.2.8`

| | |
|---|---|
| Hardware | Mini PC · 4 cores · 7.6 GiB RAM · 1.8 TB disk |
| OS | **Debian 13 (trixie)**, x86_64 — note: newer than gw01/cmp01 (bookworm) |
| Purpose | Observability, dashboards, ops tooling, the GitOps deploy agent |
| Runs | Beszel hub, Uptime Kuma, ntfy, Diun, Glance, Portainer, Dozzle, n8n |

n8n here is also the GitOps deploy trigger — see [`../plan/gitops.md`](../plan/gitops.md).
Docker is not yet installed. Accessible via the github key (sudo not yet passwordless).

> **Mixed Debian releases:** `util01` is trixie, `gw01`/`cmp01` are bookworm. The `system/apt`
> and `system/docker` roles must derive the apt suite/codename from
> `ansible_distribution_release` rather than hardcoding `bookworm`, or the Docker repo won't
> resolve on `util01`.

### `ai01` — AI workloads · `10.0.2.9`

| | |
|---|---|
| Hardware | Mac Mini M4 Pro |
| OS | macOS 15 (`macos` group) |
| Purpose | Local LLM inference |
| Runs | Ollama (native via Homebrew + launchd — **not** Docker; Docker on macOS gets no Metal acceleration), a Qwen model, and paperless-ai |

Unified memory holds larger models than the A4000's 16 GB VRAM, so inference lives here, not
on `cmp01`.

### `ctl01` — Admin / control node · `10.0.2.4`

| | |
|---|---|
| Hardware | Mac Mini M1 |
| OS | macOS 15 (`macos` group) |
| Purpose | Ansible control node, Tailscale jumphost, local development |
| Runs | Colima (container runtime), dev toolchain (Go, Node, kubectl, Helm, kind, AWS CLI), Tailscale |

Docker Engine can't run natively on macOS, so **Colima** provides the Docker-compatible
socket the `docker` CLI and `dev/kind` need. Started as a `brew services` unit so the VM comes
up on login. `ai01` deliberately does **not** run Colima — its only workload (Ollama) is
native for Metal acceleration.

Holds an Omada DHCP reservation at `10.0.2.4` (assigned 2026-08-30).

## Unmanaged / semi-managed hosts

| Host | IP | Hardware / OS | Status | Purpose |
|---|---|---|---|---|
| `router` | `10.0.2.1` | Omada, dual-WAN | unmanaged | Edge routing (ACT + Airtel load share) |
| `omada` | `10.0.2.21` | Omada controller | unmanaged | Network management UI/API |
| `dns01` | `10.0.2.7` | Pi Zero / Pi-hole v5 | unmanaged | Ad/tracker blocking; Blocky's blocking upstream |
| `nas01` | `10.0.2.6` | TrueNAS, RAIDZ2 | **semi-managed** | Primary storage; NFS exports driven via TrueNAS API |
| `nas02` | `10.0.2.3` | Synology, 4 TiB RAID1 | unmanaged | Backup target (Restic over SFTP; NFS is off) |

## Cross-cutting inventory groups

- `docker_hosts` — `gw01`, `cmp01`, `util01`
- `gpu_nodes` — `cmp01`
- `storage_nodes` — `nas01`, `nas02`
- `backup_clients` — `cmp01`, `util01`, `gw01`

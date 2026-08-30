# Service Catalog

Every service planned for v3: which host, target role path, domain, and where a working v2
implementation exists in `archive/` to reference. Per user direction, v3 roles are written
**fresh** — `archive/` is read for insight into how things ran, not copied as a template.

**Status legend:** 🟢 role exists · 🟡 planned, has a v2 reference · 🔵 planned, net-new ·
⚪ retired, not planned.

> **Single Sign-On:** every user-facing service in this catalog sits behind **Authentik**
> (on `util01`), enforced by nginx forward-auth on `gw01`. There is one login for the whole
> homelab — see [`auth.md`](auth.md) for the integration tiers. New services default to
> Tier-2 forward-auth unless they support native OIDC.

## System / bootstrap roles

| Role (target path) | Applies to | Status | v2 reference (insight only) |
|---|---|---|---|
| `system/ansible_user` | all | 🟢 built | — |
| `system/hostname` | all | 🟢 built | — |
| `system/network` | macos | 🟢 built | — |
| `system/apt` | linux | 🟢 built | moved from `archive/roles/packages/system/apt` (rewritten) |
| `system/ntp` | linux | 🟢 built | moved from archive; rewritten native (systemd-timesyncd, no galaxy dep) |
| `system/docker` | linux | 🟢 built | moved from `archive/roles/packages/system/docker`; codename-dynamic (bookworm+trixie) |
| `system/unattended_upgrades` | linux | 🟢 built | net-new — maintenance layer 1 (security pocket, no auto-reboot) |
| `system/tailscale` | linux | 🟡 planned | `artis3n.tailscale` |
| `system/beszel_agent` | linux | 🟢 built + **active** | native install; hub key set, agents live fleet-wide |

> **Monitoring coverage:** agents run on all Linux hosts (systemd) + macOS hosts (launchd); systems auto-registered by `beszel_hub` (`beszel_register_hosts`). Live: gw01, cmp01, util01, ai01, ctl01. `nas01`/`nas02` need a manual container — see [`../runbooks/beszel-nas-agents.md`](../runbooks/beszel-nas-agents.md). dns01 excluded (no armv6 agent build).
| `system/nvidia_docker` | cmp01 | 🟢 built | registers NVIDIA runtime with Docker (Jellyfin NVENC) |
| `system/nfs_mounts` | cmp01 | 🟢 built | mounts nas01:/mnt/data-pool/media/data at /mnt/media/data (NFSv4.2, fstab); read/write as uid 1001 |
| `services/storage/truenas_nfs` | nas01 (API) | 🟢 built | manages TrueNAS NFS export defs via REST API (create/update); restricts media export to cmp01 |
| `system/brew` | macos | 🟡 planned | — |
| `system/colima` | ctl01 | 🔵 net-new | — (Docker-compatible runtime on macOS; required by `dev/kind`) |

## `gw01` — Gateway services

| Service | Target role | Domain | Status | Notes |
|---|---|---|---|---|
| Blocky (local DNS) | `services/network/blocky` | `dns.puhome.net` | 🟢 built + **live** | local names + Pi-hole→Quad9 fallback; blocking off; cutover done 2026-08-22 |
| nginx (reverse proxy) | `services/network/nginx` | — | 🟢 built | native; per-site vhosts from `nginx_sites`; Authentik forward-auth opt-in; **deploy needs the wildcard cert** |
| certbot (wildcard TLS) | `services/network/certbot` | — | 🟢 built | Cloudflare DNS-01, single `*.puhome.net` cert; **needs `vault_cloudflare_dns_token` + `vault_certbot_email`** |
| Tailscale subnet router | `system/tailscale` | — | 🟢 built | native; advertises `10.0.2.0/24`; **needs `vault_tailscale_authkey`** |

Pi-hole on `dns01` stays **unmanaged**, but v3 must add a **conditional-forward
`puhome.net → 10.0.2.2`** to it (one-time, documented) so the secondary resolver answers local
names.

## `cmp01` — Compute services

| Service | Target role | Domain | Status | v2 reference |
|---|---|---|---|---|
| arr stack (**nzbget (primary Usenet)**, qbittorrent, prowlarr, sonarr, radarr, lidarr, bazarr) | `services/media/arr` | per-app (SSO) | 🟢 built + live | VPN=qbit only (OpenVPN/PIA, port-fwd); nzbget + *arr on bridge |
| gluetun (VPN, **qBittorrent only**) | part of `services/media/arr` | — | 🟢 built + live | PIA OpenVPN (not WireGuard); port-forwarding active; egress verified tunneled |
| Jellyfin + Jellyseerr | `services/media/jellyfin` | `video.puhome.net`, `seer.puhome.net` | 🟢 built + live | NVENC via A4000 (GPU visible in-container); own auth (native clients) |
| Kavita | `services/media/kavita` | `books.puhome.net` | 🟢 built + live | **pinned to 0.8.2** (0.9.x hangs on MigrateEmailTemplates first-boot) |
| Paperless-ngx | `services/documents/paperless` | `docs.puhome.net` | 🟢 built + live | migrated (182 docs) to local disk; postgres:17 glibc; tika/gotenberg; **Authentik OIDC login** (tier-1) + API tokens |

**Out of scope for v3 (user directive):** Immich (photos), Vaultwarden (passwords). Removed
from `playbooks/hosts/cmp01.yml`.

**arr VPN scope:** v3 puts **only qBittorrent + its port-forward helper** behind gluetun. The
\*arr apps reach indexers over HTTPS and stay on the normal bridge network, so a VPN blip no
longer takes down the whole stack (the v2 failure mode). PIA is the provider (port-forward
helper is already PIA-shaped).

## `util01` — Observability / ops services

| Service | Target role | Domain | Status | v2 reference |
|---|---|---|---|---|
| Authentik (SSO) | `services/auth/authentik` | `auth.puhome.net` | 🟢 built + **live** | v2026.8.0; server+worker+PG+Redis on util01; served via nginx+wildcard TLS; akadmin bootstrapped |
| Authentik config (forward-auth) | `services/auth/authentik_config` | — | 🟢 built + **live** | domain-level forward-auth provider via API (idempotent); embedded outpost gates all *.puhome.net |
| Beszel (hub) | `services/monitoring/beszel_hub` | `metrics.puhome.net` | 🟢 built + live | agents active fleet-wide |
| Uptime Kuma | `services/monitoring/uptime_kuma` | `synthetics.puhome.net` | 🟢 built + live | — |
| ntfy | `services/monitoring/ntfy` | `ntfy.puhome.net` | 🟢 built + live | notification sink (open pub/sub; token ACLs = later) |
| Diun | `services/monitoring/diun` | — | 🟢 built + live | image-update notifier, **one per docker host** (single-endpoint provider). -> ntfy `homelab-updates` + n8n upgrade webhook. `watchByDefault: true` (fixed 2026-08-30 — it was unset, so Diun watched nothing since deployment) |
| Dozzle | `services/monitoring/dozzle` | `logs.puhome.net` | 🟢 built + live | live container logs |
| Homepage | `services/dashboard/homepage` | `dash.puhome.net` | 🟢 built + live | service links + widgets + Docker(util01); replaced Glance. **Nodes tab** (2026-08-30): per-node container inventory, discovered live from the Portainer API |
| Glance | `services/dashboard/glance` | — | ⚪ retired | replaced by Homepage (role kept in repo) |
| Portainer | `services/dashboard/portainer` | `docker.puhome.net` | 🟢 built + live | local role (not the ext galaxy one) |
| n8n | `services/productivity/n8n` | `n8n.puhome.net` | 🟢 built + live | also GitOps trigger (phase 7). Owns two maintenance workflows (2026-08-30), defined as JSON in the role and imported via the n8n CLI |
| Syncthing | `services/productivity/syncthing` | `sync.puhome.net` | 🟢 built + live | standalone file sync; GUI behind forward-auth; data under `/opt/homelab/syncthing/data` (Restic-backed) |

## `ai01` — AI workloads

| Service | Target role | Status | Notes |
|---|---|---|---|
| Ollama (native) | `services/ai/ollama` | 🔵 net-new | Homebrew + launchd, **not Docker** (Metal accel) |
| Qwen model pull | part of `services/ai/ollama` | 🔵 net-new | latest Qwen |
| paperless-ai | `services/ai/paperless_ai` | 🔵 net-new | auto-tagging, points at Ollama here |

## `ctl01` — Dev toolchain

| Package | Target role | Status | v2 reference |
|---|---|---|---|
| Go | `dev/go` | 🟡 planned | `archive/roles/packages/dev/go` |
| Node.js | `dev/node` | 🟡 planned | `geerlingguy.nodejs` |
| kubectl | `dev/kubectl` | 🟡 planned | `archive/roles/packages/system/kubectl` |
| Helm | `dev/helm` | 🟡 planned | `archive/roles/packages/system/helm` |
| kind | `dev/kind` | 🟡 planned | `archive/roles/packages/system/kind` |
| AWS CLI | `dev/awscli` | 🟡 planned | `deekayen.awscli2` |

Also the **Ansible control node** — runs the playbooks against the fleet.

## Maintenance / ops (cross-host)

| Concern | Target role / mechanism | Status | Spec |
|---|---|---|---|
| Security patches | `system/unattended_upgrades` | 🔵 net-new | [maintenance](maintenance.md#1-os-security-patches--automatic) |
| Full OS upgrades | `--tags patch` play | 🔵 net-new | [maintenance](maintenance.md#2-full-os-upgrades--deliberate) |
| Container updates | Diun notify + Ansible apply | 🔵 net-new | [maintenance](maintenance.md#3-container-updates--notify-then-ansible-applies) |
| Backups | `ops/restic` → nas02 (SFTP) | 🔵 net-new | [maintenance](maintenance.md#4-backups--restic--nas02-over-sftp) |
| GitOps deploy | builds.sr.ht + n8n | 🔵 net-new | [gitops](../plan/gitops.md) |
| Container upgrades + space reclaim | `ops/maintenance` (util01) driven by n8n | 🟢 built + live | [maintenance](maintenance.md#3-container-updates--diun-detects-n8n-applies-ntfy-reports) |

## Retired in v2, not planned for v3

Immich, Vaultwarden, Affine, Joplin, Kener, Miniflux, Seafile, Silverbullet, Statping,
Trillium, Websurfx, Homer, Sourcebot, the k8s media stack, k3s, Samba, Syncthing, Navidrome/
Headphones (music), Fusion, opencloud, webdav. Pull one back only by adding it here and to a
roadmap phase first.

> **2026-08-22 additions:**
> - **Filebrowser** (`services/productivity/filebrowser`, cmp01, `files.puhome.net`) — web file manager rooted at `/mnt/media/data`, runs as uid 1001 (writes land correctly), `noauth` behind Authentik forward-auth.
> - **Portainer** now manages all docker hosts: local `primary` (util01) + **Agent** envs on cmp01/gw01 (`services/dashboard/portainer_agent`, codified registration in the portainer role).
> - **Dozzle** aggregates logs from every docker host via remote agents (`services/monitoring/dozzle_agent` on cmp01/gw01 + `DOZZLE_REMOTE_AGENT`).
> - Agents deploy via `playbooks/ops/agents.yml` (`docker_hosts:!util01`), wired into `site.yml`.

> **Homepage dashboard widgets (2026-08-23):** `dash.puhome.net` now has live
> widgets. The `homepage` role fetches keys at deploy (arr `config.xml`, bazarr
> `config.yaml`, nzbget.conf, jellyseerr `settings.json` via slurp on cmp01;
> truenas/authentik/qbit from vault; mints a Jellyfin API key idempotently) and
> renders them into the on-host `services.yaml` (never git). **Live widgets:**
> Sonarr, Radarr, Lidarr, Prowlarr, Bazarr, NZBGet, qBittorrent, Jellyseerr,
> Jellyfin, TrueNAS (pools), Authentik, **Portainer** (token minted at deploy),
> **Paperless** (DRF token for uknth), **Uptime Kuma** (public status page `homelab`
> created by uptime_kuma_config). **Tiles only (need creds):** Kavita, Synology, Pi-hole. Top:
> greeting, datetime, util01 resources, Open-Meteo weather (Bengaluru — change in
> defaults), search.
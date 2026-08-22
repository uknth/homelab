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
| `system/beszel_agent` | linux | 🟢 built | moved from archive; native install, **guarded on hub key** (activates in phase 4) |
| `system/nfs_mounts` | cmp01 | 🔵 net-new | `archive/roles/packages/ops/mount-nas1` (was CIFS→wrong NAS; v3 is NFS→nas01) |
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
| arr stack (qbittorrent, prowlarr, sonarr, radarr, lidarr, bazarr) | `services/media/arr` | per-app | 🟡 planned | `archive/roles/docker2/media/arr` |
| gluetun (VPN, **qBittorrent only**) | part of `services/media/arr` | — | 🟡 planned | same — but v3 scopes VPN to qbit, not the whole stack |
| Jellyfin + Jellyseerr | `services/media/jellyfin` | `video.puhome.net` (alias `media`) | 🟡 planned | `archive/roles/docker2/media/jellyfin` |
| Kavita | `services/media/kavita` | `books.puhome.net` | 🔵 net-new | none |
| Paperless-ngx | `services/documents/paperless` | `docs.puhome.net` | 🟡 planned | `archive/roles/docker2/services/paperless-ngx` |

**Out of scope for v3 (user directive):** Immich (photos), Vaultwarden (passwords). Removed
from `playbooks/hosts/cmp01.yml`.

**arr VPN scope:** v3 puts **only qBittorrent + its port-forward helper** behind gluetun. The
\*arr apps reach indexers over HTTPS and stay on the normal bridge network, so a VPN blip no
longer takes down the whole stack (the v2 failure mode). PIA is the provider (port-forward
helper is already PIA-shaped).

## `util01` — Observability / ops services

| Service | Target role | Domain | Status | v2 reference |
|---|---|---|---|---|
| Authentik (SSO) | `services/auth/authentik` | `auth.puhome.net` | 🔵 net-new | — (identity provider for the whole fleet; see [auth.md](auth.md)) |
| Beszel (hub) | `services/monitoring/beszel_hub` | `metrics.puhome.net` | 🟡 planned | `archive/roles/docker2/monitor/beszel` |
| Uptime Kuma | `services/monitoring/uptime_kuma` | `synthetics.puhome.net` | 🔵 net-new | — |
| ntfy | `services/monitoring/ntfy` | `ntfy.puhome.net` | 🔵 net-new | — (notification sink) |
| Diun | `services/monitoring/diun` | — | 🔵 net-new | — (image-update notifier) |
| Dozzle | `services/monitoring/dozzle` | `logs.puhome.net` | 🔵 net-new | — (live container logs) |
| Glance | `services/dashboard/glance` | `dash.puhome.net` | 🟡 planned | `archive/roles/docker2/services/glance` |
| Portainer | `services/dashboard/portainer` | `docker.puhome.net` | 🟡 planned | `uknth/ansible-role-portainer` |
| n8n | `services/productivity/n8n` | `n8n.puhome.net` | 🔵 net-new | — (also GitOps trigger, see [gitops](../plan/gitops.md)) |

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

## Retired in v2, not planned for v3

Immich, Vaultwarden, Affine, Joplin, Kener, Miniflux, Seafile, Silverbullet, Statping,
Trillium, Websurfx, Homer, Sourcebot, the k8s media stack, k3s, Samba, Syncthing, Navidrome/
Headphones (music), Fusion, opencloud, webdav. Pull one back only by adding it here and to a
roadmap phase first.

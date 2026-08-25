# Dashboard — Homarr (dash.puhome.net)

Homarr (`homarr-labs/homarr` v1) replaced gethomepage/homepage on util01 as of
2026-08-25. **Key difference:** Homepage was declarative YAML (fully codified);
**Homarr is database-driven** — the board layout, tiles, widgets and integrations
are configured in its UI and persisted in `/opt/homelab/homarr/appdata` (SQLite,
secrets encrypted with `SECRET_ENCRYPTION_KEY`). So the *deployment* is codified
(`services/dashboard/homarr`); the *board* is UI state on a persisted volume
(and covered by backups if added to the restic set).

## What's codified
- Container (`ghcr.io/homarr-labs/homarr:latest`, port 7575), `/appdata` volume,
  docker.sock (for the Docker/host widgets), `SECRET_ENCRYPTION_KEY` (vault-pinned
  — never change it or the DB becomes unreadable).
- Auth: `AUTH_PROVIDERS=credentials,oidc` — a bootstrap credentials admin **plus**
  Authentik OIDC (button "Authentik"). Provider codified in `authentik_oidc_apps`
  (slug `homarr`, callback `https://dash.puhome.net/api/auth/callback/oidc`).
- Reverse proxy: `dash` vhost → util01:7575, **`sso: false`** (Tier-1 OIDC — nginx
  must not intercept the callback; same rule as Beszel/Jellyfin).

## First-run (one-time UI)
1. Open `dash.puhome.net` → onboarding (`/init`). Create the admin user
   **`uknth`** with `vault_homarr_password`. (Or click **Authentik** to sign in via
   SSO; promote that user to admin from the credentials admin if needed.)
2. Flip `homarr_oidc_auto_login: true` in the role defaults for 1-click SSO once
   you've confirmed the Authentik button works.

## Board recipe (rebuild the old Homepage layout)
Create a board and add these groups/apps. Icons: type the name in Homarr's icon
picker (it pulls from the same dashboard-icons set Homepage used). Live widgets
use Homarr **Integrations** (Settings → Integrations → add, with URL + API key);
attach a widget to the board pointing at the integration.

Apps talk to services by host:port (not the reverse proxy): media apps on
`cmp01.host.puhome.net`, util apps on `util01.host.puhome.net`.

| Group | App | Link | Integration/widget (URL) |
|---|---|---|---|
| Media | Jellyfin | video.puhome.net | Jellyfin — http://cmp01.host.puhome.net:8096 |
| Media | Jellyseerr | seer.puhome.net | Jellyseerr — :5055 |
| Media | Kavita | books.puhome.net | — |
| Downloads | Prowlarr | prowlarr.puhome.net | Prowlarr — :9696 |
| Downloads | Sonarr | sonarr.puhome.net | Sonarr — :8989 |
| Downloads | Radarr | radarr.puhome.net | Radarr — :7878 |
| Downloads | Lidarr | lidarr.puhome.net | Lidarr — :8686 |
| Downloads | Bazarr | bazarr.puhome.net | Bazarr — :6767 |
| Downloads | NZBGet | nzbget.puhome.net | NZBGet — :6789 (user/pass) |
| Downloads | qBittorrent | torrent.puhome.net | qBittorrent — :8080 (admin) |
| Music | Navidrome | music.puhome.net | — |
| Music | LMS (WiiM) | lms.puhome.net | — |
| Music | slskd | soulseek.puhome.net | — |
| Music | Soularr | soularr.puhome.net | — |
| Docs & Files | Paperless | docs.puhome.net | Paperless — :8000 |
| Docs & Files | Filebrowser | files.puhome.net | — |
| Docs & Files | Syncthing | sync.puhome.net | — |
| Storage | TrueNAS | https://10.0.2.6 | — |
| Storage | Synology | https://10.0.2.7:5001 | — |
| System | Authentik | auth.puhome.net | — |
| System | Portainer | docker.puhome.net | Docker (native, via docker.sock) |
| System | Beszel | metrics.puhome.net | — |
| System | Uptime Kuma | synthetics.puhome.net | — |
| System | Dozzle | logs.puhome.net | — |
| System | n8n | n8n.puhome.net | — |
| System | ntfy | ntfy.puhome.net | — |

Extra widgets Homarr provides natively (no key): clock/date, weather (Bengaluru
12.9716, 77.5946), notebook, RSS, **Docker containers** + **health/host stats**
(via the mounted docker.sock), bookmarks, search (DuckDuckGo).

API keys: each *arr key is at Settings → General → API Key in that app; TrueNAS /
Authentik / qbit keys are in the vault. Homarr encrypts them into its DB.

## Revert to Homepage
The homepage role is kept. In `playbooks/hosts/util01.yml` uncomment the
`services/dashboard/homepage` role (comment out homarr), point the `dash` vhost
back to `util01:8094 sso:true`, and re-run `--tags homepage,nginx`.

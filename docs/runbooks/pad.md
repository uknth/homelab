# pad — scratchpad (flatnotes) at pad.puhome.net

Plain-markdown notes for people and agents. flatnotes `v5.5.5`
(`roles/services/productivity/flatnotes`), runs on cmp01, published on `:8105`,
proxied by gw01 as `https://pad.puhome.net`.

## The rule

**No secrets and no work data, ever.** There is no login: `FLATNOTES_AUTH_TYPE=none`,
vhost `sso: false`. The only gate is the nginx allow-list on gw01 (`allow:` on the
`pad` entry in `hosts/host_vars/gw01/vars.yml`): LAN `10.0.2.0/24` and Tailscale
`100.64.0.0/10` are allowed, every other source gets 403. Anyone on either network
can read, write and delete.

## API

Base: `https://pad.puhome.net/api/` (no token needed; verified against flatnotes
v5.5.5 `server/main.py`). Interactive docs: `/docs`, schema: `/openapi.json`.

| Action | Request |
|---|---|
| List / search | `GET /api/search?term=<q>&sort=score\|title\|lastModified&order=asc\|desc&limit=<n>` (`term=*` lists all) |
| Get note | `GET /api/notes/{title}` -> `{title, content, last_modified}` |
| Create | `POST /api/notes` body `{"title": "...", "content": "..."}`; 409 if the title exists |
| Update / append | `PATCH /api/notes/{title}` body `{"new_title"?: "...", "new_content"?: "..."}`. `new_content` **replaces** the body: to append, GET first, then PATCH the concatenation |
| Delete | `DELETE /api/notes/{title}` |

Also: `GET /api/tags`, `GET /health`, attachments under `/api/attachments`.

```bash
curl -s 'https://pad.puhome.net/api/search?term=*&limit=20'
curl -s -X POST https://pad.puhome.net/api/notes -H 'Content-Type: application/json' \
  -d '{"title":"todo","content":"- buy milk"}'
```

Titles are file names (`<title>.md`): no `/` or other filesystem-invalid characters.

## Where the files live

cmp01: `/opt/homelab/pad/data/<title>.md` (owner uid/gid 1001, the `ansible` user).
The search index is `/opt/homelab/pad/data/.flatnotes`; it is rebuilt automatically
and excluded from backup. Compose: `/opt/homelab/pad/compose`.

## Backup and restore one note

`/opt/homelab/pad/data` is in cmp01's `restic_paths` (nightly to nas02, see
`hosts/host_vars/cmp01/vars.yml`). Restore a single note:

```bash
# on cmp01, as root (repo/password env as set up by ops/restic; see /usr/local/bin/homelab-backup)
restic snapshots
restic restore latest --target /tmp/pad-restore --include '/opt/homelab/pad/data/<title>.md'
install -o 1001 -g 1001 -m 0644 /tmp/pad-restore/opt/homelab/pad/data/<title>.md /opt/homelab/pad/data/
```

flatnotes picks the file up on its next index sync (or `docker restart flatnotes`).

## Monitoring and updates

Uptime Kuma gets an HTTP monitor automatically from the `nginx_sites` entry
(it checks `https://pad.puhome.net/`; Kuma sits inside the allow-list). Diun
watches all containers by default, so new flatnotes tags notify via ntfy; bump
`flatnotes_image` in the role defaults to upgrade.

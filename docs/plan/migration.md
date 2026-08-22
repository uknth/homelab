# Migration: archive/ (v2) → roles/ (v3)

Role-by-role mapping for porting a working v2 implementation into the new v3 structure. Use
this alongside [`../spec/services.md`](../spec/services.md) (which says *what's planned and
where*) and [`../spec/conventions.md`](../spec/conventions.md) (which says *what the new
layout looks like*).

## What carries over unchanged

The core Docker-role task pattern from v2 is still correct and should be reused as-is:

1. Create required directories (`ansible.builtin.file` or `mkdir -p`).
2. Template `docker-compose.yml.j2` (and any config files) into the service directory.
3. Deploy with `community.docker.docker_compose_v2`.

Variable-prefix convention (`vw_*`, `arr_*`, etc.) and the `defaults/main.yml` +
`handlers/main.yml` + `tasks/main.yml` + `templates/` role skeleton also carry over
unchanged.

## What changes

| v2 | v3 | Why |
|---|---|---|
| `roles/docker/<category>/<service>/` | `roles/services/<domain>/<service>/` | Renamed category → domain (`media`, `photos`, `documents`, `productivity`, `network`, `monitoring`, `dashboard`, `ai`) to group by what the service *does* for the user, not by deployment mechanism |
| `roles/packages/<category>/<package>/` | `roles/system/<name>/` (host-level) or `roles/dev/<name>/` (ctl01 toolchain) | Split by *when it runs* (host bootstrap vs. dev workstation setup) rather than a flat `packages` bucket |
| `hosts/group_vars/all/vars` (no extension) | `hosts/group_vars/all/vars.yml` | File extension added |
| `hosts/group_vars/all/vault` (no extension) | `hosts/group_vars/all/vault.yml` | File extension added |
| `pb_managed.yaml`, `pb_host_*.yaml`, `playbook.yaml` (repo root, flat) | `playbooks/bootstrap/*.yml`, `playbooks/hosts/*.yml`, `site.yml` | Bootstrap vs. per-host service plays separated into subdirectories; single `site.yml` entry point |
| Host names `gateway-1`, `control-1`, `nas-2`, `workstation-1` | `gw01`, `util01`, `cmp01`, `ctl01` (+ new `ai01`) | Renamed/restructured — `control-1` (monitoring) split conceptually from `workstation-1` (dev), with AI workloads pulled out into their own host (`ai01`); see [`../spec/hosts.md`](../spec/hosts.md) |
| Linux-only fleet | Linux + macOS (`ctl01`, `ai01`) | v3 adds macOS as a managed OS — new `system/*` roles branch on `ansible_system` where a v2 role has no macOS equivalent to port from |

## Per-service source mapping

For the exact archive path to port each planned service/role from, see the "v2 reference"
column in [`../spec/services.md`](../spec/services.md) — it's kept there rather than
duplicated here so there's one place to update as roles get built.

Two porting nuances worth calling out:

- **Portainer** has no `archive/` role — its v2 implementation lives in the external
  `uknth/ansible-role-portainer` repo (listed in `requirements.yml`). Decide when
  implementing `services/dashboard/portainer` whether to keep consuming it as an external
  Galaxy role or fold it in as a local role for consistency with the rest of `services/`.
- **Beszel** splits across two v2 roles that map to two different v3 hosts: the *hub*
  (`archive/roles/docker2/monitor/beszel`) → `services/monitoring/beszel_hub` on `util01`,
  and the *agent* (`archive/roles/packages/system/beszel-agent`) → `system/beszel_agent`,
  applied to every Linux host in phase 1b so `util01` can monitor the whole fleet.

## Net-new services (no v2 reference)

Kavita, Uptime Kuma, n8n, Ollama, and Hermes have no v2 implementation to port — build these
from upstream documentation/Docker images directly, following the same role skeleton as
everything else. See [`../spec/services.md`](../spec/services.md) for the full list.

## Retired, not migrating

Roles under `archive/roles/` not listed in any `services.md` table (Affine, Joplin, Kener,
Miniflux, Pi-hole, Seafile, Silverbullet, Statping, Trillium, Websurfx, Homer, Sourcebot, the
old `k8s/` media stack, `k3s`, Samba, Syncthing, `lazygit`/`skaffold`/`taskfile`) are staying
retired. See the "Retired in v2, not currently planned for v3" table in
[`../spec/services.md`](../spec/services.md) for the full list and reasoning per item. Don't
port one back without first adding it to the spec and a roadmap phase.

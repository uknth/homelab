# AGENTS.md — Homelab Ansible Repository

This document provides guidance for AI coding agents (Claude, Copilot, etc.) working in this
repository.

---

## Repository Overview

This is an **Ansible-based homelab infrastructure-as-code** repository managing a small home
network of Linux and macOS machines: playbooks, roles, and group variables provision and
configure self-hosted services across the fleet.

**This branch (`v3-dev`) is a from-scratch rebuild.** The previous implementation now lives
under `archive/` as a working reference; very little of the new `roles/`/`playbooks/`
structure is built yet. Before writing any role or playbook here, read
**[`docs/README.md`](docs/README.md)** — it's the source of truth for the target design (spec)
and the build-out order (plan). This file only covers conventions for *how* to work in the
repo; `docs/` covers *what* to build and *why*.

**Infrastructure summary (target state — see [`docs/spec/hosts.md`](docs/spec/hosts.md)):**

| Host | IP | OS | Role |
|---|---|---|---|
| `gw01` | 10.0.2.2 | Debian | Network gateway — Blocky DNS, nginx, certbot, Tailscale subnet router |
| `cmp01` | 10.0.2.5 | Debian | Compute (A4000 GPU) — arr stack, Jellyfin, Kavita, Paperless-ngx |
| `util01` | 10.0.2.8 | Debian | Observability/ops — Beszel, Uptime Kuma, ntfy, Diun, Glance, Portainer, Dozzle, n8n |
| `ctl01` | 10.0.2.4* | macOS | Ansible control node / jumphost / dev toolchain |
| `ai01` | 10.0.2.9 | macOS | AI workloads — Ollama (native), Qwen, paperless-ai |
| `nas01` | 10.0.2.6 | TrueNAS | Semi-managed (NFS exports via API) — primary storage |
| `nas02` | 10.0.2.3 | Synology | Unmanaged — Restic backup target (SFTP) |
| `dns01` | 10.0.2.7 | Pi-hole | Unmanaged — ad/tracker blocking (Blocky's upstream) |

\* `ctl01` still on DHCP `10.0.2.115`; reserve `10.0.2.4` in Omada. Immich/Vaultwarden are
**out of scope** for v3. See [`docs/spec/hosts.md`](docs/spec/hosts.md) for the full spec.

---

## Repository Structure

```
.
├── ansible.cfg                # Ansible config (inventory path, roles_path)
├── site.yml                   # Master playbook — imports bootstrap + host playbooks
├── init.yml                   # One-time bootstrap of the 'ansible' user on a fresh host
├── requirements.yml           # External Ansible Galaxy roles & collections
├── hosts/hosts.yml            # Inventory: all hosts, groups, IPs
├── hosts/hosts.md             # Quick-reference host/service notes
├── hosts/group_vars/          # MUST live next to the inventory (see conventions.md) —
│   ├── all/vars.yml           #   Global non-secret variables
│   ├── all/vault.yml          #   ansible-vault encrypted secrets (never edit directly)
│   ├── linux/vars.yml         #   Linux-only defaults
│   └── macos/vars.yml         #   macOS-only defaults
├── keys/                      # SSH private keys (git-ignored)
├── playbooks/
│   ├── bootstrap/             # linux.yml, macos.yml — per-OS host bootstrap
│   └── hosts/                 # gw01.yml, cmp01.yml, util01.yml, ctl01.yml, ai01.yml
├── roles/
│   ├── system/                 # OS/host-level roles (cross-platform where needed)
│   ├── services/<domain>/      # user-facing services, grouped by domain (media, photos, ...)
│   └── dev/                    # dev toolchain roles (ctl01)
├── docs/                       # Spec + plan — see docs/README.md
└── archive/                    # Retired v2 roles/playbooks — reference only, do not restore
                                 # without going through docs/plan/migration.md first
```

---

## Documentation

Full design and build-out plan lives under **`docs/`**:

- [`docs/spec/architecture.md`](docs/spec/architecture.md) — network, domains, storage, access model
- [`docs/spec/hosts.md`](docs/spec/hosts.md) — per-host spec
- [`docs/spec/services.md`](docs/spec/services.md) — full service catalog, incl. which v2
  role in `archive/` each new role should be ported from
- [`docs/spec/conventions.md`](docs/spec/conventions.md) — role/variable/tag conventions
  (superseded here only where this file gives repo-wide agent guidance not specific to role
  design)
- [`docs/plan/roadmap.md`](docs/plan/roadmap.md) — build-out phases and current status
- [`docs/plan/migration.md`](docs/plan/migration.md) — archive → v3 mapping mechanics

**Before adding a new role or service, check `docs/spec/services.md` first.** If it's not in
the catalog, add it there (and a phase in `docs/plan/roadmap.md`) before writing code — don't
let the implementation get ahead of the design.

---

## Variables and Secrets

- All **non-secret variables** live in `hosts/group_vars/all/vars.yml`, plus
  `hosts/group_vars/linux/vars.yml` / `hosts/group_vars/macos/vars.yml` for OS-specific defaults. This is
  the first place to look for port numbers, directory paths, usernames, and configuration
  values.
- All **secrets** are referenced as `{{ vault_* }}` Jinja2 variables and stored encrypted in
  `hosts/group_vars/all/vault.yml`. **Never write secrets in plaintext.** Use
  `ansible-vault edit hosts/group_vars/all/vault.yml` to update secrets.
- `defaults/main.yml` in each role holds role-level default values (safe placeholders).
  `group_vars/` overrides these with real values.
- Prefix service-specific variables with a short service prefix (e.g. `vw_` for Vaultwarden,
  `arr_` for the arr stack).

---

## Role Conventions

See [`docs/spec/conventions.md`](docs/spec/conventions.md) for the full writeup. Summary:

```
roles/<category>/<name>/
├── defaults/main.yml    # safe placeholder values
├── handlers/main.yml    # e.g. "Restart <service>"
├── tasks/
│   ├── main.yml         # entry point; dispatches on ansible_system if cross-platform
│   ├── linux.yml
│   └── macos.yml
└── templates/
    ├── docker-compose.yml.j2   # for services/ roles
    └── env.j2 (optional)
```

**Task pattern for `services/` (Docker) roles:**
1. Create required directories.
2. Template `docker-compose.yml.j2` (and any config files) into the service directory.
3. Deploy with `community.docker.docker_compose_v2`.

**Cross-platform roles** (currently everything under `system/`) use one role with
OS-specific task files (`tasks/linux.yml`, `tasks/macos.yml`) dispatched from `tasks/main.yml`
via `when: ansible_system == "Linux"` / `"Darwin"` — don't fork into separate
`<name>_linux`/`<name>_macos` roles.

---

## Tags

```
tags: ["<service-or-host>", "<category>"]
```

Examples: `[jellyfin, media]`, `[bootstrap, linux]`, `[cmp01, compute]`.

```bash
ansible-playbook site.yml --tags "bootstrap,linux"
ansible-playbook site.yml --tags "cmp01"
ansible-playbook site.yml --tags "arr,jellyfin"
```

---

## Running Playbooks

```bash
# Install external roles/collections first (one-time or after requirements.yml changes)
ansible-galaxy install -r requirements.yml

# One-time: bootstrap the ansible user on a fresh host (existing admin account)
ansible-playbook init.yml --tags "ansible-user" \
  -e "ansible_host=<ip>" -e "ansible_user=<admin_user>" \
  --ask-pass --ask-become-pass --ask-vault-pass

# Full run (only stages already uncommented in site.yml actually execute)
ansible-playbook site.yml

# Run only a specific host's playbook
ansible-playbook playbooks/hosts/cmp01.yml

# Dry-run
ansible-playbook site.yml --check
```

SSH keys must be present in `keys/` (`keys/ansible_rsa.private`).

`site.yml` imports each bootstrap/host playbook behind a commented-out
`import_playbook` line — uncomment a line only once that phase's roles actually exist (see
[`docs/plan/roadmap.md`](docs/plan/roadmap.md) for current status).

---

## Adding a New Role

1. Check it's in [`docs/spec/services.md`](docs/spec/services.md) (or the system/dev tables
   there); add it if missing, and give it a phase in
   [`docs/plan/roadmap.md`](docs/plan/roadmap.md).
2. Create `roles/<category>/<name>/` per the layout above.
3. If a v2 implementation exists in `archive/` (check `docs/spec/services.md`'s "v2
   reference" column), port it rather than starting from scratch — see
   [`docs/plan/migration.md`](docs/plan/migration.md).
4. Register real values in `hosts/group_vars/all/vars.yml` (and `vault.yml` for secrets), prefixed
   with the service's short name.
5. Add the role to the appropriate `playbooks/hosts/<name>.yml` (or `playbooks/bootstrap/*`)
   with tags.
6. Uncomment the corresponding `import_playbook` line in `site.yml` once ready to run for
   real.

---

## Inventory & Host Groups

- `managed` — hosts Ansible provisions
  - `linux` — `gw01`, `util01`, `cmp01`
  - `macos` — `ctl01`, `ai01`
- `unmanaged` — reference-only entries (`nas01`, `nas02`, `ha01`); no playbooks run against these
- `gpu_nodes` — hosts with a GPU (`cmp01`)
- `storage_nodes` — NAS hosts (`nas01`, `nas02`)

---

## Key Ansible Collections & External Roles

See `requirements.yml` for the current list (Tailscale, DNS, ntp, nginx, certbot, Portainer,
Beszel, Node.js, AWS CLI, Neovim, etc.) and `collections:` (`community.docker`,
`community.general`, `vladgh.samba`, `kubernetes.core`).

---

## What Lives in `archive/`

The `archive/` directory contains **retired v2 roles and playbooks** — a working reference
implementation predating the v3 rebuild. Do **not** restore or reference these directly
without going through [`docs/plan/migration.md`](docs/plan/migration.md) first: most services
in `archive/` map onto a planned v3 role, but the structure, file extensions, and host names
have all changed. A few archived roles (old status pages, note-taking apps, the k8s media
stack) are retired for good — see the "Retired, not migrating" section of
[`docs/spec/services.md`](docs/spec/services.md).

---

## Security Notes

- **SSH keys** in `keys/` are git-ignored. Never commit them.
- **Vault file** (`hosts/group_vars/all/vault.yml`) is encrypted with `ansible-vault`. Never commit
  decrypted secrets.
- All `vault_*` variables must resolve from the vault file before playbooks can run. Provide
  the vault password via `--ask-vault-pass` or a vault password file.
- The `ansible` user is the standard remote service account for all managed hosts, created by
  `roles/system/ansible_user`.

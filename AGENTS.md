# AGENTS.md — Homelab Ansible Repository

This document provides guidance for AI coding agents (Claude, Copilot, etc.) working in this repository.

---

## Repository Overview

This is an **Ansible-based homelab infrastructure-as-code** repository that manages a small home network of Linux servers. It uses playbooks, roles, and group variables to provision and configure self-hosted services across several machines.

**Infrastructure summary:**

| Host | Distribution | Role |
|---|---|---|
| `gateway-1.host` | Debian | Network gateway — Nginx, Tailscale, Blocky DNS |
| `control-1.host` | Ubuntu | Monitoring & dashboards — Beszel, Glance |
| `nas-2.host` | Debian | NAS services — Media, Photos, Documents, Vault |
| `workstation-1.host` | Debian | Developer tools — Go, kubectl, Helm, kind, etc. |
| `nas-1.host` | Synology DSM | Unmanaged NAS (reference only) |
| `iot-1.host` | Home Assistant OS | Unmanaged IoT hub (reference only) |
| `dns-1.host` | Pi-Hole | Unmanaged DNS (reference only) |

---

## Repository Structure

```
.
├── ansible.cfg               # Ansible configuration (inventory path, SSH settings)
├── playbook.yaml             # Master playbook (imports all host-specific playbooks)
├── pb_managed.yaml           # Runs on all managed_hosts: hostname, apt, ntp, docker, dns
├── pb_host_control-1.yaml    # control-1 specific roles
├── pb_host_nas-2.yaml        # nas-2 specific roles
├── pb_host_workstation-1.yaml# workstation-1 specific roles
├── pb_host_gateway-1.yaml    # gateway-1 specific roles
├── requirements.yml          # External Ansible Galaxy roles & collections
├── hosts/hosts.yml           # Inventory: all hosts with IPs and SSH config
├── group_vars/all/vars       # All non-secret variables
├── group_vars/all/vault      # Ansible-vault encrypted secrets (never edit directly)
├── keys/                     # SSH private keys (git-ignored)
├── roles/                    # Local custom roles (active)
│   ├── docker/               # Docker-compose based service deployments
│   │   ├── media/            # arr stack, jellyfin, music (navidrome)
│   │   ├── monitor/          # beszel monitoring hub
│   │   ├── network/          # blocky DNS
│   │   ├── services/         # vaultwarden, immich, paperless-ngx, glance, fusion
│   │   └── ui/               # homer dashboard
│   └── packages/             # System/package installation roles
│       ├── dev/              # go, node, awscli, skaffold, taskfile
│       ├── network/          # nginx, certbot, tailscale, syncthing, samba
│       ├── ops/              # mount-nas1
│       └── system/           # apt, docker, ntp, beszel-agent, helm, kind, kubectl
└── archive/                  # Deprecated/retired roles (do not use)
```

---

## Variables and Secrets

- All **non-secret variables** live in `group_vars/all/vars`. This is the first place to look for port numbers, directory paths, usernames, and configuration values.
- All **secrets** are referenced as `{{ vault_* }}` Jinja2 variables and stored encrypted in `group_vars/all/vault`. **Never write secrets in plaintext.** Use `ansible-vault edit group_vars/all/vault` to update secrets.
- `defaults/main.yml` in each role holds role-level default values (safe placeholder values). Variables in `group_vars/all/vars` override these.

---

## Role Conventions

### Docker service roles (`roles/docker/`)

Each role follows this standard structure:

```
roles/docker/<category>/<service>/
├── defaults/main.yml       # Default variable values (safe placeholders)
├── handlers/main.yml       # Handlers (e.g., "Restart <service>")
├── tasks/main.yml          # Main task list
└── templates/
    ├── docker-compose.yml.j2   # Jinja2 docker-compose template
    └── env.j2 (optional)       # Environment file template
```

**Task pattern for docker roles:**
1. Create required directories (using `ansible.builtin.file` or `ansible.builtin.command: mkdir -p`)
2. Template `docker-compose.yml.j2` (and any config files) into the service directory
3. Deploy with `community.docker.docker_compose_v2`

**Standard directory variables:**
- `config_dir` → `/home/ansible/.config/homelab` — service config & compose files
- `service_dir` → `/mnt/service` — persistent service data
- `data_dir` → `/home/ansible/.local/share/homelab` — general data

### Package roles (`roles/packages/`)

```
roles/packages/<category>/<package>/
├── defaults/main.yml   (optional)
├── tasks/main.yml
├── templates/          (optional)
└── vars/main.yml       (optional)
```

Tasks typically check if the binary is already installed (idempotent) before downloading/installing.

---

## Tags

Tags are used extensively for selective role execution. Convention:

```
tags: ["<layer>", "<category>", "<service>"]
```

Examples:
- `["docker", "service", "vaultwarden"]`
- `["system", "docker"]`
- `["dev", "go", "installer"]`

Run a tagged subset with: `ansible-playbook playbook.yaml --tags "docker,vaultwarden"`

---

## Running Playbooks

```bash
# Install external roles/collections first (one-time or after requirements.yml changes)
ansible-galaxy install -r requirements.yml

# Run the full playbook
ansible-playbook playbook.yaml

# Run only a specific host playbook
ansible-playbook pb_host_nas-2.yaml

# Run with tags to limit scope
ansible-playbook playbook.yaml --tags "docker,jellyfin"

# Dry-run (check mode)
ansible-playbook playbook.yaml --check

# Skip docker installation on a host
ansible-playbook pb_managed.yaml -e "no_docker=true"

# Skip DNS configuration
ansible-playbook pb_managed.yaml -e "no_dns=true"
```

SSH keys must be present in the `keys/` directory (`keys/ansible_rsa.private`).

---

## Adding a New Docker Service Role

1. Create the role directory under the appropriate category:
   ```
   roles/docker/<category>/<service>/
   ```
2. Add `defaults/main.yml` with all variables the role needs, using safe placeholder values.
3. Add `tasks/main.yml` following the standard docker pattern (mkdir → template → docker_compose_v2).
4. Add Jinja2 templates under `templates/`.
5. If the service needs a restart handler, add `handlers/main.yml`.
6. Register all real values in `group_vars/all/vars`. Prefix variables with the service name (e.g., `vw_` for vaultwarden, `arr_` for the arr stack).
7. Store any secrets as `{{ vault_<name> }}` references — add actual values via `ansible-vault edit`.
8. Add the role to the appropriate host playbook (`pb_host_*.yaml`) with tags.
9. If the service is accessible via a subdomain, add it to `service_mapping` in `group_vars/all/vars`.

---

## Adding a New Package Role

1. Create the role directory: `roles/packages/<category>/<package>/`
2. Write an idempotent `tasks/main.yml` (check if already installed before acting).
3. Add the role to the appropriate host playbook with tags.

---

## Inventory & Host Groups

- `managed_hosts` — all hosts provisioned by this repo (used in `pb_managed.yaml`)
  - `controls` — gateway-1, control-1
  - `nases` — nas-2
  - `workstations` — workstation-1
- `unmanaged_hosts` — reference-only entries (nas-1, iot-1, dns-1); no playbooks run against these

---

## Key Ansible Collections & External Roles

| Name | Source | Used for |
|---|---|---|
| `community.docker` | Galaxy | `docker_compose_v2` module |
| `community.general` | Galaxy | Various utilities |
| `vladgh.samba` | Galaxy | Samba shares |
| `geerlingguy.nginx` | Galaxy | Nginx on gateway-1 |
| `geerlingguy.certbot` | Galaxy | TLS certs via Let's Encrypt/Cloudflare |
| `artis3n.tailscale` | GitHub | Tailscale VPN |
| `dbrennand.beszel` | Galaxy | Beszel monitoring agent |
| `oefenweb.dns` | GitHub | DNS resolver config (Debian) |
| `dns` (metno) | GitHub | DNS resolver config (Ubuntu) |

---

## What Lives in `archive/`

The `archive/` directory contains **retired roles** that are no longer used (e.g., old k8s-based media stack, pi-hole, seafile, joplin). Do **not** reference or restore these without explicit intent. They exist for historical reference only.

---

## Security Notes

- **SSH keys** in `keys/` are git-ignored. Never commit them.
- **Vault file** (`group_vars/all/vault`) is encrypted with `ansible-vault`. Never commit decrypted secrets.
- All `vault_*` variables must resolve from the vault file before playbooks can run. Provide the vault password via `--ask-vault-pass` or a vault password file.
- The `ansible` user is the standard remote user for all managed hosts.

# Conventions

How v3 roles, playbooks, variables, and tags are structured. These conventions are already in
effect for the roles that exist (`roles/system/*`) — follow them for everything new.

## Role directory layout

Roles live under three top-level categories in `roles/`:

```
roles/
├── system/     # OS/host-level setup: users, hostname, network, packages, agents
├── services/   # user-facing services, grouped by domain
│   └── <domain>/<service>/    # e.g. services/media/jellyfin, services/photos/immich
└── dev/        # developer tooling installed on ctl01 (or future dev hosts)
```

This replaces the v2 split of `roles/docker/<category>/<service>` vs.
`roles/packages/<category>/<package>` — see [`../plan/migration.md`](../plan/migration.md)
for the mapping.

A typical role:

```
roles/<category>/<name>/
├── defaults/main.yml    # safe placeholder values
├── handlers/main.yml    # e.g. "Restart <service>"
├── tasks/
│   ├── main.yml         # entry point — dispatches by ansible_system when cross-platform
│   ├── linux.yml        # Debian/Ubuntu-specific tasks
│   └── macos.yml        # macOS-specific tasks
└── templates/
    ├── docker-compose.yml.j2   # for services/ roles
    └── env.j2 (optional)
```

## Cross-platform roles

Any role that applies to both `linux` and `macos` hosts (currently everything under
`system/`) uses a single role with OS-specific task files, dispatched from `tasks/main.yml`
on `ansible_system`:

```yaml
- name: Do the linux thing
  ansible.builtin.import_tasks: linux.yml
  when: ansible_system == "Linux"

- name: Do the macos thing
  ansible.builtin.import_tasks: macos.yml
  when: ansible_system == "Darwin"
```

See `roles/system/hostname/tasks/main.yml` and `roles/system/ansible_user/tasks/main.yml` for
the pattern in practice. Don't fork a role into `<name>_linux` / `<name>_macos` — branch
inside one role instead.

`services/` roles are Docker-based and Linux-only in practice (they land on `cmp01` and
`util01`); they don't need this split. The container runtime differs by OS: Linux hosts run
Docker Engine (`system/docker`); macOS hosts that need containers run **Colima**
(`system/colima`, currently only `ctl01` for `dev/kind`). Both expose a Docker-compatible
socket, so `community.docker` modules work unchanged on either.

## Bootstrapping a new host

1. `init.yml` runs once against a fresh machine, connecting as an existing admin account
   (`--ask-pass --ask-become-pass --ask-vault-pass`), and creates the `ansible` service
   account via `system/ansible_user`.
2. `playbooks/bootstrap/linux.yml` or `playbooks/bootstrap/macos.yml` then runs as the
   `ansible` user for all standard host setup (hostname, network, packages, agents).
3. The host's `playbooks/hosts/<name>.yml` deploys its services.

`site.yml` is the top-level entry point that imports all of the above; individual
`import_playbook` lines are commented out until that host/stage is ready to run — see
[`../plan/roadmap.md`](../plan/roadmap.md) for current status.

`init.yml` is **idempotent** — it creates the `ansible` account only when missing and re-asserts
sudo/key/SSH every run, so it's safe to run against the whole fleet anytime. Onboarding a new
host is: create `uknth` + unified key + passwordless sudo, add to inventory, then
`ansible-playbook init.yml -l <host>`.

## Unified `uknth` login

There are two SSH identities in play, kept deliberately separate:

- **`ansible`** — the automation service account. Key: `keys/ansible_rsa.private` (RSA,
  generated on the control node, public half in the vault as `vault_public_key`). This is what
  every playbook connects as.
- **`uknth`** — the personal admin account used for break-glass and for the very first
  `init.yml` contact before `ansible` exists. It is **unified onto a single key across the whole
  fleet: the `id_rsa` keypair** (`~/.ssh/id_rsa` on the control node). Historically some Linux
  hosts trusted a separate `github_id_rsa` key and the Macs trusted `id_rsa`; v3 collapses this
  to just `id_rsa` everywhere so `init.yml` has one key to connect with.

**Unifying the key is a manual step** (done once, out-of-band): take the `id_rsa` public key —
it's already in `~/.ssh/authorized_keys` on the Mac hosts — and add it to
`~uknth/.ssh/authorized_keys` on every host, removing the old `github_id_rsa` entry. `id_rsa`
itself is never replaced. After this, `init.yml` (which connects as `uknth` via `id_rsa`) works
uniformly against any host.

## Variables and secrets

- **`group_vars/` lives in `hosts/` (next to the inventory), not at the repo root.** This is
  load-bearing: Ansible resolves `group_vars/` relative to the inventory's directory *and* the
  playbook's directory. Because `site.yml` imports subdir playbooks (`playbooks/hosts/*.yml`),
  a repo-root `group_vars/` would **not** load for those imported plays — `service_root`,
  `vault_*`, etc. would come up undefined. Keeping `group_vars/` adjacent to
  `hosts/hosts.yml` makes it load for every playbook regardless of where it sits. Do not move
  it back to the repo root.
- Non-secret variables live in `hosts/group_vars/all/vars.yml`, plus `hosts/group_vars/linux/vars.yml`
  and `hosts/group_vars/macos/vars.yml` for OS-specific defaults.
- Secrets are `{{ vault_* }}` references, stored encrypted in `hosts/group_vars/all/vault.yml`.
  Never write a secret in plaintext anywhere in the repo. Edit with
  `ansible-vault edit hosts/group_vars/all/vault.yml`.
- The vault password is read from a **git-ignored file outside the repo** so runs are
  non-interactive: `ansible.cfg` sets `vault_password_file = ~/.config/homelab/.vault_pass`.
  Each control node (currently `ctl01`) creates its own copy — `chmod 600`, never
  committed. Bootstrap secrets that must exist before any run: `vault_public_key` (the
  `keys/ansible_rsa.private.pub` contents, installed into the `ansible` user's
  `authorized_keys`) and `vault_sudo_password` (the `ansible` user's sudo password).
- Role-level defaults go in each role's `defaults/main.yml` as safe placeholders;
  `group_vars/` overrides them with real values.
- Prefix service-specific variables with a short service prefix (v2 convention, carried
  forward): `vw_*` for Vaultwarden, `arr_*` for the arr stack, etc.
- Standard directory variables (v2 convention, carried forward — confirm final values when
  each role is implemented): `config_dir`, `data_dir` for config/state under the `ansible`
  (or per-service) user's home, plus a `service_dir`-style path for persistent Docker
  volumes.

## Tags

Every role inclusion in a playbook carries tags so subsets can be targeted:

```yaml
- role: services/media/jellyfin
  tags: [jellyfin, media]
```

Playbooks also tag the whole play by host/stage (`[bootstrap, linux]`, `[cmp01, compute]`),
so you can run e.g.:

```bash
ansible-playbook site.yml --tags "bootstrap,linux"
ansible-playbook site.yml --tags "cmp01"
ansible-playbook site.yml --tags "arr,jellyfin"
```

## Adding a new role — checklist

1. Confirm it's in the spec: add it to the right table in
   [`services.md`](services.md) (or the system/dev tables) if it isn't already, and give it a
   status/phase in [`../plan/roadmap.md`](../plan/roadmap.md).
2. Create `roles/<category>/<name>/` following the layout above.
3. If a v2 implementation exists in `archive/`, port it rather than starting from scratch —
   see [`../plan/migration.md`](../plan/migration.md) for the specific source path and what
   needs to change.
4. Add real values to `hosts/group_vars/all/vars.yml` (and `vault.yml` for secrets), prefixed with
   the service's short name.
5. Add the role to the appropriate `playbooks/hosts/<name>.yml` with tags.
6. Uncomment (or add) the corresponding `import_playbook` line in `site.yml` once the host
   stage is ready to run for real.

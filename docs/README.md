# Homelab Documentation

This directory is the source of truth for the **v3** redesign of the homelab. It exists
because v3 is a from-scratch rebuild: the previous implementation (now under `archive/`)
grew organically and its structure no longer matches how the fleet is actually used. This
time we design before we build.

## Workflow

Every change to the homelab follows the same three-stage flow:

1. **Spec** (`docs/spec/`) — what the target state looks like: hosts, network, services,
   domains, and the conventions new roles/playbooks must follow. This is the design, not
   the implementation.
2. **Plan** (`docs/plan/`) — how we get from where the repo is today to the spec: phased
   ordering, and for each planned role, whether it's new or being rebuilt from a working
   implementation already sitting in `archive/`.
3. **Playbook** (`roles/`, `playbooks/`, `site.yml`) — the actual Ansible implementation,
   built against the spec and following the plan's ordering.

If you're about to write a role or playbook, the spec and plan should already answer *what*
and *in what order*. If they don't, update the docs first — don't let the implementation
drift ahead of the design.

## Contents

**Spec**
- [`spec/architecture.md`](spec/architecture.md) — network layout, DNS design, domain scheme,
  storage, access model, topology diagram.
- [`spec/hosts.md`](spec/hosts.md) — every host: hardware, OS, purpose, what it runs.
- [`spec/services.md`](spec/services.md) — full service catalog: which host, which domain,
  where the v2 reference implementation lives in `archive/`.
- [`spec/storage.md`](spec/storage.md) — the storage golden rule (state local, media on NFS),
  the single-mount requirement, pinned `media` GID, TrueNAS/Synology roles.
- [`spec/auth.md`](spec/auth.md) — single sign-on with Authentik: the one identity provider all
  services sit behind, the three integration tiers, and the nginx forward-auth flow.
- [`spec/maintenance.md`](spec/maintenance.md) — patching, container updates (Diun + Ansible),
  backups (Restic → nas02 over SFTP), the ntfy notification sink.
- [`spec/conventions.md`](spec/conventions.md) — role directory layout, variable naming,
  tagging scheme, secrets handling for v3.

**Plan**
- [`plan/roadmap.md`](plan/roadmap.md) — build-out phases and current status.
- [`plan/gitops.md`](plan/gitops.md) — the SourceHut → builds.sr.ht → n8n → Ansible deploy
  pipeline.
- [`plan/migration.md`](plan/migration.md) — role-by-role mapping from `archive/` (v2) to
  the new `roles/` layout (v3). Note: v3 roles are written **fresh**; `archive/` is read for
  insight, not copied.

## Related documents

- [`AGENTS.md`](../AGENTS.md) (repo root) — day-to-day conventions for anyone (human or
  agent) working in this repo; links back here for the full design.
- [`hosts/hosts.yml`](../hosts/hosts.yml) — the live Ansible inventory. `spec/hosts.md` is
  the narrative version; the inventory is the executable source of truth for IPs/groups.
- `archive/` — retired v2 roles and playbooks, kept as working reference implementations
  for the v3 rebuild. See `plan/migration.md` for how they map onto v3.

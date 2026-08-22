# GitOps — SourceHut → n8n → Ansible

The deployment pipeline. Code lives on **SourceHut** (`sr.ht`), not GitHub, so the flow uses
builds.sr.ht for CI and n8n on `util01` as the deploy trigger. No Argo/Flux — those are
Kubernetes tools and this fleet is Docker Compose + Ansible.

## Flow

```
 developer ──push──▶ git.sr.ht/~<user>/homelab   (branch: master)
                          │
                          ▼
                 builds.sr.ht  (.build.yml)          ← NO secrets, no host access
                   ├─ ansible-lint
                   └─ ansible-playbook --syntax-check (parses only; does NOT decrypt
                          │  on success                 vault, does NOT connect to hosts)
                          ▼
                 POST webhook ─────────────▶  n8n @ util01 (10.0.2.8)   ← HOLDS secrets, on-LAN
                                                 ├─ verify shared secret (HMAC header)
                                                 ├─ git pull on util01
                                                 ├─ ansible-playbook --tags <changed> --check   (dry run)
                                                 └─ ansible-playbook --tags <changed>           (apply)
                                                      (vault password + SSH key are local files)
                          │
                          ▼
                 result ──▶ ntfy topic: homelab-deploy
```

## Why this shape

- **CI never holds production secrets.** builds.sr.ht only runs *static* checks — `ansible-lint`
  and `--syntax-check` — which parse the playbooks without decrypting the vault or connecting
  to any host. So sr.ht never needs the vault password or an SSH key. A real dry-run (`--check`)
  needs *both* vault decryption *and* host access, so it belongs on the LAN executor, not in
  cloud CI.
- **The vault password lives in exactly two on-LAN files, never in git or sr.ht:**
  `ctl01` (manual control node) and `util01` (automated executor). Both use the
  `ansible.cfg` → `vault_password_file = ~/.config/homelab/.vault_pass` convention (chmod 600,
  git-ignored), plus the ansible SSH private key (`keys/ansible_rsa.private`). Provision both
  onto `util01` out-of-band when building this phase.
- **n8n is the deploy trigger**, reusing a service already planned for `util01`. The webhook is
  authenticated with a shared HMAC secret and is only reachable over Tailscale (or a scoped
  Tailscale Funnel) — never open to the internet.
- **`util01` is the executor**, running `ansible-playbook --check` then apply against the
  changed hosts. It pulls the repo fresh each deploy so running state always matches `main`.

## Secrets summary — where the vault password is (and isn't)

| Location | Has vault password? | Why |
|---|---|---|
| git.sr.ht repo | ❌ never | only encrypted `vault.yml` is committed |
| builds.sr.ht CI | ❌ never | static checks only (lint + syntax-check) |
| `ctl01` | ✅ file | manual control node |
| `util01` | ✅ file | automated executor (n8n → ansible) |

If concentrating the vault password on two boxes ever feels too broad, the more
GitOps-idiomatic alternative is **sops + age** (per-host age keys, no shared password) — a
larger change from the current `ansible-vault` setup, noted here as a future option, not a
Phase 7 requirement.

## Components to build (Phase 7)

1. `.build.yml` at repo root — the builds.sr.ht manifest: install Ansible + collections,
   `ansible-lint`, `--syntax-check`, `--check --diff`. Fails the build on any lint/dry-run
   error. Post the success webhook as the final task.
2. n8n workflow on `util01`:
   - Webhook node (HMAC-verified).
   - Exec node: `git -C /opt/homelab pull`.
   - Exec node: `ansible-playbook site.yml --tags "<from payload>" --vault-password-file …`.
   - ntfy node: report to `homelab-deploy`.
3. A deploy secret in the vault (`vault_gitops_webhook_secret`) and the vault-password file
   provisioned onto `util01` out-of-band (never committed).

## Alternative considered

**ansible-pull on a timer** (each host pulls `main` and applies itself, no inbound webhook)
was considered — more robust, nothing exposed — but rejected in favour of push-triggered
deploys for immediate feedback. The CI half (builds.sr.ht lint + dry-run) is identical either
way, so switching later only changes the apply half.

## Prerequisite

Mirror/host the repo on `git.sr.ht`. Today it's a local git repo with a GitHub-style key
convention; the SourceHut remote and a build user/secret need to be set up before Phase 7.

## Phase 7 build notes (2026-08-22)

**CI is live-ready** (`.build.yml`): verified that `ansible-playbook site.yml
--syntax-check` passes with **no vault password** (CI strips `vault_password_file`
from `ansible.cfg` into `ci.cfg`), so builds.sr.ht never needs a secret. Lint is
advisory for now. **To activate:** enable the git.sr.ht → builds.sr.ht integration
for `~uknth/homelab` (push triggers the build).

**Trigger is polling, not an inbound webhook.** builds.sr.ht runs in the cloud and
cannot reach n8n on the LAN (`util01`, 10.0.2.8) without exposing it publicly
(Tailscale Funnel/Cloudflare Tunnel — extra attack surface). So the deploy trigger
inverts: **n8n on util01 polls git.sr.ht** for a new `master` commit (and, optionally,
a green builds.sr.ht status) and then runs the on-LAN executor. No inbound exposure.

**Executor still = util01**, out-of-band provisioned with ansible + the repo +
`~/.config/homelab/.vault_pass` + `keys/ansible_rsa.private`.

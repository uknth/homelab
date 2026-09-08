# GitOps — Gitea → Gitea Actions → Ansible

The deployment pipeline. Code lives on **Gitea** (`git.puhome.net`, on `cmp01`), which is
the fleet's source of truth. CI and the deploy trigger are Gitea Actions; the thing that
actually runs Ansible is an on-LAN executor on `util01`. No Argo/Flux — those are Kubernetes
tools and this fleet is Docker Compose + Ansible.

> **History:** this used to be SourceHut → builds.sr.ht → n8n polling. That design was
> replaced in 2026-09 when Gitea was stood up locally. `git.sr.ht` is now a **mirror only**,
> and GitHub is planned as a second mirror. Nothing polls sr.ht any more — the executor must
> watch the repo that PRs and merges actually happen against.

## The rule

**Deploying means opening a PR.** Nobody runs `ansible-playbook` against the fleet by hand.
A hand-run bypasses CI, the PR dry-run, and the record of what was applied and when — which
is the entire point of having the pipeline.

```
change ──▶ branch ──▶ PR ──▶ (human merges) ──▶ deploy
```

The one legitimate exception is bootstrapping the deploy path itself: the executor and its
trigger cannot deploy themselves. Say plainly that it is a bootstrap when you do it.

## Flow

```
 developer ──push──▶ Gitea @ cmp01  (git@ssh.git.puhome.net:uknth/homelab.git)
                          │
            ┌─────────────┴──────────────┐
            ▼                            ▼
      PR opened                      merge to master
            │                            │
            ▼                            ▼
   ci.yml + pr-dryrun.yml            ci.yml + deploy.yml
   ├─ yamllint                       └─ ssh ──▶ util01 (10.0.2.8)
   ├─ ansible-lint                             forced command: gitops-trigger.sh
   ├─ syntax-check                                     │
   ├─ python-tests                                     ▼
   └─ ssh util01: `check <sha>`                  deploy.sh
      (full-fleet --check --diff,                ├─ git fetch/reset to master
       posted back as a PR comment)              ├─ --check --diff   ← GATE
                                                 └─ --check passes? then apply
                                                          │
                                                          ▼
                                                 ntfy: homelab-deploy
```

### The gate that matters

`deploy.sh` runs its **own** full-fleet `--check --diff` before applying, and refuses to
apply if it fails. This is not redundant with `pr-dryrun.yml` — it re-checks the merged
state at apply time, on the executor, with the vault available.

It has already earned its keep: PR #1 merged with a check-mode bug in a new role, and the
gate meant the deploy was a **no-op** rather than a half-configured `cmp01`. When a deploy
fails, look for `dry-run failed; see /tmp/gitops-check.log` in the job log before assuming
anything was applied.

## Where the secrets are (and aren't)

| Location | Vault password? | SSH to fleet? | Notes |
|---|---|---|---|
| Gitea repo | ❌ never | ❌ | only encrypted `vault.yml` is committed |
| Gitea Actions runner (cmp01) | ❌ never | ⚠️ trigger key only | holds the Docker socket — a privilege boundary, not a sandbox |
| `util01` (executor) | ✅ file | ✅ | the only host that can actually change the fleet |
| `ctl01` | ✅ file | ✅ | manual control node |

- **CI jobs run static checks only** — `yamllint`, `ansible-lint`, `--syntax-check`, unit
  tests — none of which decrypt the vault or touch a host. A real `--check` needs both, which
  is exactly why it happens on `util01` and not in a CI container.
- **The runner cannot deploy.** All it can do is SSH to `util01` and say `apply` or
  `check <ref>`. `gitops-trigger.sh` is a **forced command**: it is the only thing the CI key
  can run, and it accepts only those two verbs. That wrapper is the security boundary of this
  design — read its header before changing it.
- **`GITEA_TOKEN` is injected per-run by Gitea itself**, scoped to the repo and the run. It is
  what lets a job clone this **private** repo; it is not a standing credential and does not
  need to be added by hand.

> **Store multi-line secrets base64-encoded, on one line.** A PEM private key added as a
> multi-line Actions secret defeated Gitea's log masking and was printed in full into a job
> log (2026-09-07). The key was rotated, revoked, and re-added base64'd. Treat any multi-line
> secret as un-maskable.

## Components

| Piece | Where | What it does |
|---|---|---|
| `services/development/gitea` | cmp01 | Gitea itself. Repos on NFS from nas01, backed up to nas02 by restic. Web on `git.puhome.net`; SSH on `ssh.git.puhome.net` **portless** (`git@ssh.git.puhome.net`, no `:2222`) via a macvlan sidecar at `10.0.2.18` |
| `services/development/gitea_runner` | cmp01 | `act_runner`, paired 1:1 with Gitea. No fleet credentials |
| `.gitea/workflows/ci.yml` | — | yamllint · ansible-lint · syntax-check · python-tests |
| `.gitea/workflows/pr-dryrun.yml` | — | full-fleet `--check --diff` on every PR, posted back as a comment |
| `.gitea/workflows/deploy.yml` | — | on merge to master, triggers the executor |
| `ops/gitops_executor` | util01 | `deploy.sh`, `gitops-trigger.sh`, the repo clone, and the hourly backstop timer |

`gitops_auto_apply` is **on**. That is not a loosening: the human gate moved from a flag
nobody remembers to flip, to the PR merge itself. The systemd timer is now an hourly
*backstop* for a dropped webhook — the event path is the trigger, and `deploy.sh`'s
up-to-date check makes every other tick a no-op.

## Mirroring

`.gitea/workflows/mirror.yml` force-pushes heads and tags (with `--prune`) to
**github.com/uknth/homelab** and **git.sr.ht/~uknth/homelab** on every push to
master. Both are public; this repo is private on Gitea. That asymmetry is
deliberate — see the audit recorded in [`../HANDOFF.md`](../HANDOFF.md).

Nothing is ever merged on a mirror, and anything pushed to one directly is
destroyed by the next run.

### A force-push does not delete anything

The single most expensive lesson of 2026-09-08. Force-pushing moves refs; the
objects stay and remain fetchable **by SHA**, on every host tested:

- **Gitea** kept every purged blob, because `refs/pull/*/head` pinned the
  pre-purge commits and GC will not drop a referenced object.
- **GitHub** kept serving blobs through its API after the branches were gone.
  Deleting branches changes nothing.
- **sr.ht** kept serving pre-purge commits to a `git fetch <sha>`.

The only thing that worked was **deleting and recreating the repository**. And
it must be verified by fetching a known old SHA — reading the branch list tells
you nothing, and is exactly the check that would have declared success falsely.

### Adding a mirror for a NEW repo

Not yet built; `homelab` is the only mirrored repo. Gitea has no
instance-level mirroring — every endpoint is `/repos/{owner}/{repo}/...` — so
this is a per-repo decision. Three shapes, with the trade-off that matters:

| approach | trigger | cost |
|---|---|---|
| **Per-repo `mirror.yml`** (preferred) | on push — no lag | one identical file per repo; `homelab` can template it at repo-creation |
| Gitea native push mirror | on push | HTTPS only in 1.27.3 (`remote_username`/`remote_password`), so a GitHub PAT and an sr.ht token per repo — more credentials, which is what the workflow avoids |
| Central scheduled workflow | **cron** | rejected: a push sits unmirrored until the next tick, and one broken job silently stops mirroring everywhere |

A central *event-driven* variant is viable if per-repo files become tedious:
Gitea push webhook → n8n → trigger the mirror. That plays to n8n's actual
strength (routing events, not git plumbing), but adds a second system holding a
push-capable key, and would need `workflow_dispatch` support confirmed in this
Gitea version first. Prefer the per-repo file until there is a real reason not
to.

## Gotchas

- **Checkout is hand-rolled, not `actions/checkout`.** That action needs Node, which
  `python:3.12-slim` does not have. The clone uses `${GITHUB_HEAD_REF:-$GITHUB_REF_NAME}`,
  because on a `pull_request` event `GITHUB_REF_NAME` is the PR *number*, not a branch.
  Known wart: this clones by branch name, so a still-queued run for a merged-and-deleted
  branch fails. Cloning `GITHUB_SHA` would be immune.
- **`deploy.yml` does not gate on `ci.yml`.** A red build still deploys today. Branch
  protection requiring the CI checks on `master` is the fix, and is still open.
- **Deploy runs collapse under concurrency** (`group: gitops-deploy`,
  `cancel-in-progress: false`). A superseded *pending* deploy is cancelled in favour of the
  newest commit; the surviving run deploys a superset. A cancelled deploy after two quick
  merges is correct behaviour.
- **Merging a PR fires stray `pull_request` events** on any other open PR whose base moved.
  Harmless, but they queue on the serial runner.
- **`--check` lies about first-ever deploys.** Any task depending on an earlier task's side
  effect fails, because check mode never produced it. See the check-mode section in
  [`../HANDOFF.md`](../HANDOFF.md) and the rule in [`../../AGENTS.md`](../../AGENTS.md).

## Alternative considered

**ansible-pull on a timer** (each host pulls and applies itself) was considered — more
robust, nothing exposed — but rejected in favour of push-triggered deploys for immediate
feedback. The CI half is identical either way, so switching later only changes the apply half.

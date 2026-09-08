# Session Handoff — Context Dump (updated 2026-09-09)

Single pick-up point for a fresh session. Everything below reflects `master` at
tag **v3.0.0**. Read this, then [`plan/roadmap.md`](plan/roadmap.md) and the
[`spec/`](spec/) docs for depth. Next up: **Phase 8**.

## Where we are
- v3 homelab as Ansible IaC (spec-first, codified, idempotent). **Phases 0–7 done.**
- Branch `master`; remote is **Gitea** (`git@ssh.git.puhome.net:uknth/homelab.git`,
  web at `git.puhome.net`, on cmp01). git.sr.ht is a **mirror only** now, and GitHub
  is planned as a second mirror — neither is the source of truth. See
  [`plan/gitops.md`](plan/gitops.md).
- **Deploys go through the pipeline, never by hand.** Change → PR → merge → Gitea
  Actions → executor on util01. `gitops_auto_apply` is now **on**; the human gate is
  the PR merge, not a flag. The systemd timer is an hourly *backstop* for a dropped
  webhook, not the trigger.
- Vault: `hosts/group_vars/all/vault.yml`; password file `~/.config/homelab/.vault_pass`
  (ansible.cfg). To edit safely: `ansible-vault view … > tmp`, append, then
  `ansible-vault encrypt tmp --output hosts/group_vars/all/vault.yml` (do NOT pass
  `--vault-password-file` to encrypt — ansible.cfg already provides the id).

## If you read nothing else

Three things, in the order I would act on them.

1. **n8n restarts on every deploy.** A docs-only merge still reports 39 changed
   tasks, 30 of them n8n re-templating credentials, re-importing and
   re-publishing every workflow, then restarting. That briefly drops the
   webhooks and schedules the research pipeline, ticket sync and maintenance
   jobs all depend on — on every deploy, including ones that change nothing. It
   also makes `--diff` unreadable, which matters now that the PR dry-run is a
   review artefact people are meant to read. Seven roles need a `changed_when`
   or a read-before-write; n8n is the one worth fixing first.

2. **Branch protection is real but bypassable.** `master` requires `CI / *` with
   0 approvals, and `deploy.yml` does not gate on `ci.yml` — the executor's own
   pre-apply `--check` is what actually protects the fleet. PR #9 was
   force-merged without waiting for CI (deliberately). Do not assume a commit on
   master passed CI; check.

3. **Gitea still holds 38 purged vault blobs**, pinned by `refs/pull/1..8/head`.
   Private repo, so hygiene not exposure — but it is the last piece of the
   2026-09-08 purge, and it needs a decision (deleting those refs costs the
   "Files changed" view on eight merged PRs).

Everything else is either blocked on the user, or written up below with reasons.

## Fleet
| Host | IP | Role / key services |
|---|---|---|
| gw01 | 10.0.2.2 | Gateway: Tailscale subnet router (10.0.2.0/24), Blocky DNS (filters AAAA), nginx + `*.puhome.net` wildcard TLS + Authentik forward-auth, certbot |
| util01 | 10.0.2.8 | Observability/platform: Authentik (SSO), Beszel hub, ntfy, Diun, Uptime Kuma, **Homepage** dash, Portainer, Dozzle, n8n, Syncthing, GitOps executor |
| cmp01 | 10.0.2.5 | Compute/GPU: arr stack (prowlarr/sonarr/radarr/lidarr/bazarr/nzbget + qbittorrent behind gluetun), Jellyfin+Jellyseerr (NVENC), Kavita, Paperless, Filebrowser, **Navidrome, LMS, slskd, Soularr** |
| nas01 | 10.0.2.6 | TrueNAS: data-pool (RAIDZ2, **HBA degraded** — swap pending) + scratch-pool; NFS to cmp01 |
| nas02 | 10.0.2.3 | Synology: restic backup target (SFTP, `/restic/<host>`) |
| dns01 | 10.0.2.7 | Pi Zero, Pi-hole — Blocky's upstream |
| ai01 | 10.0.2.9 | Mac Mini M4 Pro — **Phase 8, not built** |
| ctl01 | 10.0.2.4 | Mac Mini M1 — bootstrapped (ansible user + Beszel agent live); dev toolchain is **Phase 8** |
| Macs | — | Beszel agents (launchd) |

Routing: `<name>.puhome.net` → gw01 nginx (Authentik-gated); `<name>.host.puhome.net`
→ real host IP (for widget/API traffic that can't do SSO).

## What this session added (post-Phase-7)
- **Music stack** (cmp01): Navidrome (Subsonic player → phones/desktop), **LMS/Lyrion**
  (streams to the **WiiM** via built-in Squeezelite; host networking, CLI on 9091),
  slskd + Soularr (Soulseek download bridge off Lidarr's wanted list). **Mixarr dropped**
  (recommendation engine; user curates in Lidarr). Runbook: [`runbooks/music-stack.md`](runbooks/music-stack.md).
- **NZB > torrents**: arr delay profiles enforce Usenet-first (usenetDelay 0, torrentDelay 30,
  bypassIfHighestQuality off) — `roles/services/media/arr/tasks/protocol_priority.yml`.
- **Dashboard**: trialled Homarr, reverted (DB/UI-driven ≠ IaC). Homepage kept and reworked:
  2 tabs (**Home** = family incl. Jellyseerr; **System** = admin-only), each with a **links
  block** (bookmarks) on top and a **widgets block** (services) below in a **two-pane CSS
  masonry** (`#services` columns:2). Restyled: light, 18px, full-width white header bar,
  compact search (LLM-search placeholder for Phase 8). Widgets added: Navidrome, Paperless,
  Beszel. CSS in `roles/services/dashboard/homepage/templates/custom.css.j2`.

## Dual-WAN monitoring (added 2026-08-30)
- **Problem**: the Omada load-shares ACT + Airtel, so one dead ISP is invisible
  from the LAN — every monitor stays green until *both* links drop.
- **Fix**: `ops/wanwatch` on gw01 probes each ISP from an alias IP
  (`10.0.2.19` → WAN1 ACT, `10.0.2.20` → WAN2 Airtel) that an Omada
  policy-routing rule in **Only** mode forces out that WAN. n8n
  (`homelab-wan-watch`, every 2 min) runs it over a forced-command SSH key;
  Uptime Kuma push monitors own the verdict and fire the existing ntfy channel;
  Homepage shows a per-ISP tile off `/webhook/wan-status`.
- **Live and verified 2026-08-30.** Omada rules created; both probes report
  distinct public IPs (RDAP confirms 49.207.x = ACT on WAN1, 122.171.x = Airtel
  on WAN2, so the labels are the right way round). Kuma push tokens captured
  into `kuma_wan{1,2}_push_token`; n8n schedule, Kuma pushes and the
  `/webhook/wan-status` replay all confirmed end to end.
- Three bugs found and fixed while bringing it up, all worth remembering:
  **(1)** n8n's SSH node prepends `cd <dir> ;` to every command, so a strict
  argument parser exits 64 and the job fails *quietly* — `ops/maintenance` had
  already hit this and its sed strip is now reused verbatim.
  **(2)** `api.ipify.org` is on Pi-hole's blocklist and resolved to `0.0.0.0`,
  so the public-IP lookup silently returned nothing; it is now an IP literal
  (`https://1.1.1.1/cdn-cgi/trace`) that local DNS cannot touch.
  **(3)** n8n 2.x publishes workflows as versions and `$getWorkflowStaticData`
  no longer survives between executions — the dashboard now replays gw01's
  `status.json` over the same forced-command key (`--last`) instead.
- **Alert path proven** via a synthetic `status=down` push (2026-08-30): Kuma
  went red, ntfy delivered "WAN2 · Airtel Black Down", and the next scheduled
  probe delivered the recovery. Still untested: a **real** single-ISP outage
  (that the surviving link stays green while one is physically down).
  Full procedure: [`runbooks/dual-wan-monitoring.md`](runbooks/dual-wan-monitoring.md).
- **Fixed a pre-existing hole in `uptime_kuma_config` while testing**: monitors
  created through the API never got the ntfy channel attached (`isDefault` only
  applies in the UI), and push monitors could not notify at all because Kuma
  sends ntfy a "view" action with an empty URL and ntfy 400s the message. That
  means **`Backups` has never been able to alert** since it was created — a
  failed nightly restic run would have gone unnoticed. The role now attaches the
  channel at creation, gives push monitors a dashboard URL, and repairs existing
  monitors; all monitors verified attached.

## Dashboard: status band + live alerts (2026-08-31)
- **System tab** gained a full-width **Status** band (under Admin, above Download
  Activity) with `Internet` (per-ISP WAN) and `Alerts` (ntfy volume, 12h).
- **Bottom status bar** on every tab (`custom.js` + `custom.css`, new to the
  homepage role): WAN pills + alert count + newest alert, and **live toasts**
  from ntfy's SSE stream.
- New n8n workflow **`homelab-alerts-summary`** (`/webhook/alerts-summary`):
  ntfy answers NDJSON, which Homepage's customapi cannot parse. Window capped at
  ntfy's 12h `cache-duration`.
- Browser-side fetches must be HTTPS same-scheme and CORS-allowed — they use the
  `n8n.puhome.net` / `ntfy.puhome.net` vhosts, not direct ports. The wan-status
  Respond node now sets `Access-Control-Allow-Origin`.
- Homepage serves custom assets at **`/api/config/custom.js`**, not `/custom.js`.

## Manual changes on unmanaged hosts (not in Ansible)
- **dns01 (Pi-hole) — whitelisted `push.apple.com`** (2026-08-31). It was being
  blocked by `blocklistproject/Lists/ads.txt`, so Pi-hole forged `0.0.0.0` for
  it network-wide. Applied with `pihole -w push.apple.com`. **This lives only on
  the Pi** — dns01 is unmanaged, so a rebuild loses it. Note: the domain has no
  A record upstream anyway (Apple returns NODATA), so this was a correctness fix
  rather than the cause of the iOS push problem it was found while chasing.
  Pi-hole is v5.18.2 (v6.4.3 available).

## Open items / TODO (carry forward)

Reworked 2026-09-08 after an unattended sweep (PR #7). Split by what actually
blocks them, because "open" was hiding three different situations.

### Fixed in PR #7 — verify after it deploys

- **nzbget unbounded log + default buffers.** `WriteLog=rotate` (`RotateLog=7`),
  `ArticleCache=700`, `WriteBuffer=1024`, all enforced by the drift block in
  `arr/tasks/download_config.yml`, plus a one-off delete of the 398 MB
  `nzbget.log` guarded on the file still saying `append` so it fires exactly
  once. Was deferred for an active download; **the queue was empty when this
  was written**, and the block stops nzbget to apply.
- **CI checked out by branch name**, so merged PRs went red once their branch
  was deleted. All four jobs now fetch `$GITHUB_SHA`, falling back to the branch
  if the server refuses a by-SHA fetch.
- **`ansible-lint` re-downloaded Galaxy roles every run.** `offline: true` in
  `.ansible-lint`. Those roles are legacy v2 leftovers unused by anything under
  `playbooks/hosts/`, so nothing needed fetching and the network dependency was
  pure flakiness.
- **Arena Model in the Open WebUI picker** — **VERIFIED GONE 2026-09-08** after
  deploy; the user confirmed the picker is clean. The env var never had a chance:
  `evaluation.arena.enable=true` was already persisted in `webui.db`, and
  Open WebUI's PersistentConfig lets the DB outrank env once seeded.
  `ENABLE_PERSISTENT_CONFIG=false` makes the role authoritative on every start.
  Trade-off: admin-UI settings no longer survive a restart, which is also what
  stops an anonymous visitor changing settings for everyone on a login-less
  endpoint.
- **Homepage re-templated on every run.** The Portainer token is now minted once
  and cached at `{{ homepage_root_dir }}/.portainer_token`, re-minted only if the
  cache is gone or Portainer no longer lists a matching token. (The "Portainer
  accumulates tokens" half was already fixed — the role deletes old
  `homepage-dash` tokens before minting.)
- **Jellyfin SSO admin mapping.** Codified: the role reads each SSO user's policy
  and POSTs it back with `IsAdministrator` flipped, only for users that lack it,
  so a rebuild no longer silently demotes `uknth`.

### Done outside the PR (live changes, nothing to merge)

- **Branch protection on `master`** — now requires `CI / *` (all four CI jobs) with
  0 required approvals so you can still merge your own PRs. The PR dry-run is
  deliberately **not** required: it can fail for environmental reasons (a host
  briefly unreachable) and `deploy.sh` runs its own pre-apply check anyway.
- **`buildx_buildkit_mybuilder0` removed** from cmp01; only the `default` builder
  remains.
- **Homebrew "dubious ownership" was already fixed** — the entry was stale.
  Verified 2026-09-08: `brew --version` is clean as both `ansible` and `uknth` on
  ai01.

### Found 2026-09-08, not yet fixed — the fleet is not idempotent

A **docs-only** deploy (PR #6, which changed nothing but markdown) still reported
**39 changed tasks and restarted n8n.** A second run of an unchanged repo should
be silent; this is drift-reporting noise loud enough to hide a real change.

Counted from `/tmp/gitops-apply.log` on util01:

| Source | Changed | Note |
|---|---|---|
| `services/productivity/n8n` | **30** | re-templates credentials, re-imports and re-publishes every workflow, deletes the plaintext files, then **restarts n8n** — every single run |
| `services/dashboard/homepage` | 1 | "Template homepage config files" — the Portainer token churn, fixed in PR #7 |
| `services/monitoring/beszel_hub` | 3 | superuser / app-URL / OIDC API calls report changed unconditionally |
| `services/dashboard/portainer` | 1 | "Configure Authentik OAuth", same pattern |
| `system/network` (ai01, ctl01) | 2 | macOS `networksetup` reports changed every run |
| `system/tailscale` (gw01) | 1 | "Bring Tailscale up as subnet router" |
| `services/auth/authentik` | 1 | "Create Authentik directories" — a plain directory task should not churn |
| `services/productivity/tickets` | 1 | "Deploy tickets" |

n8n is the one that matters: restarting it on every deploy briefly drops the
webhooks and schedules that the research pipeline, the ticket sync and the
maintenance jobs all depend on. The rest is cosmetic but it is what makes a
`--diff` unreadable, which is the real cost — the PR dry-run is only useful if
a changed line means something.

Not attempted in PR #7: this is seven roles and each needs its own idempotency
fix (proper `changed_when`, or a read-before-write check), which is a different
piece of work from the sweep. Worth doing before the dry-run output is trusted
as a review artefact.

### Mirroring (decided 2026-09-08)

Gitea is the source of truth; **github.com/uknth/homelab** and
**git.sr.ht/~uknth/homelab** are mirrors. Both are **public**, and this repo is
private on Gitea — that asymmetry is deliberate and was confirmed explicitly.

`.gitea/workflows/mirror.yml` force-pushes heads and tags with `--prune`, so the
mirrors match this repo exactly. **The user accepted that this destroys the
pre-existing GitHub branches** (`v2-home`, `main`, `dev01` and an old `master`
carrying 2 unique commits) rather than mirroring to a side branch. sr.ht was
already a clean fast-forward, 25 commits behind.

Audited before enabling, and worth re-running before any change widens what is
published:

| check | result |
|---|---|
| private keys tracked or in history | none — `keys/` is self-ignoring (`*` + `!.gitignore`), only `keys/.gitignore` is tracked |
| `sk-ant-` / `ghp_` / `github_pat_` / `AKIA` / PEM headers | 0 in tracked files **and** 0 across all history |
| `hosts/group_vars/all/vault.yml` | `$ANSIBLE_VAULT;1.1;AES256` |
| credentials in templates | all `{{ vault_* }}` references, no literals |
| ssh keys appearing in docs/host_vars | public halves only |

What IS published: ~16 RFC1918 addresses, ~30 `puhome.net` hostnames, the full
service/port map and the SSO design. Reconnaissance value, not credentials.
**The one standing consequence: an ansible-vault file now sits in a public repo,
so it is only as strong as the vault password.** If that password is weak or
reused, change it — the mirror makes it an offline brute-force target rather
than a LAN-only one.

### Vault history purge (2026-09-08) — and what a force-push does NOT do

The vault password was rotated from **9 characters to 64**, and every historical
vault blob was then purged from git history with `git filter-repo`. Rekeying
alone was not enough: git keeps the old blobs forever, so the weak-password
versions would have stayed public in history.

**Five paths ever held a vault**, and they were found by scanning every blob in
all 193 commits for the `$ANSIBLE_VAULT` header — not by guessing names, which
missed two of them:

```
vault.yml                       (root; hidden from path-filtered log by history simplification)
group_vars/sidekick/vault       (2023)
group_vars/all/vault            (21 commits, 2023-01 .. 2026-06 — the largest)
group_vars/all/vault.yml
hosts/group_vars/all/vault.yml
```

Result: 38 vault blobs before, 0 after the rewrite, 1 after re-adding the
current vault. History survived — 189 commits (4 became empty and were dropped),
oldest still 2023-01-08, `v3.0.0` intact. Backup bundle was taken first.

**The lesson worth keeping — a force-push does not delete anything.** It moves
refs; the objects stay and remain fetchable by SHA:

| host | after force-push | why |
|---|---|---|
| Gitea | 38 blobs still present | `refs/pull/1..8/head` pin the pre-purge commits, so GC cannot drop them |
| GitHub | old blobs still served by API | keeps unreferenced objects until its own GC; deleting *branches* does nothing |
| sr.ht | pre-purge commits still fetchable by SHA | same |

So the only reliable fix for a public remote is **deleting and recreating the
repository**, which is what was done. Deleting branches, or force-pushing, is
not sufficient — verify with a direct blob/commit fetch by SHA rather than by
looking at the branch list.

**VERIFIED 2026-09-09** after both repos were deleted and recreated, against a
populated repo so a 404 means something:

| probe | before | after |
|---|---|---|
| GitHub blob `09ef9f0b877f` | http 200, vault content | **404** |
| GitHub blob `172f9ada3e15` | http 200, vault content | **404** |
| sr.ht `git fetch <pre-purge sha>` | succeeded | **refused** |
| all three heads | — | identical (`7630f283df99`) |

**Still outstanding: Gitea itself holds 38 vault blobs**, pinned by
`refs/pull/1..8/head`. GC cannot drop them while those refs exist. Deleting the
refs purges the blobs but breaks the "Files changed" view on those eight merged
PRs — their descriptions and comments survive, as those live in the database.
Gitea is private, so this is hygiene rather than exposure, and it is left as a
deliberate open choice rather than done silently.

**The executor needs a manual reset after any rewrite** — or did, until
`deploy.sh` was changed to `reset --hard` + `git clean` (see the comment there).
`git pull --ff-only` fails on rewritten history and reports "git pull failed",
which points at git rather than at the rewrite.

### Blocked on you — I cannot do these

- **HBA swap** (nas01): LSI 9300-8i needs physically fitting, then move disks,
  `zpool online`/`clear`/`scrub`.
- ~~**Dual-WAN outage path unverified**~~ — **VERIFIED 2026-09-08 by the user**:
  a physical WAN cable pull produced the expected failover and alerting. The
  last untested assumption in the dual-WAN work is now tested.
- **Dashboard + status band visual sign-off** — needs your eyes in a browser.
- **Kavita Homepage widget** — needs a UI-generated API key, or the real Kavita
  admin password (`vault_kavita_admin_password` did not match; username `uknth`).
- **Gitea → sr.ht/GitHub mirrors** — `.gitea/workflows/mirror.yml` exists as of
  2026-09-08. The remaining manual step is adding this public key to **both**
  accounts, after which the workflow runs on every push to master:
  `ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIMZv6MmbYqklc2ljjVuRxbJAMENB3VtkKSf+GjxH79fj gitea-mirror@puhome.net`
  (github.com/settings/keys and meta.sr.ht/keys). Until it is added the job goes
  red on a permission-denied, which is the intended signal.

### Still open, deliberately not attempted in the sweep

- **Mirroring a NEW repo is undecided.** `homelab` mirrors via its own
  `.gitea/workflows/mirror.yml`; Gitea has no instance-level mirroring, so each
  repo needs its own arrangement. Options and the trade-offs are written up in
  [`plan/gitops.md`](plan/gitops.md#adding-a-mirror-for-a-new-repo). Short
  version: prefer a per-repo `mirror.yml` (push-triggered, no cron, independent
  failures); a central *scheduled* job was rejected because cron leaves pushes
  unmirrored until the next tick; a webhook → n8n router is the viable central
  option but needs `workflow_dispatch` support confirmed first.
- **Quartz search box still uses FlexSearch**, not `search.puhome.net`. Needs a
  templated `quartz.layout.ts` mounted into the `quartz-build` image — TypeScript
  that must compile against a specific Quartz version, which cannot be verified
  from here. The build script stages and swaps, so a bad build leaves the served
  wiki intact, but this deserves an iteration with feedback rather than a blind
  unattended guess.
- **Diun still notifies out-of-band**, not through the PR flow. This is a design
  question (who opens the PR, and how an image bump becomes a diff) rather than a
  fix, so it wants deciding before building.
- **cmp01 reboot** pending for `linux-image-6.1.0-52`. Not urgent, and rebooting
  the host that runs Gitea, the runner and the media stack is not an unattended
  action.
- **Phase 9d** — LLM summaries on cmp01's A4000. Unstarted; it is a build, not a
  loose end.
- **Mixed Debian releases** — `util01` is trixie, `gw01`/`cmp01` are bookworm. An
  OS-upgrade decision, not a sweep item.
- **`ansible` in the macOS `admin` group** (`ansible_user_macos_admin`) — noted as
  a standing privilege choice, not a defect.

## Session 2026-08-31 — Beszel: ai01 outage, fleet agent upgrade, nas02 online

Started from "ai01 is down on beszel". Three outcomes: a new failure mode, a
fleet-wide agent upgrade, and the last dark system brought up.

### Incident — ai01 "down" but the agent never died
Every surface check passed: ping, port 45876, TCP connect from the hub, SSH
handshake, matching agent key, same agent version as ctl01, no macOS firewall, no
sleep/wake, and the process had been up a full day with no new `fatal error`. So
this was **not** the documented `SIGBUS` crash-loop.

Root cause: the agent was alive but **emitting malformed JSON**. Fetching stats with
the hub's own key showed two zeroed bytes — `"m":"\u0000pple M4 Pro"` (harmless, Go
escaped it) and a raw NUL replacing the opening quote of `"container"`, which makes
the payload unparseable. The hub discards unparseable responses **silently**, so
`updated` froze at 12:07 UTC while the tile stayed nominally reachable. ctl01 and
cmp01, fetched identically, were clean.

Fix: `launchctl kickstart -k system/dev.beszel.agent`. Recovered on its own two poll
cycles later — **no hub restart needed**. Two zeroed bytes at string boundaries in a
long-lived process points at memory corruption in agent 0.9.1 on Apple Silicon; the
same host had previously taken a `SIGBUS` in that binary. Now documented as cause (3)
in [`runbooks/beszel-nas-agents.md`](runbooks/beszel-nas-agents.md).

### Fleet agent upgrade 0.9.1 → 0.18.8 (matches the hub)
`roles/system/beszel_agent` could not actually upgrade anything. Both OS paths gated
the download on `creates: /opt/beszel/beszel-agent`, so bumping `beszel_agent_version`
silently no-opped — the binary already existed. Fixed:

- Gate on the **installed version** (`beszel-agent -v`) instead of `creates:`.
- **Stage then `mv`** into place — extracting over a running binary fails `ETXTBSY`;
  a rename swaps the directory entry and the live process keeps the old inode.
- The download tasks now **notify their restart handler** (previously only the
  plist/unit template did, so a version bump would never have cycled the service).
- Download **retries** (`curl --retry` / `until: succeeded`) — ctl01's first attempt
  died on a transient `curl: (56)`, unsurprising on the dual-WAN link.
- `playbooks/bootstrap/linux.yml` gained the `always`-tagged `setup` pre-task that
  `macos.yml` already had; without it `--tags beszel-agent` failed outright with
  `'ansible_system' is undefined`. Pre-existing; affected any tag-scoped run.

All five managed hosts on 0.18.8, re-runs report `changed=0`.

### nas02 online — 7/7 systems up
Deployed via **Package Center (SynoCommunity), not Docker** —
`/volume1/@appstore/beszel-agent/bin/beszel-agent`, running as `sc-beszel-agent`.
The runbook's `docker run` recipe was dropped in favour of it.

Verified from the hub API rather than the UI, which surfaced two things a green tile
hides:

- **nas02 is fine**: root is the 2.28 GB `md0` system partition, with the 4 TB mirror
  (`md2`, 3662.87 GB) correctly reported as an extra filesystem.
- **nas01 is NOT monitoring its pool** — 458.56 GB at 0.03 % used (the boot device),
  `efs` empty. The runbook had recommended `EXTRA_FILESYSTEMS`, but that env var is
  **binary-only**; a containerised agent needs an `/extra-filesystems/<name>:ro`
  mount. **Open item.**
- **nas02 SMART is array-level only** — `smart_devices` lists `md0`/`md1`/`md2` as
  `mdraid`, `state=PASSED`, but `temp`/`hours`/`cycles` are all `0` and the physical
  `sda`/`sdb` are absent. Catches a degraded mirror, not a failing drive. The package
  runs unprivileged. DSM's Storage Manager still does per-disk SMART. **Open item.**

Result: **7/7 systems up** (gw01, cmp01, util01, ai01, ctl01, nas01, nas02).

## Session 2026-08-31 — Phase 8a: paperless-ai + local inference (cmp01)

Deployed and verified. Runs **inside the Paperless compose project** (not a separate
role) so it shares Paperless's lifecycle and reaches it as `webserver` over the project
network rather than a host IP.

- **llama.cpp, not Ollama.** Ollama *is* llama.cpp wrapped in a model registry and
  load/unload logic — all dead weight for one permanently-pinned model, and less
  declarative (the model becomes runtime state in a volume). With `llama-server` the
  exact GGUF and every flag are literal values in the role.
- **Gemma 3 4B Q4_K_M** (`ggml-org/gemma-3-4b-it-GGUF`, sha256-pinned, 2.3 GiB),
  using ~3.3 GB of the A4000's 16 GB. Sub-2B models were rejected: the job needs
  strict JSON and they fail it intermittently, which shows up as documents silently
  going untagged. Measured: **1785 prompt tokens → valid JSON in 1.4 s.**
- **Network isolation.** `llama` sits on an `internal: true` network with no LAN
  route, no published port and no internet; Ansible fetches the GGUF to a bind mount
  so the container needs no outbound access. Only paperless-ai can reach it.
- **The token is derived, not stored.** The role ensures a dedicated `paperless-ai`
  Paperless service account and reads its DRF token back at deploy time, so nothing
  lands in the vault and it cannot drift if rotated. It is a superuser (Paperless has
  no narrower role that can re-tag documents it does not own) and deliberately
  separate from `uknth` so it is revocable on its own.
- **`docs-ai.puhome.net`**, SSO-gated — unlike Paperless itself, which stays open for
  API/mobile clients. Verified redirecting to Authentik.

### Gotchas found the hard way (all now handled in the role)
1. **Docker env does NOT configure paperless-ai**, contrary to upstream's claim. The
   app logs "No .env file found. Starting setup process..." and aborts scanning
   regardless of container environment — it reads core config from `/app/data/.env`
   only. Ansible now templates that file directly (same declarative outcome, the
   source the app actually honours); the setup wizard never runs.
2. **`PROCESS_PREDEFINED_DOCUMENTS` is inverted.** `yes` is the *restrictive* setting
   (process only documents carrying `TAGS`); `no` turns it loose on the whole archive.
   Set to `yes` with trigger tag **`ai-process`** — nothing is touched until a document
   is deliberately tagged. Verified: 0 of 182 documents modified after the initial scan.
3. **The RAG service reads different variable names** than the Node app writes
   (upstream issue #896) and reports "Server: Offline" without `PAPERLESS_URL` /
   `PAPERLESS_NGX_URL` / `PAPERLESS_HOST` / `PAPERLESS_TOKEN` / `PAPERLESS_APIKEY`
   aliases (base URL, no `/api`). All set.
4. **Bundled ChromaDB phones home** by default; `ANONYMIZED_TELEMETRY=false` set —
   the whole point of local inference here is that these are financial records.
5. **Upstream is unmaintained** (paused for a rewrite). It holds a superuser token and
   writes to every document, so it is excluded from the `ops/maintenance` auto-upgrade
   allowlist — review before bumping the image.

**To start using it:** tag a document `ai-process` in Paperless. Within 30 minutes
(`*/30` cron) it is analysed, rewritten, and tagged `ai-processed` — filter on that tag
to review or undo everything it has touched. Widen the rollout only once tag quality
looks right.

## Phase 8 (in progress)
- **8a · cmp01 — DONE 2026-08-31**: paperless-ai + llama.cpp (Gemma 3 4B) on the A4000.
  See the session notes above.
- **8b · ai01 — blocked on a decision**: native Ollama (Homebrew + launchd; Docker on macOS
  gets no Metal accel) serving the **agent** model at 64k+ context. Also intended to back the
  **Homepage LLM search** (still a placeholder box). Not started — the user is reviewing the
  agent options first, and the choice determines the model and context budget.
- **8c · ctl01**: dev toolchain (`system/colima` + `dev/{go,node,kubectl,helm,kind,awscli}`)
  plus the agent itself. Prereqs **done** — 10.0.2.4 reserved, inventory updated, and
  `ansible` is now in the macOS `admin` group. Remaining: `site.yml -l ctl01`.
- Build per [`spec/conventions.md`](spec/conventions.md); wire into `site.yml`; update the roadmap.

### Agent decision (8b/8c) — researched 2026-08-31, awaiting the user
Requirement: web UI primary, Telegram/WhatsApp secondary; agent on **ctl01**, LLM on **ai01**.

- **Hermes Agent** (Nous Research, MIT) — recommended. Web UI is the community
  [`nesquena/hermes-webui`](https://github.com/nesquena/hermes-webui) (three-panel, port 8787,
  **native OIDC** so it drops into Authentik). Native cron. Model-agnostic; documented Ollama path.
- **OpenClaw** (formerly Clawdbot/Moltbot) — built-in dashboard, human-authored skills (a better
  fit for the declarative principle), but third-party reports of a March 2026 CVE cluster incl.
  CVSS 9.9 make it the riskier choice for something executing shell commands on the LAN.
- **Open tension:** Hermes *writes its own skills* into `~/.hermes/skills/` — mutable state the
  roles do not own, the same objection that killed Homarr. Treat `~/.hermes` as restic-backed
  data, or prefer OpenClaw's authored skills.
- **Sharp edges:** Hermes needs **≥64k context**, and `OLLAMA_CONTEXT_LENGTH` can only be set
  server-side at startup (the OpenAI API cannot raise it per-request) — the most common failure.
  Ollama also binds `127.0.0.1` by default; reaching it from ctl01 needs `OLLAMA_HOST=0.0.0.0`
  in the launchd plist, and it has **no auth**. WhatsApp bridges are unofficial and risk a ban —
  prefer Telegram's bot API.
- **Biggest risk:** ctl01 holds the vault password and `ansible_rsa.private` — fleet-wide root.
  An agent with shell access there, fed untrusted web/document/message input, is a
  prompt-injection path to full compromise. Run its tools under the Docker backend via Colima
  (already in 8c scope) and keep those credentials off its reachable filesystem — or host it on
  util01, which carries no fleet-wide secrets.

### Phase 8 design questions — status
1. **`hermes` vs `paperless-ai`** — RESOLVED. They were never alternatives: `hermes` is
   [hermes-agent](https://hermes-agent.nousresearch.com/) (a personal agent), paperless-ai is
   document tagging. Both are wanted, on different hosts. `playbooks/hosts/ai01.yml` is still
   stale scaffolding referencing `services/ai/hermes` and `services/ai/ollama`, neither of
   which exists; fix it when 8b is built.
2. **paperless-ai's runtime** — RESOLVED. It runs on **cmp01** inside the Paperless compose
   project with its own llama.cpp, so Colima never goes near ai01 and the spec line holds.
3. **`system/brew` is commented out** of `playbooks/bootstrap/macos.yml` — STILL OPEN, and a
   prerequisite for every Homebrew-driven role in 8b/8c. `ansible` is now in the macOS `admin`
   group so it *can* write to `/opt/homebrew`, but git still rejects the repo as "dubious
   ownership" for that user (`brew --version` reports "shallow or no git repository"). The
   `system/brew` role must set `safe.directory` for `/opt/homebrew` and its taps.
4. **Nothing built for ai01/ctl01** — STILL OPEN. No `roles/dev/`, no `roles/services/ai/`.


## Memory (persisted preferences — /Users/uknth/.claude/.../memory/)
- **dashboard-must-be-declarative**: dashboards/infra must be YAML/config-driven, not UI/DB-driven
  (why Homarr was rejected). Clean, simple aesthetic; **light mode only**; few tabs.
- **prefers-manual-curation**: curates media by hand in the *arr apps; don't propose
  recommendation/discovery services.

## Deploy cheatsheet

**Deploying = opening a PR.** Do not run `ansible-playbook` against the fleet by
hand; that bypasses CI, the PR dry-run, and the record of what was applied when.

```
git checkout -b <branch> && git commit && git push origin <branch>
# open a PR against master in Gitea  ->  ci.yml + pr-dryrun.yml run
# merge  ->  deploy.yml -> util01 executor -> apply
```

The commands below still exist for **reading** state or for a genuine bootstrap
(the deploy path cannot deploy itself). Say so explicitly if you use them.

```
ansible-playbook site.yml --check --diff        # dry-run only (check-mode-safe)
ansible-playbook playbooks/hosts/<host>.yml --tags <role>
```

## Session 2026-08-30 — maintenance automation

- **Homepage "Nodes" tab** — per-node container inventory. Node list from the
  Portainer API (`/api/endpoints`), containers from the Docker API through it.
  Nothing enumerated by hand, so undeclared containers show up — which is how the
  orphans below were found.
- **Automatic container upgrades** (reverses the old "never auto-update" rule —
  see [`spec/maintenance.md`](spec/maintenance.md) §3). Diun → n8n webhook →
  `ops/maintenance/maintenance.sh` → ntfy. Stateless only; `paperless`,
  `authentik`, `n8n` are held and reported for manual application.
- **`ops/maintenance`** (new role, util01): one script for `--image` /
  `--upgrade-all` / `--prune` / `--all`, reached by n8n over an SSH key locked to
  a forced command. Weekly `--all` runs Sundays 04:00.
- **Diun fixed and fleet-wide.** It now runs on all three docker hosts (its
  provider is single-endpoint). More importantly `watchByDefault` was never set,
  so Diun had watched **nothing** since deployment — it logged "No image found"
  every 6h. Now tracking 17 images on cmp01, 12 on util01.
- **`site.yml` now imports cmp01.** The import had been commented out since
  Phase 5, so the GitOps executor never reconciled cmp01's services.

### Incident 2026-08-30 — Jellyfin down (resolved)

An automated upgrade recreated Jellyfin, which then failed to start:
`open /lib/firmware/nvidia/535.261.03/gsp_ga10x.bin: no such file or directory`.

**Not caused by the upgrade — exposed by it.** `unattended-upgrades` had moved the
NVIDIA packages to 535.309.01 while the *old* 535.261.03 kernel module stayed
loaded, and `/var/run/cdi/nvidia.yaml` (generated 2026-08-17) still pinned the old
driver's firmware paths. Running containers were unaffected; any new GPU container
would fail. Jellyfin would have died on the next reboot regardless.

Fixed by reloading the nvidia kernel modules (after stopping `nvidia-persistenced`
and the Beszel agent, which held `/dev/nvidia*`) and regenerating the CDI spec.
`system/nvidia_docker` now detects the drift and regenerates automatically, so a
future driver bump can't silently break GPU containers.

**A reboot of cmp01 is still pending** for `linux-image-6.1.0-52` — unrelated to
the above, and not urgent.

### Closed 2026-08-30 (verified live)
- **gw01 container DNS — fixed.** gw01's resolv.conf is loopback (it *is* the DNS
  host), so Docker fell back to public DNS and containers got IPv6-only answers on
  an IPv4-only net; Diun on gw01 could not reach any registry. `/etc/docker/daemon.json`
  now pins container DNS to Blocky and docker was restarted (06:48 UTC). Verified:
  Diun on gw01 now analyses 4 images with `failed=0` (it previously failed every run).
- **Orphaned containers — removed.** `mixarr` (cmp01) and `homarr` (util01) are gone,
  images and all. cmp01 is at 187 GB / 915 GB (22%); only 4.8 GB of unused volumes
  remain, so the ~37 GB was reclaimed.
- **ctl01 IP — reserved.** Now `10.0.2.4` in Omada; `hosts/hosts.yml`, `AGENTS.md`,
  `spec/hosts.md`, `spec/architecture.md` and the roadmap prereq all updated.

### Monitoring audit 2026-08-30 — three of seven systems were dark

Prompted by "we got no alert when ctl01 changed IP". Alerting turned out to be
**working**; the audit found three unrelated faults instead.

- **Alerting is fine.** ctl01's Status alert fired at 12:50:55 UTC, exactly the
  configured 10 minutes after it went down at 12:40:55, and ntfy delivered
  "Connection to ctl01 is down". The earlier check simply fell inside that
  10-minute window. Note ntfy's cache is **12 h**, so older alerts (ai01's, which
  did fire on 2026-08-25) have already aged out of `/homelab-alerts/json`.
- **`beszel_hub` never corrected a changed address** — the register task only
  POSTs names the hub has never seen, so ctl01 stayed pinned to the dead DHCP
  `10.0.2.115` and would have sat "down" forever. The role now PATCHes `host`/`port`
  when they drift from the inventory, and notifies a hub restart (the hub caches
  addresses in memory — the PATCH alone does not take effect).
- **The alert loop used a stale snapshot.** It looped over the systems list read
  *before* new systems were POSTed, so a freshly registered host got no alerts
  until the role ran a second time. Fixed by re-reading systems after registration.
- **ai01's agent was dead for five days.** `beszel-agent` crashed with `SIGBUS` in
  `gopsutil/v4/sensors.TemperaturesWithContext` on 2026-08-25 (Apple Silicon SMC
  sensor read, macOS 26.4.1). launchd restarted it and the port reopened, so a port
  probe looked healthy while the hub saw nothing. Restarting the agent recovered it.
  Both failure modes are now in [`runbooks/beszel-nas-agents.md`](runbooks/beszel-nas-agents.md).

Result: **6/7 systems up** (gw01, cmp01, util01, ai01, ctl01, nas01).

### Still open
- ~~**nas02 Beszel agent was never deployed**~~ — **RESOLVED 2026-08-31**, see the
  monitoring session below. 7/7 systems now up.
- **`ansible` is now in the macOS `admin` group** (`system/ansible_user`,
  `ansible_user_macos_admin`) so Homebrew-driven roles can write to `/opt/homebrew`
  (owned `uknth:admin`). It already held NOPASSWD sudo, so this grants no new
  privilege. ~~Still outstanding: git "dubious ownership" for `ansible`~~ —
  **RESOLVED**, `system/brew` sets the `safe.directory` entries. Verified
  2026-09-08: `brew --version` is clean as both `ansible` and `uknth` on ai01.
- **cmp01 reboot** pending for `linux-image-6.1.0-52` — not urgent, and now that
  cmp01 runs Gitea + the Actions runner a reboot takes the deploy pipeline down
  with it. Do it deliberately, not as part of a sweep.
- ~~**`buildx_buildkit_mybuilder0`** leftover buildx builder on cmp01~~ —
  **RESOLVED 2026-09-08**, removed with `docker buildx rm mybuilder`; only the
  `default` builder remains.

## Session 2026-09-07 — nzbget "cannot add .nzb" triage (no server fault)

Reported as: uploading a `.nzb` through the web UI hangs. **nzbget and the proxy were
both fine — it was Safari.** The same upload succeeded in Firefox.

- **The append request never left the browser.** While the upload sat "stuck", nginx on
  gw01 logged only the UI's 1s polling GETs and no `POST /jsonrpc`. Confirmed it was not
  merely in flight and therefore unlogged (nginx logs on completion): no established
  nginx → `10.0.2.5:6789` connection, `/var/lib/nginx/body/` empty, browser sockets idle
  at Recv-Q/Send-Q 0. Earlier `POST /jsonrpc → 499` entries are the aborted attempts —
  499 is nginx's "client closed request".
- **Server-side add path verified working**: posting a minimal `.nzb` straight to the
  API on cmp01 returned an NZBID and queued the collection correctly; test entry deleted.
- **Ruled out**: container health (up, v26.3-ls262), disk (`/scratch` 459G, `/data` 13T
  free), news servers (both active), path ownership (`abc`/1001), auth
  (`ControlUsername=uknth`; no restricted/add accounts shadowing it), and any proxy body
  limit — `client_max_body_size` is `1024m` in `/etc/nginx/nginx.conf:21`, and a 2 MB
  probe POST got the same 302 as a 10 KB one.
- Triage did surface the two nzbget config issues now listed under
  [Open items / TODO](#open-items--todo-carry-forward).

**If this recurs: check the browser first.** Reproduce in a second browser before
touching the stack.


## Session 2026-09-08 — ask/search split, LiteLLM, and the first real PR deploys

Five PRs (#1–#5), all merged, all deployed through Gitea. **This was the first time
the PR pipeline was used for real**, and it found four genuine bugs before anything
reached a host.

### What shipped

- **`ask.puhome.net` and `search.puhome.net` are now separate services**, because
  they are two trust domains rather than two features:
  - `search.puhome.net` (vaultask) — wiki search + wiki-grounded answers. Holds the
    notes, including `Secrets/` and `Finances/`, talks **only** to omlx on ai01, and
    is behind Authentik. Its ungrounded fallback was **removed**: when retrieval
    misses it says so and lists the closest notes rather than answering from general
    knowledge.
  - `ask.puhome.net` (Open WebUI, cmp01:8104) — general chat, **no wiki data at all**,
    reaches Anthropic. **No login** (`WEBUI_AUTH=false`, vhost `sso: false`), by
    explicit and repeated user instruction.
- **LiteLLM proxy** (cmp01, container-internal only, not published to the host) owns
  every provider key. Open WebUI holds none — it is the unauthenticated surface.
- **Model set is enforced, not configured.** LiteLLM defines `qwen` and
  `claude-sonnet-5`. Opus and Haiku are not restricted; they are *absent*, so they
  cannot be routed to whatever a picker shows or an API call asks for. Haiku was
  removed on request (PR #4), which means every fallback now bills at Sonnet rates
  ($3/$15 per Mtok) on an endpoint with no login — the Anthropic spend cap is the
  only backstop.

### Failover is proven, not assumed

Stopped omlx on ai01 (`launchctl bootout system/net.puhome.omlx`) and watched a
request land on Anthropic, then restored it:

```
omlx up      -> served_by: qwen
omlx stopped -> served_by: claude-haiku-4-5-20251001   (chain was qwen->haiku->sonnet)
omlx back    -> served_by: qwen
```

`KeepAlive` is `true`, so killing the process only respawns it — a real outage
window needs `bootout` then `bootstrap`.

### The check-mode trap — read this before adding a role

Four separate failures this session, all the same root cause: **a task that depends
on an earlier task's side effect, which `--check` never produces.** Because
`pr-dryrun.yml` runs the whole fleet in check mode on every PR, this now fails a PR
rather than being invisible. It only ever bites on a role's *first-ever* deploy,
which is precisely when nobody is looking for it.

1. nginx `sites-enabled` symlink — the vhost it points at has not been templated yet.
   Fixed with `force: "{{ ansible_check_mode }}"` (conditional, not flat `true`, so a
   real run still fails loudly on a site with no template behind it).
2. Key generation with `check_mode: false` writing into a compose dir that check mode
   never created. Now skipped under `--check`, with a placeholder fact.
3. The restart **handler** — guarding the deploy task was not enough, because the
   templates still report `changed` under check mode and notify the handler anyway.
   **A notified handler needs the same guard as the task that notifies it.**
4. Same handler pattern latent in 26 other roles; all guarded in PR #3, and the rule
   is now written into [`AGENTS.md`](../AGENTS.md) under Role Conventions.

Also, for the third time: `set -o pipefail` without `executable: /bin/bash`. cmp01's
`/bin/sh` is dash and rejects it. macOS `/bin/sh` is bash and accepts it, which is why
`beszel_agent/tasks/macos.yml` still has one and is deliberately left alone.

### Pipeline behaviours worth knowing

- **`deploy.sh` runs its own `--check` before applying and refuses to apply if it
  fails.** This is why PR #1's broken merge changed *nothing* on any host instead of
  half-configuring cmp01. Best safety property in the pipeline.
- **Deploy runs collapse under concurrency.** With `cancel-in-progress: false`, a
  superseded *pending* deploy is cancelled in favour of the newest commit. A
  "cancelled" deploy after two quick merges is correct, not a failure — the surviving
  run deploys a superset.
- **Merging a PR fires stray `pull_request` events** on any other open PR whose base
  moved. Harmless, but they occupy the serial runner and can fail if their branch was
  deleted (see Open items).
- **`docker compose restart <svc>` from `/opt/homelab/openwebui/compose/` silently
  does nothing** and still exits 0: the compose project is named `openwebui`, but
  compose infers `compose` from the directory, matches no containers, and succeeds.
  Restart by container name, and always check `Up <n> seconds` afterwards.

### One-off exception to the deploy rule

The Haiku removal (PR #4) was applied **directly to cmp01 first**, at the user's
explicit request ("for this time only"), with the PR bringing git back into sync. The
next deploy reported `ok` on `Template the litellm config`, confirming the hand-edit
was byte-identical to what Ansible renders. That check is the reason to bother doing
it that way — a `changed` there would have meant silent drift.

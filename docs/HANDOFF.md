# Session Handoff — Context Dump (updated 2026-08-30)

Single pick-up point for a fresh session. Everything below reflects `master` at
tag **v3.0.0**. Read this, then [`plan/roadmap.md`](plan/roadmap.md) and the
[`spec/`](spec/) docs for depth. Next up: **Phase 8**.

## Where we are
- v3 homelab as Ansible IaC (spec-first, codified, idempotent). **Phases 0–7 done.**
- Branch `master`; remote `git.sr.ht:~uknth/homelab` (builds.sr.ht CI on push).
- GitOps executor on util01: systemd timer runs `deploy.sh` (tracks master), in
  **dry-run** mode (`gitops_auto_apply` off) — flip to enable auto-apply.
- Vault: `hosts/group_vars/all/vault.yml`; password file `~/.config/homelab/.vault_pass`
  (ansible.cfg). To edit safely: `ansible-vault view … > tmp`, append, then
  `ansible-vault encrypt tmp --output hosts/group_vars/all/vault.yml` (do NOT pass
  `--vault-password-file` to encrypt — ansible.cfg already provides the id).

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
- **Homepage re-templates on every run**: `gather_keys.yml` mints a *fresh*
  Portainer API token each time, so `services.yaml` always differs and Homepage
  restarts on every `site.yml` — and Portainer accumulates tokens. Pre-existing,
  predates the dashboard work. Worth making the token lookup reuse an existing
  one (or store it in the vault).
- **Status band + bottom bar need visual sign-off** — verified end to end
  server-side (endpoints, CORS, SSE, asset serving) but not seen in a browser.
- **Dual-WAN watch: outage path unverified.** Everything is live and reporting,
  but no ISP has actually gone down since it was built. Pull one WAN cable to
  confirm the red/ntfy path before trusting it.
- **Dashboard visual sign-off pending**: two-pane card widths + masonry split need the user's
  eyes (no browser preview here). If cards look narrow, force full-width on the card element.
  Navidrome now-playing only shows while something plays.
- **Kavita Homepage widget** not wired: Kavita 0.9.x plugin-auth rejects a DB-set API key and
  only exposes library counts (not reading progress). Needs a **UI-generated API key** from the
  user, or the real Kavita admin password (vault_kavita_admin_password didn't match; username is
  `uknth`). Kavita stays a link for now.
- **HBA swap** (nas01): LSI 9300-8i ordered for the degraded RAIDZ2 data-pool. After fitting:
  move disks, `zpool online`/`clear`/`scrub`. Do only after hardware is in.
- **Jellyfin SSO admin** mapping not codified (a rebuild resets `uknth` to non-admin) — see
  [`spec/auth.md`](spec/auth.md).
- **GitOps auto-apply** still off (dry-run). Enable when confident.

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
```
ansible-playbook site.yml                       # whole fleet
ansible-playbook playbooks/hosts/<host>.yml     # one host
ansible-playbook playbooks/hosts/<host>.yml --tags <role>
ansible-playbook site.yml --check               # dry-run (check-mode-safe)
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
- **nas02 Beszel agent was never deployed** — port 45876 is closed and the system
  has read "down" since 2026-08-22. Its Status alert has never fired because Beszel
  only alerts on an up→down transition and nas02 was never up. nas02 is unmanaged
  (Synology) and the backup account is SFTP-chrooted, so this needs a manual
  Container Manager / admin-SSH step — the exact `docker run` is in
  [`runbooks/beszel-nas-agents.md`](runbooks/beszel-nas-agents.md).
- **`ansible` is now in the macOS `admin` group** (`system/ansible_user`,
  `ansible_user_macos_admin`) so Homebrew-driven roles can write to `/opt/homebrew`
  (owned `uknth:admin`). It already held NOPASSWD sudo, so this grants no new
  privilege. Still outstanding: git refuses the repo as "dubious ownership" for
  `ansible`, so `brew` reports "shallow or no git repository" — `system/brew` must
  set `safe.directory` for `/opt/homebrew` (and its taps).
- **cmp01 reboot** pending for `linux-image-6.1.0-52` — not urgent.
- **`buildx_buildkit_mybuilder0`** runs on cmp01 but is declared nowhere — a leftover
  buildx builder. Harmless; remove with `docker buildx rm mybuilder` when convenient.

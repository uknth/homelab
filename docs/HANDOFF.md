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

## Open items / TODO (carry forward)
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

## Phase 8 (in progress — scope being expanded 2026-08-30)
- **8a · ai01**: Ollama (native, Homebrew + launchd — Docker on macOS gets no Metal accel)
  + a Qwen model + **paperless-ai** (auto-tag/OCR-assist Paperless docs). Same LLM backs the
  **Homepage LLM search** (replace the placeholder search box).
- **8b · ctl01**: dev toolchain (`system/colima` + `dev/{go,node,kubectl,helm,kind,awscli}`).
  Prereqs now **done** — 10.0.2.4 reserved, inventory updated. Remaining: run
  `init.yml -l ctl01` then `site.yml -l ctl01`.
- Build per [`spec/conventions.md`](spec/conventions.md); wire into `site.yml`; update the roadmap.

### Phase 8 design questions (unresolved in the docs — decide before building)
1. **`hermes` vs `paperless-ai`.** `playbooks/hosts/ai01.yml` (scaffolding, commented out of
   `site.yml`) lists `services/ai/ollama` + `services/ai/hermes` — but `hermes` appears in no
   spec, only `plan/migration.md:51` ("no v2 implementation"). Meanwhile `spec/services.md:84`
   lists **paperless-ai**, which is absent from the playbook. The two disagree.
2. **paperless-ai has no runtime on ai01.** It is Docker-only, and `spec/hosts.md:80` explicitly
   keeps Colima *off* ai01. Either Colima goes on ai01 (contradicting the spec) or the container
   runs on cmp01 next to Paperless and talks to `ai01:11434` over the LAN. Latter is preferred.
3. **`system/brew` is commented out** of `playbooks/bootstrap/macos.yml`. Every Phase 8 role on
   both Macs is Homebrew-driven, so that role is an unstated prerequisite for the whole phase.
4. **Nothing is built**: no `roles/dev/`, no `roles/services/ai/`. Both host playbooks are
   scaffolding referencing 9 roles that do not exist.

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

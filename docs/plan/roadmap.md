# Roadmap

Build-out phases for v3, in dependency order. Status reflects what's actually wired into
`site.yml` and present under `roles/`. Update this doc as phases complete.

> **Resuming?** Start with [`../HANDOFF.md`](../HANDOFF.md) — the latest context dump
> (tag `v3.0.0`, Phase 8 next).

## Prerequisites (not phases, but blockers)

- [x] **Fleet bootstrap (`init.yml`)** — DONE 2026-08-22. The `ansible` user exists on all
      five managed hosts with NOPASSWD sudo and the `keys/ansible_rsa.private` key installed;
      `ansible managed -m ping` is green and sudo resolves to root everywhere. Vault password
      file (`~/.config/homelab/.vault_pass`) and vault keys (`vault_public_key`,
      `vault_sudo_password`, `vault_truenas_api_key`) all in place.
- [x] **TrueNAS API** — `vault_truenas_api_key` verified against `https://10.0.2.6/api/v2.0/` (SCALE 25.10); NFS export managed via `services/storage/truenas_nfs` (phase 3).
- [ ] **Mixed Debian releases** — `util01` is Debian 13 (trixie); `gw01`/`cmp01` are Debian 12
      (bookworm). `system/apt` and `system/docker` must resolve the codename dynamically.
- [x] **ctl01 IP** — DONE 2026-08-30. Reserved `10.0.2.4` in Omada; inventory updated and
      reachable as `ansible@10.0.2.4`.
- [x] **Git remote** — `origin` is now **Gitea** on cmp01 (`git@ssh.git.puhome.net:uknth/homelab.git`). `git.sr.ht` is demoted to a mirror; GitHub planned as a second mirror. Neither mirror is wired up yet — the keypair exists, the public key still needs adding on both sides.

## Phases

| Phase | Scope | Status |
|---|---|---|
| 0 | Spec & plan rewritten to real hardware; `hosts/hosts.yml` corrected; `user`/`media_gid` defined; Immich/Vaultwarden dropped | 🟢 done (this pass) |
| 1 | `system/` bootstrap (all Linux): ansible_user, hostname, apt, ntp, docker, **unattended_upgrades**, beszel_agent. (Tailscale is NOT here — it's gw01-only, phase 2.) | 🟢 built + run on gw01/cmp01/util01 (2026-08-22). All roles graduated out of `archive/`. NTP is native timesyncd; Docker codename-dynamic (verified on bookworm + trixie); beszel_agent guarded on hub key (activates phase 4). Idempotent, live gw01 DNS undisturbed. |
| 2 | `gw01`: **Tailscale subnet router** (10.0.2.0/24, the only TS node), Blocky (local + fallback, blocking off), nginx + `*.puhome.net` wildcard + **Authentik forward-auth snippet**, certbot (Cloudflare DNS-01); Pi-hole conditional-forward | 🟢 deployed 2026-08-22 — Blocky live; Tailscale up + advertising 10.0.2.0/24; wildcard `*.puhome.net` cert issued (auto-renew timer active); nginx native serving TLS (default-deny, vhosts added per-service in phases 4–5). **Manual follow-ups:** approve subnet route + disable key expiry for gw01 in Tailscale admin console; add Pi-hole conditional-forward on dns01. |
| 3 | **Storage**: NFS export managed via TrueNAS API (`services/storage/truenas_nfs`, restricted to cmp01); `system/nfs_mounts` mounts it at /mnt/media/data on cmp01 | 🟢 done 2026-08-22 — mount live (rw, fstab), uid/gid aligned to existing owner 1001 (not 1500); export idempotent + drift-corrected |
| 4 | `util01`: Authentik (SSO), Beszel hub (+agents fleet-wide), ntfy, Diun, Uptime Kuma, Glance, Portainer, Dozzle, n8n | 🟢 done 2026-08-22 — all 9 live (each own compose project). **SSO enforced**: domain-level forward-auth via `authentik_config` role (idempotent, covers all *.puhome.net); admin UIs redirect to Authentik login. |
| 5a | `cmp01`: arr stack (nzbget primary + qbit behind VPN + *arr) | 🟢 done 2026-08-22 — gluetun PIA **OpenVPN** (WireGuard unsupported for PIA), port-fwd active; nzbget (Usenet, primary) + *arr on bridge; VPN-scoping proven (arr apps stayed up while gluetun down) |
| 5b | `cmp01`: Jellyfin + Jellyseerr (NVENC), Kavita | 🟢 done 2026-08-22 — Jellyfin+Jellyseerr live, A4000/NVENC verified in-container. Kavita **parked** (upstream first-boot migration bug). Added `system/nvidia_docker`. Fixed a fleet-wide issue: Blocky now filters AAAA (IPv4-only net) — containers no longer hang on IPv6. |
| 5c | `cmp01`: Paperless-ngx | 🟢 done 2026-08-22 — migrated existing DB+docs (182) from TrueNAS to local disk; postgres:17 glibc; collation refreshed |
| 6 | **Maintenance**: `ops/restic` → nas02 (SFTP) — scoped to **Paperless + Syncthing**; `--tags patch` play; Uptime Kuma verification | 🟢 done 2026-08-22 — backups **live**: repos init'd, nightly timers armed, first snapshots verified (Paperless 1.45 GiB, Syncthing). Repo path is `/restic/<host>` (Synology SFTP chroot). `uptime_kuma_config` role added: HTTP monitor per service (all green) + **Backups** push monitor restic pings. Also this pass: **Syncthing** (util01), **Filebrowser** (cmp01, media mgmt), **Portainer/Dozzle agents** (fleet-wide), **Beszel** agents on macOS+nas01. |
| 7 | **GitOps**: Gitea Actions CI + on-LAN executor (see [gitops](gitops.md)) | 🟢 done 2026-08-23 — CI live (builds.sr.ht runs on push). Executor on util01: full-fleet `--check` dry-run **verified clean (0 failures across all 7 hosts)**; poll timer **enabled in dry-run mode** (`gitops_auto_apply` off — flip to enable auto-apply). Hardened ~6 roles to be check-mode-safe (read-only API/`networksetup` lookups run under `--check`). Executor secrets on util01: vault-pass + `ansible_rsa.private` + `restic_backup_ed25519`. **Superseded 2026-09-07:** sr.ht/builds.sr.ht and the poll trigger were replaced by Gitea + Gitea Actions; `gitops_auto_apply` is now on and the human gate is the PR merge. First real PR-driven deploys landed 2026-09-08 (PRs #1–#5). |
| 8 | **8a** `cmp01`: paperless-ai + llama.cpp (Gemma 3 4B) · **8b** `ai01`: resident LLM · **8c** `ctl01`: Colima + dev toolchain | 🟡 in progress — **8a done 2026-08-31**. **8b done 2026-09-04**: `omlx` (Apple MLX) serving **Qwen3-30B-A3B 4-bit**, not Ollama and not llama.cpp. Built, deployed and **verified serving** — auth enforced (401 without key), bound to `10.0.2.9:8000` + loopback only, survives reboot. The memory guard needed an **explicit** ceiling (`--memory-guard-gb 21`): both named tiers sized below the 16.80 GB of weights, so `safe` never loaded the model and `balanced` loaded it but rejected every request. `system/brew` was built alongside to make Homebrew usable by the `ansible` user (closes a standing HANDOFF item). **8c dropped 2026-09-04 (user decision)**: the dev toolchain (`dev/go`, `dev/node`, `dev/kubectl`, `dev/helm`, `dev/kind`, `dev/awscli`) is not wanted and will not be built. Those six role entries are commented out of `playbooks/hosts/ctl01.yml`, which had been failing at parse time because they never existed. **`system/colima` survives 8c** — it was already pulled forward as a Phase 10 prerequisite (researchd needs a container runtime on ctl01) and is built there, not here. The Hermes-vs-OpenClaw question is **closed — neither**, see [`../spec/research.md`](../spec/research.md#why-not-an-agent-framework) |
| 9 | **Knowledge base** — one merged home wiki over all three Obsidian vaults. **9a** mirror via `obsidian-headless` (mirror-remote) + per-vault Quartz render · **9b** merge into a single topic-organised tree (vaults dissolved, collisions resolved, links rewritten) · **9c** hybrid index (sqlite-vec + FTS5) powering search *and* cross-vault related links · **9d** local-LLM enrichment (summaries, links, tags) | 🟡 in progress — **9a done + live** (`wiki.puhome.net`, hourly `homelab-vault-ingest`). 9a rendered three separate sites; **9b supersedes that with one merged wiki** — see [`../spec/knowledge.md`](../spec/knowledge.md). **9c** (index → search + cross-vault related links) and **9d** (LLM summaries on cmp01's A4000) both have **no ai01 dependency** — corrected 2026-09-01, enrichment was wrongly placed on ai01 |
| 10 | **Research agent** — a topic/link goes in, a cited body of notes lands in the wiki under `Agent-Research/`. `services/ai/omlx` (ai01, the model) · `system/colima` + `services/ai/researchd` (ctl01, the pipeline, with SearXNG inside its compose project) · `services/knowledge/researchpull` + a fourth `vaultmerge` source (cmp01) · n8n `homelab-agent-research` | 🟡 in progress — spec written 2026-09-03, [`../spec/research.md`](../spec/research.md). **8b is done** (the model is live). `system/colima` is built inside this phase, 8c having been dropped. Wiki wiring (fourth `vaultmerge` source + `research.puhome.net` + dashboard tile + restic scope) landed 2026-09-04; `researchd` itself in build. First thing in the fleet that *writes* knowledge and reads the open internet, so the containment model is the design, not an afterthought |

## Post-Phase-7 enhancements (2026-08-29, tag v3.0.0)

Within phases 5–6, layered on after GitOps:
- **Music stack** (cmp01): Navidrome + LMS/Lyrion (WiiM) + slskd/Soularr (Soulseek);
  Mixarr dropped. NZB prioritised over torrents (arr delay profiles).
- **Dashboard**: Homarr trialled then reverted (not config-driven); Homepage reworked
  into 2 tabs (Home/System) with a links block + two-pane widgets block, restyled.
- **Dual-WAN watch** (`ops/wanwatch`, gw01): per-ISP probing for the load-shared
  Omada edge — alias IPs pinned to each WAN by Omada policy routing, run by n8n,
  verdict held by Uptime Kuma push monitors, tile on Homepage. Needs two manual
  Omada rules before it is live — see [`../runbooks/dual-wan-monitoring.md`](../runbooks/dual-wan-monitoring.md).

Full detail + open items in [`../HANDOFF.md`](../HANDOFF.md).

## Why this order

1. **Prerequisites first** — nothing runs against a host we can't authenticate to.
2. **Bootstrap (1)** — every later phase assumes the `ansible` user, docker, and base packages.
3. **Gateway (2) before services** — DNS + reverse proxy + wildcard cert are what make every
   `<name>.puhome.net` resolve and get TLS. Standing up cmp01/util01 first means testing without
   working domains.
4. **Storage (3) before compute** — the arr stack and Jellyfin can't be deployed correctly
   until the single `/mnt/media` NFS mount and the pinned `media` GID exist. This is a distinct
   phase precisely because getting it wrong (SQLite on NFS, split mounts, UID drift) is the
   top failure mode — see [storage](../spec/storage.md).
5. **Observability + SSO (4) before the heavy stack** — Beszel/Uptime Kuma/ntfy/Diun in place
   before cmp01 carries the bulk of services, so we're not flying blind through the biggest
   phase. **Authentik deploys first within this phase**: it's the single sign-on every later
   service sits behind, so it (plus the gw01 forward-auth snippet from phase 2) must exist
   before the apps in phase 5 — see [auth](../spec/auth.md).
6. **Compute (5a/b/c)** — split so arr (with its VPN scoping) lands and stabilises before
   Jellyfin/Kavita (share the phase-3 mount) and Paperless (independent).
7. **Maintenance (6) after there's something worth maintaining** — patching, updates, and
   especially backups only matter once real services and data exist.
8. **GitOps (7)** — automate deploys once the manual `ansible-playbook` path is proven.
9. **AI + dev (8)** — personal-use machines; nothing else depends on them.
10. **Knowledge base (9) last** — it is the first thing that *consumes* the AI host rather
    than just standing it up, so it wants 8b settled. Only the enrichment stage (9d) needs a
    model: the mirror, the merge, and the whole retrieval layer are deliberately built to
    work without one, so 9a–9c need not wait.
11. **Research agent (10) after the wiki exists** — it publishes *into* the merged wiki, so
    the merge, index and Quartz build must already work. It is also the only phase that
    grants a service outbound internet access and feeds untrusted text to a model, so it
    deliberately goes last, once every other moving part is stable and observable.

## Bringing a phase online

Implement roles per [`../spec/conventions.md`](../spec/conventions.md) (write fresh; read
`archive/` only for insight), then uncomment that phase's `import_playbook` line in `site.yml`
and flip its status here.

## Onboarding a new host later

The pipeline is designed so adding a machine is three manual steps + one idempotent playbook:

1. On the new host, create a `uknth` admin user, add the **unified `uknth` public key** (the
   `id_rsa` key, see [conventions](../spec/conventions.md#unified-uknth-login)), and grant it
   passwordless sudo.
2. Add the host to `hosts/hosts.yml` in the right group.
3. Run `ansible-playbook init.yml -l <newhost>` — it creates the `ansible` service account and
   installs its key. `init.yml` is **idempotent**: on hosts already done, every task skips, so
   it's safe to run against the whole fleet anytime.
4. Run `ansible-playbook site.yml -l <newhost>` for the rest.

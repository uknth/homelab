# Session Handoff — Context Dump (2026-08-29)

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
| ctl01 | 10.0.2.115 (DHCP; reserve 10.0.2.4) | Mac Mini M1 — bootstrapped (ansible user + Beszel agent live); dev toolchain is **Phase 8** |
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

## Phase 8 (next)
- **ai01**: Ollama (native) + a Qwen model + **paperless-ai** (auto-tag/OCR-assist Paperless
  docs). Same LLM can back the **Homepage LLM search** (replace the placeholder search box).
- **ctl01**: dev toolchain. Prereqs: reserve **10.0.2.4** in Omada, add ctl01 to `hosts/hosts.yml`,
  run `init.yml -l ctl01` then `site.yml -l ctl01`.
- Build per [`spec/conventions.md`](spec/conventions.md); wire into `site.yml`; update the roadmap.

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

### Open from this session
- **gw01 container DNS.** gw01's resolv.conf is loopback (it *is* the DNS host),
  so Docker fell back to public DNS and containers got IPv6-only answers on an
  IPv4-only net — Diun on gw01 can't reach any registry. `/etc/docker/daemon.json`
  now pins container DNS to Blocky, but **a `systemctl restart docker` on gw01 is
  still pending** — deliberately not automated, since it restarts Blocky and
  briefly drops LAN DNS.
- **Orphaned containers**: `mixarr` (cmp01, unhealthy) and `homarr` (util01) are
  still running though both were retired; ~37 GB reclaimable on cmp01.

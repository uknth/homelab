# Music stack — Lidarr + Soulseek (slskd/Soularr) + Navidrome

Discovery is manual (curate directly in Lidarr — Mixarr was dropped 2026-08-23;
its recommendations weren't worth the extra service). The flow:

```
Lidarr (add artists/albums → wanted list → imports to /data/music)
  → Soularr → slskd (Soulseek)         [PRIMARY — best music coverage]
  → Prowlarr indexers → nzbget/qbit    [FALLBACK — Usenet/torrent]
     in-progress on scratch-pool, complete on data-pool (hardlinks)
Navidrome serves /data/music (read-only) to every device (Subsonic API).
```

All run on cmp01 (arr docker network). Web UIs:
`soulseek.puhome.net` (slskd, SSO), `soularr.puhome.net` (SSO),
`music.puhome.net` (Navidrome — **native auth, not SSO**, so mobile clients work).

## Playback — Navidrome (`music.puhome.net`)

Purpose-built music server speaking the **Subsonic API**, so you pick any client
per device. Reads `/data/music` read-only; Lidarr curates, Navidrome serves.

**First-run (one manual step — Navidrome has no seed-admin env/API):** open
`music.puhome.net` and create the admin user `uknth` with
`vault_navidrome_password`. That single account then works in every client.

**Clients:** Android — Symfonium (best), Tempo, DSub, Substreamer; iOS —
play:Sub, Amperfy, substreamer; Desktop — Feishin, Supersonic; plus the built-in
web UI. Point each at `https://music.puhome.net`, user `uknth`, the vault password.

- `ND_ENCRYPTIONKEY` (vault) keeps stored passwords stable across restores.
- Library rescans hourly (`ND_SCANSCHEDULE`); new Lidarr imports appear automatically.
- Navidrome's own SQLite DB lives on local disk at `/opt/homelab/navidrome/data`.

## Download — Soulseek (slskd + Soularr)

- **slskd** connects to Soulseek as `vault_slskd_username` (account
  `puhome-music-9c11b4`, generated + stored in vault; registered on first connect).
- **Soularr** runs every ~5 min: reads Lidarr's wanted list, searches slskd,
  downloads to `/scratch/soulseek` → `/data/downloads/soulseek`, imports to `/data/music`.
- slskd shares `/data/music` back (Soulseek etiquette).
- To (re)deploy: `ansible-playbook playbooks/hosts/cmp01.yml --tags slskd,soularr`
- Tuning: `roles/services/media/soularr/templates/config.ini.j2` —
  `minimum_filename_match_ratio` (raise toward 0.8 if numeric album titles like
  "1989" pull in false year-matched folders).

## Notes
- Only fetch music you're entitled to; slskd/indexers are just transports.
- slskd/soularr/navidrome config is codified (keys read at deploy / vault).

## Play on WiiM — LMS (Lyrion Music Server)

WiiM has no Subsonic support, so Navidrome can't stream to it directly. `services/media/lms`
runs Lyrion Music Server on cmp01 (`host` networking) pointed read-only at the same
`/data/music`; the WiiM's built-in Squeezelite connects and streams directly — the phone is
only a remote (and with LMS presets, not needed at all).

- Ports (on cmp01 `10.0.2.5`): **9000** web+stream, **3483** tcp/udp slimproto (the WiiM),
  **9091** CLI (moved off 9090 — Cockpit owns it, via `EXTRA_ARGS=--cliport`).
- Library folder defaults to `/music`; LMS auto-scans on first boot (hourly thereafter).
- Admin UI: `lms.puhome.net` (SSO) — the WiiM itself talks to `10.0.2.5:9000/3483` directly,
  not through nginx (it can't do Authentik).

**WiiM setup:** WiiM Home app → your device → Music Sources / Services → **Logitech/Lyrion
Media Server**. If the WiiM shares cmp01's subnet it auto-discovers; if it's on another
VLAN/subnet, add the server manually by IP **`10.0.2.5`** (port 9000). Then browse the library
and play — audio streams cmp01 → WiiM. Set WiiM **presets** to start playback with no phone.

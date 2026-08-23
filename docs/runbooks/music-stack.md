# Music stack — Lidarr + Soulseek (slskd/Soularr) + Mixarr

Strategy (mirrors the Jellyseerr→Radarr→nzbget flow, adapted for music):

```
Mixarr (discovery: Spotify/Last.fm/ListenBrainz/AI → review queue)
  → Lidarr (wanted list, imports to /data/music, updates Jellyfin)
     → Soularr → slskd (Soulseek)         [PRIMARY — best music coverage]
     → Prowlarr indexers → nzbget/qbit    [FALLBACK — Usenet/torrent]
        in-progress on scratch-pool, complete on data-pool (hardlinks)
```

All three run on cmp01 (arr docker network). Web UIs (Authentik-gated):
`soulseek.puhome.net` (slskd), `soularr.puhome.net`, `mixarr.puhome.net`.

## Activation (manual — needs your accounts)

1. **Soulseek account** (free): register a username/password using a Soulseek
   client (Nicotine+ / SoulseekQt) or soulseek.org. Then add to the vault:
   ```
   ! ansible-vault edit hosts/group_vars/all/vault.yml
   #   vault_slskd_username: "..."
   #   vault_slskd_password: "..."
   #   vault_slskd_api_key: "<random>"          # optional; used by Soularr
   #   vault_mixarr_session_secret: "<random>"  # optional; else default is used
   ```
   Then: `ansible-playbook playbooks/hosts/cmp01.yml --tags slskd,soularr,mixarr`
   slskd will connect to Soulseek; Soularr auto-runs every 5 min against Lidarr's
   wanted list.

2. **Mixarr** (`mixarr.puhome.net`): Settings → Connections → add Lidarr
   (URL `http://lidarr:8686` or `http://cmp01.host.puhome.net:8686`, key
   `d073edb606dd45008bfb63a4441f2017`). Then add source connectors (Spotify /
   Last.fm / ListenBrainz — your accounts/API keys) + optionally point AI recs at
   the ai01 Ollama (once Phase 8 is up). Approve artists → they flow to Lidarr.

## Notes
- slskd shares `/data/music` back (Soulseek etiquette).
- Only fetch music you're entitled to; slskd/indexers are just transports.
- Config for slskd/soularr is codified (keys read at deploy / vault); mixarr's
  Lidarr connection + source connectors are set in its UI (in the /data volume,
  which is NOT yet in the restic set — consider adding /opt/homelab/mixarr).

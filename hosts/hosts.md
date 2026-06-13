# Hosts

## Nodes

Role-based names of the Nodes

| Hostname | Purpose         |
| -------- | --------------- |
| nas01    | TrueNAS         |
| nas02    | Synology NAS    |
| gw01     | Gateway         |
| ha01     | HomeAssistant   |
| util01   | Utility node    |
| gpu01    | GPU workloads   |
| ai01     | Mac Mini M4 Pro |
| admin01  | Mac Mini M1     |
| c01      | Compute node    |
| dns01    | DNS node        |


gw01,ha01 -> RP5, running debian
c01 -> custom pc, running debian
nas01 -> custom pc, running Truenas with 24tb in RAIDZ2
nas02 -> synology nas device
util01 -> mini-pc (skullsaint), debian
dns01 -> RPZero, pi-hole

## Node to Process mapping

Compute Node c01 (debian)
---

1. arr/downloader stack (sonarr, radarr, etc.)
2. jellyfin/media stack (jellyfin, jellyseer, navidrome etc.)
3. immich stack 
4. Paperless-ngx
5. Kavita stack

TrueNAS nas01 (truenas)
---
1. Dedicated Storage Layer for ARR, JellyFin, Immich, Kavita & Paperless-ngx

BackupNAS nas02 (synology)
---
1. Dedicated Backup machine 

Gateway gw01 (debian)
---
1. Blocky or equivalent DNS Server
2. Network Gateway 

Utility util01 (debian)
---
1. GUI (dash.puhome.net)
2. Beszel (metrics.puhome.net)
3. n8n (n8n.puhome.net)
4. portainer (docker.puhome.net)
5. Uptime-Kuma (synthetics.puhome.net)


Admin admin01 (macos)
--- 
1. Jumphost
2. Tailscale
3. Local Development


AI ai01 (macos)
---
1. Hermes Agent
2. Local LLMs using Olama or equivalent

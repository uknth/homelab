# Beszel agents on the NAS appliances (nas01 / nas02)

`nas01` (TrueNAS SCALE) and `nas02` (Synology) are **unmanaged** — Ansible doesn't
provision them — so their Beszel agents are deployed by hand. They're already
registered as systems in the hub (`beszel_register_hosts` in `beszel_hub`), so once
each agent runs, its tile goes from **down → up** automatically. No per-agent secret:
the agent just needs the hub's public **KEY** and to listen on **45876**.

**Hub KEY** (same for every agent — it's `vault_beszel_agent_key`):
```
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIMM6KcGi+CFcOs8WeWgb7soRbi/NfvDms9KMe00ijG4p
```

## nas01 — TrueNAS SCALE (25.10, Docker-based)
Apps → **Discover Apps → Custom App** (install by YAML). Persists across reboots/updates
(a raw `docker run` on TrueNAS can be pruned by the middleware):
```yaml
services:
  beszel-agent:
    image: henrygd/beszel-agent:latest
    container_name: beszel-agent
    restart: unless-stopped
    network_mode: host                 # listen on the host's :45876
    environment:
      PORT: "45876"
      KEY: "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIMM6KcGi+CFcOs8WeWgb7soRbi/NfvDms9KMe00ijG4p"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro   # also report container stats
```

## nas02 — Synology (Container Manager / SSH)
SSH in and run (or recreate the equivalent in Container Manager with host networking):
```bash
sudo docker run -d --name beszel-agent --restart unless-stopped \
  --network host \
  -e PORT=45876 \
  -e KEY="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIMM6KcGi+CFcOs8WeWgb7soRbi/NfvDms9KMe00ijG4p" \
  -v /var/run/docker.sock:/var/run/docker.sock:ro \
  henrygd/beszel-agent:latest
```

## If the tile stays `down`
The hub (`util01`, 10.0.2.8) connects **to** the agent at `<nas-ip>:45876`. If it can't:
- **Synology:** DSM → Security → Firewall may block 45876 — allow it from 10.0.2.0/24.
- **TrueNAS:** confirm the app is on host networking and listening (`ss -tlnp | grep 45876`).
- Verify from util01: `nc -zv <nas-ip> 45876`.

## Extra: monitor the actual ZFS pool disks (nas01)
By default the agent reports the container/root fs. To surface the pool, add
`EXTRA_FILESYSTEMS` (comma-separated mount points or device names) to the agent env —
handy for keeping an eye on the RAIDZ2 pool while the ASMedia→LSI HBA swap is pending.

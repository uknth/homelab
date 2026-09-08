# Knowledge base — Obsidian vaults → wiki (phase 9a)

Three Obsidian vaults (`Pikachu` work, `Snorlax` knowledge base, `Psyduck`
personal/homelab) mirrored read-only onto cmp01 and published as a static
Quartz wiki. Design and rationale: [`../spec/knowledge.md`](../spec/knowledge.md).

```
Obsidian Sync (authored on the Macs — the only writer)
  → obsidian-headless, mode=mirror-remote   [/opt/homelab/vaultsync/vaults]
  → vaultmerge: 3 vaults -> ONE topic tree  [/opt/homelab/vaultmerge/merged]
  → vaultindex: embeddings + LLM summaries  [/opt/homelab/vaultindex/data]
  → Quartz build → nginx                    [wiki.puhome.net]
     driven hourly by n8n `homelab-vault-ingest`

  vaultask (always up, reads the two outputs)  [search.puhome.net]
     FTS5/BM25 + cosine, fused with RRF — search, not answers
```

The wiki is **one merged site**, not three vault sites. Topics — Homelab, Work,
Career, Engineering, Sources, Finances, Credentials, Inbox — come from a
declarative mapping in `vaultmerge_path_topics` / `vaultmerge_tag_topics`, so
reshaping the information architecture is a variable change plus a rebuild.

Nothing here writes back to a vault, by three independent mechanisms: Sync runs
in `mirror-remote` mode (local changes are reverted, not merged), the Quartz
builder mounts the mirror `:ro`, and the wiki is generated HTML with no editor.

## First run — the one manual step

`ob login` is interactive (Obsidian account + the E2E encryption password) and
cannot be automated. Everything else is idempotent Ansible.

The vaults are **end-to-end encrypted**, and **each vault has its own password**.
`ob sync-setup` prompts for it interactively, but the ingest script runs without
a TTY, so the passwords come from the Ansible vault — one variable per vault:

```sh
ansible-vault edit hosts/group_vars/all/vault.yml
#   vault_obsidian_e2e_pikachu: "<Pikachu E2E password>"
#   vault_obsidian_e2e_snorlax: "<Snorlax E2E password>"
#   vault_obsidian_e2e_psyduck: "<Psyduck E2E password>"
```

There is deliberately **no shared default**: a global fallback could only ever
resolve to the wrong key for two of the three vaults, and would fail at decrypt
time rather than at configuration time.

Any password you leave unset simply means that vault is linked by hand instead —
`--setup` prints the exact `docker exec -it ... ob sync-setup` command for it and
carries on with the others. Mixing the two approaches is fine. Either way the
password is needed only on the first link.

```sh
ansible-playbook site.yml --tags vaultsync,quartz -l cmp01

# on cmp01, once:
docker exec -it vaultsync ob login          # Sync credentials + E2E password
docker exec vaultsync ob sync-list-remote   # confirm the three vault names

# The ingest script is ansible:ansible 0750 — n8n drives it over SSH as that
# user through the forced-command key, exactly like ops/maintenance. A human
# runs it with sudo -u; running it as yourself is a permission denied.
sudo -u ansible /opt/homelab/vaultsync/vault-ingest.sh --setup
sudo -u ansible /opt/homelab/vaultsync/vault-ingest.sh --all
```

`--setup` links each vault and applies the sync policy (`mirror-remote`,
`--file-types image,pdf`, `--configs ''`). It is safe to re-run: already-linked
vaults skip the link and only re-assert their config.

The account also holds an **`Archive`** vault, deliberately not synced — add it
to `vaultsync_vaults` (and `quartz_sites`) if you want it published.

If `ob sync-list-remote` shows different vault names than `Pikachu` / `Snorlax` /
`Psyduck`, correct `vaultsync_vaults[].remote` in the role defaults — `name` is
the local directory and the wiki path, `remote` is what Sync calls it.

## Day to day

n8n's **`homelab-vault-ingest`** runs hourly: `ob sync` per vault, then reindex
and Quartz rebuild **only if the mirror actually changed**. A quiet hour is one
SSH round-trip and no build. Failures post to the `homelab-jobs` ntfy topic.

Both scripts are owned by `ansible` (mode 0750), so run them with `sudo -u`:

```sh
sudo -u ansible /opt/homelab/vaultmerge/merge-vaults.sh           # re-merge only
sudo -u ansible /opt/homelab/vaultsync/vault-ingest.sh --sync     # pull only
sudo -u ansible /opt/homelab/vaultsync/vault-ingest.sh --all      # pull + rebuild if changed
sudo -u ansible /opt/homelab/vaultsync/vault-ingest.sh --status   # last run, as JSON
sudo -u ansible /opt/homelab/quartz/quartz-build.sh               # rebuild the wiki alone
```

`GET https://n8n.puhome.net/webhook/vault-status` replays the last run for the
dashboard, with `stale: true` once it is older than three hours.

## Things worth knowing

**Change detection does not parse `ob sync` output.** obsidian-headless is
0.0.x and its output is not a contract, so the script fingerprints the mirror
(mtime + size + path over every `.md`) before and after. That cannot drift from
what the indexer and Quartz actually see.

**The version is pinned and nothing will tell you when it moves.** Diun watches
container registries, not npm. Bump `vaultsync_headless_version` deliberately;
the role reinstalls only when the pin and the installed version differ.

**The first deploy takes ~12 minutes at the plugin-install step, and looks
hung.** `quartz plugin install` does a `git clone` + `npm install` + esbuild
build for *each* of 42 plugins — roughly 4.6 GB of disk I/O at ~200% CPU, with
no progress output. It is not stuck. To watch it:

```sh
cd /opt/homelab/quartz/src/.quartz/plugins
for d in */; do [ -f "$d/dist/index.js" ] && echo x; done | wc -l   # of 42
```

This is one-time: the `.deps-<version>` marker means a converged host skips it
entirely, and only a `quartz_version` bump pays the cost again.

**Quartz is a git checkout, not an npm package.** It is pinned to
`quartz_version` and treated as immutable — `npm ci` and `quartz plugin install`
write into it, so it is never `git pull`ed. Changing the pin discards and
re-clones, which is the only way plugin state stays consistent with the tag.

**Per-vault configs are generated from Quartz's own defaults.** Only
`pageTitle`, `baseUrl`, `ignorePatterns`, `analytics` and `defaultDateType` are
overridden; the 42-plugin list (emitters, explorer, graph, search, backlinks)
comes verbatim from the pinned tag rather than being hand-maintained. Quartz v5
reads `quartz.config.yaml` from the working directory and has no `--config`
flag, hence the swap-before-build in `quartz-build.sh`.

**Excalidraw notes are excluded, and must stay excluded.** `*.excalidraw.md` is
a JSON blob in a markdown wrapper; Quartz's parser dies on it with
`chunks[startIndex].slice is not a function`, and because one bad file aborts
the *whole* vault build, a single drawing takes the entire site down. The
`quartz_ignore_patterns` entry is a generic glob so new drawings are covered
automatically. Quartz cannot render them either way.

**Enrichment runs on cmp01, not ai01.** Summarising a note is short-context,
high-volume work — what the A4000 is for. Two llama.cpp servers sit on an
`internal: true` network with no published ports (same containment as the
Paperless tagging model): `bge-small` for embeddings (~36 MiB) and **Gemma 3 12B
Q4_K_M** (~6.8 GiB) for summaries, in the ~12.7 GiB free beside
`paperless-llama`. Nothing here depends on ai01 or on Phase 8b.

**Generated text goes around the note, never into it.** A `> [!abstract]`
callout above the body, a `## Related` section below, source text verbatim in
between — both visibly marked as generated. This is where someone looks up a
credential or a financial figure, so a confident hallucination is worse than no
wiki. The summary prompt is constrained to the note's own content and returns
`SKIP` rather than inventing one for a fragmentary note.

**Everything is cached by content-hash.** A note whose text has not changed is
never re-embedded and never re-summarised, so an hourly run costs nothing until
something is edited. A model error is deliberately *not* cached, so it retries
next pass instead of poisoning the cache with a null.

**Related links are brute-force cosine, not a vector database.** ~450 notes is
~100k comparisons — microseconds. `sqlite-vec` earns its place when the search
UI arrives; it would be pure overhead for a "show me 5 related notes" ranking.

**The merge is a full rebuild, every time.** It empties the output and re-derives
it, so notes deleted upstream disappear here too. It clears the *contents* of
`/merged` rather than the directory: that path is a bind mount, and `rmdir` on a
mount point fails with `EACCES`.

**Collisions are resolved symmetrically.** 35 notes share a filename across
vaults. When names collide, *every* member of the set gets a source segment
(`Career/work/Interview`, `Career/personal/Interview`) — not just the losers — so
the output never depends on processing order.

**Links resolve within their source vault first.** `[[Interview]]` in a Pikachu
note means Pikachu's Interview, so merging never silently re-points an existing
link at another vault's note. Links inside fenced code blocks are left alone —
a shell snippet containing `[[ -z "$url" ]]` is a test expression, not a link.
Unresolved links are written to `/merged/.dangling-links.json`; most were already
dangling in Obsidian (people named in meeting notes, deleted notes, template
placeholders), plus links into the excluded `Utils/`.

**Provenance is frontmatter, not folders.** Each merged note carries
`merged_from:` and `merged_topic:`. The keys are namespaced because notes already
use `source:` for their own citation metadata, and a duplicate YAML key fails the
entire build.

**nginx mounts `public/`, not `public/site/`.** Each build swaps the site
directory with `mv`, replacing its inode; a bind mount directly on `public/site`
would stay pinned to the old deleted one and serve nothing.

**The build runs from a staging copy, not the mirror.** `quartz-build.sh` does
`cp -a` from `/vaults/<v>` into `/content/<v>` and builds from there, because
two things have to be done to the content that the read-only mirror forbids
(and that Sync would revert anyway). `prepare-content.mjs` does both:

- **Strips Dataview blocks.** Quartz cannot execute Obsidian's Dataview plugin,
  so ```` ```dataviewjs ```` fences render as a wall of raw JavaScript. The
  vaults contain ~160 of them across 90 files, mostly folder-note MOCs — whose
  job Quartz already does natively with folder pages, the explorer, and
  backlinks. Headings left empty by the strip are dropped too, so a MOC does not
  render as a row of bare headings. Disable with `quartz_strip_dataview: false`.
- **Writes a root `index.md` when the vault has none.** Quartz builds the site
  root from `content/index.md`; without it the vault root has no page at all.
  Generating markdown (rather than post-hoc HTML) means the root is a real
  Quartz page with explorer, search and graph, not a bare listing.

The mirror is never modified — verified by checking the Dataview blocks are
still present in `/vaults` after a build.

**The build calls `bootstrap-cli.mjs` directly, not `npx quartz`.** npx
link-installs the package into an `_npx` cache under `HOME`, and that cache
symlinks back to the checkout — pointless work plus a path loop that any
recursive walk falls into. `HOME` is also deliberately outside `/src` for the
same reason.

**Builds are staged and swapped.** A failed build leaves the currently-served
wiki intact instead of half-replacing it. The swap happens inside the container
because `/public` is owned by the container uid.

**`Secrets/` and `Finances/` are published.** Deliberate, per the 2026-09-01
decision: every surface is LAN-only and Authentik-gated. This means plaintext
copies of credentials exist outside the E2E-encrypted vault, in the generated
HTML (and later in the search index). To change it, set `excluded_folders` on
the Psyduck entry in `vaultsync_vaults` and re-run `--setup` — the exclusion
applies at the transport layer, so the content stops reaching cmp01 at all.
Already-synced files are **not** removed retroactively; delete the mirror
directory and re-sync.

**Do not run the Obsidian desktop app on cmp01.** Upstream warns that desktop
Sync and headless Sync on the same device cause data conflicts. Different
devices (the Macs) are fine — that is the whole design.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `permission denied` running a script | It is `ansible:ansible` 0750 by design. Use `sudo -u ansible <script>`. |
| `Password not provided.` on `--setup` | That vault is E2E encrypted and its `vault_obsidian_e2e_<name>` is unset. Set it, or link by hand — `--setup` prints the command. |
| `--setup` says the vault is not found | Name mismatch. Check `ob sync-list-remote` against `vaultsync_vaults[].remote`. |
| Ingest fails right after a reboot | The container starts before the login token is readable, or `ob login` expired. Re-run `docker exec -it vaultsync ob login`. |
| Wiki serves a stale vault | The build only runs when the fingerprint moved. Force with `/opt/homelab/quartz/quartz-build.sh`. |
| `quartz plugin install` fails | It shells out to `git clone`; the builder must be `node:22` (full), not `-slim`, which ships no git. |
| n8n job fails with exit 64 or no output | n8n's SSH node prepends `cd <dir> ;` to the command. The script strips it — same fix as `ops/maintenance` and `ops/wanwatch`. |
| Wiki build fails on one vault, others fine | Almost certainly an unsupported file type; check the named file in the build output. Add a glob to `quartz_ignore_patterns`. |
| A vault root returns 403 | No root `index.md` was generated — check the build output for `prepared /content/<v>: ... wrote index.md`. |
| No summaries on any page | `vaultindex` not deployed, or the sidecar is missing. Check `/opt/homelab/vaultindex/data/enrichment.json` and run `sudo -u ansible /opt/homelab/vaultindex/reindex.sh`. |
| Summaries stop updating | The content-hash cache at `/opt/homelab/vaultindex/data/cache.json`. Delete it to force a full re-summarise (~30-60 min). |
| GPU out of memory | Gemma 3 12B (~6.8 GiB) plus `paperless-llama` (~3.3 GiB) on a 16 GiB A4000. Check with `nvidia-smi`; drop to the 4B by pointing `vaultindex_llm_model_file` at it. |
| Raw JavaScript on a wiki page | A Dataview fence the stripper missed. It matches ```` ```dataview ```` and ```` ```dataviewjs ````; anything else needs a pattern in `prepare-content.mjs`. |
| Wiki 404s on a page that exists | Quartz emits `<page>.html`; nginx needs `try_files $uri $uri/ $uri.html`. Check `/opt/homelab/quartz/nginx.conf`. |

## Search — `search.puhome.net`

> Renamed from `ask.puhome.net` on 2026-09-08. That hostname now serves a **different**
> service (Open WebUI general chat) which holds no wiki data — see
> [`../spec/services.md`](../spec/services.md). If a bookmark or script still points at
> `ask.puhome.net` expecting wiki search, it is pointing at the wrong service.

Hybrid retrieval over the merged tree. **No LLM**: it returns notes, not
answers (user decision 2026-09-02 — ship search, judge generation after using
it). The summary model is never started by a search.

- **FTS5/BM25** catches proper nouns, code identifiers and rare terms — most of
  what technical notes are made of, and exactly where vector search is weakest.
- **Embeddings** catch meaning, so a query need not use the note's wording. The
  query is embedded by the always-on `vaultindex-embed` (~36 MiB).
- **RRF** fuses the two rankings without needing their scores to be comparable,
  which they are not (BM25 is negative-lower-better, cosine is 0..1).

No `sqlite-vec` and no native modules: Node 22 ships `node:sqlite` with FTS5
built in, and ~440 vectors is a brute-force cosine in microseconds. The service
owns no state — it mounts the merge and enrichment outputs read-only and
rebuilds its in-memory index when they change, so it needs no restart after an
ingest.

If the embedding server is unreachable, search degrades to keyword-only and
says so in the UI rather than returning nothing.

**On the dashboard:** the header search box on `dash.puhome.net` (the Phase-8
placeholder) now queries the wiki instead of DuckDuckGo — `provider: custom`
pointing at `https://search.puhome.net/?q=`.

## Not yet built

**Q&A over the search results.** The retrieval layer is in place and the
summary model already exists on cmp01, so this is wiring rather than new
infrastructure. The open question is the model lifecycle: the 12B takes ~40s to
load on demand, which is poor for interactive use, so it wants either an
idle-timeout keep-alive or the always-on 4B.

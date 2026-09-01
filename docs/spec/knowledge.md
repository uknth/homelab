# Knowledge Base — Obsidian vaults, wiki, and search

Three Obsidian vaults are the user's primary written record. This spec makes them
**readable and searchable from anywhere on the LAN** without changing how they are
written: Obsidian on the Mac stays the only writer, and everything here is a
derived, disposable read model.

| Vault | Contents | Notes | Words |
|---|---|---|---|
| `Pikachu` | Work notes | 196 | ~122k |
| `Snorlax` | Knowledge base — material collected from the internet | 163 | ~193k |
| `Psyduck` | Personal notes, homelab, personal projects | 87 | ~44k |

**~446 notes / ~359k words total.** That number drives most of the decisions below.

## The target: ONE wiki, not three

**Corrected 2026-09-01.** The first pass built three separate Quartz sites, one
per vault, on the reasoning that wikilinks do not resolve across vault
boundaries. That reasoning was backwards: the boundaries are the problem the
wiki exists to remove. The intent is a **single home wiki** that merges all
three vaults into one body of knowledge — the one place to look up how the
homelab works, what the finances are, or anything from work.

Four capabilities, in dependency order:

1. **Mirror** — pull the vaults read-only. *(9a, done)*
2. **Merge** — dissolve the vaults into one topic-organised tree, resolving the
   name collisions and rewriting links so they still resolve. *(9b)*
3. **Index** — hybrid search over the merged corpus, which doubles as the engine
   for cross-vault "related" links that Obsidian cannot produce. *(9c)*
4. **Enrich** — a local LLM adds summaries. *(9d)*

   Note that "related links" and "topic normalisation" are **embedding** jobs,
   not LLM jobs: they fall out of stage 3 and need no model at all.

Every stage emits **markdown**, so the renderer is a swappable last step.
Replacing Quartz later costs one role, not a redesign.

## Access model

**User decision (2026-09-01): one site, one gate, everything in it.** No
audience-scoped builds and no per-page ACL. The wiki is reachable only on the
home LAN (or Tailscale) and only after Authentik, and the few people with
network access are trusted with all of it — work, finances, and credentials
alike.

This is recorded because it is a deliberate simplification, not an oversight:
an earlier proposal split the wiki into family/personal/work sites, and it was
rejected as unnecessary for a closed network. If the audience ever widens, the
build already produces a single tree and splitting it by topic is mechanical.

## Golden rule: one writer, many readers

Obsidian Sync is the **single source of truth**, authored from the Macs. The mirror, the index, and the
wiki are all **derived and rebuildable** — losing any of them costs a rebuild, not data.

This is why the wiki is a static publish and not Docmost or DokuWiki. Both of those
store pages in their own backing store with their own editor, which makes them a
*second writer*: the moment a page is edited in the wiki, vault and wiki have diverged
with no non-manual reconciliation. DokuWiki additionally uses its own markup rather
than Markdown. A read-only publish has no such failure mode.

## Why not Milvus

~359k words chunks to roughly **1,500–2,500 vectors**. Milvus standalone is etcd +
MinIO + separate query/index/data components — several GB of RAM for a system designed
around 10^8 vectors. That is four orders of magnitude past this corpus, and it
contradicts the pattern the rest of the fleet follows (llama.cpp over Ollama; Homarr
reverted for not being config-driven).

**Chosen: `sqlite-vec` + SQLite **FTS5**, in one file**, embedded directly in the search
service. No server, no second container, no network hop. It also gets **hybrid retrieval**
for free — BM25 keyword search and vector search live in the same database and are fused
with Reciprocal Rank Fusion in a single query path. At this corpus size hybrid materially
beats pure vector search, which is weak on proper nouns, code identifiers, and rare terms
— exactly what technical notes are full of.

Backup: the index is **not** backed up. It rebuilds from the mirror in minutes.

## Retrieval: chunks locate, whole notes answer

Chunk-level RAG is the wrong shape for a corpus this small. Average note length is
~800 words; ten complete notes is ~15k tokens, which any model here holds comfortably.

So the pipeline is:

1. Chunk each note by heading (H1/H2/H3) for **recall only**, ~300 words with overlap.
2. Hybrid search (FTS5 + vector, RRF-fused) returns the best-matching *chunks*.
3. Deduplicate chunks up to their **parent notes**, take the top N notes.
4. Feed those notes **whole** to the model, with a citation instruction.

The model therefore always reasons over complete notes with their full context, not
fragments — the main quality failure of naive RAG, and avoidable entirely at this scale.

## Placement

Everything except the answering model runs on **`cmp01`**, colocated with the A4000 so
embedding never crosses a host boundary.

```
  Obsidian Sync (vaults authored on the Macs — single writer)
       |  obsidian-headless, mode=mirror-remote, scheduled by n8n
       v
  cmp01:/opt/homelab/vaults/{pikachu,snorlax,psyduck}   read-only mirror
       |
       +--> vaultindex   diff -> chunk -> embed -> sqlite-vec + FTS5
       |        |
       |        +-- embeddings: llama-server (CUDA, internal network, no published port)
       |        +-- answers:    ai01 llama-server over LAN
       |
       +--> quartz       static build -> wiki.puhome.net
       +--> vaultindex UI                 ask.puhome.net
```

### Roles

| Role | Host | Purpose |
|---|---|---|
| `services/knowledge/vaultsync` | cmp01 | `obsidian-headless` client + per-vault sync config (mirror-remote) |
| `services/knowledge/vaultindex` | cmp01 | Indexer + hybrid search API + Q&A UI + embedding llama-server |
| `services/knowledge/quartz` | cmp01 | Quartz static build, served by a small nginx container |
| `services/ai/llama_server` | ai01 | Native llama.cpp answer/agent endpoint (see Phase 8b) |

### Ports (cmp01, verified free against the current catalog)

| Port | Service |
|---|---|
| 8100 | vaultindex API + UI |
| 8101 | quartz static site |
| — | embedding llama-server: `internal: true` network, no published port |

### Domains

- `wiki.puhome.net` -> cmp01:8101 (Quartz)
- `ask.puhome.net` -> cmp01:8100 (search + Q&A)

Both Tier-2 forward-auth. Neither is exposed off-LAN beyond Tailscale.

## Transport: `obsidian-headless` on cmp01

The vaults already live in **Obsidian Sync**, and Obsidian ships an official **headless Sync
client** for exactly this case (CI pipelines, agents, automation):
<https://obsidian.md/help/sync/headless>.

`npm install -g obsidian-headless` (Node >= 22, currently **v0.0.14**). It authenticates with
`ob login` against the existing Sync subscription and runs on Linux, so it installs directly
on **cmp01** — no intermediate Mac, no Electron GUI daemon, no rsync hop, and **no second sync
mechanism** alongside Obsidian Sync.

Per vault, one `ob sync-setup` + `ob sync-config`:

| Option | Value | Why |
|---|---|---|
| `--mode` | `mirror-remote` | "Only download, revert local changes." This is the golden rule as a **mechanism**: a stray local write is not merely ignored, it is reverted. Stronger than a receive-only Syncthing folder. |
| `--excluded-folders` | `Secrets,Finances` (Psyduck) | Excluded content is never transferred to cmp01 in the first place. |
| `--file-types` | `image,pdf` | Skips `audio,video,unsupported` — most of the 92M/48M vault bulk is attachments the wiki does not need and the index never reads. |
| `--configs` | *(empty)* | Do not sync `app`/`appearance`/`plugin` config categories; `.obsidian` state is machinery, not content. |
| `--device-name` | `cmp01-knowledge` | Identifies this consumer in Sync version history. |

**Upstream caveat:** the docs warn *"Do not use both the desktop app Sync and Headless Sync on
the same device."* cmp01 is a distinct device from the Macs, so this does not apply — but it
does mean cmp01 must never also run the desktop app.

**Version pinning:** `0.0.14` is early software. The role installs
`obsidian-headless@{{ vaultsync_headless_version }}`, pinned in `defaults/main.yml` and
bumped deliberately — the same posture as Kavita 0.8.2 and the Beszel agent. Diun does not
watch npm, so this pin has no auto-update path and is only ever moved by hand.

**Manual one-time step:** `ob login` is interactive and needs the Sync credentials plus the E2E
encryption password. Ansible installs and configures everything else; the login itself is done
by hand once on cmp01 and recorded in [the runbook](../runbooks/knowledge-base.md), alongside the other documented manual steps
(Tailscale subnet approval, Pi-hole conditional-forward, Omada policy rules).

### Scheduling — n8n owns it

n8n runs `ob sync` **one-shot per vault** on a schedule over the existing forced-command SSH
key, then triggers `vaultindex reindex` and the Quartz rebuild — one chained workflow,
`homelab-vault-ingest`, alongside the existing four.

**Cadence: hourly** (`vaultsync_schedule_cron`). Per user direction the wiki is updated
*periodically, not in real time*. Hourly is affordable because every stage is incremental:
`ob sync` transfers only changed files, `vaultindex reindex` re-embeds only notes whose
content hash moved, and Quartz rebuilds only on a non-empty changeset. A quiet hour costs
one SSH round-trip and no GPU work. This is the established fleet
pattern (`ops/wanwatch`, `ops/maintenance`): **n8n schedules and reports, the host does the
work.** Failures land in the existing ntfy channel.

`ob sync --continuous` (a watching daemon) is deliberately *not* used — it would put the sync
schedule outside n8n and outside the notification path.

> **n8n is a trigger, not a transformer.** Chunking, embedding, and the Quartz build never run
> as n8n nodes; they are CLI entrypoints on cmp01 that n8n invokes.

## Scope: everything is indexed

**User decision (2026-09-01): `Secrets/` and `Finances/` are included** — in the mirror, the
index, the wiki, and the answer path. The reasoning is that all three surfaces are LAN-only
and Authentik-gated, so the vault's own privacy partition adds nothing here.

Recorded because it is deliberate and looks like an oversight otherwise: this creates
plaintext copies of credentials and 2FA recovery codes **outside** the E2E-encrypted vault —
in `vaultindex.db` and in the generated static HTML — and those notes will appear in search
results for unrelated queries. If that becomes noisy, `vaultindex_excluded_paths` can drop
them from the index while the wiki keeps serving them; the split is a config flag, not a
redesign.

> This does **not** relax the separate rule that agents never *write* into `Secrets/` or
> `Resources/Finances/`. Reading them for the index and writing to them are different
> operations; only the former is authorised here.

Still excluded, as machinery rather than content:

| Pattern | Reason |
|---|---|
| `**/.obsidian/**`, `**/.trash/**`, `**/.smart-env/**`, `**/.smtcmp_*` | Plugin state and vault machinery |
| `**/.git/**`, `**/.claude/**`, `**/.claudian/**` | Tooling state |

These need no `--excluded-folders` entry: `ob sync-config --configs` is empty, so the config
directory never transfers at all. The indexer re-asserts them anyway as a second line of
defence, since the mirror is not the only thing that could ever feed it.

Attachments sync as `image,pdf` only and are served by the wiki but never indexed; no OCR or
image understanding in scope.

## Answering model — local only

Per user direction, **no vault content leaves the LAN.** Pikachu is work material, and the
same boundary is applied uniformly to all three vaults rather than being enforced per-vault.
The answering endpoint is `ai01`, reached at `ai01.host.puhome.net`.

This makes the knowledge base depend on **Phase 8b**, which is still open (agent model
undecided). The resolution that keeps them from blocking each other:

- ai01 runs **one** llama-server as a shared resident model, native via Homebrew + launchd
  (Metal; Docker on macOS gets no GPU). It serves vault Q&A now, and the agent later.
- ai01 is a Mac Mini M4 Pro with **24 GB unified memory**, so there is budget for exactly
  one substantial model. Running a separate Q&A model *alongside* an agent model does not
  fit — hence one shared resident.
- Embeddings do **not** run on ai01. A small embedding model (bge-small or nomic-embed-text)
  runs in a second CUDA llama-server on cmp01, on an `internal: true` network with no
  published port and no route to the LAN or internet — the same containment pattern as the
  Paperless tagging model.

Until ai01 is built, `vaultindex` runs in **retrieval-only mode**: hybrid search returns
ranked notes with highlighted excerpts and no generated answer. This is a config flag, not
a different build, and it is genuinely useful on its own.

## Wiki: one merged Quartz site

**Quartz 4/5**, chosen because it is Obsidian-native: it resolves `[[wikilinks]]`,
renders transclusions and callouts, and generates backlinks, an explorer, a
graph and client-side search without conversion.

It builds **one site** from the merged tree — not one per vault. The build runs
from a writable staging copy of the merged content, never from the mirror.

### Organisation: by topic, vaults dissolved

Top-level structure is **topic**, derived by rule from each note's source path
and frontmatter tags — not by vault. Provenance is preserved in frontmatter
(`merged_from: pikachu|snorlax|psyduck` plus `merged_topic:`), not in the folder
hierarchy. The keys are namespaced because notes already use `source:` for their
own citation metadata, and a duplicate YAML key fails the whole build.

The mapping lives in a role variable so the taxonomy is reviewable and
declarative, and unmapped notes land in a catch-all rather than vanishing.

### Collisions

35 of 499 notes share a filename across vaults (`Interview.md`, `Home.md`,
`MOC.md`, `Projects.md`, …) — mostly the career material that exists in both
Pikachu and Psyduck, which is precisely why merging is worth doing. Colliding
notes are disambiguated by a source-derived sub-path, deterministically, so the
same input always produces the same output path.

### Links

Merging moves every file, so wikilinks must be rewritten to their new paths.
A link is resolved **within its source vault first** — `[[Interview]]` in a
Pikachu note means Pikachu's Interview — so the merge never silently
re-points an existing link at another vault's note. Genuinely new cross-vault
links come later, from the index (9c) and the LLM (9d), and are added as a
clearly marked "related" layer rather than injected into the prose.

## AI enrichment — placement and constraints

**Corrected 2026-09-01: enrichment runs on cmp01, not ai01, and is not blocked
on Phase 8b.** The original spec put it on ai01 and marked it blocked on the
agent-model decision. That contradicted this document's own reasoning for
Paperless: note summarisation is *short-context, high-volume* work, which is
precisely what the A4000 is for. The GPU has ~12.7 GB free alongside the
existing `paperless-llama`, which is room for a 12–14B model at Q4.

`ai01` remains reserved for the long-context agent model (9c's answering path);
it has nothing to do with enriching the wiki.



Per the 2026-09-01 decision the LLM produces **summaries, related links, and
normalised topic tags**. Two hard rules:

- **It never rewrites note bodies.** Source text is reproduced verbatim;
  generated material sits alongside it and is visibly marked as generated. This
  wiki is the place someone looks up a credential or a financial figure, and a
  plausible hallucinated number is worse than no wiki at all.
- **Output is cached by note content-hash.** Running a local model over ~500
  notes hourly is slow, and non-deterministic output would make the site churn
  on every build even when nothing changed. Enrichment is computed once per note
  version and reused until that note changes.

The second rule changes one property: the wiki remains *derived*, but is no
longer cheap to rebuild from nothing. The enrichment cache is therefore worth
persisting — see [Backups](#backups).

## Backups

`cmp01` is already a `backup_client`. The mirror, the merged tree, the index and
the built site are all derived and rebuildable, so none of them are backed up.

The **enrichment cache (9d) is the exception**: it is still derived, but
regenerating it means re-running a local LLM over every note. Back that up when
it exists — it is small (text keyed by content-hash) and expensive to recreate.

The one thing worth noting in `hosts/host_vars/cmp01/vars.yml` is an explicit comment saying
this omission is deliberate, so a later reader does not "fix" it.

## Out of scope

- Writing to vaults from the web. Read-only, both interfaces, permanently.
- OCR / image / PDF understanding of attachments.
- Cross-vault link resolution.
- Any cloud LLM in the answer path.

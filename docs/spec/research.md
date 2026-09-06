# Research Agent — commissioned research, published to the wiki

A topic, a link, or an article goes in; a structured, cited body of notes comes
out, published into the wiki under `Agent-Research/` with a map-of-content
linking every note it produced.

This is the first thing in the fleet that *writes* knowledge rather than
mirroring it, and the first that reads the open internet. Both facts drive the
design below far more than the feature list does.

## What it is not

**It is not a general-purpose agent.** Hermes and OpenClaw were both considered
and both rejected — see [Why not an agent framework](#why-not-an-agent-framework).
The job here is a fixed pipeline: plan, search, fetch, summarise, synthesise,
write. Every stage's input and output are known in advance. Nothing about it
needs a model that can choose its own actions, and giving it one would forfeit
the single strongest security property this design has.

## The security argument, first

`researchd` reads arbitrary web pages and feeds them to a language model. That
is untrusted input by definition, and it is why placement matters:

> **ctl01 holds the Ansible vault password and `keys/ansible_rsa.private` —
> fleet-wide root.** Anything on that host that ingests hostile text is a
> prompt-injection path to full compromise of every machine here.

Per user direction the agent runs on ctl01 regardless. Four properties make that
safe, in descending order of how much work they do:

### 1. The model never chooses an action

This is the real defence, and it is architectural rather than a control that can
be misconfigured. In this pipeline the model is called exactly three ways:

| Call | Input | Output | Consumed as |
|---|---|---|---|
| Plan | the user's topic | JSON outline | schema-validated, then discarded if invalid |
| Summarise | one document's text | summary + excerpt offsets | text |
| Synthesise | a set of summaries | prose | text |

It never emits a command, never selects a URL to fetch, never picks a tool, and
never sees a credential. A malicious instruction embedded in a fetched page can
corrupt a *summary*. It cannot cause an *action*, because there is no code path
from model output to anything but a markdown file. A general agent framework
deletes this property on day one — that is what "agentic" means.

The one place model output does influence later fetching is depth-4/5 recursion,
where synthesis proposes related topics to research next. Those proposals are
treated strictly as **search queries, never URLs**, are capped in number, are
bounded by the depth budget, and traverse the same search-then-fetch path as
everything else. Worst case: it researches a topic an injected page suggested.
That is a content-quality problem, not an escape.

### 2. It holds no credentials, because delivery is a pull

`researchd` never pushes its output anywhere. It exposes the finished bundle on
its own API and **cmp01 fetches it**. Consequently researchd has no SSH key, no
vault password, and no write access to any host but itself.

It holds **exactly one secret**: the inference API key for `omlx` on ai01. That
key buys an attacker nothing but token generation on a model they could already
reach if they were inside the container anyway. Everything of actual value —
fleet root, the vault, the NAS — is reachable only through credentials
researchd has never been given.

### 3. It cannot see the host filesystem

One container, one volume — its own data directory. `~/.config/homelab/.vault_pass`,
`keys/`, and the repo are never mounted.

**Colima's default `$HOME` mount into the VM must be disabled** (`mounts: null`
in `system/colima` — see the correction below; this originally said `mounts: []`,
which is the opposite). Otherwise the VM — and anything that escapes a container into
it — can read ctl01's vault password directly. This is a prerequisite for the
role and worth fixing on its own merits, independently of this project.

### 4. Its egress is allowlisted

Colima is a Linux VM, so Docker's `DOCKER-USER` iptables chain is the normal,
testable enforcement point:

| Rule | Destination |
|---|---|
| ALLOW | `10.0.2.9:8000` — omlx on ai01. **The only LAN destination.** |
| **DROP** | `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16` — the entire rest of the LAN |
| ALLOW | `0.0.0.0/0` tcp/80, tcp/443 — the public internet, which it must read |

SearXNG does not appear here because it is **not on the LAN**: it runs beside
researchd in the same compose project and is reached over the project network
as `http://searxng:8080`. So the allowlist reduces to a single permitted LAN
address.

It can read the web and talk to one model. It cannot reach the NAS, gw01,
Authentik, Portainer, n8n, util01, or any other host. The image ships no ssh client, no
docker socket, and no shell tooling beyond the Python runtime.

## Why not an agent framework

| | |
|---|---|
| **Hermes** | Writes its own skills into `~/.hermes/skills/` — mutable state no role owns, which is the same objection that got Homarr reverted. Needs ≥64k context and shell access it would not use here. |
| **OpenClaw** | Better skill model (human-authored), but the March 2026 CVE cluster including a CVSS 9.9 makes it the wrong thing to put on the host holding fleet-root credentials. |
| **Both** | Buy a capability — open-ended tool selection — that this job does not need, and pay for it with property #1 above. |

A single-purpose service is also simply the fleet's established pattern:
llama.cpp over Ollama, `sqlite-vec` over Milvus, Homepage over Homarr. Small,
declarative, no runtime state the roles do not own.

## Pipeline

This is the **`native` engine's** pipeline, and it is the one the security
argument above is written about. The `gptr` engine covers the same stages but
decides internally how — and property #1 is genuinely **weaker** for it: it
lets the model choose search queries *and* which results to scrape, which is
closer to "the model chooses an action" than the native loop's strict
search-then-fetch. It gets no shell, no tool registry and no credential, and
what it hands back is schema-validated data — but the honest statement is that
#1 holds fully only for `native`. That is a large part of why `native` is the
default.

What is unchanged for every engine is #2, #3 and #4: no credentials, no host
filesystem, and the same `DOCKER-USER` allowlist — the sidecar sits on the same
project network and inherits the identical rules, so its blast radius is the
same as researchd's own. That is what actually bounds the risk here. See
[Engines](#engines).

```
  topic | url | article
        │
   1. resolve      url -> fetch + extract -> derive topic;  plain topic -> use as-is
        │
   2. plan         LLM -> JSON outline: N subtopics, each with search queries + scope
        │
   3. search       SearXNG (web) + arXiv, OpenAlex, Crossref, Wikipedia, Open Library
        │          dedup by URL and DOI
        │
   4. fetch        httpx + trafilatura -> clean text;  PDFs -> text extraction
        │          per-doc size cap, hard timeout, honest UA, robots respected
        │
   5. summarise    LLM, once per source -> 150-250 word summary + excerpt offsets
        │
   6. synthesise   LLM, once per subtopic -> a note, cited from the summaries
        │
   7. write        markdown tree + frontmatter + MOC + bibliography
        │
   8. ready        bundle offered on the API;  ntfy fires;  n8n takes over
```

Stages 5 and 6 are the only expensive ones and both are per-item, so a job's
cost is linear in `subtopics x sources` — which is exactly what the depth dial
controls.

### Excerpts are extracted, never generated

The summarise stage returns *character offsets* into the source text, not quoted
strings. The code slices the excerpt out of the original itself and verifies the
result appears verbatim (whitespace-normalised) in the fetched document. Any
excerpt that fails that check is dropped silently.

A model cannot fabricate a quotation through an offset. This is the same rule
`knowledge.md` applies to note bodies — generated text may sit *beside* source
material, never *inside* it — and it is the difference between a research note
and a plausible-sounding fiction with links attached.

## Depth

One dial, `1`–`5`, default **2**.

| depth | subtopics | sources each | recursion | extra output | est. wall time |
|---|---|---|---|---|---|
| 1 | 3 | 3 | — | — | ~3 min |
| **2** | **5** | **5** | — | — | ~8 min |
| 3 | 8 | 6 | — | Related-Topics note | ~20 min |
| 4 | 12 | 8 | 1 level | + recursed subtopic notes | ~45 min |
| 5 | 18 | 10 | 2 levels | + papers prioritised, Open-Questions note | ~2 h |

Times are estimates against one resident model on an M4 Pro and will be
corrected here once measured. The parameters live in role defaults as a table,
so tuning the scale is a variable change and never a code change.

## Output

For `How to write Spark Jobs?` at depth 2:

```
Agent-Research/How-To-Write-Spark-Jobs/
├── How-To-Write-Spark-Jobs.md      <- the MOC (folder note)
├── Sources.md                      <- full bibliography
├── Spark-Execution-Model.md
├── Writing-A-Spark-Job.md
├── Partitioning-And-Shuffles.md
├── Performance-Tuning.md
└── Testing-And-Deployment.md
```

The MOC is named after the topic rather than `MOC.md` deliberately: `MOC.md` is
already one of the 35 known cross-vault filename collisions, and a folder note
matching its directory is the pattern Obsidian and Quartz both render best.

### Frontmatter contract

```yaml
title: Partitioning and Shuffles
research_topic: How to write Spark Jobs
research_slug: How-To-Write-Spark-Jobs
research_depth: 2
research_generated: 2026-09-03T14:32:11+05:30
research_model: <model id and quantisation>
research_engine: native | gptr
merged_topic: Agent-Research
merged_from: agent-research
tags: [agent-research, generated]
```

`research_engine` is the ENGINE ID, not its label, because this is the value a
Dataview query filters on and a label can be reworded where an id cannot. It
records the engine that actually ran — which is not always the one the job
asked for, since an unknown id falls back to `native`, and a page claiming an
engine that did not write it would be worse than no label. Every file in the
tree carries it, not only the MOC. The human-readable label appears in each
note's machine-written callout instead.

`merged_topic` / `merged_from` are the keys `vaultmerge` already uses, so these
notes flow through the existing merge without special-casing. The namespacing
exists because notes use bare `source:` for their own citations and a duplicate
YAML key fails the whole Quartz build.

### Note shape

Every generated note opens with a callout marking it machine-written, and every
claim carries a footnote to a real link. Excerpts are blockquoted with
attribution, and the model's explanation sits *below* the quote, never merged
into it.

## Placement

```
you ──► research.puhome.net              UI + API on ctl01, Authentik-gated
             │
             │   researchd + searxng  (one Colima compose project, egress-locked)
             ├── LLM ──────────► ai01:8000      the only LAN destination
             ├── search ───────► searxng (project network) + arXiv/OpenAlex/Crossref/Wikipedia
             └── fetch ────────► public internet :443
             │
             └── ready ──► ntfy + webhook
                              │
              n8n `homelab-agent-research`   (trigger and transport only)
                              └── ssh cmp01 ──► pulls bundle FROM ctl01's API
                                       └── vaultmerge ─► vaultindex ─► quartz
                                                └── callback ─► UI shows published
```

The split follows the user's constraint exactly: **the model runs on ai01 and
nowhere else; the agent runs on ctl01 and nowhere else; n8n orchestrates from
util01 and talks to the agent only over HTTP.**

n8n stays a **trigger, not a transformer** — the rule the vault ingest already
follows. No searching, no fetching, and no LLM call ever runs as an n8n node.

## API

```
POST /api/research          {topic|url, depth}  -> {job_id}
GET  /api/jobs                                  -> list with status
GET  /api/jobs/{id}                             -> job detail + per-stage state
GET  /api/jobs/{id}/events                      -> SSE progress stream
GET  /api/jobs/{id}/bundle.tar.gz               -> the markdown output
POST /api/jobs/{id}/claim                       ready -> publishing (n8n, before ingest)
POST /api/jobs/{id}/unclaim                      publishing -> ready (n8n, on ingest failure)
POST /api/jobs/{id}/published                   {wiki_url}  <- n8n's callback
POST /api/jobs/{id}/cancel
GET  /healthz
```

### Job states

```
queued -> planning -> searching -> fetching -> summarising
       -> synthesising -> writing -> ready -> publishing -> published
                                       └─> failed | cancelled
```

`ready` means the markdown exists. **`published` means it is in the wiki** — set
only by n8n's callback after the Quartz rebuild. The UI's completion checkmark
tracks `published`, not `ready`, so a green tick always means the link works.

`publishing` sits in between, entirely owned by n8n's poll and never by the
pipeline itself: `/claim` is a single conditional `UPDATE ... WHERE
status='ready'` that atomically moves a job into it right before the SSH
ingest starts, so an overlapping poll cannot start a second ingest of the
same job. n8n workflow static data was tried as this guard first and does
not persist on this deployment at all — the lock has to live in researchd's
own database instead. `/claim` also reclaims a `publishing` row whose claim
is older than `researchd_claim_stale_minutes` (default 60), so a job whose
ingest died mid-flight is retried rather than wedged forever; `/unclaim` does
the same on the ingest's own explicit failure branch.

## Engines

The research loop is **pluggable**. A job names an engine at submit time and the
UI offers a dropdown beside the depth selector; everything downstream — the
writer, the wiki contract, the API, the SSE stream, the bundle, the n8n publish
path — is identical no matter which one ran.

| id | Label | State |
|---|---|---|
| `native` | Built-in | researchd's own loop. **The default.** The only engine hardened against this model's failure modes (the bare-list plan coercion, note-local footnote renumbering) and the only one the security argument above holds for in full. |
| `gptr` | GPT Researcher | `gpt-researcher` 0.16.0, Apache-2.0, actively maintained. Works, with a known sourcing limitation — see below. |

**STORM was evaluated and dropped.** Stanford's `knowledge-storm` has the better
research method on paper (multi-perspective question asking) and its
Wikipedia-shaped output would have suited the wiki well, but: its last real
release is v1.1.0 from January 2025 and its last commit of any kind is
2025-09-30, so it is effectively frozen; it hard-pins `dspy_ai==2.4.9` and drags
in `sentence-transformers`/torch; it calls
`SentenceTransformer("paraphrase-MiniLM-L6-v2")` unconditionally with no
injection hook; and driving its stage methods directly — required to keep the
in-memory article objects `.run()` discards — leaves its `callback_handler`
unset, so the first stage dies on
`'NoneType' object has no attribute 'on_identify_perspective_start'`. Not worth
carrying. The sidecar contract below is engine-agnostic, so adding it back later
costs only the sidecar itself.

### Why a sidecar container, not a library

A third-party engine brings its own dependency closure, and those closures do
not reliably co-resolve with researchd's or with each other (GPT Researcher
needs `langchain>=1.0` and `numpy<2.3`; STORM wanted `dspy_ai==2.4.9` and
torch). One container per engine makes that a non-problem and leaves the
researchd image exactly as small as it was.

The sidecar speaks one small HTTP contract on the project network:

```
GET  /healthz                -> {"ok": true, "engine": str}
POST /research               {topic, depth, job_id} -> {engine_job_id}
GET  /research/{eid}         -> {status, stage, detail, error, result}
POST /research/{eid}/cancel
```

researchd polls the sidecar and republishes its `stage`/`detail` as its own SSE
events, so the UI shows live progress for a remote engine as for the native one.
The `result` field names match `write.NoteSpec` / `SourceCitation` exactly, so
conversion is a field-by-field copy rather than a remapping that could quietly
drop something — and it is **schema-validated on arrival**
(`models.RemoteEngineResult`), because it crosses a trust boundary just as model
output does.

An engine that is down reports `available: false` and is disabled in the
dropdown; researchd deliberately does not `depends_on` its engines, so a broken
sidecar can never stop researchd starting. A job naming an engine that no longer
exists falls back to `native`.

### Embeddings

`gptr` needs an embedding model and **omlx serves chat completions only** —
there is no `/v1/embeddings` on ai01. So the project runs its own: llama.cpp
serving `bge-small-en-v1.5` (q8_0, ~36 MiB, 384-dim, mean pooling) — the same
model `services/knowledge/vaultindex` already runs on cmp01, so the fleet has
one embedding model and the two systems' vectors stay comparable.

It is a **container on the project network**, not a native macOS service on
ctl01 and not vaultindex's endpoint on cmp01:

- On the project network the intra-project ACCEPT already covers it, so it needs
  **no new allowlist rule at all**.
- A host-side server on ctl01 would sit at `10.0.2.4`, inside the `10.0.0.0/8`
  DROP, needing a permanent exception. (It *would* work — the Colima VM routes
  to ctl01's own LAN address via gvproxy, verified — it is simply not worth an
  exception for something that can live in the sandbox.)
- Reusing vaultindex's endpoint would mean letting researchd reach the host
  holding the Obsidian vaults, contradicting an explicitly asserted and verified
  property of this design.

### The llama.cpp / OpenAI embedding mismatch

llama.cpp's `/v1/embeddings` is not quite OpenAI's, and gpt-researcher is
written against OpenAI's. langchain's `OpenAIEmbeddings` defaults to
`check_embedding_ctx_length=True`, which posts **tiktoken integer arrays**
rather than text — legal against OpenAI, which shares that vocabulary. llama.cpp
reads those integers as ids in the SERVED model's vocabulary instead, which
surfaces as two different-looking errors from one cause:

| Served model | Error |
|---|---|
| bge-small (512 ctx) | `input (1202 tokens) is too large to process` — the array length, not real text |
| nomic (2048 ctx) | `400 {'message': 'Prompt contains invalid tokens'}` — ids out of range |

The first reads exactly like a genuine context-size problem and is not one.
The fix is `check_embedding_ctx_length: False` (plain strings on the wire), set
in the sidecar's `EMBEDDING_KWARGS`.

**There is no size limitation on sources.** gpt-researcher's compressor pipeline
is `[RecursiveCharacterTextSplitter(chunk_size=1000), EmbeddingsFilter]` — it
splits every scraped page into ~1000-character (~250-token) chunks *before*
embedding, so nothing close to any model's context ever reaches the endpoint.
Verified in the running container: across two jobs, 33 sources added and zero
embedding errors.

Worth recording because it cost several rounds: a longer-context model
(`nomic-embed-text-v1.5`) and raised `--ctx-size`/batch sizes were tried first,
on the assumption that the bge-small error was about size. None of it helped,
and all of it was reverted. bge-small's 512-token context is ample for
250-token chunks, and keeping it means one embedding model in the fleet rather
than two.

## Web UI

One page, served by the same container, at `research.puhome.net`.

- A topic box and a depth selector.
- A list of research cards, newest first, persisted in SQLite so the list
  survives a restart.
- A running card shows the current stage and an `n/m` source counter, driven by
  the SSE stream — no polling.
- A finished card shows a checkmark and a link into the wiki.
- Clicking a card expands the stage timeline and the sources as they resolve, so
  a long depth-5 job is legible while it runs.
- A topic already researched is shown as such, with its date and a re-run
  action.

Re-running an existing topic replaces its directory and appends to the
`research_generated` history rather than creating `-2` duplicates.

## Backups — this one IS backed up

Every other artifact in the knowledge pipeline is deliberately excluded from
restic: the vault mirror, the merged tree, the index and the built site are all
derived from Obsidian Sync and rebuild in minutes.

**`Agent-Research/` is not derived.** It has no upstream — the model that wrote
it is non-deterministic and the sources drift or vanish. The published tree on
cmp01 is the only copy in existence, so it is **original data and must be added
to the restic scope**, alongside Paperless and Syncthing.

The researchd job database is *not* backed up; it is UI history, and the notes
it points at are safe elsewhere.

## Inference — ai01 runs `omlx`, and only `omlx`

**Decision (2026-09-03): `omlx`, not llama.cpp.** This also settles the
long-open Phase 8b question of what ai01 runs.

[`omlx`](https://github.com/jundot/omlx) is an Apple-MLX inference server
(Apache-2.0, built on `mlx-lm`) with continuous batching and SSD-backed KV
caching, aimed squarely at Apple Silicon. It is **already installed on ai01**
via a third-party Homebrew tap (`jundot/omlx`) — though never once run: there is
no `~/.omlx`, no model cache, no launchd plist and no log.

Why it wins here over llama.cpp, which the fleet otherwise prefers:

- **MLX is the native path on Apple Silicon.** llama.cpp's Metal backend is a
  port; MLX is the framework Apple built for this hardware.
- **Continuous batching is worth real money to this workload.** The summarise
  stage is many small independent calls — a depth-5 job issues ~180 of them.
  Batching them is the difference between a two-hour job and a much longer one.
  llama-server's batching is far weaker.
- **SSD-backed paged KV cache** (`--paged-ssd-cache-dir`) is exactly the relief
  valve a 24 GB box needs. ai01 now has 149 GB free, so this is free headroom.
- **OpenAI-compatible** (`/v1/chat/completions`), so the client is a plain HTTP
  call and the engine stays swappable.

### Version: upgrade to 0.6.4

ai01 has **0.4.4rc1** — a release candidate, and well behind. Upstream stable is
**0.6.4**, and the 0.6.x line specifically fixed continuous batching and
prefix-cache reconstruction, both of which this pipeline leans on directly.

The role installs a **pinned** `omlx_version`, bumped by hand — the same posture
as `obsidian-headless 0.0.14`, Kavita and the Beszel agent. Diun does not watch
Homebrew taps, so there is no auto-update path and none is wanted for a
single-maintainer pre-1.0 dependency.

### Configuration — four things the role must not get wrong

1. **`--host` must be set.** omlx binds `127.0.0.1:8000` by default, which is
   unreachable from ctl01. The role binds `10.0.2.9,127.0.0.1` — the LAN address
   explicitly, *not* `0.0.0.0`, so it is not listening on every interface it
   might acquire.
2. **`--api-key`, from the vault.** omlx supports one; the handoff's standing
   complaint that "Ollama has no auth" does not have to be inherited. This is
   the single secret researchd carries.
3. **The model is pre-placed, never pulled on demand.** omlx defaults to
   downloading from HuggingFace into `~/.omlx/models` — runtime state no role
   owns, which is precisely the objection that got Ollama rejected. Ansible
   instead fetches the MLX quantisation to `{{ omlx_model_dir }}` and points
   `--model-dir` at it, so the exact model on disk is a declared fact.
4. **Not `brew services`.** The formula's bundled plist runs `omlx serve` with
   zero arguments — i.e. every default, including the localhost bind. The role
   templates its own launchd plist with the real flags.

### Model

**Qwen3-30B-A3B, 4-bit MLX** (user decision, 2026-09-03). A mixture-of-experts
model with ~3B parameters active per token: it generates at roughly small-model
speed while synthesising like a much larger one, which is the exact shape of
this workload. `mlx-lm` ships `qwen3_moe.py`, so the architecture is supported.

Pinned to **`mlx-community/Qwen3-30B-A3B-Instruct-2507-4bit`** (verified to
exist, 2026-09-03). The `Instruct-2507` refresh is the right variant here: it is
the non-thinking instruct line, so it does not burn tokens on reasoning traces
the pipeline would only throw away. `mlx-community` also publishes a `-DWQ`
(distilled-weight-quantised) build of the same model at the same size and
plausibly better quality — a drop-in `omlx_model_repo` change worth A/B testing
once the pipeline is running, not a decision to make blind up front.

Budget honestly: ~16–17 GB of weights at 4-bit against 24 GB of unified memory
shared with macOS. That is **tight**, and it is why `--memory-guard` and the SSD
cache tier are configured rather than left default.

> **One resident model, fleet-wide.** ai01 has budget for exactly one. It serves
> research synthesis now and the vault Q&A path later, as
> [`knowledge.md`](knowledge.md) already assumes. Note-summary enrichment stays
> on cmp01's A4000 and is unaffected.

## Ports and domains

| Host | Port | Service | Free? |
|---|---|---|---|
| ai01 (10.0.2.9) | 8000 | omlx | first service on the host |
| ctl01 (10.0.2.4) | 8110 | researchd API + UI | first published port on the host |
| ctl01 | *(none)* | SearXNG | project network only, no published port |
| ctl01 | *(none)* | `researchd-embed` (llama.cpp) | project network only, no published port |
| ctl01 | *(none)* | `researchd-gptr` engine sidecar | project network only, no published port |

Only the first of those is published. Every engine and support service is
reached by container name on the project network, which is why adding engines
never widens the LAN allowlist — see "Engines" below.

| Domain | Backend | Gate |
|---|---|---|
| `research.puhome.net` | `10.0.2.4:8110` | Tier-2 forward-auth |

Added as an `nginx_sites` entry in `hosts/host_vars/gw01/vars.yml`, which
**auto-generates its Uptime Kuma HTTP monitor** — `kuma_http_monitors` is derived
from `nginx_sites`, so monitoring needs no separate registration.

### SearXNG placement — ctl01, not util01

**Changed 2026-09-03 on user direction: util01 is already carrying twelve
services.** Moving SearXNG to ctl01 turned out to be better than a load shuffle:

- It leaves researchd with **exactly one** permitted LAN destination instead of
  two, which is a materially simpler containment story.
- The search call stops crossing a host boundary on the hot path — a depth-5 job
  issues ~180 of them.
- It follows the pattern paperless-ai already set: run inside the consumer's
  compose project and reach it by service name over the project network, rather
  than as a separate deployment addressed by host IP.

So SearXNG is **part of `services/ai/researchd`**, not its own role, and gets no
published port and no domain. Exposing it later as a personal metasearch engine
is one `nginx_sites` line plus a port mapping — worth doing only if wanted for
its own sake.

`util01:8095` is released back to the free pool.

omlx is deliberately **not** given a domain. It is reached only at
`10.0.2.9:8000` from one allowlisted source, and putting it behind nginx would
give it a second, broader path in.

## Wiring into the wiki

`vaultmerge` gains a fourth source. Its `.mjs` iterates `Object.keys(SOURCES)`
and resolves each to `path.join(SRC, vault)`, where `SRC` is the *vaultsync
mirror* directory — so a fourth entry would land generated content inside the
tree `obsidian-headless` manages in `mirror-remote` mode. That works today, but
it mixes "mirror of Obsidian" with "written by us" under one parent, which is
the kind of thing that bites later.

So the change is two lines, not one:

```yaml
vaultmerge_sources:
  pikachu: work
  snorlax: kb
  psyduck: personal
  research: agent          # <- new

# New: sources whose tree does not live under the vaultsync mirror.
vaultmerge_source_overrides:
  research: "{{ service_root }}/research/tree"

vaultmerge_path_topics:
  - { match: "research", topic: "Agent-Research" }
```

and in `merge-vaults.mjs.j2`, one line:

```javascript
const vdir = OVERRIDES[vault] ?? path.join(SRC, vault)
```

Generated notes keep their `merged_topic: Agent-Research`, so they land in one
top-level wiki section and never interleave with hand-written notes.

## Roles and dependencies

| Role | Host | Status |
|---|---|---|
| `services/ai/omlx` | ai01 | **new** — the resident model (settles Phase 8b) |
| `system/colima` | ctl01 | **new, and a hard prerequisite** — does not exist |
| `services/ai/researchd` | ctl01 | **new** — pipeline, API, UI |
| SearXNG | ctl01 | **new** — a second service inside the researchd compose project, not its own role |
| `researchd-embed` | ctl01 | **new** — llama.cpp embeddings, inside the researchd compose project, not its own role |
| `researchd-gptr` | ctl01 | **new** — the engine sidecar, built from `files/engines/gptr`, inside the same project |
| `services/knowledge/researchpull` | cmp01 | **new** — pulls the bundle, unpacks it |
| `services/knowledge/vaultmerge` | cmp01 | edit — fourth source |
| `services/productivity/n8n` | util01 | edit — `workflow-agent-research.json.j2` |
| `services/network/nginx` | gw01 | edit — two `nginx_sites` entries |
| `services/dashboard/homepage` | util01 | edit — two tiles |
| `ops/restic` | cmp01 | edit — add `Agent-Research/` to the backup scope |

**`system/colima` does not exist, and neither do any `dev/*` roles** — Phase 8c
is entirely unbuilt, and `playbooks/hosts/ctl01.yml` references six roles that
are not there. researchd cannot run on ctl01 until Colima does, so building it
is in scope for this phase whether or not the rest of 8c is.

It must be built with **`mounts: null`**. Colima mounts `$HOME` into its VM by
default, which would place ctl01's vault password inside the VM the container
runs in and quietly undo [containment property 3](#3-it-cannot-see-the-host-filesystem).

> **Correction, 2026-09-04.** This spec originally said `mounts: []`. That is
> **backwards**, and following it literally would have mounted `$HOME` writable
> into the VM — the exact outcome the requirement exists to prevent. In Colima's
> config an explicit empty list means "unset, apply the default", and the
> default *is* the home mount; a nil/absent key is what yields zero mounts. The
> implemented role uses `mounts: null`.
>
> The guarantee does not rest on getting that literal right, which is the point:
> the role runs `colima ssh -- mount` against the live VM and asserts no host
> path appears, **on every run**. Verified 2026-09-04 against ctl01 — 31 mount
> entries, zero under `/Users`, and no `sshfs`/`virtiofs`/`9p` transport
> present. Note that this check is only meaningful when the command actually
> executes: `colima` shells out to `limactl` via `$PATH`, and a PATH-less
> invocation fails in a way that greps as a clean pass.

### `system/brew` is the other blocker — verified 2026-09-03

omlx installs from a Homebrew tap, so the omlx role cannot run until Homebrew
works for the `ansible` user on ai01. It does not:

| Check | Result |
|---|---|
| `ansible` in the `admin` group | yes |
| `/opt/homebrew` writable by `ansible` | **no** — owned `uknth:admin`, group has `r-x` |
| `brew --version` | `Homebrew >=4.3.0 (shallow or no git repository)` |
| `git config --global safe.directory` | unset |
| `jundot/omlx` tap | already added |

The HANDOFF assumed admin-group membership alone was enough to write to
`/opt/homebrew`. It is not — the prefix is not group-writable.

**Resolution: run Homebrew as `uknth`, do not make the prefix group-writable.**
Homebrew's own position is that one user owns the prefix, and `chmod -R g+w` over
it is a well-known way to break a future `brew update`. `ansible` already has
NOPASSWD sudo, so `become_user: uknth` costs nothing and leaves permissions
alone. `system/brew` therefore needs to set `safe.directory` for `/opt/homebrew`
and its taps **for `uknth`**, and every brew task runs as that user.

This is a fleet-wide fix — it unblocks every Homebrew-driven role in 8b and 8c,
not just this one.

## Out of scope

- Writing into any Obsidian vault. Research lands in the wiki only (user
  decision, 2026-09-03). Adding an agent-owned vault synced to Obsidian later is
  a folder plus a Syncthing share, not a redesign.
- Any cloud LLM, in any stage.
- Paywalled sources, and any attempt to bypass a paywall.
- Editing research from the web. Read-only, like the rest of the wiki.

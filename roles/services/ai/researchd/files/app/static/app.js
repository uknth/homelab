// researchd -- vanilla JS, no build step, no CDN. One SSE connection per
// running job; the list itself is loaded once and kept live from there.
// No polling anywhere -- see docs/spec/research.md, "Web UI".

const jobsById = new Map(); // id -> { summary, detail, expanded, es }
let order = []; // job ids, newest first
let engines = []; // [{id, label, available, is_default}], from GET /api/engines

const jobsEl = document.getElementById('jobs');
const emptyEl = document.getElementById('empty');
const hintEl = document.getElementById('hint');
const form = document.getElementById('compose');
const topicInput = document.getElementById('topic');
const depthSelect = document.getElementById('depth');
const engineSelect = document.getElementById('engine');
const submitBtn = document.getElementById('submit');

const TERMINAL = new Set(['ready', 'published', 'failed', 'cancelled']);
const STAGES = ['planning', 'searching', 'fetching', 'summarising', 'synthesising', 'writing'];

const esc = (s) => (s ?? '').toString()
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

const isUrl = (s) => /^https?:\/\//i.test(s.trim());

function fmtDate(iso) {
  if (!iso) return '';
  try {
    return new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
  } catch {
    return iso;
  }
}

function statusLabel(status) {
  if (status === 'published') return 'Published';
  if (status === 'ready') return 'Ready — awaiting publish';
  if (status === 'failed') return 'Failed';
  if (status === 'cancelled') return 'Cancelled';
  return status.charAt(0).toUpperCase() + status.slice(1) + '…';
}

function dotClass(status) {
  if (status === 'failed' || status === 'cancelled') return 'err';
  if (status === 'published' || status === 'ready') return 'ok';
  return 'running';
}

function engineLabelFor(id) {
  // Falls back to the raw id (rather than hiding the badge) for a job
  // whose engine has since been removed from the registry -- see
  // pipeline.py's native fallback for the job-side half of this.
  if (!id) return '';
  const found = engines.find((e) => e.id === id);
  return found ? found.label : id;
}

// -------------------------------------------------------------- render ----

function render() {
  emptyEl.hidden = order.length > 0;
  jobsEl.innerHTML = order.map(renderCard).join('');
  jobsEl.querySelectorAll('li.job').forEach((li) => {
    li.addEventListener('click', (e) => {
      if (e.target.closest('button, a')) return;
      toggle(li.dataset.id);
    });
  });
  jobsEl.querySelectorAll('.rerun-btn').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      rerun(btn.dataset.id);
    });
  });
}

function renderCard(id) {
  const entry = jobsById.get(id);
  if (!entry || !entry.summary) return '';
  const s = entry.summary;
  const counts = entry.detail && entry.detail.source_counts;
  const counter = counts && !TERMINAL.has(s.status)
    ? `<span class="counter">${counts.summarised}/${counts.total} sources</span>`
    : '';
  const link = s.status === 'published' && s.wiki_url
    ? `<a class="job-link" href="${esc(s.wiki_url)}" target="_blank" rel="noopener">view in wiki &rarr;</a>`
    : '';
  const check = s.status === 'published' ? '<span class="check">&#10003;</span> ' : '';
  const engineLabel = engineLabelFor(s.engine);

  return `
    <li class="job${entry.expanded ? ' open' : ''}" data-id="${id}">
      <div class="job-head">
        <span class="job-topic">${check}${esc(s.topic)}</span>
        <span class="job-depth">depth ${s.depth}</span>
        ${engineLabel ? `<span class="job-engine">${esc(engineLabel)}</span>` : ''}
        <span class="job-date">${fmtDate(s.created_at)}</span>
      </div>
      <div class="job-status">
        <span class="badge"><span class="dot ${dotClass(s.status)}"></span>${statusLabel(s.status)}</span>
        ${counter}
        ${link}
      </div>
      ${s.error ? `<div class="error-detail">${esc(s.error)}</div>` : ''}
      <div class="detail">${renderDetail(entry)}</div>
    </li>`;
}

function renderDetail(entry) {
  const d = entry.detail;
  if (!entry.expanded) return '';
  if (!d) return '<p class="hint">Loading…</p>';

  const timeline = STAGES.map((stage) => {
    const rec = (d.stages || []).find((st) => st.stage === stage);
    const cls = rec ? rec.status : (d.status === stage ? 'active' : '');
    const detailText = rec && rec.detail ? ` — ${esc(rec.detail)}` : '';
    return `<li class="${cls}"><span class="stage-name">${stage}</span>${detailText}</li>`;
  }).join('');

  const sources = (d.sources || []).map((src) => `
    <li>${srcIcon(src.status)}
      <a href="${esc(src.url)}" target="_blank" rel="noopener">${esc(src.title || src.url)}</a>
      <span style="opacity:.7"> — ${esc(src.subtopic)}</span>
    </li>`).join('');

  const rerunBtn = TERMINAL.has(d.status)
    ? `<button class="rerun-btn" data-id="${esc(id_of(entry))}">Re-run this topic</button>` : '';

  return `
    <ol class="timeline">${timeline}</ol>
    <div class="sources-list">
      ${d.sources && d.sources.length ? `<ul>${sources}</ul>` : 'No sources yet.'}
    </div>
    ${rerunBtn}`;
}

function id_of(entry) {
  return entry.summary ? entry.summary.id : '';
}

function srcIcon(status) {
  if (status === 'summarised') return '&#10003;';
  if (status === 'failed') return '&#10007;';
  return '&hellip;';
}

// --------------------------------------------------------------- state ----

async function toggle(id) {
  const entry = jobsById.get(id);
  if (!entry) return;
  entry.expanded = !entry.expanded;
  if (entry.expanded && !entry.detail) {
    try {
      const res = await fetch(`/api/jobs/${id}`);
      entry.detail = await res.json();
    } catch {
      // leave the "Loading…" placeholder up; next SSE snapshot fills it in
    }
  }
  render();
}

function applyEvent(entry, payload) {
  if (payload.type === 'snapshot') {
    entry.detail = payload.job;
    entry.summary = { ...entry.summary, ...payload.job };
    return;
  }
  entry.detail = entry.detail || {};
  if (payload.type === 'stage') {
    entry.summary = { ...entry.summary, status: payload.stage };
    entry.detail.status = payload.stage;
    if (payload.stage === 'published' && payload.wiki_url) {
      entry.summary.wiki_url = payload.wiki_url;
      entry.detail.wiki_url = payload.wiki_url;
    }
  } else if (payload.type === 'failed') {
    entry.summary = { ...entry.summary, status: 'failed', error: payload.error };
    entry.detail.status = 'failed';
    entry.detail.error = payload.error;
  } else if (payload.type === 'sources_found') {
    const c = entry.detail.source_counts || { total: 0, summarised: 0, failed: 0 };
    c.total += payload.count;
    entry.detail.source_counts = c;
  } else if (payload.type === 'source_summarised') {
    const c = entry.detail.source_counts || { total: 0, summarised: 0, failed: 0 };
    c.summarised += 1;
    entry.detail.source_counts = c;
  } else if (payload.type === 'progress') {
    entry.detail.source_counts = {
      ...(entry.detail.source_counts || {}), summarised: payload.done, total: payload.total,
    };
  }
  // 'note_ready', 'source_fetched' etc. don't change summary card state;
  // the timeline/source list catches up from the next full detail fetch.
}

function subscribe(id) {
  const entry = jobsById.get(id);
  if (!entry || entry.es) return;
  const es = new EventSource(`/api/jobs/${id}/events`);
  entry.es = es;

  es.onmessage = (e) => {
    let payload;
    try {
      payload = JSON.parse(e.data);
    } catch {
      return;
    }
    const cur = jobsById.get(id);
    if (!cur) return;
    applyEvent(cur, payload);
    render();
    if (payload.type === 'failed' || TERMINAL.has(payload.stage)) {
      es.close();
      cur.es = null;
      refreshDetail(id);
    }
  };

  es.onerror = () => {
    // EventSource retries on its own; nothing to do here.
  };
}

async function refreshDetail(id) {
  try {
    const res = await fetch(`/api/jobs/${id}`);
    const detail = await res.json();
    const cur = jobsById.get(id) || {};
    cur.detail = detail;
    cur.summary = { ...cur.summary, ...detail };
    jobsById.set(id, cur);
    render();
  } catch {
    // best effort -- the card already shows the terminal status from SSE
  }
}

// ------------------------------------------------------------- actions ----

function rerun(id) {
  const entry = jobsById.get(id);
  if (!entry || !entry.summary) return;
  submitJob(entry.summary.source_url || entry.summary.topic, entry.summary.depth, entry.summary.engine);
}

async function submitJob(topic, depth, engine) {
  submitBtn.disabled = true;
  hintEl.textContent = '';
  hintEl.classList.remove('err');
  const body = isUrl(topic) ? { url: topic, depth, engine } : { topic, depth, engine };
  try {
    const res = await fetch('/api/research', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    const { job_id } = await res.json();
    order.unshift(job_id);
    jobsById.set(job_id, {
      summary: {
        id: job_id,
        topic: isUrl(topic) ? topic : topic,
        source_url: isUrl(topic) ? topic : null,
        depth,
        engine,
        status: 'queued',
        error: null,
        wiki_url: null,
        created_at: new Date().toISOString(),
      },
      expanded: false,
    });
    render();
    subscribe(job_id);
  } catch (e) {
    hintEl.textContent = `Could not start research: ${e.message}`;
    hintEl.classList.add('err');
  } finally {
    submitBtn.disabled = false;
  }
}

form.addEventListener('submit', (e) => {
  e.preventDefault();
  const topic = topicInput.value.trim();
  if (!topic) return;
  submitJob(topic, Number(depthSelect.value), engineSelect.value);
  topicInput.value = '';
});

// --------------------------------------------------------------- boot -----

async function loadEngines() {
  try {
    const res = await fetch('/api/engines');
    engines = await res.json();
  } catch {
    engines = []; // the compose form's engine select is just left empty
  }
  engineSelect.innerHTML = engines.map((e) => {
    const label = e.available ? e.label : `${e.label} (unavailable)`;
    return `<option value="${esc(e.id)}"${e.is_default ? ' selected' : ''}${e.available ? '' : ' disabled'}>${esc(label)}</option>`;
  }).join('');
}

async function loadJobs() {
  try {
    const res = await fetch('/api/jobs');
    const list = await res.json();
    order = list.map((j) => j.id);
    for (const j of list) {
      jobsById.set(j.id, { summary: j, expanded: false });
    }
    render();
    for (const j of list) {
      if (!TERMINAL.has(j.status)) subscribe(j.id);
    }
  } catch (e) {
    hintEl.textContent = `Could not load research history: ${e.message}`;
    hintEl.classList.add('err');
  }
}

loadEngines();
loadJobs();

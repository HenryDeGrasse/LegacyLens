/* ── LegacyLens Web UI ─────────────────────────────────────────── */

// ── HTML Sanitizer (defense against XSS via LLM output) ─────────

function sanitizeHtml(html) {
  // Strip dangerous elements and attributes from marked output
  const el = document.createElement('div');
  el.innerHTML = html;
  // Remove script, iframe, object, embed, form elements
  const dangerous = el.querySelectorAll('script, iframe, object, embed, form, link, meta, base');
  dangerous.forEach(n => n.remove());
  // Remove on* event handler attributes from all elements
  el.querySelectorAll('*').forEach(node => {
    for (const attr of [...node.attributes]) {
      if (attr.name.startsWith('on') || attr.name === 'srcdoc' ||
          (attr.name === 'href' && attr.value.trim().toLowerCase().startsWith('javascript:'))) {
        node.removeAttribute(attr.name);
      }
    }
  });
  return el.innerHTML;
}

// ── State ────────────────────────────────────────────────────────

const state = {
  history: [],
  historyIdx: -1,
  draft: '',
  streaming: false,
  currentAnswer: '',
  abortController: null,
  queryCount: 0,
};

// ── DOM refs ─────────────────────────────────────────────────────

const $ = (sel) => document.querySelector(sel);
const input = $('#query-input');
const answerContent = $('#answer-content');
const treeContainer = $('#tree-container');
const sourceChunks = $('#source-chunks');
const intentBadge = $('#intent-badge');
const stateIndicator = $('#state-indicator');
const statsDisplay = $('#stats-display');
const chunkCount = $('#chunk-count');
const treeActions = $('#tree-actions');

// ── Query submission ─────────────────────────────────────────────

input.addEventListener('keydown', (e) => {
  // Autocomplete takes priority when visible
  if (autocompleteContainer.style.display !== 'none') {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      _acSelected = Math.min(_acSelected + 1, _acItems.length - 1);
      highlightAcItem();
      return;
    } else if (e.key === 'ArrowUp' && _acSelected > 0) {
      e.preventDefault();
      _acSelected--;
      highlightAcItem();
      return;
    } else if (e.key === 'Enter' && _acSelected >= 0) {
      e.preventDefault();
      selectAcItem(_acItems[_acSelected]);
      return;
    } else if (e.key === 'Escape') {
      hideAutocomplete();
      return;
    }
  }

  // Normal input handling
  if (e.key === 'Enter' && !state.streaming) {
    hideAutocomplete();
    const q = input.value.trim();
    if (!q) return;
    addHistory(q);
    submitQuery(q);
    input.value = '';
  } else if (e.key === 'ArrowUp') {
    e.preventDefault();
    navigateHistory(-1);
  } else if (e.key === 'ArrowDown') {
    e.preventDefault();
    navigateHistory(1);
  }
});

function setQuery(q) {
  input.value = q;
  input.focus();
}

// ── History ──────────────────────────────────────────────────────

function addHistory(q) {
  if (state.history.length === 0 || state.history[state.history.length - 1] !== q) {
    state.history.push(q);
  }
  state.historyIdx = -1;
  state.draft = '';
}

function navigateHistory(dir) {
  if (state.history.length === 0) return;

  if (state.historyIdx === -1 && dir === -1) {
    state.draft = input.value;
    state.historyIdx = state.history.length - 1;
  } else if (dir === -1 && state.historyIdx > 0) {
    state.historyIdx--;
  } else if (dir === 1 && state.historyIdx < state.history.length - 1) {
    state.historyIdx++;
  } else if (dir === 1) {
    state.historyIdx = -1;
    input.value = state.draft;
    return;
  }

  input.value = state.history[state.historyIdx];
}

// ── Parse slash commands ─────────────────────────────────────────

function parseSlashCommand(q) {
  const match = q.match(/^\/(explain|deps|impact|metrics)\s+(\S+)/i);
  if (!match) return null;
  return { command: match[1].toLowerCase(), routine: match[2].toUpperCase() };
}

// ── Main query flow ──────────────────────────────────────────────

async function submitQuery(question) {
  const slash = parseSlashCommand(question);

  if (slash && slash.command === 'deps') {
    await fetchDeps(slash.routine);
    return;
  }

  if (slash && slash.command === 'impact') {
    await fetchImpact(slash.routine);
    return;
  }

  if (slash && slash.command === 'metrics') {
    await fetchMetrics(slash.routine);
    return;
  }

  // For /explain, rewrite as a natural language query for the stream endpoint
  const streamQuestion = slash && slash.command === 'explain'
    ? `Explain the routine ${slash.routine}`
    : question;

  setStreaming(true);
  setIntent('');
  setState('SEARCHING...');

  // Show user query
  answerContent.innerHTML = `<div class="user-query">USER&gt; ${escapeHtml(question)}</div><div id="answer-stream" class="streaming-cursor"></div>`;
  sourceChunks.innerHTML = '<div class="source-empty">Retrieving...</div>';

  state.currentAnswer = '';
  state.abortController = new AbortController();
  const startTime = performance.now();

  try {
    const resp = await fetch('/api/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question: streamQuestion }),
      signal: state.abortController.signal,
    });

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let renderTimer = null;
    let pendingTokens = '';

    const flushTokens = () => {
      if (pendingTokens) {
        state.currentAnswer += pendingTokens;
        pendingTokens = '';
        renderAnswer();
      }
    };

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      let eventType = '';
      for (const line of lines) {
        if (line.startsWith('event: ')) {
          eventType = line.slice(7).trim();
        } else if (line.startsWith('data: ') && eventType) {
          const data = JSON.parse(line.slice(6));
          handleEvent(eventType, data, startTime);
          if (eventType === 'token') {
            pendingTokens += data.t;
            if (!renderTimer) {
              renderTimer = setTimeout(() => {
                flushTokens();
                renderTimer = null;
              }, 50);
            }
          }
          eventType = '';
        }
      }
    }

    // Final flush
    if (renderTimer) clearTimeout(renderTimer);
    flushTokens();

  } catch (err) {
    if (err.name !== 'AbortError') {
      showError(err.message);
    }
  }

  const elapsed = performance.now() - startTime;
  statsDisplay.textContent = `${(elapsed / 1000).toFixed(1)}s`;
  setStreaming(false);
  setState('READY');
  incrementQueryCounter();
}

// ── SSE event handlers ───────────────────────────────────────────

function handleEvent(type, data, startTime) {
  switch (type) {
    case 'routing':
      setIntent(data.intent);
      setState('RETRIEVING...');
      // Auto-fetch deps for call graph panel
      if (data.routine_names && data.routine_names.length > 0) {
        fetchDepsForTree(data.routine_names[0]);
      }
      break;

    case 'chunks':
      renderChunks(data);
      setState('GENERATING...');
      break;

    case 'token':
      // Handled in batch by submitQuery
      break;

    case 'done':
      if (data.cached) {
        statsDisplay.textContent = 'cached';
      }
      break;

    case 'error':
      showError(data.message);
      break;
  }
}

// ── Answer rendering ─────────────────────────────────────────────

function renderAnswer() {
  const streamEl = document.getElementById('answer-stream');
  if (!streamEl) return;

  try {
    streamEl.innerHTML = sanitizeHtml(marked.parse(state.currentAnswer));
    streamEl.classList.add('streaming-cursor');
  } catch {
    streamEl.textContent = state.currentAnswer;
  }

  // Auto-scroll
  const panelBody = answerContent.closest('.panel-body');
  if (panelBody) {
    panelBody.scrollTop = panelBody.scrollHeight;
  }
}

function finalizeAnswer() {
  const streamEl = document.getElementById('answer-stream');
  if (streamEl) {
    streamEl.classList.remove('streaming-cursor');
  }
}

// ── Source chunks rendering ──────────────────────────────────────

function renderChunks(chunks) {
  if (!chunks || chunks.length === 0) {
    sourceChunks.innerHTML = '<div class="source-empty">No chunks retrieved.</div>';
    chunkCount.textContent = '';
    return;
  }

  chunkCount.textContent = `${chunks.length} chunks`;
  sourceChunks.innerHTML = chunks.map((c, i) => {
    const scorePercent = Math.round(c.score * 100);
    const codePreview = escapeHtml(c.text || '');
    const isLong = (c.text || '').length > 500;

    return `
      <div class="chunk-card">
        <div class="chunk-header">
          <span class="routine">${escapeHtml(c.routine_name)}</span>
          <span class="chunk-type" data-type="${c.chunk_type}">${c.chunk_type}</span>
          <span class="file-path">${escapeHtml(c.file_path)}:${c.start_line}-${c.end_line}</span>
          <span class="score">${scorePercent}%</span>
        </div>
        <div class="score-bar"><div class="score-bar-fill" style="width:${scorePercent}%"></div></div>
        <div class="chunk-code ${isLong ? 'collapsed' : ''}" id="chunk-code-${i}">${codePreview}</div>
        ${isLong ? `<button class="chunk-toggle" onclick="toggleChunk(${i})">show more</button>` : ''}
      </div>
    `;
  }).join('');
}

function toggleChunk(idx) {
  const el = document.getElementById(`chunk-code-${idx}`);
  const btn = el.nextElementSibling;
  if (el.classList.contains('collapsed')) {
    el.classList.remove('collapsed');
    btn.textContent = 'show less';
  } else {
    el.classList.add('collapsed');
    btn.textContent = 'show more';
  }
}

// ── Call graph tree ──────────────────────────────────────────────

async function fetchDeps(routine) {
  setStreaming(true);
  setState('RESOLVING...');
  setIntent('DEPENDENCY');

  answerContent.innerHTML = `<div class="user-query">USER&gt; /deps ${escapeHtml(routine)}</div><div id="answer-stream">Loading dependencies for <strong>${escapeHtml(routine)}</strong>...</div>`;

  try {
    const resp = await fetch('/dependencies', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ routine_name: routine, depth: 2 }),
    });
    const data = await resp.json();

    if (data.detail) throw new Error(data.detail);

    renderDepsAnswer(routine, data);
    renderTree(routine, data.direct_calls || [], data.all_callers || []);
    treeActions.style.display = '';

  } catch (err) {
    showError(err.message);
  }

  setStreaming(false);
  setState('READY');
  incrementQueryCounter();
}

async function fetchImpact(routine) {
  setStreaming(true);
  setState('CALCULATING...');
  setIntent('IMPACT');

  answerContent.innerHTML = `<div class="user-query">USER&gt; /impact ${escapeHtml(routine)}</div><div id="answer-stream">Analyzing impact of <strong>${escapeHtml(routine)}</strong>...</div>`;

  try {
    const resp = await fetch('/impact', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ routine_name: routine, depth: 2 }),
    });
    const data = await resp.json();

    if (data.detail) throw new Error(data.detail);

    renderImpactAnswer(routine, data);
    renderImpactTree(routine, data.levels || {});
    treeActions.style.display = '';

  } catch (err) {
    showError(err.message);
  }

  setStreaming(false);
  setState('READY');
  incrementQueryCounter();
}

async function fetchDepsForTree(routine) {
  try {
    const resp = await fetch('/dependencies', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ routine_name: routine, depth: 1 }),
    });
    const data = await resp.json();
    if (!data.detail) {
      renderTree(routine, data.direct_calls || [], data.all_callers || []);
      treeActions.style.display = '';
    }
  } catch {
    // Silent fail — tree is supplementary
  }
}

function renderDepsAnswer(routine, data) {
  const directCalls = data.direct_calls || [];
  const callers = data.all_callers || [];

  let md = `## Dependencies: ${routine}\n\n`;
  md += `### Calls (${directCalls.length})\n`;
  md += directCalls.length ? directCalls.map(r => `- \`${r}\``).join('\n') : '- None';
  md += `\n\n### Called by (${callers.length})\n`;
  md += callers.length ? callers.map(r => `- \`${r}\``).join('\n') : '- None';

  const streamEl = document.getElementById('answer-stream');
  if (streamEl) {
    streamEl.innerHTML = sanitizeHtml(marked.parse(md));
    streamEl.classList.remove('streaming-cursor');
  }
}

function renderImpactAnswer(routine, data) {
  const levels = data.levels || {};
  const total = data.total_affected || 0;

  let md = `## Impact Analysis: ${routine}\n\n`;
  md += `**Total affected: ${total} routines**\n\n`;

  for (const [depth, routines] of Object.entries(levels)) {
    md += `### Depth ${depth} (${routines.length})\n`;
    md += routines.slice(0, 20).map(r => `- \`${r}\``).join('\n');
    if (routines.length > 20) md += `\n- _...and ${routines.length - 20} more_`;
    md += '\n\n';
  }

  if (Object.keys(levels).length === 0) {
    md += 'No routines are affected.';
  }

  const streamEl = document.getElementById('answer-stream');
  if (streamEl) {
    streamEl.innerHTML = sanitizeHtml(marked.parse(md));
    streamEl.classList.remove('streaming-cursor');
  }
}

function renderTree(routine, calls, callers) {

  let html = `<div class="tree-node">
    <details open>
      <summary><span class="routine-name">${escapeHtml(routine)}</span></summary>
      <div class="node-content">`;

  // Calls section
  if (calls.length > 0) {
    html += `<div class="tree-node"><details open>
      <summary><span class="category-label">Calls &rarr; (${calls.length})</span></summary>
      <div class="node-content">
        ${calls.map(r => `<div class="tree-leaf" onclick="drillDown('${escapeHtml(r)}')" ondblclick="explainRoutine('${escapeHtml(r)}')"><span class="routine-name">${escapeHtml(r)}</span></div>`).join('')}
      </div></details></div>`;
  }

  // Called by section
  if (callers.length > 0) {
    html += `<div class="tree-node"><details open>
      <summary><span class="category-label">&larr; Called by (${callers.length})</span></summary>
      <div class="node-content">
        ${callers.map(r => `<div class="tree-leaf" onclick="drillDown('${escapeHtml(r)}')" ondblclick="explainRoutine('${escapeHtml(r)}')"><span class="routine-name">${escapeHtml(r)}</span></div>`).join('')}
      </div></details></div>`;
  }

  if (calls.length === 0 && callers.length === 0) {
    html += `<div style="color: var(--text-muted); padding: 4px 16px;">No dependencies found</div>`;
  }

  html += `</div></details></div>`;
  treeContainer.innerHTML = html;
}

function renderImpactTree(routine, affectedByDepth) {
  let html = `<div class="tree-node">
    <details open>
      <summary><span class="routine-name" style="color: var(--accent-red);">Impact: ${escapeHtml(routine)}</span></summary>
      <div class="node-content">`;

  for (const [depth, routines] of Object.entries(affectedByDepth)) {
    html += `<div class="tree-node"><details open>
      <summary><span class="category-label">Depth ${depth} (${routines.length})</span></summary>
      <div class="node-content">
        ${routines.map(r => `<div class="tree-leaf" onclick="drillDown('${escapeHtml(r)}')" ondblclick="explainRoutine('${escapeHtml(r)}')"><span class="routine-name">${escapeHtml(r)}</span></div>`).join('')}
      </div></details></div>`;
  }

  html += `</div></details></div>`;
  treeContainer.innerHTML = html;
}

function drillDown(routine) {
  fetchDeps(routine);
}

function explainRoutine(routine) {
  const q = `/explain ${routine}`;
  addHistory(q);
  submitQuery(q);
}

// ── Metrics ──────────────────────────────────────────────────────

async function fetchMetrics(routine) {
  setStreaming(true);
  setState('ANALYZING...');
  setIntent('METRICS');

  answerContent.innerHTML = `<div class="user-query">USER&gt; /metrics ${escapeHtml(routine)}</div><div id="answer-stream">Computing metrics for <strong>${escapeHtml(routine)}</strong>...</div>`;

  try {
    const resp = await fetch('/metrics', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ routine_name: routine }),
    });
    const data = await resp.json();

    if (data.detail) throw new Error(data.detail);
    if (data.error) throw new Error(data.error);

    let md = `## Metrics: ${data.routine_name}\n\n`;
    md += `**File:** \`${data.file_path}:${data.start_line}-${data.end_line}\`\n\n`;

    md += `### Lines of Code\n`;
    md += `| Metric | Value |\n|---|---|\n`;
    md += `| Total Lines | ${data.loc.total} |\n`;
    md += `| Code Lines | ${data.loc.code} |\n`;
    md += `| Comment Lines | ${data.loc.comment} (${Math.round(data.loc.comment_ratio * 100)}%) |\n`;
    md += `| Blank Lines | ${data.loc.blank} |\n`;
    md += `| Size Rating | **${data.size_rating}** |\n\n`;

    md += `### Complexity\n`;
    md += `| Metric | Value |\n|---|---|\n`;
    md += `| Cyclomatic Complexity | ${data.complexity.cyclomatic} |\n`;
    md += `| Max Nesting Depth | ${data.complexity.max_nesting_depth} |\n`;
    md += `| Complexity Rating | **${data.complexity.rating}** |\n`;
    md += `| Parameters | ${data.parameters} |\n\n`;

    md += `### Dependencies\n`;
    md += `| Metric | Value |\n|---|---|\n`;
    md += `| Unique Calls | ${data.dependencies.calls} |\n`;
    md += `| Unique Callers | ${data.dependencies.callers} |\n`;
    if (data.patterns.length > 0) {
      md += `| Patterns | ${data.patterns.join(', ')} |\n`;
    }

    const streamEl = document.getElementById('answer-stream');
    if (streamEl) {
      streamEl.innerHTML = sanitizeHtml(marked.parse(md));
      streamEl.classList.remove('streaming-cursor');
    }

    // Also fetch deps for the tree panel
    fetchDepsForTree(routine);

  } catch (err) {
    showError(err.message);
  }

  setStreaming(false);
  setState('READY');
  incrementQueryCounter();
}

// ── UI state helpers ─────────────────────────────────────────────

function setStreaming(active) {
  state.streaming = active;
  input.disabled = active;
  if (!active) {
    input.focus();
    finalizeAnswer();
  }
}

function setIntent(intent) {
  intentBadge.textContent = intent || 'READY';
  intentBadge.setAttribute('data-intent', intent);
  intentBadge.classList.toggle('active', !!intent);
}

function setState(s) {
  stateIndicator.textContent = s;
  stateIndicator.classList.toggle('thinking', s !== 'READY');
}

function showError(msg) {
  const streamEl = document.getElementById('answer-stream');
  if (streamEl) {
    streamEl.innerHTML = `<div style="color: var(--accent-red);">Error: ${escapeHtml(msg)}</div>`;
    streamEl.classList.remove('streaming-cursor');
  }
  setState('ERROR');
}

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

// ── Query Counter (hit counter aesthetic) ────────────────────────

function incrementQueryCounter() {
  state.queryCount++;
  const el = document.getElementById('query-counter');
  if (el) {
    el.textContent = `QUERIES: ${String(state.queryCount).padStart(6, '0')}`;
  }
}

// ── Autocomplete ─────────────────────────────────────────────────

const autocompleteContainer = document.createElement('div');
autocompleteContainer.className = 'autocomplete-dropdown';
autocompleteContainer.style.display = 'none';
input.parentElement.style.position = 'relative';
input.parentElement.appendChild(autocompleteContainer);

let _acDebounce = null;
let _acSelected = -1;
let _acItems = [];

input.addEventListener('input', () => {
  clearTimeout(_acDebounce);
  const val = input.value.trim();
  if (val.length < 2 || val.startsWith('/')) {
    hideAutocomplete();
    return;
  }
  _acDebounce = setTimeout(() => fetchAutocomplete(val), 150);
});

async function fetchAutocomplete(query) {
  try {
    const resp = await fetch(`/api/routines?q=${encodeURIComponent(query)}&limit=8`);
    const data = await resp.json();
    if (data.routines && data.routines.length > 0) {
      showAutocomplete(data.routines);
    } else {
      hideAutocomplete();
    }
  } catch { hideAutocomplete(); }
}

function showAutocomplete(items) {
  _acItems = items;
  _acSelected = -1;
  autocompleteContainer.innerHTML = items.map((name, i) =>
    `<div class="ac-item" data-idx="${i}" onmousedown="selectAcItem('${name}')">${escapeHtml(name)}</div>`
  ).join('');
  autocompleteContainer.style.display = 'block';
}

function hideAutocomplete() {
  autocompleteContainer.style.display = 'none';
  _acItems = [];
  _acSelected = -1;
}

function highlightAcItem() {
  autocompleteContainer.querySelectorAll('.ac-item').forEach((el, i) => {
    el.classList.toggle('selected', i === _acSelected);
  });
}

function selectAcItem(name) {
  input.value = `What does ${name} do?`;
  hideAutocomplete();
  input.focus();
}

document.addEventListener('click', (e) => {
  if (!input.parentElement.contains(e.target)) hideAutocomplete();
});

// ── Init ─────────────────────────────────────────────────────────

input.focus();

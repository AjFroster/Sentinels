/* Sentinels desktop shell.
 *
 * No framework and no build step -- the whole point is that this file drops
 * into an Electron or Tauri window unchanged. Everything is server state; the
 * page holds only what is on screen.
 */
'use strict';

const HUES = ['--s1', '--s2', '--s3', '--s4', '--s5'];
const CLASS_HINT = {
  sealed: 'Local bench only. Nothing leaves this machine.',
  open: 'Full council. Cloud members may see this question.',
  redacted: 'Identifiers stripped locally, then escalated.',
};

const $ = (id) => document.getElementById(id);
const thread = $('thread');

let classification = 'sealed';
let running = false;
const hueOf = {}; // member name -> css var, stable for the session
let stream = null;

/* ---------- helpers ---------- */

function hueFor(name) {
  if (!hueOf[name]) hueOf[name] = HUES[Object.keys(hueOf).length % HUES.length];
  return hueOf[name];
}

function initials(name) {
  return name.replace(/[^A-Za-z ]/g, '').split(/\s+/).map((w) => w[0]).join('').slice(0, 2).toUpperCase();
}

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

function scrollDown() {
  thread.scrollTop = thread.scrollHeight;
}

/* ---------- thread rendering ---------- */

function startThread(question, cls) {
  // hueOf is deliberately NOT reset -- a member keeps the same colour for the
  // life of the window, so the bench swatch and the avatar always agree.
  thread.replaceChildren();
  const head = el('div', 'qhead');
  head.append(el('h2', null, question));
  const meta = el('div', 'qmeta');
  meta.append(el('span', `cls ${cls}`, cls));
  meta.append(el('span', 'chip', 'deliberating'));
  head.append(meta);
  thread.append(head);
}

function addStage(n, name) {
  const div = el('div', 'stagediv');
  div.append(el('b', null, `Stage ${n} · ${name}`));
  thread.append(div);
  scrollDown();
}

function addMessage(member, model, text, opts = {}) {
  const msg = el('div', 'msg');
  msg.dataset.member = member;
  if (opts.pending) msg.classList.add('pending');

  const av = el('div', `av${opts.masked ? ' masked' : ''}`, initials(member));
  if (!opts.masked) av.style.background = `var(${hueFor(member)})`;
  msg.append(av);

  const col = el('div');
  const who = el('div', 'who');
  who.append(el('b', null, member));
  if (model) who.append(el('span', 'mono mdl', model));
  col.append(who);
  const body = el('div', `body${opts.pending ? ' dots3' : ''}`, text);
  col.append(body);
  msg.append(col);
  thread.append(msg);
  scrollDown();
  return msg;
}

function resolvePending(member, text) {
  const nodes = [...thread.querySelectorAll('.msg.pending')];
  const node = nodes.find((n) => n.dataset.member === member) || nodes[0];
  if (!node) return addMessage(member, null, text);
  node.classList.remove('pending');
  const body = node.querySelector('.body');
  body.classList.remove('dots3');
  body.textContent = text;
  scrollDown();
  return node;
}

function addNote(text, isError) {
  thread.append(el('div', `note${isError ? ' err' : ''}`, text));
  scrollDown();
}

function bullets(items, empty) {
  const ul = el('ul');
  if (!items || !items.length) {
    ul.append(el('li', null, empty));
  } else {
    items.forEach((i) => ul.append(el('li', null, i)));
  }
  return ul;
}

function section(cls, title, node) {
  const sec = el('div', `bsec ${cls || ''}`.trim());
  sec.append(el('h6', null, title));
  sec.append(node);
  return sec;
}

function renderBrief(record, runId) {
  const b = record.brief;
  const wrap = el('div', 'brief');

  const head = el('div', 'brief-hd');
  head.append(el('b', null, 'Brief'));
  head.append(el('span', 'chip', `${Math.round(record.stages_elapsed_s)}s`));
  head.append(el('span', `cls ${record.classification}`, record.classification));
  const audit = el('button', 'auditbtn', 'Audit');
  audit.type = 'button';
  audit.style.marginLeft = 'auto';
  audit.addEventListener('click', () => openAudit(runId));
  head.append(audit);

  const copy = el('button', 'copy', 'Copy for Claude Code');
  copy.type = 'button';
  copy.addEventListener('click', () => copyBrief(runId, copy));
  head.append(copy);
  wrap.append(head);

  const body = el('div', 'brief-bd');
  body.append(section('', 'Decision', el('p', null, b.decision)));
  body.append(section('', 'Rationale', bullets(b.rationale, 'none recorded')));
  body.append(section('dissent', 'Dissent — on record',
    bullets(b.dissent, 'council was unanimous')));
  body.append(section('', 'Constraints', bullets(b.constraints, 'none recorded')));
  body.append(section('open', 'Open questions',
    bullets(b.open_questions, 'none recorded — treat as a warning, not a clean bill')));
  wrap.append(body);

  wrap.append(el('div', 'disclaim',
    'Model output, not verified fact. An implementer with tools should check any claim before acting.'));

  thread.append(wrap);
  scrollDown();
}

async function copyBrief(runId, button) {
  try {
    const res = await fetch(`/council/briefs/${runId}/markdown`);
    const { markdown } = await res.json();
    await navigator.clipboard.writeText(markdown);
    button.textContent = 'Copied';
    button.classList.add('done');
    setTimeout(() => {
      button.textContent = 'Copy for Claude Code';
      button.classList.remove('done');
    }, 2000);
  } catch (err) {
    button.textContent = 'Copy failed';
  }
}

/* ---------- deliberation ---------- */

function setRunning(state) {
  running = state;
  $('send').disabled = state;
  $('send').textContent = state ? 'Deliberating…' : 'Send to council';
}

async function ask(question) {
  setRunning(true);
  startThread(question, classification);

  let runId;
  try {
    const res = await fetch('/council/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      // No context field: the server uses the saved setting. The client is
      // not responsible for remembering what the project is.
      body: JSON.stringify({ question, classification }),
    });
    if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
    runId = (await res.json()).id;
  } catch (err) {
    addNote(`Could not start deliberation — ${err.message}`, true);
    setRunning(false);
    return;
  }

  stream = new EventSource(`/council/events/${runId}`);

  stream.addEventListener('excluded', (e) => {
    const d = JSON.parse(e.data);
    addNote(`▪ ${d.members.join(', ')} excluded — ${d.reason}`);
    markExcluded(d.members);
  });
  stream.addEventListener('stage', (e) => {
    const d = JSON.parse(e.data);
    addStage(d.n, d.name);
  });
  stream.addEventListener('thinking', (e) => {
    const d = JSON.parse(e.data);
    addMessage(d.member, d.model === 'masked' ? null : d.model, 'thinking',
      { pending: true, masked: d.model === 'masked' });
  });
  stream.addEventListener('opinion', (e) => {
    const d = JSON.parse(e.data);
    resolvePending(d.member, d.text);
  });
  stream.addEventListener('critique', (e) => {
    const d = JSON.parse(e.data);
    resolvePending(d.member, d.text);
  });
  stream.addEventListener('brief', (e) => {
    renderBrief(JSON.parse(e.data), runId);
  });
  stream.addEventListener('error', (e) => {
    if (e.data) addNote(JSON.parse(e.data).message, true);
  });
  stream.addEventListener('done', () => {
    stream.close();
    stream = null;
    setRunning(false);
    loadBriefs();
    refreshStatus();
  });
  stream.onerror = () => {
    if (stream) {
      addNote('Connection to the council was lost.', true);
      stream.close();
      stream = null;
      setRunning(false);
    }
  };
}

/* ---------- sidebar & status ---------- */

function markExcluded(names) {
  document.querySelectorAll('#bench .member').forEach((node) => {
    if (names.includes(node.dataset.name)) node.classList.add('off');
  });
}

async function loadBriefs() {
  try {
    const rows = await (await fetch('/council/briefs')).json();
    const list = $('brief-list');
    if (!rows.length) {
      list.replaceChildren(el('p', 'hint', 'None yet.'));
      return;
    }
    list.replaceChildren();
    rows.forEach((r) => {
      const btn = el('button', 'row');
      const dot = el('span', 'dot');
      dot.style.background = r.classification === 'sealed' ? 'var(--ink-3)'
        : r.classification === 'open' ? 'var(--ok)' : 'var(--dis)';
      btn.append(dot, el('span', 'ttl', r.question));
      btn.addEventListener('click', () => openBrief(r.id));
      list.append(btn);
    });
  } catch { /* sidebar is decoration; never break the app over it */ }
}

async function openBrief(runId, push = true) {
  if (running) return;
  if (push && location.hash !== `#/brief/${runId}`) location.hash = `#/brief/${runId}`;
  try {
    const record = await (await fetch(`/council/briefs/${runId}`)).json();
    startThread(record.question, record.classification);
    thread.querySelector('.qmeta .chip').textContent =
      `${record.members.length} members · ${Math.round(record.stages_elapsed_s)}s`;
    renderBrief(record, runId);
  } catch (err) {
    addNote(`Could not open brief — ${err.message}`, true);
  }
}

async function refreshStatus() {
  try {
    const s = await (await fetch('/council/status')).json();

    $('ollama-state').textContent = s.ollama_reachable ? 'ollama · up' : 'ollama · unreachable';
    $('ollama-state').className = s.ollama_reachable ? '' : 'dead';

    $('loaded-models').textContent = s.loaded.length
      ? s.loaded.map((m) => m.model).join(' · ')
      : 'no model resident';

    const { used_mb: used, total_mb: total } = s.memory;
    const pct = total ? Math.round((used / total) * 100) : 0;
    const bar = $('ram-bar');
    bar.style.width = `${pct}%`;
    bar.classList.toggle('hot', pct >= 85);
    $('ram-text').textContent = `${(used / 1024).toFixed(1)} / ${(total / 1024).toFixed(1)} GB`;

    $('chairman-state').textContent = `chairman · ${s.chairman}`;
    $('ctx-banner').hidden = s.context_set;

    const sent = s.egress_bytes;
    const badge = $('airgap');
    badge.classList.toggle('leaked', sent > 0);
    $('airgap-text').textContent = sent > 0
      ? `${sent.toLocaleString()} BYTES SENT`
      : 'AIRGAPPED · 0 BYTES SENT';

    const bench = $('bench');
    if (!bench.childElementCount) {
      s.bench.forEach((m) => {
        const node = el('div', 'member');
        node.dataset.name = m.name;
        const sw = el('span', 'sw');
        sw.style.background = `var(${hueFor(m.name)})`;
        node.append(sw, el('span', null, m.name), el('span', 'mono mdl', ''));
        node.lastChild.textContent = m.model;
        node.lastChild.style.cssText = 'margin-left:auto;font-size:.6rem';
        bench.append(node);
      });
    }
  } catch { /* status bar is ambient; a failed poll is not an error state */ }
}

/* ---------- audit ---------- */

/* The hash chain is the whole privacy argument made checkable. Showing it as a
   claim without letting anyone verify it would be theatre. */
async function openAudit(runId) {
  const dialog = $('audit-dlg');
  const rows = $('audit-rows');
  const verdict = $('audit-verdict');
  rows.replaceChildren();
  verdict.textContent = 'Checking…';
  verdict.className = 'fhelp';
  dialog.showModal();

  try {
    const [events, check] = await Promise.all([
      (await fetch(`/council/briefs/${runId}/events`)).json(),
      (await fetch(`/council/briefs/${runId}/verify`)).json(),
    ]);

    verdict.textContent = check.ok
      ? `Chain intact — ${check.checked} entries verified.`
      : `Chain broken — ${check.reason}`;
    verdict.className = check.ok ? 'fhelp verdict-ok' : 'fhelp verdict-bad';

    events.forEach((e) => {
      const tr = document.createElement('tr');
      const who = e.data.member || e.data.name
        || (e.data.members ? e.data.members.join(', ') : '');
      tr.append(
        el('td', 'hash', String(e.seq)),
        el('td', 'ev', e.event),
        el('td', 'who', who),
        el('td', 'hash', new Date(e.at).toLocaleTimeString()),
        el('td', 'hash', `${e.hash.slice(0, 10)}…`),
      );
      rows.append(tr);
    });
  } catch (err) {
    verdict.textContent = `Could not read the audit trail — ${err.message}`;
    verdict.className = 'fhelp verdict-bad';
  }
}

/* ---------- settings ---------- */

const dlg = $('settings-dlg');
let starterContext = '';
let defaultClass = 'sealed';
let installedModels = [];
let bench = [];
let chairman = null;

function modelSelect(value) {
  const sel = el('select');
  const options = installedModels.includes(value)
    ? installedModels
    : [value, ...installedModels];   // keep a model Ollama no longer has
  options.forEach((m) => {
    const opt = el('option', null, m);
    opt.value = m;
    if (m === value) opt.selected = true;
    sel.append(opt);
  });
  return sel;
}

/* One distinct model across the bench is the weak case, so say so where the
   choice is being made rather than in a doc nobody reads. */
function renderDiversity() {
  const distinct = new Set(bench.map((m) => m.model)).size;
  const node = $('diversity');
  node.textContent = `${bench.length} members · ${distinct} distinct `
    + `model${distinct === 1 ? '' : 's'}`;
  node.classList.toggle('thin', distinct < 2);
  if (distinct < 2) node.textContent += ' — they will mostly agree';
}

function memberRow(member, onRemove) {
  const row = el('div', 'brow');

  const name = el('input');
  name.value = member.name;
  name.maxLength = 40;
  name.addEventListener('input', () => { member.name = name.value; });
  row.append(name);

  const model = modelSelect(member.model);
  model.addEventListener('change', () => { member.model = model.value; renderDiversity(); });
  row.append(model);

  if (onRemove) {
    const rm = el('button', 'rm', '\u00d7');
    rm.type = 'button';
    rm.title = 'Remove member';
    rm.disabled = bench.length <= 2;
    rm.addEventListener('click', () => onRemove(member));
    row.append(rm);
  } else {
    const cloud = el('label', 'cloud');
    const box = el('input');
    box.type = 'checkbox';
    box.checked = member.is_cloud;
    box.addEventListener('change', () => { member.is_cloud = box.checked; });
    cloud.append(box, el('span', null, 'cloud'));
    row.append(cloud);
  }

  const persona = el('input', 'persona');
  persona.value = member.persona;
  persona.maxLength = 300;
  persona.placeholder = 'How this member should behave';
  persona.addEventListener('input', () => { member.persona = persona.value; });
  row.append(persona);

  return row;
}

function renderBench() {
  const rows = $('bench-rows');
  rows.replaceChildren();
  bench.forEach((m) => rows.append(memberRow(m, (target) => {
    if (bench.length <= 2) return;
    bench = bench.filter((x) => x !== target);
    renderBench();
  })));
  $('chair-row').replaceChildren(memberRow(chairman, null));
  $('add-member').disabled = bench.length >= 7;
  renderDiversity();
}

function selectSeg(container, value) {
  [...container.children].forEach((b) => b.classList.toggle('on', b.dataset.v === value));
}

function setClassification(value) {
  classification = value;
  selectSeg($('classification'), value);
  $('cls-hint').textContent = CLASS_HINT[value];
}

async function openSettings() {
  try {
    const [settings, meta, models] = await Promise.all([
      (await fetch('/council/settings')).json(),
      (await fetch('/council/settings/meta')).json(),
      (await fetch('/council/models')).json(),
    ]);
    installedModels = models.installed;
    bench = settings.bench.map((m) => ({ ...m }));
    chairman = { ...settings.chairman };
    renderBench();
    starterContext = meta.starter_context;
    $('ctx-input').value = settings.context;
    $('cfg-path').textContent = meta.path;
    defaultClass = settings.default_classification;
    selectSeg($('default-cls'), defaultClass);
    updateCount();
    dlg.showModal();
    $('ctx-input').focus();
  } catch (err) {
    addNote(`Could not load settings — ${err.message}`, true);
  }
}

function updateCount() {
  const n = $('ctx-input').value.length;
  $('ctx-count').textContent = n;
  $('ctx-count').parentElement.classList.toggle('over', n > 4000);
}

async function saveSettings() {
  const button = $('save-settings');
  button.disabled = true;
  try {
    const res = await fetch('/council/settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        context: $('ctx-input').value,
        default_classification: defaultClass,
        bench,
        chairman,
      }),
    });
    if (!res.ok) {
      const detail = await res.json().catch(() => null);
      // Pydantic reports the readable half in the first error's msg.
      throw new Error(detail?.detail?.[0]?.msg || `${res.status}`);
    }
    dlg.close();
    setClassification(defaultClass);
    $('bench').replaceChildren();   // rebuilt from the new settings
    refreshStatus();
  } catch (err) {
    addNote(`Could not save settings — ${err.message}`, true);
  } finally {
    button.disabled = false;
  }
}

$('settings-btn').addEventListener('click', openSettings);
$('ctx-fix').addEventListener('click', openSettings);
$('ctx-input').addEventListener('input', updateCount);
$('save-settings').addEventListener('click', saveSettings);
$('ctx-starter').addEventListener('click', () => {
  $('ctx-input').value = starterContext;
  updateCount();
  $('ctx-input').focus();
});
$('add-member').addEventListener('click', () => {
  if (bench.length >= 7) return;
  bench.push({
    name: `Member ${bench.length + 1}`,
    model: installedModels[0] || 'qwen2.5:1.5b',
    persona: 'a member who weighs the question on its merits',
    is_cloud: false,
  });
  renderBench();
});

$('default-cls').addEventListener('click', (e) => {
  const btn = e.target.closest('button[data-v]');
  if (!btn) return;
  defaultClass = btn.dataset.v;
  selectSeg($('default-cls'), defaultClass);
});

/* ---------- wiring ---------- */

$('classification').addEventListener('click', (e) => {
  const btn = e.target.closest('button[data-v]');
  if (btn) setClassification(btn.dataset.v);
});

$('composer').addEventListener('submit', (e) => {
  e.preventDefault();
  const box = $('question');
  const text = box.value.trim();
  if (text.length < 8 || running) return;
  box.value = '';
  ask(text);
});

$('question').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
    e.preventDefault();
    $('composer').requestSubmit();
  }
});

$('new-btn').addEventListener('click', () => $('question').focus());

document.addEventListener('keydown', (e) => {
  if (e.key.toLowerCase() === 'k' && (e.ctrlKey || e.metaKey) && e.shiftKey) {
    e.preventDefault();
    $('question').focus();
  }
});

/* Deep links. A deliberation is worth pointing someone at -- and in a desktop
   shell it is how the tray and the sidebar reopen one without re-running it. */
function routeFromHash() {
  if (location.hash === '#/settings') { openSettings(); return; }
  const audit = location.hash.match(/^#\/audit\/([a-z0-9]+)$/i);
  if (audit) { openBrief(audit[1], false).then(() => openAudit(audit[1])); return; }
  const match = location.hash.match(/^#\/brief\/([a-z0-9]+)$/i);
  if (match) openBrief(match[1], false);
}

window.addEventListener('hashchange', routeFromHash);

(async () => {
  try {
    const settings = await (await fetch('/council/settings')).json();
    defaultClass = settings.default_classification;
    setClassification(defaultClass);
  } catch { /* defaults are already correct in the markup */ }
})();

/* ---------- desktop shell ---------- */

/* window.sentinels only exists under Electron. Everything here is additive so
   the identical document still works in a plain browser tab. */
const desktop = window.sentinels;
if (desktop?.isDesktop) {
  document.body.classList.add('desktop');

  $('lights').addEventListener('click', (e) => {
    const action = e.target.closest('button[data-win]')?.dataset.win;
    if (action === 'close') desktop.close();
    else if (action === 'minimize') desktop.minimize();
    else if (action === 'maximize') desktop.maximize();
  });

  desktop.onFocusComposer(() => $('question').focus());
  desktop.onBackendDown(() => {
    addNote('The local council backend stopped. Restart Sentinels to bring it back.', true);
    $('ollama-state').textContent = 'backend down';
    $('ollama-state').className = 'dead';
  });
}

refreshStatus();
loadBriefs().then(routeFromHash);
setInterval(refreshStatus, 5000);

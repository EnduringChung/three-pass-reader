/* Writer: contenteditable + toolbar + autosave + citations + non-generative checks. */
const $ = s => document.querySelector(s);
const api = (p, opt) => fetch(p, opt).then(r => r.json());
async function send(p, method, body){
  const r = await fetch(p, {method, headers: body === undefined ? undefined : {'Content-Type': 'application/json'},
    body: body === undefined ? undefined : JSON.stringify(body)});
  if (!r.ok) throw new Error(`${method} ${p} → HTTP ${r.status}`);
  return r.json().catch(() => ({}));
}
const jput = (p, body) => send(p, 'PUT', body);
const jpost = (p, body) => send(p, 'POST', body);

let DRAFT = null;                 // current draft id
let docs = [];
let pending = null, pendingTimer = null, dirty = false;
let citeStyle = localStorage.getItem('wr-cite-style') || 'author-year';

const docEl = $('#doc'), titleEl = $('#title');

/* ---------- save queue ---------- */
function chip(txt, bad){
  const c = $('#chip');
  c.textContent = txt;
  c.style.color = bad ? 'var(--orange)' : '';
}
function scheduleSave(){
  dirty = true;
  clearTimeout(pendingTimer);
  pendingTimer = setTimeout(saveNow, 700);
}
async function saveNow(){
  if (!DRAFT || !dirty) return;
  dirty = false;
  const html = docEl.innerHTML, title = titleEl.value || 'Untitled';
  try {
    await jput(`/api/drafts/${DRAFT}`, {html, title});
    const w = countWords(docEl.innerText);
    $('#words').textContent = `${w} words`;
    chip('Saved ' + new Date().toLocaleTimeString());
    loadDocs();
  } catch (e){
    dirty = true;                             // keep the text pending; never claim saved
    chip('⚠ not saved — retrying', true);
    setTimeout(saveNow, 2000);
  }
}
function flush(){
  clearTimeout(pendingTimer);
  if (dirty) return saveNow();
  return null;
}
addEventListener('pagehide', () => { if (dirty && DRAFT){
  // keepalive so a refresh inside the debounce window does not lose the last edit
  try { navigator.sendBeacon(`/api/drafts/${DRAFT}/__noop`); } catch (e) {}
  fetch(`/api/drafts/${DRAFT}`, {method: 'PUT', keepalive: true,
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({html: docEl.innerHTML, title: titleEl.value})});
}});
document.addEventListener('visibilitychange', () => { if (document.hidden) flush(); });

/* ---------- documents list ---------- */
async function loadDocs(){
  docs = await api('/api/drafts');
  const box = $('#docs');
  box.querySelectorAll('.doc').forEach(n => n.remove());
  for (const d of docs){
    const el = document.createElement('div');
    el.className = 'doc' + (d.id === DRAFT ? ' on' : '');
    el.innerHTML = `<span class="t">${escapeHtml(d.title || 'Untitled')}</span>
      <span class="muted" style="font-size:11px">${d.n_chars || 0} chars · ${(d.updated_at || '').slice(5, 16)}</span>
      ${d.source_name ? `<span class="muted" style="font-size:10.5px;display:block">from ${escapeHtml(d.source_name)}</span>` : ''}`;
    el.onclick = () => { flush(); openDraft(d.id); };
    const del = document.createElement('button');
    del.textContent = '✕'; del.title = 'delete this document';
    del.className = 'ref-star';
    del.style.cssText = 'float:right;margin-top:-18px;color:var(--muted);padding:0 4px';
    del.onclick = async (ev) => {
      ev.stopPropagation();
      const msg = d.source_name
        ? `Remove “${d.title || 'Untitled'}” from the writer?\n\nYour file ${d.source_name} stays on disk, untouched.`
        : `Delete “${d.title || 'Untitled'}”? This cannot be undone.`;
      if (!confirm(msg)) return;
      try {
        await send(`/api/drafts/${d.id}`, 'DELETE');
        if (d.id === DRAFT){ DRAFT = null; docEl.innerHTML = ''; titleEl.value = ''; }
        await loadDocs();
        if (!docs.length) $('#btn-new').click();
        else if (!DRAFT) await openDraft(docs[0].id);
      } catch (e){ chip('delete failed', true); }
    };
    el.appendChild(del);
    box.appendChild(el);
  }
}
async function openDraft(id){
  DRAFT = id;
  const d = await api(`/api/drafts/${id}`);
  if (d.detail){ chip('document not found', true); return; }
  docEl.innerHTML = d.html || '';
  titleEl.value = d.title || '';
  $('#words').textContent = `${countWords(docEl.innerText)} words`;
  $('#path').textContent = `draft #${d.id}`;
  localStorage.setItem('wr-last', String(id));
  await loadDocs();
  hideIssues();
  labelLinks();
  if (EMBED){                         // mounted beside the PDF: typing should just work
    docEl.focus();
    const r = document.createRange();
    r.selectNodeContents(docEl); r.collapse(true);
    const s = getSelection(); s.removeAllRanges(); s.addRange(r);
  }
}
$('#btn-new').onclick = async () => {
  const d = await jpost('/api/drafts', {title: 'Untitled document', work_id: new URLSearchParams(location.search).get('work') ?
    parseInt(new URLSearchParams(location.search).get('work')) : null});
  await loadDocs();
  await openDraft(d.id);
  docEl.focus();                        // type into the body straight away
  const r = document.createRange(); r.selectNodeContents(docEl); r.collapse(true);
  const s = getSelection(); s.removeAllRanges(); s.addRange(r);
};
titleEl.oninput = scheduleSave;
titleEl.onblur = () => flush();

/* ---------- toolbar ---------- */
document.querySelectorAll('#toolbar [data-cmd]').forEach(b => {
  b.onmousedown = e => e.preventDefault();          // keep the selection
  b.onclick = () => {
    docEl.focus();
    const arg = b.dataset.arg;
    document.execCommand(b.dataset.cmd, false, arg || null);
    scheduleSave(); refreshToolbar();
  };
});
function refreshToolbar(){
  document.querySelectorAll('#toolbar [data-cmd]').forEach(b => {
    const cmd = b.dataset.cmd;
    let on = false;
    try { on = document.queryCommandState(cmd); } catch (e) {}
    b.classList.toggle('on', !!on);
  });
}
document.addEventListener('selectionchange', () => { if (document.activeElement === docEl) refreshToolbar(); });
docEl.addEventListener('input', scheduleSave);
docEl.addEventListener('blur', () => flush());
docEl.addEventListener('keydown', e => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's'){ e.preventDefault(); flush(); }
});
docEl.addEventListener('paste', e => {
  // paste as plain paragraphs: Word/Google Docs HTML never reaches the sanitiser in the first place
  e.preventDefault();
  const text = (e.clipboardData || window.clipboardData).getData('text/plain') || '';
  const html = text.split(/\n{2,}/).map(p => `<p>${escapeHtml(p).replace(/\n/g, '<br>')}</p>`).join('');
  document.execCommand('insertHTML', false, html);
  scheduleSave();
});

/* ---------- images ---------- */
$('#btn-img').onclick = () => $('#imgfile').click();
$('#imgfile').onchange = async e => {
  const f = e.target.files[0];
  if (!f || !DRAFT) return;
  const fd = new FormData();
  fd.append('file', f);
  chip('uploading image…');
  try {
    const r = await fetch(`/api/drafts/${DRAFT}/images`, {method: 'POST', body: fd});
    if (!r.ok){ const j = await r.json().catch(() => ({})); throw new Error(j.detail || `HTTP ${r.status}`); }
    const {url} = await r.json();
    docEl.focus();
    insertFigure(url);
    scheduleSave();
    chip('image added');
  } catch (err){
    chip('image failed: ' + err.message, true);
  }
  e.target.value = '';
};


/* A <figure> must not be inserted INSIDE a <p> — the HTML parser hoists it out and the image is
   lost (observed: a figure with a caption and no <img>). Split the paragraph and insert the
   figure between the two halves instead. */
function insertFigure(url){
  const fig = document.createElement('figure');
  const img = document.createElement('img');
  img.src = url; img.alt = '';
  const cap = document.createElement('figcaption');
  cap.textContent = 'Caption…';
  fig.append(img, cap);

  const sel = getSelection();
  const rng = sel.rangeCount ? sel.getRangeAt(0) : null;
  if (!rng || !docEl.contains(rng.startContainer)){
    docEl.appendChild(fig);
    focusCaption(cap);
    return;
  }
  let host = rng.startContainer;
  if (host.nodeType === 3) host = host.parentNode;
  while (host && host.parentNode !== docEl) host = host.parentNode;

  if (!host || (host.tagName !== 'P' && host.tagName !== 'DIV')){
    (host ? host.parentNode : docEl).insertBefore(fig, host ? host.nextSibling : null);
    focusCaption(cap);
    return;
  }
  // split the text around the caret into a second paragraph
  const after = document.createRange();
  after.setStart(rng.startContainer, rng.startOffset);
  after.setEndAfter(host.lastChild || host);
  const tail = after.extractContents();
  const p2 = document.createElement('p');
  p2.appendChild(tail);
  host.parentNode.insertBefore(fig, host.nextSibling);
  if (p2.textContent.trim() || p2.querySelector('img')){
    host.parentNode.insertBefore(p2, fig.nextSibling);
  }
  focusCaption(cap);
}

function focusCaption(cap){
  const r = document.createRange();
  r.selectNodeContents(cap);
  const s = getSelection();
  s.removeAllRanges(); s.addRange(r);
}

/* ---------- links ---------- */
function normaliseUrl(raw){
  const v = (raw || '').trim();
  if (!v) return '';
  if (/^(https?:|mailto:|#)/i.test(v)) return v;
  return 'https://' + v;
}

function linkAtCaret(){
  const sel = getSelection();
  if (!sel.rangeCount) return null;
  let node = sel.getRangeAt(0).startContainer;
  if (node.nodeType === 3) node = node.parentNode;
  return node && node.closest ? node.closest('#doc a[href]') : null;
}

$('#btn-link').onclick = () => {
  const existing = linkAtCaret();
  const answer = prompt(existing ? 'Link URL (leave empty to remove the link):' : 'Link URL:',
                        existing ? existing.getAttribute('href') : 'https://');
  if (answer === null) return;
  const url = normaliseUrl(answer);
  if (existing){
    if (!url){
      const a = existing;
      while (a.firstChild) a.parentNode.insertBefore(a.firstChild, a);
      a.remove();
    } else {
      existing.setAttribute('href', url);
    }
  } else {
    const sel = getSelection();
    if (!sel.rangeCount || sel.isCollapsed){
      chip('select the words to link first', true);
      return;
    }
    document.execCommand('createLink', false, url);
    for (const a of docEl.querySelectorAll('a[href]')) a.title = 'Ctrl+click to open ' + a.getAttribute('href');
  }
  scheduleSave();
  chip('link saved');
};

$('#btn-openlink').onclick = () => {
  const a = linkAtCaret();
  if (!a){ chip('cursor is not inside a link', true); return; }
  window.open(a.getAttribute('href'), '_blank', 'noopener');
};

/* A contenteditable swallows clicks on <a>, so navigation has to be explicit: Ctrl (or Cmd) + click.
   Plain click keeps placing the caret so the text stays editable. */
docEl.addEventListener('click', (ev) => {
  const a = ev.target.closest ? ev.target.closest('#doc a[href]') : null;
  if (!a) return;
  if (ev.ctrlKey || ev.metaKey){
    ev.preventDefault();
    ev.stopPropagation();
    window.open(a.getAttribute('href'), '_blank', 'noopener');
  }
}, true);

function labelLinks(){
  for (const a of docEl.querySelectorAll('a[href]')){
    a.title = 'Ctrl+click to open ' + a.getAttribute('href');
  }
}

/* ---------- import .docx ---------- */
$('#btn-import').onclick = () => $('#docxfile').click();
$('#docxfile').onchange = async (ev) => {
  const f = ev.target.files[0];
  ev.target.value = '';                       // picking the same file twice must still fire
  if (!f) return;
  chip('importing…');
  const fd = new FormData();
  fd.append('file', f, f.name);
  try {
    const r = await fetch('/api/drafts/import', {method: 'POST', body: fd});
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || 'import failed');
    await loadDocs();
    await openDraft(d.id);
    const s = d.stats || {};
    chip(`imported ${s.paragraphs||0}¶ · ${s.headings||0} headings · ${s.tables||0} tables · ${s.images||0} images`);
  } catch (e){ chip('import failed: ' + e.message, true); }
};

/* ---------- citations ---------- */
let lastRange = null;
docEl.addEventListener('mouseup', rememberRange);
docEl.addEventListener('keyup', rememberRange);
function rememberRange(){
  const s = getSelection();
  if (s.rangeCount && docEl.contains(s.anchorNode)) lastRange = s.getRangeAt(0).cloneRange();
}
function surname(authors){
  // "Abbott NJ" → "Abbott"; "S.J. Lee" → "Lee"; "van den Berg" → "van den Berg"; "Smith, J." → "Smith"
  const first = (authors || '').split(',')[0].trim();
  const toks = first.split(/\s+/).filter(Boolean);
  while (toks.length > 1 && /^[A-Z]{1,3}\.?$/.test(toks[toks.length - 1])) toks.pop();   // trailing initials
  while (toks.length > 1 && /^(?:[A-Z]\.){1,3}$/.test(toks[0])) toks.shift();            // leading initials
  if (toks.length === 1 && /^(?:[A-Z]\.){1,3}$/.test(toks[0])) return first;
  return toks.join(' ') || first;
}
function citeText(c){
  if (citeStyle === 'numeric') return '';           // filled by renumber()
  if (c.authors) return `(${surname(c.authors)}${(c.authors.split(',').length > 1) ? ' et al.' : ''}, ${c.year || 'n.d.'})`;
  if (c.title) return `(${c.title.slice(0, 40)}, ${c.year || 'n.d.'})`;
  return `(${(c.raw || 'reference').slice(0, 40)}, ${c.year || 'n.d.'})`;
}
$('#btn-style').onclick = () => {
  citeStyle = citeStyle === 'author-year' ? 'numeric' : 'author-year';
  localStorage.setItem('wr-cite-style', citeStyle);
  $('#btn-style').textContent = citeStyle === 'author-year' ? '(Author, year)' : '[1] numeric';
  renumber();
  scheduleSave();
};
function renumber(){
  const cites = [...docEl.querySelectorAll('cite')];
  cites.forEach((c, i) => {
    if (citeStyle === 'numeric'){
      c.textContent = `[${i + 1}]`;
    } else {
      const authors = c.dataset.authors || '';
      const year = c.dataset.year || '';
      const title = c.dataset.title || '';
      if (authors){
        c.textContent = `(${surname(authors)}${authors.split(',').length > 1 ? ' et al.' : ''}, ${year || 'n.d.'})`;
      } else {
        c.textContent = `(${title.slice(0, 40)}, ${year || 'n.d.'})`;
      }
    }
    c.dataset.style = citeStyle;
  });
}

$('#btn-cite').onclick = async () => {
  rememberRange();
  const box = $('#citebox');
  box.style.display = box.style.display === 'flex' ? 'none' : 'flex';
  if (box.style.display === 'flex'){ $('#citeq').focus(); await searchCitations(''); }
};
$('#citeq').oninput = e => searchCitations(e.target.value);
async function searchCitations(q){
  const rows = await api(`/api/citations?q=${encodeURIComponent(q)}&limit=40`);
  const box = $('#citeresults');
  box.innerHTML = '';
  for (const r of rows){
    const el = document.createElement('div');
    el.className = 'ci';
    const head = r.authors ? `${surname(r.authors)} ${r.year || ''}`.trim()
                          : (r.title || '').slice(0, 50);
    // untitled references (Elsevier numeric style) are identifiable only by their raw string,
    // so show it wrapped in full instead of a truncated title that is often empty
    const detail = r.raw || r.title || r.source || '';
    el.innerHTML = `<div><b>${escapeHtml(head)}</b>
        ${r.doi ? `<span class="muted" style="font-size:11px">· ${escapeHtml(r.doi)}</span>` : ''}</div>
      <div style="font-size:11.5px;line-height:1.4;margin-top:3px">${escapeHtml(detail.slice(0, 300))}</div>
      ${r.source ? `<div class="muted" style="font-size:10.5px;margin-top:2px">cited in: ${escapeHtml(r.source.slice(0, 60))}</div>` : ''}`;
    el.onclick = () => insertCitation(r);
    box.appendChild(el);
  }
  if (!rows.length) box.innerHTML = '<div class="ci muted">No matches — extract references on some papers first.</div>';
}
function insertCitation(r){
  docEl.focus();
  const sel = getSelection();
  if (lastRange){ sel.removeAllRanges(); sel.addRange(lastRange); }
  const span = document.createElement('cite');
  span.dataset.ref = r.ref_id || '';
  span.dataset.work = r.work_id || '';
  span.dataset.authors = r.authors || '';
  span.dataset.year = r.year || '';
  span.dataset.title = r.title || '';
  span.dataset.raw = r.raw || '';
  if (r.raw) span.title = r.raw;          // hover shows what the citation actually refers to
  span.dataset.style = citeStyle;
  span.textContent = citeStyle === 'numeric' ? '[1]' : citeText(r);
  const rng = sel.rangeCount ? sel.getRangeAt(0) : null;
  if (rng){
    rng.deleteContents();
    rng.insertNode(span);
    rng.setStartAfter(span); rng.collapse(true);
    sel.removeAllRanges(); sel.addRange(rng);
  } else {
    docEl.appendChild(span);
  }
  renumber();
  $('#citebox').style.display = 'none';
  scheduleSave();
}

/* ---------- bibliography ---------- */
$('#btn-bib').onclick = async () => {
  const ids = [...new Set([...docEl.querySelectorAll('cite')]
    .map(c => c.dataset.ref).filter(Boolean))];
  const rows = ids.length ? await api(`/api/refs?ids=${ids.join(',')}`) : [];
  const byId = Object.fromEntries(rows.map(r => [String(r.id), r]));
  const entries = [...new Set([...docEl.querySelectorAll('cite')].map(c => {
    const r = byId[c.dataset.ref];
    if (r) return `${r.raw}${r.doi ? ' https://doi.org/' + r.doi : ''}`;
    return `${c.dataset.authors || c.dataset.title || 'reference'}`;
  }))].sort();
  docEl.querySelectorAll('[data-generated="bib"]').forEach(n => n.remove());
  const h = document.createElement('h2');
  h.textContent = 'References';
  h.dataset.generated = 'bib';
  const ul = document.createElement('ul');
  ul.dataset.generated = 'bib';
  entries.forEach(e => { const li = document.createElement('li'); li.textContent = e; ul.appendChild(li); });
  docEl.append(h, ul);
  scheduleSave();
  chip(`${entries.length} references listed`);
};

/* ---------- checks: flags only, never replacement prose ---------- */
const HEDGES = /\b(may|might|could|seems?|appears?|suggests?|possibly|likely|perhaps|presumably)\b/gi;
const WEASEL = /\b(very|quite|rather|significant|importantly|novel|robust|clearly|obviously)\b/gi;
const PASSIVE = /\b(is|are|was|were|be|been|being)\s+(\w+ed|\w+en)\b/gi;

function textNodesWords(){
  // [{word, start}] over the editor's text, for issue → selection mapping
  const out = [];
  let i = 0;
  (function walk(node){
    if (node.nodeType === 3){
      const parts = node.nodeValue.split(/(\s+)/);
      for (const p of parts){
        if (!/\s/.test(p) && p) out.push({word: p, node, index: i, offset: 0});
        i += p.length;
      }
    } else if (node.nodeType === 1) { for (const c of node.childNodes) walk(c); }
  })(docEl);
  return out;
}

function analyse(){
  const text = docEl.innerText || '';
  const issues = [];
  const paras = text.split(/\n+/).filter(p => p.trim());
  let pIdx = 0;
  for (const p of paras){
    pIdx++;
    const words = p.split(/\s+/).filter(Boolean);
    if (words.length > 40) issues.push({k: 'long sentence', d: `${words.length} words: “${p.slice(0, 60)}…”`, p: pIdx});
    const passive = p.match(PASSIVE) || [];
    if (passive.length) issues.push({k: 'passive voice', d: `${passive.length}× in paragraph ${pIdx}: ${passive.slice(0, 3).join(', ')}`, p: pIdx});
    const hedges = p.match(HEDGES) || [];
    if (hedges.length >= 2) issues.push({k: 'hedging', d: `${hedges.length}× (${[...new Set(hedges.map(h => h.toLowerCase()))].join(', ')})`, p: pIdx});
    const weasel = p.match(WEASEL) || [];
    if (weasel.length >= 2) issues.push({k: 'weasel words', d: `${[...new Set(weasel.map(w => w.toLowerCase()))].join(', ')}`, p: pIdx});
    const counts = {};
    for (const w of words.map(w => w.toLowerCase().replace(/[^a-z-]/g, ''))){
      if (w.length < 4 || ['that', 'with', 'this', 'these', 'were', 'have', 'from', 'which'].includes(w)) continue;
      counts[w] = (counts[w] || 0) + 1;
    }
    const reps = Object.entries(counts).filter(([, n]) => n >= 4);
    if (reps.length) issues.push({k: 'repeated word', d: reps.map(([w, n]) => `${w} ×${n}`).join(', '), p: pIdx});
  }
  const allWords = text.split(/\s+/).filter(Boolean);
  const ly = allWords.filter(w => /ly$/i.test(w)).length;
  if (allWords.length > 80 && ly / allWords.length > 0.06)
    issues.push({k: 'adverb density', d: `${Math.round(100 * ly / allWords.length)}% -ly words`});
  const stems = {};
  for (const w of allWords.map(x => x.toLowerCase().replace(/[^a-z]/g, ''))){
    if (w.length < 6) continue;
    const stem = w.replace(/i[sz]ation$/, 'iz').replace(/isation$/, 'iz').replace(/ization$/, 'iz')
                  .replace(/[sz]e$/, 'z').replace(/e$/, '');
    stems[stem] = stems[stem] || new Set();
    stems[stem].add(w);
  }
  for (const [stem, set] of Object.entries(stems)){
    if (set.size > 1 && stem.length > 5)
      issues.push({k: 'inconsistent spelling', d: [...set].join(' / ')});
  }
  return issues;
}

$('#btn-check').onclick = () => {
  const issues = analyse();
  const box = $('#issues');
  box.innerHTML = '';
  if (!issues.length){
    box.innerHTML = '<div class="muted">Nothing flagged. (Checks are mechanical: they cannot judge whether the argument is right.)</div>';
    box.style.display = 'block';
    return;
  }
  box.innerHTML = `<div class="muted" style="margin-bottom:6px">${issues.length} flag(s) — what to look at, not what to write.</div>`;
  for (const it of issues){
    const el = document.createElement('div');
    el.className = 'iss';
    el.innerHTML = `<b>${it.k}</b> — ${escapeHtml(it.d)}`;
    box.appendChild(el);
  }
  box.style.display = 'block';
};
function hideIssues(){ $('#issues').style.display = 'none'; }

/* ---------- export ---------- */
$('#btn-export').onclick = async () => {
  if (!DRAFT) return;
  await flush();
  try {
    const r = await jpost(`/api/drafts/${DRAFT}/export`, {});
    chip(`exported → ${r.markdown}`, false);
    $('#path').textContent = r.markdown;
  } catch (e){ chip('export failed', true); }
};

/* ---------- helpers ---------- */
function countWords(t){ return (t || '').split(/\s+/).filter(Boolean).length; }
function escapeHtml(s){
  return String(s == null ? '' : s).replace(/[&<>"']/g,
    c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
}

/* ---------- embed mode (reader's Write tab) ---------- */
const EMBED = new URLSearchParams(location.search).get('embed') === '1';
if (EMBED) document.body.classList.add('embed');

/* ---------- boot ---------- */
(async () => {
  $('#btn-style').textContent = citeStyle === 'author-year' ? '(Author, year)' : '[1] numeric';
  await loadDocs();
  const qid = new URLSearchParams(location.search).get('id');
  if (qid){
    // an explicit id wins even when the document is not in this list — scratch pads (kind='scratch')
    // are deliberately excluded from `docs`, and resolving the id against the list silently opened
    // the first unrelated document instead of the pad.
    await openDraft(parseInt(qid, 10));
  } else {
    const last = localStorage.getItem('wr-last');
    if (last && docs.some(d => String(d.id) === String(last))) await openDraft(parseInt(last));
    else if (docs.length) await openDraft(docs[0].id);
  }
  if (!qid && !docs.length){
    const d = await jpost('/api/drafts', {title: 'Untitled document'});
    await loadDocs(); await openDraft(d.id);
  }
  addEventListener('beforeunload', () => { if (dirty) flush(); });
})();

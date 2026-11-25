/* Three-Pass Reader — reader logic (Phase 1) */
pdfjsLib.GlobalWorkerOptions.workerSrc = '/static/pdf.js/pdf.worker.min.js';
const $ = s => document.querySelector(s);
const api = (p, opt) => fetch(p, opt).then(r => r.json());
let unloading = false;                       // set on pagehide so in-flight saves survive the unload
async function send(p, method, body){
  const r = await fetch(p, {method, keepalive: unloading,
    headers: body === undefined ? undefined : {'Content-Type': 'application/json'},
    body: body === undefined ? undefined : JSON.stringify(body)});
  if (!r.ok) throw new Error(`${method} ${p} → HTTP ${r.status}`);   // never report a failed write as saved
  return r.json().catch(() => ({}));
}
const jpost = (p, body) => send(p, 'POST', body);
const jput  = (p, body) => send(p, 'PUT', body);

const params = new URLSearchParams(location.search);
let WORK = parseInt(params.get('work'));
let file_id = null, pdfDoc = null, pageNum = 1, workRow = null;
let splitDir = localStorage.getItem(`tr-${WORK}-dir`) || 'h';
let splitRev = localStorage.getItem(`tr-${WORK}-rev`) === '1';
let activeTab = 'pass2';
let passTemplates = {};

/* ---------- save-status chip + save queue ---------- */
function chip(state){
  const el = $('#savechip'); if (!el) return;
  el.style.color = state === 'error' ? 'var(--orange)' : '';
  if (state === 'saving') el.textContent = 'Saving…';
  else if (state === 'saved') el.textContent = 'Saved ' + new Date().toLocaleTimeString();
  else if (state === 'error') el.textContent = '⚠ not saved — retrying';
}

const pendingSaves = new Map();          // key -> {fn, timer, tries}
function autosave(key, fn, ms=600){
  const prev = pendingSaves.get(key);
  if (prev) clearTimeout(prev.timer);
  const job = { fn, timer: null, tries: prev ? prev.tries : 0 };
  job.timer = setTimeout(() => flushSave(key), ms);
  pendingSaves.set(key, job);
  chip('saving');
}
async function flushSave(key){
  const job = pendingSaves.get(key); if (!job) return;
  clearTimeout(job.timer); pendingSaves.delete(key);
  try {
    await job.fn();
    chip(pendingSaves.size ? 'saving' : 'saved');
  } catch (err){
    console.error('save failed:', key, err);
    const tries = (job.tries || 0) + 1;
    pendingSaves.set(key, { fn: job.fn, timer: null, tries });
    chip('error');
    if (tries <= 3) setTimeout(() => flushSave(key), 2000 * tries);
  }
}
async function flushAll(){
  await Promise.all([...pendingSaves.keys()].map(flushSave));
  return pendingSaves.size === 0;
}
/* a refresh/tab switch must not swallow the last few hundred ms of typing */
addEventListener('pagehide', () => { unloading = true; flushAll(); });
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'hidden') flushAll();
});
$('#btn-save').onclick = async () => {
  const btn = $('#btn-save');
  btn.disabled = true;
  const ok = await flushAll();
  btn.disabled = false;
  chip(ok ? 'saved' : 'error');
  if (ok){ btn.textContent = 'Saved ✓'; setTimeout(() => btn.textContent = 'Save', 1200); }
};
addEventListener('keydown', e => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's'){ e.preventDefault(); $('#btn-save').click(); }
});

/* ---------- split pane ---------- */
function restoreSplit(){
  const pdf = $('#pdfpane');
  pdf.style.width = ''; pdf.style.height = ''; pdf.style.flex = '';
  if (matchMedia('(max-width:719px)').matches) return;   // stacked on phones: full width
  const v = localStorage.getItem(`tr-${WORK}-split-${splitDir}`)
        || localStorage.getItem(`tr-${WORK}-split`);     // legacy single key
  if (v){ if (splitDir === 'h') pdf.style.width = v; else pdf.style.height = v; pdf.style.flex = 'none'; }
}
function applyLayout(){
  $('#split').className = splitDir + (splitRev ? ' rev' : '');
  $('#btn-orient').textContent = splitDir === 'h' ? (splitRev ? '⇆' : '⇄') : '⇅';
}
function setLayout(d, rev){
  const dirChanged = d !== splitDir;
  splitDir = d; splitRev = !!rev;
  applyLayout();
  localStorage.setItem(`tr-${WORK}-dir`, d);
  localStorage.setItem(`tr-${WORK}-rev`, splitRev ? '1' : '0');
  if (dirChanged) restoreSplit();     // px height is meaningless in the other orientation
  requestAnimationFrame(()=>refitPages(true));
}

/* layout menu behind the ⇄ button */
let layoutMenuOff = null;
function closeLayoutMenu(){
  const m = $('#layoutmenu'); if (m) m.remove();
  if (layoutMenuOff){ layoutMenuOff(); layoutMenuOff = null; }
}
function toggleLayoutMenu(){
  if ($('#layoutmenu')){ closeLayoutMenu(); return; }
  const btn = $('#btn-orient'), r = btn.getBoundingClientRect();
  const m = document.createElement('div');
  m.id = 'layoutmenu';
  m.style.top = (r.bottom + 6) + 'px';
  m.style.left = Math.max(8, Math.min(r.left, innerWidth - 228)) + 'px';
  [['h', false, 'PDF left · notes right'],
   ['h', true,  'Notes left · PDF right'],
   ['v', false, 'PDF top · notes below'],
   ['v', true,  'Notes top · PDF below']].forEach(([d, rev, label])=>{
    const on = d === splitDir && rev === !!splitRev;
    const o = document.createElement('div');
    o.className = 'opt' + (on ? ' on' : '');
    o.textContent = (on ? '✓ ' : '') + label;
    o.onclick = ()=>{ setLayout(d, rev); closeLayoutMenu(); };
    m.appendChild(o);
  });
  document.body.appendChild(m);
  setTimeout(()=>{
    const outside = ev=>{ if (!m.contains(ev.target)) closeLayoutMenu(); };
    const esc = ev=>{ if (ev.key === 'Escape') closeLayoutMenu(); };
    document.addEventListener('pointerdown', outside);
    document.addEventListener('keydown', esc);
    layoutMenuOff = ()=>{ document.removeEventListener('pointerdown', outside); document.removeEventListener('keydown', esc); };
  }, 0);
}
$('#btn-orient').onclick = (e)=>{ e.stopPropagation(); toggleLayoutMenu(); };

(function initDivider(){
  const divider = $('#divider'), split = $('#split'), pdf = $('#pdfpane'), notes = $('#notespane');
  let dragging = false;
  divider.addEventListener('pointerdown', e=>{ dragging = true; divider.setPointerCapture(e.pointerId); });
  divider.addEventListener('pointerup', ()=> dragging = false);
  divider.addEventListener('pointermove', e=>{
    if (!dragging) return;
    const r = split.getBoundingClientRect();
    // reversed panes: the PDF sits on the far side of the divider, so measure from that edge
    if (splitDir === 'h'){
      let w = splitRev ? r.right - e.clientX : e.clientX - r.left;
      w = Math.min(Math.max(w, r.width*0.2), r.width*0.8);
      pdf.style.width = w + 'px'; pdf.style.flex = 'none';
      localStorage.setItem(`tr-${WORK}-split-h`, pdf.style.width);
    } else {
      let h = splitRev ? r.bottom - e.clientY : e.clientY - r.top;
      h = Math.min(Math.max(h, r.height*0.2), r.height*0.8);
      pdf.style.height = h + 'px'; pdf.style.flex = 'none';
      localStorage.setItem(`tr-${WORK}-split-v`, pdf.style.height);
    }
    refitPages();
  });
  restoreSplit();
  // independent scroll restore (done after PDF loads; skipped when deep-linking to an anchor)
  for (const [id] of [['pdfpane'],['notespane']]){
    const el = $('#'+id), k = `tr-${WORK}-scroll-${id}`;
    el._scrollKey = k;
    el.addEventListener('scroll', ()=>{ localStorage.setItem(k, el.scrollTop); if (id==='pdfpane') renderVisible(); });
  }
})();

/* phone tabs */
function setPhoneTab(t){
  document.body.classList.toggle('phone-notes', t==='notes');
  document.body.classList.toggle('phone-pdf', t==='pdf');
  $('#pt-pdf').classList.toggle('on', t==='pdf');
  $('#pt-notes').classList.toggle('on', t==='notes');
  if (t === 'pdf') requestAnimationFrame(()=>refitPages());  // pane width was 0 while hidden
}
$('#pt-pdf').onclick = ()=>setPhoneTab('pdf');
$('#pt-notes').onclick = ()=>setPhoneTab('notes');
if (matchMedia('(max-width:719px)').matches) setPhoneTab('pdf');
addEventListener('resize', ()=>{
  if (matchMedia('(max-width:719px)').matches) restoreSplit();  // drop a desktop px split on phones
  refitPages();
});

/* ---------- pdf rendering (lazy) ---------- */
async function loadPdf(){
  const papers = await api('/api/papers');
  const w = papers.find(p=>p.id===WORK);
  if (!w){ $('#wtitle').textContent='Work not found'; return; }
  $('#wtitle').textContent = w.title;
  loadStar();
  workRow = w;
  file_id = w.file_id;
  pdfDoc = await pdfjsLib.getDocument(`/pdf/${file_id}`).promise;
  const pane = $('#pdfpane');
  for (let i=1; i<=pdfDoc.numPages; i++){
    const page = await pdfDoc.getPage(i);
    const vp = page.getViewport({scale: 1});
    const box = document.createElement('div');
    box.className = 'pagebox'; box.dataset.page = i;
    box._page = page; box._vp1 = vp;
    box.innerHTML = `<span class="muted pgno">p. ${i}</span>`;
    pane.appendChild(box);
  }
  layoutBoxes();
  $('#pageinfo').textContent = `${pdfDoc.numPages} pages`;
  // deep-link anchor
  const anchorId = params.get('anchor');
  if (anchorId){
    const cards = await api(`/api/works/${WORK}/cards`);
    for (const c of cards) for (const a of c.anchors) if (a.id==anchorId){
      jumpToPage(a.page);
    }
  } else {
    // restore device scroll position only when not jumping to an anchor
    for (const id of ['pdfpane','notespane']){
      const el = $('#'+id), v = localStorage.getItem(el._scrollKey);
      if (v) el.scrollTop = parseInt(v);
    }
  }
  await loadHighlights();
  renderVisible();
}
function jumpToPage(n){
  const box = document.querySelector(`.pagebox[data-page='${n}']`);
  if (box) box.scrollIntoView({block:'start'});
}

/* ---------- zoom / fit ---------- */
const ZOOM_KEY = 'tr-zoom';
const MIN_ZOOM = 0.5, MAX_ZOOM = 6, ZOOM_STEP = 1.25;
let zoom = parseFloat(localStorage.getItem(ZOOM_KEY)) || 1;
const pageBoxes = ()=> [...$('#pdfpane').querySelectorAll('.pagebox')];

function fitBaseScale(){
  const pane = $('#pdfpane');
  const vp1 = pageBoxes()[0]?._vp1;
  if (!pane.clientWidth || !vp1) return 1;
  const gutter = Math.min(32, Math.max(6, pane.clientWidth * 0.04));
  return Math.max(0.05, (pane.clientWidth - gutter) / vp1.width);
}
function layoutBoxes(){
  const s = fitBaseScale() * zoom;
  lastPaneW = $('#pdfpane').clientWidth;
  for (const b of pageBoxes()){
    b.style.width  = Math.round(b._vp1.width  * s) + 'px';
    b.style.height = Math.round(b._vp1.height * s) + 'px';
  }
}
function syncZoomLabels(){
  const pct = Math.round(zoom * 100) + '%';
  document.querySelectorAll('.zoomlabel').forEach(el => el.textContent = pct);
}
function invalidateBoxes(){
  for (const b of pageBoxes()){
    if (b._canvas){ b._canvas.remove(); b._canvas = null; }
    b._rendered = false;
  }
}
function setZoom(z, opts = {}){
  z = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, z));
  if (Math.abs(z - zoom) < 0.005) return;
  const pane = $('#pdfpane');
  // keep the reading position: same page, same fraction down it, same horizontal slice
  let anchor = null;
  if (opts.keepAnchor !== false && pane.clientHeight && pdfDoc){
    const top = pane.scrollTop;
    for (const b of pageBoxes()){
      if (b.offsetTop + b.offsetHeight > top + 1){
        anchor = { b,
          frac: Math.min(1, Math.max(0, (top - b.offsetTop) / Math.max(1, b.offsetHeight))),
          fracX: (pane.scrollLeft + pane.clientWidth/2 - b.offsetLeft) / Math.max(1, b.offsetWidth) };
        break;
      }
    }
  }
  zoom = z;
  localStorage.setItem(ZOOM_KEY, String(zoom));
  syncZoomLabels();
  invalidateBoxes();
  layoutBoxes();
  if (anchor){
    pane.scrollTop = anchor.b.offsetTop + anchor.frac * anchor.b.offsetHeight;
    pane.scrollLeft = Math.max(0, anchor.b.offsetLeft + anchor.fracX * anchor.b.offsetWidth - pane.clientWidth/2);
  }
  renderVisible();
}
document.querySelectorAll('[data-zoom]').forEach(btn => btn.onclick = ()=>{
  const a = btn.dataset.zoom;
  if (a === 'in') setZoom(zoom * ZOOM_STEP);
  else if (a === 'out') setZoom(zoom / ZOOM_STEP);
  else setZoom(1);
});
addEventListener('keydown', e=>{
  if (['INPUT','TEXTAREA','SELECT'].includes(e.target.tagName)) return;
  if (e.key === '+' || e.key === '=') { e.preventDefault(); setZoom(zoom * ZOOM_STEP); }
  else if (e.key === '-' || e.key === '_') { e.preventDefault(); setZoom(zoom / ZOOM_STEP); }
  else if (e.key === '0') { e.preventDefault(); setZoom(1); }
});
$('#pdfpane').addEventListener('wheel', e=>{
  if (!(e.ctrlKey || e.metaKey)) return;   // trackpad pinch / ctrl+scroll
  e.preventDefault();
  setZoom(zoom * (e.deltaY < 0 ? 1.1 : 1/1.1));
}, {passive:false});

/* re-fit the pages when the pane changes width (window resize, divider drag, notes
   pane reflowing at boot) */
let refitTimer = null, lastPaneW = 0;
function refitPages(force){
  if (!pdfDoc) return;
  const w = $('#pdfpane').clientWidth;
  if (!w) return;                       // pane hidden (phone Notes tab)
  if (!force && w === lastPaneW) return;
  layoutBoxes();
  clearTimeout(refitTimer);
  refitTimer = setTimeout(()=>{ invalidateBoxes(); renderVisible(); }, 220);
}
if (window.ResizeObserver) new ResizeObserver(()=>refitPages()).observe($('#pdfpane'));

async function renderVisible(){
  const pane = $('#pdfpane'); if (!pdfDoc || !pane.clientWidth) return;
  const pr = pane.getBoundingClientRect();
  for (const box of pane.querySelectorAll('.pagebox')){
    const br = box.getBoundingClientRect();
    const visible = br.bottom > pr.top - 600 && br.top < pr.bottom + 600;
    if (visible && !box._rendered){
      box._rendered = true;
      const scale = box.clientWidth / box._page.getViewport({scale:1}).width;
      if (!(scale > 0)) { box._rendered = false; continue; }
      const vp = box._page.getViewport({scale});
      // cap the backing store: keeps memory sane on phones at high zoom
      const ratio = Math.min(window.devicePixelRatio || 1, 2, 4096 / vp.width);
      const canvas = document.createElement('canvas');
      canvas.width = Math.floor(vp.width * ratio); canvas.height = Math.floor(vp.height * ratio);
      canvas.style.width = '100%'; canvas.style.height = '100%';
      box.prepend(canvas);
      await box._page.render({canvasContext: canvas.getContext('2d'), viewport: vp,
        transform: ratio !== 1 ? [ratio,0,0,ratio,0,0] : null}).promise;
      box._canvas = canvas;
      await buildTextLayer(box);
      const cur = Math.abs(br.top - pr.top) < br.height;
      if (cur) pageNum = parseInt(box.dataset.page);
    }
  }
}

/* ---------- snapshot crop ---------- */
let snapMode = false, snapRect = null, snapOverlay = null, snapPane = null;
$('#btn-snap').onclick = toggleSnap;
addEventListener('keydown', e=>{
  if (e.key === 's' && e.target.tagName !== 'TEXTAREA' && e.target.tagName !== 'INPUT') toggleSnap();
  if (e.key === 'Escape' && snapMode) toggleSnap();
});
function toggleSnap(){
  snapMode = !snapMode;
  $('#btn-snap').style.borderColor = snapMode ? 'var(--accent)' : '';
  $('#btn-snap').textContent = snapMode ? '✂ click a figure… (Esc cancels)' : '✂ Snapshot';
  if (!snapMode && snapOverlay){ snapOverlay.remove(); snapOverlay = null; }
}
document.addEventListener('pointerdown', e=>{
  if (!snapMode) return;
  const box = e.target.closest('.pagebox');
  if (!box || !box._canvas) return;
  e.preventDefault();
  snapPane = box;
  snapOverlay = document.createElement('div');
  snapOverlay.className = 'crop-overlay';
  box.appendChild(snapOverlay);
  const br = box.getBoundingClientRect();
  const x0 = e.clientX, y0 = e.clientY;
  const move = ev=>{
    snapOverlay.style.left = Math.min(x0,ev.clientX)-br.left + 'px';
    snapOverlay.style.top = Math.min(y0,ev.clientY)-br.top + 'px';
    snapOverlay.style.width = Math.abs(ev.clientX-x0)+'px';
    snapOverlay.style.height = Math.abs(ev.clientY-y0)+'px';
  };
  const up = async ev=>{
    document.removeEventListener('pointermove', move);
    document.removeEventListener('pointerup', up);
    const r = snapOverlay.getBoundingClientRect();
    if (r.width < 12 || r.height < 12){ snapOverlay.remove(); snapOverlay=null; return; }
    await doCrop(box, box.getBoundingClientRect(), r);
    snapOverlay.remove(); snapOverlay = null;
  };
  document.addEventListener('pointermove', move);
  document.addEventListener('pointerup', up);
});
async function doCrop(box, boxR, rectR){
  const canvas = box._canvas;
  const sx = (rectR.left - boxR.left) / boxR.width * canvas.width;
  const sy = (rectR.top - boxR.top) / boxR.height * canvas.height;
  const sw = rectR.width / boxR.width * canvas.width;
  const sh = rectR.height / boxR.height * canvas.height;
  const out = document.createElement('canvas');
  out.width = sw; out.height = sh;
  out.getContext('2d').drawImage(canvas, sx, sy, sw, sh, 0, 0, sw, sh);
  const data_url = out.toDataURL('image/png');
  const frac = [ (rectR.left-boxR.left)/boxR.width, (rectR.top-boxR.top)/boxR.height,
                 (rectR.right-boxR.left)/boxR.width, (rectR.bottom-boxR.top)/boxR.height ];
  const res = await jpost('/api/capture', {file_id, page: parseInt(box.dataset.page), rect: frac, data_url});
  await attachAnchorToCard(res.anchor_id, box.dataset.page);
  toggleSnap();
}
async function attachAnchorToCard(anchorId, page){
  let cid = currentCardId;
  if (!cid){
    cid = (await jpost(`/api/works/${WORK}/cards`, {pass: activeTab, status:'seed'})).id;
    currentCardId = cid;
    await loadCards();
  }
  await api(`/api/cards/${cid}/anchors`, {method:'PUT', headers:{'Content-Type':'application/json'}, body: JSON.stringify({anchor_id: anchorId})});
  await loadCards();
}

/* ---------- notes pane: templates + answers ---------- */
let answers = {};       // qkey -> updated_at (server truth)
let currentCardId = null;

document.querySelectorAll('#tabs button').forEach(b=> b.onclick = ()=> switchTab(b.dataset.tab));
async function switchTab(t){
  await flushAll();                 // write out the current tab before swapping the form
  activeTab = t;
  document.querySelectorAll('#tabs button').forEach(b=> b.classList.toggle('on', b.dataset.tab===t));
  await renderTab();
  if (t === 'write' || t === 'scratch'){   // an editor fills the pane; a stale scroll hides its top
    const p = $('#notespane');
    p.scrollTop = 0;
    requestAnimationFrame(() => { p.scrollTop = 0; });
  }
}
async function loadAnswers(pass){
  answers = await api(`/api/works/${WORK}/answers/${pass}`);
}
function saveAnswer(key, pass, qkey, getValue){
  autosave(key, () => jput(`/api/works/${WORK}/answers/${pass}`, {qkey, value: getValue()}));
}
function fieldEl(f, pass){
  const d = document.createElement('div'); d.className='field';
  d.innerHTML = `<label>${f.label}</label><div class="hint">${f.hint||''}</div>`;
  const v = answers[f.qkey] ? answers[f.qkey].value : '';
  const key = `a-${pass}-${f.qkey}`;
  if (f.type === 'text'){
    const i = document.createElement('input'); i.value = v;
    i.oninput = ()=> saveAnswer(key, pass, f.qkey, ()=> i.value);
    i.onblur  = ()=> flushSave(key);      // leaving the field saves immediately
    d.appendChild(i);
  } else {
    const t = document.createElement('textarea'); t.value = v;
    t.oninput = ()=> saveAnswer(key, pass, f.qkey, ()=> t.value);
    t.onblur  = ()=> flushSave(key);
    d.appendChild(t);
  }
  return d;
}
async function renderTab(){
  await loadAnswers(activeTab);
  const body = $('#tabbody'); body.innerHTML = '';
  if (activeTab === 'scratch'){ body.appendChild(await scratchTabSection()); return; }
  if (activeTab === 'refs'){ body.appendChild(await refsTabSection()); return; }
  if (activeTab === 'write'){ body.appendChild(await writeTabSection()); return; }
  const tpl = passTemplates[activeTab];
  const h = document.createElement('h3'); h.style.margin='0 0 12px'; h.textContent = tpl.core_prompt;
  body.appendChild(h);

  if ((tpl.todos||[]).length){
    const d = document.createElement('div'); d.className='field'; d.style.opacity='.85';
    d.innerHTML = `<label>Pass routine</label>` +
      tpl.todos.map((t,i)=>`<div class="muted" style="margin:2px 0">${i+1}. ${t}</div>`).join('');
    body.appendChild(d);
  }

  if (activeTab === 'pass1' && (tpl.verdict || []).length) body.appendChild(verdictSection());
  if (activeTab === 'pass2'){ body.appendChild(cardSection()); await loadCards(); }
  if (activeTab === 'pass3'){ body.appendChild(resolveSection()); await loadResolve(); }

  (tpl.fields||[]).filter(f=>f.core!==false).forEach(f=> body.appendChild(fieldEl(f, activeTab)));
  if ((tpl.more_fields||[]).length){
    const btn = document.createElement('button'); btn.className='more-toggle';
    btn.textContent = '▸ full prompts (5 Cs etc.)';
    const wrap = document.createElement('div'); wrap.style.display='none';
    (tpl.more_fields||[]).forEach(f=> wrap.appendChild(fieldEl(f, activeTab)));
    btn.onclick = ()=>{ wrap.style.display = wrap.style.display==='none'?'block':'none';
      btn.textContent = wrap.style.display==='none' ? '▸ full prompts (5 Cs etc.)' : '▾ full prompts'; };
    body.appendChild(btn); body.appendChild(wrap);
  }
  (tpl.fields||[]).filter(f=>f.core===false).forEach(f=> body.appendChild(fieldEl(f, activeTab)));

  if ((tpl.lists||[]).length) tpl.lists.forEach(k=> body.appendChild(listSection(activeTab, k)));
  if (tpl.checklist){
    const d = document.createElement('div'); d.className='field';
    d.innerHTML = `<label>Checklist (optional)</label>` + tpl.checklist.map(c=>`<div class="muted">□ ${c}</div>`).join('');
    body.appendChild(d);
  }
  if (tpl.note){
    const d = document.createElement('div'); d.className='field';
    d.innerHTML = `<div class="muted" style="font-style:italic">${tpl.note}</div>`;
    body.appendChild(d);
  }
}

/* ---------- cards ---------- */
function cardSection(){
  const wrap = document.createElement('div');
  const head = document.createElement('div'); head.className='row'; head.style.marginBottom='10px';
  head.innerHTML = `<strong>Evidence cards</strong> <span class="muted">— seed one line, grow later</span>`;
  const add = document.createElement('button'); add.textContent='+ New card'; add.className='primary';
  add.style.marginLeft='auto';
  add.onclick = async ()=>{
    const r = await jpost(`/api/works/${WORK}/cards`, {pass:'pass2', status:'seed'});
    currentCardId = r.id; await loadCards();
  };
  head.appendChild(add); wrap.appendChild(head);
  const stream = document.createElement('div'); stream.id='cardstream'; wrap.appendChild(stream);
  wrap._load = loadCards;
  return wrap;
}
async function loadCards(){
  const stream = $('#cardstream'); if (!stream) return;
  const cards = await api(`/api/works/${WORK}/cards?pass_name=pass2`);
  stream.innerHTML = '';
  for (const c of cards) stream.appendChild(cardEl(c));
}
function cardEl(c){
  const d = document.createElement('div'); d.className='card'; d.dataset.id = c.id;
  const th = document.createElement('div'); th.className='thumbs';
  for (const a of c.anchors){
    const img = document.createElement('img'); img.src = `/api/anchors/${a.id}/image`; img.title = `p.${a.page}`;
    img.onclick = ()=>{ $('#lightbox').style.display='flex'; $('#lbimg').src = img.src; };
    img.ondblclick = ()=> jumpToPage(a.page);
    const sp = document.createElement('span'); sp.className='chip'; sp.textContent = `p.${a.page}`;
    const cell = document.createElement('span'); cell.style.position='relative'; cell.appendChild(img);
    cell.appendChild(sp); th.appendChild(cell);
  }
  d.appendChild(th);
  const mk = (label, key, hint)=>{
    const f = document.createElement('div'); f.className='field';
    f.innerHTML = `<label style="font-size:12px">${label}</label>${hint?`<div class="hint">${hint}</div>`:''}`;
    const t = document.createElement('textarea'); t.value = c[key]||'';
    const ckey = `c-${c.id}-${key}`;
    t.oninput = ()=> autosave(ckey, ()=> send(`/api/cards/${c.id}`, 'PATCH', {[key]: t.value}));
    t.onblur  = ()=> flushSave(ckey);
    f.appendChild(t); d.appendChild(f);
  };
  mk("Observation — what does the data show? (before the authors' claims)", 'observation',
     passTemplates.pass2.card_hints.observation);
  mk('Interpretation — what do you think it means?', 'interpretation');
  mk('Open question?', 'question', passTemplates.pass2.card_hints.question);

  const row = document.createElement('div'); row.className='row';
  const st = document.createElement('span'); st.className='chip '+c.status; st.textContent=c.status;
  st.onclick = async ()=>{  // cycle status
    const order = ['seed','open','resolved','parked'];
    const next = order[(order.indexOf(c.status)+1)%order.length];
    await api(`/api/cards/${c.id}`, {method:'PATCH', headers:{'Content-Type':'application/json'}, body: JSON.stringify({status: next})});
    st.className='chip '+next; st.textContent=next;
  };
  st.title='click to cycle status';
  const linkBtn = document.createElement('button'); linkBtn.textContent='🔗 Link card';
  linkBtn.onclick = ()=> cardPicker(c.id);
  const del = document.createElement('button'); del.textContent='🗑';
  del.onclick = async ()=>{ await api(`/api/cards/${c.id}`, {method:'DELETE'}); await loadCards(); };
  row.append(st, linkBtn, del);
  d.appendChild(row);

  const links = document.createElement('div'); links.className='linksbox'; links.id=`links-${c.id}`;
  d.appendChild(links);
  d._refreshLinks = async ()=>{
    const l = await api(`/api/cards/${c.id}/links`);
    links.innerHTML = '';
    const line = (arr, lbl)=>{ if(!arr.length) return;
      const h = document.createElement('div'); h.innerHTML = `<span class="muted">${lbl}</span>`;
      arr.forEach(x=>{ const a=document.createElement('div'); a.className='li';
        const txt = (x.question||x.observation||'(card)').slice(0,70);
        a.innerHTML = `<a href="/reader?work=${x.work_id}&anchor=${c.id===x.id?'':x.id}">${x.title}</a>: ${txt}`;
        h.appendChild(a); });
      links.appendChild(h); };
    line(l.links, '→ links to'); line(l.backlinks, '← linked from');
    if (l.links.length || l.backlinks.length) d._links = l;
  };
  d._refreshLinks();
  return d;
}
async function cardPicker(cardId){
  const q = prompt('Search for a card to link (words to remember it by):');
  if (!q) return;
  const opts = await api(`/api/picker?q=${encodeURIComponent(q)}`);
  const stream = $('#cardstream');
  const box = document.createElement('div'); box.className='picker'; box.style.position='fixed';
  box.style.top='80px'; box.style.left='50%'; box.style.transform='translateX(-50%)';
  box.innerHTML = opts.length ? '' : '<div class="opt muted">No cards match.</div>';
  for (const o of opts.filter(o=>o.id!==cardId)){
    const el = document.createElement('div'); el.className='opt';
    el.innerHTML = `<strong>${o.title}</strong> <span class="muted">${(o.question||o.observation||'').slice(0,60)}</span>`;
    el.onclick = async ()=>{
      await jpost(`/api/cards/${cardId}/links`, {dst_id: o.id});
      box.remove(); await loadCards();
    };
    box.appendChild(el);
  }
  const close = document.createElement('div'); close.className='opt muted'; close.textContent='✕ close';
  close.onclick = ()=>box.remove(); box.appendChild(close);
  document.body.appendChild(box);
}
async function refreshAllLinkBoxes(){
  for (const el of document.querySelectorAll('.card')) if (el._refreshLinks) await el._refreshLinks();
}

/* ---------- pass3: resolve cards ---------- */
function resolveSection(){
  const wrap = document.createElement('div');
  wrap.innerHTML = `<strong>Open cards to resolve</strong> <span class="muted">— resolution is kept separately, your original interpretation stays untouched</span><div id="resolve" style="margin-top:8px"></div>`;
  return wrap;
}
async function loadResolve(){
  const el = $('#resolve'); if (!el) return;
  const cards = await api(`/api/works/${WORK}/cards`);
  el.innerHTML = '';
  for (const c of cards.filter(c=>['open','parked'].includes(c.status))){
    const d = document.createElement('div'); d.className='card';
    d.innerHTML = `<span class="chip ${c.status}">${c.status}</span>
      <div class="muted" style="margin:4px 0">${(c.question||c.observation||'').slice(0,120)}</div>`;
    const th = document.createElement('div'); th.className='thumbs';
    for (const a of c.anchors){
      const img = document.createElement('img'); img.src=`/api/anchors/${a.id}/image`;
      img.onclick = ()=>{ $('#lightbox').style.display='flex'; $('#lbimg').src=img.src; };
      th.appendChild(img);
    }
    d.appendChild(th);
    const t = document.createElement('textarea'); t.placeholder='What you verified — resolution';
    t.value = c.resolution||'';
    const rkey = `c-${c.id}-res`;
    t.oninput = ()=> autosave(rkey, ()=> send(`/api/cards/${c.id}`, 'PATCH', {resolution: t.value, status: t.value ? 'resolved' : c.status}));
    t.onblur  = ()=> flushSave(rkey);
    d.appendChild(t);
    el.appendChild(d);
  }
  if (!el.children.length) el.innerHTML = '<p class="muted">No open cards. Park questions in Pass 2.</p>';
}

/* ---------- lists ---------- */
function listSection(pass, kind){
  const d = document.createElement('div'); d.className='field';
  d.innerHTML = `<label>${kind.replace(/_/g,' ')}</label><div id="list-${kind}"></div>`;
  const row = document.createElement('div'); row.className='list-row';
  const inp = document.createElement('input'); inp.placeholder='Add… (Enter)';
  inp.onkeydown = async e=>{ if (e.key==='Enter' && inp.value.trim()){
    await jpost(`/api/works/${WORK}/items/${pass}/${kind}`, {value: inp.value.trim()});
    inp.value=''; loadList(pass, kind, d.querySelector(`#list-${kind}`)); }};
  row.appendChild(inp); d.appendChild(row);
  const el = d.querySelector(`#list-${kind}`);
  loadList(pass, kind, el);
  return d;
}
async function loadList(pass, kind, el){
  const items = await api(`/api/works/${WORK}/items/${pass}/${kind}`);
  el.innerHTML = '';
  for (const it of items){
    const r = document.createElement('div'); r.className='list-row';
    r.innerHTML = `<input type="checkbox" ${it.done?'checked':''}><span style="flex:1">${it.value}</span>`;
    r.querySelector('input').onchange = async e=>{
      // listitem done-state: delete+re-add semantics are overkill; PATCH not in API — remove on check
      if (e.target.checked){ await api(`/api/items/${it.id}`, {method:'DELETE'}); loadList(pass, kind, el); }
    };
    el.appendChild(r);
  }
}


/* ---------- verdict (Pass 1 outcome) ---------- */
function verdictSection(){
  const d = document.createElement('div'); d.className = 'field';
  d.innerHTML = '<label>Verdict <span class="muted">— what happens to this paper</span></label>';
  const row = document.createElement('div'); row.className = 'row'; row.style.gap = '6px';
  ['advance', 'park', 'skip'].forEach(v => {
    const b = document.createElement('button');
    b.textContent = v; b.dataset.v = v;
    if (workRow && workRow.verdict === v) b.classList.add('primary');
    b.onclick = async () => {
      const next = (workRow && workRow.verdict === v) ? '' : v;      // click again to clear
      try {
        await send(`/api/works/${WORK}/verdict`, 'PATCH', {verdict: next});
        if (workRow) workRow.verdict = next;
        [...row.children].forEach(x => x.classList.toggle('primary', x.dataset.v === next));
        chip('saved');
      } catch (err){ console.error('verdict save failed', err); chip('error'); }
    };
    row.appendChild(b);
  });
  d.appendChild(row);
  return d;
}

/* ---------- toast (export etc.) ---------- */
function toast(msg, ms = 4000){
  let el = document.getElementById('rt-toast');
  if (!el){
    el = document.createElement('div'); el.id = 'rt-toast';
    el.style.cssText = 'position:fixed;left:50%;transform:translateX(-50%);bottom:18px;z-index:70;'
      + 'background:var(--panel2);border:1px solid var(--border);border-radius:8px;padding:9px 14px;'
      + 'font-size:13px;box-shadow:0 4px 16px #0009;max-width:80vw;word-break:break-all';
    document.body.appendChild(el);
  }
  el.textContent = msg; el.style.display = 'block';
  clearTimeout(el._t); el._t = setTimeout(() => { el.style.display = 'none'; }, ms);
}

/* ---------- highlights: text layer + colour bands ---------- */
const HL_COLORS = ['claim','evidence','method','doubt','jargon'];
const HL_LABEL = {claim:'claim / gap', evidence:'evidence / data', method:'method',
                  doubt:'doubt / overclaim', jargon:'jargon → parking lot'};
let hlCache = [];              // [{id,page,color,rects,text}] for this file
let pendingSel = null;         // selection awaiting a colour

function ensureLayers(box){
  let hl = box.querySelector('#hl-layer');
  if (!hl){ hl = document.createElement('div'); hl.id = 'hl-layer'; }
  let tl = box.querySelector('.textLayer');
  if (!tl){ tl = document.createElement('div'); tl.className = 'textLayer'; }
  box.appendChild(hl); box.appendChild(tl);      // append() moves, so order stays canvas < hl < text
  return tl;
}

async function buildTextLayer(box){
  const scale = box.clientWidth / box._vp1.width;
  if (!(scale > 0)) return;
  const layer = ensureLayers(box);
  if (layer._scale === scale && layer.childElementCount){ paintHighlights(box); return; }
  const vp = box._page.getViewport({scale});
  layer._scale = scale;
  layer.innerHTML = '';
  layer.style.width = vp.width + 'px';
  layer.style.height = vp.height + 'px';
  layer.style.setProperty('--scale-factor', vp.scale);
  try {
    const tc = await box._page.getTextContent();
    const task = pdfjsLib.renderTextLayer({textContentSource: tc, container: layer,
                                           viewport: vp, textDivs: []});
    await task.promise;
  } catch (err){ console.error('text layer failed on page', box.dataset.page, err); }
  paintHighlights(box);
}

function selectionQuads(){
  const sel = getSelection();
  if (!sel || sel.isCollapsed || !sel.rangeCount) return null;
  const range = sel.getRangeAt(0);
  if (!$('#pdfpane').contains(range.commonAncestorContainer)) return null;
  const quads = [];
  for (const box of pageBoxes()){
    let hit = false;
    try { hit = range.intersectsNode(box); } catch(_){ hit = false; }
    if (!hit) continue;
    const br = box.getBoundingClientRect();
    const rects = [];
    for (const r of range.getClientRects()){
      const x0 = Math.max(r.left, br.left), x1 = Math.min(r.right, br.right);
      let y0 = Math.max(r.top, br.top),     y1 = Math.min(r.bottom, br.bottom);
      if (x1 - x0 < 1.5 || y1 - y0 < 1.5) continue;
      const dy = (y1 - y0) * 0.12;            // line boxes are taller than glyphs
      y0 += dy; y1 -= dy;
      rects.push([+((x0-br.left)/br.width).toFixed(4), +((y0-br.top)/br.height).toFixed(4),
                  +((x1-br.left)/br.width).toFixed(4), +((y1-br.top)/br.height).toFixed(4)]);
    }
    if (rects.length) quads.push({page: parseInt(box.dataset.page), rects});
  }
  const text = sel.toString().replace(/\s+/g, ' ').trim().slice(0, 500);
  if (!quads.length || !text) return null;
  return {quads, text};
}

function paintHighlights(box){
  if (!box._canvas) return;
  let layer = box.querySelector('#hl-layer');
  if (!layer){ layer = document.createElement('div'); layer.id = 'hl-layer'; box.appendChild(layer); }
  layer.innerHTML = '';
  const page = parseInt(box.dataset.page);
  const w = box.clientWidth, h = box.clientHeight;
  if (!w || !h) return;
  for (const hl of hlCache){
    if (hl.page !== page) continue;
    for (const r of hl.rects){
      const d = document.createElement('div');
      d.className = 'hl'; d.dataset.c = hl.color; d.dataset.id = hl.id;
      d.style.left = (r[0]*w) + 'px'; d.style.top = (r[1]*h) + 'px';
      d.style.width = ((r[2]-r[0])*w) + 'px'; d.style.height = ((r[3]-r[1])*h) + 'px';
      layer.appendChild(d);
    }
  }
}
function paintAll(){ pageBoxes().forEach(b => { if (b._canvas) paintHighlights(b); }); }

async function loadHighlights(){
  try { hlCache = await api(`/api/files/${file_id}/highlights`); }
  catch (e){ console.error('highlights load failed', e); hlCache = []; }
  paintAll();
}

/* palette */
let palette = null;
function showPalette(x, y, sel){
  pendingSel = sel;
  if (!palette){
    palette = document.createElement('div'); palette.id = 'hl-palette';
    HL_COLORS.forEach((c, i) => {
      const b = document.createElement('button');
      b.style.background = `var(--hl-${c})`;
      b.title = `${HL_LABEL[c]}  (${i+1})`;
      b.onclick = () => applyColor(c);
      palette.appendChild(b);
    });
    const tip = document.createElement('span');
    tip.className = 'muted'; tip.style.cssText = 'font-size:11px;padding:0 4px';
    tip.textContent = '1–5'; palette.appendChild(tip);
    document.body.appendChild(palette);
  }
  hideHlMenu();
  palette.style.display = 'flex';
  const w = palette.offsetWidth || 170, h = palette.offsetHeight || 34;
  palette.style.left = Math.max(6, Math.min(x + 8, innerWidth - w - 8)) + 'px';
  palette.style.top  = Math.max(6, Math.min(y + 12, innerHeight - h - 8)) + 'px';
}
function hidePalette(){ if (palette) palette.style.display = 'none'; pendingSel = null; lastSelText = ''; }

async function applyColor(color){
  if (!pendingSel) return;
  const {quads, text} = pendingSel;
  hidePalette();
  try {
    for (const q of quads){
      const res = await send(`/api/files/${file_id}/highlights`, 'POST',
                             {page: q.page, color, rects: q.rects, text});
      hlCache.push({id: res.id, page: q.page, color, rects: q.rects, text, note: ''});
    }
    getSelection().removeAllRanges();
    paintAll();
    chip('saved');
  } catch (err){
    console.error('highlight save failed', err);   // never claim saved when the server refused
    chip('error');
  }
}

/* click an existing band → recolour / delete / copy */
function hitHighlight(e){
  const box = e.target.closest ? e.target.closest('.pagebox') : null;
  if (!box) return null;
  const br = box.getBoundingClientRect();
  const fx = (e.clientX - br.left) / br.width, fy = (e.clientY - br.top) / br.height;
  const page = parseInt(box.dataset.page);
  for (let i = hlCache.length - 1; i >= 0; i--){
    const h = hlCache[i];
    if (h.page !== page) continue;
    if (h.rects.some(r => fx >= r[0] && fx <= r[2] && fy >= r[1] && fy <= r[3])) return h;
  }
  return null;
}
let hlMenu = null;
function showHlMenu(e, hl){
  if (!hlMenu){ hlMenu = document.createElement('div'); hlMenu.className = 'hl-menu';
                document.body.appendChild(hlMenu); }
  hlMenu.innerHTML = '';
  HL_COLORS.forEach(c => {
    const b = document.createElement('button');
    b.style.cssText = 'width:20px;height:20px;padding:0;border-radius:4px;border:1px solid #0006;'
                    + `background:var(--hl-${c})`;
    b.title = HL_LABEL[c];
    b.onclick = async () => {
      try { await send(`/api/highlights/${hl.id}`, 'PATCH', {color: c}); hl.color = c; paintAll(); }
      catch (err){ console.error(err); }
      hideHlMenu();
    };
    hlMenu.appendChild(b);
  });
  const del = document.createElement('button');
  del.textContent = 'Delete'; del.style.fontSize = '11px';
  del.onclick = async () => {
    try { await send(`/api/highlights/${hl.id}`, 'DELETE');
          hlCache = hlCache.filter(x => x.id !== hl.id); paintAll(); }
    catch (err){ console.error(err); }
    hideHlMenu();
  };
  const cp = document.createElement('button');
  cp.textContent = 'Copy'; cp.style.fontSize = '11px';
  cp.onclick = async () => {
    try { await navigator.clipboard.writeText(hl.text || ''); cp.textContent = 'Copied'; }
    catch (_){ cp.textContent = '—'; }
  };
  hlMenu.append(del, cp);
  hlMenu.style.display = 'flex';
  hlMenu.style.left = Math.max(6, Math.min(e.clientX + 8, innerWidth - 250)) + 'px';
  hlMenu.style.top  = Math.max(6, Math.min(e.clientY + 10, innerHeight - 60)) + 'px';
}
function hideHlMenu(){ if (hlMenu) hlMenu.style.display = 'none'; }

/* The palette must not depend on a mouseup: on iOS/iPadOS a text selection is made by
   long-press then dragging handles, which fires selectionchange but no mouseup at all.
   So selectionchange is the primary trigger, anchored to the last line of the selection;
   mouseup only adds desktop immediacy and the click-a-band menu. */
let lastSelText = '';
function offerPaletteFromSelection(){
  const sel = selectionQuads();
  if (!sel) return false;
  if (sel.text === lastSelText && pendingSel && palette && palette.style.display !== 'none') return true;
  lastSelText = sel.text;
  let x = innerWidth / 2, y = innerHeight / 2;
  const range = getSelection().rangeCount ? getSelection().getRangeAt(0) : null;
  if (range){
    const rects = range.getClientRects();
    if (rects.length){
      const r = rects[rects.length - 1];
      x = Math.min(r.right, innerWidth - 40); y = r.bottom + 4;
    }
  }
  showPalette(x, y, sel);
  return true;
}

let selTimer = null;
document.addEventListener('selectionchange', () => {
  clearTimeout(selTimer);
  selTimer = setTimeout(() => {
    if (snapMode) return;
    const s = getSelection();
    if (!s || s.isCollapsed || !s.rangeCount) return;
    if (!$('#pdfpane').contains(s.getRangeAt(0).commonAncestorContainer)) return;
    offerPaletteFromSelection();
  }, 200);
});
document.addEventListener('touchend', e => {
  if (e.target.closest && (e.target.closest('#hl-palette') || e.target.closest('.hl-menu'))) return;
  setTimeout(() => { if (!snapMode) offerPaletteFromSelection(); }, 120);
}, {passive: true});

document.addEventListener('mouseup', e => {
  if (snapMode) return;
  if (e.target.closest && (e.target.closest('#hl-palette') || e.target.closest('.hl-menu'))) return;
  const x = e.clientX, y = e.clientY, tgt = e;
  setTimeout(() => {
    const sel = selectionQuads();
    if (sel){ showPalette(x, y, sel); lastSelText = sel.text; return; }
    const s = getSelection();
    if (s && s.isCollapsed){
      const hl = hitHighlight(tgt);
      if (hl) showHlMenu(tgt, hl); else hideHlMenu();
    }
  }, 10);
});
document.addEventListener('pointerdown', e => {
  if (e.target.closest && (e.target.closest('#hl-palette') || e.target.closest('.hl-menu'))) return;
  if (!getSelection().isCollapsed) hidePalette();
  hideHlMenu();
});
addEventListener('keydown', e => {
  const tag = e.target.tagName;
  if (tag === 'TEXTAREA' || tag === 'INPUT') return;
  if (!pendingSel){ if (e.key === 'Escape') hideHlMenu(); return; }
  const i = '12345'.indexOf(e.key);
  if (i >= 0){ e.preventDefault(); applyColor(HL_COLORS[i]); }
  else if (e.key === 'Escape'){ hidePalette(); getSelection().removeAllRanges(); }
});


/* ---------- Refs tab ---------- */
const REF_CHIP = {exact: 'resolved', tentative: 'parked', unmatched: 'seed', unparsed: 'seed'};

function refRow(r){
  const d = document.createElement('div'); d.className = 'ref-row';
  const top = document.createElement('div'); top.className = 'row'; top.style.gap = '6px';
  const star = document.createElement('button');
  star.className = 'ref-star'; star.textContent = r.star ? '★' : '☆';
  star.title = 'star this reference';
  star.onclick = async () => {
    try { await send(`/api/refs/${r.id}`, 'PATCH', {star: r.star ? 0 : 1});
          r.star = r.star ? 0 : 1; star.textContent = r.star ? '★' : '☆'; }
    catch (e){ toast('Could not save star'); }
  };
  top.appendChild(star);

  const chip = document.createElement('span');
  chip.className = 'chip ' + (REF_CHIP[r.match_status] || 'seed');
  chip.textContent = r.match_status + (r.source === 'manual' ? ' · manual' : '');
  top.appendChild(chip);

  const label = document.createElement('span');
  label.className = 'ref-label';
  label.textContent = r.title_norm ? r.raw.slice(0, 140) : (r.authors || '') + ' ' + (r.year || '');
  label.title = r.raw;
  top.appendChild(label);
  d.appendChild(top);

  if (r.doi){
    const doi = document.createElement('div'); doi.className = 'muted';
    doi.style.cssText = 'font-size:12px;margin:2px 0 4px 26px';
    let looked = '';
    try {
      const meta = r.meta ? JSON.parse(r.meta) : null;
      if (meta && (meta.source || meta.query)) looked = ' · looked up';
      if (meta && meta.tie) looked = ' · ambiguous (two candidates)';
    } catch (e) {}
    doi.textContent = `DOI ${r.doi}${looked}`;
    d.appendChild(doi);
  }

  const why = document.createElement('input');
  why.placeholder = 'why this matters to my question…'; why.value = r.why || '';
  why.style.marginTop = '4px';
  const key = `ref-${r.id}`;
  why.oninput = () => autosave(key, () => send(`/api/refs/${r.id}`, 'PATCH', {why: why.value}));
  why.onblur = () => flushSave(key);
  d.appendChild(why);

  const bot = document.createElement('div'); bot.className = 'row'; bot.style.gap = '6px';
  bot.style.marginTop = '4px';
  const fix = document.createElement('input');
  fix.placeholder = 'fix (typo, wrong match, checked…)'; fix.value = r.fix || '';
  fix.oninput = () => autosave(`reffix-${r.id}`, () => send(`/api/refs/${r.id}`, 'PATCH', {fix: fix.value}));
  fix.onblur = () => flushSave(`reffix-${r.id}`);
  bot.appendChild(fix);
  if (r.source === 'manual'){
    const del = document.createElement('button');
    del.textContent = '✕'; del.title = 'delete this manual row';
    del.onclick = async () => {
      try { await send(`/api/refs/${r.id}`, 'DELETE'); d.remove(); }
      catch (e){ toast('Delete failed'); }
    };
    bot.appendChild(del);
  }
  d.appendChild(bot);
  return d;
}

function manualPanel(scanned){
  const box = document.createElement('div'); box.className = 'field';
  box.innerHTML = `<label>Add references by hand ${scanned ? '<span class="chip parked">scanned PDF</span>' : ''}</label>`
    + `<div class="muted" style="margin-bottom:6px">`
    + (scanned ? 'This PDF has no text layer, so nothing can be extracted. ' : '')
    + 'Paste the list — one reference per line — or paste DOIs, one per line.</div>';
  const ta = document.createElement('textarea');
  ta.style.minHeight = '96px';
  ta.placeholder = 'Smith, J., & Lee, K. (2022). Title of the article. Journal Name 7, 40.\n10.1234/example.2022.001';
  box.appendChild(ta);
  const row = document.createElement('div'); row.className = 'row'; row.style.gap = '6px';
  row.style.marginTop = '6px';
  const mk = (label, body) => {
    const b = document.createElement('button'); b.textContent = label;
    b.onclick = async () => {
      if (!ta.value.trim()){ toast('Nothing pasted'); return; }
      b.disabled = true;
      try {
        const r = await jpost(`/api/works/${WORK}/refs/manual`, body(ta.value));
        const extra = r.rejected && r.rejected.length ? `, ${r.rejected.length} rejected` : '';
        toast(`added ${r.added ?? 0}, duplicates ${r.updated ?? 0}${extra}`, 6000);
        await renderTab();
      } catch (e){ toast('Add failed: ' + e.message); b.disabled = false; }
    };
    return b;
  };
  row.appendChild(mk('Add as reference lines', v => ({text: v})));
  row.appendChild(mk('Add as DOIs (looks up metadata)', v => ({dois: v, resolve: true})));
  box.appendChild(row);
  return box;
}

async function refsTabSection(){
  const wrap = document.createElement('div');
  const data = await api(`/api/works/${WORK}/refs`);
  const refs = data.refs || [];
  const tl = data.text_layer || 'unknown';
  const auto = refs.filter(r => r.source !== 'manual').length;

  const head = document.createElement('div'); head.className = 'field';
  head.innerHTML = `<label>References <span class="muted">— ${refs.length} row(s): `
    + `${auto} extracted, ${refs.length - auto} manual</span></label>`
    + `<div class="muted" style="margin-bottom:8px">text layer: <b>${tl}</b></div>`;
  const row = document.createElement('div'); row.className = 'row'; row.style.gap = '6px';
  const btn = document.createElement('button'); btn.className = 'primary';
  btn.textContent = 'Extract references';
  if (tl === 'image-only' || tl === 'unreadable'){
    btn.disabled = true; btn.title = 'no text layer in this PDF';
  }
  btn.onclick = async () => {
    btn.disabled = true; btn.textContent = 'Extracting…';
    try {
      const r = await jpost(`/api/works/${WORK}/extract-refs`, {});
      if (r.status === 'no-text-layer') toast('No text layer — scanned PDF. Use the manual panel below.', 7000);
      else toast(`${r.entries} entries · style ${r.style} · ` +
                 Object.entries(r.by_status || {}).map(([k, v]) => `${v} ${k}`).join(' · '), 9000);
    } catch (e){ toast('Extraction failed: ' + e.message, 7000); }
    await renderTab();
  };
  row.appendChild(btn);
  const all = document.createElement('a');
  all.href = '/refs'; all.innerHTML = '<button>Common references</button>';
  row.appendChild(all);
  head.appendChild(row);
  wrap.appendChild(head);

  wrap.appendChild(manualPanel(tl === 'image-only' || tl === 'unreadable'));

  if (!refs.length){
    const p = document.createElement('p'); p.className = 'muted';
    p.textContent = tl === 'image-only'
      ? 'Scanned PDF — nothing to extract. Paste the reference list or DOIs above.'
      : 'No references yet. Press “Extract references”.';
    wrap.appendChild(p);
    return wrap;
  }
  for (const r of refs) wrap.appendChild(refRow(r));
  return wrap;
}


/* ---------- favourite star ---------- */
async function loadStar(){
  try {
    const rows = await api('/api/papers');
    const me = rows.find(x => x.id === WORK);
    setStarUI(me ? !!me.starred : false);
  } catch (e) { /* the star is a convenience; never block the reader on it */ }
}

function setStarUI(on){
  const b = $('#btn-star');
  if (!b) return;
  b.textContent = on ? '★' : '☆';
  b.title = on ? 'remove from favourites' : 'add to favourites';
  b.style.color = on ? '#ffd166' : '';
  b.dataset.on = on ? '1' : '';
}

$('#btn-star').onclick = async () => {
  const on = !$('#btn-star').dataset.on;
  setStarUI(on);                              // optimistic
  try {
    const r = await fetch(`/api/works/${WORK}/star`, {
      method: 'PATCH', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({starred: on})});
    if (!r.ok) throw new Error('HTTP ' + r.status);
    setStarUI(!!(await r.json()).starred);
    toast(on ? 'Added to favourites' : 'Removed from favourites');
  } catch (e) {
    setStarUI(!on);
    toast('Could not save the star');
  }
};

/* ---------- Scratch pad: the writer's editor, one pad per paper ---------- */
async function scratchTabSection(){
  const d = await api(`/api/works/${WORK}/scratch`);
  const wrap = document.createElement('div');
  wrap.style.cssText = 'display:flex;flex-direction:column;height:calc(100vh - 190px);min-height:420px';

  const bar = document.createElement('div');
  bar.className = 'row';
  bar.style.cssText = 'gap:6px;flex:0 0 auto;margin-bottom:6px';
  const label = document.createElement('span');
  label.className = 'muted';
  label.style.cssText = 'font-size:11.5px';
  label.textContent = 'Scratch — unsorted thinking, same tools as Write';
  const full = document.createElement('a');
  full.href = `/write?id=${d.id}`;
  full.target = '_blank';
  full.innerHTML = '<button title="open full screen">⤢</button>';

  const frame = document.createElement('iframe');
  frame.style.cssText = 'flex:1 1 auto;min-height:0;width:100%;border:1px solid var(--border);' +
                        'border-radius:6px;background:var(--bg)';
  frame.src = `/write?id=${d.id}&embed=1`;

  bar.append(label, full);
  wrap.append(bar, frame);
  setTimeout(() => { const p = $('#notespane'); if (p) p.scrollTop = 0; }, 50);
  return wrap;
}

/* ---------- Write tab: the editor inline, beside the PDF ---------- */
async function writeTabSection(){
  const wrap = document.createElement('div');
  // the notes pane scrolls; the editor needs its own full-height box so the iframe can fill it
  wrap.style.cssText = 'display:flex;flex-direction:column;height:calc(100vh - 190px);min-height:420px';

  let docs = await api('/api/drafts');
  if (!docs.length){
    await jpost('/api/drafts', {title: 'Untitled document', work_id: WORK});
    docs = await api('/api/drafts');
  }

  const bar = document.createElement('div');
  bar.className = 'row'; bar.style.cssText = 'gap:6px;flex:0 0 auto;margin-bottom:6px';

  const sel = document.createElement('select');
  sel.style.cssText = 'flex:1 1 auto;min-width:0';
  for (const d of docs){
    const o = document.createElement('option');
    o.value = d.id; o.textContent = d.title || 'Untitled';
    sel.appendChild(o);
  }
  const lastId = localStorage.getItem('wrtab-draft');
  sel.value = (docs.some(d => String(d.id) === String(lastId)) ? lastId : docs[0].id);

  const frame = document.createElement('iframe');
  frame.style.cssText = 'flex:1 1 auto;min-height:0;width:100%;border:1px solid var(--border);' +
                        'border-radius:6px;background:var(--bg)';
  const loadFrame = () => {
    frame.src = `/write?id=${sel.value}&embed=1`;
    localStorage.setItem('wrtab-draft', sel.value);
  };
  sel.onchange = loadFrame;

  const mkBtn = (label, title, fn) => {
    const b = document.createElement('button');
    b.textContent = label; b.title = title;
    b.onclick = fn;
    return b;
  };
  const newDoc = mkBtn('+', 'new document for this paper', async () => {
    const d = await jpost('/api/drafts', {title: $('#wtitle').textContent.slice(0, 80), work_id: WORK});
    localStorage.setItem('wrtab-draft', d.id);
    await renderTab();
    focusWriter();                     // land in the new document, not on its toolbar
  });
  const delDoc = mkBtn('✕', 'delete the selected document', async () => {
    const opt = [...sel.options].find(o => o.value === sel.value);
    if (!confirm(`Remove “${opt ? opt.textContent : 'this document'}” from the writer?\n\n` +
                 'Files on disk are never touched.')) return;
    try {
      await send(`/api/drafts/${sel.value}`, 'DELETE');
      const left = (await api('/api/drafts')).filter(d => String(d.id) !== String(sel.value));
      if (!left.length) await jpost('/api/drafts', {title: 'Untitled document', work_id: WORK});
      await renderTab();
    } catch (e){ toast('Delete failed'); }
  });
  const full = document.createElement('a');
  full.href = `/write?id=${sel.value}`; full.target = '_blank';
  full.innerHTML = '<button title="open full screen">⤢</button>';

  bar.append(sel, newDoc, delDoc, full);
  wrap.append(bar, frame);
  loadFrame();
  // the pane restores its remembered scroll for the pass tabs; that would hide the picker
  setTimeout(() => { const p = $('#notespane'); if (p) p.scrollTop = 0; }, 50);
  return wrap;
}

function focusWriter(){
  const f = document.querySelector('#tabbody iframe');
  if (!f) return;
  try { f.contentDocument.querySelector('#doc').focus(); } catch (e) {}
}

/* ---------- export ---------- */
const expBtn = $('#btn-export');
if (expBtn) expBtn.onclick = async () => {
  expBtn.disabled = true; expBtn.textContent = '⤒ Exporting…';
  try {
    const res = await jpost(`/api/works/${WORK}/export`, {});
    const extra = res.xlsx ? ' + matrix row' : (res.xlsx_error ? ` (matrix skipped: ${res.xlsx_error})` : '');
    toast(`Exported${extra} → ${res.markdown}`, 8000);
  } catch (err){
    toast('Export failed: ' + err.message, 6000);
  } finally {
    expBtn.disabled = false; expBtn.textContent = '⤒ Export';
  }
};

/* ---------- lightbox ---------- */
$('#lightbox').onclick = ()=> $('#lightbox').style.display='none';

/* ---------- boot ---------- */
(async ()=>{
  for (const t of ['pass1','pass2','pass3'])
    passTemplates[t] = await api(`/api/templates/${t}`);
  syncZoomLabels();
  applyLayout();
  await loadPdf();
  await renderTab();
  // if a deep-linked anchor was passed, remember its card as current
  const anchorId = params.get('anchor');
  if (anchorId){
    const cards = await api(`/api/works/${WORK}/cards`);
    const c = cards.find(c=>c.anchors.some(a=>a.id==anchorId));
    if (c) currentCardId = c.id;
  }
})();

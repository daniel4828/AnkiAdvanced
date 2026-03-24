// ── State ──────────────────────────────────────────────────────────────────
let deckId      = null;
let rootDeckId      = null;   // set when studying all categories (mixed mode)
let unfinishedMode  = false;  // set when studying the "Unfinished Cards" virtual deck
let deckName    = '';
let category    = '';
let card        = null;   // current card dict from API
let story       = null;   // story dict with sentences[]
let sentence    = null;   // current sentence from story (may be null)
let wordDetails = null;   // full word data: examples + characters
let _currentWordId = null; // word ID open in word-detail view
let userInput   = '';     // creating category: what the user typed
let browseWords  = [];   // all words from /api/browse-words
let browseAll    = [];   // kept for legacy (unused by new browse)
let _browseSort  = 'pinyin-asc';
let _browseSelected = new Set();  // selected word IDs (multiselect)
let _browseDecks = [];            // flat deck list for move dropdown
let optDeckId    = null; // deck whose options modal is open
const collapsed  = new Set();  // parent deck IDs that are collapsed
let _cachedDecks = null;       // last fetched deck tree (for toggle re-renders)

// ── Prompt modal ────────────────────────────────────────────────────────────
let _promptResolve = null;
function showPrompt(title, defaultValue = '') {
  return new Promise(resolve => {
    _promptResolve = resolve;
    document.getElementById('prompt-modal-title').textContent = title;
    const input = document.getElementById('prompt-modal-input');
    input.value = defaultValue;
    document.getElementById('prompt-modal-overlay').style.display = '';
    document.getElementById('prompt-modal').style.display = '';
    setTimeout(() => { input.focus(); input.select(); }, 50);
  });
}
function confirmPromptModal() {
  const input = document.getElementById('prompt-modal-input');
  const val = input.style.display === 'none' ? true : input.value;
  const resolve = _promptResolve;
  _resetPromptModal();
  closePromptModal();
  if (resolve) resolve(val);
}
function cancelPromptModal() {
  const resolve = _promptResolve;
  _resetPromptModal();
  closePromptModal();
  if (resolve) resolve(null);
}
function _resetPromptModal() {
  const input = document.getElementById('prompt-modal-input');
  input.style.display = '';
  const btn = document.getElementById('prompt-modal-confirm-btn');
  btn.textContent = 'OK';
  btn.style.color = 'var(--primary)';
  btn.style.borderColor = 'var(--primary)';
}
function closePromptModal() {
  document.getElementById('prompt-modal-overlay').style.display = 'none';
  document.getElementById('prompt-modal').style.display = 'none';
  _promptResolve = null;
}
function showConfirm(message) {
  return new Promise(resolve => {
    _promptResolve = resolve;
    document.getElementById('prompt-modal-title').textContent = message;
    document.getElementById('prompt-modal-input').style.display = 'none';
    document.getElementById('prompt-modal-confirm-btn').textContent = 'Delete';
    document.getElementById('prompt-modal-confirm-btn').style.color = '#e53e3e';
    document.getElementById('prompt-modal-confirm-btn').style.borderColor = '#e53e3e';
    document.getElementById('prompt-modal-overlay').style.display = '';
    document.getElementById('prompt-modal').style.display = '';
  });
}

// ── API helper ─────────────────────────────────────────────────────────────
async function api(method, path, body) {
  const opts = { method };
  if (body !== undefined) {
    opts.headers = { 'Content-Type': 'application/json' };
    opts.body = JSON.stringify(body);
  }
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error(`${method} ${path} → ${r.status}`);
  return r.json();
}

// ── View switcher ──────────────────────────────────────────────────────────
function showView(name) {
  ['loading', 'decks', 'review', 'done', 'browse', 'word-detail', 'hanzi-detail', 'stats'].forEach(v => {
    document.getElementById(`view-${v}`).style.display = 'none';
  });
  document.getElementById(`view-${name}`).style.display =
    name === 'browse' ? 'flex' : 'block';
  document.querySelector('main').classList.toggle('browse-open', name === 'browse');
  document.getElementById('back-btn').style.display = name === 'decks' ? 'none' : 'block';
  document.getElementById('header-title').textContent =
    name === 'review'       ? deckName :
    name === 'browse'       ? 'Browse' :
    name === 'word-detail'  ? 'Word Detail' :
    name === 'hanzi-detail' ? 'Hanzi Detail' :
    name === 'stats'        ? 'Stats' : 'AnkiAdvanced';
}

function setLoading(msg) {
  document.getElementById('loading-msg').textContent = msg || 'Loading…';
  showView('loading');
}

function cap(s) { return s.charAt(0).toUpperCase() + s.slice(1); }

function showError(msg) {
  const el = document.getElementById('error-banner');
  el.textContent = msg;
  el.style.display = 'block';
  setTimeout(() => { el.style.display = 'none'; }, 6000);
}

// ── Deck list ───────────────────────────────────────────────────────────────
async function loadDecks() {
  setLoading('Loading decks…');
  try {
    const decks = await api('GET', '/api/decks');
    _cachedDecks = decks;
    renderDecks(decks);
    showView('decks');
  } catch (e) {
    showError('Could not load decks: ' + e.message);
    showView('decks');
  }
}

function flatten(nodes, depth = 0) {
  return nodes.flatMap(n => [{ ...n, _depth: depth }, ...flatten(n.children || [], depth + 1)]);
}

// Direct category-leaf children of a deck keyed by category
function getCategoryLeaves(deck) {
  const map = {};
  for (const child of (deck.children || [])) {
    if (child.category && (!child.children || child.children.length === 0)) {
      map[child.category] = child;
    }
  }
  return map;
}

// All category-leaf decks anywhere under this deck (recursive)
function getDeepCategoryLeaves(deck) {
  const result = [];
  for (const child of (deck.children || [])) {
    if (child.category && (!child.children || child.children.length === 0)) {
      result.push(child);
    } else {
      result.push(...getDeepCategoryLeaves(child));
    }
  }
  return result;
}

// Aggregate counts for one category from all deep leaves
function aggregateCounts(deck, category) {
  const leaves = getDeepCategoryLeaves(deck).filter(l => l.category === category);
  const agg = { new: 0, learning: 0, review: 0 };
  for (const l of leaves) for (const k of ['new', 'learning', 'review']) agg[k] += (l.counts || {})[k] || 0;
  return agg;
}

function countHtml(c) {
  return `<span class="n-new">${c.new}</span> <span class="n-lrn">${c.learning}</span> <span class="n-rev">${c.review}</span>`;
}

// Build 3 inline pills (L/R/C) for any deck. Uses direct cat leaves if present, else aggregates.
function buildCategoryButtons(deck) {
  const CATS   = ['listening', 'reading', 'creating'];
  const LABELS = { listening: 'L', reading: 'R', creating: 'C' };
  const catLeaves = getCategoryLeaves(deck);
  const safeName  = deck.name.replace(/'/g, "\\'");
  return CATS.map(cat => {
    const label = LABELS[cat];
    const leaf = catLeaves[cat];
    if (leaf) {
      const c = leaf.counts || { new: 0, learning: 0, review: 0 };
      const allSusp = !!leaf.all_suspended;
      const badgeIcon = allSusp ? '▶' : '⏸';
      const badgeClass = allSusp ? 'cat-susp-badge cat-badge-suspended' : 'cat-susp-badge cat-badge-active';
      const pillClass = allSusp ? 'cat-pill cat-pill-dimmed' : 'cat-pill';
      const title = allSusp ? `Unsuspend all ${label} cards` : `Suspend all ${label} cards`;
      return `<span class="cat-pill-group"><button class="${badgeClass}" onclick="event.stopPropagation();toggleCategorySuspension(${leaf.id},'${cat}')" title="${title}">${badgeIcon}</button><span class="cat-pill-wrap"><button class="${pillClass}" onclick="event.stopPropagation();startReview(${leaf.id},'${cat}','${safeName}',${!!leaf.no_story})"><span class="cat-pill-label">${label}</span><span class="cat-pill-counts">${countHtml(c)}</span></button><button class="cat-pill-gear" onclick="event.stopPropagation();openOptions(${leaf.id})" title="Options">⚙</button></span></span>`;
    }
    const c = aggregateCounts(deck, cat);
    const hasCards = getDeepCategoryLeaves(deck).some(l => l.category === cat);
    if (!hasCards) return `<button class="cat-pill" disabled><span class="cat-pill-label">${label}</span><span class="cat-pill-counts"><span class="n-zero">—</span></span></button>`;
    const leaves = getDeepCategoryLeaves(deck).filter(l => l.category === cat);
    const allSusp = leaves.length > 0 && leaves.every(l => !!l.all_suspended);
    const badgeIcon = allSusp ? '▶' : '⏸';
    const badgeClass = allSusp ? 'cat-susp-badge cat-badge-suspended' : 'cat-susp-badge cat-badge-active';
    const pillClass = allSusp ? 'cat-pill cat-pill-dimmed' : 'cat-pill';
    const title = allSusp ? `Unsuspend all ${label} cards` : `Suspend all ${label} cards`;
    return `<span class="cat-pill-group"><button class="${badgeClass}" onclick="event.stopPropagation();toggleCategorySuspension(${deck.id},'${cat}')" title="${title}">${badgeIcon}</button><span class="cat-pill-wrap"><button class="${pillClass}" onclick="event.stopPropagation();startReview(${deck.id},'${cat}','${safeName}',${!!deck.no_story})"><span class="cat-pill-label">${label}</span><span class="cat-pill-counts">${countHtml(c)}</span></button><button class="cat-pill-gear" onclick="event.stopPropagation();openOptions(${deck.id})" title="Options">⚙</button></span></span>`;
  }).join('');
}

function renderDecks(decks) {
  const navRow = `
    <div class="nav-row">
      <button class="nav-btn" onclick="openBrowse()">Browse Cards</button>
      <button class="nav-btn" onclick="openStats()">Stats</button>
      <button class="nav-btn" onclick="openCostModal()">API Costs</button>
      <button class="nav-btn" onclick="openImportModal()">Import</button>
      <button class="nav-btn" onclick="createDeck()">New Deck</button>
      <button class="nav-btn" onclick="openTrash()">Trash</button>
    </div>`;

  const virtualDecks = decks.filter(d => d.virtual);
  const allDeck = virtualDecks.find(d => d.name === 'All');
  // Real decks live as children of the "All" virtual deck
  const allChildren = allDeck ? (allDeck.children || []) : decks.filter(d => !d.virtual);
  const sentencesDeck = allChildren.find(d => d.name === 'Sentences');
  const regularDecks = allChildren.filter(d => d.name !== 'Sentences' && d.name !== 'Default');

  let html = '';

  // ── Filtered Decks section ────────────────────────────────────────────────
  let filteredHtml = '';

  for (const vd of virtualDecks) {
    if (vd.id === 'unfinished') {
      const c = vd.counts;
      filteredHtml += `
        <div class="filtered-row unfinished-entry" onclick="startReviewUnfinished()">
          <span class="filtered-name">${vd.name}</span>
          <span class="filtered-count">${c.learning}</span>
        </div>`;
    }
  }

  if (allDeck) {
    const safeName = 'All';
    filteredHtml += `
      <div class="tree-row tree-parent">
        <span class="tree-toggle"></span>
        <span class="tree-name" onclick="startReviewMixed(${allDeck.id},'${safeName}')" style="cursor:pointer">All</span>
        <span class="deck-counts"><span class="n-new">${(allDeck.counts||{}).new||0}</span><span class="n-lrn">${(allDeck.counts||{}).learning||0}</span><span class="n-rev">${(allDeck.counts||{}).review||0}</span></span>
        <div class="cat-pills-row">${buildCategoryButtons(allDeck)}</div>
      </div>`;
  }

  if (sentencesDeck) {
    filteredHtml += renderDeckRows([sentencesDeck], 0);
  }

  if (filteredHtml) {
    html += `<div class="section-label">Filtered Decks</div><div class="tree-card filtered-tree-card">${filteredHtml}</div>`;
  }

  // ── Regular Decks section ─────────────────────────────────────────────────
  const regularHtml = renderDeckRows(regularDecks, 0);
  if (regularHtml.trim()) {
    html += `<div class="section-label">Decks</div><div class="tree-card">${regularHtml}</div>`;
  }

  document.getElementById('view-decks').innerHTML = navRow + html;
}

function renderDeckRows(decks, depth) {
  return decks.map(deck => {
    // Category leaf decks are consumed as pills — not rendered as rows
    if (deck.category && (!deck.children || deck.children.length === 0)) return '';

    const structChildren = (deck.children || []).filter(
      c => !(c.category && (!c.children || c.children.length === 0))
    );
    const hasStructChildren = structChildren.length > 0;
    const isCollapsed = collapsed.has(deck.id);
    const indent = depth * 18;

    const toggleIcon = hasStructChildren ? (isCollapsed ? '▶' : '▼') : '';
    const safeName  = deck.name.replace(/'/g, "\\'");
    const c = deck.counts || { new: 0, learning: 0, review: 0 };
    const deckCounts = `<span class="deck-counts"><span class="n-new">${c.new}</span><span class="n-lrn">${c.learning}</span><span class="n-rev">${c.review}</span></span>`;

    const buryMode   = deck.bury_mode || 'all';
    const buryIcon   = buryMode === 'all' ? '⛓' : buryMode === 'none' ? '⊘' : '≡';
    const buryClass  = `bury-btn bury-${buryMode}`;
    const buryTitle  = buryMode === 'all'    ? 'Bury siblings: All (click for None)'
                     : buryMode === 'none'   ? 'Bury siblings: None (click for Custom)'
                     :                         'Bury siblings: Custom (click for All)';
    const row = `
      <div class="tree-row tree-parent" style="padding-left:${16 + indent}px">
        <span class="tree-toggle" onclick="toggleDeck(${deck.id})">${toggleIcon}</span>
        <span class="tree-name" onclick="startReviewMixed(${deck.id},'${safeName}',${!!deck.no_story})" style="cursor:pointer">${deck.name}</span>
        ${deckCounts}
        <button class="${buryClass}" onclick="event.stopPropagation();toggleBury(${deck.id})" title="${buryTitle}">${buryIcon}</button>
        <div class="deck-menu-wrap">
          <button class="deck-susp-btn ${deck.deck_all_suspended ? 'deck-all-suspended' : ''}" onclick="event.stopPropagation();toggleDeckAllSuspension(${deck.id})" title="${deck.deck_all_suspended ? 'Unsuspend all cards' : 'Suspend all cards'}">${deck.deck_all_suspended ? '▶' : '⏸'}</button>
          <button class="gear-btn" onclick="event.stopPropagation();toggleDeckMenu(event,${deck.id},'${safeName}',${!!deck.filtered})" title="Deck options">⚙</button>
        </div>
        <div class="cat-pills-row">${buildCategoryButtons(deck)}</div>
      </div>`;

    const childRows = hasStructChildren && !isCollapsed
      ? renderDeckRows(structChildren, depth + 1)
      : '';

    return row + childRows;
  }).join('');
}

async function toggleCategorySuspension(deckId, category) {
  try {
    await api('POST', `/api/decks/${deckId}/categories/${category}/toggle-suspension`);
    const decks = await api('GET', '/api/decks');
    _cachedDecks = decks;
    renderDecks(decks);
  } catch (e) {
    showError('Could not toggle suspension: ' + e.message);
  }
}

async function toggleDeckAllSuspension(deckId) {
  try {
    await api('POST', `/api/decks/${deckId}/toggle-all-suspension`);
    const decks = await api('GET', '/api/decks');
    _cachedDecks = decks;
    renderDecks(decks);
  } catch (e) {
    showError('Could not toggle suspension: ' + e.message);
  }
}

function toggleDeck(deckId) {
  if (collapsed.has(deckId)) {
    collapsed.delete(deckId);
  } else {
    collapsed.add(deckId);
  }
  if (_cachedDecks) {
    const scrollEl = document.querySelector('main');
    const scrollY = scrollEl ? scrollEl.scrollTop : 0;
    renderDecks(_cachedDecks);
    if (scrollEl) scrollEl.scrollTop = scrollY;
  } else {
    loadDecks();
  }
}

async function toggleBury(deckId) {
  try {
    const { bury_mode } = await api('POST', `/api/decks/${deckId}/preset/toggle-bury`);
    // Optimistic update in cached tree
    if (_cachedDecks) {
      const flat = [];
      const walk = nodes => nodes.forEach(n => { flat.push(n); walk(n.children || []); });
      walk(_cachedDecks);
      const deck = flat.find(d => d.id === deckId);
      if (deck) deck.bury_mode = bury_mode;
      const scrollEl = document.querySelector('main');
      const scrollY = scrollEl ? scrollEl.scrollTop : 0;
      renderDecks(_cachedDecks);
      if (scrollEl) scrollEl.scrollTop = scrollY;
    }
  } catch (e) {
    showError('Failed to toggle burying: ' + e.message);
  }
}

// ── Deck context menu ────────────────────────────────────────────────────────
function toggleDeckMenu(e, id, safeName, filtered = false) {
  closeDeckMenu();
  const btn = e.currentTarget;
  const menu = document.createElement('div');
  menu.id = 'deck-menu';
  menu.className = 'deck-dropdown';
  if (filtered) {
    menu.innerHTML = `
      <button onclick="closeDeckMenu();openBrowseForDeck(${id})">Browse</button>
      <button onclick="closeDeckMenu();clearDeckCards(${id},'${safeName}')">Clear all cards</button>
    `;
  } else {
    menu.innerHTML = `
      <button onclick="closeDeckMenu();openBrowseForDeck(${id})">Browse</button>
      <button onclick="closeDeckMenu();renameDeck(${id},'${safeName}')">Rename</button>
      <button onclick="closeDeckMenu();openOptions(${id})">Options</button>
      <button onclick="closeDeckMenu();deleteDeck(${id},'${safeName}')">Delete</button>
    `;
  }
  document.body.appendChild(menu);
  const r = btn.getBoundingClientRect();
  const menuH = menu.offsetHeight;
  const spaceBelow = window.innerHeight - r.bottom;
  const top = spaceBelow >= menuH + 4
    ? r.bottom + window.scrollY + 4
    : r.top  + window.scrollY - menuH - 4;
  menu.style.top  = top + 'px';
  menu.style.left = (r.left + window.scrollX - menu.offsetWidth + btn.offsetWidth) + 'px';
  setTimeout(() => document.addEventListener('click', closeDeckMenu, { once: true }), 0);
}
function closeDeckMenu() {
  document.getElementById('deck-menu')?.remove();
}

async function deleteDeck(id, name) {
  const confirmed = await showConfirm(`Delete deck "${name}" and all its cards? This cannot be undone.`);
  if (!confirmed) return;
  try {
    await api('DELETE', `/api/decks/${id}`);
    loadDecks();
  } catch (e) {
    showError('Delete failed: ' + e.message);
  }
}

async function clearDeckCards(id, name) {
  const confirmed = await showConfirm(`Delete all notes in "${name}"? This cannot be undone.`);
  if (!confirmed) return;
  try {
    await api('DELETE', `/api/decks/${id}/cards`);
    loadDecks();
  } catch (e) {
    showError('Clear failed: ' + e.message);
  }
}

async function renameDeck(id, currentName) {
  const name = await showPrompt('Rename deck', currentName);
  if (!name || name === currentName) return;
  try {
    await api('PUT', `/api/decks/${id}`, { name });
    loadDecks();
  } catch (e) {
    showError('Rename failed: ' + e.message);
  }
}

async function createDeck() {
  const path = await showPrompt('New deck path (use :: to nest, e.g. Daily::03-19)');
  if (!path || !path.trim()) return;
  try {
    await api('POST', `/api/decks?name=${encodeURIComponent(path.trim())}`);
    loadDecks();
  } catch (e) {
    showError('Create deck failed: ' + e.message);
  }
}

// ── Browse ───────────────────────────────────────────────────────────────────
let _browseSearchTimer = null;
let _browseMode   = 'notes';   // 'notes' | 'hanzi'
let _browseFilter = 'all';     // note_type or 'all'; for hanzi mode: 'all'
let _browseDeckId = null;      // deck filter (notes mode only)
let _allHanzi     = [];        // cache

function _sortWords(words) {
  const sorted = [...words];
  const locale = { sensitivity: 'base' };
  switch (_browseSort) {
    case 'pinyin-asc':  sorted.sort((a, b) => (a.pinyin || '').localeCompare(b.pinyin || '', 'en', locale)); break;
    case 'pinyin-desc': sorted.sort((a, b) => (b.pinyin || '').localeCompare(a.pinyin || '', 'en', locale)); break;
    case 'hanzi-asc':   sorted.sort((a, b) => (a.word_zh || '').localeCompare(b.word_zh || '', 'zh')); break;
    case 'hanzi-desc':  sorted.sort((a, b) => (b.word_zh || '').localeCompare(a.word_zh || '', 'zh')); break;
    case 'newest':      sorted.sort((a, b) => b.id - a.id); break;
  }
  return sorted;
}

function onBrowseSort(val) {
  _browseSort = val;
  const q = document.getElementById('browse-search').value.trim();
  if (_browseMode === 'hanzi') renderHanziList(_allHanzi, q);
  else if (q) onBrowseSearch(q); else renderBrowseWords(_filteredBrowseWords());
}

function _filteredBrowseWords() {
  let words = browseWords;
  if (_browseFilter !== 'all') words = words.filter(w => w.note_type === _browseFilter);
  if (_browseDeckId !== null) words = words.filter(w => w.cards.some(c => c.deck_id === _browseDeckId));
  return words;
}

function setBrowseFilter(mode, filter) {
  _browseMode   = mode;
  _browseFilter = filter;
  _browseDeckId = null;
  // Update sidebar active state
  document.querySelectorAll('.bs-item').forEach(el => el.classList.remove('bs-active'));
  const btnId = mode === 'hanzi' ? 'bsf-hanzi' : `bsf-${filter}`;
  const btn = document.getElementById(btnId);
  if (btn) btn.classList.add('bs-active');
  document.getElementById('browse-search').value = '';
  _browseSelected.clear();
  _updateBrowseActionBar();
  if (mode === 'hanzi') renderHanziList(_allHanzi);
  else renderBrowseWords(_filteredBrowseWords());
}

function setBrowseDeckFilter(deckId) {
  _browseMode   = 'notes';
  _browseFilter = 'all';
  _browseDeckId = deckId;
  document.querySelectorAll('.bs-item, .bs-deck-item').forEach(el => el.classList.remove('bs-active'));
  document.querySelectorAll(`.bs-deck-item[data-id="${deckId}"]`).forEach(el => el.classList.add('bs-active'));
  document.getElementById('browse-search').value = '';
  _browseSelected.clear();
  _updateBrowseActionBar();
  renderBrowseWords(_filteredBrowseWords());
}

async function openBrowseForDeck(deckId) {
  await openBrowse();
  setBrowseDeckFilter(deckId);
}

async function openBrowse() {
  setLoading('Loading…');
  try {
    const [words, hanzi, deckTree] = await Promise.all([
      api('GET', '/api/browse-words'),
      api('GET', '/api/hanzi'),
      api('GET', '/api/decks'),
    ]);
    browseWords = words;
    _allHanzi = hanzi;
    _browseDecks = _flattenDecks(deckTree);
    _browseSelected.clear();
    showView('browse');
    document.getElementById('browse-search').value = '';
    document.getElementById('browse-sort').value = _browseSort;
    _renderBrowseSidebar();
    _updateBrowseActionBar();
    setBrowseFilter('notes', 'all');
  } catch (e) {
    showError('Browse failed: ' + e.message);
    showView('decks');
  }
}

function _flattenDecks(tree) {
  const result = [];
  function walk(nodes) {
    for (const n of nodes) {
      if (!n.virtual) result.push(n);
      if (n.children?.length) walk(n.children);
    }
  }
  walk(tree);
  return result;
}

function _renderBrowseSidebar() {
  // Deck tree in sidebar
  const container = document.getElementById('browse-deck-tree');
  const allDecks = browseWords.flatMap(w => w.cards.map(c => ({id: c.deck_id, name: c.deck_name})));
  const uniqueDecks = [...new Map(allDecks.map(d => [d.id, d])).values()];
  container.innerHTML = uniqueDecks.map(d =>
    `<button class="bs-deck-item" data-id="${d.id}" onclick="setBrowseDeckFilter(${d.id})">${d.name}</button>`
  ).join('');
}

function onBrowseSearch(val) {
  clearTimeout(_browseSearchTimer);
  const q = val.trim();
  if (_browseMode === 'hanzi') { renderHanziList(_allHanzi, q); return; }
  if (!q) { renderBrowseWords(_filteredBrowseWords()); return; }
  _browseSearchTimer = setTimeout(async () => {
    try {
      const result = await api('GET', `/api/search-words?q=${encodeURIComponent(q)}`);
      const base = _filteredBrowseWords();
      const primarySet   = new Set(result.primary);
      const secondarySet = new Set(result.secondary);
      const primary   = base.filter(w => primarySet.has(w.id));
      const secondary = base.filter(w => secondarySet.has(w.id));
      renderBrowseSearchResults(primary, secondary, q);
    } catch (e) { showError('Search failed: ' + e.message); }
  }, 250);
}

function _wordRow(w) {
  const dots = ['listening', 'reading', 'creating'].map(cat => {
    const c = w.cards.find(c => c.category === cat);
    return c
      ? `<span class="bw-dot bw-dot-${c.state}" title="${cat}: ${c.state}"></span>`
      : `<span class="bw-dot bw-dot-none" title="${cat}: —"></span>`;
  }).join('');
  const def = (w.definition || '').slice(0, 60) + ((w.definition || '').length > 60 ? '…' : '');
  const sel = _browseSelected.has(w.id) ? ' bw-row-selected' : '';
  return `
    <div class="bw-row${sel}" data-word-id="${w.id}" onclick="onBrowseRowClick(event,${w.id})">
      <div class="bw-left">
        <span class="bw-hanzi">${w.word_zh}</span>
        <span class="bw-pinyin">${w.pinyin || ''}</span>
      </div>
      <div class="bw-mid">
        <span class="bw-def">${def}</span>
      </div>
      <div class="bw-right">${dots}</div>
    </div>`;
}

function onBrowseRowClick(e, wordId) {
  if (e.metaKey || e.ctrlKey || _browseSelected.size > 0) {
    if (_browseSelected.has(wordId)) {
      _browseSelected.delete(wordId);
    } else {
      _browseSelected.add(wordId);
    }
    document.querySelectorAll(`.bw-row[data-word-id="${wordId}"]`).forEach(el => {
      el.classList.toggle('bw-row-selected', _browseSelected.has(wordId));
    });
    _updateBrowseActionBar();
  } else {
    openWordDetail(wordId);
  }
}

function _updateBrowseActionBar() {
  const bar = document.getElementById('browse-action-bar');
  const n = _browseSelected.size;
  if (!n) { bar.style.display = 'none'; return; }
  bar.style.display = 'flex';
  document.getElementById('ba-count').textContent = `${n} word${n > 1 ? 's' : ''} selected`;
  // Populate move deck dropdown
  const sel = document.getElementById('ba-move-deck');
  const current = sel.value;
  sel.innerHTML = _browseDecks
    .filter(d => !d.virtual)
    .map(d => `<option value="${d.id}">${d.name}</option>`)
    .join('');
  if (current) sel.value = current;
}

function clearBrowseSelection() {
  _browseSelected.clear();
  document.querySelectorAll('.bw-row-selected').forEach(el => el.classList.remove('bw-row-selected'));
  _updateBrowseActionBar();
}

function toggleBrowseMovePanel() {
  const panel = document.getElementById('ba-move-panel');
  panel.style.display = panel.style.display === 'none' ? '' : 'none';
}

async function browseActionBury() {
  const word_ids = [..._browseSelected];
  try {
    await api('POST', '/api/cards/bulk-bury', { word_ids });
    await _browseReload();
  } catch (e) { showError('Bury failed: ' + e.message); }
}

async function browseActionSuspend() {
  const word_ids = [..._browseSelected];
  try {
    await api('POST', '/api/cards/bulk-suspend', { word_ids });
    await _browseReload();
  } catch (e) { showError('Suspend failed: ' + e.message); }
}

async function browseActionDelete() {
  const n = _browseSelected.size;
  const ok = await showConfirm(`Delete ${n} note${n > 1 ? 's' : ''}? This cannot be undone.`);
  if (!ok) return;
  const word_ids = [..._browseSelected];
  try {
    await api('POST', '/api/cards/bulk-delete', { word_ids });
    await _browseReload();
  } catch (e) { showError('Delete failed: ' + e.message); }
}

async function browseActionMove() {
  const deck_id = parseInt(document.getElementById('ba-move-deck').value);
  if (!deck_id) return;
  const word_ids = [..._browseSelected];
  try {
    await api('POST', '/api/cards/bulk-move', { word_ids, deck_id });
    document.getElementById('ba-move-panel').style.display = 'none';
    await _browseReload();
  } catch (e) { showError('Move failed: ' + e.message); }
}

async function _browseReload() {
  const q = document.getElementById('browse-search').value.trim();
  browseWords = await api('GET', '/api/browse-words');
  _browseSelected.clear();
  _updateBrowseActionBar();
  if (q) onBrowseSearch(q);
  else renderBrowseWords(_filteredBrowseWords());
}

function renderBrowseWords(words) {
  const list = document.getElementById('browse-list');
  if (!words.length) {
    list.innerHTML = '<div class="browse-empty">No words found</div>';
    return;
  }
  list.innerHTML = `<div class="bw-list">${_sortWords(words).map(_wordRow).join('')}</div>`;
}

function renderBrowseSearchResults(primary, secondary, q) {
  const list = document.getElementById('browse-list');
  if (!primary.length && !secondary.length) {
    list.innerHTML = '<div class="browse-empty">No results for "' + q + '"</div>';
    return;
  }
  let html = '';
  if (primary.length) {
    html += `<div class="browse-section-label">Words (${primary.length})</div>
             <div class="bw-list">${_sortWords(primary).map(_wordRow).join('')}</div>`;
  }
  if (secondary.length) {
    html += `<div class="browse-section-label">Found in examples / notes (${secondary.length})</div>
             <div class="bw-list">${_sortWords(secondary).map(_wordRow).join('')}</div>`;
  }
  list.innerHTML = html;
}

function renderHanziList(hanzi, q = '') {
  const list = document.getElementById('browse-list');
  let items = hanzi;
  if (q) {
    const lq = q.toLowerCase();
    items = hanzi.filter(h =>
      h.char.includes(q) || (h.pinyin || '').toLowerCase().includes(lq) ||
      (h.etymology || '').toLowerCase().includes(lq)
    );
  }
  if (!items.length) {
    list.innerHTML = '<div class="browse-empty">No hanzi found</div>';
    return;
  }
  // Group alphabetically by pinyin first letter
  const groups = {};
  items.forEach(h => {
    const key = (h.pinyin || '?')[0].toUpperCase();
    (groups[key] = groups[key] || []).push(h);
  });
  const sortedKeys = Object.keys(groups).sort();
  let html = '';
  for (const key of sortedKeys) {
    html += `<div class="browse-section-label">${key}</div>
             <div class="bw-list">${groups[key].map(_hanziRow).join('')}</div>`;
  }
  list.innerHTML = html;
}

function _hanziRow(h) {
  const hsk = h.hsk_level ? `<span class="bw-hsk">HSK${h.hsk_level}</span>` : '';
  const etym = (h.etymology || '').slice(0, 60) + ((h.etymology || '').length > 60 ? '…' : '');
  return `<div class="bw-row" onclick="openHanziDetail(${h.id})">
    <div class="bw-left">
      <span class="bw-hanzi">${h.char}</span>
      <span class="bw-pinyin">${h.pinyin || ''}</span>
    </div>
    <div class="bw-mid"><span class="bw-def">${etym}</span></div>
    <div class="bw-right">${hsk}</div>
  </div>`;
}

// ── Word Detail ───────────────────────────────────────────────────────────────
async function openWordDetail(wordId) {
  _currentWordId = wordId;
  setLoading('Loading word…');
  try {
    const word = await api('GET', `/api/word/${wordId}`);
    word.cards = await api('GET', `/api/words/${wordId}/cards`);
    renderWordDetail(word);
    showView('word-detail');
  } catch (e) {
    showError('Failed to load word: ' + e.message);
    showView('browse');
  }
}

function renderWordDetail(word) {
  document.getElementById('wd-edit-btn').onclick = () => openWordEditModal(word);
  document.getElementById('wd-hanzi').textContent = word.word_zh || '';
  document.getElementById('wd-pinyin').textContent = word.pinyin || '';
  document.getElementById('wd-def').textContent = word.definition || '';
  const posEl = document.getElementById('wd-pos');
  posEl.textContent = word.pos || '';
  posEl.style.display = word.pos ? 'inline-block' : 'none';
  const defZhEl = document.getElementById('wd-def-zh');
  defZhEl.textContent = word.definition_zh || '';
  defZhEl.style.display = word.definition_zh ? 'block' : 'none';

  // Characters section — each hanzi is clickable
  const charsEl = document.getElementById('wd-chars-section');
  if (word.characters?.length) {
    const rows = word.characters.map(wc => {
      const char = wc.char || '';
      const pin  = wc.pinyin || '';
      const ctx  = wc.meaning_in_context ? `<span class="wd-ctx">${wc.meaning_in_context}</span>` : '';
      const etym = wc.etymology ? `<div class="wd-etym">${wc.etymology}</div>` : '';
      let meanings = '';
      try { meanings = wc.other_meanings ? JSON.parse(wc.other_meanings).join(', ') : ''; } catch {}
      const meanHtml = meanings ? `<span class="wd-char-meaning">${meanings}</span>` : '';
      const pinEsc = pin.replace(/'/g, "\\'");
      const charEsc = char.replace(/'/g, "\\'");
      return `<div class="wd-char-row wd-char-link" onclick="openHanziRegenModal(${wc.char_id},'${charEsc}','${pinEsc}')">
        <span class="wd-char-zh">${char}</span>
        <span class="wd-char-pin">${pin}</span>
        ${meanHtml}${ctx}${etym}
      </div>`;
    }).join('');
    charsEl.innerHTML =
      `<div class="wd-section-head section-toggle" onclick="toggleSection('wd-chars-body')">` +
        `<span id="wd-chars-body-arrow">▶</span> Characters</div>` +
      `<div id="wd-chars-body" class="wd-section-body" style="display:none">${rows}</div>`;
  } else {
    charsEl.innerHTML = '';
  }

  // Examples section
  const exEl = document.getElementById('wd-examples-section');
  if (word.examples?.length) {
    const rows = word.examples.map(ex => `
      <div class="wd-example-row">
        <div class="wd-ex-zh">${ex.example_zh || ''}</div>
        ${ex.example_pinyin ? `<div class="wd-ex-pin">${ex.example_pinyin}</div>` : ''}
        ${ex.example_de ? `<div class="wd-ex-de">${ex.example_de}</div>` : ''}
      </div>`).join('');
    exEl.innerHTML =
      `<div class="wd-section-head section-toggle" onclick="toggleSection('wd-examples-body')">` +
        `<span id="wd-examples-body-arrow">▶</span> Examples</div>` +
      `<div id="wd-examples-body" class="wd-section-body" style="display:none">${rows}</div>`;
  } else {
    exEl.innerHTML = '';
  }

  // Component words section (for sentences/chengyu) — clickable
  const compEl = document.getElementById('wd-components-section');
  if (word.components?.length) {
    const rows = word.components.map(comp => `
      <div class="wd-char-row wd-char-link" onclick="openWordDetail(${comp.id})">
        <span class="wd-char-zh">${comp.word_zh}</span>
        <span class="wd-char-pin">${comp.pinyin || ''}</span>
        <span class="wd-ctx">${comp.definition || ''}</span>
      </div>`).join('');
    compEl.innerHTML =
      `<div class="wd-section-head section-toggle" onclick="toggleSection('wd-comps-body')">` +
        `<span id="wd-comps-body-arrow">▶</span> Component Words</div>` +
      `<div id="wd-comps-body" class="wd-section-body" style="display:none">${rows}</div>`;
  } else {
    compEl.innerHTML = '';
  }

  // Cards section
  renderWordDetailCards(word.cards || [], word.id);
}

function renderWordDetailCards(cards, wordId) {
  const el = document.getElementById('wd-cards-section');
  if (!cards.length) { el.innerHTML = ''; return; }
  const CAT_FULL = { listening: 'Listening', reading: 'Reading', creating: 'Creating' };
  const rows = cards.map(c => {
    const isSuspended = c.state === 'suspended';
    const intv  = c.interval > 0 ? `${c.interval}d` : '—';
    const ease  = c.ease ? `${Math.round(c.ease * 100)}%` : '—';
    const due   = c.due ? c.due.slice(0, 10) : '—';
    const isBuried = c.buried_until && c.buried_until >= new Date().toISOString().slice(0, 10);
    return `
      <div class="wd-card-block" id="wd-card-${c.id}">
        <div class="wd-card-head">
          <span class="wd-cat-label">${CAT_FULL[c.category] || c.category}</span>
          <span class="badge badge-${c.state}">${c.state}</span>
          ${isBuried ? '<span class="badge badge-buried">buried</span>' : ''}
          <div class="wd-card-menu-wrap">
            <button class="wd-menu-btn" onclick="toggleCardMenu(${c.id}, event)">⋯</button>
            <div class="wd-card-menu" id="wd-menu-${c.id}" style="display:none">
              <button class="wd-menu-item" onclick="cardAction(${c.id}, 'bury', ${wordId})">Bury until tomorrow</button>
              <button class="wd-menu-item ${isSuspended ? 'wd-menu-item-active' : ''}"
                      onclick="cardAction(${c.id}, 'suspend', ${wordId})">
                ${isSuspended ? 'Unsuspend' : 'Suspend'}
              </button>
              <button class="wd-menu-item wd-menu-item-danger"
                      onclick="cardAction(${c.id}, 'reset', ${wordId})">Reset to new</button>
            </div>
          </div>
        </div>
        <div class="wd-card-stats">
          <span>Deck <b>${c.deck_path || c.deck_name || '—'}</b></span>
          <span>Interval <b>${intv}</b></span>
          <span>Due <b>${due}</b></span>
          <span>Ease <b>${ease}</b></span>
          <span>Lapses <b>${c.lapses}</b></span>
        </div>
      </div>`;
  }).join('');
  el.innerHTML = `<div class="wd-section-head">Cards</div><div class="wd-cards-list">${rows}</div>`;
}

function toggleCardMenu(cardId, e) {
  e.stopPropagation();
  const menu = document.getElementById(`wd-menu-${cardId}`);
  const isOpen = menu.style.display !== 'none';
  closeAllCardMenus();
  if (!isOpen) menu.style.display = 'block';
}

function closeAllCardMenus() {
  document.querySelectorAll('.wd-card-menu').forEach(m => m.style.display = 'none');
}

document.addEventListener('click', closeAllCardMenus);

async function cardAction(cardId, action, wordId) {
  closeAllCardMenus();
  try {
    await api('POST', `/api/cards/${cardId}/${action}`);
    const word = await api('GET', `/api/word/${wordId}`);
    renderWordDetailCards(word.cards || [], wordId);
  } catch (e) {
    showError(`Action failed: ${e.message}`);
  }
}

// ── Word edit (from word-detail view) ────────────────────────────────────────
function openWordEditModal(word) {
  _editFromWord = true;
  _openEditModal(word);
}

// ── Hanzi Regenerate Modal ───────────────────────────────────────────────────
let _regenCharId     = null;
let _regenFromReview = false;

function openHanziRegenModal(charId, char, pinyin, fromReview = false) {
  _regenCharId     = charId;
  _regenFromReview = fromReview;
  document.getElementById('hanzi-regen-char').textContent = char;
  document.getElementById('hanzi-regen-pin').textContent  = pinyin || '';
  document.getElementById('hanzi-regen-modal-overlay').style.display = '';
  document.getElementById('hanzi-regen-modal').style.display         = '';
}

function closeHanziRegenModal() {
  document.getElementById('hanzi-regen-modal-overlay').style.display = 'none';
  document.getElementById('hanzi-regen-modal').style.display         = 'none';
}

async function confirmHanziRegen() {
  closeHanziRegenModal();
  try {
    const updated = await api('POST', `/api/hanzi/${_regenCharId}/regenerate`);
    if (_regenFromReview) {
      // Patch in-memory wordDetails and re-render the card back without navigating away
      if (wordDetails?.characters) {
        wordDetails.characters = wordDetails.characters.map(c =>
          c.char_id === _regenCharId
            ? { ...c, etymology: updated.etymology, other_meanings: updated.other_meanings }
            : c
        );
      }
      renderVocabDetail();
    } else {
      if (_currentWordId) await openWordDetail(_currentWordId);
    }
  } catch (e) {
    showError('Regeneration failed: ' + e.message);
  }
}

// ── Hanzi Detail ─────────────────────────────────────────────────────────────
async function openHanziDetail(charId) {
  setLoading('Loading hanzi…');
  try {
    const hanzi = await api('GET', `/api/hanzi/${charId}`);
    renderHanziDetail(hanzi);
    showView('hanzi-detail');
  } catch (e) {
    showError('Failed to load hanzi: ' + e.message);
    showView('browse');
  }
}

function renderHanziDetail(h) {
  document.getElementById('hd-char').textContent   = h.char || '';
  document.getElementById('hd-pinyin').textContent = h.pinyin || '';
  const tradRow = document.getElementById('hd-trad-row');
  if (h.traditional) {
    document.getElementById('hd-trad').textContent = h.traditional;
    tradRow.style.display = '';
  } else {
    tradRow.style.display = 'none';
  }
  document.getElementById('hd-edit-btn').onclick = () => openHanziEditModal(h);

  let bodyHtml = '';

  if (h.etymology) {
    bodyHtml += `<div class="wd-section-head">Etymology</div>
      <div class="wd-section-body"><div class="wd-etym">${h.etymology}</div></div>`;
  }

  let compounds = [];
  try { compounds = h.compounds ? JSON.parse(h.compounds) : []; } catch {}
  if (compounds.length) {
    bodyHtml += `<div class="wd-section-head">Compounds</div>
      <div class="wd-section-body"><div class="hd-compounds">` +
      compounds.map(c => `<span class="hd-compound">${c}</span>`).join('') +
      `</div></div>`;
  }

  if (h.words?.length) {
    bodyHtml += `<div class="wd-section-head">Words containing ${h.char}</div>
      <div class="wd-section-body bw-list">` +
      h.words.map(w => `<div class="bw-row" onclick="openWordDetail(${w.id})">
        <div class="bw-left"><span class="bw-hanzi">${w.word_zh}</span><span class="bw-pinyin">${w.pinyin||''}</span></div>
        <div class="bw-mid"><span class="bw-def">${(w.definition||'').slice(0,60)}</span></div>
      </div>`).join('') +
      `</div>`;
  }

  document.getElementById('hd-body').innerHTML = bodyHtml || '<div class="browse-empty">No data</div>';
}

// ── Hanzi edit modal ──────────────────────────────────────────────────────────
let _editHanziId = null;

function openHanziEditModal(h) {
  _editHanziId = h.id;
  document.getElementById('hedit-pinyin').value    = h.pinyin    || '';
  document.getElementById('hedit-trad').value      = h.traditional || '';
  document.getElementById('hedit-hsk').value       = h.hsk_level != null ? h.hsk_level : '';
  document.getElementById('hedit-etym').value      = h.etymology  || '';
  document.getElementById('hedit-compounds').value = h.compounds  || '';
  document.getElementById('hanzi-edit-modal-overlay').style.display = '';
  document.getElementById('hanzi-edit-modal').style.display         = '';
}
function closeHanziEditModal() {
  document.getElementById('hanzi-edit-modal-overlay').style.display = 'none';
  document.getElementById('hanzi-edit-modal').style.display         = 'none';
}
async function saveHanziEdit() {
  const body = {
    pinyin:        document.getElementById('hedit-pinyin').value.trim(),
    traditional:   document.getElementById('hedit-trad').value.trim(),
    hsk_level:     document.getElementById('hedit-hsk').value ? parseInt(document.getElementById('hedit-hsk').value) : null,
    etymology:     document.getElementById('hedit-etym').value.trim(),
    compounds:     document.getElementById('hedit-compounds').value.trim(),
  };
  try {
    const updated = await api('PUT', `/api/hanzi/${_editHanziId}`, body);
    closeHanziEditModal();
    renderHanziDetail(updated);
  } catch (e) {
    showError('Save failed: ' + e.message);
  }
}

// ── applyFilters kept for legacy (no longer used by browse) ──────────────────
function applyFilters() {}

// ── Stats ────────────────────────────────────────────────────────────────────
async function openStats() {
  setLoading('Loading stats…');
  try {
    const data = await api('GET', '/api/stats');
    showView('stats');
    renderStats(data);
  } catch (e) {
    showError('Stats failed: ' + e.message);
    showView('decks');
  }
}

async function openCostModal() {
  try {
    const data = await api('GET', '/api/costs');
    renderCostModal(data);
    document.getElementById('cost-modal-overlay').style.display = 'block';
    document.getElementById('cost-modal').style.display = 'flex';
  } catch (e) {
    showError('Failed to load cost data: ' + e.message);
  }
}

function closeCostModal() {
  document.getElementById('cost-modal-overlay').style.display = 'none';
  document.getElementById('cost-modal').style.display = 'none';
}

function _formatPurpose(p) {
  if (!p || p === 'story') return 'Story';
  if (p.startsWith('hanzi:')) return 'Hanzi ' + p.slice(6);
  return p.charAt(0).toUpperCase() + p.slice(1);
}

function renderCostModal(data) {
  const fmt = n => '$' + n.toFixed(4);
  const fmtFull = n => '$' + n.toFixed(6);

  let html = `<div class="cost-total">Total spent <b>${fmt(data.total_cost)}</b></div>`;

  if (!data.calls.length) {
    html += '<div class="cost-empty">No API calls logged yet.</div>';
  } else {
    html += `<table class="cost-table">
      <thead><tr>
        <th>Date</th><th>Model</th><th>Purpose</th><th>Tokens in / out</th>
        <th style="text-align:right">Cost</th>
      </tr></thead><tbody>`;
    for (const c of data.calls) {
      const dt = c.called_at.slice(0, 16).replace('T', ' ');
      const model = c.model
        .replace('claude-', '')
        .replace('-20251001', '')
        .replace('deepseek-chat', 'DeepSeek V3')
        .replace('deepseek-reasoner', 'DeepSeek R1')
        .replace('glm-4-flash', 'GLM-4-Flash')
        .replace('glm-4-air', 'GLM-4-Air')
        .replace('qwen-turbo', 'Qwen Turbo')
        .replace('qwen-plus', 'Qwen Plus');
      const purpose = _formatPurpose(c.purpose);
      html += `<tr>
        <td style="color:var(--muted);font-size:12px;white-space:nowrap">${dt}</td>
        <td><span class="cost-model">${model}</span></td>
        <td style="color:var(--muted);font-size:12px">${purpose}</td>
        <td class="cost-num" style="color:var(--muted)">${c.input_tokens.toLocaleString()} / ${c.output_tokens.toLocaleString()}</td>
        <td class="cost-num cost-value">${fmtFull(c.cost)}</td>
      </tr>`;
    }
    html += '</tbody></table>';
  }

  document.getElementById('cost-modal-body').innerHTML = html;
}

function renderStats(data) {
  // Big numbers
  document.getElementById('stat-grid').innerHTML = [
    { num: data.streak_days,    label: 'Day Streak' },
    { num: data.total_words,    label: 'Total Words' },
    { num: data.reviews_today,  label: 'Reviews Today' },
    { num: data.new_today,      label: 'New Today' },
  ].map(s => `
    <div class="stat-card">
      <div class="stat-num">${s.num}</div>
      <div class="stat-label">${s.label}</div>
    </div>`).join('');

  // Bar chart
  const days = data.reviews_by_day || [];
  const maxCount = Math.max(...days.map(d => d.count), 1);
  document.getElementById('bar-chart').innerHTML = days.map(d => {
    const pct = Math.round((d.count / maxCount) * 100);
    const label = d.date.slice(5); // MM-DD
    return `
      <div class="bar-col" title="${d.date}: ${d.count}">
        <div class="bar-fill" style="height:${pct}%"></div>
        <div class="bar-day">${label}</div>
      </div>`;
  }).join('');

  // State pills
  const sc = data.state_counts || {};
  const STATES = ['new','learning','review','relearn','suspended'];
  const colors = { new:'var(--primary)', learning:'var(--hard)', review:'var(--good)',
                   relearn:'var(--again)', suspended:'var(--muted)' };
  document.getElementById('state-row').innerHTML = STATES.map(s => `
    <div class="state-pill">
      <div class="state-pill-num" style="color:${colors[s]}">${sc[s] || 0}</div>
      <div class="state-pill-label">${s}</div>
    </div>`).join('');
}

// ── Options modal ─────────────────────────────────────────────────────────────
let allPresets = [];

function loadPresetFields(preset) {
  document.getElementById('opt-new-per-day').value     = preset.new_per_day;
  document.getElementById('opt-reviews-per-day').value = preset.reviews_per_day;
  document.getElementById('opt-learn-steps').value     = preset.learning_steps;
  document.getElementById('opt-grad-int').value        = preset.graduating_interval;
  document.getElementById('opt-easy-int').value        = preset.easy_interval;
  document.getElementById('opt-relearn-steps').value   = preset.relearning_steps;
  document.getElementById('opt-leech').value           = preset.leech_threshold;
  document.getElementById('opt-new-gather-order').value        = preset.new_gather_order                || 'ascending_position';
  document.getElementById('opt-new-sort-order').value          = preset.new_sort_order                  || 'card_type_gathered';
  document.getElementById('opt-new-review-order').value        = preset.new_review_order                || 'mixed';
  document.getElementById('opt-interday-learning-order').value = preset.interday_learning_review_order  || 'mixed';
  document.getElementById('opt-review-sort-order').value       = preset.review_sort_order               || 'due_random';
  document.getElementById('opt-bury-new').checked      = !!preset.bury_new_siblings;
  document.getElementById('opt-bury-review').checked   = !!preset.bury_review_siblings;
  document.getElementById('opt-bury-interday').checked = !!preset.bury_interday_siblings;
  const btnDef = document.getElementById('btn-set-default');
  btnDef.textContent = preset.is_default ? '✓ Already default' : 'Set as default';
  btnDef.disabled = !!preset.is_default;
  const btnDel = document.getElementById('btn-delete-preset');
  btnDel.disabled = allPresets.length <= 1;
}

function renderPresetSelect(selectedId) {
  const sel = document.getElementById('opt-preset-select');
  sel.innerHTML = allPresets.map(p =>
    `<option value="${p.id}" ${p.id === selectedId ? 'selected' : ''}>${p.name}${p.is_default ? ' ★' : ''}</option>`
  ).join('');
}

async function openOptions(deckId) {
  optDeckId = deckId;
  try {
    const [preset, presets] = await Promise.all([
      api('GET', `/api/decks/${deckId}/preset`),
      api('GET', '/api/presets'),
    ]);
    allPresets = presets;
    renderPresetSelect(preset.id);
    loadPresetFields(preset);
    document.getElementById('modal-overlay').classList.add('open');
  } catch (e) {
    showError('Could not load options: ' + e.message);
  }
}

async function switchPreset(presetId) {
  presetId = parseInt(presetId);
  try {
    await api('PUT', `/api/decks/${optDeckId}/preset/assign?preset_id=${presetId}`);
    const preset = allPresets.find(p => p.id === presetId);
    loadPresetFields(preset);
  } catch (e) {
    showError('Failed to switch preset: ' + e.message);
  }
}

async function addPreset() {
  const name = prompt('Preset name:');
  if (!name) return;
  const currentId = parseInt(document.getElementById('opt-preset-select').value);
  try {
    const preset = await api('POST', `/api/presets?name=${encodeURIComponent(name)}&clone_from_id=${currentId}`);
    allPresets = await api('GET', '/api/presets');
    renderPresetSelect(preset.id);
    await switchPreset(preset.id);
  } catch (e) {
    showError('Failed to create preset: ' + e.message);
  }
}

async function renamePreset() {
  const currentId = parseInt(document.getElementById('opt-preset-select').value);
  const current = allPresets.find(p => p.id === currentId);
  const name = prompt('New name:', current?.name || '');
  if (!name || name === current?.name) return;
  try {
    await api('PUT', `/api/decks/${optDeckId}/preset`, { name });
    allPresets = await api('GET', '/api/presets');
    renderPresetSelect(currentId);
  } catch (e) {
    showError('Failed to rename: ' + e.message);
  }
}

async function deletePreset() {
  if (allPresets.length <= 1) return;
  const currentId = parseInt(document.getElementById('opt-preset-select').value);
  const current = allPresets.find(p => p.id === currentId);
  if (!confirm(`Delete preset "${current?.name}"? Decks using it will be reassigned to the default preset.`)) return;
  // First reassign all decks using this preset to the default
  const defaultPreset = allPresets.find(p => p.is_default && p.id !== currentId) || allPresets.find(p => p.id !== currentId);
  try {
    await api('PUT', `/api/decks/${optDeckId}/preset/assign?preset_id=${defaultPreset.id}`);
    await api('DELETE', `/api/presets/${currentId}`);
    allPresets = await api('GET', '/api/presets');
    renderPresetSelect(defaultPreset.id);
    loadPresetFields(defaultPreset);
  } catch (e) {
    showError('Delete failed: ' + e.message);
  }
}

function closeModal() {
  document.getElementById('modal-overlay').classList.remove('open');
  optDeckId = null;
}

async function saveOptions() {
  if (!optDeckId) return;
  const fields = {
    new_per_day:         parseInt(document.getElementById('opt-new-per-day').value),
    reviews_per_day:     parseInt(document.getElementById('opt-reviews-per-day').value),
    learning_steps:      document.getElementById('opt-learn-steps').value.trim(),
    graduating_interval: parseInt(document.getElementById('opt-grad-int').value),
    easy_interval:       parseInt(document.getElementById('opt-easy-int').value),
    relearning_steps:    document.getElementById('opt-relearn-steps').value.trim(),
    leech_threshold:     parseInt(document.getElementById('opt-leech').value),
    new_gather_order:               document.getElementById('opt-new-gather-order').value,
    new_sort_order:                 document.getElementById('opt-new-sort-order').value,
    new_review_order:               document.getElementById('opt-new-review-order').value,
    interday_learning_review_order: document.getElementById('opt-interday-learning-order').value,
    review_sort_order:              document.getElementById('opt-review-sort-order').value,
    bury_new_siblings:      document.getElementById('opt-bury-new').checked      ? 1 : 0,
    bury_review_siblings:   document.getElementById('opt-bury-review').checked   ? 1 : 0,
    bury_interday_siblings: document.getElementById('opt-bury-interday').checked ? 1 : 0,
  };
  // Warn if a story for today already exists — order settings change would cause mismatch
  if (story !== null) {
    const ok = confirm('You have an active story. Changing sort settings will affect card order and may no longer match the story. Continue?');
    if (!ok) return;
  }
  try {
    await api('PUT', `/api/decks/${optDeckId}/preset`, fields);
    closeModal();
    loadDecks();
  } catch (e) {
    showError('Save failed: ' + e.message);
  }
}

async function setDefaultPreset() {
  if (!optDeckId) return;
  try {
    await api('POST', `/api/decks/${optDeckId}/preset/set-default`);
    allPresets = await api('GET', '/api/presets');
    const currentId = parseInt(document.getElementById('opt-preset-select').value);
    renderPresetSelect(currentId);
    const btn = document.getElementById('btn-set-default');
    btn.textContent = '✓ Already default';
    btn.disabled = true;
  } catch (e) {
    showError('Failed: ' + e.message);
  }
}

// ── Start review session ────────────────────────────────────────────────────
async function startReview(id, cat, name, noStory = false) {
  deckId   = id;
  category = cat;
  deckName = name;

  try {
    if (noStory) {
      await _doStartReview(null, 2);
      return;
    }
    const [{ count, has_story }, todayCounts] = await Promise.all([
      api('GET', `/api/story/${deckId}/${category}/count`),
      api('GET', `/api/today/${deckId}/${category}`),
    ]);
    const learning = todayCounts?.counts?.learning || 0;
    if (has_story || count === 0) {
      await _doStartReview(null, 2);
    } else {
      await openStorySetup(count, { learningCount: learning });
    }
  } catch (e) {
    showError('Failed to start session: ' + e.message);
    showView('decks');
    return;
  }
}

async function _doStartReview(topic, maxHsk, model) {
  setLoading('Generating your story…');
  try {
    const storyUrl = `/api/story/${deckId}/${category}` + _storyParams(topic, maxHsk, model);
    const [todayData, storyData] = await Promise.all([
      api('GET', `/api/today/${deckId}/${category}`),
      api('GET', storyUrl),
    ]);

    story = await _resolveStory(storyData, deckId, category, topic, maxHsk);

    if (!todayData.card) {
      showView('done');
      return;
    }

    document.getElementById('loading-msg').textContent = 'Loading audio…';
    try {
      await fetch(`/api/preload-session/${deckId}/${category}`, { method: 'POST' });
    } catch (_) {}

    showView('review');
    loadCard(todayData.card, todayData.counts);
  } catch (e) {
    showError('Failed to start session: ' + e.message);
    showView('decks');
  }
}

function _storyParams(topic, maxHsk, model) {
  const p = new URLSearchParams();
  if (topic)                              p.set('topic', topic);
  if (maxHsk !== 2)                       p.set('max_hsk', maxHsk);
  if (model && model !== 'claude-haiku-4-5-20251001') p.set('model', model);
  const s = p.toString();
  return s ? '?' + s : '';
}

// ── Start mixed (all-category) review session ────────────────────────────────
async function startReviewMixed(id, name, noStory = false) {
  rootDeckId = id;
  deckId     = id;
  deckName   = name;
  story      = null;
  try {
    const todayData = await api('GET', `/api/today-mixed/${id}`);
    if (!todayData.card) {
      rootDeckId = null;
      showView('done');
      return;
    }
    if (noStory) {
      await _doStartReviewMixed(null, 2, null, true);
      return;
    }
    const c = todayData.counts;
    const total = (c.new || 0) + (c.learning || 0) + (c.review || 0);
    const learning = c.learning || 0;
    const firstCat = todayData.card.category;
    const { has_story } = await api('GET', `/api/story/${id}/${firstCat}/count`);
    if (has_story) {
      await _doStartReviewMixed(null, 2);
    } else {
      openStorySetup(total, { isMixed: true, learningCount: learning });
    }
  } catch (e) {
    showError('Failed to start session: ' + e.message);
    rootDeckId = null;
    showView('decks');
  }
}

async function _doStartReviewMixed(topic, maxHsk, model, noStory = false) {
  setLoading(noStory ? 'Loading…' : 'Generating stories…');
  try {
    const todayData = await api('GET', `/api/today-mixed/${rootDeckId}`);
    if (!todayData.card) {
      rootDeckId = null;
      showView('done');
      return;
    }
    category = todayData.card.category;

    if (!noStory) {
      // Await story for the first card's category so it's ready when review starts
      try {
        story = await api('GET', `/api/story/${rootDeckId}/${category}` + _storyParams(topic, maxHsk, model));
      } catch (_) {}
      // Fire story generation for the other categories in the background
      for (const cat of ['listening', 'reading', 'creating'].filter(c => c !== category)) {
        fetch(`/api/story/${rootDeckId}/${cat}` + _storyParams(topic, maxHsk, model)).catch(() => {});
      }
    }

    document.getElementById('loading-msg').textContent = 'Loading audio…';
    try {
      await fetch(`/api/preload-session/${rootDeckId}/${category}`, { method: 'POST' });
    } catch (_) {}

    showView('review');
    loadCard(todayData.card, todayData.counts);
  } catch (e) {
    showError('Failed to start session: ' + e.message);
    rootDeckId = null;
    showView('decks');
  }
}

// ── Start "Unfinished Cards" review session ───────────────────────────────────
async function startReviewUnfinished() {
  deckName = 'Unfinished Cards';
  story    = null;
  try {
    const counts = await api('GET', '/api/today-unfinished');
    if (!counts.card) {
      showView('done');
      return;
    }
    const total = counts.counts.learning || 0;
    openStorySetup(total, { isUnfinished: true });
  } catch (e) {
    showError('Failed to start session: ' + e.message);
    showView('decks');
  }
}

async function _doStartReviewUnfinished(topic, maxHsk, model) {
  unfinishedMode = true;
  setLoading('Loading cards…');
  try {
    const [combos, todayData] = await Promise.all([
      api('GET', '/api/today-unfinished-decks'),
      api('GET', '/api/today-unfinished'),
    ]);
    if (!todayData.card) {
      unfinishedMode = false;
      showView('done');
      return;
    }
    category = todayData.card.category;
    const firstDeckId = todayData.card.deck_id;
    // Await story for the first card's deck+category, fire the rest in background
    try {
      story = await api('GET', `/api/story/${firstDeckId}/${category}` + _storyParams(topic, maxHsk, model));
    } catch (_) {}
    for (const { deck_id, category: cat } of combos) {
      if (deck_id === firstDeckId && cat === category) continue;
      fetch(`/api/story/${deck_id}/${cat}` + _storyParams(topic, maxHsk, model)).catch(() => {});
    }
    showView('review');
    loadCard(todayData.card, todayData.counts);
  } catch (e) {
    showError('Failed to start session: ' + e.message);
    unfinishedMode = false;
    showView('decks');
  }
}

// ── Load a card ─────────────────────────────────────────────────────────────
function loadCard(c, counts) {
  card = c;
  wordDetails = null;

  // Update progress counts
  document.getElementById('cnt-new').textContent = counts.new;
  document.getElementById('cnt-lrn').textContent = counts.learning;
  document.getElementById('cnt-rev').textContent = counts.review;

  // Set interval labels on rating buttons (e.g. "1m", "10m", "4d")
  const iv = card.intervals || {};
  [1, 2, 3, 4].forEach(r => {
    document.getElementById(`int-${r}`).textContent = iv[r] || '';
  });

  // Find sentence for this card's word in the story.
  // If no exact match (e.g. using a historical story), fall back to the first sentence.
  sentence = story?.sentences?.find(s => s.word_id === card.word_id)
          || story?.sentences?.[0]
          || null;

  // Update sentence position counter
  const counter = document.getElementById('sentence-counter');
  if (sentence && story?.sentences?.length) {
    counter.textContent = `Sentence ${sentence.position + 1} / ${story.sentences.length}`;
    counter.style.display = 'block';
  } else {
    counter.style.display = 'none';
  }

  // Update card type badge (note type + category)
  const noteLabel = { vocabulary: 'Word', sentence: 'Sentence', chengyu: '成语', expression: '表达' }[card.note_type] || card.note_type;
  const catLetter = { reading: 'R', listening: 'L', creating: 'C' }[category] || category;
  document.getElementById('card-type-badge').textContent = `${noteLabel} · ${catLetter}`;

  // HSK badge — always visible; "HSK -" when unknown (click to AI-fill)
  const hskBadge = document.getElementById('card-hsk-badge');
  hskBadge.textContent = card.hsk_level ? `HSK ${card.hsk_level}` : 'HSK -';
  hskBadge.classList.toggle('hsk-unknown', !card.hsk_level);
  hskBadge.disabled = false;
  hskBadge.style.display = 'inline';

  // Reset pinyin toggle
  document.getElementById('pinyin-row').style.display = 'none';
  document.getElementById('pinyin-btn').classList.remove('active');

  // Close modals if open
  closeEditCard();
  closeStoryModal();
  document.getElementById('story-btn').style.display = 'none';

  // Preload full word details for the back side (local DB — near-instant)
  fetch(`/api/word/${c.word_id}`)
    .then(r => r.ok ? r.json() : null)
    .then(d => {
      wordDetails = d;
      // If back is already showing (user flipped before fetch completed), re-render
      if (document.getElementById('side-back').style.display !== 'none') {
        // Re-render interactive word-zh now that components are available
        const nt = wordDetails?.note_type || card.note_type;
        const wzEl = document.getElementById('word-zh');
        const isMultiWord = nt === 'sentence' || nt === 'chengyu' || nt === 'expression';
        if (isMultiWord && wordDetails?.components?.length) {
          wzEl.innerHTML = renderInteractiveZh(card.word_zh, wordDetails.components);
        }
        renderVocabDetail();
        renderNotesSection();
        _callRenderWordAnalysis();
      }
    })
    .catch(() => {});

  showFront();

  // Auto-play audio for the listening category
  if (category === 'listening') {
    playSentence();
  }
}

// ── Front of card ───────────────────────────────────────────────────────────
function showFront() {
  const isListening  = category === 'listening';
  const isCreating   = category === 'creating';
  const isSentence   = card.note_type === 'sentence';

  document.getElementById('side-front').style.display = 'flex';
  document.getElementById('side-front').style.flexDirection = 'column';
  document.getElementById('side-front').style.gap = '16px';
  document.getElementById('side-back').style.display = 'none';

  // Listening elements
  document.getElementById('front-listen-icon').style.display = isListening ? 'flex' : 'none';
  document.getElementById('back-meta-play-btn').style.display = 'none';

  // Reading: Chinese sentence
  const sentFront = document.getElementById('sentence-front');
  sentFront.style.display = (!isListening && !isCreating) ? 'flex' : 'none';
  if (!isListening && !isCreating) {
    sentFront.innerHTML = renderSentence();
  }

  // Creating: prompt + input
  document.getElementById('sentence-en-front').style.display   = isCreating ? 'flex' : 'none';
  document.getElementById('creating-input-wrap').style.display = isCreating ? 'flex' : 'none';
  if (isCreating) {
    // Sentence notes: show the German source sentence as the prompt
    // Other notes: show the AI story sentence in English
    const prompt = isSentence
      ? (card.source_sentence || card.definition || '')
      : (sentence?.sentence_en || '');
    document.getElementById('sentence-en-front').textContent = prompt;
    const inp = document.getElementById('creating-input');
    inp.value = '';
    userInput = '';
    // Focus input after a short delay so the card render doesn't steal it
    setTimeout(() => inp.focus(), 80);
  }

  // Rename reveal button for creating
  document.getElementById('reveal-btn').textContent = isCreating ? 'Check Answer' : 'Show Answer';
}

// ── Answer diff (creating category) ─────────────────────────────────────────
function diffAnswer(userInput, correct, wordZh) {
  if (!userInput) return { html: '(no answer)', pct: 0, bar: '░'.repeat(10) };

  const userChars = [...userInput];
  const corrChars = correct ? [...correct] : [];

  // Find where the target word starts in the user's input
  const wordIdx = userInput.indexOf(wordZh);
  const wordLen = [...wordZh].length;

  // Bag-of-characters: which hanzi from correct appear in user's answer?
  const hanzi = /[\u4e00-\u9fff\u3400-\u4dbf]/;
  const corrSet = new Set(corrChars.filter(ch => hanzi.test(ch)));
  const userSet = new Set(userChars.filter(ch => hanzi.test(ch)));
  const total   = corrSet.size;
  const matched = [...corrSet].filter(ch => userSet.has(ch)).length;
  const pct = total > 0 ? Math.round((matched / total) * 100) : 0;
  const filled = Math.round(pct / 10);
  const bar = '▓'.repeat(filled) + '░'.repeat(10 - filled);

  // Per-character coloring: green if char appears anywhere in correct sentence
  const html = userChars.map((ch, i) => {
    const inWord = wordIdx >= 0 && i >= wordIdx && i < wordIdx + wordLen;
    if (inWord) return `<span class="ch-target">${ch}</span>`;
    if (hanzi.test(ch) && corrSet.has(ch)) return `<span class="ch-match">${ch}</span>`;
    return `<span class="ch-miss">${ch}</span>`;
  }).join('');

  return { html, pct, bar };
}

// ── Back of card ────────────────────────────────────────────────────────────
function revealAnswer() {
  const isCreating = category === 'creating';

  // Capture user input before hiding front
  if (isCreating) {
    userInput = document.getElementById('creating-input').value.trim();
  }

  document.getElementById('side-front').style.display = 'none';
  document.getElementById('side-back').style.display  = 'flex';
  document.getElementById('side-back').style.flexDirection = 'column';
  document.getElementById('side-back').style.gap = '16px';
  document.getElementById('back-meta-play-btn').style.display = isCreating ? 'none' : 'flex';

  const isSentenceNote = card.note_type === 'sentence';

  if (isCreating) {
    // Show answer comparison block; hide normal sentence row
    document.getElementById('creating-answer-section').style.display = 'flex';
    document.getElementById('sentence-row-back').style.display = 'none';
    // Sentence notes: correct answer is card.word_zh (the whole sentence)
    const correctZh = isSentenceNote ? card.word_zh : sentence?.sentence_zh;
    const { html: userHtml, pct, bar } = diffAnswer(userInput, correctZh, card.word_zh);
    document.getElementById('user-answer-text').innerHTML = userHtml;
    const matchBar = document.getElementById('answer-match-bar');
    if (correctZh && userInput) {
      const color = pct >= 80 ? 'var(--good)' : pct >= 50 ? 'var(--hard)' : 'var(--again)';
      matchBar.innerHTML = `<span class="match-bar" style="color:${color}">${bar} ${pct}%</span>`;
      matchBar.style.display = 'block';
    } else {
      matchBar.style.display = 'none';
    }
    document.getElementById('correct-answer-text').innerHTML = renderSentence();
  } else {
    document.getElementById('creating-answer-section').style.display = 'none';
    document.getElementById('sentence-row-back').style.display = 'flex';
    document.getElementById('sentence-back').innerHTML = renderSentence();
  }

  // Sentence notes have no story — hide story button and English sentence from story
  document.getElementById('sentence-en').textContent = isSentenceNote
    ? (card.definition || '')
    : (sentence?.sentence_en || '');
  document.getElementById('story-btn').style.display =
    (!isSentenceNote && story?.sentences?.length > 1) ? 'block' : 'none';
  const noteType = wordDetails?.note_type || card.note_type;
  const wordZhEl = document.getElementById('word-zh');
  const isMultiWord = noteType === 'sentence' || noteType === 'chengyu' || noteType === 'expression';
  if (isMultiWord && wordDetails?.components?.length) {
    wordZhEl.innerHTML = renderInteractiveZh(card.word_zh, wordDetails.components);
  } else {
    wordZhEl.textContent = card.word_zh;
  }
  const wordPinEl = document.getElementById('word-pin');
  wordPinEl.textContent = isSentenceNote ? '' : (card.pinyin || '');
  wordPinEl.style.display = isSentenceNote ? 'none' : '';
  document.getElementById('word-def').textContent = card.definition || '';

  const posEl = document.getElementById('word-pos');
  posEl.textContent   = card.pos || '';
  posEl.style.display = card.pos ? 'inline-block' : 'none';

  // Re-enable rating buttons
  document.querySelectorAll('.r-btn').forEach(b => b.disabled = false);

  // Populate character breakdown, examples, notes, grammar, and word analysis
  renderVocabDetail();
  renderNotesSection();
  _callRenderWordAnalysis();

  // Auto-play audio on reveal for all categories
  playSentence();
}

// ── Populate vocab detail (chars + examples) ────────────────────────────────
function toggleSection(id) {
  const body = document.getElementById(id);
  const arrow = document.getElementById(id + '-arrow');
  const open = body.style.display !== 'none';
  body.style.display = open ? 'none' : 'block';
  arrow.textContent = open ? '▶' : '▼';
}

// ── Interactive sentence/chengyu rendering ───────────────────────────────────

// Wrap component words in hoverable spans; unmatched chars are plain text.
function renderInteractiveZh(text, components) {
  // Build a list of (start, end, compIdx) matches
  const matches = [];
  for (let i = 0; i < components.length; i++) {
    const w = components[i].word_zh;
    const pos = text.indexOf(w);
    if (pos !== -1) matches.push({ start: pos, end: pos + [...w].length, idx: i });
  }
  // Sort by start; drop overlaps
  matches.sort((a, b) => a.start - b.start);
  const used = [];
  for (const m of matches) {
    if (used.length && used[used.length - 1].end > m.start) continue;
    used.push(m);
  }
  // Build HTML char-by-char
  const chars = [...text];
  let html = '';
  let i = 0;
  for (const m of used) {
    while (i < m.start) html += chars[i++];
    const span = chars.slice(m.start, m.end).join('');
    html += `<span class="iword" data-comp-idx="${m.idx}" ` +
            `onmouseenter="showWordTip(${m.idx},this)" ` +
            `onmouseleave="hideWordTip()">${span}</span>`;
    i = m.end;
  }
  while (i < chars.length) html += chars[i++];
  return html;
}

let _tipTimeout = null;

function showWordTip(idx, el) {
  clearTimeout(_tipTimeout);
  const comp = wordDetails?.components?.[idx];
  if (!comp) return;

  const tipChars = comp.characters || [];
  let inner = `<div class="tip-header">
    <span class="tip-zh">${comp.word_zh}</span>
    ${comp.pinyin ? `<span class="tip-pin">${comp.pinyin}</span>` : ''}
  </div>`;
  if (comp.definition) inner += `<div class="tip-def">${comp.definition}</div>`;
  if (tipChars.length) {
    inner += `<hr class="tip-divider">`;
    for (const c of tipChars) {
      inner += `<div class="tip-char-row">
        <span class="tip-char-zh">${c.char}</span>
        ${c.pinyin ? `<span class="tip-char-pin">${c.pinyin}</span>` : ''}
        ${c.meaning_in_context ? `<span class="tip-char-ctx">— ${c.meaning_in_context}</span>` : ''}
      </div>`;
      if (c.etymology) inner += `<div class="tip-etym">${c.etymology.trim()}</div>`;
    }
  }

  const tip = document.getElementById('word-tip');
  tip.innerHTML = inner;
  tip.style.display = 'block';

  // Position: centred above (or below if not enough room)
  const rect = el.getBoundingClientRect();
  const tipW = Math.min(300, window.innerWidth - 24);
  let left = rect.left + rect.width / 2 - tipW / 2;
  left = Math.max(12, Math.min(left, window.innerWidth - tipW - 12));
  tip.style.maxWidth = tipW + 'px';
  tip.style.left = left + 'px';
  const tipH = tip.offsetHeight || 200;
  tip.style.top = rect.top > tipH + 8
    ? (rect.top - tipH - 8) + 'px'
    : (rect.bottom + 8) + 'px';
}

function hideWordTip() {
  _tipTimeout = setTimeout(() => {
    const tip = document.getElementById('word-tip');
    if (tip) tip.style.display = 'none';
  }, 80);
}

function renderVocabDetail() {
  // Characters
  const chars = wordDetails?.characters || [];
  const charSection = document.getElementById('char-section');
  if (chars.length > 0) {
    const rows = chars.map(c => {
      let right = '';
      if (c.pinyin)             right += `<div class="char-row-pin">${c.pinyin}</div>`;
      let meanings = '';
      try { meanings = c.other_meanings ? JSON.parse(c.other_meanings).join(', ') : ''; } catch {}
      if (meanings)             right += `<div class="char-row-info">${meanings}</div>`;
      if (c.meaning_in_context) right += `<div class="char-row-info">${c.meaning_in_context}</div>`;
      if (c.etymology)          right += `<div class="char-row-etym">${c.etymology}</div>`;
      const charEsc = (c.char || '').replace(/'/g, "\\'");
      const pinEsc  = (c.pinyin || '').replace(/'/g, "\\'");
      return `<div class="char-row char-row-link" onclick="openHanziRegenModal(${c.char_id},'${charEsc}','${pinEsc}',true)">` +
        `<div class="char-row-zh">${c.char}</div><div class="char-row-right">${right}</div></div>`;
    }).join('');
    charSection.innerHTML =
      `<div class="section-label section-toggle" onclick="toggleSection('char-section-body')">` +
        `<span id="char-section-body-arrow">▶</span> Characters</div>` +
      `<div id="char-section-body" style="display:none">${rows}</div>`;
  } else {
    charSection.innerHTML = '';
  }

  // Examples
  const examples = wordDetails?.examples || [];
  const exSection = document.getElementById('examples-section');
  if (examples.length > 0) {
    const items = examples.map(ex => {
      let html = `<div class="example-item">`;
      html += `<div class="example-zh">${ex.example_zh || ''}</div>`;
      if (ex.example_pinyin) html += `<div class="example-pin">${ex.example_pinyin}</div>`;
      if (ex.example_de)     html += `<div class="example-de">${ex.example_de}</div>`;
      html += `</div>`;
      return html;
    }).join('');
    exSection.innerHTML =
      `<div class="section-label section-toggle" onclick="toggleSection('ex-section-body')">` +
        `<span id="ex-section-body-arrow">▶</span> Examples</div>` +
      `<div id="ex-section-body" style="display:none">${items}</div>`;
  } else {
    exSection.innerHTML = '';
  }
}

function renderNotesSection() {
  const section = document.getElementById('notes-section');
  if (card?.notes) {
    section.innerHTML =
      `<div class="section-label section-toggle" onclick="toggleSection('notes-section-body')">` +
        `<span id="notes-section-body-arrow">▶</span> Notes</div>` +
      `<div id="notes-section-body" class="notes-body" style="display:none">${card.notes}</div>`;
    section.style.display = 'block';
  } else {
    section.innerHTML = '';
    section.style.display = 'none';
  }
}

function renderGrammarNotesSection() {
  const section = document.getElementById('grammar-notes-section');
  const grammar = wordDetails?.grammar_notes || card?.grammar_notes;
  if (grammar) {
    section.innerHTML =
      `<div class="section-label section-toggle" onclick="toggleSection('grammar-notes-body')">` +
        `<span id="grammar-notes-body-arrow">▶</span> Grammar</div>` +
      `<div id="grammar-notes-body" class="notes-body" style="display:none">${grammar}</div>`;
    section.style.display = 'block';
  } else {
    section.innerHTML = '';
    section.style.display = 'none';
  }
}

function renderWordAnalysis() {
  const section = document.getElementById('word-analysis-section');
  const nt = wordDetails?.note_type || card?.note_type;
  const isMultiWord = nt === 'sentence' || nt === 'chengyu' || nt === 'expression';
  const components = wordDetails?.components || [];

  if (!isMultiWord || components.length === 0) {
    section.innerHTML = '';
    return;
  }

  const expanded = false;

  const wordCards = components.map((comp, idx) => {
    const isCharOnly = !comp.definition && !comp.pos;
    let header = `<span class="wa-word-zh">${comp.word_zh || ''}</span>`;
    if (comp.pinyin) header += `<span class="wa-word-pin">${comp.pinyin}</span>`;
    if (!isCharOnly) {
      if (comp.hsk_level) header += `<span class="wa-hsk-badge">HSK ${comp.hsk_level}</span>`;
      if (comp.definition) header += `<span class="wa-word-def">${comp.definition}</span>`;
    }

    let body = '';
    if (!isCharOnly && comp.characters?.length) {
      body = comp.characters.map(c => {
        let right = '';
        if (c.pinyin)             right += `<span class="wa-char-pin">${c.pinyin}</span>`;
        if (c.meaning_in_context) right += `<span class="wa-char-ctx">${c.meaning_in_context}</span>`;
        if (c.etymology)          right += `<div class="wa-char-etym">${c.etymology}</div>`;
        return `<div class="wa-char-row">` +
          `<span class="wa-char-zh">${c.char}</span>` +
          `<div class="wa-char-right">${right}</div>` +
          `</div>`;
      }).join('');
    }

    const cardId = `wa-card-${idx}`;
    const bodyId = `wa-body-${idx}`;
    const hasChars = body.length > 0;
    return `<div class="wa-word-card" id="${cardId}">` +
      `<div class="wa-word-header${hasChars ? ' wa-word-header-toggle' : ''}"` +
        (hasChars ? ` onclick="toggleSection('${bodyId}')"` : '') + `>` +
        header +
        (hasChars ? `<span class="wa-toggle-arrow" id="${bodyId}-arrow">${expanded ? '▼' : '▶'}</span>` : '') +
      `</div>` +
      (hasChars ? `<div id="${bodyId}" class="wa-chars-list" style="display:${expanded ? 'block' : 'none'}">${body}</div>` : '') +
      `</div>`;
  }).join('');

  section.innerHTML =
    `<div class="section-label">Word Analysis</div>` +
    `<div class="wa-list">${wordCards}</div>`;
}

function _callRenderWordAnalysis() {
  renderGrammarNotesSection();
  renderWordAnalysis();
}

// ── Render sentence (with target word highlighted) ──────────────────────────
function renderSentence() {
  if (!sentence) {
    // No story sentence — just show the word itself
    return `<span class="hl">${card.word_zh}</span>`;
  }
  const zh   = sentence.sentence_zh;
  const word = card.word_zh;
  // Wrap in <span> so the flex container has a single child — avoids flex
  // treating the text node and the highlight span as separate block items
  const inner = zh.replace(word, `<span class="hl">${word}</span>`);
  return `<span>${inner}</span>`;
}

// ── Submit rating ───────────────────────────────────────────────────────────
async function rate(rating) {
  document.querySelectorAll('.r-btn').forEach(b => b.disabled = true);
  try {
    let url = `/api/review?card_id=${card.id}&rating=${rating}`;
    if (unfinishedMode) url += `&unfinished_mode=true`;
    else if (rootDeckId) url += `&root_deck_id=${rootDeckId}`;
    else if (deckId) url += `&parent_deck_id=${deckId}`;
    const result = await api('POST', url);
    if (!result.next_card) {
      rootDeckId = null;
      unfinishedMode = false;
      showView('done');
      return;
    }
    if (unfinishedMode || rootDeckId) category = result.next_card.category;
    loadCard(result.next_card, result.counts);
    document.getElementById('undo-btn').disabled = false;
  } catch (e) {
    showError('Submit failed: ' + e.message);
    document.querySelectorAll('.r-btn').forEach(b => b.disabled = false);
  }
}

// ── Undo last rating ─────────────────────────────────────────────────────────
async function undoReview() {
  try {
    const result = await api('POST', '/api/review/undo');
    loadCard(result.card, result.counts);
    // Show the back of the card so the user can re-rate
    revealAnswer();
    document.getElementById('undo-btn').disabled = true;
  } catch (e) {
    showError('Nothing to undo');
  }
}

// ── Pinyin toggle ────────────────────────────────────────────────────────────
let pinyinCache = {};

async function togglePinyin() {
  const row = document.getElementById('pinyin-row');
  const btn = document.getElementById('pinyin-btn');
  if (row.style.display !== 'none') {
    row.style.display = 'none';
    btn.classList.remove('active');
    return;
  }
  const text = sentence?.sentence_zh || card?.word_zh;
  if (!text) return;
  if (!pinyinCache[text]) {
    try {
      const data = await api('GET', `/api/pinyin?text=${encodeURIComponent(text)}`);
      pinyinCache[text] = data.syllables;
    } catch (e) {
      return;
    }
  }
  const syllables = pinyinCache[text];
  const chars = [...text];
  const wordStart = text.indexOf(card.word_zh);
  const wordEnd = wordStart + [...card.word_zh].length;
  row.innerHTML = chars.map((_ch, i) => {
    const py = syllables[i] || '';
    const isTarget = wordStart >= 0 && i >= wordStart && i < wordEnd;
    return `<span class="py-char${isTarget ? ' py-target' : ''}">`+
             `<span class="py-syl">${py}</span>`+
           `</span>`;
  }).join('');
  row.style.display = 'flex';
  btn.classList.add('active');
}

// ── Story error modal ─────────────────────────────────────────────────────────
let _storyErrorResolve = null;

function _openStoryErrorModal(errorData) {
  document.getElementById('story-error-msg').textContent =
    `Failed using ${errorData.model}: ${errorData.reason}`;
  const histBtn  = document.getElementById('story-error-history-btn');
  const histNote = document.getElementById('story-error-history-note');
  if (errorData.has_history) {
    histBtn.disabled = false;
    histBtn.style.opacity = '';
    histNote.textContent = '⚠ Saved sentences may not include all current words';
    histNote.style.display = '';
  } else {
    histBtn.disabled = true;
    histBtn.style.opacity = '0.4';
    histNote.style.display = 'none';
  }
  const sel = document.getElementById('story-error-model');
  for (const opt of sel.options) {
    if (opt.value !== errorData.model) { opt.selected = true; break; }
  }
  document.getElementById('story-error-overlay').style.display = 'block';
  document.getElementById('story-error-modal').style.display = 'flex';
  return new Promise(r => { _storyErrorResolve = r; });
}

function _closeStoryErrorModal() {
  document.getElementById('story-error-overlay').style.display = 'none';
  document.getElementById('story-error-modal').style.display = 'none';
}

function storyErrorSkip() {
  _closeStoryErrorModal();
  if (_storyErrorResolve) { _storyErrorResolve({ action: 'skip' }); _storyErrorResolve = null; }
}

function storyErrorRetry() {
  const model = document.getElementById('story-error-model').value;
  _closeStoryErrorModal();
  if (_storyErrorResolve) { _storyErrorResolve({ action: 'retry', model }); _storyErrorResolve = null; }
}

function storyErrorUseHistory() {
  _closeStoryErrorModal();
  if (_storyErrorResolve) { _storyErrorResolve({ action: 'history' }); _storyErrorResolve = null; }
}

async function _resolveStory(storyData, resolvedeckId, resolveCat, topic, maxHsk) {
  if (!storyData?.error) return storyData;
  const choice = await _openStoryErrorModal(storyData);
  if (choice.action === 'skip') return null;
  if (choice.action === 'history') {
    try { return await api('GET', `/api/story/${resolvedeckId}/${resolveCat}/history`); }
    catch (_) { return null; }
  }
  // retry with new model — not counted toward the 2-attempt limit
  setLoading('Generating your story…');
  let newData;
  try {
    newData = await api('GET', `/api/story/${resolvedeckId}/${resolveCat}` + _storyParams(topic, maxHsk, choice.model));
  } catch (e) {
    newData = { error: true, reason: e.message, model: choice.model, has_history: storyData.has_history };
  }
  return _resolveStory(newData, resolvedeckId, resolveCat, topic, maxHsk);
}

// ── Story setup modal ────────────────────────────────────────────────────────
let _setupResolve = null;
let _setupIsRegen = false;
let _setupIsMixed = false;
let _setupIsUnfinished = false;

function openStorySetup(sentenceCount, { isMixed = false, isUnfinished = false, learningCount = 0 } = {}) {
  _setupIsRegen = !isMixed && !isUnfinished && !!card; // card exists (fresh single-cat) → regenerating
  _setupIsMixed = isMixed;
  _setupIsUnfinished = isUnfinished;
  document.getElementById('setup-count-label').textContent =
    `This story will have ${sentenceCount} sentence${sentenceCount !== 1 ? 's' : ''}.`;
  const warn = document.getElementById('setup-learning-warning');
  if (learningCount > 0) {
    warn.textContent = `⚠ ${learningCount} card${learningCount !== 1 ? 's' : ''} still in the Again queue. Generating now may cause a mismatch between story order and review order.`;
    warn.style.display = 'block';
  } else {
    warn.style.display = 'none';
  }
  document.getElementById('setup-topic').value = '';
  document.getElementById('setup-hsk-slider').value = 2;
  updateHskLabel();
  document.getElementById('setup-modal-overlay').style.display = 'block';
  document.getElementById('setup-modal').style.display        = 'flex';
  document.getElementById('setup-topic').focus();
  return new Promise(resolve => { _setupResolve = resolve; });
}

function togglePriceTable(e) {
  e.preventDefault();
  e.stopPropagation();
  const popup = document.getElementById('price-table-popup');
  popup.style.display = popup.style.display === 'none' ? 'block' : 'none';
}

function updateHskLabel() {
  const v = document.getElementById('setup-hsk-slider').value;
  document.getElementById('setup-hsk-badge').textContent = `HSK ${v}`;
}

function confirmStorySetup() {
  const topic  = document.getElementById('setup-topic').value.trim() || null;
  const maxHsk = parseInt(document.getElementById('setup-hsk-slider').value, 10);
  const model  = document.getElementById('setup-model').value;
  _closeSetupModal();
  if (_setupIsRegen) {
    _doRegenerateStory(topic, maxHsk, model);
  } else if (_setupIsUnfinished) {
    _doStartReviewUnfinished(topic, maxHsk, model);
  } else if (_setupIsMixed) {
    _doStartReviewMixed(topic, maxHsk, model);
  } else {
    _doStartReview(topic, maxHsk, model);
  }
}

function cancelStorySetup() {
  _closeSetupModal();
  if (!_setupIsRegen) showView('decks');
}

function _closeSetupModal() {
  document.getElementById('setup-modal-overlay').style.display = 'none';
  document.getElementById('setup-modal').style.display        = 'none';
  document.getElementById('price-table-popup').style.display  = 'none';
}

// ── Story modal ───────────────────────────────────────────────────────────────
function openStoryModal() {
  if (!story?.sentences?.length) return;
  const currentPos = sentence?.position ?? -1;
  const html = story.sentences.map(s => {
    const isCurrent = s.position === currentPos;
    const highlighted = s.sentence_zh.replace(
      s.word_zh,
      `<span class="story-target">${s.word_zh}</span>`
    );
    const esc = encodeURIComponent(s.sentence_zh);
    return `<div class="story-sentence${isCurrent ? ' story-sentence-current' : ''}">
      <span class="story-num">${s.position + 1}</span>
      <div class="story-content">
        <div class="story-zh">${highlighted}</div>
        <div class="story-en">${s.sentence_en}</div>
      </div>
      <button class="story-play-btn" onclick="playStoryLine('${esc}')" title="Play">▶</button>
    </div>`;
  }).join('');
  document.getElementById('story-modal-body').innerHTML = html;
  document.getElementById('story-modal-overlay').style.display = 'block';
  document.getElementById('story-modal').style.display = 'flex';
}

async function playStoryLine(encodedText) {
  try { await api('POST', `/api/speak?text=${encodedText}`); }
  catch (e) { showError('TTS failed: ' + e.message); }
}

let _storyPlaying = false;

async function toggleFullStory() {
  if (_storyPlaying) {
    stopFullStory();
    return;
  }
  if (!story?.sentences?.length) return;
  _storyPlaying = true;
  const btn = document.getElementById('story-play-all-btn');
  btn.textContent = '■ Stop';
  try {
    await api('POST', '/api/speak-multi', { texts: story.sentences.map(s => s.sentence_zh) });
  } catch (e) { /* stopped or error — ignore */ }
  _storyPlaying = false;
  btn.textContent = '▶ Play full story';
}

function stopFullStory() {
  if (!_storyPlaying) return;
  _storyPlaying = false;
  document.getElementById('story-play-all-btn').textContent = '▶ Play full story';
  api('POST', '/api/speak-stop').catch(() => {});
}

function closeStoryModal() {
  stopFullStory();
  document.getElementById('story-modal-overlay').style.display = 'none';
  document.getElementById('story-modal').style.display = 'none';
}

// ── Edit card modal ───────────────────────────────────────────────────────────
let _editWordId   = null;   // word ID being edited
let _editFromWord = false;  // true when opened from word-detail view

function _openEditModal(wordObj) {
  _editWordId = wordObj.id || wordObj.word_id;
  document.getElementById('edit-word-zh').value       = wordObj.word_zh       || '';
  document.getElementById('edit-pinyin').value        = wordObj.pinyin        || '';
  document.getElementById('edit-definition').value    = wordObj.definition    || '';
  document.getElementById('edit-pos').value           = wordObj.pos           || '';
  document.getElementById('edit-traditional').value   = wordObj.traditional   || '';
  document.getElementById('edit-definition-zh').value = wordObj.definition_zh || '';
  document.getElementById('edit-notes').value         = wordObj.notes         || '';
  // Show card action menu only when opened during active review
  const menuWrap = document.getElementById('edit-card-menu-wrap');
  menuWrap.style.display = _editFromWord ? 'none' : 'inline-block';
  if (!_editFromWord && card) {
    const isSuspended = card.state === 'suspended';
    document.getElementById('edit-suspend-btn').textContent = isSuspended ? 'Unsuspend' : 'Suspend';
  }
  document.getElementById('edit-card-menu').style.display = 'none';
  document.getElementById('edit-modal-overlay').style.display = 'block';
  document.getElementById('edit-modal').style.display         = 'flex';
}

function openEditCard() {
  _editFromWord = false;
  _openEditModal(card);
}

function openEditCardFromDetail(wordId) {
  closeAllCardMenus();
  _editFromWord = true;
  api('GET', `/api/word/${wordId}`).then(w => _openEditModal(w)).catch(e => showError(e.message));
}

function closeEditCard() {
  document.getElementById('edit-modal-overlay').style.display = 'none';
  document.getElementById('edit-modal').style.display         = 'none';
  document.getElementById('edit-card-menu').style.display     = 'none';
}

function toggleEditCardMenu(e) {
  e.stopPropagation();
  const menu = document.getElementById('edit-card-menu');
  menu.style.display = menu.style.display === 'none' ? 'block' : 'none';
}

document.addEventListener('click', () => {
  const menu = document.getElementById('edit-card-menu');
  if (menu) menu.style.display = 'none';
});

async function editModalCardAction(action) {
  if (!card) return;
  const cardId = card.id;
  closeEditCard();
  try {
    if (action === 'delete') {
      await api('DELETE', `/api/cards/${cardId}`);
    } else {
      await api('POST', `/api/cards/${cardId}/${action}`);
    }
    // Advance to next card
    let nextData;
    if (unfinishedMode) {
      nextData = await api('GET', '/api/today-unfinished');
    } else if (rootDeckId) {
      nextData = await api('GET', `/api/today-mixed/${rootDeckId}`);
    } else {
      nextData = await api('GET', `/api/today/${deckId}/${category}`);
    }
    if (!nextData.card) {
      rootDeckId = null;
      unfinishedMode = false;
      showView('done');
      return;
    }
    if (unfinishedMode || rootDeckId) category = nextData.card.category;
    loadCard(nextData.card, nextData.counts);
  } catch (e) {
    showError(`Action failed: ${e.message}`);
  }
}

async function saveEditCard() {
  const body = {
    word_zh:       document.getElementById('edit-word-zh').value.trim(),
    pinyin:        document.getElementById('edit-pinyin').value.trim(),
    definition:    document.getElementById('edit-definition').value.trim(),
    pos:           document.getElementById('edit-pos').value.trim(),
    traditional:   document.getElementById('edit-traditional').value.trim(),
    definition_zh: document.getElementById('edit-definition-zh').value.trim(),
    notes:         document.getElementById('edit-notes').value.trim(),
  };
  try {
    const updated = await api('PUT', `/api/word/${_editWordId}`, body);
    if (_editFromWord) {
      // Refresh word-detail header in place
      document.getElementById('wd-hanzi').textContent  = updated.word_zh || '';
      document.getElementById('wd-pinyin').textContent = updated.pinyin  || '';
      document.getElementById('wd-def').textContent    = updated.definition || '';
      const posEl = document.getElementById('wd-pos');
      posEl.textContent = updated.pos || '';
      posEl.style.display = updated.pos ? 'inline-block' : 'none';
      const defZhEl = document.getElementById('wd-def-zh');
      defZhEl.textContent    = updated.definition_zh || '';
      defZhEl.style.display  = updated.definition_zh ? 'block' : 'none';
    } else {
      // Refresh review card in place
      Object.assign(card, {
        word_zh: updated.word_zh, pinyin: updated.pinyin,
        definition: updated.definition, pos: updated.pos,
        traditional: updated.traditional, definition_zh: updated.definition_zh,
        notes: updated.notes,
      });
      document.getElementById('word-zh').textContent  = updated.word_zh || '';
      document.getElementById('word-pin').textContent = updated.pinyin  || '';
      document.getElementById('word-def').textContent = updated.definition || '';
      const posEl = document.getElementById('word-pos');
      posEl.textContent   = updated.pos || '';
      posEl.style.display = updated.pos ? 'inline-block' : 'none';
      renderNotesSection();
    }
    closeEditCard();
  } catch (e) {
    showError('Save failed: ' + e.message);
  }
}

// ── AI Enrich (HSK badge click) ──────────────────────────────────────────────
async function enrichCard() {
  if (!card) return;
  const badge = document.getElementById('card-hsk-badge');
  badge.textContent = '…';
  badge.disabled = true;
  try {
    const updated = await api('POST', `/api/word/${card.word_id}/ai-enrich`);
    // Update in-memory card HSK level
    if (updated?.hsk_level) card.hsk_level = updated.hsk_level;
    badge.textContent = card.hsk_level ? `HSK ${card.hsk_level}` : 'HSK -';
    badge.classList.toggle('hsk-unknown', !card.hsk_level);
    // Refresh word detail if back is visible
    if (updated && document.getElementById('side-back').style.display !== 'none') {
      wordDetails = updated;
      renderVocabDetail();
      _callRenderWordAnalysis();
    }
  } catch (e) {
    badge.textContent = card.hsk_level ? `HSK ${card.hsk_level}` : 'HSK -';
    showError('AI enrich failed: ' + e.message);
  } finally {
    badge.disabled = false;
  }
}

// ── TTS ─────────────────────────────────────────────────────────────────────
async function playSentence() {
  const text = sentence?.sentence_zh || card?.word_zh;
  if (!text) return;
  try {
    await api('POST', `/api/speak?text=${encodeURIComponent(text)}`);
  } catch (e) {
    showError('TTS failed: ' + e.message);
  }
}

// ── Regenerate story ─────────────────────────────────────────────────────────
async function regenerateStory() {
  const count = story?.sentences?.length ?? 0;
  let learning = 0;
  try {
    if (deckId && category) {
      const todayCounts = await api('GET', `/api/today/${deckId}/${category}`);
      learning = todayCounts?.counts?.learning || 0;
    }
  } catch (_) {}
  try {
    await openStorySetup(count, { learningCount: learning });
  } catch (_) {
    showView('review');
  }
}

async function _doRegenerateStory(topic, maxHsk, model) {
  setLoading('Regenerating story…');
  try {
    const storyData = await api('POST', `/api/story/${deckId}/${category}/regenerate` + _storyParams(topic, maxHsk, model));
    story = await _resolveStory(storyData, deckId, category, topic, maxHsk);
    sentence = story?.sentences?.find(s => s.word_id === card.word_id) || null;
    try {
      await fetch(`/api/preload-session/${deckId}/${category}`, { method: 'POST' });
    } catch (_) {}
    showView('review');
    showFront();
  } catch (e) {
    showError('Regenerate failed: ' + e.message);
    showView('review');
  }
}

// ── Back to decks ────────────────────────────────────────────────────────────
function goBack() {
  if (document.getElementById('view-word-detail').style.display !== 'none') {
    showView('browse');
    return;
  }
  if (document.getElementById('view-hanzi-detail').style.display !== 'none') {
    showView('browse');
    return;
  }
  card = null; story = null; sentence = null; wordDetails = null; userInput = '';
  rootDeckId = null; unfinishedMode = false;
  browseWords = []; browseAll = []; _browseSelected.clear();
  loadDecks();
}

// ── Import modal ─────────────────────────────────────────────────────────────

let importResolutions = {};    // {word_zh: "keep"|"update"|"custom"}
let _previewEntries = [];      // full entry list from last preview (with raw_yaml)
let _cardConfigs = {};         // {word_zh: {include, deck_path, suspended:{reading,listening,creating}}}
let _importDeckOptions = [];   // flat list of deck paths for per-card dropdowns
let _conflictData = [];        // full conflict list from last preview
let _conflictEdits = {};       // {word_zh: {field: value}} custom edits
let _conflictSelections = {};  // {word_zh: "keep"|"update"}

// Default per-category suspension states (creating active, others suspended)
const IMPORT_DEFAULT_SUSPENDED = { reading: false, listening: false, creating: true };

const NOTE_TYPE_LABEL = { vocabulary: 'Word', sentence: 'Sentence', chengyu: '成语', expression: 'Expr' };
const STATUS_ICON  = { ok: '✓', duplicate: '⚠', invalid: '✕' };
const STATUS_COLOR = { ok: 'var(--clr-ok,#27ae60)', duplicate: '#e67e22', invalid: '#e74c3c' };

// Escape a value for use in an HTML attribute (prevents quote-breaking)
function _ea(str) { return String(str ?? '').replace(/&/g,'&amp;').replace(/"/g,'&quot;'); }

function openYamlEditFromBtn(btn) {
  const idx = btn.dataset.idx !== undefined ? parseInt(btn.dataset.idx) : -1;
  openYamlEdit(btn.dataset.word, btn.dataset.yaml, btn.dataset.deck, idx);
}

// ── Render the per-card import table ─────────────────────────────────────────

function _importRenderTable() {
  const tbody = document.getElementById('import-table-body');
  const globalDeck = document.getElementById('import-deck-path').value.trim();

  const deckOptHtml = `<option value="">— default —</option>` +
    _importDeckOptions.map(p => `<option value="${_ea(p)}">${p}</option>`).join('');

  tbody.innerHTML = _previewEntries.map((e, idx) => {
    const cfg = _cardConfigs[e.simplified] || {};
    const include  = cfg.include ?? (e.status !== 'invalid');
    const susp     = cfg.suspended || IMPORT_DEFAULT_SUSPENDED;
    const deckVal  = cfg.deck_path || '';
    const isInvalid = e.status === 'invalid';

    const rowClass = isInvalid ? 'import-row-invalid' : (!include ? 'import-row-excluded' : '');

    const inclBtnCls = isInvalid ? 'import-toggle-btn inactive' :
                       (include ? 'import-toggle-btn active' : 'import-toggle-btn inactive');
    const inclLabel  = include ? '+' : '−';
    const inclDisabled = isInvalid ? 'disabled' : '';

    const suspBtn = (cat) => {
      const isSusp = susp[cat] ?? IMPORT_DEFAULT_SUSPENDED[cat];
      const cls = isSusp ? 'import-toggle-btn suspended' : 'import-toggle-btn unsuspended';
      const lbl = isSusp ? '✕' : '✓';
      const dis = (!include || isInvalid) ? 'disabled' : '';
      return `<button class="${cls}" ${dis}
        onclick="importToggleSuspended(${_ea(JSON.stringify(e.simplified))}, '${cat}')"
        title="${isSusp ? 'suspended — click to activate' : 'active — click to suspend'}">${lbl}</button>`;
    };

    const statusSpan = `<span style="color:${STATUS_COLOR[e.status]}">${STATUS_ICON[e.status]}</span>` +
      (e.reason ? ` <span style="font-size:10px;color:${STATUS_COLOR[e.status]}" title="${_ea(e.reason)}">!</span>` : '');

    return `<tr class="${rowClass}" id="import-row-${idx}">
      <td>
        <button class="${inclBtnCls}" ${inclDisabled}
          onclick="importToggleInclude(${_ea(JSON.stringify(e.simplified))})">${inclLabel}</button>
      </td>
      <td style="font-weight:500" title="${_ea(e.simplified)}">${e.simplified.length > 6 ? e.simplified.slice(0,4) + '…' : e.simplified}
        ${e.raw_yaml ? `<button class="edit-cancel-btn" style="font-size:10px;padding:1px 5px;margin-left:4px"
          data-word="${_ea(e.simplified)}" data-yaml="${_ea(e.raw_yaml)}" data-deck="" data-idx="${idx}"
          onclick="openYamlEditFromBtn(this)">Edit</button>` : ''}
      </td>
      <td style="color:var(--clr-muted,#888);font-size:11px;max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${_ea(e.english || '')}">${e.english || ''}</td>
      <td style="color:var(--clr-muted,#888);font-size:11px">${NOTE_TYPE_LABEL[e.note_type] || e.note_type}</td>
      <td style="color:var(--clr-muted,#888);font-size:11px">${e.hsk || ''}</td>
      <td>${statusSpan}</td>
      <td>${suspBtn('listening')}</td>
      <td>${suspBtn('reading')}</td>
      <td>${suspBtn('creating')}</td>
      <td>
        <select class="import-row-deck-select"
          onchange="importSetCardDeck(${_ea(JSON.stringify(e.simplified))}, this.value)"
          ${(!include || isInvalid) ? 'disabled' : ''}>
          ${deckOptHtml}
        </select>
      </td>
    </tr>`;
  }).join('');

  // Set selected deck value for each row's <select>
  tbody.querySelectorAll('select.import-row-deck-select').forEach((sel, i) => {
    const e = _previewEntries[i];
    if (!e) return;
    sel.value = (_cardConfigs[e.simplified] || {}).deck_path || '';
  });
}

function importToggleInclude(wordZh) {
  const cfg = _cardConfigs[wordZh] || {};
  _cardConfigs[wordZh] = { ...cfg, include: !(cfg.include ?? true) };
  _importRenderTable();
}

function importToggleSuspended(wordZh, category) {
  const cfg = _cardConfigs[wordZh] || {};
  const susp = { ...IMPORT_DEFAULT_SUSPENDED, ...(cfg.suspended || {}) };
  susp[category] = !susp[category];
  _cardConfigs[wordZh] = { ...cfg, suspended: susp };
  _importRenderTable();
}

function importSetCardDeck(wordZh, deckPath) {
  const cfg = _cardConfigs[wordZh] || {};
  _cardConfigs[wordZh] = { ...cfg, deck_path: deckPath || null };
}

function importSelectAll(include) {
  _previewEntries.forEach(e => {
    if (e.status === 'invalid') return;
    const cfg = _cardConfigs[e.simplified] || {};
    _cardConfigs[e.simplified] = { ...cfg, include };
  });
  _importRenderTable();
}

function importSetAllSuspended(category, suspended) {
  _previewEntries.forEach(e => {
    if (e.status === 'invalid') return;
    const cfg = _cardConfigs[e.simplified] || {};
    const susp = { ...IMPORT_DEFAULT_SUSPENDED, ...(cfg.suspended || {}) };
    susp[category] = suspended;
    _cardConfigs[e.simplified] = { ...cfg, suspended: susp };
  });
  _importRenderTable();
}

function importApplyGlobalDeck() {
  // When global deck changes, cards using the default automatically use it
  // (deck_path: null means "use global default"), so just re-render
  _importRenderTable();
}

async function openImportModal() {
  importResolutions = {};
  _previewEntries = [];
  _cardConfigs = {};
  _conflictData = [];
  _conflictEdits = {};
  _conflictSelections = {};
  document.getElementById('import-file').value = '';
  document.getElementById('import-preview').style.display = 'none';
  document.getElementById('import-conflicts-section').style.display = 'none';
  document.getElementById('import-deck-section').style.display = 'none';
  document.getElementById('import-result').style.display = 'none';
  document.getElementById('import-submit-btn').style.display = 'none';
  document.getElementById('import-deck-path').value = '';
  document.getElementById('deck-picker-new-badge').style.display = 'none';
  document.getElementById('deck-picker-dropdown').style.display = 'none';

  // Build suggestion list for deck picker and per-card dropdown
  const decks = await api('GET', '/api/decks');
  window._deckSuggestions = [];
  _importDeckOptions = [];
  function addDeckSuggestions(list, prefix) {
    for (const d of list) {
      if (d.virtual) {
        if (d.children && d.children.length) addDeckSuggestions(d.children, prefix);
        continue;
      }
      if (d.category) continue;
      const path = prefix ? `${prefix}::${d.name}` : d.name;
      window._deckSuggestions.push(path);
      _importDeckOptions.push(path);
      if (d.children && d.children.length) addDeckSuggestions(d.children, path);
    }
  }
  addDeckSuggestions(decks, '');

  // Open file picker directly — modal opens after file is selected
  document.getElementById('import-file').click();
}

function closeImportModal() {
  document.getElementById('import-modal-overlay').style.display = 'none';
  document.getElementById('import-modal').style.display = 'none';
  const btn = document.getElementById('import-submit-btn');
  btn.onclick = doImport;
}

function onImportFileChange() {
  const fileInput = document.getElementById('import-file');
  if (!fileInput.files.length) return;  // user cancelled picker

  importResolutions = {};
  _previewEntries = [];
  _cardConfigs = {};
  _conflictData = [];
  _conflictEdits = {};
  _conflictSelections = {};
  document.getElementById('import-preview').style.display = 'none';
  document.getElementById('import-conflicts-section').style.display = 'none';
  document.getElementById('import-deck-section').style.display = 'none';
  document.getElementById('import-submit-btn').style.display = 'none';
  document.getElementById('import-result').style.display = 'none';

  // Open modal now that a file has been chosen
  document.getElementById('import-modal-overlay').style.display = 'block';
  document.getElementById('import-modal').style.display = 'flex';

  // Auto-preview as soon as a file is selected
  previewImport();
}

async function previewImport(yamlContent) {
  const fileInput = document.getElementById('import-file');
  if (!yamlContent && !fileInput.files.length) { showError('Please select a YAML file.'); return; }

  const btn = document.getElementById('import-preview-btn');
  btn.disabled = true;
  btn.textContent = 'Loading…';

  const form = new FormData();
  if (yamlContent) {
    form.append('file', new File([yamlContent], 'edited.yaml', { type: 'application/x-yaml' }));
  } else {
    form.append('file', fileInput.files[0]);
  }

  try {
    const res = await fetch('/api/import/preview', { method: 'POST', body: form });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
    const data = await res.json();

    if (data.error) {
      showError('YAML parse error: ' + data.error);
      btn.disabled = false;
      btn.textContent = 'Preview';
      return;
    }

    // Summary line
    const s = data.summary;
    const summaryEl = document.getElementById('import-summary');
    const parts = [];
    if (s.ok)           parts.push(`<span style="color:${STATUS_COLOR.ok}">${s.ok} ready</span>`);
    if (s.duplicate)    parts.push(`<span style="color:${STATUS_COLOR.duplicate}">${s.duplicate} duplicate</span>`);
    if (s.invalid)      parts.push(`<span style="color:${STATUS_COLOR.invalid}">${s.invalid} invalid</span>`);
    if (s.unknown_type) parts.push(`${s.unknown_type} unknown type`);
    summaryEl.innerHTML = parts.join(' · ') || 'No importable entries found.';

    // Initialize card configs with defaults
    _previewEntries = data.entries;
    const prevConfigs = { ..._cardConfigs };  // preserve any existing user changes
    _cardConfigs = {};
    data.entries.forEach(e => {
      if (prevConfigs[e.simplified]) {
        const prev = prevConfigs[e.simplified];
        // If status changed from invalid → ok/duplicate, reset include to true
        const wasInvalid = prev.include === false && e.status !== 'invalid';
        _cardConfigs[e.simplified] = wasInvalid
          ? { ...prev, include: true }
          : prev;
      } else {
        _cardConfigs[e.simplified] = {
          include: e.status !== 'invalid',
          deck_path: null,
          suspended: { ...IMPORT_DEFAULT_SUSPENDED },
        };
      }
    });

    _importRenderTable();
    document.getElementById('import-preview').style.display = 'block';

    // Conflict resolution
    if (data.conflicts && data.conflicts.length > 0) {
      importResolutions = {};
      _conflictData = data.conflicts;
      _conflictSelections = {};
      _conflictEdits = {};
      data.conflicts.forEach(c => { _conflictSelections[c.simplified] = 'keep'; });
      document.getElementById('import-conflicts-count').textContent = data.conflicts.length;
      document.getElementById('import-conflicts-section').style.display = 'block';
    } else {
      _conflictData = [];
      document.getElementById('import-conflicts-section').style.display = 'none';
    }

    if (s.ok > 0 || s.duplicate > 0) {
      document.getElementById('import-deck-section').style.display = 'block';
      document.getElementById('import-submit-btn').style.display = '';
    }
    if (!yamlContent) btn.style.display = 'none';
    else { btn.disabled = false; btn.textContent = 'Preview'; }
  } catch (e) {
    showError('Preview failed: ' + e.message);
    btn.disabled = false;
    btn.textContent = 'Preview';
  }
}

async function doImport() {
  const fileInput = document.getElementById('import-file');
  const deckPath  = document.getElementById('import-deck-path').value.trim();
  const resultEl  = document.getElementById('import-result');

  if (!fileInput.files.length) { showError('Please select a YAML file.'); return; }
  if (!deckPath) { showError('Please enter a target deck.'); return; }

  const btn = document.getElementById('import-submit-btn');
  btn.disabled = true;
  btn.textContent = 'Importing…';

  // Build card_configs: only include entries explicitly configured
  const cardConfigsMap = {};
  _previewEntries.forEach(e => {
    const cfg = _cardConfigs[e.simplified];
    if (cfg) cardConfigsMap[e.simplified] = cfg;
  });

  const form = new FormData();
  form.append('file', fileInput.files[0]);
  form.append('deck_path', deckPath);
  if (Object.keys(importResolutions).length > 0) {
    form.append('resolutions', JSON.stringify(importResolutions));
  }
  form.append('card_configs', JSON.stringify(cardConfigsMap));
  // Send custom field edits for "custom" resolutions
  const customFieldsMap = {};
  _conflictData.forEach(c => {
    if (importResolutions[c.simplified] === 'custom') {
      const sel = _conflictSelections[c.simplified] || 'keep';
      const base = sel === 'keep' ? c.existing : c.incoming;
      const edits = _conflictEdits[c.simplified] || {};
      customFieldsMap[c.simplified] = { ...base, ...edits };
    }
  });
  if (Object.keys(customFieldsMap).length > 0) {
    form.append('custom_fields', JSON.stringify(customFieldsMap));
  }

  try {
    const res = await fetch('/api/import/upload', { method: 'POST', body: form });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || res.statusText);
    }
    const data = await res.json();
    closeImportModal();
    loadDecks();

    const parts = [`✓ Imported ${data.imported}`];
    if (data.skipped_duplicate) parts.push(`${data.skipped_duplicate} duplicates skipped`);
    if (data.skipped_invalid)   parts.push(`${data.skipped_invalid} invalid skipped`);
    const banner = document.getElementById('error-banner');
    banner.textContent = parts.join(' · ');
    banner.style.background = data.skipped_invalid ? '#e67e22' : '#27ae60';
    banner.style.color = '#fff';
    banner.style.display = 'block';
    setTimeout(() => { banner.style.display = 'none'; banner.style.background = ''; banner.style.color = ''; }, 4000);
  } catch (e) {
    resultEl.style.display = 'block';
    resultEl.innerHTML = `<span style="color:#e74c3c">Error: ${e.message}</span>`;
    btn.disabled = false;
    btn.textContent = 'Import';
  }
}

const _CF_FIELD_LABELS = { pinyin: 'Pinyin', definition: 'Definition', traditional: 'Traditional' };

function openConflictModal() {
  _renderConflictModal();
  document.getElementById('conflict-modal-overlay').style.display = 'block';
  document.getElementById('conflict-modal').style.display = 'flex';
}

function closeConflictModal() {
  document.getElementById('conflict-modal-overlay').style.display = 'none';
  document.getElementById('conflict-modal').style.display = 'none';
}

function _renderConflictModal() {
  const body = document.getElementById('conflict-modal-body');
  body.innerHTML = _conflictData.map((c, idx) => {
    const sel = _conflictSelections[c.simplified] || 'keep';
    const edits = _conflictEdits[c.simplified] || {};

    const renderField = (f) => {
      const existingVal = c.existing[f] || '';
      const incomingVal = c.incoming[f] || '';
      const isEdited = edits[f] !== undefined;
      const isDiff = existingVal !== incomingVal;
      const currentVal = isEdited ? edits[f] : (sel === 'keep' ? existingVal : incomingVal);
      return `
        <div class="cf-field">
          <div class="cf-field-label">
            ${_CF_FIELD_LABELS[f]}
            <span id="cf-badge-${idx}-${f}" class="cf-edited-badge" style="${isEdited ? '' : 'display:none'}">edited</span>
            ${isDiff && !isEdited ? `<span class="cf-diff-badge">differs</span>` : ''}
          </div>
          <div class="cf-field-compare">
            <span class="cf-compare-val ${sel === 'keep' && !isEdited ? 'cf-active' : ''}"
              title="Existing: ${_ea(existingVal)}"
              onclick="conflictLoadField(${idx},'${f}','existing')">${existingVal || '—'}</span>
            <span style="color:var(--clr-muted,#888)">↔</span>
            <span class="cf-compare-val ${sel === 'update' && !isEdited ? 'cf-active' : ''}"
              title="Incoming: ${_ea(incomingVal)}"
              onclick="conflictLoadField(${idx},'${f}','incoming')">${incomingVal || '—'}</span>
          </div>
          <input class="edit-input cf-field-input" value="${_ea(currentVal)}"
            oninput="conflictEditField(${idx},'${f}',this.value)">
        </div>`;
    };

    return `
      <div class="cf-card">
        <div class="cf-card-header">
          <span class="cf-word">${c.simplified}</span>
          <div class="cf-version-btns">
            <button class="cf-version-btn ${sel === 'keep' ? 'cf-version-selected' : ''}"
              onclick="conflictSelectVersion(${idx},'keep')">✓ Existing</button>
            <button class="cf-version-btn ${sel === 'update' ? 'cf-version-selected' : ''}"
              onclick="conflictSelectVersion(${idx},'update')">✓ Incoming</button>
          </div>
        </div>
        ${Object.keys(_CF_FIELD_LABELS).map(renderField).join('')}
      </div>`;
  }).join('');
}

function conflictSelectVersion(idx, version) {
  const c = _conflictData[idx];
  if (!c) return;
  _conflictSelections[c.simplified] = version;
  delete _conflictEdits[c.simplified];
  _renderConflictModal();
}

function conflictLoadField(idx, field, source) {
  const c = _conflictData[idx];
  if (!c) return;
  const val = source === 'existing' ? (c.existing[field] || '') : (c.incoming[field] || '');
  _conflictEdits[c.simplified] = { ...(_conflictEdits[c.simplified] || {}), [field]: val };
  _renderConflictModal();
}

function conflictEditField(idx, field, value) {
  const c = _conflictData[idx];
  if (!c) return;
  const edits = { ...(_conflictEdits[c.simplified] || {}) };
  const sel = _conflictSelections[c.simplified] || 'keep';
  const baseVal = sel === 'keep' ? (c.existing[field] || '') : (c.incoming[field] || '');
  if (value !== baseVal) {
    edits[field] = value;
  } else {
    delete edits[field];
  }
  _conflictEdits[c.simplified] = Object.keys(edits).length ? edits : undefined;
  if (!_conflictEdits[c.simplified]) delete _conflictEdits[c.simplified];
  // Update just the badge without re-rendering (preserve focus)
  const badgeEl = document.getElementById(`cf-badge-${idx}-${field}`);
  if (badgeEl) {
    badgeEl.style.display = edits[field] !== undefined ? '' : 'none';
  }
}

function conflictAcceptAll(version) {
  _conflictData.forEach(c => {
    _conflictSelections[c.simplified] = version;
    delete _conflictEdits[c.simplified];
  });
  _renderConflictModal();
}

function conflictDone() {
  importResolutions = {};
  _conflictData.forEach(c => {
    const edits = _conflictEdits[c.simplified];
    if (edits && Object.keys(edits).length > 0) {
      importResolutions[c.simplified] = 'custom';
    } else {
      importResolutions[c.simplified] = _conflictSelections[c.simplified] || 'keep';
    }
  });
  closeConflictModal();
}

// ── Trash ────────────────────────────────────────────────────────────────────
let _trashData = null;
let _trashExpandedDecks = new Set();

function _trashDaysLeft(deleted_at) {
  const purgeDate = new Date(deleted_at + 'Z');
  purgeDate.setDate(purgeDate.getDate() + 30);
  return Math.ceil((purgeDate - Date.now()) / 86400000);
}

function _renderTrash() {
  const { decks, cards } = _trashData;
  const body = document.getElementById('trash-modal-body');
  const isEmpty = !decks.length && !cards.length;
  document.getElementById('trash-empty-all-btn').style.display = isEmpty ? 'none' : '';
  let html = '';

  if (decks.length) {
    html += '<div class="trash-section-header">Decks</div>';
    for (const d of decks) {
      const expanded = _trashExpandedDecks.has(d.id);
      const hasCards = d.cards && d.cards.length > 0;
      const toggleIcon = hasCards
        ? `<button class="trash-toggle" onclick="toggleTrashDeck(${d.id})">${expanded ? '▾' : '▸'}</button>`
        : `<span class="trash-toggle-spacer"></span>`;
      html += `<div class="trash-row">
        ${toggleIcon}
        <div class="trash-row-info">
          <span class="trash-name">${d.name}</span>
          <span class="trash-meta">${hasCards ? d.cards.length + ' card' + (d.cards.length !== 1 ? 's' : '') : 'empty'} · ${_trashDaysLeft(d.deleted_at)}d left</span>
        </div>
        <div class="trash-row-actions">
          <button class="trash-restore-btn" onclick="restoreDeck(${d.id})">Restore</button>
          <button class="trash-purge-btn" onclick="purgeDeck(${d.id})">Delete</button>
        </div>
      </div>`;
      if (expanded && hasCards) {
        html += `<div class="trash-deck-cards">
          <div class="trash-deck-cards-header">
            <span class="trash-deck-cards-count">${d.cards.length} card${d.cards.length !== 1 ? 's' : ''}</span>
            <button class="trash-purge-btn trash-purge-all-cards-btn" onclick="purgeAllCardsFromDeck(${d.id}, ${d.cards.length})">Delete all</button>
          </div>`;
        for (const c of d.cards) {
          html += `<div class="trash-card-row">
            <div class="trash-row-info">
              <span class="trash-name">${c.word_zh}</span>
              <span class="trash-meta">${c.category} · ${c.state}</span>
            </div>
            <button class="trash-purge-btn" onclick="purgeCardFromDeck(${d.id}, ${c.id})">Delete</button>
          </div>`;
        }
        html += '</div>';
      }
    }
  }

  if (cards.length) {
    html += '<div class="trash-section-header">Cards</div>';
    html += cards.map(c => `<div class="trash-row">
      <div class="trash-row-info">
        <span class="trash-name">${c.word_zh}</span>
        <span class="trash-meta">${c.category} · ${c.deck_path} · ${_trashDaysLeft(c.deleted_at)}d left</span>
      </div>
      <div class="trash-row-actions">
        <button class="trash-restore-btn" onclick="restoreCard(${c.id})">Restore</button>
        <button class="trash-purge-btn" onclick="purgeCard(${c.id})">Delete</button>
      </div>
    </div>`).join('');
  }

  body.innerHTML = html || '<div class="trash-empty">Trash is empty</div>';
}

async function _refreshTrash() {
  const body = document.getElementById('trash-modal-body');
  try {
    _trashData = await api('GET', '/api/trash');
    _renderTrash();
  } catch (e) {
    body.innerHTML = `<div class="trash-empty">Error: ${e.message}</div>`;
  }
}

async function openTrash() {
  document.getElementById('trash-modal-overlay').style.display = '';
  document.getElementById('trash-modal').style.display = '';
  document.getElementById('trash-modal-body').innerHTML = '<div class="trash-empty">Loading…</div>';
  await _refreshTrash();
}

function toggleTrashDeck(id) {
  if (_trashExpandedDecks.has(id)) _trashExpandedDecks.delete(id);
  else _trashExpandedDecks.add(id);
  _renderTrash();
}

function closeTrash() {
  document.getElementById('trash-modal-overlay').style.display = 'none';
  document.getElementById('trash-modal').style.display = 'none';
}
async function restoreDeck(id) {
  await api('POST', `/api/trash/${id}/restore`);
  loadDecks();
  await _refreshTrash();
}
async function purgeDeck(id) {
  const ok = await showConfirm('Permanently delete this deck and all its cards?');
  if (!ok) return;
  await api('DELETE', `/api/trash/${id}`);
  _trashExpandedDecks.delete(id);
  await _refreshTrash();
  loadDecks();
}
async function restoreCard(id) {
  await api('POST', `/api/trash/cards/${id}/restore`);
  await _refreshTrash();
}
async function purgeCard(id) {
  const ok = await showConfirm('Permanently delete this card?');
  if (!ok) return;
  await api('DELETE', `/api/trash/cards/${id}`);
  await _refreshTrash();
}
async function purgeCardFromDeck(deckId, cardId) {
  const ok = await showConfirm('Permanently delete this card?');
  if (!ok) return;
  await api('DELETE', `/api/trash/${deckId}/cards/${cardId}`);
  await _refreshTrash();
}
async function purgeAllCardsFromDeck(deckId, count) {
  const ok = await showConfirm(`Permanently delete all ${count} card${count !== 1 ? 's' : ''} in this deck?`);
  if (!ok) return;
  await api('DELETE', `/api/trash/${deckId}/cards`);
  await _refreshTrash();
}
async function emptyTrash() {
  const ok = await showConfirm('Permanently delete everything in trash? This cannot be undone.');
  if (!ok) return;
  await api('DELETE', '/api/trash');
  _trashExpandedDecks.clear();
  await _refreshTrash();
  loadDecks();
}
// ── YAML entry editor ────────────────────────────────────────────────────────

let _yamlEditDeckPath = '';
let _yamlEditEntryIdx = -1; // >=0 means opened from preview table → Save mode

function openYamlEdit(wordZh, rawYaml, deckPath, entryIdx) {
  _yamlEditDeckPath = deckPath || document.getElementById('import-deck-path').value.trim();
  _yamlEditEntryIdx = (entryIdx !== undefined && entryIdx >= 0) ? entryIdx : -1;
  document.getElementById('yaml-edit-title').textContent = wordZh;
  document.getElementById('yaml-edit-textarea').value = rawYaml;
  document.getElementById('yaml-edit-feedback').style.display = 'none';
  document.getElementById('yaml-edit-feedback').innerHTML = '';
  document.getElementById('yaml-edit-check-btn').disabled = false;
  const importBtn = document.getElementById('yaml-edit-import-btn');
  importBtn.disabled = false;
  if (_yamlEditEntryIdx >= 0) {
    importBtn.textContent = 'Save';
    importBtn.onclick = saveYamlEdit;
  } else {
    importBtn.textContent = 'Import';
    importBtn.onclick = importYamlEntry;
  }
  document.getElementById('yaml-edit-overlay').style.display = 'block';
  document.getElementById('yaml-edit-modal').style.display = 'flex';
}

function closeYamlEdit() {
  document.getElementById('yaml-edit-overlay').style.display = 'none';
  document.getElementById('yaml-edit-modal').style.display = 'none';
}

async function saveYamlEdit() {
  if (_yamlEditEntryIdx < 0 || !_previewEntries.length) return;
  const newYaml = document.getElementById('yaml-edit-textarea').value.trim();
  // Update the entry in our in-memory list
  _previewEntries[_yamlEditEntryIdx].raw_yaml = newYaml;
  // Reconstruct the full YAML from all entries that have raw_yaml
  const yamlContent = _previewEntries
    .filter(e => e.raw_yaml)
    .map(e => `- ${e.raw_yaml.replace(/\n/g, '\n  ')}`)
    .join('\n');
  closeYamlEdit();
  await previewImport(yamlContent);
}

async function checkYamlEntry() {
  const yamlText = document.getElementById('yaml-edit-textarea').value.trim();
  const feedbackEl = document.getElementById('yaml-edit-feedback');
  const btn = document.getElementById('yaml-edit-check-btn');
  btn.disabled = true;
  btn.textContent = 'Checking…';

  try {
    const blob = new Blob([`- ${yamlText.replace(/\n/g, '\n  ')}`], { type: 'application/x-yaml' });
    const form = new FormData();
    form.append('file', new File([blob], 'entry.yaml'));
    const res = await fetch('/api/import/preview', { method: 'POST', body: form });
    const data = await res.json();

    feedbackEl.style.display = 'block';
    if (data.error) {
      feedbackEl.innerHTML = `<span style="color:#e74c3c">YAML error: ${data.error}</span>`;
    } else if (!data.entries.length) {
      feedbackEl.innerHTML = `<span style="color:#e74c3c">No entry found — check the YAML structure.</span>`;
    } else {
      const e = data.entries[0];
      const color = STATUS_COLOR[e.status] || '#888';
      feedbackEl.innerHTML = `<span style="color:${color}">${STATUS_ICON[e.status]} ${e.simplified}</span>`
        + (e.reason ? ` <span style="color:#e74c3c;font-size:12px">${e.reason}</span>` : '')
        + (e.status === 'ok' ? ` <span style="color:var(--clr-muted,#888);font-size:12px">— ready to import</span>` : '');
    }
  } catch (err) {
    feedbackEl.style.display = 'block';
    feedbackEl.innerHTML = `<span style="color:#e74c3c">Check failed: ${err.message}</span>`;
  } finally {
    btn.disabled = false;
    btn.textContent = 'Check';
  }
}

async function importYamlEntry() {
  const yamlText = document.getElementById('yaml-edit-textarea').value.trim();
  const feedbackEl = document.getElementById('yaml-edit-feedback');
  const btn = document.getElementById('yaml-edit-import-btn');

  if (!_yamlEditDeckPath) {
    feedbackEl.style.display = 'block';
    feedbackEl.innerHTML = `<span style="color:#e74c3c">No target deck — go back and set one.</span>`;
    return;
  }

  btn.disabled = true;
  btn.textContent = 'Importing…';

  try {
    // Wrap the entry dict as a YAML list item
    const blob = new Blob([`- ${yamlText.replace(/\n/g, '\n  ')}`], { type: 'application/x-yaml' });
    const form = new FormData();
    form.append('file', new File([blob], 'entry.yaml'));
    form.append('deck_path', _yamlEditDeckPath);
    const res = await fetch('/api/import/upload', { method: 'POST', body: form });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
    const data = await res.json();

    feedbackEl.style.display = 'block';
    if (data.imported > 0) {
      feedbackEl.innerHTML = `<span style="color:${STATUS_COLOR.ok}">Imported successfully.</span>`;
      btn.textContent = 'Done';
      btn.onclick = closeYamlEdit;
      btn.disabled = false;
      loadDecks();
    } else if (data.skipped_duplicate > 0) {
      feedbackEl.innerHTML = `<span style="color:#e67e22">Already in deck — nothing imported.</span>`;
      btn.disabled = false;
      btn.textContent = 'Import';
    } else {
      const reason = data.skipped_entries?.[0]?.reason || 'unknown reason';
      feedbackEl.innerHTML = `<span style="color:#e74c3c">Still invalid: ${reason}</span>`;
      btn.disabled = false;
      btn.textContent = 'Import';
    }
  } catch (err) {
    feedbackEl.style.display = 'block';
    feedbackEl.innerHTML = `<span style="color:#e74c3c">Import failed: ${err.message}</span>`;
    btn.disabled = false;
    btn.textContent = 'Import';
  }
}

// ── Server restart ───────────────────────────────────────────────────────────
async function restartServer() {
  const btn = document.getElementById('restart-btn');
  btn.classList.add('spinning');
  btn.disabled = true;
  try {
    await fetch('/api/restart', { method: 'POST' });
  } catch (_) { /* server going down — expected */ }

  // Poll until the server is back up, then reload
  const poll = async () => {
    try {
      const r = await fetch('/api/decks');
      if (r.ok) { location.reload(); return; }
    } catch (_) {}
    setTimeout(poll, 400);
  };
  setTimeout(poll, 600);
}

document.addEventListener('keydown', e => {
  const tag = document.activeElement?.tagName;
  const inInput = tag === 'INPUT' || tag === 'TEXTAREA';

  if (e.key === 'R' && e.shiftKey && !e.ctrlKey && !e.metaKey) {
    if (!inInput) { e.preventDefault(); restartServer(); }
    return;
  }

  if (inInput || e.ctrlKey || e.metaKey || e.altKey) return;

  // Only handle review shortcuts when the review view is active
  const reviewView = document.getElementById('view-review');
  if (!reviewView || reviewView.style.display === 'none') return;

  const backVisible = document.getElementById('side-back')?.style.display === 'flex';

  if (e.key === 'r') {
    e.preventDefault();
    playSentence();
  } else if (e.key === ' ') {
    e.preventDefault();
    if (!backVisible) revealAnswer();
  } else if (['1','2','3','4'].includes(e.key) && backVisible) {
    e.preventDefault();
    const btns = document.querySelectorAll('.r-btn');
    if (btns.length && !btns[0].disabled) rate(Number(e.key));
  } else if (e.key === 'z') {
    const undoBtn = document.getElementById('undo-btn');
    if (undoBtn && !undoBtn.disabled) {
      e.preventDefault();
      undoReview();
    }
  }
});

// ── Deck picker ───────────────────────────────────────────────────────────────

let _deckPickerActiveIdx = -1;

function _deckPathHtml(path) {
  return path.split('::').map(s => `<span>${s}</span>`).join('<span class="deck-picker-sep"> :: </span>');
}

function _renderDeckDropdown(suggestions, query) {
  const dd = document.getElementById('deck-picker-dropdown');
  if (!dd) return;
  const isNew = !!query && !suggestions.some(s => s.toLowerCase() === query.toLowerCase());
  document.getElementById('deck-picker-new-badge').style.display = (isNew && query) ? '' : 'none';
  _deckPickerActiveIdx = -1;

  let html = suggestions.map((s, i) =>
    `<div class="deck-picker-option" data-idx="${i}" onclick="deckPickerSelect('${s.replace(/'/g, "\\'")}')">${_deckPathHtml(s)}</div>`
  ).join('');

  if (!html && !isNew) html = '<div class="deck-picker-empty">No existing decks</div>';

  if (isNew && query) {
    html += `<div class="deck-picker-create" onclick="deckPickerSelect('${query.replace(/'/g, "\\'")}')">+ Create ${_deckPathHtml(query)}</div>`;
  }

  dd.innerHTML = html;
  const show = !!(suggestions.length || isNew || !query);
  dd.style.display = show ? 'block' : 'none';
  if (show) _positionDeckDropdown();
}

function _positionDeckDropdown() {
  const input = document.getElementById('import-deck-path');
  const dd = document.getElementById('deck-picker-dropdown');
  if (!input || !dd) return;
  const r = input.getBoundingClientRect();
  const ddH = Math.min(220, dd.scrollHeight);
  const spaceAbove = r.top;
  const spaceBelow = window.innerHeight - r.bottom;
  dd.style.width = r.width + 'px';
  dd.style.left = r.left + 'px';
  if (spaceAbove >= ddH + 8 || spaceAbove > spaceBelow) {
    dd.style.bottom = (window.innerHeight - r.top + 4) + 'px';
    dd.style.top = 'auto';
  } else {
    dd.style.top = (r.bottom + 4) + 'px';
    dd.style.bottom = 'auto';
  }
}

function deckPickerOpen() {
  const q = document.getElementById('import-deck-path').value.trim();
  const filtered = (window._deckSuggestions || []).filter(s => !q || s.toLowerCase().includes(q.toLowerCase()));
  _renderDeckDropdown(filtered, q);
}

function deckPickerFilter() {
  const q = document.getElementById('import-deck-path').value.trim();
  const filtered = (window._deckSuggestions || []).filter(s => !q || s.toLowerCase().includes(q.toLowerCase()));
  _renderDeckDropdown(filtered, q);
}

function deckPickerSelect(path) {
  document.getElementById('import-deck-path').value = path;
  document.getElementById('deck-picker-dropdown').style.display = 'none';
  const isNew = !(window._deckSuggestions || []).some(s => s.toLowerCase() === path.toLowerCase());
  document.getElementById('deck-picker-new-badge').style.display = (isNew && path) ? '' : 'none';
}

function deckPickerKey(e) {
  const dd = document.getElementById('deck-picker-dropdown');
  if (!dd) return;
  if (dd.style.display === 'none') {
    if (e.key === 'ArrowDown') { e.preventDefault(); deckPickerOpen(); }
    return;
  }
  const opts = dd.querySelectorAll('.deck-picker-option, .deck-picker-create');
  if (e.key === 'Escape') { dd.style.display = 'none'; return; }
  if (e.key === 'ArrowDown') {
    e.preventDefault();
    _deckPickerActiveIdx = Math.min(_deckPickerActiveIdx + 1, opts.length - 1);
  } else if (e.key === 'ArrowUp') {
    e.preventDefault();
    _deckPickerActiveIdx = Math.max(_deckPickerActiveIdx - 1, -1);
  } else if (e.key === 'Enter' && _deckPickerActiveIdx >= 0) {
    e.preventDefault();
    opts[_deckPickerActiveIdx].click();
    return;
  } else { return; }
  opts.forEach((o, i) => o.classList.toggle('active', i === _deckPickerActiveIdx));
  if (_deckPickerActiveIdx >= 0) opts[_deckPickerActiveIdx].scrollIntoView({ block: 'nearest' });
}

document.addEventListener('click', e => {
  const picker = document.getElementById('deck-picker');
  const dd = document.getElementById('deck-picker-dropdown');
  if (picker && dd && !picker.contains(e.target) && !dd.contains(e.target)) {
    dd.style.display = 'none';
  }
});

// ── Boot ─────────────────────────────────────────────────────────────────────
loadDecks();

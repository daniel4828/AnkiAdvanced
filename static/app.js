// ── State ──────────────────────────────────────────────────────────────────
let deckId      = null;
let rootDeckId  = null;   // set when studying all categories (mixed mode)
let deckName    = '';
let category    = '';
let card        = null;   // current card dict from API
let story       = null;   // story dict with sentences[]
let sentence    = null;   // current sentence from story (may be null)
let wordDetails = null;   // full word data: examples + characters
let userInput   = '';     // creating category: what the user typed
let browseAll    = [];   // all cards from API for client-side filtering
let optDeckId    = null; // deck whose options modal is open
const collapsed  = new Set();  // parent deck IDs that are collapsed

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
  ['loading', 'decks', 'review', 'done', 'browse', 'stats'].forEach(v => {
    document.getElementById(`view-${v}`).style.display = 'none';
  });
  document.getElementById(`view-${name}`).style.display = 'block';
  document.getElementById('back-btn').style.display = name === 'decks' ? 'none' : 'block';
  document.getElementById('header-title').textContent =
    name === 'review' ? `${deckName} · ${cap(category)}` :
    name === 'browse' ? 'Browse' :
    name === 'stats'  ? 'Stats'  : 'AnkiAdvanced';
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
    const leaf = catLeaves[cat];
    if (leaf) {
      // Direct leaf — use leaf's deck_id (single-deck session)
      const c = leaf.counts || { new: 0, learning: 0, review: 0 };
      return `<span class="cat-pill-wrap"><button class="cat-pill" onclick="event.stopPropagation();startReview(${leaf.id},'${cat}','${safeName}')"><span class="cat-pill-label">${LABELS[cat]}</span><span class="cat-pill-counts">${countHtml(c)}</span></button><button class="cat-pill-gear" onclick="event.stopPropagation();openOptions(${leaf.id})" title="Options">⚙</button></span>`;
    }
    // Structural parent — aggregate and use this deck's id (multi-deck session via backend)
    const c = aggregateCounts(deck, cat);
    const hasCards = getDeepCategoryLeaves(deck).some(l => l.category === cat);
    if (!hasCards) return `<button class="cat-pill" disabled><span class="cat-pill-label">${LABELS[cat]}</span><span class="cat-pill-counts"><span class="n-zero">—</span></span></button>`;
    return `<span class="cat-pill-wrap"><button class="cat-pill" onclick="event.stopPropagation();startReview(${deck.id},'${cat}','${safeName}')"><span class="cat-pill-label">${LABELS[cat]}</span><span class="cat-pill-counts">${countHtml(c)}</span></button><button class="cat-pill-gear" onclick="event.stopPropagation();openOptions(${deck.id})" title="Options">⚙</button></span>`;
  }).join('');
}

function renderDecks(decks) {
  const navRow = `
    <div class="nav-row">
      <button class="nav-btn" onclick="openBrowse()">Browse Cards</button>
      <button class="nav-btn" onclick="openStats()">Stats</button>
    </div>`;
  const rows = renderDeckRows(decks, 0);
  document.getElementById('view-decks').innerHTML = navRow + `<div class="tree-card">${rows}</div>`;
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

    const row = `
      <div class="tree-row tree-parent" style="padding-left:${16 + indent}px">
        <span class="tree-toggle" onclick="toggleDeck(${deck.id})">${toggleIcon}</span>
        <span class="tree-name" onclick="startReviewMixed(${deck.id},'${safeName}')" style="cursor:pointer">${deck.name}</span>
        ${deckCounts}
        <button class="gear-btn" onclick="openOptions(${deck.id})"
                title="Deck options">⚙</button>
        <div class="cat-pills-row">${buildCategoryButtons(deck)}</div>
      </div>`;

    const childRows = hasStructChildren && !isCollapsed
      ? renderDeckRows(structChildren, depth + 1)
      : '';

    return row + childRows;
  }).join('');
}

function toggleDeck(deckId) {
  if (collapsed.has(deckId)) {
    collapsed.delete(deckId);
  } else {
    collapsed.add(deckId);
  }
  loadDecks();
}

// ── Browse ───────────────────────────────────────────────────────────────────
async function openBrowse() {
  setLoading('Loading cards…');
  try {
    browseAll = await api('GET', '/api/browse');
    showView('browse');
    applyFilters();
  } catch (e) {
    showError('Browse failed: ' + e.message);
    showView('decks');
  }
}

function applyFilters() {
  const q     = document.getElementById('f-search').value.toLowerCase();
  const state = document.getElementById('f-state').value;
  const cat   = document.getElementById('f-cat').value;
  const filtered = browseAll.filter(c => {
    if (state && c.state !== state) return false;
    if (cat   && c.category !== cat) return false;
    if (q && !c.word_zh?.toLowerCase().includes(q)
           && !c.definition?.toLowerCase().includes(q)
           && !c.pinyin?.toLowerCase().includes(q)) return false;
    return true;
  });
  renderBrowse(filtered);
}

function renderBrowse(cards) {
  const list = document.getElementById('browse-list');
  if (!cards.length) {
    list.innerHTML = '<div style="text-align:center;color:var(--muted);padding:40px 0">No cards found</div>';
    return;
  }
  list.innerHTML = cards.map(c => {
    const def = (c.definition || '').slice(0, 48) + ((c.definition || '').length > 48 ? '…' : '');
    const due = c.due ? c.due.slice(0, 10) : '';
    const intv = c.interval > 0 ? `${c.interval}d` : '';
    return `
      <div class="browse-item">
        <div class="browse-top">
          <div class="browse-word">${c.word_zh}</div>
          <div class="browse-badges">
            <span class="badge badge-${c.category}">${c.category.slice(0,1).toUpperCase()}</span>
            <span class="badge badge-${c.state}">${c.state}</span>
          </div>
        </div>
        <div class="browse-def">${def}</div>
        <div class="browse-meta">${c.pinyin || ''}${due ? ' · due ' + due : ''}${intv ? ' · ' + intv : ''}</div>
      </div>`;
  }).join('');
}

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
  document.getElementById('opt-insertion-order').value = preset.insertion_order || 'sequential';
  document.getElementById('opt-relearn-steps').value   = preset.relearning_steps;
  document.getElementById('opt-leech').value           = preset.leech_threshold;
  document.getElementById('opt-bury-siblings').checked  = !!preset.bury_siblings;
  document.getElementById('opt-randomize-story').checked = !!preset.randomize_story_order;
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
    insertion_order:     document.getElementById('opt-insertion-order').value,
    bury_siblings:          document.getElementById('opt-bury-siblings').checked ? 1 : 0,
    randomize_story_order:  document.getElementById('opt-randomize-story').checked ? 1 : 0,
  };
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
async function startReview(id, cat, name) {
  deckId   = id;
  category = cat;
  deckName = name;

  // Show setup modal before generating
  try {
    const { count } = await api('GET', `/api/story/${deckId}/${category}/count`);
    await openStorySetup(count);
  } catch (e) {
    showError('Failed to start session: ' + e.message);
    showView('decks');
    return;
  }
}

async function _doStartReview(topic, maxHsk) {
  setLoading('Generating your story…');
  try {
    const storyUrl = `/api/story/${deckId}/${category}` + _storyParams(topic, maxHsk);
    const [todayData, storyData] = await Promise.all([
      api('GET', `/api/today/${deckId}/${category}`),
      api('GET', storyUrl),
    ]);

    story = storyData;

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

function _storyParams(topic, maxHsk) {
  const p = new URLSearchParams();
  if (topic)        p.set('topic', topic);
  if (maxHsk !== 2) p.set('max_hsk', maxHsk);
  const s = p.toString();
  return s ? '?' + s : '';
}

// ── Start mixed (all-category) review session ────────────────────────────────
async function startReviewMixed(id, name) {
  rootDeckId = id;
  deckId     = id;
  deckName   = name;
  story      = null;  // no single story in mixed mode
  setLoading('Loading cards…');
  try {
    const todayData = await api('GET', `/api/today-mixed/${id}`);
    if (!todayData.card) {
      rootDeckId = null;
      showView('done');
      return;
    }
    category = todayData.card.category;
    showView('review');
    loadCard(todayData.card, todayData.counts);
  } catch (e) {
    showError('Failed to start session: ' + e.message);
    rootDeckId = null;
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

  // Find sentence for this card's word in the story
  sentence = story?.sentences?.find(s => s.word_id === card.word_id) || null;

  // Update sentence position counter
  const counter = document.getElementById('sentence-counter');
  if (sentence && story?.sentences?.length) {
    counter.textContent = `Sentence ${sentence.position + 1} / ${story.sentences.length}`;
    counter.style.display = 'block';
  } else {
    counter.style.display = 'none';
  }

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
        renderVocabDetail();
        renderNotesSection();
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
  const isListening = category === 'listening';
  const isCreating  = category === 'creating';

  document.getElementById('side-front').style.display = 'flex';
  document.getElementById('side-front').style.flexDirection = 'column';
  document.getElementById('side-front').style.gap = '16px';
  document.getElementById('side-back').style.display = 'none';

  // Listening elements
  document.getElementById('front-listen-icon').style.display = isListening ? 'flex' : 'none';
  document.getElementById('front-play-btn').style.display    = isListening ? 'flex' : 'none';

  // Reading: Chinese sentence
  const sentFront = document.getElementById('sentence-front');
  sentFront.style.display = (!isListening && !isCreating) ? 'flex' : 'none';
  if (!isListening && !isCreating) {
    sentFront.innerHTML = renderSentence();
  }

  // Creating: English sentence + input
  document.getElementById('sentence-en-front').style.display   = isCreating ? 'flex' : 'none';
  document.getElementById('creating-input-wrap').style.display = isCreating ? 'flex' : 'none';
  if (isCreating) {
    document.getElementById('sentence-en-front').textContent = sentence?.sentence_en || '';
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

  if (isCreating) {
    // Show answer comparison block; hide normal sentence row
    document.getElementById('creating-answer-section').style.display = 'flex';
    document.getElementById('sentence-row-back').style.display = 'none';
    const { html: userHtml, pct, bar } = diffAnswer(userInput, sentence?.sentence_zh, card.word_zh);
    document.getElementById('user-answer-text').innerHTML = userHtml;
    const matchBar = document.getElementById('answer-match-bar');
    if (sentence && userInput) {
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

  document.getElementById('sentence-en').textContent = sentence?.sentence_en || '';
  document.getElementById('story-btn').style.display = story?.sentences?.length > 1 ? 'block' : 'none';
  document.getElementById('word-zh').textContent  = card.word_zh;
  document.getElementById('word-pin').textContent = card.pinyin || '';
  document.getElementById('word-def').textContent = card.definition || '';

  const posEl = document.getElementById('word-pos');
  posEl.textContent   = card.pos || '';
  posEl.style.display = card.pos ? 'inline-block' : 'none';

  // Re-enable rating buttons
  document.querySelectorAll('.r-btn').forEach(b => b.disabled = false);

  // Populate character breakdown, examples, and notes from preloaded word details
  renderVocabDetail();
  renderNotesSection();

  // Auto-play audio on reveal for reading and creating
  if (category === 'reading' || category === 'creating') {
    playSentence();
  }
}

// ── Populate vocab detail (chars + examples) ────────────────────────────────
function toggleSection(id) {
  const body = document.getElementById(id);
  const arrow = document.getElementById(id + '-arrow');
  const open = body.style.display !== 'none';
  body.style.display = open ? 'none' : 'block';
  arrow.textContent = open ? '▶' : '▼';
}

function renderVocabDetail() {
  // Characters
  const chars = wordDetails?.characters || [];
  const charSection = document.getElementById('char-section');
  if (chars.length > 0) {
    const rows = chars.map(c => {
      let right = '';
      if (c.pinyin)             right += `<div class="char-row-pin">${c.pinyin}</div>`;
      if (c.meaning_in_context) right += `<div class="char-row-info">${c.meaning_in_context}</div>`;
      if (c.etymology)          right += `<div class="char-row-etym">${c.etymology}</div>`;
      return `<div class="char-row"><div class="char-row-zh">${c.char}</div><div class="char-row-right">${right}</div></div>`;
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
    if (rootDeckId) url += `&root_deck_id=${rootDeckId}`;
    const result = await api('POST', url);
    if (!result.next_card) {
      rootDeckId = null;
      showView('done');
      return;
    }
    if (rootDeckId) category = result.next_card.category;
    loadCard(result.next_card, result.counts);
  } catch (e) {
    showError('Submit failed: ' + e.message);
    document.querySelectorAll('.r-btn').forEach(b => b.disabled = false);
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
  row.innerHTML = chars.map((ch, i) => {
    const py = syllables[i] || '';
    const isTarget = wordStart >= 0 && i >= wordStart && i < wordEnd;
    return `<span class="py-char${isTarget ? ' py-target' : ''}">`+
             `<span class="py-syl">${py}</span>`+
           `</span>`;
  }).join('');
  row.style.display = 'flex';
  btn.classList.add('active');
}

// ── Story setup modal ────────────────────────────────────────────────────────
let _setupResolve = null;
let _setupIsRegen = false;

function openStorySetup(sentenceCount) {
  _setupIsRegen = !!card; // card exists → we're regenerating, not starting fresh
  document.getElementById('setup-count-label').textContent =
    `This story will have ${sentenceCount} sentence${sentenceCount !== 1 ? 's' : ''}.`;
  document.getElementById('setup-topic').value = '';
  document.getElementById('setup-hsk-slider').value = 2;
  updateHskLabel();
  document.getElementById('setup-modal-overlay').style.display = 'block';
  document.getElementById('setup-modal').style.display        = 'flex';
  document.getElementById('setup-topic').focus();
  return new Promise((resolve, reject) => { _setupResolve = resolve; });
}

function updateHskLabel() {
  const v = document.getElementById('setup-hsk-slider').value;
  document.getElementById('setup-hsk-badge').textContent = `HSK ${v}`;
}

function confirmStorySetup() {
  const topic  = document.getElementById('setup-topic').value.trim() || null;
  const maxHsk = parseInt(document.getElementById('setup-hsk-slider').value, 10);
  _closeSetupModal();
  if (_setupIsRegen) {
    _doRegenerateStory(topic, maxHsk);
  } else {
    _doStartReview(topic, maxHsk);
  }
}

function cancelStorySetup() {
  _closeSetupModal();
  if (!_setupIsRegen) showView('decks');
}

function _closeSetupModal() {
  document.getElementById('setup-modal-overlay').style.display = 'none';
  document.getElementById('setup-modal').style.display        = 'none';
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
function openEditCard() {
  document.getElementById('edit-word-zh').value       = card.word_zh || '';
  document.getElementById('edit-pinyin').value        = card.pinyin || '';
  document.getElementById('edit-definition').value    = card.definition || '';
  document.getElementById('edit-pos').value           = card.pos || '';
  document.getElementById('edit-traditional').value   = card.traditional || '';
  document.getElementById('edit-definition-zh').value = card.definition_zh || '';
  document.getElementById('edit-notes').value         = card.notes || '';
  document.getElementById('edit-modal-overlay').style.display = 'block';
  document.getElementById('edit-modal').style.display         = 'flex';
}

function closeEditCard() {
  document.getElementById('edit-modal-overlay').style.display = 'none';
  document.getElementById('edit-modal').style.display         = 'none';
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
    const updated = await api('PUT', `/api/word/${card.word_id}`, body);
    Object.assign(card, {
      word_zh: updated.word_zh, pinyin: updated.pinyin,
      definition: updated.definition, pos: updated.pos,
      traditional: updated.traditional, definition_zh: updated.definition_zh,
      notes: updated.notes,
    });
    document.getElementById('word-zh').textContent  = updated.word_zh || '';
    document.getElementById('word-pin').textContent = updated.pinyin || '';
    document.getElementById('word-def').textContent = updated.definition || '';
    const posEl = document.getElementById('word-pos');
    posEl.textContent   = updated.pos || '';
    posEl.style.display = updated.pos ? 'inline-block' : 'none';
    renderNotesSection();
    closeEditCard();
  } catch (e) {
    showError('Save failed: ' + e.message);
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
  // Show setup modal before regenerating
  const count = story?.sentences?.length ?? 0;
  try {
    await openStorySetup(count);
  } catch (_) {
    showView('review');
  }
}

async function _doRegenerateStory(topic, maxHsk) {
  setLoading('Regenerating story…');
  try {
    story = await api('POST', `/api/story/${deckId}/${category}/regenerate` + _storyParams(topic, maxHsk));
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
  card = null; story = null; sentence = null; wordDetails = null; userInput = '';
  rootDeckId = null;
  browseAll = [];
  loadDecks();
}

// ── Boot ─────────────────────────────────────────────────────────────────────
loadDecks();

// ── Markdown renderer (notes field) ─────────────────────────────────────────
function renderMarkdown(text) {
  if (!text) return '';
  // Escape HTML first
  let html = text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
  // Bold: **text**
  html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  // Italic: *text* (single asterisk, not matched by bold)
  html = html.replace(/(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)/g, '<em>$1</em>');
  // Split into lines for block-level processing
  const lines = html.split('\n');
  const out = [];
  let inList = false;
  for (const line of lines) {
    const li = line.match(/^[-*]\s+(.*)/);
    if (li) {
      if (!inList) { out.push('<ul>'); inList = true; }
      out.push(`<li>${li[1]}</li>`);
    } else {
      if (inList) { out.push('</ul>'); inList = false; }
      if (line.trim() === '') {
        out.push('<br>');
      } else {
        out.push(`<p>${line}</p>`);
      }
    }
  }
  if (inList) out.push('</ul>');
  return out.join('');
}

// ── State ──────────────────────────────────────────────────────────────────
let deckId      = null;
let rootDeckId      = null;   // set when studying all categories (mixed mode)
let unfinishedMode  = false;  // set when studying the "Unfinished Cards" virtual deck
let quickMode       = false;  // set when reviewing without AI story generation
let deckName    = '';
let category    = '';
let card        = null;   // current card dict from API
let story       = null;   // story dict with sentences[]
let sentence    = null;   // current sentence from story (may be null)
let wordDetails = null;   // full word data: examples + characters
let _currentWordId = null; // word ID open in word-detail view
let _prevView = null;      // view we came from before opening word-detail
let _sessionReviewedCount = 0; // cards rated this session (for clap animation)
let userInput   = '';     // creating category: what the user typed
let clozeExtraWord = ''; // extra word blanked in cloze front (revealed on back)
let wordBankTokens = [];  // [{char, num}] shuffled non-target tokens
let wordBankOrder  = [];  // [{type:'char'|'target', char?, word?, num?}] original order
let browseWords  = [];   // all words from /api/browse-words
let browseAll    = [];   // kept for legacy (unused by new browse)
let _browseSort  = 'pinyin-asc';
let _browseSelected = new Set();  // selected word IDs (multiselect)
let _browseDecks = [];            // flat deck list for move dropdown
let _browseDeckTree = [];         // top-level user decks (children of All) for sidebar tree
let _browseDeckExpanded = new Set(); // deck IDs expanded in sidebar tree
let optDeckId    = null; // deck whose options modal is open
const collapsed  = new Set(JSON.parse(localStorage.getItem('collapsedDecks') || '[]'));  // parent deck IDs that are collapsed
let _retentionData = null;  // cached result from GET /api/retention
let _cachedDecks = null;       // last fetched deck tree (for toggle re-renders)
let _timerInterval = null;
let _timerStart = null;
let _sessionTotalMs = 0;
let _sessionRatedCount = 0;

// ── Card schedule calendar ───────────────────────────────────────────────────
let _calData     = null;   // {history, future} from API
let _calYear     = null;
let _calMonth    = null;   // 0-based
let _calCategory = null;   // current card's category — shown on today even if not in dues

const _RATING_CLASS = { 1: 'again', 2: 'hard', 3: 'good', 4: 'easy' };
const _CAT_CLASS    = { listening: 'listening', reading: 'reading', creating: 'creating' };

function _calKey(dateStr) { return dateStr; }  // "YYYY-MM-DD"

const _CAT_LETTER = { listening: '听', reading: '读', creating: '创' };

function _buildCalDayMap() {
  // Deduplicate: per (date, category) keep only the last review
  const histByKey = {};
  for (const h of (_calData?.history || [])) {
    histByKey[`${h.date}|${h.category}`] = h;
  }
  const dueByKey = {};
  for (const f of (_calData?.future || [])) {
    dueByKey[`${f.due}|${f.category}`] = f;
  }

  const map = {};
  for (const h of Object.values(histByKey)) {
    if (!map[h.date]) map[h.date] = { ratings: [], dues: [] };
    map[h.date].ratings.push({ rating: h.rating, category: h.category });
  }
  for (const f of Object.values(dueByKey)) {
    if (!map[f.due]) map[f.due] = { ratings: [], dues: [] };
    map[f.due].dues.push({ category: f.category, state: f.state });
  }
  return map;
}

function _renderCal() {
  const timelineEl = document.getElementById('cal-timeline');
  if (!timelineEl) return;

  const today = new Date();
  const todayStr = today.toISOString().slice(0, 10);
  const monthNames = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  const dayMap = _buildCalDayMap();

  // Range: first history date → today + 3 months
  const allDates = [
    ...(_calData?.history || []).map(h => h.date),
    ...(_calData?.future  || []).map(f => f.due),
  ];
  let startDate = today;
  if (allDates.length) {
    const minStr = allDates.reduce((a, b) => a < b ? a : b);
    const parsed = new Date(minStr);
    if (!isNaN(parsed)) startDate = parsed;
  }
  const endDate = new Date(today.getFullYear(), today.getMonth() + 4, 0); // last day of today+3 months

  // Find first review date to scroll to on open
  const histDates = (_calData?.history || []).map(h => h.date).filter(Boolean).sort();
  let firstMonthId = null;
  if (histDates.length) {
    const firstParsed = new Date(histDates[0]);
    if (!isNaN(firstParsed)) {
      firstMonthId = `cal-month-${firstParsed.getFullYear()}-${firstParsed.getMonth()}`;
    }
  }

  let html = '';
  let yr = startDate.getFullYear(), mo = startDate.getMonth();
  const endYr = endDate.getFullYear(), endMo = endDate.getMonth();
  let todayMonthId = null;

  while (yr < endYr || (yr === endYr && mo <= endMo)) {
    const monthId = `cal-month-${yr}-${mo}`;
    if (yr === today.getFullYear() && mo === today.getMonth()) todayMonthId = monthId;

    html += `<div class="cal-month-block" id="${monthId}">`;
    html += `<div class="cal-month-heading">${monthNames[mo]} ${yr}</div>`;
    html += `<div class="cal-weekdays"><span>Mo</span><span>Tu</span><span>We</span><span>Th</span><span>Fr</span><span>Sa</span><span>Su</span></div>`;
    html += `<div class="cal-grid">`;

    const firstDay = new Date(yr, mo, 1);
    let startOffset = firstDay.getDay() - 1;
    if (startOffset < 0) startOffset = 6;
    for (let i = 0; i < startOffset; i++) html += '<div class="cal-cell cal-empty"></div>';

    const daysInMonth = new Date(yr, mo + 1, 0).getDate();
    for (let d = 1; d <= daysInMonth; d++) {
      const mm = String(mo + 1).padStart(2, '0');
      const dd = String(d).padStart(2, '0');
      const dateStr = `${yr}-${mm}-${dd}`;
      const isToday = dateStr === todayStr;
      const info = dayMap[dateStr];

      // The current category's chip is suppressed (we're already reviewing it),
      // so only ratings + other-category dues count as visible content. A date
      // whose only due is the current category must render like an empty day:
      // no grey "has-future" background, and its day number must still show.
      const ratings     = info?.ratings || [];
      const visibleDues = (info?.dues || []).filter(f => f.category !== _calCategory);
      const hasVisible   = ratings.length > 0 || visibleDues.length > 0;
      const hasFutureDue = dateStr > todayStr && visibleDues.length > 0;
      html += `<div class="cal-cell${isToday ? ' cal-today' : ''}${hasFutureDue ? ' cal-has-future' : ''}">`;
      if (hasVisible) {
        html += '<div class="cal-chips">';
        for (const r of ratings) {
          const rCls = _RATING_CLASS[r.rating] || 'good';
          const letter = _CAT_LETTER[r.category] || '?';
          html += `<span class="cal-chip cal-chip-${rCls}" title="${r.category}: ${rCls}">${letter}</span>`;
        }
        for (const f of visibleDues) {
          const cCls = _CAT_CLASS[f.category] || '';
          const letter = _CAT_LETTER[f.category] || '?';
          html += `<span class="cal-chip cal-chip-due-${cCls}" title="${f.category} due">${letter}</span>`;
        }
        html += '</div>';
      } else if (isToday && _calCategory) {
        const letter = _CAT_LETTER[_calCategory] || '?';
        const cCls   = _CAT_CLASS[_calCategory]  || '';
        html += `<div class="cal-chips"><span class="cal-chip cal-chip-due-${cCls}" title="${_calCategory} today">${letter}</span></div>`;
      } else {
        html += `<span class="cal-day-num${isToday ? ' cal-day-num-today' : ''}">${d}</span>`;
      }
      html += '</div>';
    }

    html += '</div></div>'; // close cal-grid + cal-month-block

    mo++;
    if (mo > 11) { mo = 0; yr++; }
  }

  timelineEl.innerHTML = html;

  // Scroll to first reviewed month (or today if no history)
  const scrollTargetId = firstMonthId || todayMonthId;
  if (scrollTargetId) {
    requestAnimationFrame(() => {
      const panel = document.getElementById('review-cal-panel');
      const el    = document.getElementById(scrollTargetId);
      if (panel && el) {
        const panelRect = panel.getBoundingClientRect();
        const elRect    = el.getBoundingClientRect();
        panel.scrollTop += elRect.top - panelRect.top;
      }
    });
  }
}

async function _loadCardCalendar(cardId, category) {
  const panel = document.getElementById('review-cal-panel');
  _calData     = null;
  _calCategory = category || null;
  if (panel) panel.style.display = 'none';
  try {
    const data = await api('GET', `/api/cards/${cardId}/calendar`);
    if (!data) return;
    _calData = data;
    const today = new Date();
    _calYear  = today.getFullYear();
    _calMonth = today.getMonth();
    _renderCal();
    if (panel) panel.style.display = '';
  } catch (e) { /* silently skip if unavailable */ }
}

// ── Card timer ──────────────────────────────────────────────────────────────
function _startTimer() {
  _stopTimer();
  _timerStart = Date.now();
  const el = document.getElementById('card-timer');
  el.textContent = '0s';
  el.style.display = 'block';
  _timerInterval = setInterval(() => {
    const s = Math.floor((Date.now() - _timerStart) / 1000);
    el.textContent = s < 60 ? `${s}s` : `${Math.floor(s / 60)}m${s % 60}s`;
  }, 1000);
}
function _stopTimer() {
  if (_timerInterval) { clearInterval(_timerInterval); _timerInterval = null; }
  document.getElementById('card-timer').style.display = 'none';
}
function _updateAvgTimeBadge() {
  const el = document.getElementById('avg-time-badge');
  if (_sessionRatedCount === 0) { el.style.display = 'none'; return; }
  const avgS = Math.round(_sessionTotalMs / _sessionRatedCount / 1000);
  const label = avgS < 60 ? `${avgS}s` : `${Math.floor(avgS / 60)}m${avgS % 60}s`;
  el.textContent = `avg ${label}/card`;
  el.style.display = 'inline';
}

// ── Story info row (Sentence x/y · Topic) ───────────────────────────────────
function _updateStoryInfoRow() {
  const row = document.getElementById('story-info-row');
  if (sentence && story?.sentences?.length) {
    const pos = `Sentence ${sentence.position + 1} / ${story.sentences.length}`;
    const label = story.topic ? `${pos}  ·  ${story.topic}` : pos;
    row.innerHTML = `<span class="story-info-label">${label}</span><button class="story-regen-btn" onclick="event.stopPropagation();regenerateStory()" title="Regenerate story">↺</button>`;
    row.style.display = 'flex';
  } else {
    row.style.display = 'none';
  }
}

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
function _triggerClapAnimation() {
  const emojis = ['👏', '👏', '👏', '⭐', '✨', '🌟'];
  const count = 18;
  for (let i = 0; i < count; i++) {
    setTimeout(() => {
      const el = document.createElement('span');
      el.className = 'clap-particle';
      el.textContent = emojis[Math.floor(Math.random() * emojis.length)];
      const x = 5 + Math.random() * 90;
      const rise = 55 + Math.random() * 35;
      const dur = 1.4 + Math.random() * 0.8;
      const tilt = (Math.random() - 0.5) * 30;
      el.style.cssText = `left:${x}vw;--rise:-${rise}vh;--dur:${dur}s;--tilt:${tilt}deg`;
      document.body.appendChild(el);
      el.addEventListener('animationend', () => el.remove());
    }, i * 80);
  }
}

function showView(name) {
  if (name === 'done' && _sessionReviewedCount > 0) _triggerClapAnimation();
  ['loading', 'decks', 'review', 'done', 'browse', 'word-detail', 'hanzi-detail', 'stats'].forEach(v => {
    document.getElementById(`view-${v}`).style.display = 'none';
  });
  document.getElementById(`view-${name}`).style.display =
    name === 'browse' ? 'flex' : 'block';
  document.querySelector('main').classList.toggle('browse-open', name === 'browse');
  document.querySelector('main').classList.toggle('review-open', name === 'review');
  const countsRow = document.getElementById('counts-row');
  if (countsRow) countsRow.style.display = name === 'review' ? 'flex' : 'none';
  document.getElementById('back-btn').style.display = name === 'decks' ? 'none' : 'block';
  document.getElementById('header-title').textContent =
    name === 'review'       ? deckName :
    name === 'browse'       ? 'Browse' :
    name === 'word-detail'  ? 'Word Detail' :
    name === 'hanzi-detail' ? 'Hanzi Detail' :
    name === 'stats'        ? 'Stats' : 'AnkiAdvanced';
  if (name === 'decks') quickMode = false;
  const headerRegenBtn = document.getElementById('header-regen-btn');
  if (headerRegenBtn) headerRegenBtn.style.display = (name === 'review' && !unfinishedMode && !quickMode) ? '' : 'none';
  if (name === 'review') {
    const regenBtn = document.querySelector('.regen-btn');
    if (regenBtn) regenBtn.style.display = (unfinishedMode || quickMode) ? 'none' : '';
  }
}

// Show the loading view. Pass useProgress=true for story/audio generation to show the progress bar.
function setLoading(msg, useProgress = false) {
  document.getElementById('loading-msg').textContent = msg || 'Loading…';
  const wrap = document.getElementById('loading-progress-wrap');
  const bar  = document.getElementById('loading-progress-bar');
  const sub  = document.getElementById('loading-sub');
  const spinner = document.getElementById('loading-spinner');
  if (useProgress) {
    wrap.style.display = 'block';
    bar.style.width = '0%';
    bar.className = '';
  } else {
    wrap.style.display = 'none';
  }
  if (sub) { sub.textContent = ''; sub.className = ''; }
  if (spinner) spinner.style.visibility = '';
  showView('loading');
}

// Update progress bar and status text during a multi-step loading operation.
// percent: 0–100; msg: main heading (optional); sub: detail line (optional)
function setLoadingStep(percent, msg, sub) {
  const bar   = document.getElementById('loading-progress-bar');
  const msgEl = document.getElementById('loading-msg');
  const subEl = document.getElementById('loading-sub');
  if (bar)   { bar.style.width = percent + '%'; bar.className = ''; }
  if (msgEl && msg) msgEl.textContent = msg;
  if (subEl) { subEl.textContent = sub || ''; subEl.className = ''; }
}

// Slowly advance the progress bar from `from` → `to` percent over `durationMs`.
// Returns a cancel function. Does NOT set the bar above `to`.
let _fakeProgressTimer = null;
function _startFakeProgress(from, to, durationMs) {
  _stopFakeProgress();
  const steps = Math.ceil(durationMs / 250);
  const inc   = (to - from) / steps;
  let current = from;
  _fakeProgressTimer = setInterval(() => {
    current = Math.min(current + inc, to);
    const bar = document.getElementById('loading-progress-bar');
    if (bar && parseFloat(bar.style.width) < current) bar.style.width = current + '%';
  }, 250);
}
function _stopFakeProgress() {
  if (_fakeProgressTimer) { clearInterval(_fakeProgressTimer); _fakeProgressTimer = null; }
}

// Poll /api/story-progress/{deckId}/{cat} and update the loading sub-text + progress bar.
// Handles warning phase (retry): resets bar to 5% and restarts fake progress.
let _storyProgressPoll = null;
function _startStoryProgressPoll(deckId, cat) {
  _stopStoryProgressPoll();
  _storyProgressPoll = setInterval(async () => {
    try {
      const p = await fetch(`/api/story-progress/${deckId}/${cat}`).then(r => r.json());
      if (!p || p.phase === 'idle') return;
      const subEl = document.getElementById('loading-sub');
      const bar   = document.getElementById('loading-progress-bar');
      if (p.translate_warn) {
        if (subEl) { subEl.textContent = p.translate_warn; subEl.className = 'warn'; }
        return;
      }
      if (p.phase === 'warning') {
        _stopFakeProgress();
        if (bar) { bar.style.width = '5%'; bar.className = 'warn'; }
        _startFakeProgress(5, 50, 30000);
        if (subEl) { subEl.textContent = p.msg; subEl.className = 'warn'; }
      } else if (p.msg) {
        if (subEl) { subEl.textContent = p.msg; subEl.className = ''; }
        if (bar && p.phase !== 'ai_done') bar.className = '';
      }
    } catch (_) {}
  }, 400);
}
function _stopStoryProgressPoll() {
  if (_storyProgressPoll) { clearInterval(_storyProgressPoll); _storyProgressPoll = null; }
}

// Preload TTS for a session while polling per-sentence progress.
// deckId/cat → used to build the API URL and progress-poll key.
// onProgress(done, total) called whenever progress updates.
async function _preloadWithProgress(deckId, cat, onProgress) {
  let finished = false;
  const preloadDone = fetch(`/api/preload-session/${deckId}/${cat}`, { method: 'POST' })
    .then(() => { finished = true; })
    .catch(() => { finished = true; });

  // Poll progress endpoint until preload completes
  while (!finished) {
    await new Promise(r => setTimeout(r, 350));
    if (finished) break;
    try {
      const p = await fetch(`/api/tts-progress/${deckId}/${cat}`).then(r => r.json());
      if (p.total > 0) onProgress(p.done, p.total);
      if (p.error) {
        const subEl = document.getElementById('loading-sub');
        if (subEl) { subEl.textContent = p.error; subEl.className = 'warn'; }
      }
    } catch (_) {}
  }
  await preloadDone;
}

function _showLoadingSuccess(msg) {
  const bar   = document.getElementById('loading-progress-bar');
  const msgEl = document.getElementById('loading-msg');
  const subEl = document.getElementById('loading-sub');
  const spinner = document.getElementById('loading-spinner');
  if (bar)    { bar.style.width = '100%'; bar.className = 'success'; }
  if (msgEl)  msgEl.textContent = msg || 'Done!';
  if (subEl)  { subEl.textContent = ''; subEl.className = ''; }
  if (spinner) spinner.style.visibility = 'hidden';
}

function _showLoadingError(headline, detail) {
  const bar   = document.getElementById('loading-progress-bar');
  const msgEl = document.getElementById('loading-msg');
  const subEl = document.getElementById('loading-sub');
  const spinner = document.getElementById('loading-spinner');
  if (bar)    { bar.className = 'error'; }
  if (msgEl)  msgEl.textContent = headline || 'Failed';
  if (subEl)  { subEl.textContent = detail || ''; subEl.className = detail ? 'error' : ''; }
  if (spinner) spinner.style.visibility = 'hidden';
}

function _resetLoadingSpinner() {
  const spinner = document.getElementById('loading-spinner');
  if (spinner) spinner.style.visibility = '';
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
    const [decks, retention] = await Promise.all([
      api('GET', '/api/decks'),
      api('GET', '/api/retention?days=0').catch(() => null),
    ]);
    _cachedDecks = decks;
    _retentionData = retention;
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

// ── Retention rate helpers ────────────────────────────────────────────────────

function _rrClass(val) {
  if (val === null) return '';
  if (val >= 0.90) return 'rr-high';
  if (val >= 0.75) return 'rr-mid';
  return 'rr-low';
}

function _formatRR(val) {
  if (val === null) return '—';
  return Math.round(val * 100) + '%';
}

function _mixNewBtn(deckId, override) {
  const icons = { mixed: '⇄', reviews_first: '↓', new_first: '↑' };
  const titles = {
    mixed:        'Override: mixed (click → after reviews)',
    reviews_first:'Override: new after reviews (click → new before reviews)',
    new_first:    'Override: new before reviews (click → no override)',
    null:         'No override — using deck setting (click → mixed)',
  };
  const icon  = icons[override] || '⇄';
  const title = titles[override ?? 'null'] || titles['null'];
  const cls   = override ? 'mix-new-btn mix-on' : 'mix-new-btn';
  return `<button class="${cls}" onclick="event.stopPropagation();toggleMixNew(${deckId})" title="${title}">${icon}</button>`;
}

// Compute RR for a deck (structural or leaf) using cached _retentionData
function _calcDeckRR(deck) {
  if (!_retentionData?.by_deck) return { overall: null, by_category: {} };
  const leaves = deck.category
    ? [deck]
    : getDeepCategoryLeaves(deck);

  let totalC = 0, totalT = 0;
  const byCat = {};

  for (const leaf of leaves) {
    const d = _retentionData.by_deck[leaf.id];
    if (!d) continue;
    totalC += d.correct;
    totalT += d.total;
    const cat = leaf.category;
    if (cat) {
      if (!byCat[cat]) byCat[cat] = { c: 0, t: 0 };
      byCat[cat].c += d.correct;
      byCat[cat].t += d.total;
    }
  }

  const overall = totalT > 0 ? totalC / totalT : null;
  const by_category = {};
  for (const [cat, v] of Object.entries(byCat)) {
    by_category[cat] = v.t > 0 ? v.c / v.t : null;
  }
  return { overall, total: totalT, by_category };
}

// Build tooltip text for a deck's RR
function _rrTooltip(rr) {
  const lines = [`Today's retention: ${_formatRR(rr.overall)} (${rr.total ?? 0} reviews)`];
  const LABELS = { reading: 'R', listening: 'L', creating: 'C' };
  for (const [cat, val] of Object.entries(rr.by_category)) {
    lines.push(`${LABELS[cat] ?? cat}: ${_formatRR(val)}`);
  }
  return lines.join(' · ');
}

// Update the RR badge in the review header
function _updateReviewRRBadge(deckOrId) {
  const badge = document.getElementById('review-rr-badge');
  if (!_retentionData) return;
  let rr;
  if (typeof deckOrId === 'object') {
    rr = _calcDeckRR(deckOrId);
  } else {
    const deck = _findDeckInTree(_cachedDecks, deckOrId);
    if (!deck) { if (badge) badge.style.display = 'none'; _clearCatRRSpans(); return; }
    rr = _calcDeckRR(deck);
  }
  // Overall badge
  if (badge) {
    badge.textContent = 'RR ' + _formatRR(rr.overall);
    badge.className = 'review-rr-badge' + (rr.overall === null ? ' rr-no-data' : '');
    badge.title = rr.overall === null ? 'No reviews yet today' : _rrTooltip(rr);
    badge.style.display = '';
  }
  // Per-category spans
  const MAP = { reading: 'r', listening: 'l', creating: 'c' };
  for (const [cat, key] of Object.entries(MAP)) {
    const el = document.getElementById(`cnt-${key}-rr`);
    if (!el) continue;
    const val = rr.by_category[cat] ?? null;
    el.textContent = _formatRR(val);
    el.className = 'cnt-cat-rr';
  }
}

function _clearCatRRSpans() {
  for (const key of ['r', 'l', 'c']) {
    const el = document.getElementById(`cnt-${key}-rr`);
    if (el) { el.textContent = ''; el.className = 'cnt-cat-rr'; }
  }
}

function _findDeckInTree(nodes, id) {
  for (const n of (nodes || [])) {
    if (n.id === id) return n;
    const found = _findDeckInTree(n.children, id);
    if (found) return found;
  }
  return null;
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


// Compute RR for a list of leaf deck objects (using cached _retentionData)
function _leavesRR(leaves) {
  if (!_retentionData?.by_deck) return null;
  let c = 0, t = 0;
  for (const l of leaves) {
    const d = _retentionData.by_deck[l.id];
    if (d) { c += d.correct; t += d.total; }
  }
  return t > 0 ? c / t : null;
}

function _catRRSpan(val) {
  const cls = val === null ? 'rr-none' : '';
  const txt = val === null ? '—' : _formatRR(val);
  return `<span class="cat-pill-rr ${cls}">${txt}</span>`;
}

// Build 3 inline pills (L/R/C) for any deck. Uses direct cat leaves if present, else aggregates.
function buildCategoryButtons(deck) {
  const DEFAULT_ORDER = ['listening', 'reading', 'creating'];
  const orderStr = deck.category_order || 'listening,reading,creating';
  const ordered = orderStr.split(',').map(s => s.trim()).filter(s => DEFAULT_ORDER.includes(s));
  // Ensure all 3 categories present (in case of corrupt/partial value)
  const CATS = [...ordered, ...DEFAULT_ORDER.filter(c => !ordered.includes(c))];
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
      const rr = _leavesRR([leaf]);
      return `<span class="cat-pill-group"><button class="${badgeClass}" onclick="event.stopPropagation();toggleCategorySuspension(${leaf.id},'${cat}')" title="${title}">${badgeIcon}</button><span class="cat-pill-wrap"><button class="${pillClass}" onclick="event.stopPropagation();startReview(${leaf.id},'${cat}','${safeName}',${!!leaf.no_story})"><span class="cat-pill-label">${label}</span><span class="cat-pill-counts">${countHtml(c)}</span>${_catRRSpan(rr)}</button><button class="cat-pill-quick" onclick="event.stopPropagation();startReview(${leaf.id},'${cat}','${safeName}',${!!leaf.no_story},true)" title="Quick review — no AI story">⚡</button><button class="cat-pill-gear" onclick="event.stopPropagation();openOptions(${leaf.id})" title="Options">⚙</button></span></span>`;
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
    const rr = _leavesRR(leaves);
    return `<span class="cat-pill-group"><button class="${badgeClass}" onclick="event.stopPropagation();toggleCategorySuspension(${deck.id},'${cat}')" title="${title}">${badgeIcon}</button><span class="cat-pill-wrap"><button class="${pillClass}" onclick="event.stopPropagation();startReview(${deck.id},'${cat}','${safeName}',${!!deck.no_story})"><span class="cat-pill-label">${label}</span><span class="cat-pill-counts">${countHtml(c)}</span>${_catRRSpan(rr)}</button><button class="cat-pill-quick" onclick="event.stopPropagation();startReview(${deck.id},'${cat}','${safeName}',${!!deck.no_story},true)" title="Quick review — no AI story">⚡</button><button class="cat-pill-gear" onclick="event.stopPropagation();openOptions(${deck.id})" title="Options">⚙</button></span></span>`;
  }).join('');
}

function renderDecks(decks) {
  const navRow = `
    <div class="nav-row">
      <button class="nav-btn" onclick="openBrowse()" title="Shortcut: B">Browse Cards</button>
      <button class="nav-btn" onclick="openStats()">Stats</button>
      <button class="nav-btn" onclick="openCostModal()">API Costs</button>
      <button class="nav-btn" onclick="openImportModal()" title="Shortcut: Command+I">Import</button>
      <button class="nav-btn" onclick="openQuickAddCard()" title="Shortcut: A">Add Card</button>
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
    const allBuryMode  = allDeck.bury_mode || 'all';
    const allBuryIcon  = allBuryMode === 'all' ? '⛓' : allBuryMode === 'none' ? '⊘' : '≡';
    const allBuryClass = `bury-btn bury-${allBuryMode}`;
    const allBuryTitle = allBuryMode === 'all'  ? 'Bury siblings: All (click for None)'
                       : allBuryMode === 'none' ? 'Bury siblings: None (click for Custom)'
                       :                          'Bury siblings: Custom (click for All)';
    const allRRData = _retentionData?.all;
    const allRRVal = allRRData?.total > 0 ? allRRData.correct / allRRData.total : null;
    const allRRBadge = allRRVal !== null
      ? `<span class="deck-rr-badge" title="Today's retention: ${_formatRR(allRRVal)} (${allRRData.total} reviews)">${_formatRR(allRRVal)}</span>`
      : '';
    filteredHtml += `
      <div class="tree-row tree-parent">
        <span class="tree-toggle"></span>
        <span class="tree-name" onclick="startReviewMixed(${allDeck.id},'${safeName}')" style="cursor:pointer">All</span>
        <span class="deck-counts">${_mixNewBtn(allDeck.id, allDeck.new_review_order_override)}<span class="n-new">${(allDeck.counts||{}).new||0}</span><span class="n-lrn">${(allDeck.counts||{}).learning||0}</span><span class="n-rev">${(allDeck.counts||{}).review||0}</span></span>
        ${allRRBadge}
        <button class="${allBuryClass}" onclick="event.stopPropagation();toggleBury(${allDeck.id})" title="${allBuryTitle}">${allBuryIcon}</button>
        <div class="deck-menu-wrap">
          <button class="deck-susp-btn ${allDeck.deck_all_suspended ? 'deck-all-suspended' : ''}" onclick="event.stopPropagation();toggleDeckAllSuspension(${allDeck.id})" title="${allDeck.deck_all_suspended ? 'Unsuspend all cards' : 'Suspend all cards'}">${allDeck.deck_all_suspended ? '▶' : '⏸'}</button>
          <button class="gear-btn" onclick="event.stopPropagation();toggleDeckMenu(event,${allDeck.id},'${safeName}',false)" title="Deck options">⚙</button>
        </div>
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
  const deckSortMode = localStorage.getItem('deckSortMode') || 'name';
  const sortLabel = deckSortMode === 'due' ? 'Sort: Due ↓' : deckSortMode === 'name-desc' ? 'Sort: Z→A' : 'Sort: A→Z';
  const regularHtml = renderDeckRows(regularDecks, 0, deckSortMode);
  if (regularHtml.trim()) {
    html += `<div class="section-label section-label-row">Decks<button class="deck-sort-btn" onclick="toggleDeckSort()">${sortLabel}</button></div><div class="tree-card">${regularHtml}</div>`;
  }

  document.getElementById('view-decks').innerHTML =
    navRow + '<div id="home-calendar" class="cal-card"></div>' + html;
  if (typeof initCalendar === 'function') initCalendar();
}

function toggleDeckSort() {
  const cur = localStorage.getItem('deckSortMode') || 'name';
  const next = cur === 'name' ? 'name-desc' : cur === 'name-desc' ? 'due' : 'name';
  localStorage.setItem('deckSortMode', next);
  renderDecks(_cachedDecks);
}

function renderDeckRows(decks, depth, sortMode) {
  const mode = sortMode || 'name';
  const sorted = [...decks].sort((a, b) => {
    if (mode === 'due') {
      const dueA = (a.counts?.new || 0) + (a.counts?.learning || 0) + (a.counts?.review || 0);
      const dueB = (b.counts?.new || 0) + (b.counts?.learning || 0) + (b.counts?.review || 0);
      return dueB - dueA || a.name.localeCompare(b.name);
    }
    if (mode === 'name-desc') return b.name.localeCompare(a.name);
    return a.name.localeCompare(b.name);
  });
  return sorted.map(deck => {
    // Category leaf decks are consumed as pills — not rendered as rows
    if (deck.category && (!deck.children || deck.children.length === 0)) return '';

    const structChildren = (deck.children || [])
      .filter(c => !(c.category && (!c.children || c.children.length === 0)));
    const hasStructChildren = structChildren.length > 0;
    const isCollapsed = collapsed.has(deck.id);
    const indent = depth * 18;

    const toggleIcon = hasStructChildren ? (isCollapsed ? '▶' : '▼') : '';
    const safeName  = deck.name.replace(/'/g, "\\'");
    const c = deck.counts || { new: 0, learning: 0, review: 0 };
    const deckCounts = `<span class="deck-counts">${_mixNewBtn(deck.id, deck.new_review_order_override)}<span class="n-new">${c.new}</span><span class="n-lrn">${c.learning}</span><span class="n-rev">${c.review}</span></span>`;

    const buryMode   = deck.bury_mode || 'all';
    const buryIcon   = buryMode === 'all' ? '⛓' : buryMode === 'none' ? '⊘' : '≡';
    const buryClass  = `bury-btn bury-${buryMode}`;
    const buryTitle  = buryMode === 'all'    ? 'Bury siblings: All (click for None)'
                     : buryMode === 'none'   ? 'Bury siblings: None (click for Custom)'
                     :                         'Bury siblings: Custom (click for All)';
    const rrData = _calcDeckRR(deck);
    const rrBadge = rrData.overall !== null
      ? `<span class="deck-rr-badge" title="${_rrTooltip(rrData)}">${_formatRR(rrData.overall)}</span>`
      : '';
    const row = `
      <div class="tree-row tree-parent" style="padding-left:${16 + indent}px">
        <span class="tree-toggle" onclick="toggleDeck(${deck.id})">${toggleIcon}</span>
        <span class="tree-name-wrap">
          <span class="tree-name" onclick="startReviewMixed(${deck.id},'${safeName}',${!!deck.no_story})" style="cursor:pointer">${deck.name}</span>
          ${!deck.no_story ? `<button class="deck-regen-btn" onclick="event.stopPropagation();regenerateStoryFromList(${deck.id})" title="Regenerate story">↺</button>` : ''}
        </span>
        ${deckCounts}
        ${rrBadge}
        <button class="${buryClass}" onclick="event.stopPropagation();toggleBury(${deck.id})" title="${buryTitle}">${buryIcon}</button>
        <div class="deck-menu-wrap">
          <button class="deck-susp-btn ${deck.deck_all_suspended ? 'deck-all-suspended' : ''}" onclick="event.stopPropagation();toggleDeckAllSuspension(${deck.id})" title="${deck.deck_all_suspended ? 'Unsuspend all cards' : 'Suspend all cards'}">${deck.deck_all_suspended ? '▶' : '⏸'}</button>
          <button class="gear-btn" onclick="event.stopPropagation();toggleDeckMenu(event,${deck.id},'${safeName}',${!!deck.filtered})" title="Deck options">⚙</button>
        </div>
        <div class="cat-pills-row">${buildCategoryButtons(deck)}</div>
      </div>`;

    const childRows = hasStructChildren && !isCollapsed
      ? renderDeckRows(structChildren, depth + 1, mode)
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
  localStorage.setItem('collapsedDecks', JSON.stringify([...collapsed]));
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

async function toggleMixNew(deckId) {
  try {
    const { new_review_order_override } = await api('POST', `/api/decks/${deckId}/preset/toggle-mix`);
    if (_cachedDecks) {
      const flat = [];
      const walk = nodes => nodes.forEach(n => { flat.push(n); walk(n.children || []); });
      walk(_cachedDecks);
      const deck = flat.find(d => d.id === deckId);
      if (deck) deck.new_review_order_override = new_review_order_override;
      const scrollEl = document.querySelector('main');
      const scrollY = scrollEl ? scrollEl.scrollTop : 0;
      renderDecks(_cachedDecks);
      if (scrollEl) scrollEl.scrollTop = scrollY;
    }
  } catch (e) {
    showError('Failed to toggle mix setting: ' + e.message);
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
      <button onclick="closeDeckMenu();openOptions(${id})">Options</button>
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

async function openQuickAddCard() {
  const defaultDeck = (document.getElementById('import-deck-path')?.value || '').trim();
  const deckPath = await showPrompt('Deck path for new card (use :: to nest)', defaultDeck);
  if (!deckPath || !deckPath.trim()) return;

  const yamlTemplate = [
    'type: word',
    'simplified: ',
    'traditional: ',
    'pinyin: ',
    'hsk: 1',
    'translations:',
    '  en: ',
    '  zh-CN: ',
  ].join('\n');

  openYamlEdit('Add card', yamlTemplate, deckPath.trim(), -1);
}

// ── Browse ───────────────────────────────────────────────────────────────────
let _browseSearchTimer = null;
let _browseMode       = 'notes';   // 'notes' | 'hanzi'
let _browseFilter     = 'all';     // note_type or 'all'; for hanzi mode: 'all'
let _browseCardStatus = 'all';     // 'all' | 'learning' | 'reference'
let _browseDeckId     = null;      // deck filter (notes mode only)
let _allHanzi         = [];        // cache

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

function _leafDeckIds(deckId) {
  const deck = _browseDecks.find(d => d.id === deckId);
  if (!deck) return new Set([deckId]);
  const ids = new Set();
  function collect(nodes) {
    for (const n of nodes) {
      if (!n.children?.length) ids.add(n.id);
      else collect(n.children);
    }
  }
  if (deck.children?.length) collect(deck.children);
  else ids.add(deckId);
  return ids;
}

function _filteredBrowseWords() {
  let words = browseWords;
  if (_browseFilter !== 'all') words = words.filter(w => w.note_type === _browseFilter);
  if (_browseDeckId !== null) {
    const leafIds = _leafDeckIds(_browseDeckId);
    words = words.filter(w => w.cards.some(c => leafIds.has(c.deck_id)));
  }
  if (_browseCardStatus === 'learning')   words = words.filter(w => w.cards.length > 0);
  if (_browseCardStatus === 'reference')  words = words.filter(w => w.cards.length === 0);
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

function setBrowseStatusFilter(status) {
  _browseCardStatus = status;
  document.querySelectorAll('.bs-status-item').forEach(el => el.classList.remove('bs-active'));
  const btn = document.getElementById(`bss-${status}`);
  if (btn) btn.classList.add('bs-active');
  document.getElementById('browse-search').value = '';
  _browseSelected.clear();
  _updateBrowseActionBar();
  if (_browseMode === 'notes') renderBrowseWords(_filteredBrowseWords());
}

function setBrowseDeckFilter(deckId) {
  if (_browseDeckId === deckId) {
    _browseDeckId = null;
    document.querySelectorAll('.bs-deck-item').forEach(el => el.classList.remove('bs-active'));
    _browseSelected.clear();
    _updateBrowseActionBar();
    renderBrowseWords(_filteredBrowseWords());
    return;
  }
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
    // Top-level user decks: children of the "All" virtual root
    const _allRoot = deckTree.find(d => d.virtual && d.id !== 'unfinished');
    _browseDeckTree = _allRoot ? (_allRoot.children || []) : deckTree.filter(d => !d.virtual);
    _browseDeckExpanded = new Set();
    _browseSelected.clear();
    _browseCardStatus = 'all';
    showView('browse');
    document.getElementById('browse-search').value = '';
    document.getElementById('browse-sort').value = _browseSort;
    _renderBrowseSidebar();
    _updateBrowseActionBar();
    document.querySelectorAll('.bs-status-item').forEach(el => el.classList.remove('bs-active'));
    const bssAll = document.getElementById('bss-all');
    if (bssAll) bssAll.classList.add('bs-active');
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

function _hasExpandableChildren(deck) {
  return (deck.children || []).some(c => !c.category && !c.virtual);
}

function _renderDeckTreeNode(deck, depth) {
  const isExpanded = _browseDeckExpanded.has(deck.id);
  const hasKids = _hasExpandableChildren(deck);
  const isActive = _browseDeckId === deck.id;
  const indent = 16 + depth * 14;

  const arrow = hasKids
    ? `<span class="bs-deck-arrow">${isExpanded ? '▾' : '▸'}</span>`
    : `<span class="bs-deck-arrow bs-deck-arrow-leaf"></span>`;

  const onclick = hasKids
    ? `toggleBrowseDeckExpand(${deck.id})`
    : `setBrowseDeckFilter(${deck.id})`;

  let html = `<button class="bs-deck-item${isActive ? ' bs-active' : ''}" data-id="${deck.id}"
    style="padding-left:${indent}px" onclick="${onclick}">${arrow}${deck.name}</button>`;

  if (isExpanded && hasKids) {
    const kids = (deck.children || [])
      .filter(c => !c.category && !c.virtual)
      .sort((a, b) => a.name.localeCompare(b.name));
    html += kids.map(c => _renderDeckTreeNode(c, depth + 1)).join('');
  }
  return html;
}

function _renderBrowseSidebar() {
  const container = document.getElementById('browse-deck-tree');
  const topLevel = _browseDeckTree
    .filter(d => !d.category && !d.virtual)
    .sort((a, b) => a.name.localeCompare(b.name));
  container.innerHTML = topLevel.map(d => _renderDeckTreeNode(d, 0)).join('');
}

function toggleBrowseDeckExpand(deckId) {
  if (_browseDeckExpanded.has(deckId)) {
    _browseDeckExpanded.delete(deckId);
  } else {
    _browseDeckExpanded.add(deckId);
  }
  _renderBrowseSidebar();
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
  const def = (w.definition || '').slice(0, 60) + ((w.definition || '').length > 60 ? '…' : '');
  const sel = _browseSelected.has(w.id) ? ' bw-row-selected' : '';
  let rightHtml;
  if (w.cards.length === 0) {
    rightHtml = `<button class="bw-add-btn" onclick="openAddToDeckModal(event,${w.id})" title="添加到牌组">＋ 添加</button>`;
  } else {
    const CAT_LETTER = { listening: 'L', reading: 'R', creating: 'C' };
    rightHtml = ['listening', 'reading', 'creating'].map(cat => {
      const c = w.cards.find(c => c.category === cat);
      const letter = CAT_LETTER[cat];
      if (!c) return `<button class="rcat-btn bw-rcat-missing" title="${cat}: —" disabled>${letter}</button>`;
      const isSusp = c.state === 'suspended';
      const cls = `rcat-btn ${isSusp ? 'rcat-susp' : 'rcat-active'}`;
      const tip = `${cat}: ${c.state} — click to ${isSusp ? 'activate' : 'suspend'}`;
      return `<button class="${cls}" title="${tip}" onclick="toggleBrowseDotSuspend(event,${c.id},${w.id})">${letter}</button>`;
    }).join('');
  }
  return `
    <div class="bw-row${sel}" data-word-id="${w.id}" onclick="onBrowseRowClick(event,${w.id})">
      <div class="bw-left">
        <span class="bw-hanzi">${w.word_zh}</span>
        <span class="bw-pinyin">${w.pinyin || ''}</span>
      </div>
      <div class="bw-mid">
        <span class="bw-def">${def}</span>
      </div>
      <div class="bw-right">${rightHtml}</div>
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
  _renderBrowseSidebar();
  if (q) onBrowseSearch(q);
  else renderBrowseWords(_filteredBrowseWords());
}

// ── Add to deck modal ─────────────────────────────────────────────────────────
let _addToDeckEntryId = null;

function openAddToDeckModal(e, entryId) {
  e.stopPropagation();
  _addToDeckEntryId = entryId;
  const select = document.getElementById('add-to-deck-select');
  const parentDecks = _browseDecks.filter(d => !d.category && !d.virtual);
  if (!parentDecks.length) { showError('No decks available'); return; }
  select.innerHTML = parentDecks.map(d => `<option value="${d.id}">${d.name}</option>`).join('');
  document.getElementById('add-to-deck-modal-overlay').style.display = '';
  document.getElementById('add-to-deck-modal').style.display = '';
}

function closeAddToDeckModal() {
  document.getElementById('add-to-deck-modal-overlay').style.display = 'none';
  document.getElementById('add-to-deck-modal').style.display = 'none';
  _addToDeckEntryId = null;
}

async function confirmAddToDeck() {
  const deckId = parseInt(document.getElementById('add-to-deck-select').value);
  if (!deckId || !_addToDeckEntryId) return;
  try {
    await api('POST', `/api/entries/${_addToDeckEntryId}/add-to-deck`, { deck_id: deckId });
    closeAddToDeckModal();
    await _browseReload();
  } catch (e) { showError('Failed to add to deck: ' + e.message); }
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
async function openWordByZh(zh) {
  let word = browseWords.find(w => w.word_zh === zh);
  if (word) { openWordDetail(word.id); return; }
  try {
    const all = await api('GET', '/api/browse-words');
    browseWords = all;
    const found = all.find(w => w.word_zh === zh);
    if (found) openWordDetail(found.id);
    else showError(`"${zh}" not found`);
  } catch (e) { showError(e.message); }
}

async function openWordDetail(wordId) {
  // Capture which view we're coming from so we can go back to it
  const views = ['review', 'browse', 'hanzi-detail', 'word-detail', 'stats', 'done', 'decks'];
  _prevView = views.find(v => document.getElementById(`view-${v}`)?.style.display !== 'none') || null;
  _currentWordId = wordId;
  setLoading('Loading word…');
  try {
    const word = await api('GET', `/api/word/${wordId}`);
    word.cards = await api('GET', `/api/words/${wordId}/cards`);
    renderWordDetail(word);
    showView('word-detail');
    const backBtn = document.getElementById('wd-back-review-btn');
    if (backBtn) backBtn.style.display = _prevView === 'review' ? 'block' : 'none';
  } catch (e) {
    showError('Failed to load word: ' + e.message);
    showView('browse');
  }
}

function renderWordDetail(word) {
  document.getElementById('wd-edit-btn').onclick = () => openWordEditModal(word);
  const regenAllBtn = document.getElementById('wd-regen-all-btn');
  if (regenAllBtn) regenAllBtn.onclick = () => word.id && regenAllFields(word.id);
  document.getElementById('wd-hanzi').textContent = word.word_zh || '';
  document.getElementById('wd-pinyin').textContent = word.pinyin || '';
  document.getElementById('wd-def').textContent = word.definition || '';
  const posEl = document.getElementById('wd-pos');
  posEl.textContent = word.pos || '—';
  posEl.style.display = 'inline-block';
  const regEl = document.getElementById('wd-register');
  const regLabels = {
    spoken: '口语', written: '书面语', both: '通用',
    spoken_colloquial: '口语俚语', spoken_neutral: '中性口语',
    neutral: '通用', formal_written: '正式书面语', literary: '文学语体'
  };
  if (word.register) {
    regEl.textContent = regLabels[word.register] || word.register;
    regEl.style.display = 'inline-block';
  } else {
    regEl.style.display = 'none';
  }
  const defZhEl = document.getElementById('wd-def-zh');
  defZhEl.textContent = word.definition_zh || '';
  defZhEl.style.display = word.definition_zh ? 'block' : 'none';
  const defDeEl = document.getElementById('wd-def-de');
  defDeEl.textContent = word.definition_de ? `🇩🇪 ${word.definition_de}` : '';
  defDeEl.style.display = word.definition_de ? 'block' : 'none';
  const defFrEl = document.getElementById('wd-def-fr');
  defFrEl.textContent = word.definition_fr ? `🇫🇷 ${word.definition_fr}` : '';
  defFrEl.style.display = word.definition_fr ? 'block' : 'none';

  const defRegenEl = document.getElementById('wd-def-regen');
  if (defRegenEl) {
    defRegenEl.innerHTML = word.id
      ? `<button class="field-regen-btn" onclick="event.stopPropagation();regenFields(${word.id},['definition','definition_zh','definition_de','definition_fr','pos'],'wd-def-block')" title="Regenerate definitions & part of speech">↺</button>`
      : '';
  }

  // Synonyms / antonyms section — collapsible, clickable
  const relEl = document.getElementById('wd-relations-section');
  const synonyms = (word.relations || []).filter(r => r.relation_type === 'synonym');
  const antonyms = (word.relations || []).filter(r => r.relation_type === 'antonym');
  if (synonyms.length || antonyms.length) {
    const _relItem = r =>
      `<span class="wd-rel-item wd-rel-link" title="${r.related_de || ''}"
        onclick="openWordByZh(${_ea(JSON.stringify(r.related_zh))})">${r.related_zh}` +
      (r.related_pinyin ? ` <span class="wd-rel-pin">${r.related_pinyin}</span>` : '') +
      `</span>`;
    let inner = '';
    if (synonyms.length) {
      inner += `<div class="wd-rel-group"><span class="wd-rel-label">近义词</span>`;
      inner += synonyms.map(_relItem).join('');
      inner += `</div>`;
    }
    if (antonyms.length) {
      inner += `<div class="wd-rel-group"><span class="wd-rel-label">反义词</span>`;
      inner += antonyms.map(_relItem).join('');
      inner += `</div>`;
    }
    relEl.innerHTML =
      `<div class="section-label section-toggle" onclick="toggleSection('wd-relations-body')">` +
        `<span id="wd-relations-body-arrow">▶</span> Relations</div>` +
      `<div id="wd-relations-body" style="display:none">${inner}</div>`;
  } else {
    relEl.innerHTML = '';
  }

  // Shared sections (notes, word analysis, examples)
  renderNotesSection(document.getElementById('wd-notes-section'), word.notes, word.id);
  renderWordAnalysis(document.getElementById('wd-word-analysis-section'), word, word.id);
  renderVocabDetail(document.getElementById('wd-examples-section'), word.examples, word.id);

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
              <button class="wd-menu-item" onclick="openMoveCardPanel(${c.id}, event)">Move to deck…</button>
              <button class="wd-menu-item wd-menu-item-danger"
                      onclick="cardAction(${c.id}, 'reset', ${wordId})">Reset to new</button>
            </div>
            <div class="wd-move-panel" id="wd-move-${c.id}" style="display:none" onclick="event.stopPropagation()">
              <input id="wd-move-inp-${c.id}" class="wd-deck-picker-input" autocomplete="off" placeholder="Deck…"
                onfocus="wdPickerOpen(this)" oninput="wdPickerFilter(this)" onkeydown="wdPickerKey(event, this)">
              <button onclick="applyMoveCard(${c.id}, ${wordId})">Apply</button>
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
  const CAT_LETTER = { listening: 'L', reading: 'R', creating: 'C' };
  const circles = ['listening', 'reading', 'creating'].map(cat => {
    const c = cards.find(c => c.category === cat);
    const letter = CAT_LETTER[cat];
    if (!c) return `<button class="rcat-btn bw-rcat-missing" disabled title="${cat}: —">${letter}</button>`;
    const isSusp = c.state === 'suspended';
    const cls = `rcat-btn ${isSusp ? 'rcat-susp' : 'rcat-active'}`;
    const tip = `${cat}: ${c.state} — click to ${isSusp ? 'activate' : 'suspend'}`;
    return `<button class="${cls}" title="${tip}" onclick="toggleBrowseDotSuspend(event,${c.id},${wordId})">${letter}</button>`;
  }).join('');
  el.innerHTML = `<div class="wd-section-head wd-cards-head">
    <span>Cards <span class="wd-cat-circles">${circles}</span></span>
    <button class="wd-move-all-btn" onclick="openMoveAllCardsPanel(${wordId})">Move all…</button>
  </div>
  <div class="wd-move-all-panel" id="wd-move-all-${wordId}" style="display:none" onclick="event.stopPropagation()">
    <input id="wd-move-all-inp-${wordId}" class="wd-deck-picker-input" autocomplete="off" placeholder="Deck…"
      onfocus="wdPickerOpen(this)" oninput="wdPickerFilter(this)" onkeydown="wdPickerKey(event, this)">
    <button onclick="applyMoveAllCards(${wordId})">Apply</button>
    <button onclick="document.getElementById('wd-move-all-${wordId}').style.display='none';wdPickerClose()">✕</button>
  </div>
  <div class="wd-cards-list">${rows}</div>`;
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
  document.querySelectorAll('.wd-move-panel').forEach(p => p.style.display = 'none');
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

function openMoveAllCardsPanel(wordId) {
  const panel = document.getElementById(`wd-move-all-${wordId}`);
  if (!panel) return;
  const isOpen = panel.style.display !== 'none';
  if (isOpen) { panel.style.display = 'none'; wdPickerClose(); return; }
  panel.style.display = 'flex';
  const inp = document.getElementById(`wd-move-all-inp-${wordId}`);
  inp.value = '';
  inp.focus();
}

async function applyMoveAllCards(wordId) {
  const panel = document.getElementById(`wd-move-all-${wordId}`);
  const inp = document.getElementById(`wd-move-all-inp-${wordId}`);
  const path = inp.value.trim();
  if (!path) return;
  panel.style.display = 'none';
  wdPickerClose();
  try {
    const deck_id = await _wdResolveDeck(path);
    await api('POST', '/api/cards/bulk-move', { word_ids: [wordId], deck_id });
    const word = await api('GET', `/api/word/${wordId}`);
    word.cards = await api('GET', `/api/words/${wordId}/cards`);
    renderWordDetailCards(word.cards || [], wordId);
  } catch (e) {
    showError(`Move failed: ${e.message}`);
  }
}

async function toggleBrowseDotSuspend(e, cardId, wordId) {
  e.stopPropagation();
  const btn = e.currentTarget;
  const isSuspended = btn.classList.contains('rcat-susp');
  const newState = isSuspended ? 'new' : 'suspended';
  try {
    await api('POST', `/api/cards/${cardId}/suspend`);
    // Update in-memory browseWords
    const word = browseWords.find(w => w.id === wordId);
    if (word) {
      const card = word.cards.find(c => c.id === cardId);
      if (card) card.state = newState;
    }
    btn.className = `rcat-btn ${newState === 'suspended' ? 'rcat-susp' : 'rcat-active'}`;
    btn.title = btn.title.replace(/— .+$/, `— ${newState === 'suspended' ? 'click to activate' : 'click to suspend'}`);
  } catch (err) {
    showError('Suspend failed: ' + err.message);
  }
}

function openMoveCardPanel(cardId, e) {
  e.stopPropagation();
  closeAllCardMenus();
  const panel = document.getElementById(`wd-move-${cardId}`);
  document.querySelectorAll('.wd-move-panel').forEach(p => p.style.display = 'none');
  wdPickerClose();
  panel.style.display = 'flex';
  const inp = document.getElementById(`wd-move-inp-${cardId}`);
  inp.value = '';
  inp.focus();
}

async function applyMoveCard(cardId, wordId) {
  const panel = document.getElementById(`wd-move-${cardId}`);
  const inp = document.getElementById(`wd-move-inp-${cardId}`);
  const path = inp.value.trim();
  if (!path) return;
  panel.style.display = 'none';
  wdPickerClose();
  try {
    const deck_id = await _wdResolveDeck(path);
    await api('POST', `/api/cards/${cardId}/move`, { deck_id });
    const word = await api('GET', `/api/word/${wordId}`);
    word.cards = await api('GET', `/api/words/${wordId}/cards`);
    renderWordDetailCards(word.cards || [], wordId);
  } catch (e) {
    showError(`Move failed: ${e.message}`);
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
      _callRenderWordAnalysis();
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

  const compounds = Array.isArray(h.compounds) ? h.compounds : [];
  if (compounds.length) {
    bodyHtml += `<div class="wd-section-head">Compounds</div>
      <div class="wd-section-body"><div class="hd-compounds">` +
      compounds.map(c => {
        const zh = c.compound_zh || c.simplified || String(c);
        const tip = c.meaning ? ` title="${c.meaning}"` : '';
        return `<span class="hd-compound"${tip}>${zh}</span>`;
      }).join('') +
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
  document.getElementById('hedit-compounds').value = Array.isArray(h.compounds)
    ? JSON.stringify(h.compounds, null, 2)
    : (h.compounds || '');
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
        .replace('deepseek-v4-flash', 'DeepSeek V4 Flash')
        .replace('deepseek-v4-pro', 'DeepSeek V4 Pro')
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

const CAT_LABELS = { listening: 'L – Listening', reading: 'R – Reading', creating: 'C – Creating' };

function _setCategoryOrderUI(order) {
  const list = document.getElementById('opt-cat-order-list');
  if (!list) return;
  list.innerHTML = '';
  order.forEach((cat, i) => {
    const li = document.createElement('li');
    li.dataset.cat = cat;
    li.innerHTML = `<span class="cat-order-label">${CAT_LABELS[cat] || cat}</span>
      <span class="cat-order-btns">
        <button type="button" onclick="_moveCatOrder(this,-1)" ${i === 0 ? 'disabled' : ''}>▲</button>
        <button type="button" onclick="_moveCatOrder(this,1)"  ${i === order.length - 1 ? 'disabled' : ''}>▼</button>
      </span>`;
    list.appendChild(li);
  });
}

function _moveCatOrder(btn, dir) {
  const li = btn.closest('li');
  const list = li.parentElement;
  const items = [...list.children];
  const idx = items.indexOf(li);
  const swapIdx = idx + dir;
  if (swapIdx < 0 || swapIdx >= items.length) return;
  if (dir === -1) list.insertBefore(li, items[swapIdx]);
  else list.insertBefore(items[swapIdx], li);
  const newOrder = [...list.children].map(el => el.dataset.cat);
  _setCategoryOrderUI(newOrder);
}

function _getCategoryOrderUI() {
  const list = document.getElementById('opt-cat-order-list');
  if (!list) return 'listening,reading,creating';
  return [...list.children].map(el => el.dataset.cat).join(',');
}

let currentPresetId = null;

function loadPresetFields(preset) {
  currentPresetId = preset.id;
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
  document.getElementById('opt-sibling-sep').value     = preset.sibling_separation ?? 3;
  document.getElementById('opt-sibling-factor').value  = preset.sibling_factor ?? 0.2;

  // Category order
  const order = (preset.category_order || 'listening,reading,creating').split(',').map(s => s.trim());
  _setCategoryOrderUI(order);
  const btnDef = document.getElementById('btn-set-default');
  btnDef.textContent = preset.is_default ? '✓ Already default' : 'Set as default';
  btnDef.disabled = !!preset.is_default;
  const btnDel = document.getElementById('btn-delete-preset');
  btnDel.disabled = allPresets.length <= 1;

  // Category overrides
  _loadCategoryOverrides(preset.category_overrides || {});
}

const _CAT_OVERRIDE_FIELDS = [
  'new_per_day', 'reviews_per_day', 'learning_steps',
  'graduating_interval', 'easy_interval', 'relearning_steps',
];

function _loadCategoryOverrides(overrides) {
  for (const details of document.querySelectorAll('.cat-override-details')) {
    const cat = details.dataset.cat;
    const catOverrides = overrides[cat] || {};
    let hasAny = false;
    for (const input of details.querySelectorAll('input[data-field]')) {
      const val = catOverrides[input.dataset.field];
      input.value = val != null ? val : '';
      if (val != null) hasAny = true;
    }
    if (hasAny) {
      details.setAttribute('data-has-overrides', '');
      details.open = true;
    } else {
      details.removeAttribute('data-has-overrides');
      details.open = false;
    }
  }
}

function _collectCategoryOverrides() {
  const result = {};
  for (const details of document.querySelectorAll('.cat-override-details')) {
    const cat = details.dataset.cat;
    const fields = {};
    for (const input of details.querySelectorAll('input[data-field]')) {
      const raw = input.value.trim();
      if (raw !== '') {
        fields[input.dataset.field] = input.type === 'number' ? Number(raw) : raw;
      }
    }
    if (Object.keys(fields).length > 0) result[cat] = fields;
  }
  return result;
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
    const preset = await api('GET', `/api/decks/${optDeckId}/preset`);
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
    sibling_separation:     parseInt(document.getElementById('opt-sibling-sep').value) || 3,
    sibling_factor:         parseFloat(document.getElementById('opt-sibling-factor').value) || 0.2,
    category_order: _getCategoryOrderUI(),
  };
  // Warn if a story for today already exists — order settings change would cause mismatch
  if (story !== null) {
    const ok = confirm('You have an active story. Changing sort settings will affect card order and may no longer match the story. Continue?');
    if (!ok) return;
  }
  try {
    const [savedPreset] = await Promise.all([
      api('PUT', `/api/decks/${optDeckId}/preset`, fields),
    ]);
    const presetId = currentPresetId;
    // Save category overrides
    const catOverrides = _collectCategoryOverrides();
    const cats = ['listening', 'reading', 'creating'];
    await Promise.all(cats.map(cat => {
      if (catOverrides[cat]) {
        return api('PUT', `/api/presets/${presetId}/categories/${cat}`, catOverrides[cat]);
      } else {
        return api('DELETE', `/api/presets/${presetId}/categories/${cat}`).catch(() => {});
      }
    }));
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
async function startReview(id, cat, name, noStory = false, quick = false) {
  quickMode = quick;
  deckId   = id;
  category = cat;
  deckName = name;
  _sessionReviewedCount = 0;
  _sessionTotalMs = 0;
  _sessionRatedCount = 0;
  _updateAvgTimeBadge();
  _updateReviewRRBadge(id);

  try {
    if (noStory || quick) {
      await _doStartReview(null, 2);
      return;
    }
    const [{ count, has_story, estimated_tokens }, todayCounts] = await Promise.all([
      api('GET', `/api/story/${deckId}/${category}/count`),
      api('GET', `/api/today/${deckId}/${category}`),
    ]);
    const learning = todayCounts?.counts?.learning_future || 0;
    if (has_story || count === 0) {
      await _doStartReview(null, 2);
    } else {
      await openStorySetup(count, { learningCount: learning, estimatedTokens: estimated_tokens });
    }
  } catch (e) {
    showError('Failed to start session: ' + e.message);
    showView('decks');
    return;
  }
}

async function _doStartReview(topic, maxHsk, model, grammarFocus, grammarPct, mode = 'story', chapterIds = null) {
  if (quickMode) {
    setLoading('Loading audio…', true);
    try {
      const todayData = await api('GET', `/api/today/${deckId}/${category}`);
      if (!todayData.card) { showView('done'); return; }
      try {
        await fetch(`/api/preload-session/${deckId}/${category}?quick=true`, { method: 'POST' });
      } catch (_) {}
      showView('review');
      loadCard(todayData.card, todayData.counts);
    } catch (e) {
      showError('Failed to start session: ' + e.message);
      showView('decks');
    }
    return;
  }
  setLoading('Generating story…', true);
  setLoadingStep(10, null, 'Sending request to AI…');
  _startFakeProgress(10, 55, 45000);
  try {
    const storyDeckId = rootDeckId || deckId;
    const storyCategory = rootDeckId ? 'unified' : category;
    _startStoryProgressPoll(storyDeckId, storyCategory);
    const storyUrl = `/api/story/${storyDeckId}/${storyCategory}` + _storyParams(topic, maxHsk, model, grammarFocus, grammarPct, mode, chapterIds);
    let todayData, storyData;
    try {
      [todayData, storyData] = await Promise.all([
        api('GET', `/api/today/${deckId}/${category}`),
        api('GET', storyUrl),
      ]);
    } catch (e) {
      _stopFakeProgress(); _stopStoryProgressPoll();
      _showLoadingError('AI request failed', e.message);
      await new Promise(r => setTimeout(r, 2500));
      showError('Failed to start session: ' + e.message);
      showView('decks');
      return;
    }

    _stopFakeProgress(); _stopStoryProgressPoll();
    setLoadingStep(65, null, 'Story received, processing…');
    story = await _resolveStory(storyData, storyDeckId, storyCategory, topic, maxHsk, grammarFocus, grammarPct, mode);

    if (!todayData.card) {
      showView('done');
      return;
    }

    const sentenceCount = story?.sentences?.length ?? 0;
    setLoadingStep(70, 'Story ready!',
      sentenceCount > 0 ? `Generating audio — 0 / ${sentenceCount} sentences…` : 'Loading audio…');
    await _preloadWithProgress(deckId, category, (done, total) => {
      const pct = 70 + Math.round((done / total) * 28);
      setLoadingStep(pct, null, `Generating audio — ${done} / ${total} sentences…`);
    });

    _showLoadingSuccess('Ready!');
    await new Promise(r => setTimeout(r, 300));
    showView('review');
    loadCard(todayData.card, todayData.counts);
  } catch (e) {
    _stopFakeProgress(); _stopStoryProgressPoll();
    _showLoadingError('Failed to load session', e.message);
    await new Promise(r => setTimeout(r, 2500));
    showError('Failed to start session: ' + e.message);
    showView('decks');
  }
}

function _storyParams(topic, maxHsk, model, grammarFocus, grammarPct, mode, chapterIds) {
  const p = new URLSearchParams();
  if (topic)                              p.set('topic', topic);
  if (maxHsk !== 3)                       p.set('max_hsk', maxHsk);
  if (model && model !== 'deepseek-v4-flash') p.set('model', model);
  if (grammarFocus)                       p.set('grammar_focus', grammarFocus);
  if (grammarFocus && grammarPct !== 75)  p.set('grammar_pct', grammarPct);
  if (mode && mode !== 'story')           p.set('mode', mode);
  if (chapterIds && chapterIds.length)    p.set('chapter_ids', chapterIds.join(','));
  const s = p.toString();
  return s ? '?' + s : '';
}

// ── Start mixed (all-category) review session ────────────────────────────────
async function startReviewMixed(id, name, noStory = false, quick = false) {
  quickMode  = quick;
  rootDeckId = id;
  deckId     = id;
  deckName   = name;
  story      = null;
  _sessionReviewedCount = 0;
  _sessionTotalMs = 0;
  _sessionRatedCount = 0;
  _updateAvgTimeBadge();
  _updateReviewRRBadge(id);
  try {
    const todayData = await api('GET', `/api/today-mixed/${id}`);
    if (!todayData.card) {
      rootDeckId = null;
      showView('done');
      return;
    }
    if (noStory || quick) {
      await _doStartReviewMixed(null, 2, null, null, 50, 'story', true);
      return;
    }
    const c = todayData.counts;
    const total = (c.new || 0) + (c.learning || 0) + (c.review || 0);
    const learning = c.learning_future || 0;
    const firstCat = todayData.card.category;
    const { has_story, estimated_tokens } = await api('GET', `/api/story/${id}/unified/count`);
    if (has_story) {
      await _doStartReviewMixed(null, 2, null, null, 50, 'story');
    } else {
      openStorySetup(total, { isMixed: true, learningCount: learning, estimatedTokens: estimated_tokens });
    }
  } catch (e) {
    showError('Failed to start session: ' + e.message);
    rootDeckId = null;
    showView('decks');
  }
}

async function _doStartReviewMixed(topic, maxHsk, model, grammarFocus, grammarPct, mode = 'story', noStory = false, chapterIds = null) {
  setLoading(noStory ? 'Loading…' : 'Generating stories…', !noStory);
  if (!noStory) {
    setLoadingStep(10, null, 'Sending request to AI…');
    _startFakeProgress(10, 55, 45000);
    _startStoryProgressPoll(rootDeckId, 'unified');
  }
  try {
    const todayData = await api('GET', `/api/today-mixed/${rootDeckId}`);
    if (!todayData.card) {
      _stopFakeProgress(); _stopStoryProgressPoll();
      rootDeckId = null;
      showView('done');
      return;
    }
    category = todayData.card.category;

    if (!noStory) {
      // Load a single unified story covering all categories (1 AI call instead of 3)
      try {
        story = await api('GET', `/api/story/${rootDeckId}/unified` + _storyParams(topic, maxHsk, model, grammarFocus, grammarPct, mode, chapterIds));
      } catch (e) {
        _stopFakeProgress(); _stopStoryProgressPoll();
        _showLoadingError('AI request failed', e.message);
        await new Promise(r => setTimeout(r, 2500));
        showError('Failed to generate story: ' + e.message);
        rootDeckId = null;
        showView('decks');
        return;
      }
      _stopFakeProgress(); _stopStoryProgressPoll();
      fetch(`/api/preload-session/${rootDeckId}/unified`, { method: 'POST' }).catch(() => {});
    }

    if (!noStory) {
      const sentenceCount = story?.sentences?.length ?? 0;
      setLoadingStep(70, 'Story ready!',
        sentenceCount > 0 ? `Generating audio — 0 / ${sentenceCount} sentences…` : 'Loading audio…');
      await _preloadWithProgress(rootDeckId, category, (done, total) => {
        const pct = 70 + Math.round((done / total) * 28);
        setLoadingStep(pct, null, `Generating audio — ${done} / ${total} sentences…`);
      });
      _showLoadingSuccess('Ready!');
      await new Promise(r => setTimeout(r, 300));
    } else {
      try {
        await fetch(`/api/preload-session/${rootDeckId}/${category}`, { method: 'POST' });
      } catch (_) {}
    }
    showView('review');
    loadCard(todayData.card, todayData.counts);
  } catch (e) {
    _stopFakeProgress(); _stopStoryProgressPoll();
    _showLoadingError('Failed to load session', e.message);
    await new Promise(r => setTimeout(r, 2500));
    showError('Failed to start session: ' + e.message);
    rootDeckId = null;
    showView('decks');
  }
}

// ── Start "Unfinished Cards" review session ───────────────────────────────────
async function startReviewUnfinished() {
  deckName = 'Unfinished Cards';
  story    = null;
  _sessionReviewedCount = 0;
  _sessionTotalMs = 0;
  _sessionRatedCount = 0;
  _updateAvgTimeBadge();
  try {
    const counts = await api('GET', '/api/today-unfinished');
    if (!counts.card) {
      showView('done');
      return;
    }
    await _doStartReviewUnfinished(null, 3, null);
  } catch (e) {
    showError('Failed to start session: ' + e.message);
    showView('decks');
  }
}

async function _doStartReviewUnfinished(topic, maxHsk, model, grammarFocus, grammarPct, mode = 'story', chapterIds = null) {
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
    // Load a single unified story for the first card's deck
    try {
      story = await api('GET', `/api/story/${firstDeckId}/unified` + _storyParams(topic, maxHsk, model, grammarFocus, grammarPct, mode, chapterIds));
    } catch (_) {}
    fetch(`/api/preload-session/${firstDeckId}/unified`, { method: 'POST' }).catch(() => {});
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
  renderReviewCatRow(); // clear circles immediately when new card loads

  // In unfinished mode each card may belong to a different deck/category
  if (unfinishedMode) {
    category = c.category;
    deckId   = c.deck_id;
  }

  // Update progress counts
  document.getElementById('cnt-new').textContent = counts.new;
  document.getElementById('cnt-lrn').textContent = counts.learning;
  document.getElementById('cnt-rev').textContent = counts.review;

  // Highlight the active state item
  const stateToItemId = { new: 'cnt-item-new', learning: 'cnt-item-lrn', review: 'cnt-item-rev', relearn: 'cnt-item-lrn' };
  ['cnt-item-new', 'cnt-item-lrn', 'cnt-item-rev'].forEach(id => document.getElementById(id)?.classList.remove('cnt-item-active'));
  const activeStateId = stateToItemId[c?.state];
  if (activeStateId) document.getElementById(activeStateId)?.classList.add('cnt-item-active');

  // Per-category breakdown (mixed/all mode only)
  const byCatEl = document.getElementById('cnt-by-cat');
  if (counts.by_cat && byCatEl) {
    byCatEl.style.display = 'flex';
    const catMap = {r: 'reading', l: 'listening', c: 'creating'};
    for (const [prefix, cat] of Object.entries(catMap)) {
      const cc = counts.by_cat[cat] || {new: 0, learning: 0, review: 0};
      document.getElementById(`cnt-${prefix}-new`).textContent = cc.new;
      document.getElementById(`cnt-${prefix}-lrn`).textContent = cc.learning;
      document.getElementById(`cnt-${prefix}-rev`).textContent = cc.review;
    }
    // Highlight the active category item
    ['cnt-cat-reading', 'cnt-cat-listening', 'cnt-cat-creating'].forEach(id => document.getElementById(id)?.classList.remove('cnt-cat-item-active'));
    const activeCat = c?.category;
    if (activeCat) document.getElementById(`cnt-cat-${activeCat}`)?.classList.add('cnt-cat-item-active');
  } else if (byCatEl) {
    byCatEl.style.display = 'none';
  }

  // Set interval labels on rating buttons (e.g. "1m", "10m", "4d")
  const iv = card.intervals || {};
  [1, 2, 3, 4].forEach(r => {
    document.getElementById(`int-${r}`).textContent = iv[r] || '';
  });

  // Find sentence for this card's word in the story.
  // If no match, leave sentence null — renderSentence() will show just the word.
  sentence = story?.sentences?.find(s => s.word_ids?.includes(card.word_id)) || null;

  // In unfinished mode or mixed mode: story may be from a different deck/category.
  // Async-load the correct story and update the display when it arrives.
  if (!sentence && (unfinishedMode || rootDeckId) && !quickMode) {
    const snap = c;
    const storyDeckId = unfinishedMode ? c.deck_id : rootDeckId;
    // Push the freshly-found `sentence` into the visible UI (reading / cloze / sentence-note).
    const applySentenceToUI = () => {
      _updateStoryInfoRow();
      const isListening  = category === 'listening';
      const isCreating   = category === 'creating';
      const isSentenceNt = card.note_type === 'sentence';
      const isCloze      = isCreating && !isSentenceNt;
      if (!isListening && !isCreating) {
        // Reading: update sentence with full highlighted sentence
        const sentFront = document.getElementById('sentence-front');
        if (sentFront.style.display !== 'none') sentFront.innerHTML = renderSentence();
      } else if (isCloze) {
        // Word bank: sentence just loaded — update hint and rebuild token bank
        const enFront = document.getElementById('sentence-en-front');
        enFront.style.display = 'flex';
        enFront.textContent = sentence.sentence_de || sentence.sentence_fr || '';
        if (document.getElementById('word-bank-wrap').style.display !== 'none') {
          renderWordBankUI();
        }
      } else if (isCreating && isSentenceNt) {
        // Sentence notes: update English prompt
        const inp = document.getElementById('sentence-en-front');
        if (inp.style.display !== 'none') inp.textContent = sentence.sentence_de || sentence.sentence_fr || '';
      }
    };
    fetch(`/api/story/${storyDeckId}/unified`)
      .then(r => r.ok ? r.json() : null)
      .then(s => {
        if (card !== snap) return;
        fetch(`/api/preload-session/${storyDeckId}/unified`, { method: 'POST' }).catch(() => {});
        if (s?.sentences) {
          story    = s;
          sentence = story.sentences.find(s => s.word_ids?.includes(card.word_id)) || null;
        }
        if (sentence) {
          applySentenceToUI();
        } else {
          // Word not in this story (e.g. cross-day card in mixed review): fall back to
          // the word's own most recent sentence, which carries the German translation.
          fetch(`/api/sentence-for-word/${card.word_id}`)
            .then(r => r.ok ? r.json() : null)
            .then(d => {
              if (card !== snap || !d?.sentence) return;
              sentence = d.sentence;
              applySentenceToUI();
            }).catch(() => {});
        }
        // Update listening hint now that sentence is loaded
        if (snap.category === 'listening' && document.getElementById('side-back').style.display === 'none') {
          _initListenHint();
        }
        // Auto-play deferred from loadCard: play now that story is loaded
        if (snap.category === 'listening' && document.getElementById('side-back').style.display === 'none') {
          playSentence();
        }
      }).catch(() => {
        // On fetch error, still play audio (falls back to word_zh)
        if (card === snap && snap.category === 'listening' &&
            document.getElementById('side-back').style.display === 'none') {
          playSentence();
        }
      });
  }

  // Update story info row (sentence counter + topic)
  _updateStoryInfoRow();

  // Update card type badge (note type only — category shown by circles)
  const noteLabel = { vocabulary: 'Word', sentence: 'Sentence', chengyu: '成语', expression: '表达' }[card.note_type] || card.note_type;
  document.getElementById('card-type-badge').textContent = noteLabel;

  // Deck path bar
  const deckPath = document.getElementById('card-deck-path');
  if (card.deck_path) {
    deckPath.textContent = card.deck_path.replace(/_/g, ' ');
    deckPath.style.display = 'block';
  } else {
    deckPath.style.display = 'none';
  }

  // HSK badge — always visible; "HSK -" when unknown (click to AI-fill)
  const hskBadge = document.getElementById('card-hsk-badge');
  hskBadge.textContent = card.hsk_level ? `HSK ${card.hsk_level}` : 'HSK -';
  hskBadge.classList.toggle('hsk-unknown', !card.hsk_level);
  hskBadge.disabled = false;
  hskBadge.style.display = 'inline';

  // Reset pinyin (clear content + hide revealed state)
  const _pr = document.getElementById('pinyin-row');
  _pr.innerHTML = '';
  _pr.dataset.loadedFor = '';
  _pr.classList.remove('pinyin-revealed');

  // Close modals if open
  closeEditCard();
  closeStoryModal();
  document.getElementById('review-card-menu').style.display = 'none';
  const reviewSuspendBtn = document.getElementById('review-suspend-btn');
  if (reviewSuspendBtn) reviewSuspendBtn.textContent = (c.state === 'suspended') ? 'Unsuspend' : 'Suspend';

  // Preload full word details for the back side (local DB — near-instant)
  fetch(`/api/word/${c.word_id}`)
    .then(r => r.ok ? r.json() : null)
    .then(d => {
      if (!d) return;
      wordDetails = d;
      // If back is already showing (user flipped before fetch completed), re-render with full data
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
        // MOST IMPORTANT: Re-render category row with actual card data
        renderReviewCatRow();
      }
    })
    .catch(() => {});

  showFront();
  _startTimer();
  _loadCardCalendar(c.id, c.category);

  // Auto-play audio for the listening category.
  // If sentence is missing and a story fetch is in flight, defer to the fetch callback above.
  if (category === 'listening') {
    if (!sentence && (unfinishedMode || rootDeckId)) {
      // Deferred — fetch callback will call playSentence() once story is loaded
    } else {
      playSentence();
    }
  }
}

// ── Front of card ───────────────────────────────────────────────────────────
function showFront() {
  const isListening  = category === 'listening';
  const isCreating   = category === 'creating';
  const isSentence   = card.note_type === 'sentence';

  document.getElementById('review-cat-row').innerHTML = '';
  document.getElementById('side-front').style.display = 'flex';
  document.getElementById('side-front').style.flexDirection = 'column';
  document.getElementById('side-front').style.gap = '16px';
  document.getElementById('side-back').style.display = 'none';
  const _mascot = document.getElementById('front-mascot');
  if (_mascot) _mascot.style.display = 'flex';
  const _vc = document.getElementById('vocab-content');
  if (_vc) _vc.style.display = 'none';

  // Listening elements
  document.getElementById('front-listen-icon').style.display = isListening ? 'flex' : 'none';
  document.getElementById('back-meta-play-btn').style.display = 'none';
  _listenCount = 0;
  _updateListenCounters();

  // Listening hint slider
  const hintWrap = document.getElementById('listen-hint-wrap');
  if (isListening) {
    hintWrap.style.display = 'flex';
    _initListenHint();
  } else {
    hintWrap.style.display = 'none';
  }

  // Word bank mode: creating category for non-sentence notes (disabled in quick mode)
  const isCloze = isCreating && !isSentence && !quickMode;

  // Reading only: Chinese sentence on front
  const sentFront = document.getElementById('sentence-front');
  sentFront.style.display = !isListening && !isCreating ? 'flex' : 'none';
  if (!isListening && !isCreating) sentFront.innerHTML = renderSentence();

  // Creating: show English hint + appropriate input
  document.getElementById('sentence-en-front').style.display   = isCreating ? 'flex' : 'none';
  document.getElementById('creating-input-wrap').style.display = (isCreating && !isCloze) ? 'flex' : 'none';
  document.getElementById('word-bank-wrap').style.display      = isCloze ? 'flex' : 'none';
  if (isCloze) _initWordBankSlider();

  // Creating: show FR and DE (both always visible); fallback EN if neither exists
  const wordDefHint   = document.getElementById('creating-word-def');
  const wordDefHintWb = document.getElementById('creating-word-def-wb');
  if (isCreating) {
    const parts = [];
    if (card.definition) parts.push(`🇬🇧 ${card.definition}`);
    if (card.definition_fr) parts.push(`🇫🇷 ${card.definition_fr}`);
    if (card.definition_de) parts.push(`🇩🇪 ${card.definition_de}`);
    const defText = parts.join('<br>');
    if (isCloze) {
      wordDefHint.style.display = 'none';
      wordDefHintWb.innerHTML = defText;
      wordDefHintWb.style.display = defText ? 'block' : 'none';
    } else {
      wordDefHintWb.style.display = 'none';
      wordDefHint.innerHTML = defText;
      wordDefHint.style.display = defText ? 'block' : 'none';
    }
  } else {
    wordDefHint.style.display = 'none';
    wordDefHintWb.style.display = 'none';
  }

  if (isCreating) {
    if (isSentence || quickMode) {
      // Sentence notes or quick mode: text input
      const prompt = isSentence
        ? (card.source_sentence || card.definition || '')
        : (card.definition_de || card.definition || '');
      document.getElementById('sentence-en-front').textContent = prompt;
      document.getElementById('creating-input-label').textContent = isSentence ? 'Your translation in Chinese' : 'Write the word in Chinese';
      document.getElementById('creating-input').placeholder = 'Type here…';
      const inp = document.getElementById('creating-input');
      inp.value = '';
      userInput = '';
      setTimeout(() => inp.focus(), 80);
    } else {
      // Word bank mode: German/French translation as hint; word bank renders below
      document.getElementById('sentence-en-front').textContent = sentence?.sentence_de || sentence?.sentence_fr || '';
      userInput = '';
      renderWordBankUI();
    }
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
  _stopTimer();
  const isCreating = category === 'creating';

  // Capture user input before hiding front
  if (isCreating) {
    const isClozeMode = card.note_type !== 'sentence' && !quickMode;
    if (isClozeMode) {
      // Word bank mode: parse number sequence into reconstructed sentence
      const wbRaw = document.getElementById('word-bank-input').value.trim();
      userInput = _parseWordBankInput(wbRaw).join('');
    } else {
      userInput = document.getElementById('creating-input').value.trim();
    }
  }

  document.getElementById('side-front').style.display = 'none';
  document.getElementById('side-back').style.display  = 'flex';
  document.getElementById('side-back').style.flexDirection = 'column';
  document.getElementById('side-back').style.gap = '16px';
  const _mascotBack = document.getElementById('front-mascot');
  if (_mascotBack) _mascotBack.style.display = 'none';
  const _vcBack = document.getElementById('vocab-content');
  if (_vcBack) _vcBack.style.display = 'block';
  document.getElementById('back-meta-play-btn').style.display = isCreating ? 'none' : 'flex';
  _updateListenCounters();

  // Pre-load pinyin in background (shown blurred until p is pressed)
  const _pinyinText = sentence?.sentence_zh || card?.word_zh;
  if (_pinyinText) _loadPinyinRow(_pinyinText);

  const isSentenceNote = card.note_type === 'sentence';

  if (isCreating) {
    // Show answer comparison block; hide normal sentence row
    document.getElementById('creating-answer-section').style.display = 'flex';
    document.getElementById('sentence-row-back').style.display = 'none';
    const matchBar = document.getElementById('answer-match-bar');

    if (!isSentenceNote) {
      // ── Word bank mode: compare reconstructed sentence ────────────────────
      const correctZh = sentence?.sentence_zh || card.word_zh;

      // LCS-based match percentage (handles missing/extra words gracefully)
      const ua = [...userInput], ca = [...correctZh];
      const dp = Array(ua.length + 1).fill(null).map(() => Array(ca.length + 1).fill(0));
      for (let i = 1; i <= ua.length; i++)
        for (let j = 1; j <= ca.length; j++)
          dp[i][j] = ua[i-1] === ca[j-1] ? dp[i-1][j-1] + 1 : Math.max(dp[i-1][j], dp[i][j-1]);
      const lcs = dp[ua.length][ca.length];
      const pct = ca.length > 0 ? Math.round((lcs / ca.length) * 100) : 0;

      // Per-character coloring: target word = blue, others green/red by presence
      const corrSet = new Set(ca);
      const hanzi = /[\u4E00-\u9FFF]/;
      const targetIdx = userInput.indexOf(card.word_zh);
      const targetLen = [...card.word_zh].length;
      let userHtml;
      if (!userInput) {
        userHtml = '<span class="ch-miss">(no answer)</span>';
      } else {
        const chars = [...userInput];
        const tStart = targetIdx >= 0 ? [...userInput.slice(0, targetIdx)].length : -1;
        userHtml = chars.map((ch, i) => {
          if (tStart >= 0 && i >= tStart && i < tStart + targetLen)
            return `<span class="ch-target">${ch}</span>`;
          if (hanzi.test(ch) && corrSet.has(ch)) return `<span class="ch-match">${ch}</span>`;
          return `<span class="ch-miss">${ch}</span>`;
        }).join('');
      }
      document.getElementById('user-answer-text').innerHTML = userHtml;

      if (userInput) {
        const filled = Math.round(pct / 10);
        const bar = '▓'.repeat(filled) + '░'.repeat(10 - filled);
        const color = pct >= 100 ? 'var(--good)' : pct >= 60 ? 'var(--hard)' : 'var(--again)';
        matchBar.innerHTML = `<span class="match-bar" style="color:${color}">${bar} ${pct}%</span>`;
        matchBar.style.display = 'block';
        if (pct >= 100) triggerApplause();
      } else {
        matchBar.style.display = 'none';
      }
      document.getElementById('correct-answer-text').innerHTML = renderSentence();
    } else {
      // ── Sentence notes: full translation comparison (old behaviour) ──────
      const correctZh = card.word_zh;
      const { html: userHtml, pct, bar } = diffAnswer(userInput, correctZh, card.word_zh);
      document.getElementById('user-answer-text').innerHTML = userHtml;
      if (correctZh && userInput) {
        const color = pct >= 80 ? 'var(--good)' : pct >= 50 ? 'var(--hard)' : 'var(--again)';
        matchBar.innerHTML = `<span class="match-bar" style="color:${color}">${bar} ${pct}%</span>`;
        matchBar.style.display = 'block';
        if (pct >= 100) triggerApplause();
      } else {
        matchBar.style.display = 'none';
      }
      document.getElementById('correct-answer-text').innerHTML = renderSentence();
    }
  } else {
    document.getElementById('creating-answer-section').style.display = 'none';
    document.getElementById('sentence-row-back').style.display = 'flex';
    document.getElementById('sentence-back').innerHTML = renderSentence();
  }

  // Sentence notes have no story — hide story button; show German/French translation
  const _sentFrEl = document.getElementById('sentence-fr');
  const _sentDeEl = document.getElementById('sentence-de');
  if (isSentenceNote) {
    _sentFrEl.textContent = '';
    _sentFrEl.style.display = 'none';
    _sentDeEl.textContent = card.definition || '';
    _sentDeEl.style.display = card.definition ? '' : 'none';
  } else {
    _sentFrEl.textContent = sentence?.sentence_fr || '';
    _sentFrEl.style.display = sentence?.sentence_fr ? '' : 'none';
    _sentDeEl.textContent = sentence?.sentence_de || '';
    _sentDeEl.style.display = sentence?.sentence_de ? '' : 'none';
  }

  // Kahneman concept section
  const _conceptEl = document.getElementById('sentence-concept');
  if (!isSentenceNote && sentence?.concept_zh) {
    const chNum = sentence.concept_en ? parseInt(sentence.concept_en.match(/Chapter (\d+)/)?.[1]) : null;
    const renderConcept = (ch) => {
      _conceptEl.innerHTML =
          (ch?.part_zh ? `<span class="concept-part-label">${ch.part_zh}</span>` : '')
        + `<span class="concept-chapter-title">${sentence.concept_zh}</span>`
        + (ch?.concept_zh ? `<span class="concept-chapter-desc">${ch.concept_zh}</span>` : '')
        + (chNum ? `<span class="concept-chapter-hint">点击查看书中原句 ›</span>` : '');
      _conceptEl.style.display = '';
      if (chNum) {
        _conceptEl.classList.add('concept-clickable');
        _conceptEl.onclick = () => openKahnemanExamples(chNum, sentence.concept_zh);
      } else {
        _conceptEl.classList.remove('concept-clickable');
        _conceptEl.onclick = null;
      }
    };
    const cachedCh = chNum && _kahnemanChapters ? _kahnemanChapters.find(c => c.number === chNum) : null;
    renderConcept(cachedCh);
    if (!cachedCh && chNum) {
      _ensureKahnemanChapters().then(() => {
        const ch = _kahnemanChapters?.find(c => c.number === chNum);
        if (ch) renderConcept(ch);
      });
    }
  } else {
    _conceptEl.style.display = 'none';
    _conceptEl.innerHTML = '';
  }
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
  const wordDefDeEl = document.getElementById('word-def-de');
  wordDefDeEl.textContent = card.definition_de ? `🇩🇪 ${card.definition_de}` : '';
  wordDefDeEl.style.display = card.definition_de ? 'block' : 'none';
  const wordDefFrEl = document.getElementById('word-def-fr');
  wordDefFrEl.textContent = card.definition_fr ? `🇫🇷 ${card.definition_fr}` : '';
  wordDefFrEl.style.display = card.definition_fr ? 'block' : 'none';

  const posEl = document.getElementById('word-pos');
  posEl.textContent   = card.pos || '';
  posEl.style.display = card.pos ? 'inline-block' : 'none';

  const regEl = document.getElementById('word-register');
  const regLabels = {
    spoken: '口语', written: '书面语', both: '通用',
    spoken_colloquial: '口语俚语', spoken_neutral: '中性口语',
    neutral: '通用', formal_written: '正式书面语', literary: '文学语体'
  };
  if (card.register) {
    regEl.textContent = regLabels[card.register] || card.register;
    regEl.style.display = 'inline-block';
  } else {
    regEl.style.display = 'none';
  }

  // Re-enable rating buttons
  document.querySelectorAll('.r-btn').forEach(b => b.disabled = false);

  // Show multi-word rating UI when the sentence contains multiple vocab words
  _renderMultiRatingIfNeeded();

  // Populate character breakdown, examples, notes, grammar, and word analysis
  renderNotesSection();
  _callRenderWordAnalysis();
  renderVocabDetail();
  renderReviewCatRow();

  // Auto-play audio on reveal for all categories
  playSentence();
}

// ── Populate vocab detail (chars + examples) ────────────────────────────────
function toggleSection(id) {
  const body = document.getElementById(id);
  const arrow = document.getElementById(id + '-arrow');
  if (body.dataset.peek) {
    // Three-state cycle: peek → open → closed → peek
    const state = body.dataset.state || 'peek';
    if (state === 'peek') {
      body.classList.remove('section-peek');
      body.classList.add('section-open');
      body.style.display = '';
      body.dataset.state = 'open';
      arrow.textContent = '▼';
    } else if (state === 'open') {
      body.classList.remove('section-open');
      body.style.display = 'none';
      body.dataset.state = 'closed';
      arrow.textContent = '▶';
    } else {
      body.classList.add('section-peek');
      body.classList.remove('section-open');
      body.style.display = '';
      body.dataset.state = 'peek';
      arrow.textContent = '▷';
    }
  } else {
    const open = body.style.display !== 'none';
    body.style.display = open ? 'none' : 'block';
    arrow.textContent = open ? '▶' : '▼';
  }
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

// ── Category suspension row on review card back ──────────────────────────────
function renderReviewCatRow() {
  const el = document.getElementById('review-cat-row');
  if (!el) return;
  const cards = wordDetails?.cards;
  if (!cards?.length) { el.innerHTML = ''; return; }
  const CATS = ['reading', 'listening', 'creating'];
  const LABELS = { reading: 'Reading', listening: 'Listening', creating: 'Creating' };
  const html = CATS.map(cat => {
    const c = cards.find(c => c.category === cat && !c.deleted_at);
    if (!c) return '';
    const isCurrent = cat === card?.category;
    const isSusp = c.state === 'suspended';
    const cls = ['rcat-btn', isSusp ? 'rcat-susp' : 'rcat-active', isCurrent ? 'rcat-current' : ''].join(' ').trim();
    const title = isSusp ? `Activate ${LABELS[cat]}` : `Suspend ${LABELS[cat]}`;
    const letter = LABELS[cat][0];
    return `<button class="${cls}" onclick="toggleReviewCat(${c.id})" type="button" title="${title}">${letter}</button>`;
  }).join('');
  el.innerHTML = html;
}

function _toggleSuspendCat(category) {
  const cards = wordDetails?.cards || [];
  const c = cards.find(c => c.category === category && !c.deleted_at);
  if (c) toggleReviewCat(c.id);
}

async function toggleReviewCat(cardId) {
  try {
    await api('POST', `/api/cards/${cardId}/suspend`);
    const updated = await api('GET', `/api/words/${card.word_id}/cards`);
    if (wordDetails) wordDetails.cards = updated;
    renderReviewCatRow();
  } catch (e) {
    showError('Failed: ' + e.message);
  }
}

function _getActiveWordId() {
  return _currentWordId ?? wordDetails?.id ?? card?.word_id ?? null;
}

// ── Regen preview modal ──────────────────────────────────────────────────────
let _regenState = null; // { wordId, fields, containerId }

function regenAllFields(wordId) {
  const allFields = ['definition', 'definition_zh', 'definition_de', 'definition_fr', 'pos',
                     'notes', 'examples', 'etymology', 'compounds'];
  regenFields(wordId, allFields, 'wd-all');
}

function regenAllFieldsFromReview() {
  const wordId = _getActiveWordId();
  if (!wordId) return showError('No active word');
  const allFields = ['definition', 'definition_zh', 'definition_de', 'definition_fr', 'pos',
                     'notes', 'examples', 'etymology', 'compounds'];
  regenFields(wordId, allFields, 'review-regen-all');
}

async function regenFields(wordId, fields, containerId) {
  const el = document.getElementById(containerId);
  const btn = el?.querySelector('.field-regen-btn');
  if (btn) { btn.disabled = true; btn.textContent = '…'; }
  try {
    const preview = await api('POST', `/api/word/${wordId}/regenerate-fields`, { fields, preview: true });
    _regenState = { wordId, fields, containerId };
    _showRegenPreviewModal(preview);
  } catch (e) {
    showError('Regeneration failed: ' + e.message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '↺'; }
  }
}

function _showRegenPreviewModal(previewData) {
  _closeRegenPreviewModal();
  const { fields, result } = previewData;
  const overlay = document.createElement('div');
  overlay.id = 'regen-preview-overlay';
  overlay.className = 'regen-preview-overlay';
  overlay.onclick = (e) => { if (e.target === overlay) _closeRegenPreviewModal(); };

  const wantEtym = fields.includes('etymology');
  const wantComp = fields.includes('compounds');

  let bodyHtml = '';

  const DEF_FIELDS = ['definition', 'definition_zh', 'definition_de', 'definition_fr', 'pos'];
  if (fields.some(f => DEF_FIELDS.includes(f))) {
    const esc = s => (s || '').replace(/"/g, '&quot;');
    let defHtml = '';
    if (fields.includes('pos'))
      defHtml += `<div class="regen-def-row"><label>POS</label><input type="text" id="regen-pos" value="${esc(result.pos)}" placeholder="n. / v. / adj."></div>`;
    if (fields.includes('definition'))
      defHtml += `<div class="regen-def-row"><label>EN</label><input type="text" id="regen-def" value="${esc(result.definition)}" placeholder="English definition"></div>`;
    if (fields.includes('definition_zh'))
      defHtml += `<div class="regen-def-row"><label>ZH</label><input type="text" id="regen-def-zh" value="${esc(result.definition_zh)}" placeholder="中文释义"></div>`;
    if (fields.includes('definition_de'))
      defHtml += `<div class="regen-def-row"><label>DE</label><input type="text" id="regen-def-de" value="${esc(result.definition_de)}" placeholder="Deutsche Definition"></div>`;
    if (fields.includes('definition_fr'))
      defHtml += `<div class="regen-def-row"><label>FR</label><input type="text" id="regen-def-fr" value="${esc(result.definition_fr)}" placeholder="Définition française"></div>`;
    bodyHtml += `<div><div class="regen-section-label">Definitions &amp; Part of Speech</div><div class="regen-def-group">${defHtml}</div></div>`;
  }

  if (fields.includes('notes')) {
    const text = (result.notes || '').replace(/"/g, '&quot;');
    bodyHtml += `<div>
      <div class="regen-section-label">Notes</div>
      <textarea id="regen-notes-text" class="regen-notes-textarea">${result.notes || ''}</textarea>
    </div>`;
  }

  if (fields.includes('examples')) {
    const exRows = (result.examples || []).map((ex, i) => _regenExampleRowHtml(ex, i)).join('');
    bodyHtml += `<div>
      <div class="regen-section-label">Examples</div>
      <div class="regen-example-labels">
        <span>ZH</span><span>Pinyin</span><span>English</span><span>DE</span><span></span>
      </div>
      <div id="regen-examples-list">${exRows}</div>
      <button class="regen-add-btn" onclick="_addRegenExample()">+ Add example</button>
    </div>`;
  }

  const wantMeanings = fields.includes('other_meanings');
  if (wantEtym || wantComp || wantMeanings) {
    const chars = result.characters || [];
    const charSections = chars.map(c => {
      const charEsc = (c.char || '').replace(/'/g, "\\'");
      let inner = `<div class="regen-char-header">${c.char || ''}</div>`;
      if (wantMeanings) {
        const meanVal = Array.isArray(c.other_meanings) ? c.other_meanings.join(', ') : (c.other_meanings || '');
        inner += `<input type="text" class="regen-meanings-input" data-field="other_meanings" placeholder="Bedeutungen (kommagetrennt)" value="${meanVal.replace(/"/g, '&quot;')}">`;
      }
      if (wantEtym) {
        inner += `<textarea class="regen-etym-textarea" data-field="etymology" placeholder="Etymology…">${c.etymology || ''}</textarea>`;
      }
      if (wantComp) {
        const cpRows = (c.compounds || []).map(cp => _regenCompoundRowHtml(cp)).join('');
        inner += `<div class="regen-compound-labels">
          <span>Simplified</span><span>Pinyin</span><span>Meaning</span><span></span>
        </div>
        <div class="regen-compounds-list">${cpRows}</div>
        <button class="regen-add-btn" onclick="_addRegenCompound(this)">+ Add compound</button>`;
      }
      return `<div class="regen-char-group" data-char-id="${c.char_id || ''}" data-char="${charEsc}">${inner}</div>`;
    }).join('');
    bodyHtml += `<div>
      <div class="regen-section-label">Characters</div>
      <div id="regen-chars-list">${charSections}</div>
    </div>`;
  }

  overlay.innerHTML = `<div class="regen-preview-modal" onclick="event.stopPropagation()">
    <div class="regen-preview-header">
      <span>AI Preview</span>
      <button onclick="_closeRegenPreviewModal()">×</button>
    </div>
    <div class="regen-preview-body">${bodyHtml}</div>
    <div id="regen-modal-error" style="display:none;color:#b91c1c;background:#fef2f2;border:1px solid #fecaca;border-radius:6px;padding:8px 12px;margin:8px 16px;font-size:13px"></div>
    <div class="regen-preview-footer">
      <button class="regen-btn regen-btn-regenerate" id="regen-btn-regen" onclick="_rerunRegen()">↺ Regenerate</button>
      <button class="regen-btn regen-btn-reject" onclick="_closeRegenPreviewModal()">✗ Reject</button>
      <button class="regen-btn regen-btn-apply" id="regen-btn-apply" onclick="_applyRegenResult()">✓ Apply</button>
    </div>
  </div>`;
  document.body.appendChild(overlay);
}

function _regenExampleRowHtml(ex, idx) {
  const esc = s => (s || '').replace(/"/g, '&quot;');
  return `<div class="regen-example-row">
    <input type="text" data-field="zh" value="${esc(ex.zh)}" placeholder="中文">
    <input type="text" data-field="pinyin" value="${esc(ex.pinyin)}" placeholder="pīnyīn">
    <input type="text" data-field="english" value="${esc(ex.english)}" placeholder="English">
    <input type="text" data-field="de" value="${esc(ex.de)}" placeholder="Deutsch">
    <button class="regen-row-del" onclick="this.closest('.regen-example-row').remove()">−</button>
  </div>`;
}

function _regenCompoundRowHtml(cp) {
  const esc = s => (s || '').replace(/"/g, '&quot;');
  return `<div class="regen-compound-row">
    <input type="text" data-field="simplified" value="${esc(cp.simplified || cp.compound_zh)}" placeholder="词">
    <input type="text" data-field="pinyin" value="${esc(cp.pinyin)}" placeholder="pīnyīn">
    <input type="text" data-field="meaning" value="${esc(cp.meaning)}" placeholder="Bedeutung">
    <button class="regen-row-del" onclick="this.closest('.regen-compound-row').remove()">−</button>
  </div>`;
}

function _addRegenExample() {
  const list = document.getElementById('regen-examples-list');
  if (list) list.insertAdjacentHTML('beforeend', _regenExampleRowHtml({}, list.children.length));
}

function _addRegenCompound(btn) {
  const list = btn.previousElementSibling;
  if (list) list.insertAdjacentHTML('beforeend', _regenCompoundRowHtml({}));
}

function _closeRegenPreviewModal() {
  document.getElementById('regen-preview-overlay')?.remove();
}

function _getRegenResultFromModal() {
  const result = {};
  const fields = _regenState?.fields || [];

  const DEF_FIELDS = ['definition', 'definition_zh', 'definition_de', 'definition_fr', 'pos'];
  if (fields.some(f => DEF_FIELDS.includes(f))) {
    if (fields.includes('pos'))           result.pos           = document.getElementById('regen-pos')?.value?.trim()    || '';
    if (fields.includes('definition'))    result.definition    = document.getElementById('regen-def')?.value?.trim()    || '';
    if (fields.includes('definition_zh')) result.definition_zh = document.getElementById('regen-def-zh')?.value?.trim() || '';
    if (fields.includes('definition_de')) result.definition_de = document.getElementById('regen-def-de')?.value?.trim() || '';
    if (fields.includes('definition_fr')) result.definition_fr = document.getElementById('regen-def-fr')?.value?.trim() || '';
  }

  if (fields.includes('notes')) {
    result.notes = document.getElementById('regen-notes-text')?.value?.trim() || '';
  }

  if (fields.includes('examples')) {
    const rows = document.querySelectorAll('#regen-examples-list .regen-example-row');
    result.examples = Array.from(rows).map(row => ({
      zh:      row.querySelector('[data-field="zh"]')?.value?.trim() || '',
      pinyin:  row.querySelector('[data-field="pinyin"]')?.value?.trim() || '',
      english: row.querySelector('[data-field="english"]')?.value?.trim() || '',
      de:      row.querySelector('[data-field="de"]')?.value?.trim() || '',
    })).filter(ex => ex.zh);
  }

  if (fields.includes('etymology') || fields.includes('compounds')) {
    const charGroups = document.querySelectorAll('#regen-chars-list .regen-char-group');
    result.characters = Array.from(charGroups).map(group => {
      const rawId = parseInt(group.dataset.charId);
      const charResult = {
        char:    group.dataset.char,
        char_id: isNaN(rawId) ? null : rawId,
      };
      if (fields.includes('other_meanings')) {
        const raw = group.querySelector('[data-field="other_meanings"]')?.value?.trim() || '';
        charResult.other_meanings = raw ? raw.split(',').map(s => s.trim()).filter(Boolean) : [];
      }
      if (fields.includes('etymology')) {
        charResult.etymology = group.querySelector('[data-field="etymology"]')?.value?.trim() || '';
      }
      if (fields.includes('compounds')) {
        const cpRows = group.querySelectorAll('.regen-compound-row');
        charResult.compounds = Array.from(cpRows).map(row => ({
          simplified: row.querySelector('[data-field="simplified"]')?.value?.trim() || '',
          pinyin:     row.querySelector('[data-field="pinyin"]')?.value?.trim() || '',
          meaning:    row.querySelector('[data-field="meaning"]')?.value?.trim() || '',
        })).filter(c => c.simplified);
      }
      return charResult;
    });
  }

  return result;
}

async function _applyRegenResult() {
  const { wordId, fields, containerId } = _regenState || {};
  if (!wordId) return;
  const applyBtn = document.getElementById('regen-btn-apply');
  const regenBtn = document.getElementById('regen-btn-regen');
  if (applyBtn) applyBtn.disabled = true;
  if (regenBtn) regenBtn.disabled = true;
  try {
    const result = _getRegenResultFromModal();
    const updated = await api('POST', `/api/word/${wordId}/apply-regen-result`, { fields, result });
    _closeRegenPreviewModal();
    if (wordDetails?.id === wordId) wordDetails = updated;
    const DEF_FIELDS = ['definition', 'definition_zh', 'definition_de', 'definition_fr', 'pos'];
    const isDefRegen = fields.some(f => DEF_FIELDS.includes(f));
    console.log('[apply] wordId=', wordId, '_currentWordId=', _currentWordId, 'fields=', fields, 'containerId=', containerId, 'isDefRegen=', isDefRegen);
    if (containerId === 'review-regen-all') {
      // Re-render all review side-panel sections
      renderNotesSection(null, updated.notes, wordId);
      renderWordAnalysis(null, updated, wordId);
      renderVocabDetail(null, updated.examples, wordId);
    } else if (containerId === 'wd-all' && _currentWordId === wordId) {
      updated.cards = wordDetails?.cards || [];
      renderWordDetail(updated);
    } else if (isDefRegen && _currentWordId === wordId) {
      // Definition/POS regen: full re-render is safe (header is always visible)
      updated.cards = wordDetails?.cards || [];
      renderWordDetail(updated);
    } else {
      // Section regen (notes/examples/etymology/compounds): targeted re-render to keep section open
      const target = document.getElementById(containerId);
      console.log('[apply] target=', target, 'containerId=', containerId);
      if (target) {
        if (isDefRegen) {
          const posEl = document.getElementById('wd-pos');
          if (posEl) { posEl.textContent = updated.pos || '—'; posEl.style.display = 'inline-block'; }
          const defEl = document.getElementById('wd-def');
          if (defEl) defEl.textContent = updated.definition || '';
          const defZhEl = document.getElementById('wd-def-zh');
          if (defZhEl) { defZhEl.textContent = updated.definition_zh || ''; defZhEl.style.display = updated.definition_zh ? 'block' : 'none'; }
          const defDeEl = document.getElementById('wd-def-de');
          if (defDeEl) { defDeEl.textContent = updated.definition_de ? `🇩🇪 ${updated.definition_de}` : ''; defDeEl.style.display = updated.definition_de ? 'block' : 'none'; }
          const defFrEl = document.getElementById('wd-def-fr');
          if (defFrEl) { defFrEl.textContent = updated.definition_fr ? `🇫🇷 ${updated.definition_fr}` : ''; defFrEl.style.display = updated.definition_fr ? 'block' : 'none'; }
        } else if (fields.includes('notes'))    renderNotesSection(target, updated.notes, wordId);
        else if (fields.includes('examples'))   renderVocabDetail(target, updated.examples, wordId);
        else                                    renderWordAnalysis(target, updated, wordId);
        const body  = document.getElementById(containerId + '-body');
        const arrow = document.getElementById(containerId + '-body-arrow');
        console.log('[apply] body=', body, 'arrow=', arrow);
        if (body)  body.style.display = 'block';
        if (arrow) arrow.textContent = '▼';
      }
    }
  } catch (e) {
    const modalErr = document.getElementById('regen-modal-error');
    if (modalErr) { modalErr.textContent = 'Apply failed: ' + e.message; modalErr.style.display = 'block'; }
    else showError('Apply failed: ' + e.message);
    if (applyBtn) applyBtn.disabled = false;
    if (regenBtn) regenBtn.disabled = false;
  }
}

async function _rerunRegen() {
  const { wordId, fields, containerId } = _regenState || {};
  if (!wordId) return;
  const regenBtn = document.getElementById('regen-btn-regen');
  const applyBtn = document.getElementById('regen-btn-apply');
  if (regenBtn) { regenBtn.disabled = true; regenBtn.textContent = '…'; }
  if (applyBtn) applyBtn.disabled = true;
  try {
    const preview = await api('POST', `/api/word/${wordId}/regenerate-fields`, { fields, preview: true });
    _regenState = { wordId, fields, containerId };
    _showRegenPreviewModal(preview);
  } catch (e) {
    showError('Regeneration failed: ' + e.message);
    if (regenBtn) { regenBtn.disabled = false; regenBtn.textContent = '↺ Regenerate'; }
    if (applyBtn) applyBtn.disabled = false;
  }
}

function renderVocabDetail(container, examples, wordId) {
  const el = container ?? document.getElementById('examples-section');
  const items = examples ?? wordDetails?.examples ?? [];
  const wid = wordId ?? _getActiveWordId();
  const bodyId = el.id + '-body';
  const regenBtn = wid ? `<button class="field-regen-btn" onclick="event.stopPropagation();regenFields(${wid},['examples'],'${el.id}')" title="Regenerate examples">↺</button>` : '';
  const html = items.length > 0
    ? items.map(ex => {
        let h = `<div class="example-item">`;
        h += `<div class="example-zh">${ex.example_zh || ''}</div>`;
        if (ex.example_pinyin) h += `<div class="example-pin">${ex.example_pinyin}</div>`;
        if (ex.example_de)     h += `<div class="example-de">${ex.example_de}</div>`;
        h += `</div>`;
        return h;
      }).join('')
    : `<div class="section-empty">—</div>`;
  el.innerHTML =
    `<div class="section-label section-label-row section-toggle" onclick="toggleSection('${bodyId}')">` +
      `<span><span id="${bodyId}-arrow">▶</span> Examples</span>${regenBtn}</div>` +
    `<div id="${bodyId}" style="display:none">${html}</div>`;
}

function renderNotesSection(container, notes, wordId) {
  const el = container ?? document.getElementById('notes-section');
  const text = notes ?? card?.notes;
  const wid = wordId ?? _getActiveWordId();
  const bodyId = el.id + '-body';
  const regenBtn = wid ? `<button class="field-regen-btn" onclick="event.stopPropagation();regenFields(${wid},['notes'],'${el.id}')" title="Regenerate notes">↺</button>` : '';
  const bodyContent = text
    ? `<div class="notes-body">${renderMarkdown(text)}</div>`
    : `<div class="section-empty">—</div>`;
  el.innerHTML =
    `<div class="section-label section-label-row section-toggle" onclick="toggleSection('${bodyId}')">` +
      `<span><span id="${bodyId}-arrow">▷</span> Notes</span>${regenBtn}</div>` +
    `<div id="${bodyId}" class="section-peek" data-peek="1" data-state="peek">${bodyContent}</div>`;
  el.style.display = '';
}

function renderWordAnalysis(container, wordData, wordId) {
  const el = container ?? document.getElementById('word-analysis-section');
  const wd = wordData ?? wordDetails;
  const nt = wd?.note_type ?? card?.note_type;
  const isMultiWord = nt === 'sentence' || nt === 'chengyu' || nt === 'expression';
  const prefix = el.id;
  const bodyId = prefix + '-body';

  // Build word groups for all note types
  let wordGroups = [];
  if (isMultiWord) {
    wordGroups = wd?.components || [];
    // chengyu/sentence with no components: fall back to characters linked directly to the entry
    if (wordGroups.length === 0 && wd?.characters?.length > 0) {
      wordGroups = [{
        id: wd.id,
        word_zh:       wd.word_zh    || card?.word_zh,
        pinyin:        wd.pinyin     || card?.pinyin,
        hsk_level:     wd.hsk_level  || card?.hsk_level,
        definition:    wd.definition || card?.definition,
        measure_words: wd.measure_words || [],
        characters:    wd.characters || [],
      }];
    }
  } else if (wd?.components?.length > 0) {
    // New-format vocabulary: word_analyses stored as components (each with own characters)
    wordGroups = wd.components;
  } else if (wd) {
    // Old-format vocabulary: characters linked directly to the entry
    wordGroups = [{
      id: wd.id,
      word_zh:       wd.word_zh    || card?.word_zh,
      pinyin:        wd.pinyin     || card?.pinyin,
      hsk_level:     wd.hsk_level  || card?.hsk_level,
      definition:    wd.definition || card?.definition,
      measure_words: wd.measure_words || [],
      characters:    wd.characters || [],
    }];
  }

  const wid = wordId ?? _getActiveWordId();
  const regenBtnWA = wid ? `<button class="field-regen-btn" onclick="event.stopPropagation();regenFields(${wid},['etymology','compounds','other_meanings'],'${el.id}')" title="Regenerate etymology, compounds &amp; meanings">↺</button>` : '';

  if (wordGroups.length === 0) {
    el.innerHTML =
      `<div class="section-label section-label-row section-toggle" onclick="toggleSection('${bodyId}')">` +
        `<span><span id="${bodyId}-arrow">▼</span> Word Analysis</span>${regenBtnWA}</div>` +
      `<div id="${bodyId}" class="wa-list section-open" data-peek="1" data-state="open"><div class="section-empty">—</div></div>`;
    return;
  }

  const wordCards = wordGroups.map((comp, idx) => {
    const wid = comp.id;
    const charBodyId = `${prefix}-wa-${idx}`;

    // Header: word (clickable to Browse) + pinyin + HSK + definition
    const zhSpan = wid
      ? `<span class="wa-word-zh wa-browse-link" onclick="openWordDetail(${wid})">${comp.word_zh || ''}</span>`
      : `<span class="wa-word-zh">${comp.word_zh || ''}</span>`;
    let header = zhSpan;
    if (comp.pinyin)     header += `<span class="wa-word-pin">${comp.pinyin}</span>`;
    if (comp.hsk_level)  header += `<span class="wa-hsk-badge">HSK ${comp.hsk_level}</span>`;
    const compDef = comp.definition || (() => {
      try { const m = JSON.parse(comp.characters?.[0]?.other_meanings || '[]'); return m.slice(0, 2).join('; '); }
      catch { return ''; }
    })();
    if (compDef) header += `<span class="wa-word-def">${compDef}</span>`;

    // Measure words row
    let mwHtml = '';
    const mw = comp.measure_words || [];
    if (mw.length) {
      const items = mw.map(m =>
        `<span class="wa-mw-item">${m.measure_zh}` +
        (m.pinyin ? ` <span class="wa-mw-pin">${m.pinyin}</span>` : '') +
        (m.meaning ? ` <span class="wa-mw-meaning">${m.meaning}</span>` : '') +
        `</span>`
      ).join('');
      mwHtml = `<div class="wa-measure-row"><span class="wa-rel-label">量词</span>${items}</div>`;
    }

    // Characters body (collapsed sub-toggle)
    const chars = comp.characters || [];
    let charBody = '';
    if (chars.length) {
      charBody = chars.map(c => {
        const charEsc = (c.char || '').replace(/'/g, "\\'");
        const pinEsc  = (c.pinyin || '').replace(/'/g, "\\'");
        let right = '';
        if (c.pinyin) right += `<span class="wa-char-pin">${c.pinyin}</span>`;
        const charMeaning = c.meaning_in_context || (() => {
          try { const m = JSON.parse(c.other_meanings || '[]'); return m.slice(0, 2).join('; '); }
          catch { return ''; }
        })();
        if (charMeaning) right += `<span class="wa-char-ctx">${charMeaning}</span>`;
        if (c.compounds?.length) {
          const cps = c.compounds.map(cp => {
            const highlightedZh = (cp.compound_zh || '').split('').map(ch =>
              ch === c.char ? `<span class="wa-compound-hl">${ch}</span>` : ch
            ).join('');
            const zhEsc = (cp.compound_zh || '').replace(/'/g, "\\'");
            const pinEsc = (cp.pinyin || '').replace(/'/g, "\\'");
            const meanEsc = (cp.meaning || '').replace(/'/g, "\\'");
            return `<span class="wa-compound-item wa-compound-clickable" onclick="event.stopPropagation();openQuickAddMenu(event,'${zhEsc}','${pinEsc}','${meanEsc}')">${highlightedZh}` +
              (cp.pinyin ? ` <span class="wa-compound-pin">${cp.pinyin}</span>` : '') +
              (cp.meaning ? ` <span class="wa-compound-meaning">${cp.meaning}</span>` : '') +
              `</span>`;
          }).join('');
          right += `<div class="wa-compounds">${cps}</div>`;
        }
        if (c.etymology) right += `<div class="wa-char-etym">${c.etymology}</div>`;
        const tradHtml = (c.traditional && c.traditional !== c.char)
          ? `<span class="wa-char-trad">${c.traditional}</span>`
          : '';
        return `<div class="wa-char-row" onclick="openHanziRegenModal(${c.char_id},'${charEsc}','${pinEsc}',true)">` +
          `<span class="wa-char-zh-col"><span class="wa-char-zh">${c.char}</span>${tradHtml}</span>` +
          `<div class="wa-char-right">${right}</div>` +
          `</div>`;
      }).join('');
    }

    const hasChars = charBody.length > 0;
    return `<div class="wa-word-card">` +
      `<div class="wa-word-header">${header}</div>` +
      (mwHtml ? `<div class="wa-word-extra">${mwHtml}</div>` : '') +
      (hasChars ? `<div class="wa-chars-list">${charBody}</div>` : '') +
      `</div>`;
  }).join('');

  el.innerHTML =
    `<div class="section-label section-label-row section-toggle" onclick="toggleSection('${bodyId}')">` +
      `<span><span id="${bodyId}-arrow">▼</span> Word Analysis</span>${regenBtnWA}</div>` +
    `<div id="${bodyId}" class="wa-list section-open" data-peek="1" data-state="open">${wordCards}</div>`;
}

function _callRenderWordAnalysis() {
  renderWordAnalysis();
}

// ── Quick-add compound word to tomorrow's Daily deck ────────────────────────

let _quickAddMenu = null;

function openQuickAddMenu(event, wordZh, pinyin, meaning) {
  closeQuickAddMenu();

  const tomorrow = new Date();
  tomorrow.setDate(tomorrow.getDate() + 1);
  const tomorrowStr = String(tomorrow.getMonth() + 1).padStart(2, '0') + '-' + String(tomorrow.getDate()).padStart(2, '0');

  const menu = document.createElement('div');
  menu.id = 'quick-add-menu';
  menu.className = 'quick-add-menu';
  menu.innerHTML =
    `<div class="qa-word">${wordZh}` +
      (pinyin ? ` <span class="qa-pin">${pinyin}</span>` : '') +
    `</div>` +
    (meaning ? `<div class="qa-meaning">${meaning}</div>` : '') +
    `<div class="qa-deck-label">daily::${tomorrowStr}</div>` +
    `<button class="qa-add-btn" onclick="doQuickAdd('${wordZh.replace(/'/g,"\\'")}','${pinyin.replace(/'/g,"\\'")}','${meaning.replace(/'/g,"\\'")}',this)">+ Add to Daily deck</button>`;

  document.body.appendChild(menu);
  _quickAddMenu = menu;

  // Position near the click
  const x = Math.min(event.clientX, window.innerWidth - 220);
  const y = event.clientY + 8;
  menu.style.left = x + 'px';
  menu.style.top = y + 'px';

  // Close on outside click
  setTimeout(() => document.addEventListener('click', closeQuickAddMenu, { once: true }), 0);
}

function closeQuickAddMenu() {
  if (_quickAddMenu) {
    _quickAddMenu.remove();
    _quickAddMenu = null;
  }
}

async function doQuickAdd(wordZh, pinyin, meaning, btn) {
  btn.disabled = true;
  btn.textContent = '…';
  try {
    const result = await api('POST', '/api/quick-add-word', { word_zh: wordZh, pinyin, meaning });
    closeQuickAddMenu();
    const msgs = {
      created:        `✓ "${wordZh}" added to ${result.deck_path}`,
      added_to_deck:  `✓ "${wordZh}" added to ${result.deck_path}`,
      already_in_deck:`"${wordZh}" is already in ${result.deck_path}`,
    };
    showQuickAddBanner(msgs[result.status] || `✓ Done`, result.status === 'already_in_deck');
  } catch (e) {
    btn.disabled = false;
    btn.textContent = '+ Add to Daily deck';
    showError(e.message || 'Failed to add word');
  }
}

function showQuickAddBanner(msg, isInfo) {
  let el = document.getElementById('quick-add-banner');
  if (!el) {
    el = document.createElement('div');
    el.id = 'quick-add-banner';
    el.className = 'quick-add-banner';
    document.body.appendChild(el);
  }
  el.textContent = msg;
  el.className = 'quick-add-banner' + (isInfo ? ' qa-info' : ' qa-success');
  el.style.display = 'block';
  clearTimeout(el._hideTimer);
  el._hideTimer = setTimeout(() => { el.style.display = 'none'; }, 3500);
}

// ── Listening hint slider (HSK-aware) ───────────────────────────────────────
let _hskLevels = null; // {word: hsk_level_number} — loaded once from static file

async function _loadHskLevels() {
  if (_hskLevels) return;
  try {
    const r = await fetch('/static/hsk_levels.json');
    _hskLevels = await r.json();
  } catch {
    _hskLevels = {};
  }
}

// Returns the HSK level of a token (tries compound first, then max of chars).
// Returns null if the token has no CJK chars or is completely unknown.
function _hskLevelOf(token) {
  if (_hskLevels[token]) return _hskLevels[token];
  const isCjk = ch => ch >= '一' && ch <= '鿿';
  let max = 0;
  for (const ch of token) {
    if (!isCjk(ch)) continue;
    const l = _hskLevels[ch];
    if (!l) return null; // unknown character → treat whole token as unknown
    max = Math.max(max, l);
  }
  return max > 0 ? max : null;
}

function _getTargetPositions(zh) {
  const targetWords = [];
  if (card?.word_zh) targetWords.push(card.word_zh);
  if (sentence?.words) {
    for (const w of sentence.words) {
      if (w.word_zh && !targetWords.includes(w.word_zh)) targetWords.push(w.word_zh);
    }
  }
  const positions = new Set();
  const markWord = (tw) => {
    // Separable words like "由...组成" — search each part independently
    const parts = tw.includes('...') ? tw.split('...').filter(p => p.length > 0) : [tw];
    for (const part of parts) {
      let start = 0;
      while (true) {
        const idx = zh.indexOf(part, start);
        if (idx === -1) break;
        for (let k = 0; k < part.length; k++) positions.add(idx + k);
        start = idx + part.length;
      }
    }
  };
  for (const tw of targetWords) markWord(tw);
  return positions;
}

function _hintSavedDefault() {
  return parseInt(localStorage.getItem('listenHintDefault') ?? '3', 10);
}

function _updateHintStar(currentVal) {
  const btn = document.getElementById('hint-save-btn');
  if (!btn) return;
  const isSaved = currentVal === _hintSavedDefault();
  btn.textContent = isSaved ? '★' : '☆';
  btn.classList.toggle('saved', isSaved);
}

function _initListenHint() {
  const slider = document.getElementById('listen-hint-slider');
  const saved = _hintSavedDefault();
  slider.value = saved;
  document.getElementById('listen-hint-pct').textContent = saved === 0 ? 'All' : `HSK ${saved}+`;
  _updateHintStar(saved);
  _loadHskLevels().then(() => _renderListenHint(saved));
}

function saveListenHintDefault() {
  const val = parseInt(document.getElementById('listen-hint-slider').value, 10);
  localStorage.setItem('listenHintDefault', val);
  _updateHintStar(val);
}

// ── Word bank tile count slider ───────────────────────────────────────────────
function _wordBankTileDefault() {
  return parseInt(localStorage.getItem('wordBankTiles') ?? '0', 10);
}

function _updateWordBankStar(val) {
  const btn = document.getElementById('word-bank-save-btn');
  if (!btn) return;
  const isSaved = val === _wordBankTileDefault();
  btn.textContent = isSaved ? '★' : '☆';
  btn.classList.toggle('saved', isSaved);
}

function _initWordBankSlider() {
  const slider = document.getElementById('word-bank-slider');
  if (!slider) return;
  const saved = _wordBankTileDefault();
  slider.value = saved;
  document.getElementById('word-bank-slider-pct').textContent = saved;
  _updateWordBankStar(saved);
}

function onWordBankSlider(val) {
  const n = parseInt(val, 10);
  document.getElementById('word-bank-slider-pct').textContent = n;
  _updateWordBankStar(n);
  renderWordBankUI();
}

function saveWordBankDefault() {
  const val = parseInt(document.getElementById('word-bank-slider').value, 10);
  localStorage.setItem('wordBankTiles', val);
  _updateWordBankStar(val);
}

function _renderListenHint(threshold) {
  // Sentence notes are excluded from stories; fall back to card.word_zh (the sentence itself).
  const isSentenceNote = card?.note_type === 'sentence';
  const zh = sentence?.sentence_zh || (isSentenceNote ? card?.word_zh : null);
  const el = document.getElementById('listen-hint-sentence');
  if (!zh) { el.textContent = ''; return; }

  const isCjk = ch => ch >= '一' && ch <= '鿿';
  // Sentence notes have no single "target word" to blank — reveal based on HSK only.
  const targetPositions = isSentenceNote && !sentence ? new Set() : _getTargetPositions(zh);

  // Reveal tokens harder than the threshold (level > threshold, or unknown to HSK).
  // threshold=0 means "All": level > 0 is true for every known word, null words also qualify.
  const revealPositions = new Set();
  if (_hskLevels) {
    // Use story tokens when available; fall back to char-by-char for sentence notes.
    const tokens = sentence?.tokens?.length
      ? sentence.tokens
      : [...zh].map(ch => [ch, null]);
    let pos = 0;
    for (const [tok] of tokens) {
      const tokStart = zh.indexOf(tok, pos);
      if (tokStart === -1) { pos += tok.length; continue; }
      const tokEnd = tokStart + tok.length;
      const overlapsTarget = [...Array(tok.length).keys()].some(k => targetPositions.has(tokStart + k));
      if (!overlapsTarget) {
        const level = _hskLevelOf(tok);
        if (level === null || level > threshold) {
          for (let k = tokStart; k < tokEnd; k++) revealPositions.add(k);
        }
      }
      pos = tokEnd;
    }
  }

  // Build HTML char by char
  let html = '';
  for (let i = 0; i < zh.length; i++) {
    const ch = zh[i];
    if (!isCjk(ch)) {
      html += ch;
    } else if (targetPositions.has(i)) {
      html += `<span class="hint-blank hint-blank-target">_</span>`;
    } else if (threshold === 0) {
      html += ch; // "All" mode: reveal all non-target characters
    } else if (revealPositions.has(i)) {
      html += ch;
    } else {
      html += `<span class="hint-blank">_</span>`;
    }
  }
  el.innerHTML = html;
}

function onListenHintSlider(val) {
  const lvl = parseInt(val, 10);
  document.getElementById('listen-hint-pct').textContent = lvl === 0 ? 'All' : `HSK ${lvl}+`;
  _updateHintStar(lvl);
  _renderListenHint(lvl);
}

function _adjustListenHintSlider(delta) {
  const slider = document.getElementById('listen-hint-slider');
  if (!slider || slider.closest('#listen-hint-wrap')?.style.display === 'none') return;
  const next = Math.max(0, Math.min(6, parseInt(slider.value, 10) + delta));
  slider.value = next;
  onListenHintSlider(next);
}

// ── Render sentence (with target word highlighted) ──────────────────────────
function renderSentence() {
  if (!sentence) {
    return `<span class="hl">${card.word_zh}</span>`;
  }
  let zh = sentence.sentence_zh;
  // Highlight co-occurring vocab words (secondary), then the current card's word (primary)
  const coWords = (sentence.words || []).filter(w => w.word_id !== card.word_id);
  for (const w of coWords) {
    zh = zh.replace(w.word_zh, `<span class="hl-secondary">${w.word_zh}</span>`);
  }
  const targetParts = card.word_zh.includes('...') ? card.word_zh.split('...').filter(p => p.length > 0) : [card.word_zh];
  for (const part of targetParts) {
    zh = zh.replace(part, `<span class="hl">${part}</span>`);
  }
  return `<span>${zh}</span>`;
}

// ── Pick a random 2-char CJK word that doesn't overlap with excludeWord ─────
function pickExtraBlankWord(zh, excludeWord) {
  const excludeIdx = zh.indexOf(excludeWord);
  const excludeEnd = excludeIdx >= 0 ? excludeIdx + excludeWord.length : -1;
  const isCjk = ch => ch >= '\u4E00' && ch <= '\u9FFF';
  const candidates = [];
  for (let i = 0; i < zh.length - 1; i++) {
    if (excludeIdx >= 0 && i < excludeEnd && i + 2 > excludeIdx) continue;
    if (isCjk(zh[i]) && isCjk(zh[i + 1])) candidates.push(i);
  }
  if (!candidates.length) return '';
  const idx = candidates[Math.floor(Math.random() * candidates.length)];
  return zh.slice(idx, idx + 2);
}

// ── Word bank (creating mode, non-sentence notes) ─────────────────────────
async function _buildWordBank() {
  const zh = sentence?.sentence_zh;
  // No sentence for this card yet — clear stale state so the previous card's
  // word bank doesn't linger on screen (renderWordBankUI clears the DOM too).
  if (!zh || !card?.word_zh) { wordBankOrder = []; wordBankTokens = []; return; }
  const target = card.word_zh;

  // Separable words like "由...组成" — split into parts to match independently
  const targetParts = target.includes('...') ? target.split('...').filter(p => p.length > 0) : null;
  const isTargetPart = text => targetParts ? targetParts.includes(text) : (text === target);
  const isTargetEmbedded = text => !targetParts && target.length > 0 && text.includes(target);

  // Build ordered sequence from tokens [[text, word_id_or_null], ...]
  let order;
  if (sentence.tokens && sentence.tokens.length) {
    order = sentence.tokens.flatMap(([text, wid]) => {
      if (isTargetPart(text)) return [{ type: 'target', word: text }];
      if (isTargetEmbedded(text)) {
        // Target is embedded in a larger token — split it out
        const idx = text.indexOf(target);
        const parts = [];
        if (idx > 0) parts.push({ type: 'char', char: text.slice(0, idx) });
        parts.push({ type: 'target', word: target });
        if (idx + target.length < text.length) parts.push({ type: 'char', char: text.slice(idx + target.length) });
        return parts;
      }
      return [{ type: 'char', char: text }];
    });
  } else {
    // Fallback: split by target word boundary, then individual chars
    const rawTokens = [];
    let i = 0;
    const tIdx = targetParts ? -1 : zh.indexOf(target);
    while (i < zh.length) {
      if (tIdx >= 0 && i === tIdx) { rawTokens.push(target); i += target.length; }
      else { rawTokens.push(zh[i]); i++; }
    }
    order = rawTokens.map(tok =>
      isTargetPart(tok)
        ? { type: 'target', word: tok }
        : { type: 'char', char: tok }
    );
  }
  // Handle case where NLP tokenizer splits the target across consecutive tokens
  // e.g., "怎么可能" tokenized as ["怎么", "可能"] — merge them into one target token
  if (!targetParts) {
    for (let i = 0; i < order.length; i++) {
      if (order[i].type !== 'char') continue;
      let acc = '';
      let j = i;
      while (j < order.length && order[j].type === 'char') {
        acc += order[j].char;
        if (acc === target) { order.splice(i, j - i + 1, { type: 'target', word: target }); break; }
        if (!target.startsWith(acc)) break;
        j++;
      }
      if (order[i]?.type === 'target') break;
    }
  }

  // For separable words, count how many parts were found; for normal words, check if any target found
  const targetCount = order.filter(it => it.type === 'target').length;
  const expectedCount = targetParts ? targetParts.length : 1;
  if (targetCount < expectedCount) order.push({ type: 'target', word: targetParts ? targetParts[targetCount] : target });

  const MAX_TILES = parseInt(document.getElementById('word-bank-slider')?.value ?? _wordBankTileDefault(), 10);
  const isWord = tok => /[一-鿿㐀-䶿]/.test(tok.char);
  const allChars = order.filter(it => it.type === 'char');
  allChars.forEach(c => { if (!isWord(c)) c.type = 'pre'; });
  const wordTokens = allChars.filter(c => c.type === 'char');
  if (wordTokens.length > MAX_TILES) {
    const tileIdxSet = new Set();
    while (tileIdxSet.size < MAX_TILES) tileIdxSet.add(Math.floor(Math.random() * wordTokens.length));
    wordTokens.forEach((c, i) => { if (!tileIdxSet.has(i)) c.type = 'pre'; });
  }
  const tileChars = order.filter(it => it.type === 'char');
  const shuffled  = [...tileChars].sort(() => Math.random() - 0.5);

  shuffled.forEach((item, n) => { item.num = n + 1; });

  wordBankOrder  = order;
  wordBankTokens = shuffled;
}

function _parseWordBankInput(text) {
  // Segment into tokens without requiring spaces:
  // - CJK runs → one token (target word)
  // - Digits: greedy 2-digit if it's a valid token number, else single digit
  const isCjk = ch => /[\u3000-\u9FFF\uF900-\uFAFF]/.test(ch);
  const chars = [...text.replace(/\s+/g, '')];
  const raw = [];
  let i = 0;
  while (i < chars.length) {
    if (isCjk(chars[i])) {
      let s = chars[i++];
      while (i < chars.length && isCjk(chars[i])) s += chars[i++];
      raw.push(s);
    } else if (/\d/.test(chars[i])) {
      // Try 2-digit match first
      if (i + 1 < chars.length && /\d/.test(chars[i + 1])) {
        const two = parseInt(chars[i] + chars[i + 1], 10);
        if (wordBankTokens.some(t => t.num === two)) { raw.push(String(two)); i += 2; continue; }
      }
      raw.push(chars[i++]);
    } else {
      // Include punctuation that matches a tile char (e.g. ，。、)
      const ch = chars[i];
      if (wordBankTokens.some(t => t.char === ch)) raw.push(ch);
      i++;
    }
  }
  // Walk wordBankOrder: pre-placed tokens auto-fill; tiles and target come from user input in order
  let rawIdx = 0;
  const result = [];
  for (const tok of wordBankOrder) {
    if (tok.type === 'pre') { result.push(tok.char); continue; }
    if (rawIdx >= raw.length) break; // user hasn't typed this far yet
    const part = raw[rawIdx++];
    if (tok.type === 'char') {
      const n = parseInt(part, 10);
      const tile = isNaN(n) ? null : wordBankTokens.find(t => t.num === n);
      result.push(tile ? tile.char : part);
    } else {
      result.push(part); // target: pass CJK through
    }
  }
  return result;
}

function updateWordBankPreview(text) {
  // Compute slot values by walking wordBankOrder with parsed user tokens
  const isCjk = ch => /[\u3000-\u9FFF\uF900-\uFAFF]/.test(ch);
  const chars = [...text.replace(/\s+/g, '')];
  const raw = [];
  let i = 0;
  while (i < chars.length) {
    if (isCjk(chars[i])) {
      let s = chars[i++];
      while (i < chars.length && isCjk(chars[i])) s += chars[i++];
      raw.push(s);
    } else if (/\d/.test(chars[i])) {
      if (i + 1 < chars.length && /\d/.test(chars[i + 1])) {
        const two = parseInt(chars[i] + chars[i + 1], 10);
        if (wordBankTokens.some(t => t.num === two)) { raw.push(String(two)); i += 2; continue; }
      }
      raw.push(chars[i++]);
    } else {
      const ch = chars[i];
      if (wordBankTokens.some(t => t.char === ch)) raw.push(ch);
      i++;
    }
  }

  // Walk wordBankOrder to assign values to numbered slots
  let rawIdx = 0, slotIdx = 0;
  const usedNums = new Set();
  document.querySelectorAll('.wb-skel-blank[data-slot]').forEach(span => span.textContent = '＿');

  for (const tok of wordBankOrder) {
    if (tok.type === 'pre') continue;
    const span = document.querySelector(`.wb-skel-blank[data-slot="${slotIdx++}"]`);
    if (rawIdx >= raw.length) continue;
    const part = raw[rawIdx++];
    if (tok.type === 'char') {
      const n = parseInt(part, 10);
      const tile = isNaN(n) ? null : wordBankTokens.find(t => t.num === n);
      if (tile) { usedNums.add(tile.num); if (span) span.textContent = tile.char; }
      else if (span) span.textContent = part;
    } else {
      if (span) span.textContent = part; // target word
    }
  }

  // Grey out used tile buttons
  document.querySelectorAll('.wb-token-btn').forEach(btn => {
    const num = parseInt(btn.querySelector('.wb-num').textContent, 10);
    btn.classList.toggle('wb-used', usedNums.has(num));
  });
}

function wordBankAddToken(num) {
  const inp = document.getElementById('word-bank-input');
  const cur = inp.value.trim();
  inp.value = cur ? cur + ' ' + num : String(num);
  updateWordBankPreview(inp.value);
  inp.focus();
}

async function renderWordBankUI() {
  await _buildWordBank();
  if (!wordBankOrder.length) {
    // Sentence not loaded / no match — clear any stale skeleton + tiles from the
    // previous card instead of leaving them on screen as a wrong sentence.
    document.getElementById('word-bank-skeleton')?.replaceChildren();
    document.getElementById('word-bank-tokens')?.replaceChildren();
    return;
  }

  // Sentence skeleton: pre-placed tokens shown as text, blanks for tiles/target (data-slot for live update)
  const skelEl = document.getElementById('word-bank-skeleton');
  if (skelEl) {
    let slotIdx = 0;
    skelEl.innerHTML = wordBankOrder.map(tok => {
      if (tok.type === 'pre') return `<span class="wb-skel-pre">${tok.char}</span>`;
      return `<span class="wb-skel-blank" data-slot="${slotIdx++}">＿</span>`;
    }).join('');
  }

  const tokensEl = document.getElementById('word-bank-tokens');
  tokensEl.innerHTML = wordBankTokens.map(tok =>
    `<button class="wb-token-btn" onmousedown="event.preventDefault()" onclick="wordBankAddToken(${tok.num})">`
    + `<span class="wb-num">${tok.num}</span>`
    + `<span class="wb-char">${tok.char}</span>`
    + `</button>`
  ).join('');

  const inp = document.getElementById('word-bank-input');
  inp.value = '';
  userInput = '';
  setTimeout(() => inp.focus(), 80);
}

// ── Cloze sentence (creating category, non-sentence notes) ──────────────────
function renderClozeSentence() {
  const inputEl = `<input class="cloze-inline-input" id="cloze-inline-input" type="text"`
    + ` autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false"`
    + ` style="width:5.8em"`
    + ` onkeydown="if(event.key==='Enter')revealAnswer()">`;
  if (!sentence) return `<span>${inputEl}</span>`;
  const zh = sentence.sentence_zh;

  // Pick an extra word to blank out (chosen before any replacements)
  clozeExtraWord = pickExtraBlankWord(zh, card.word_zh);

  // Use a temporary placeholder so the two replacements don't interfere
  let text = zh.includes(card.word_zh)
    ? zh.replace(card.word_zh, '\x00T\x00')
    : `${zh} \x00T\x00`;

  if (clozeExtraWord && text.includes(clozeExtraWord)) {
    const blank = `<span class="cloze-blank">${'＿'.repeat(clozeExtraWord.length)}</span>`;
    text = text.replace(clozeExtraWord, blank);
  }

  text = text.replace('\x00T\x00', inputEl);
  return `<span>${text}</span>`;
}

// ── Cloze answer diff ────────────────────────────────────────────────────────
function diffClozeAnswer(userInput, targetWord) {
  if (!userInput) return { html: '<span class="ch-miss">(no answer)</span>', pct: 0 };
  const userChars   = [...userInput];
  const targetChars = [...targetWord];
  const html = userChars.map((ch, i) => {
    if (ch === targetChars[i]) return `<span class="ch-match">${ch}</span>`;
    return `<span class="ch-miss">${ch}</span>`;
  }).join('');
  const matched = userChars.filter((ch, i) => ch === targetChars[i]).length;
  const pct = targetChars.length > 0 ? Math.round((matched / targetChars.length) * 100) : 0;
  return { html, pct };
}

function _renderMultiRatingIfNeeded() {
  document.getElementById('rating-row').style.display = '';
}

// ── Submit rating ───────────────────────────────────────────────────────────
async function rate(rating) {
  document.querySelectorAll('.r-btn').forEach(b => b.disabled = true);
  let _cardMs = null;
  if (_timerStart) {
    _cardMs = Date.now() - _timerStart;
    _sessionTotalMs += _cardMs;
    _sessionRatedCount++;
    _updateAvgTimeBadge();
  }
  try {
    let url = `/api/review?card_id=${card.id}&rating=${rating}`;
    if (_cardMs != null) url += `&duration_ms=${_cardMs}`;
    if (unfinishedMode) url += `&unfinished_mode=true`;
    else if (rootDeckId) url += `&root_deck_id=${rootDeckId}`;
    else if (deckId) url += `&parent_deck_id=${deckId}`;
    const result = await api('POST', url);
    _sessionReviewedCount++;
    if (typeof invalidateCalendar === 'function') invalidateCalendar();
    api('GET', '/api/retention?days=0').then(r => {
      _retentionData = r;
      _updateReviewRRBadge(deckId);
    }).catch(() => {});
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
    showView('review');
    loadCard(result.card, result.counts);
    // Show the back of the card so the user can re-rate
    revealAnswer();
    // Only disable when the stack is empty (allow multiple undos like Anki/Word)
    document.getElementById('undo-btn').disabled = result.stack_size === 0;
  } catch (e) {
    showError('Nothing to undo');
  }
}

// ── Pinyin toggle ────────────────────────────────────────────────────────────
let pinyinCache = {};

async function _loadPinyinRow(text) {
  const row = document.getElementById('pinyin-row');
  if (!text || row.dataset.loadedFor === text) return;
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
  row.dataset.loadedFor = text;
}

async function togglePinyin() {
  const row = document.getElementById('pinyin-row');
  const text = sentence?.sentence_zh || card?.word_zh;
  if (!text) return;
  await _loadPinyinRow(text);
  row.classList.toggle('pinyin-revealed');
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

async function _resolveStory(storyData, resolvedeckId, resolveCat, topic, maxHsk, grammarFocus, grammarPct, mode = 'story') {
  if (!storyData?.error) return storyData;
  const choice = await _openStoryErrorModal(storyData);
  if (choice.action === 'skip') return null;
  if (choice.action === 'history') {
    try { return await api('GET', `/api/story/${resolvedeckId}/${resolveCat}/history`); }
    catch (_) { return null; }
  }
  // retry with new model — not counted toward the 2-attempt limit
  setLoading('Generating your story…', true);
  setLoadingStep(10, null, 'Sending request to AI…');
  _startFakeProgress(10, 55, 45000);
  _startStoryProgressPoll(resolvedeckId, resolveCat);
  let newData;
  try {
    newData = await api('GET', `/api/story/${resolvedeckId}/${resolveCat}` + _storyParams(topic, maxHsk, choice.model, grammarFocus, grammarPct, mode));
  } catch (e) {
    newData = { error: true, reason: e.message, model: choice.model, has_history: storyData.has_history };
  }
  _stopFakeProgress(); _stopStoryProgressPoll();
  return _resolveStory(newData, resolvedeckId, resolveCat, topic, maxHsk, grammarFocus, grammarPct, mode);
}

// ── Story setup modal ────────────────────────────────────────────────────────
let _setupResolve = null;
let _setupIsRegen = false;
let _setupIsMixed = false;
let _setupIsUnfinished = false;
let _setupIsDeckListRegen = false;
let _deckListRegenId = null;

function openStorySetup(sentenceCount, { isMixed = false, isUnfinished = false, learningCount = 0, estimatedTokens = 0 } = {}) {
  _setupIsRegen = !isMixed && !isUnfinished && !!card; // card exists (fresh single-cat) → regenerating
  _setupIsMixed = isMixed;
  _setupIsUnfinished = isUnfinished;
  _setupIsDeckListRegen = false;
  document.getElementById('setup-count-label').textContent =
    `This story will have ${sentenceCount} sentence${sentenceCount !== 1 ? 's' : ''}.`;
  const warn = document.getElementById('setup-learning-warning');
  if (learningCount > 0) {
    warn.textContent = `⚠ ${learningCount} card${learningCount !== 1 ? 's' : ''} still in the Again queue. Generating now may cause a mismatch between story order and review order.`;
    warn.style.display = 'block';
  } else {
    warn.style.display = 'none';
  }
  const tokenWarn = document.getElementById('setup-token-warning');
  if (tokenWarn) {
    if (estimatedTokens > 3000) {
      tokenWarn.textContent = `⚠ ~${estimatedTokens.toLocaleString()} tokens estimated. This story is large and may be slow or expensive.`;
      tokenWarn.style.display = 'block';
    } else {
      tokenWarn.style.display = 'none';
    }
  }
  document.getElementById('setup-topic').value = '';
  document.getElementById('setup-grammar').value = '';
  document.getElementById('setup-grammar-pct').value = 50;
  document.getElementById('setup-hsk-slider').value = 3;
  document.getElementById('setup-mode').value = 'story';
  updateHskLabel();
  updateSetupMode();
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

function updateSetupMode() {
  const mode = document.getElementById('setup-mode').value;
  const topicLabel = document.getElementById('setup-topic-label');
  const topicInput = document.getElementById('setup-topic');
  const btn = document.getElementById('setup-generate-btn');
  const kahnemanSection = document.getElementById('setup-kahneman-section');
  if (mode === 'qa') {
    topicLabel.childNodes[0].textContent = 'Question ';
    topicInput.placeholder = 'e.g. How was life in ancient China?';
    btn.textContent = 'Generate answer';
    topicLabel.style.display = '';
    kahnemanSection.style.display = 'none';
  } else if (mode === 'expository') {
    topicLabel.childNodes[0].textContent = 'Topic ';
    topicInput.placeholder = 'e.g. The Second World War';
    btn.textContent = 'Generate text';
    topicLabel.style.display = '';
    kahnemanSection.style.display = 'none';
  } else if (mode === 'kahneman') {
    topicLabel.style.display = 'none';
    kahnemanSection.style.display = 'block';
    btn.textContent = 'Generate Kahneman';
    _loadKahnemanChapters();
  } else {
    topicLabel.childNodes[0].textContent = 'Topic ';
    topicInput.placeholder = 'e.g. a day at a coffee shop';
    btn.textContent = 'Generate story';
    topicLabel.style.display = '';
    kahnemanSection.style.display = 'none';
  }
}

let _kahnemanChapters = null;

async function _ensureKahnemanChapters() {
  if (_kahnemanChapters) return;
  try {
    const data = await api('GET', '/api/kahneman/chapters');
    if (data.available && data.chapters.length) _kahnemanChapters = data.chapters;
  } catch (e) { /* silent */ }
}

// ── Kahneman examples popup (chapter summary + book's original quotes) ──────────
const _kahnemanExamplesCache = {}; // chapter number → { summary, examples }

async function openKahnemanExamples(chNum, conceptZh) {
  const overlay = document.getElementById('kahneman-examples-overlay');
  const modal   = document.getElementById('kahneman-examples-modal');
  const titleEl = document.getElementById('kahneman-examples-title');
  const bodyEl  = document.getElementById('kahneman-examples-body');
  titleEl.textContent = conceptZh || `第${chNum}章`;
  bodyEl.innerHTML = '<div class="kahneman-examples-loading">加载中…</div>';
  overlay.style.display = '';
  modal.style.display = '';

  let chapter = _kahnemanExamplesCache[chNum];
  if (!chapter) {
    try {
      const data = await api('GET', `/api/kahneman/chapter/${chNum}`);
      chapter = {
        summary: data.chapter?.concept_zh || '',
        examples: data.chapter?.examples_zh || [],
        part: data.chapter?.part_zh || '',
      };
      _kahnemanExamplesCache[chNum] = chapter;
    } catch (e) { chapter = { summary: '', examples: [], part: '' }; }
  }
  if (modal.style.display === 'none') return; // closed while loading
  const esc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const partHtml = chapter.part
    ? `<div class="kahneman-part-label">${esc(chapter.part)}</div>` : '';
  const summaryHtml = chapter.summary
    ? `<div class="kahneman-summary">${esc(chapter.summary)}</div>` : '';
  const examplesHtml = chapter.examples.length
    ? `<div class="kahneman-examples-label">书中原句</div>`
      + chapter.examples.map(ex => `<p class="kahneman-example">${esc(ex)}</p>`).join('')
    : '<div class="kahneman-examples-loading">本章暂无书中原句。</div>';
  bodyEl.innerHTML = partHtml + summaryHtml + examplesHtml;
}

function closeKahnemanExamples() {
  document.getElementById('kahneman-examples-overlay').style.display = 'none';
  document.getElementById('kahneman-examples-modal').style.display = 'none';
}

async function _loadKahnemanChapters() {
  const container = document.getElementById('setup-kahneman-chapters');
  const loading = document.getElementById('setup-kahneman-loading');
  if (_kahnemanChapters) { _renderKahnemanChapters(); return; }
  container.style.display = 'none';
  loading.style.display = 'block';
  try {
    const data = await api('GET', '/api/kahneman/chapters');
    if (!data.available || !data.chapters.length) {
      loading.textContent = 'No chapters found. Run python extract_kahneman.py first.';
      return;
    }
    _kahnemanChapters = data.chapters;
    loading.style.display = 'none';
    container.style.display = 'block';
    _renderKahnemanChapters();
  } catch (e) {
    loading.textContent = 'Failed to load chapters.';
  }
}

function _renderKahnemanChapters() {
  const container = document.getElementById('setup-kahneman-chapters');
  if (!_kahnemanChapters) return;
  let lastPart = null;
  container.innerHTML = _kahnemanChapters.map(ch => {
    // Insert a part header before the first chapter of each book part.
    let header = '';
    if (ch.part_number != null && ch.part_number !== lastPart) {
      lastPart = ch.part_number;
      header = `<div class="kahneman-part-header">${ch.part_zh || ''}</div>`;
    }
    return header + `
    <label class="kahneman-chapter-row">
      <input type="checkbox" class="kahneman-chapter-cb" value="${ch.number}" onchange="_updateKahnemanCount()">
      <div class="kahneman-chapter-info">
        <span class="kahneman-chapter-title">第${ch.number}章 ${ch.title_zh}</span>
        <span class="kahneman-chapter-concept">${ch.concept_zh}</span>
      </div>
    </label>`;
  }).join('');
  _updateKahnemanCount();
}

function _updateKahnemanCount() {
  const checked = document.querySelectorAll('.kahneman-chapter-cb:checked').length;
  const countEl = document.getElementById('setup-kahneman-count');
  countEl.textContent = checked ? `(${checked} selected)` : '(none selected → random 5)';
}

function randomKahnemanChapters() {
  if (!_kahnemanChapters) return;
  const all = Array.from(document.querySelectorAll('.kahneman-chapter-cb'));
  all.forEach(cb => { cb.checked = false; });
  const indices = [];
  while (indices.length < Math.min(5, all.length)) {
    const i = Math.floor(Math.random() * all.length);
    if (!indices.includes(i)) indices.push(i);
  }
  indices.forEach(i => { all[i].checked = true; });
  _updateKahnemanCount();
}

function _getSelectedChapterIds() {
  return Array.from(document.querySelectorAll('.kahneman-chapter-cb:checked'))
    .map(cb => parseInt(cb.value));
}

function confirmStorySetup() {
  const topic       = document.getElementById('setup-topic').value.trim() || null;
  const maxHsk      = parseInt(document.getElementById('setup-hsk-slider').value, 10);
  const model       = document.getElementById('setup-model').value;
  const grammarFocus = document.getElementById('setup-grammar').value.trim() || null;
  const grammarPct  = parseInt(document.getElementById('setup-grammar-pct').value, 10) || 75;
  const mode        = document.getElementById('setup-mode').value;
  const chapterIds  = mode === 'kahneman' ? _getSelectedChapterIds() : null;
  _closeSetupModal();
  if (_setupIsDeckListRegen) {
    _doRegenStoryForDeckList(_deckListRegenId, topic, maxHsk, model, grammarFocus, grammarPct, mode, chapterIds);
  } else if (_setupIsRegen) {
    _doRegenerateStory(topic, maxHsk, model, grammarFocus, grammarPct, mode, chapterIds);
  } else if (_setupIsUnfinished) {
    _doStartReviewUnfinished(topic, maxHsk, model, grammarFocus, grammarPct, mode, chapterIds);
  } else if (_setupIsMixed) {
    _doStartReviewMixed(topic, maxHsk, model, grammarFocus, grammarPct, mode, chapterIds);
  } else {
    _doStartReview(topic, maxHsk, model, grammarFocus, grammarPct, mode, chapterIds);
  }
}

function cancelStorySetup() {
  _closeSetupModal();
  if (!_setupIsRegen && !_setupIsDeckListRegen) showView('decks');
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
    const conceptBadge = s.concept_zh
      ? `<div class="story-concept-badge" title="${s.concept_en || ''}">
           <span class="concept-name">${s.concept_zh}</span>
         </div>`
      : '';
    return `<div class="story-sentence${isCurrent ? ' story-sentence-current' : ''}" data-idx="${s.position}">
      <span class="story-num">${s.position + 1}</span>
      <div class="story-content">
        <div class="story-zh">${highlighted}</div>
        ${conceptBadge}
        ${s.sentence_fr ? `<div class="story-fr">🇫🇷 ${s.sentence_fr}</div>` : ''}
        ${s.sentence_de ? `<div class="story-de">🇩🇪 ${s.sentence_de}</div>` : ''}
      </div>
      <button class="story-play-btn" onclick="storyJumpTo(${s.position})" title="Play">▶</button>
    </div>`;
  }).join('');
  document.getElementById('story-modal-body').innerHTML = html;
  document.getElementById('story-modal-title').textContent = story.topic || 'Full story';
  if (_storyPlaying && _currentPlayIdx >= 0) updateStoryHighlight(_currentPlayIdx);
  document.getElementById('story-modal-overlay').style.display = 'block';
  document.getElementById('story-modal').style.display = 'flex';
}

let _storyPlaying = false;
let _currentPlayIdx = -1;
let _storyStoppedAt = -1;
let _currentAudio = null;

function updateStoryHighlight(idx) {
  document.querySelectorAll('#story-modal-body .story-sentence').forEach(el => {
    const isPlaying = parseInt(el.dataset.idx) === idx;
    el.classList.toggle('story-sentence-playing', isPlaying);
    const playBtn = el.querySelector('.story-play-btn');
    if (playBtn) playBtn.textContent = isPlaying ? '⏸' : '▶';
    if (isPlaying) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
  });
}

function _storyAudioUrl(idx) {
  return `/api/tts-file?text=${encodeURIComponent(story.sentences[idx].sentence_zh)}`;
}

function _playStoryAtIdx(idx) {
  if (!_storyPlaying || idx < 0 || idx >= story.sentences.length) {
    _storyPlaying = false;
    _currentPlayIdx = -1;
    _currentAudio = null;
    updateStoryHighlight(-1);
    const btn = document.getElementById('story-play-all-btn');
    if (btn) btn.textContent = '▶ Play full story';
    return;
  }

  _currentPlayIdx = idx;
  updateStoryHighlight(idx);

  const audio = new Audio(_storyAudioUrl(idx));
  _currentAudio = audio;

  audio.onended = () => { if (_currentAudio === audio) _playStoryAtIdx(idx + 1); };
  audio.onerror = () => { if (_currentAudio === audio) _playStoryAtIdx(idx + 1); };
  audio.play().catch(() => { if (_currentAudio === audio) _playStoryAtIdx(idx + 1); });
}

async function _startPlayback(startIdx) {
  if (!story?.sentences?.length) return;
  _storyPlaying = true;
  const btn = document.getElementById('story-play-all-btn');

  btn.textContent = '⏳ Loading audio…';
  const storyDeckId = rootDeckId || deckId;
  try {
    await api('POST', `/api/preload-session/${storyDeckId}/${category}`);
  } catch (_) {}

  if (!_storyPlaying) return;

  _storyStoppedAt = -1;
  if (btn) btn.textContent = '■ Stop';
  _playStoryAtIdx(startIdx);
}

async function toggleFullStory() {
  if (_storyPlaying) { stopFullStory(); return; }
  const startIdx = _storyStoppedAt >= 0 ? _storyStoppedAt : 0;
  await _startPlayback(startIdx);
}

function storyJumpTo(idx) {
  if (_currentAudio) { _currentAudio.onended = null; _currentAudio.pause(); _currentAudio = null; }
  if (!_storyPlaying) {
    _storyPlaying = true;
    const btn = document.getElementById('story-play-all-btn');
    if (btn) btn.textContent = '■ Stop';
  }
  _playStoryAtIdx(idx);
}

function storySkipNext() {
  if (!_storyPlaying || _currentPlayIdx < 0) return;
  const next = _currentPlayIdx + 1;
  if (next >= story.sentences.length) return;
  storyJumpTo(next);
}

function storySkipPrev() {
  if (!_storyPlaying || _currentPlayIdx < 0) return;
  storyJumpTo(Math.max(0, _currentPlayIdx - 1));
}

function storyRepeat() {
  if (_currentPlayIdx < 0) return;
  storyJumpTo(_currentPlayIdx);
}

function stopFullStory() {
  if (!_storyPlaying) return;
  _storyStoppedAt = _currentPlayIdx;
  _storyPlaying = false;
  _currentPlayIdx = -1;
  if (_currentAudio) { _currentAudio.onended = null; _currentAudio.pause(); _currentAudio = null; }
  updateStoryHighlight(-1);
  const btn = document.getElementById('story-play-all-btn');
  if (btn) btn.textContent = '▶ Continue';
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
  _editWordId = wordObj.word_id || wordObj.id;
  document.getElementById('edit-word-zh').value       = wordObj.word_zh       || '';
  document.getElementById('edit-pinyin').value        = wordObj.pinyin        || '';
  document.getElementById('edit-definition').value    = wordObj.definition    || '';
  document.getElementById('edit-pos').value           = wordObj.pos           || '';
  document.getElementById('edit-traditional').value   = wordObj.traditional   || '';
  document.getElementById('edit-definition-zh').value = wordObj.definition_zh || '';
  document.getElementById('edit-definition-de').value = wordObj.definition_de || '';
  document.getElementById('edit-definition-fr').value = wordObj.definition_fr || '';
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

function toggleReviewCardMenu(e) {
  e.stopPropagation();
  const menu = document.getElementById('review-card-menu');
  menu.style.display = menu.style.display === 'none' ? 'block' : 'none';
}

async function reviewCardAction(action) {
  if (!card) return;
  document.getElementById('review-card-menu').style.display = 'none';
  const cardId = card.id;
  try {
    if (action === 'delete') {
      await api('DELETE', `/api/cards/${cardId}`);
    } else {
      await api('POST', `/api/cards/${cardId}/${action}`);
    }
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

document.addEventListener('click', () => {
  const menu = document.getElementById('edit-card-menu');
  if (menu) menu.style.display = 'none';
  const rmenu = document.getElementById('review-card-menu');
  if (rmenu) rmenu.style.display = 'none';
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
    definition_de: document.getElementById('edit-definition-de').value.trim(),
    definition_fr: document.getElementById('edit-definition-fr').value.trim(),
    notes:         document.getElementById('edit-notes').value.trim(),
  };
  try {
    const updated = await api('PUT', `/api/word/${_editWordId}`, body);
    closeEditCard();
    if (_editFromWord) {
      await openWordDetail(_editWordId);
    } else {
      // Refresh review card in place
      Object.assign(card, {
        word_zh: updated.word_zh, pinyin: updated.pinyin,
        definition: updated.definition, pos: updated.pos,
        traditional: updated.traditional, definition_zh: updated.definition_zh,
        definition_de: updated.definition_de,
        definition_fr: updated.definition_fr,
        notes: updated.notes,
      });
      document.getElementById('word-zh').textContent  = updated.word_zh || '';
      document.getElementById('word-pin').textContent = updated.pinyin  || '';
      document.getElementById('word-def').textContent = updated.definition || '';
      const wordDefDeEl2 = document.getElementById('word-def-de');
      wordDefDeEl2.textContent = updated.definition_de ? `🇩🇪 ${updated.definition_de}` : '';
      wordDefDeEl2.style.display = updated.definition_de ? 'block' : 'none';
      const wordDefFrEl2 = document.getElementById('word-def-fr');
      wordDefFrEl2.textContent = updated.definition_fr ? `🇫🇷 ${updated.definition_fr}` : '';
      wordDefFrEl2.style.display = updated.definition_fr ? 'block' : 'none';
      const posEl = document.getElementById('word-pos');
      posEl.textContent   = updated.pos || '';
      posEl.style.display = updated.pos ? 'inline-block' : 'none';
      renderNotesSection();
    }
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
let _listenCount = 0;

function _updateListenCounters() {
  const label = _listenCount > 0 ? `×${_listenCount}` : '';
  const show  = _listenCount > 0;
  ['listen-counter', 'listen-counter-back'].forEach(id => {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = label;
    el.style.display = show ? 'inline-block' : 'none';
  });
}

async function playSentence() {
  const text = sentence?.sentence_zh || card?.word_zh;
  if (!text) return;
  _listenCount++;
  _updateListenCounters();
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
      learning = todayCounts?.counts?.learning_future || 0;
    }
  } catch (_) {}
  try {
    await openStorySetup(count, { learningCount: learning });
  } catch (_) {
    showView('review');
  }
}

async function _doRegenerateStory(topic, maxHsk, model, grammarFocus, grammarPct, mode = 'story', chapterIds = null) {
  setLoading('Regenerating story…', true);
  setLoadingStep(10, null, 'Sending request to AI…');
  _startFakeProgress(10, 55, 45000);
  try {
    const storyDeckId = rootDeckId || deckId;
    const storyCategory = rootDeckId ? 'unified' : category;
    _startStoryProgressPoll(storyDeckId, storyCategory);
    let storyData;
    try {
      storyData = await api('POST', `/api/story/${storyDeckId}/${storyCategory}/regenerate` + _storyParams(topic, maxHsk, model, grammarFocus, grammarPct, mode, chapterIds));
    } catch (e) {
      _stopFakeProgress(); _stopStoryProgressPoll();
      _showLoadingError('AI request failed', e.message);
      await new Promise(r => setTimeout(r, 2500));
      showError('Regenerate failed: ' + e.message);
      showView('review');
      return;
    }
    _stopFakeProgress(); _stopStoryProgressPoll();
    setLoadingStep(65, null, 'Story received, processing…');
    story = await _resolveStory(storyData, storyDeckId, storyCategory, topic, maxHsk, grammarFocus, grammarPct, mode);
    sentence = story?.sentences?.find(s => s.word_ids?.includes(card.word_id)) || null;
    _updateStoryInfoRow();

    const sentenceCount = story?.sentences?.length ?? 0;
    setLoadingStep(70, 'Story ready!',
      sentenceCount > 0 ? `Generating audio — 0 / ${sentenceCount} sentences…` : 'Loading audio…');
    await _preloadWithProgress(storyDeckId, storyCategory, (done, total) => {
      const pct = 70 + Math.round((done / total) * 28);
      setLoadingStep(pct, null, `Generating audio — ${done} / ${total} sentences…`);
    });
    _showLoadingSuccess('Story regenerated!');
    await new Promise(r => setTimeout(r, 500));
    showView('review');
    showFront();
  } catch (e) {
    _stopFakeProgress(); _stopStoryProgressPoll();
    _showLoadingError('Regenerate failed', e.message);
    await new Promise(r => setTimeout(r, 2500));
    showError('Regenerate failed: ' + e.message);
    showView('review');
  }
}

async function regenerateStoryFromList(deckId) {
  _deckListRegenId = deckId;
  _setupIsDeckListRegen = true;
  _setupIsRegen = false;
  _setupIsMixed = false;
  _setupIsUnfinished = false;
  let sentenceCount = 0;
  try {
    const data = await api('GET', `/api/story/${deckId}/unified/count`);
    sentenceCount = data?.count ?? 0;
  } catch (_) {}
  document.getElementById('setup-count-label').textContent =
    `This story will have ${sentenceCount} sentence${sentenceCount !== 1 ? 's' : ''}.`;
  const warn = document.getElementById('setup-learning-warning');
  warn.style.display = 'none';
  const tokenWarn = document.getElementById('setup-token-warning');
  if (tokenWarn) tokenWarn.style.display = 'none';
  document.getElementById('setup-topic').value = '';
  document.getElementById('setup-grammar').value = '';
  document.getElementById('setup-grammar-pct').value = 50;
  document.getElementById('setup-hsk-slider').value = 3;
  updateHskLabel();
  document.getElementById('setup-modal-overlay').style.display = 'block';
  document.getElementById('setup-modal').style.display = 'flex';
  document.getElementById('setup-topic').focus();
}

async function _doRegenStoryForDeckList(deckId, topic, maxHsk, model, grammarFocus, grammarPct, mode = 'story', chapterIds = null) {
  setLoading('Regenerating story…', true);
  setLoadingStep(10, null, 'Sending request to AI…');
  _startFakeProgress(10, 55, 45000);
  _startStoryProgressPoll(deckId, 'unified');
  try {
    let storyData;
    try {
      storyData = await api('POST', `/api/story/${deckId}/unified/regenerate` + _storyParams(topic, maxHsk, model, grammarFocus, grammarPct, mode, chapterIds));
    } catch (e) {
      _stopFakeProgress(); _stopStoryProgressPoll();
      _showLoadingError('AI request failed', e.message);
      await new Promise(r => setTimeout(r, 2500));
      showError('Regenerate failed: ' + e.message);
      showView('decks');
      return;
    }
    _stopFakeProgress(); _stopStoryProgressPoll();

    // Preload audio before starting review
    const sentenceCount = storyData?.sentences?.length ?? 0;
    setLoadingStep(70, 'Story ready!',
      sentenceCount > 0 ? `Generating audio — 0 / ${sentenceCount} sentences…` : 'Loading audio…');
    await _preloadWithProgress(deckId, 'unified', (done, total) => {
      const pct = 70 + Math.round((done / total) * 28);
      setLoadingStep(pct, null, `Generating audio — ${done} / ${total} sentences…`);
    });

    _showLoadingSuccess('Story regenerated!');
    await new Promise(r => setTimeout(r, 500));

    // Auto-open the deck for review (story is already in DB, will load fast)
    const deck = flatten(_cachedDecks || []).find(d => d.id === deckId);
    if (deck) {
      await startReviewMixed(deckId, deck.name, !!deck.no_story);
    } else {
      showView('decks');
    }
  } catch (e) {
    _stopFakeProgress(); _stopStoryProgressPoll();
    _showLoadingError('Regenerate failed', e.message);
    await new Promise(r => setTimeout(r, 2500));
    showError('Regenerate failed: ' + e.message);
    showView('decks');
  }
}

// ── Back to decks ────────────────────────────────────────────────────────────
function goBack() {
  if (document.getElementById('view-word-detail').style.display !== 'none') {
    showView(_prevView === 'review' ? 'review' : 'browse');
    return;
  }
  if (document.getElementById('view-hanzi-detail').style.display !== 'none') {
    showView('browse');
    return;
  }
  card = null; story = null; sentence = null; wordDetails = null; userInput = '';
  rootDeckId = null; unfinishedMode = false; _sessionReviewedCount = 0;
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
    const isB = deckVal === '__deckB__';

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

    const isDuplicate = e.status === 'duplicate';
    let midCols;
    if (isDuplicate) {
      const dupAction = cfg.duplicate_action || 'move_import';
      const moveTarget = cfg.move_target || '';
      const moveCats = cfg.move_categories || null; // null = all
      const catChecked = (cat) => (!moveCats || moveCats.includes(cat)) ? 'checked' : '';
      const catCheckboxes = `<span style="margin-left:4px;font-size:11px">
          <label title="Listening"><input type="checkbox" ${catChecked('listening')}
            onchange="importToggleDupMoveCat(${_ea(JSON.stringify(e.simplified))}, 'listening', this.checked)">L</label>
          <label title="Reading"><input type="checkbox" ${catChecked('reading')}
            onchange="importToggleDupMoveCat(${_ea(JSON.stringify(e.simplified))}, 'reading', this.checked)">R</label>
          <label title="Creating"><input type="checkbox" ${catChecked('creating')}
            onchange="importToggleDupMoveCat(${_ea(JSON.stringify(e.simplified))}, 'creating', this.checked)">C</label>
        </span>`;
      const moveOpts = dupAction === 'move' ? `
        <input list="import-deck-datalist" class="dup-move-target" value="${_ea(moveTarget)}"
          placeholder="deck path"
          oninput="importSetDupMoveTarget(${_ea(JSON.stringify(e.simplified))}, this.value)"
          style="width:120px;font-size:11px;margin-left:4px">
        ${catCheckboxes}` :
        dupAction === 'move_import' ? `
        <span style="margin-left:4px;font-size:11px;color:var(--clr-muted,#888)">→ import deck</span>
        ${catCheckboxes}` : '';
      const currentDecksHtml = (e.current_decks && e.current_decks.length)
        ? `<span style="font-size:10px;color:var(--clr-muted,#888);margin-right:4px" title="Currently in: ${_ea(e.current_decks.join(', '))}">📂 ${_ea(e.current_decks.join(', '))}</span>`
        : '';
      midCols = `<td colspan="4" style="padding:2px 6px">
        <div style="display:flex;align-items:center;flex-wrap:wrap;gap:2px">
          ${currentDecksHtml}
          <select style="font-size:11px" onchange="importSetDupAction(${_ea(JSON.stringify(e.simplified))}, this.value)">
            <option value="skip"${dupAction==='skip'?' selected':''}>Skip</option>
            <option value="reset"${dupAction==='reset'?' selected':''}>Reset progress</option>
            <option value="move_import"${dupAction==='move_import'?' selected':''}>Move to import deck</option>
            <option value="move"${dupAction==='move'?' selected':''}>Move to deck…</option>
          </select>
          ${moveOpts}
        </div>
      </td>`;
    } else {
      midCols = `<td>${suspBtn('listening')}</td>
      <td>${suspBtn('reading')}</td>
      <td>${suspBtn('creating')}</td>
      <td>
        <div class="import-deck-cell">
          <button class="import-deck-b-badge${isB ? ' active' : ''}"
            onclick="event.stopPropagation();importToggleDeckB(${_ea(JSON.stringify(e.simplified))})"
            title="${isB ? 'Remove Deck B — use default' : 'Assign to Deck B'}"
            ${(!include || isInvalid || !_deckBPath) ? 'disabled' : ''}>B</button>
          <select class="import-row-deck-select"
            onchange="importSetCardDeck(${_ea(JSON.stringify(e.simplified))}, this.value)"
            ${(!include || isInvalid || isB) ? 'disabled' : ''}>
            ${deckOptHtml}
          </select>
        </div>
      </td>`;
    }

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
      ${midCols}
      <td style="color:var(--clr-muted,#888);font-size:11px">${NOTE_TYPE_LABEL[e.note_type] || e.note_type}</td>
      <td style="color:var(--clr-muted,#888);font-size:11px">${e.hsk || ''}</td>
      <td>${statusSpan}</td>
    </tr>`;
  }).join('');

  // Set selected deck value for each row's <select> (skip B-assigned rows)
  tbody.querySelectorAll('select.import-row-deck-select').forEach((sel, i) => {
    const e = _previewEntries[i];
    if (!e) return;
    const dp = (_cardConfigs[e.simplified] || {}).deck_path || '';
    sel.value = dp === '__deckB__' ? '' : dp;
  });
}

let _resizeHandlesInited = false;
function _initImportColResize() {
  if (_resizeHandlesInited) return;
  _resizeHandlesInited = true;
  // Remove any leftover handles from a previous open
  document.querySelectorAll('.import-table .col-resize-handle').forEach(h => h.remove());
  document.querySelectorAll('.import-table thead th').forEach(th => {
    const handle = document.createElement('div');
    handle.className = 'col-resize-handle';
    th.appendChild(handle);
    let startX, startW;
    handle.addEventListener('mousedown', e => {
      startX = e.pageX;
      startW = th.offsetWidth;
      handle.classList.add('resizing');
      const onMove = e2 => { th.style.minWidth = Math.max(30, startW + e2.pageX - startX) + 'px'; };
      const onUp = () => {
        handle.classList.remove('resizing');
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);
      };
      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
      e.preventDefault();
      e.stopPropagation();
    });
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

function importSetDupAction(wordZh, action) {
  const cfg = _cardConfigs[wordZh] || {};
  _cardConfigs[wordZh] = { ...cfg, duplicate_action: action };
  _importRenderTable();
}

function importSetDupMoveTarget(wordZh, target) {
  const cfg = _cardConfigs[wordZh] || {};
  _cardConfigs[wordZh] = { ...cfg, move_target: target || null };
}

function importToggleDupMoveCat(wordZh, cat, checked) {
  const cfg = _cardConfigs[wordZh] || {};
  // null means all-categories; convert to explicit list on first toggle
  const allCats = ['listening', 'reading', 'creating'];
  let cats = cfg.move_categories ? [...cfg.move_categories] : [...allCats];
  if (checked) {
    if (!cats.includes(cat)) cats.push(cat);
  } else {
    cats = cats.filter(c => c !== cat);
  }
  // If all selected, store null (= all)
  _cardConfigs[wordZh] = { ...cfg, move_categories: cats.length === allCats.length ? null : cats };
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

function selectDailyDeck() {
  const d = new Date();
  const mmdd = String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
  deckPickerSelect('daily::' + mmdd);
  importApplyGlobalDeck();
}

function importApplyGlobalDeck() {
  // Keep datalist in sync so new deck names appear in the move-target autocomplete
  const importDeckPath = document.getElementById('import-deck-path').value.trim();
  if (importDeckPath && !_importDeckOptions.includes(importDeckPath)) {
    const dl = document.getElementById('import-deck-datalist');
    if (dl) dl.innerHTML = [..._importDeckOptions, importDeckPath].map(p => `<option value="${_ea(p)}">`).join('');
  }
  _importRenderTable();
}

async function openImportModal() {
  // Hide modal in case this is a "Try Again" from an error state
  document.getElementById('import-modal-overlay').style.display = 'none';
  document.getElementById('import-modal').style.display = 'none';

  importResolutions = {};
  _previewEntries = [];
  _cardConfigs = {};
  _conflictData = [];
  _conflictEdits = {};
  _conflictSelections = {};
  document.getElementById('import-file').value = '';
  document.getElementById('import-preview').style.display = 'none';
  document.getElementById('import-conflicts-section').style.display = 'none';
  document.getElementById('import-result').style.display = 'none';
  document.getElementById('import-submit-btn').style.display = '';
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

  // Populate datalist for duplicate move-target autocomplete
  const dl = document.getElementById('import-deck-datalist');
  if (dl) dl.innerHTML = _importDeckOptions.map(p => `<option value="${_ea(p)}">`).join('');

  // Open OS file picker — modal appears after file is chosen
  document.getElementById('import-file').click();
}

function closeImportModal() {
  document.getElementById('import-modal-overlay').style.display = 'none';
  document.getElementById('import-modal').style.display = 'none';
  const btn = document.getElementById('import-submit-btn');
  btn.onclick = doImport;
  btn.disabled = false;
  btn.textContent = 'Import';
  _resizeHandlesInited = false;
  _deckBPath = null;
  document.getElementById('import-deck-b-path').value = '';
  document.getElementById('deck-b-new-badge').style.display = 'none';
  document.getElementById('deck-b-picker-dropdown').style.display = 'none';
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
  document.getElementById('import-result').style.display = 'none';
  document.getElementById('import-deck-section').style.display = 'none';
  document.getElementById('import-submit-btn').style.display = 'none';

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
      const resultEl = document.getElementById('import-result');
      const d = data.error_detail || {};
      let msg = '<strong style="color:#e74c3c">⚠ YAML parse error</strong>';
      if (d.line) msg += ` at line ${d.line}${d.column ? `, column ${d.column}` : ''}`;
      msg += '<br>';
      const esc = s => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
      if (d.problem) msg += `<br><strong>Problem:</strong> ${esc(d.problem)}`;
      if (d.context) msg += `<br><strong>Context:</strong> ${esc(d.context)}`;
      if (d.tip)     msg += `<br><br><span style="color:#f39c12">${esc(d.tip)}</span>`;
      if (!d.problem && !d.context) msg += `<br>${esc(data.error)}`;
      resultEl.innerHTML = `<div style="font-family:monospace;font-size:12px;background:rgba(231,76,60,.08);border-radius:4px;padding:10px;line-height:1.7">${msg}</div>`;
      resultEl.style.display = 'block';
      // Show "Try Again" button — no deck picker needed yet
      document.getElementById('import-deck-section').style.display = 'none';
      const submitBtn = document.getElementById('import-submit-btn');
      submitBtn.textContent = 'Try Again';
      submitBtn.onclick = openImportModal;
      submitBtn.style.display = '';
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
          ...(e.status === 'duplicate' ? { duplicate_action: 'move_import' } : {}),
        };
      }
    });

    _importRenderTable();
    document.getElementById('import-preview').style.display = 'block';
    _initImportColResize();

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

    // Show deck picker + Import button now that YAML is valid
    document.getElementById('import-deck-section').style.display = '';
    const submitBtn = document.getElementById('import-submit-btn');
    submitBtn.textContent = 'Import';
    submitBtn.onclick = doImport;
    submitBtn.style.display = '';
    if (!yamlContent) btn.style.display = 'none';
    else { btn.disabled = false; btn.textContent = 'Preview'; }
  } catch (e) {
    showError('Preview failed: ' + e.message);
    btn.disabled = false;
    btn.textContent = 'Preview';
  }
}

async function doImport() {
  // If a file was loaded via the YAML editor preview flow, fall back to upload
  const fileInput = document.getElementById('import-file');
  if (fileInput.files.length) { return _doUploadImport(); }

  const deckPath  = document.getElementById('import-deck-path').value.trim();
  const resultEl  = document.getElementById('import-result');

  if (!deckPath) { showError('Please enter a target deck.'); return; }

  const btn = document.getElementById('import-submit-btn');
  btn.disabled = true;
  btn.textContent = 'Importing…';
  resultEl.style.display = 'none';

  const form = new FormData();
  form.append('deck_path', deckPath);

  try {
    const res = await fetch('/api/import/directory', { method: 'POST', body: form });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || res.statusText);

    const hasErrors = data.errors && data.errors.length > 0;

    if (!hasErrors) {
      closeImportModal();
    }

    loadDecks();
    const parts = [`✓ Imported ${data.imported}`];
    if (data.skipped_duplicate) parts.push(`${data.skipped_duplicate} duplicates skipped`);
    if (data.skipped_invalid)   parts.push(`${data.skipped_invalid} invalid skipped`);
    if (hasErrors) parts.push(`${data.errors.length} file error(s)`);

    if (hasErrors) {
      // Show detailed errors inside the modal
      const errLines = data.errors.map(e => {
        let msg = `⚠ ${e.file || 'unknown file'}`;
        if (e.line)    msg += `, line ${e.line}`;
        if (e.column)  msg += `, col ${e.column}`;
        msg += '\n';
        if (e.problem) msg += `  Problem: ${e.problem}\n`;
        if (e.context) msg += `  Context: ${e.context}\n`;
        if (e.tip)     msg += `  Tip: ${e.tip}\n`;
        return msg;
      }).join('\n');
      resultEl.innerHTML =
        `<div style="color:#27ae60;margin-bottom:6px">${parts.join(' · ')}</div>` +
        `<div style="color:#e74c3c;background:rgba(231,76,60,.08);border-radius:4px;padding:8px;font-family:monospace;font-size:12px">${errLines.replace(/</g,'&lt;').replace(/\n/g,'<br>')}</div>`;
      resultEl.style.display = 'block';
      btn.disabled = false;
      btn.textContent = 'Import';
    } else {
      const banner = document.getElementById('error-banner');
      banner.textContent = parts.join(' · ');
      banner.style.background = '#27ae60';
      banner.style.color = '#fff';
      banner.style.display = 'block';
      setTimeout(() => { banner.style.display = 'none'; banner.style.background = ''; banner.style.color = ''; }, 4000);
    }
  } catch (e) {
    resultEl.style.display = 'block';
    resultEl.innerHTML = `<span style="color:#e74c3c">Error: ${e.message}</span>`;
    btn.disabled = false;
    btn.textContent = 'Import';
  }
}

async function _doUploadImport() {
  // Legacy flow: used when YAML editor previews a file via file input
  const fileInput = document.getElementById('import-file');
  const deckPath  = document.getElementById('import-deck-path').value.trim();
  const resultEl  = document.getElementById('import-result');

  if (!deckPath) { showError('Please enter a target deck.'); return; }

  const btn = document.getElementById('import-submit-btn');
  btn.disabled = true;
  btn.textContent = 'Importing…';

  const cardConfigsMap = {};
  _previewEntries.forEach(e => {
    const cfg = _cardConfigs[e.simplified];
    if (cfg) {
      let resolved = {
        ...cfg,
        deck_path: cfg.deck_path === '__deckB__' ? (_deckBPath || null) : cfg.deck_path
      };
      if (resolved.duplicate_action === 'move_import') {
        resolved = { ...resolved, duplicate_action: 'move', move_target: deckPath || null };
      }
      cardConfigsMap[e.simplified] = resolved;
    }
  });

  const form = new FormData();
  form.append('file', fileInput.files[0]);
  form.append('deck_path', deckPath);
  if (Object.keys(importResolutions).length > 0) {
    form.append('resolutions', JSON.stringify(importResolutions));
  }
  form.append('card_configs', JSON.stringify(cardConfigsMap));
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


function _isVisible(id) {
  const el = document.getElementById(id);
  return !!el && getComputedStyle(el).display !== 'none';
}

function _isEditableFocusTarget(el) {
  if (!el) return false;
  const tag = el.tagName;
  const editable = tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || el.isContentEditable;
  if (!editable) return false;
  const style = getComputedStyle(el);
  return style.display !== 'none' && style.visibility !== 'hidden';
}

function _hasOpenModal() {
  const modalIds = [
    'modal-overlay',
    'edit-modal-overlay',
    'story-modal-overlay',
    'import-modal-overlay',
    'yaml-edit-overlay',
    'prompt-modal-overlay',
    'trash-modal-overlay',
    'story-error-overlay',
    'hanzi-regen-modal-overlay',
    'hanzi-edit-modal-overlay',
    'conflict-modal-overlay',
    'kahneman-examples-overlay',
  ];
  return modalIds.some(_isVisible);
}

document.addEventListener('keydown', async e => {
  const inInput = _isEditableFocusTarget(document.activeElement);

  if (e.key === 'Escape') {
    const kahnemanOverlay = document.getElementById('kahneman-examples-overlay');
    if (kahnemanOverlay && kahnemanOverlay.style.display !== 'none') {
      e.preventDefault();
      closeKahnemanExamples();
      return;
    }
    const storyOverlay = document.getElementById('story-modal-overlay');
    if (storyOverlay && storyOverlay.style.display !== 'none') {
      e.preventDefault();
      closeStoryModal();
      return;
    }
    // Blur input fields in review view so space bar can flip the card
    if (inInput) {
      const reviewView = document.getElementById('view-review');
      if (reviewView && reviewView.style.display !== 'none') {
        document.activeElement.blur();
        return;
      }
    }
  }

  if (!inInput) {
    const storyOverlay = document.getElementById('story-modal-overlay');
    if (storyOverlay && storyOverlay.style.display !== 'none') {
      if (e.code === 'Space') { e.preventDefault(); toggleFullStory(); return; }
      if (e.code === 'KeyA' && !e.metaKey && !e.ctrlKey && !e.altKey) { e.preventDefault(); storySkipPrev(); return; }
      if (e.code === 'KeyS' && !e.metaKey && !e.ctrlKey && !e.altKey) { e.preventDefault(); storyRepeat(); return; }
      if (e.code === 'KeyD' && !e.metaKey && !e.ctrlKey && !e.altKey) { e.preventDefault(); storySkipNext(); return; }
    }
  }

  if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
    const editModal = document.getElementById('edit-modal');
    if (editModal && editModal.style.display !== 'none') {
      e.preventDefault();
      saveEditCard();
      return;
    }
  }

  if (e.key === 'R' && e.shiftKey && !e.ctrlKey && !e.metaKey) {
    if (!inInput) { e.preventDefault(); _restartServer(); }
    return;
  }

  // Enter in word-detail → back to review (if opened from review)
  if (e.key === 'Enter' && !e.metaKey && !e.ctrlKey && !e.altKey && !inInput && !_hasOpenModal()) {
    if (document.getElementById('view-word-detail')?.style.display !== 'none' && _prevView === 'review') {
      e.preventDefault();
      goBack();
      return;
    }
  }

  if (!inInput && !_hasOpenModal()) {
    const code = e.code;

    if ((e.metaKey || e.ctrlKey) && code === 'KeyI' && !e.altKey) {
      e.preventDefault();
      openImportModal();
      return;
    }

    if (!e.metaKey && !e.ctrlKey && !e.altKey) {
      if (code === 'KeyD') {
        e.preventDefault();
        goBack();
        return;
      }
      if (code === 'KeyB') {
        e.preventDefault();
        openBrowse();
        return;
      }
      if (code === 'KeyA') {
        e.preventDefault();
        openQuickAddCard();
        return;
      }
    }
  }

  if (!inInput && e.code === 'Space' && !e.ctrlKey && !e.metaKey && !e.altKey) {
    const reviewView = document.getElementById('view-review');
    if (reviewView && reviewView.style.display !== 'none') {
      const backVisible = document.getElementById('side-back')?.style.display === 'flex';
      if (!backVisible) { e.preventDefault(); revealAnswer(); return; }
    }
  }

  if (inInput || e.ctrlKey || e.metaKey || e.altKey) return;


  const _toggleAndScroll = (bodyId, containerId, block = 'nearest') => {
    toggleSection(bodyId);
    if (document.getElementById(bodyId)?.style.display !== 'none')
      document.getElementById(containerId)?.scrollIntoView({ behavior: 'smooth', block });
  };

  // Review shortcuts
  const reviewView = document.getElementById('view-review');
  if (reviewView && reviewView.style.display !== 'none') {
    const backVisible = document.getElementById('side-back')?.style.display === 'flex';
    if (e.key === 'R') {
      e.preventDefault(); location.reload();
    } else if (e.key === 'r') {
      e.preventDefault(); playSentence();
    } else if (e.key === 't') {
      e.preventDefault(); togglePinyin();
    } else if (e.key === ' ') {
      e.preventDefault(); if (!backVisible) revealAnswer();
    } else if (['1','2','3','4'].includes(e.key) && backVisible) {
      e.preventDefault();
      const btns = document.querySelectorAll('.r-btn');
      if (btns.length && !btns[0].disabled) rate(Number(e.key));
    } else if (e.key === 'z') {
      const undoBtn = document.getElementById('undo-btn');
      if (undoBtn && !undoBtn.disabled) { e.preventDefault(); undoReview(); }
    } else if (backVisible && e.key === 'e') {
      e.preventDefault(); _toggleAndScroll('examples-section-body', 'examples-section');
    } else if (backVisible && e.key === 'n') {
      e.preventDefault(); _toggleAndScroll('notes-section-body', 'notes-section');
    } else if (backVisible && e.key === 'w') {
      e.preventDefault(); _toggleAndScroll('word-analysis-section-body', 'word-analysis-section', 'end');
    } else if (e.key === 'q') {
      e.preventDefault(); _adjustListenHintSlider(-1);
    } else if (e.key === 'w') {
      e.preventDefault(); _adjustListenHintSlider(1);
    } else if (e.key === 'f') {
      e.preventDefault(); _toggleSuspendCat('reading');
    } else if (e.key === 'v') {
      e.preventDefault(); _toggleSuspendCat('listening');
    } else if (e.key === 'c') {
      e.preventDefault(); _toggleSuspendCat('creating');
    } else if (e.key === 'C') {
      e.preventDefault(); regenAllFieldsFromReview();
    } else if (e.key === 'D' || e.key === '7') {
      e.preventDefault();
      reviewCardAction('delete');
    } else if (e.key === 'o') {
      e.preventDefault();
      if (deckId) openOptions(deckId);
    }
    return;
  }

  // Word-detail shortcuts
  const wdView = document.getElementById('view-word-detail');
  if (wdView && wdView.style.display !== 'none') {
    if (e.key === 'e') {
      e.preventDefault(); _toggleAndScroll('wd-examples-section-body', 'wd-examples-section');
    } else if (e.key === 'n') {
      e.preventDefault(); _toggleAndScroll('wd-notes-section-body', 'wd-notes-section');
    } else if (e.key === 'w') {
      e.preventDefault(); _toggleAndScroll('wd-word-analysis-section-body', 'wd-word-analysis-section', 'end');
    } else if (e.key === 'C') {
      e.preventDefault(); if (_currentWordId) regenAllFields(_currentWordId);
    } else if (e.key === 'r') {
      e.preventDefault(); _toggleAndScroll('wd-relations-body', 'wd-relations-section');
    }
  }
});

// ── Word-detail deck picker ───────────────────────────────────────────────────

let _wdPickerActiveInput = null;
let _wdPickerActiveIdx = -1;
let _wdDeckSuggestions = []; // [{path, id}]

function _wdBuildSuggestions() {
  const result = [];
  function walk(nodes, prefix) {
    for (const d of nodes) {
      if (d.virtual || d.category) { if (d.children) walk(d.children, prefix); continue; }
      const path = prefix ? `${prefix}::${d.name}` : d.name;
      result.push({ path, id: d.id });
      if (d.children) walk(d.children, path);
    }
  }
  walk(_browseDeckTree, '');
  return result;
}

function _wdRenderDropdown(suggestions, query) {
  const dd = document.getElementById('wd-deck-picker-dd');
  if (!dd) return;
  const isNew = !!query && !suggestions.some(s => s.path.toLowerCase() === query.toLowerCase());
  _wdPickerActiveIdx = -1;
  let html = suggestions.map((s, i) =>
    `<div class="deck-picker-option" data-idx="${i}" onclick="wdPickerSelect('${s.path.replace(/'/g, "\\'")}',${s.id})">${_deckPathHtml(s.path)}</div>`
  ).join('');
  if (!html && !isNew) html = '<div class="deck-picker-empty">No existing decks</div>';
  if (isNew && query) {
    html += `<div class="deck-picker-create" onclick="wdPickerSelect('${query.replace(/'/g, "\\'")}',null)">+ Create ${_deckPathHtml(query)}</div>`;
  }
  dd.innerHTML = html;
  _wdPositionDropdown();
  dd.style.display = '';
}

function _wdPositionDropdown() {
  const inp = _wdPickerActiveInput;
  const dd = document.getElementById('wd-deck-picker-dd');
  if (!inp || !dd) return;
  const r = inp.getBoundingClientRect();
  dd.style.width = r.width + 'px';
  dd.style.left = r.left + 'px';
  const ddH = Math.min(220, dd.scrollHeight || 220);
  if (r.bottom + ddH + 4 > window.innerHeight && r.top - ddH - 4 > 0) {
    dd.style.bottom = (window.innerHeight - r.top + 4) + 'px';
    dd.style.top = 'auto';
  } else {
    dd.style.top = (r.bottom + 4) + 'px';
    dd.style.bottom = 'auto';
  }
}

function wdPickerOpen(inp) {
  _wdPickerActiveInput = inp;
  _wdDeckSuggestions = _wdBuildSuggestions();
  const q = inp.value.trim();
  const filtered = _wdDeckSuggestions.filter(s => !q || s.path.toLowerCase().includes(q.toLowerCase()));
  _wdRenderDropdown(filtered, q);
}

function wdPickerFilter(inp) {
  _wdPickerActiveInput = inp;
  if (!_wdDeckSuggestions.length) _wdDeckSuggestions = _wdBuildSuggestions();
  const q = inp.value.trim();
  const filtered = _wdDeckSuggestions.filter(s => !q || s.path.toLowerCase().includes(q.toLowerCase()));
  _wdRenderDropdown(filtered, q);
}

function wdPickerSelect(path, id) {
  if (_wdPickerActiveInput) _wdPickerActiveInput.value = path;
  if (id !== null) _wdPickerActiveInput.dataset.deckId = id;
  else delete _wdPickerActiveInput.dataset.deckId;
  document.getElementById('wd-deck-picker-dd').style.display = 'none';
}

function wdPickerClose() {
  const dd = document.getElementById('wd-deck-picker-dd');
  if (dd) dd.style.display = 'none';
  _wdPickerActiveInput = null;
}

function wdPickerKey(e, inp) {
  const dd = document.getElementById('wd-deck-picker-dd');
  if (!dd || dd.style.display === 'none') {
    if (e.key === 'ArrowDown') { e.preventDefault(); wdPickerOpen(inp); }
    return;
  }
  const opts = dd.querySelectorAll('.deck-picker-option, .deck-picker-create');
  if (e.key === 'Escape') { dd.style.display = 'none'; return; }
  if (e.key === 'ArrowDown') { e.preventDefault(); _wdPickerActiveIdx = Math.min(_wdPickerActiveIdx + 1, opts.length - 1); }
  else if (e.key === 'ArrowUp') { e.preventDefault(); _wdPickerActiveIdx = Math.max(_wdPickerActiveIdx - 1, -1); }
  else if (e.key === 'Enter' && _wdPickerActiveIdx >= 0) { e.preventDefault(); opts[_wdPickerActiveIdx].click(); return; }
  else { return; }
  opts.forEach((o, i) => o.classList.toggle('active', i === _wdPickerActiveIdx));
  if (_wdPickerActiveIdx >= 0) opts[_wdPickerActiveIdx].scrollIntoView({ block: 'nearest' });
}

async function _wdResolveDeck(path) {
  // Try to find existing deck by path match
  if (!_wdDeckSuggestions.length) _wdDeckSuggestions = _wdBuildSuggestions();
  const found = _wdDeckSuggestions.find(s => s.path.toLowerCase() === path.toLowerCase());
  if (found) return found.id;
  // Create new deck via API (supports :: hierarchy)
  const deck = await api('POST', `/api/decks?name=${encodeURIComponent(path)}`);
  // Refresh deck data so future operations work
  const deckTree = await api('GET', '/api/decks');
  _browseDecks = _flattenDecks(deckTree);
  const allRoot = deckTree.find(d => d.virtual && d.id !== 'unfinished');
  _browseDeckTree = allRoot ? (allRoot.children || []) : deckTree.filter(d => !d.virtual);
  _wdDeckSuggestions = _wdBuildSuggestions();
  return deck.id;
}

document.addEventListener('click', e => {
  const dd = document.getElementById('wd-deck-picker-dd');
  if (!dd || dd.style.display === 'none') return;
  if (_wdPickerActiveInput && !_wdPickerActiveInput.contains(e.target) && !dd.contains(e.target)) {
    dd.style.display = 'none';
  }
});

// ── Deck picker ───────────────────────────────────────────────────────────────

let _deckPickerActiveIdx = -1;
let _deckBPickerActiveIdx = -1;
let _deckBPath = null;

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
  const pickerB = document.getElementById('deck-b-picker');
  const ddB = document.getElementById('deck-b-picker-dropdown');
  if (pickerB && ddB && !pickerB.contains(e.target) && !ddB.contains(e.target)) {
    ddB.style.display = 'none';
  }
});

// ── Deck B picker ─────────────────────────────────────────────────────────────

function _renderDeckBDropdown(suggestions, query) {
  const dd = document.getElementById('deck-b-picker-dropdown');
  if (!dd) return;
  const isNew = !!query && !suggestions.some(s => s.toLowerCase() === query.toLowerCase());
  document.getElementById('deck-b-new-badge').style.display = (isNew && query) ? '' : 'none';
  _deckBPickerActiveIdx = -1;
  let html = suggestions.map((s, i) =>
    `<div class="deck-picker-option" data-idx="${i}" onclick="deckBPickerSelect('${s.replace(/'/g, "\\'")}')">${_deckPathHtml(s)}</div>`
  ).join('');
  if (!html && !isNew) html = '<div class="deck-picker-empty">No existing decks</div>';
  if (isNew && query) {
    html += `<div class="deck-picker-create" onclick="deckBPickerSelect('${query.replace(/'/g, "\\'")}')">+ Create ${_deckPathHtml(query)}</div>`;
  }
  dd.innerHTML = html;
  const show = !!(suggestions.length || isNew || !query);
  dd.style.display = show ? 'block' : 'none';
  if (show) _positionDeckBDropdown();
}

function _positionDeckBDropdown() {
  const input = document.getElementById('import-deck-b-path');
  const dd = document.getElementById('deck-b-picker-dropdown');
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

function deckBPickerOpen() {
  const q = document.getElementById('import-deck-b-path').value.trim();
  const filtered = (window._deckSuggestions || []).filter(s => !q || s.toLowerCase().includes(q.toLowerCase()));
  _renderDeckBDropdown(filtered, q);
}

function deckBPickerFilter() {
  const q = document.getElementById('import-deck-b-path').value.trim();
  const filtered = (window._deckSuggestions || []).filter(s => !q || s.toLowerCase().includes(q.toLowerCase()));
  _renderDeckBDropdown(filtered, q);
}

function deckBPickerSelect(path) {
  document.getElementById('import-deck-b-path').value = path;
  document.getElementById('deck-b-picker-dropdown').style.display = 'none';
  const isNew = !(window._deckSuggestions || []).some(s => s.toLowerCase() === path.toLowerCase());
  document.getElementById('deck-b-new-badge').style.display = (isNew && path) ? '' : 'none';
  _deckBPath = path || null;
  _importRenderTable();
}

function deckBPickerKey(e) {
  const dd = document.getElementById('deck-b-picker-dropdown');
  if (!dd) return;
  if (dd.style.display === 'none') {
    if (e.key === 'ArrowDown') { e.preventDefault(); deckBPickerOpen(); }
    return;
  }
  const opts = dd.querySelectorAll('.deck-picker-option, .deck-picker-create');
  if (e.key === 'Escape') { dd.style.display = 'none'; return; }
  if (e.key === 'ArrowDown') {
    e.preventDefault();
    _deckBPickerActiveIdx = Math.min(_deckBPickerActiveIdx + 1, opts.length - 1);
  } else if (e.key === 'ArrowUp') {
    e.preventDefault();
    _deckBPickerActiveIdx = Math.max(_deckBPickerActiveIdx - 1, -1);
  } else if (e.key === 'Enter' && _deckBPickerActiveIdx >= 0) {
    e.preventDefault();
    opts[_deckBPickerActiveIdx].click();
    return;
  } else { return; }
  opts.forEach((o, i) => o.classList.toggle('active', i === _deckBPickerActiveIdx));
  if (_deckBPickerActiveIdx >= 0) opts[_deckBPickerActiveIdx].scrollIntoView({ block: 'nearest' });
}

function importApplyDeckB() {
  _deckBPath = document.getElementById('import-deck-b-path').value.trim() || null;
  _importRenderTable();
}

function importToggleDeckB(wordZh) {
  const cfg = _cardConfigs[wordZh] || {};
  const isB = cfg.deck_path === '__deckB__';
  _cardConfigs[wordZh] = { ...cfg, deck_path: isB ? null : '__deckB__' };
  _importRenderTable();
}

// ── UI Click Logger ───────────────────────────────────────────────────────────
const _UI_ACTION_MAP = {
  startReviewMixed:         '开始复习牌组',
  startReviewUnfinished:    '开始复习未完成卡片',
  openBrowse:               '打开浏览',
  openBrowseForDeck:        '浏览牌组卡片',
  openStats:                '打开统计',
  openCostModal:            '打开 API 费用',
  openImportModal:          '打开导入',
  openQuickAddCard:         '快速添加卡片',
  createDeck:               '新建牌组',
  openTrash:                '打开垃圾桶',
  toggleBury:               '切换埋葬',
  toggleDeckAllSuspension:  '切换暂停所有卡片',
  toggleDeckMenu:           '打开牌组菜单',
  renameDeck:               '重命名牌组',
  deleteDeck:               '删除牌组',
  clearDeckCards:           '清空牌组卡片',
  openOptions:              '打开牌组选项',
  toggleDeck:               '折叠/展开牌组',
  openWordDetail:           '查看词语详情',
  openHanziDetail:          '查看汉字详情',
  onBrowseRowClick:         '点击浏览行',
  openAddToDeckModal:       '添加到牌组',
  setBrowseDeckFilter:      '筛选牌组',
  cardAction:               '卡片操作',
  toggleCardMenu:           '卡片菜单',
  openHanziRegenModal:      '汉字重新生成',
  openWordEditModal:        '编辑词语',
  openHanziEditModal:       '编辑汉字',
  toggleSection:            '折叠/展开区块',
  toggleReviewCat:          '切换复习类别',
  _moveCatOrder:            '调整类别顺序',
  confirmPromptModal:       '确认对话框',
  cancelPromptModal:        '取消对话框',
  closeDeckMenu:            '关闭牌组菜单',
};

document.addEventListener('click', function(e) {
  const el = e.target.closest('[onclick], button, a');
  if (!el) return;

  const onclickAttr = el.getAttribute('onclick') || '';
  const fnMatch = onclickAttr.match(/^(?:event\.stopPropagation\(\);)?(\w+)/);
  const fnName = fnMatch ? fnMatch[1] : '';

  const label = _UI_ACTION_MAP[fnName]
    || (el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 30)
    || fnName
    || el.tagName;

  const extra = fnName && !_UI_ACTION_MAP[fnName] ? '' : fnName ? ` [${fnName}]` : '';
  const action = `${label}${extra}`;
  fetch('/api/log', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action }),
  }).catch(() => {});
}, true);

// ── Confetti (100% score) ─────────────────────────────────────────────────────
function triggerApplause() {
  const colors = ['#16a34a', '#2563eb', '#d97706', '#dc2626', '#0891b2', '#9333ea'];
  const count = 48;
  for (let i = 0; i < count; i++) {
    const el = document.createElement('div');
    el.className = 'confetti-piece';
    el.style.left = Math.random() * 100 + 'vw';
    el.style.backgroundColor = colors[Math.floor(Math.random() * colors.length)];
    el.style.animationDuration = (1.0 + Math.random() * 1.2) + 's';
    el.style.animationDelay = (Math.random() * 0.4) + 's';
    el.style.borderRadius = Math.random() > 0.5 ? '50%' : '2px';
    document.body.appendChild(el);
    el.addEventListener('animationend', () => el.remove());
  }
}

// ── Server restart (Shift+R, no button) ──────────────────────────────────────
async function _restartServer() {
  try { await fetch('/api/restart', { method: 'POST' }); } catch (_) {}
  const poll = async () => {
    try { const r = await fetch('/api/decks'); if (r.ok) { location.reload(); return; } } catch (_) {}
    setTimeout(poll, 400);
  };
  setTimeout(poll, 600);
}

// ── Boot ─────────────────────────────────────────────────────────────────────
loadDecks();

// ============================================================================
// Home-page calendar heatmap (issue #307)
// Shows per-day study stats above the deck list. Four metrics (retention /
// cards / time / future), two display modes (heatmap / graph), hover for a
// day summary, click a day for a full breakdown.
// ============================================================================

let _calData = null;          // cached /api/calendar-stats response
let _calLoading = false;
let _calMetric = localStorage.getItem('calMetric') || 'retention';
let _calMode   = localStorage.getItem('calMode')   || 'heatmap';
let _calSelectedDay = null;   // 'YYYY-MM-DD' currently shown in the detail panel

const _CAL_CATS = [
  { key: 'listening', zh: '听', en: 'Listening' },
  { key: 'reading',   zh: '读', en: 'Reading'   },
  { key: 'creating',  zh: '创', en: 'Creating'  },
];
const _CAL_METRICS = [
  { key: 'retention', label: 'Retention' },
  { key: 'cards',     label: 'Cards'     },
  { key: 'time',      label: 'Time'      },
  { key: 'future',    label: 'Scheduled' },
];

// ── Date helpers (local, no timezone surprises) ─────────────────────────────
function _calYmd(d) {
  return d.getFullYear() + '-' +
    String(d.getMonth() + 1).padStart(2, '0') + '-' +
    String(d.getDate()).padStart(2, '0');
}
function _calParse(s) { const [y, m, d] = s.split('-').map(Number); return new Date(y, m - 1, d); }
function _calAddDays(d, n) { const r = new Date(d); r.setDate(r.getDate() + n); return r; }
function _calWeekday(d) { return (d.getDay() + 6) % 7; }   // Mon=0 … Sun=6

// ── Entry point (called from renderDecks) ───────────────────────────────────
function initCalendar() {
  const el = document.getElementById('home-calendar');
  if (!el) return;
  if (_calData) { _calRender(); return; }
  if (_calLoading) return;
  _calLoading = true;
  el.innerHTML = '<div class="cal-loading">Loading calendar…</div>';
  api('GET', '/api/calendar-stats?days=365')
    .then(d => { _calData = d; _calLoading = false; _calRender(); })
    .catch(() => { _calLoading = false; el.innerHTML = ''; });
}

// Force a refetch (e.g. after reviewing). Safe to call even if not mounted.
function invalidateCalendar() { _calData = null; }

// ── Top-level render ────────────────────────────────────────────────────────
function _calRender() {
  const el = document.getElementById('home-calendar');
  if (!el || !_calData) return;

  const metricBtns = _CAL_METRICS.map(m =>
    `<button class="cal-seg-btn ${m.key === _calMetric ? 'active' : ''}"
             onclick="calSetMetric('${m.key}')">${m.label}</button>`).join('');
  const modeBtns = [['heatmap', 'Heatmap'], ['graph', 'Graph']].map(([k, lbl]) =>
    `<button class="cal-seg-btn ${k === _calMode ? 'active' : ''}"
             onclick="calSetMode('${k}')">${lbl}</button>`).join('');

  el.innerHTML = `
    <div class="cal-controls">
      <div class="cal-seg cal-seg-metric">${metricBtns}</div>
      <div class="cal-seg cal-seg-mode">${modeBtns}</div>
    </div>
    <div class="cal-body">${_calMode === 'heatmap' ? _calRenderHeatmap() : _calRenderGraph()}</div>
    <div class="cal-detail" id="cal-detail">${_calRenderDetail()}</div>`;
}

function calSetMetric(m) {
  _calMetric = m; localStorage.setItem('calMetric', m);
  _calSelectedDay = null; _calRender();
}
function calSetMode(m) {
  _calMode = m; localStorage.setItem('calMode', m); _calRender();
}

// ── Per-day value extraction ────────────────────────────────────────────────
// Returns {value, has} where value is null when there's nothing to show.
function _calDayValue(date) {
  if (_calMetric === 'future') {
    const f = _calData.future[date];
    return { value: f ? f.total : null, has: !!f };
  }
  const d = _calData.by_date[date];
  if (!d) return { value: null, has: false };
  if (_calMetric === 'retention') {
    return { value: d.total > 0 ? d.correct / d.total : null, has: d.total > 0 };
  }
  if (_calMetric === 'cards') {
    return { value: d.cards || 0, has: (d.cards || 0) > 0 };
  }
  if (_calMetric === 'time') {
    return { value: d.duration_ms || 0, has: (d.duration_ms || 0) > 0 };
  }
  return { value: null, has: false };
}

// Colour for a heatmap cell given its value and the window max.
function _calColor(value, has, max) {
  if (!has || value == null) return 'var(--cal-empty)';
  if (_calMetric === 'retention') {
    // red (0) → amber (.5) → green (1)
    const h = Math.round(value * 120);
    return `hsl(${h}, 62%, 46%)`;
  }
  // count-like metrics: 4 intensity buckets
  const palettes = {
    cards:  ['#9be9a8', '#40c463', '#30a14e', '#216e39'],
    time:   ['#9be9a8', '#40c463', '#30a14e', '#216e39'],
    future: ['#b3c7ff', '#7aa2ff', '#4d7cff', '#2952cc'],
  };
  const pal = palettes[_calMetric] || palettes.cards;
  if (max <= 0) return pal[0];
  const frac = value / max;
  const idx = value <= 0 ? -1 : Math.min(pal.length - 1, Math.floor(frac * pal.length - 1e-9));
  return idx < 0 ? 'var(--cal-empty)' : pal[Math.max(0, idx)];
}

// Window of dates for the current metric.
function _calWindow() {
  const today = _calParse(_calData.today);
  if (_calMetric === 'future') {
    return { start: today, end: _calAddDays(today, 90) };
  }
  return { start: _calAddDays(today, -364), end: today };
}

// ── Heatmap rendering ───────────────────────────────────────────────────────
function _calRenderHeatmap() {
  const { start, end } = _calWindow();

  // Window max for count metrics
  let max = 0;
  for (let d = new Date(start); d <= end; d = _calAddDays(d, 1)) {
    const { value, has } = _calDayValue(_calYmd(d));
    if (has && _calMetric !== 'retention') max = Math.max(max, value);
  }

  // Build padded day list, then chunk into weekly columns
  const cells = [];
  for (let i = 0; i < _calWeekday(start); i++) cells.push(null);
  for (let d = new Date(start); d <= end; d = _calAddDays(d, 1)) cells.push(_calYmd(d));
  const weeks = [];
  for (let i = 0; i < cells.length; i += 7) weeks.push(cells.slice(i, i + 7));

  const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  let lastMonth = -1;
  const monthLabels = weeks.map(w => {
    const firstReal = w.find(c => c);
    if (!firstReal) return '<span class="cal-month"></span>';
    const m = _calParse(firstReal).getMonth();
    const dom = _calParse(firstReal).getDate();
    if (m !== lastMonth && dom <= 7) { lastMonth = m; return `<span class="cal-month">${MONTHS[m]}</span>`; }
    return '<span class="cal-month"></span>';
  }).join('');

  const weekCols = weeks.map(w => {
    const days = w.map(date => {
      if (!date) return '<span class="cal-day cal-pad"></span>';
      const { value, has } = _calDayValue(date);
      const color = _calColor(value, has, max);
      const sel = date === _calSelectedDay ? ' cal-sel' : '';
      return `<span class="cal-day${sel}" style="background:${color}"
                onmouseenter="calShowTip(event,'${date}')" onmouseleave="calHideTip()"
                onclick="calSelectDay('${date}')"></span>`;
    }).join('');
    return `<span class="cal-week">${days}</span>`;
  }).join('');

  return `
    <div class="cal-heatmap-wrap">
      <div class="cal-months">${monthLabels}</div>
      <div class="cal-grid">${weekCols}</div>
      ${_calLegend(max)}
    </div>`;
}

function _calLegend(max) {
  if (_calMetric === 'retention') {
    return `<div class="cal-legend">
      <span>0%</span>
      <span class="cal-leg-sw" style="background:hsl(0,62%,46%)"></span>
      <span class="cal-leg-sw" style="background:hsl(60,62%,46%)"></span>
      <span class="cal-leg-sw" style="background:hsl(120,62%,46%)"></span>
      <span>100%</span></div>`;
  }
  const unit = _calMetric === 'time' ? 'min' : _calMetric === 'future' ? 'due' : 'cards';
  return `<div class="cal-legend"><span>less</span>
    <span class="cal-leg-sw" style="background:${_calColor(max * 0.1, true, max)}"></span>
    <span class="cal-leg-sw" style="background:${_calColor(max * 0.4, true, max)}"></span>
    <span class="cal-leg-sw" style="background:${_calColor(max * 0.7, true, max)}"></span>
    <span class="cal-leg-sw" style="background:${_calColor(max, true, max)}"></span>
    <span>more (${unit})</span></div>`;
}

// ── Graph rendering (vertical bars, x = time along the long axis) ────────────
function _calRenderGraph() {
  const { end } = _calWindow();
  const span = _calMetric === 'future' ? 45 : 45;
  const start = _calMetric === 'future' ? _calParse(_calData.today) : _calAddDays(end, -(span - 1));
  const last  = _calMetric === 'future' ? _calAddDays(start, span - 1) : end;

  const items = [];
  let max = 0;
  for (let d = new Date(start); d <= last; d = _calAddDays(d, 1)) {
    const date = _calYmd(d);
    const { value, has } = _calDayValue(date);
    let v = 0;
    if (has) v = _calMetric === 'retention' ? value : value;
    if (_calMetric !== 'retention') max = Math.max(max, v);
    items.push({ date, value: v, has });
  }
  if (_calMetric === 'retention') max = 1;
  if (max <= 0) max = 1;

  const bars = items.map(it => {
    const h = it.has ? Math.max(2, Math.round(it.value / max * 100)) : 0;
    const color = it.has ? _calColor(it.value, it.has,
      _calMetric === 'retention' ? 1 : max) : 'var(--cal-empty)';
    const sel = it.date === _calSelectedDay ? ' cal-sel' : '';
    return `<span class="cal-bar-col${sel}" onmouseenter="calShowTip(event,'${it.date}')"
              onmouseleave="calHideTip()" onclick="calSelectDay('${it.date}')">
              <span class="cal-bar" style="height:${h}%;background:${color}"></span>
            </span>`;
  }).join('');

  const fmt = x => `${x.getMonth() + 1}/${x.getDate()}`;
  return `
    <div class="cal-graph-wrap">
      <div class="cal-graph">${bars}</div>
      <div class="cal-graph-axis"><span>${fmt(start)}</span><span>${fmt(last)}</span></div>
    </div>`;
}

// ── Floating tooltip ────────────────────────────────────────────────────────
function _calTip() {
  let t = document.getElementById('cal-tip');
  if (!t) { t = document.createElement('div'); t.id = 'cal-tip'; t.className = 'cal-tip'; document.body.appendChild(t); }
  return t;
}
function calShowTip(ev, date) {
  const t = _calTip();
  t.innerHTML = _calTipHtml(date);
  t.style.display = 'block';
  const r = ev.target.getBoundingClientRect();
  const tw = t.offsetWidth, th = t.offsetHeight;
  let left = r.left + r.width / 2 - tw / 2 + window.scrollX;
  left = Math.max(6, Math.min(left, window.innerWidth - tw - 6 + window.scrollX));
  let top = r.top + window.scrollY - th - 8;
  if (top < window.scrollY + 4) top = r.bottom + window.scrollY + 8;
  t.style.left = left + 'px';
  t.style.top = top + 'px';
}
function calHideTip() { const t = document.getElementById('cal-tip'); if (t) t.style.display = 'none'; }

function _calFmtTime(ms) {
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m${String(s % 60).padStart(2, '0')}s`;
}
function _calFmtRR(c, tot) { return tot > 0 ? Math.round(c / tot * 100) + '%' : '—'; }

function _calTipHtml(date) {
  const nice = _calParse(date).toLocaleDateString(undefined,
    { weekday: 'short', month: 'short', day: 'numeric' });

  if (_calMetric === 'future') {
    const f = _calData.future[date];
    if (!f) return `<div class="cal-tip-date">${nice}</div><div class="cal-tip-empty">nothing scheduled</div>`;
    const cats = _CAL_CATS.map(c => `${c.zh} ${f.by_cat[c.key] || 0}`).join(' · ');
    return `<div class="cal-tip-date">${nice}</div>
            <div class="cal-tip-big">${f.total} scheduled</div>
            <div class="cal-tip-cats">${cats}</div>`;
  }

  const d = _calData.by_date[date];
  if (!d || d.total === 0) return `<div class="cal-tip-date">${nice}</div><div class="cal-tip-empty">no reviews</div>`;

  const head = `<div class="cal-tip-big">${d.cards} cards · ${_calFmtRR(d.correct, d.total)} retention</div>`;
  const time = d.timed_count > 0
    ? `<div class="cal-tip-sub">${_calFmtTime(d.duration_ms)} total · ${_calFmtTime(d.duration_ms / d.timed_count)}/card</div>`
    : '';
  const rows = _CAL_CATS.filter(c => d.by_cat[c.key]).map(c => {
    const cd = d.by_cat[c.key];
    const ph = `L ${_calFmtRR(cd.learning.correct, cd.learning.total)} · R ${_calFmtRR(cd.review.correct, cd.review.total)}`;
    return `<div class="cal-tip-row"><b>${c.zh}</b> ${cd.cards}c · ${_calFmtRR(cd.correct, cd.total)} <span class="cal-tip-dim">(${ph})</span></div>`;
  }).join('');
  return `<div class="cal-tip-date">${nice}</div>${head}${time}<div class="cal-tip-rows">${rows}</div>`;
}

// ── Click → detail panel ────────────────────────────────────────────────────
function calSelectDay(date) {
  _calSelectedDay = (_calSelectedDay === date) ? null : date;
  _calRender();
}

function _calRenderDetail() {
  if (!_calSelectedDay) return '';
  const date = _calSelectedDay;
  const nice = _calParse(date).toLocaleDateString(undefined,
    { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' });

  if (_calMetric === 'future') {
    const f = _calData.future[date];
    const body = !f ? '<div class="cal-tip-empty">Nothing scheduled.</div>'
      : `<table class="cal-tbl"><tr><th></th><th>Scheduled</th></tr>${
          _CAL_CATS.map(c => `<tr><td>${c.zh} ${c.en}</td><td>${f.by_cat[c.key] || 0}</td></tr>`).join('')
        }<tr class="cal-tbl-total"><td>Total</td><td>${f.total}</td></tr></table>`;
    return `<div class="cal-detail-head">${nice}</div>${body}`;
  }

  const d = _calData.by_date[date];
  if (!d || d.total === 0) return `<div class="cal-detail-head">${nice}</div><div class="cal-tip-empty">No reviews this day.</div>`;

  const catRows = _CAL_CATS.map(c => {
    const cd = d.by_cat[c.key];
    if (!cd) return `<tr><td>${c.zh} ${c.en}</td><td>0</td><td>—</td><td>—</td><td>—</td><td>—</td></tr>`;
    const avg = cd.timed_count > 0 ? _calFmtTime(cd.duration_ms / cd.timed_count) : '—';
    const tot = cd.timed_count > 0 ? _calFmtTime(cd.duration_ms) : '—';
    return `<tr>
      <td>${c.zh} ${c.en}</td>
      <td>${cd.cards}</td>
      <td>${_calFmtRR(cd.correct, cd.total)}</td>
      <td>${_calFmtRR(cd.learning.correct, cd.learning.total)}</td>
      <td>${_calFmtRR(cd.review.correct, cd.review.total)}</td>
      <td>${avg} <span class="cal-tip-dim">/ ${tot}</span></td>
    </tr>`;
  }).join('');

  const totAvg = d.timed_count > 0 ? _calFmtTime(d.duration_ms / d.timed_count) : '—';
  const totTot = d.timed_count > 0 ? _calFmtTime(d.duration_ms) : '—';
  return `
    <div class="cal-detail-head">${nice}</div>
    <table class="cal-tbl">
      <tr><th>Category</th><th>Cards</th><th>Retention</th><th>Learn</th><th>Review</th><th>Avg / Total</th></tr>
      ${catRows}
      <tr class="cal-tbl-total">
        <td>All</td><td>${d.cards}</td><td>${_calFmtRR(d.correct, d.total)}</td>
        <td>${_calFmtRR(d.learning.correct, d.learning.total)}</td>
        <td>${_calFmtRR(d.review.correct, d.review.total)}</td>
        <td>${totAvg} <span class="cal-tip-dim">/ ${totTot}</span></td>
      </tr>
    </table>`;
}

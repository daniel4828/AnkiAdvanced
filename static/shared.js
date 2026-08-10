// Shared by index.html (the full app) and add.html (the standalone /add page).
// Loaded as a plain script in both, so everything here lives on the global
// scope exactly like app.js does.

async function api(method, path, body) {
  const opts = { method };
  if (body !== undefined) {
    opts.headers = { 'Content-Type': 'application/json' };
    opts.body = JSON.stringify(body);
  }
  const r = await fetch(path, opts);
  // Session cookie expired or was cleared (#666): send the user to the login
  // form instead of letting every view fail with an unexplained error.
  if (r.status === 401) {
    location.href = '/login';
    throw new Error(`${method} ${path} → 401 (redirecting to login)`);
  }
  if (!r.ok) throw new Error(`${method} ${path} → ${r.status}`);
  return r.json();
}

// The one way to add a word anywhere in the app (#643): the full DeepSeek
// pipeline behind /api/add-word-ai, which writes a complete de-zh-bot entry
// (examples, character breakdown, measure words, synonyms, etymology) through
// the ordinary importer. The old /api/quick-add-word only filled four fields
// and — worse — reported success even when cards has UNIQUE(word_id, category)
// silently dropped the insert for a word already studied elsewhere.
//
// Lives here rather than in app.js because /add (#668) is a standalone page
// that must not pull in the 9000-line app bundle: two copies of this polling
// logic would drift, and every fix would have to be made twice.
//
// onUpdate(state, text) is called with 'running' | 'done' | 'error'.
async function addWordViaAi(wordZh, day, onUpdate) {
  let result;
  try {
    result = await api('POST', '/api/add-word-ai', { word_zh: wordZh, day });
  } catch (e) {
    onUpdate('error', e.message || 'Failed to add word');
    return;
  }

  // The deck list only exists in the full app; /add has nothing to refresh.
  const refreshDecks = () => { if (typeof loadDecks === 'function') loadDecks(); };

  // Known words come back finished — no AI call, no job to poll.
  if (!result.job_id) {
    if (result.status === 'already_listed') {
      onUpdate('done', '★ already on your list', result.deck_path);
    } else if (result.status === 'listed') {
      // Parked from a real deck: suspended, so it stops coming up for review.
      onUpdate('done', `★ moved to list from ${result.previous_decks.join(', ')}`,
               result.deck_path);
    } else if (result.status === 'reset') {
      // The cards were moved here from somewhere they had real progress
      // (#675). That progress is gone for good, so name what was thrown away
      // instead of reporting a bland success.
      const from = result.previous_decks.join(', ');
      const lost = result.reviews_discarded
        ? `, ${result.reviews_discarded} review${result.reviews_discarded === 1 ? '' : 's'} discarded`
        : '';
      onUpdate('done', `↺ reset from ${from} → ${result.deck_path}${lost}`, result.deck_path);
    } else {
      onUpdate('done', `✓ moved from Saved → ${result.deck_path}`, result.deck_path);
    }
    refreshDecks();
    return;
  }

  onUpdate('running', 'Generating…');
  const poll = async () => {
    const job = await api('GET', `/api/add-word-ai/progress/${result.job_id}`).catch(() => null);
    if (!job || job.status === 'running') {
      setTimeout(poll, 1500);
      return;
    }
    if (job.status === 'error') {
      onUpdate('error', job.error || 'Failed to add word');
      return;
    }
    // A "done" job that imported nothing means the AI produced an entry the
    // importer rejected — say so instead of claiming success.
    if (!job.summary || !job.summary.imported) {
      onUpdate('error', 'could not be imported — check the logs');
      return;
    }
    onUpdate('done', day === 'list' ? '★ added to your list' : `✓ ${result.deck_path}`,
             result.deck_path);
    refreshDecks();
  };
  setTimeout(poll, 1500);
}

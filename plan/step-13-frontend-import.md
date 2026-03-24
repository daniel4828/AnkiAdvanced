# Step 13 — Frontend: Import Modal (Preview + YAML Editor + Conflict Resolution)

**Branch:** `feat/frontend-import`
**Depends on:** Step 04 (Import API)
**Blocks:** nothing

---

## What to Read First

1. `static/index.html` — existing import modal structure (if any)
2. `static/app.js` — existing `openImportModal()` or import functions
3. `static/style.css` — existing modal styles
4. `recovery/2026-03-23_import-ui-backend.md` — full import UI spec
5. `recovery/2026-03-23_import-yaml-edit-modal.md` — YAML editor modal
6. `recovery/2026-03-23_yaml-edit-save-button.md` — save button behavior
7. `recovery/going_manually_through_chats.md` lines 400–600 — import modal details

---

## Goal

Build a full import workflow:
1. User clicks "Import" → modal opens
2. User uploads a YAML file → preview table shows
3. Table shows each entry with status (ok/duplicate/invalid)
4. User can edit individual entries in a YAML editor sub-modal
5. User resolves conflicts (keep/update radio buttons)
6. User clicks "Import" → sends file + resolutions to API

---

## HTML Structure

### Main Import Modal

```html
<div id="import-modal" class="modal" style="display:none">
  <div class="modal-overlay" onclick="closeImportModal()"></div>
  <div class="modal-content import-modal-content">
    <div class="modal-header">
      <h2>Import YAML</h2>
      <button class="modal-close" onclick="closeImportModal()">✕</button>
    </div>
    <div class="modal-body">

      <!-- Step 1: File upload -->
      <div id="import-step-upload">
        <div class="import-upload-area" onclick="document.getElementById('import-file-input').click()">
          <div class="import-upload-icon">📂</div>
          <div class="import-upload-text">Click to select a YAML file</div>
        </div>
        <input type="file" id="import-file-input" accept=".yaml,.yml" style="display:none"
               onchange="previewImport(this.files[0])">

        <!-- Deck target selector -->
        <div class="import-deck-selector">
          <label>Target deck:</label>
          <select id="import-deck-select">
            <option value="">Auto (by note type)</option>
            <!-- populated from /api/decks -->
          </select>
        </div>
      </div>

      <!-- Step 2: Preview table (shown after file selected) -->
      <div id="import-step-preview" style="display:none">
        <div id="import-summary" class="import-summary"></div>
        <table class="import-table">
          <thead>
            <tr>
              <th>Word</th>
              <th>Type</th>
              <th>Status</th>
              <th>Reason</th>
              <th></th>
            </tr>
          </thead>
          <tbody id="import-table-body"></tbody>
        </table>
      </div>

    </div>
    <div class="modal-footer">
      <button id="import-cancel-btn" onclick="closeImportModal()">Cancel</button>
      <button id="import-do-btn" onclick="doImport()" disabled>Import</button>
    </div>
  </div>
</div>
```

### YAML Editor Sub-Modal

```html
<div id="yaml-edit-modal" class="modal" style="display:none">
  <div class="modal-overlay" onclick="closeYamlEdit()"></div>
  <div class="modal-content yaml-edit-content">
    <div class="modal-header">
      <h2 id="yaml-edit-title">Edit Entry</h2>
      <button class="modal-close" onclick="closeYamlEdit()">✕</button>
    </div>
    <div class="modal-body">
      <textarea id="yaml-edit-area" class="yaml-textarea" rows="20"></textarea>
    </div>
    <div class="modal-footer">
      <button onclick="closeYamlEdit()">Cancel</button>
      <button id="yaml-save-btn" onclick="saveYamlEdit()">Save</button>
    </div>
  </div>
</div>
```

---

## App.js Functions

### Global State

```javascript
let _previewEntries = [];          // array of entry objects from /api/import/preview
let _previewFile = null;           // the File object
let _importConflictResolutions = {}; // {word_zh: 'keep'|'update'}
let _editingEntryIdx = -1;         // index into _previewEntries being edited
```

### Import Flow

```javascript
function openImportModal() {
    _previewEntries = [];
    _previewFile = null;
    _importConflictResolutions = {};
    document.getElementById('import-step-upload').style.display = 'block';
    document.getElementById('import-step-preview').style.display = 'none';
    document.getElementById('import-do-btn').disabled = true;
    document.getElementById('import-modal').style.display = 'flex';
    populateImportDeckSelector();
}

function closeImportModal() {
    document.getElementById('import-modal').style.display = 'none';
}

async function populateImportDeckSelector() {
    const decks = await api('GET', '/api/decks');
    // flatten deck tree into options
    const select = document.getElementById('import-deck-select');
    // ... populate select with deck options
}

async function previewImport(file) {
    if (!file) return;
    _previewFile = file;
    const formData = new FormData();
    formData.append('file', file);
    showLoading('Parsing YAML...');
    try {
        const result = await fetch('/api/import/preview', {method:'POST', body: formData}).then(r=>r.json());
        _previewEntries = result.entries;
        renderImportPreview(result);
        document.getElementById('import-step-preview').style.display = 'block';
        document.getElementById('import-do-btn').disabled = false;
    } catch (e) {
        showError('Preview failed: ' + e.message);
    } finally {
        hideLoading();
    }
}

function renderImportPreview(result) {
    const { entries, summary, conflicts } = result;

    // Render summary badges
    document.getElementById('import-summary').innerHTML = `
        <span class="import-badge ok">✓ ${summary.ok} new</span>
        <span class="import-badge dup">⊘ ${summary.duplicate} duplicate</span>
        <span class="import-badge invalid">✗ ${summary.invalid} invalid</span>
    `;

    // Render table rows
    const tbody = document.getElementById('import-table-body');
    tbody.innerHTML = entries.map((e, idx) => {
        let conflictHtml = '';
        // Check if this word has a conflict
        const conflict = conflicts?.find(c => c.word_zh === e.simplified);
        if (conflict) {
            conflictHtml = `
                <div class="import-conflict">
                  <label><input type="radio" name="res_${e.simplified}" value="keep"
                         ${(_importConflictResolutions[e.simplified] || 'keep') === 'keep' ? 'checked' : ''}
                         onchange="_importConflictResolutions['${e.simplified}'] = 'keep'"> Keep existing</label>
                  <label><input type="radio" name="res_${e.simplified}" value="update"
                         ${_importConflictResolutions[e.simplified] === 'update' ? 'checked' : ''}
                         onchange="_importConflictResolutions['${e.simplified}'] = 'update'"> Use incoming</label>
                </div>`;
        }
        return `
            <tr class="import-row ${e.status}">
              <td>${e.simplified}</td>
              <td class="import-type">${e.note_type || 'vocab'}</td>
              <td class="import-status"><span class="import-status-badge ${e.status}">${e.status}</span></td>
              <td class="import-reason">${e.reason || ''}${conflictHtml}</td>
              <td><button class="import-edit-btn" onclick="openYamlEdit('${e.simplified}', ${idx})">Edit</button></td>
            </tr>`;
    }).join('');
}

async function doImport() {
    const formData = new FormData();
    formData.append('file', _previewFile);
    const deckId = document.getElementById('import-deck-select').value;
    if (deckId) formData.append('deck_id', deckId);
    if (Object.keys(_importConflictResolutions).length > 0) {
        formData.append('resolutions', JSON.stringify(_importConflictResolutions));
    }
    showLoading('Importing...');
    try {
        const result = await fetch('/api/import/upload', {method:'POST', body: formData}).then(r=>r.json());
        closeImportModal();
        showSuccess(`Imported ${result.imported} words (${result.skipped_duplicate} duplicates skipped)`);
        loadDecks();
    } catch (e) {
        showError('Import failed: ' + e.message);
    } finally {
        hideLoading();
    }
}
```

### YAML Editor

```javascript
function openYamlEdit(wordZh, entryIdx) {
    _editingEntryIdx = entryIdx;
    const entry = _previewEntries[entryIdx];
    document.getElementById('yaml-edit-title').textContent = `Edit: ${wordZh}`;
    document.getElementById('yaml-edit-area').value = entry.raw_yaml || '';
    document.getElementById('yaml-edit-modal').style.display = 'flex';
}

function closeYamlEdit() {
    document.getElementById('yaml-edit-modal').style.display = 'none';
    _editingEntryIdx = -1;
}

async function saveYamlEdit() {
    const newYaml = document.getElementById('yaml-edit-area').value;
    if (_editingEntryIdx >= 0) {
        // Update in-memory entry and re-preview
        _previewEntries[_editingEntryIdx].raw_yaml = newYaml;
        // Re-run preview with updated entries
        // Simplest: rebuild file from entries and re-call preview
        // OR just update the table row locally
        closeYamlEdit();
        // Rebuild file from current entries and re-preview
        const combined = _previewEntries.map(e => e.raw_yaml).join('\n---\n');
        const blob = new Blob([`entries:\n${combined}`], {type: 'text/yaml'});
        _previewFile = new File([blob], 'edited.yaml');
        await previewImport(_previewFile);
    }
}
```

---

## CSS

```css
.import-modal-content { min-width: min(80vw, 700px); max-height: 85vh; overflow-y: auto; }
.import-upload-area { border: 2px dashed var(--border); border-radius: 8px; padding: 40px; text-align: center; cursor: pointer; }
.import-upload-area:hover { border-color: var(--primary); background: color-mix(in srgb, var(--primary) 4%, transparent); }
.import-summary { display: flex; gap: 12px; margin-bottom: 12px; }
.import-badge { padding: 3px 10px; border-radius: 12px; font-size: 12px; font-weight: 600; }
.import-badge.ok { background: color-mix(in srgb, var(--success) 15%, transparent); color: var(--success); }
.import-badge.dup { background: color-mix(in srgb, var(--warning) 15%, transparent); color: var(--warning); }
.import-badge.invalid { background: color-mix(in srgb, var(--danger) 15%, transparent); color: var(--danger); }
.import-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.import-table th { text-align: left; padding: 6px 8px; border-bottom: 2px solid var(--border); color: var(--muted); font-size: 11px; text-transform: uppercase; }
.import-table td { padding: 6px 8px; border-bottom: 1px solid var(--border-light); vertical-align: top; }
.import-status-badge { padding: 2px 6px; border-radius: 4px; font-size: 11px; font-weight: 600; }
.import-status-badge.ok { background: color-mix(in srgb, var(--success) 15%, transparent); color: var(--success); }
.import-status-badge.duplicate { background: color-mix(in srgb, var(--warning) 15%, transparent); color: var(--warning); }
.import-status-badge.invalid { background: color-mix(in srgb, var(--danger) 15%, transparent); color: var(--danger); }
.import-conflict { margin-top: 4px; display: flex; gap: 12px; font-size: 12px; }
.import-edit-btn { font-size: 11px; padding: 2px 8px; background: none; border: 1px solid var(--border); border-radius: 4px; cursor: pointer; }
.yaml-edit-content { min-width: 500px; }
.yaml-textarea { width: 100%; font-family: monospace; font-size: 13px; border: 1px solid var(--border); border-radius: 4px; padding: 8px; resize: vertical; }
```

---

## Entry Point

Add an "Import" button in the UI. Good placement: deck list header area or main toolbar.

---

## How to Implement

1. `git checkout -b feat/frontend-import` (after Step 04 is merged)
2. Edit `static/index.html` — add import modal and YAML editor modal HTML
3. Edit `static/app.js` — add all import functions
4. Edit `static/style.css` — add import styles
5. Test full workflow: upload a YAML file, see preview, edit an entry, resolve conflict, import
6. Commit and open PR

---

## Verification Checklist

- [ ] Import modal opens with file upload area
- [ ] Uploading a YAML file shows preview table immediately
- [ ] Summary badges show correct counts
- [ ] Status badges (ok/duplicate/invalid) are color-coded
- [ ] Edit button opens YAML editor with entry content
- [ ] Saving YAML edit updates the preview table
- [ ] Conflict entries show keep/update radio buttons
- [ ] "Import" button sends file + resolutions
- [ ] Success message shows imported/skipped counts
- [ ] Deck tree refreshes after import

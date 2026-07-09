/* databridge browser SPA — redesigned tabbed layout */
'use strict';

(function () {
  // ── State ──────────────────────────────────────────────────────────────────
  let _config = { connection_types: [], hide_auth_inputs: false };
  let _connections = [];
  let _systemSources = [];
  let _activeId = null;
  let _activeType = null;  // 'connection' | 'system'
  let _editingId = null;
  let _schema = null;      // { field: { type, example }, ... }
  let _filterState = { query: '', start: null, end: null, time_field: null };
  let _previewRows = [];
  let _previewLimit = 50;
  let _totalCount = 0;
  let _previewLoading = false;
  let _maskingRules = [];  // [{ field_path, action }]
  let _samplingConfig = null;  // { method, target_column, ratio_or_size }
  const _DEFAULT_WEBHOOK_TEMPLATE = `{
  "text": "Export *{{destination_dataset}}* {{status}}",
  "attachments": [{
    "fields": [
      {"title": "Job ID", "value": "{{job_id}}", "short": true},
      {"title": "Records", "value": "{{records_processed}}", "short": true},
      {"title": "Download", "value": "<{{download_url}}|Download result>", "short": false},
      {"title": "Error", "value": "{{error}}", "short": false}
    ]
  }]
}`;

  let _webhookConfig = { url: '', enabled: false, payloadTemplate: _DEFAULT_WEBHOOK_TEMPLATE };
  let _webhookAllowedPrefixes = [];
  let _assetResolution = false;
  let _assetUrlFields = [];
  let _assetUrlPrefix = '';
  let _visibleColumns = null;  // Set<string> or null = all
  let _jobPollTimer = null;
  let _schemaCollapsed = false;
  let _filterRules = [];  // [{ field, op, value }]
  let _datasinks = [];

  // field-picker combobox state
  let _fpDropdown = null;
  let _fpActiveInput = null;
  let _fpIsMulti = false;

  // cell value popover state
  let _cellPopover = null;
  let _cellPopoverTd = null;
  let _cellPopoverExpanded = false;

  // ── Helpers ────────────────────────────────────────────────────────────────
  const base = () => document.querySelector('[data-base]')?.dataset.base || '';

  async function api(method, path, body) {
    const opts = { method, headers: { 'Content-Type': 'application/json' } };
    if (body !== undefined) opts.body = JSON.stringify(body);
    const r = await fetch(base() + path, opts);
    if (r.status === 204) return null;
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.detail || `HTTP ${r.status}`);
    return data;
  }

  function esc(s) {
    if (s == null) return '';
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  function showError(msg) {
    const t = document.getElementById('error-toast');
    document.getElementById('error-toast-msg').textContent = msg;
    t.classList.remove('hidden');
    setTimeout(() => t.classList.add('hidden'), 5000);
  }

  function showSuccess(msg) {
    const t = document.getElementById('success-toast');
    document.getElementById('success-toast-msg').textContent = msg;
    t.classList.remove('hidden');
    setTimeout(() => t.classList.add('hidden'), 3000);
  }

  // ── Tab navigation ─────────────────────────────────────────────────────────
  function switchTab(tab) {
    const importView = document.getElementById('data-import-view');
    const jobsView = document.getElementById('jobs-view');
    const importBtn = document.getElementById('nav-tab-import-btn');
    const jobsBtn = document.getElementById('nav-tab-jobs-btn');
    if (tab === 'jobs') {
      importView.classList.add('hidden');
      jobsView.classList.remove('hidden');
      importBtn.classList.remove('nav-tab--active');
      jobsBtn.classList.add('nav-tab--active');
      renderJobsView();
    } else {
      jobsView.classList.add('hidden');
      importView.classList.remove('hidden');
      jobsBtn.classList.remove('nav-tab--active');
      importBtn.classList.add('nav-tab--active');
    }
  }

  // ── Connection Tab Bar ─────────────────────────────────────────────────────
  function renderConnectionTabBar() {
    const inner = document.getElementById('conn-tab-bar-inner');
    const addBtn = document.getElementById('add-connection-tab-btn');
    // Remove existing tab wrappers (keep add button)
    Array.from(inner.querySelectorAll('.group\\/tab')).forEach(el => el.remove());

    const all = [
      ..._connections.map(c => ({ ...c, isSystem: false })),
      ..._systemSources.map(c => ({ ...c, isSystem: true })),
    ];

    all.forEach(conn => {
      const wrap = document.createElement('div');
      wrap.className = 'relative group/tab flex items-end';

      const btn = document.createElement('button');
      const isActive = conn.id == _activeId;
      btn.className = 'conn-tab' + (isActive ? ' conn-tab--active' : '');
      btn.dataset.testid = `conn-tab-${conn.id}`;
      btn.setAttribute('data-testid', `conn-tab-${conn.id}`);
      btn.setAttribute('tabindex', '0');
      btn.innerHTML = `
        <span class="truncate max-w-28">${esc(conn.label)}</span>
        <span class="type-badge-pill font-label">${esc(conn.type)}</span>
        ${conn.isSystem ? '<span class="text-xs text-gray-400">sys</span>' : ''}
      `;
      btn.addEventListener('click', () => selectConnection(conn.id, conn.isSystem));
      btn.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') selectConnection(conn.id, conn.isSystem); });
      wrap.appendChild(btn);

      if (!conn.isSystem) {
        const editBtn = document.createElement('button');
        editBtn.className = 'absolute -top-2 -right-2 hidden group-hover/tab:flex items-center justify-center w-5 h-5 rounded-full bg-surface-container border border-outline-variant/30 text-on-surface-variant hover:text-primary hover:border-primary/50 transition-colors z-10';
        editBtn.title = 'Edit connection';
        editBtn.setAttribute('tabindex', '0');
        editBtn.innerHTML = '<span class="material-symbols-outlined text-[12px]">edit</span>';
        editBtn.addEventListener('click', e => { e.stopPropagation(); openEditModal(conn.id); });
        wrap.appendChild(editBtn);
      }

      inner.insertBefore(wrap, addBtn);
    });
  }

  // ── Select connection ──────────────────────────────────────────────────────
  async function selectConnection(id, isSystem) {
    _activeId = id;
    _activeType = isSystem ? 'system' : 'connection';
    _schema = null;
    _previewRows = [];
    _totalCount = 0;
    _previewLimit = 50;
    _filterState = { query: '', start: null, end: null, time_field: null };
    _visibleColumns = null;

    renderConnectionTabBar();
    renderSchemaSection({});
    clearPreviewTable();
    updateHealthBadge('SYNCING…', 'syncing');
    updateLastSynced('Detecting schema…');

    ['masking-toggle', 'sampling-toggle', 'asset-resolution-toggle', 'webhook-toggle'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.disabled = false;
    });

    // Auto-detect schema (load credentials in parallel for user connections)
    try {
      const schemaPromise = api('GET', `/api/v1/connections/${id}/schema`);
      const credsPromise = isSystem
        ? Promise.resolve(null)
        : api('GET', `/api/v1/connections/${id}/credentials`).catch(() => null);
      const [data, creds] = await Promise.all([schemaPromise, credsPromise]);
      _schema = data.fields || {};
      renderSchemaSection(_schema);
      updateHealthBadge('HEALTHY STATUS', 'healthy');
      const savedTs = creds && Object.prototype.hasOwnProperty.call(creds, 'timestamp_column')
        ? creds.timestamp_column
        : undefined;
      const timeField = savedTs !== undefined
        ? savedTs  // use saved value (may be "" = no timestamp)
        : Object.keys(_schema).find(k => /time|date|ts|stamp/i.test(k));
      _filterState.time_field = timeField || null;
      renderTimeFieldBadge(timeField);
      enableTimeRangeSelect(!!timeField);
      updateLastSynced(new Date().toLocaleTimeString());
    } catch (e) {
      updateHealthBadge('ERROR', 'error');
      updateLastSynced('Schema error');
    }

    // Load preview
    loadPreview();
    loadDatasinks();
  }

  // ── Schema section ─────────────────────────────────────────────────────────
  function _looksLikeTimestamp(v) {
    if (v == null) return false;
    if (typeof v === 'number') return v > 1e9 && v < 1e13; // Unix seconds or ms
    if (typeof v !== 'string') return false;
    return /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}/.test(v) || /^\d{4}-\d{2}-\d{2}$/.test(v);
  }

  function _fieldTimestampScore(fieldName) {
    if (!_previewRows || !_previewRows.length) return 0;
    const sample = _previewRows.slice(0, 10);
    return sample.filter(r => _looksLikeTimestamp(r[fieldName])).length / sample.length;
  }

  function _bestTimestampField() {
    if (!_schema || !_previewRows || !_previewRows.length) return null;
    let best = null, bestScore = 0;
    for (const k of Object.keys(_schema)) {
      const s = _fieldTimestampScore(k);
      if (s > bestScore) { bestScore = s; best = k; }
    }
    return bestScore >= 0.5 ? best : null;
  }

  function renderSchemaSection(fields) {
    const chipsEl = document.getElementById('schema-field-chips');
    const entries = Object.entries(fields || {});
    chipsEl.innerHTML = entries.map(([k, v]) => {
      const isTs = k === _filterState.time_field;
      const score = _fieldTimestampScore(k);
      const tsHint = score >= 0.5 ? ' <span class="ts-score-dot" title="Values look like timestamps">◷</span>' : '';
      return `<div class="field-chip${isTs ? ' field-chip--ts' : ''}" data-testid="schema-chip-${esc(k.replace(/\./g,'-'))}" onclick="window.DB.setTimestampField('${esc(k)}')" title="Set as timestamp field">
        <span class="field-name">${esc(k)}</span>
        <span class="field-type">${esc((v && v.type) || 'str')}${tsHint}</span>
      </div>`;
    }).join('');
    renderColumnPicker();
  }

  function toggleSchemaCollapse() {
    _schemaCollapsed = !_schemaCollapsed;
    const chips = document.getElementById('schema-field-chips');
    chips.classList.toggle('hidden', _schemaCollapsed);
    const btn = document.getElementById('schema-collapse-btn');
    btn.querySelector('.material-symbols-outlined').textContent = _schemaCollapsed ? 'expand_less' : 'expand_more';
  }

  async function refreshSchema() {
    if (!_activeId) return;
    updateHealthBadge('SYNCING…', 'syncing');
    try {
      const data = await api('GET', `/api/v1/connections/${_activeId}/schema`);
      _schema = data.fields || {};
      renderSchemaSection(_schema);
      updateHealthBadge('HEALTHY STATUS', 'healthy');
    } catch (e) {
      updateHealthBadge('ERROR', 'error');
      showError('Schema refresh failed: ' + e.message);
    }
  }

  function renderColumnPicker() {
    const list = document.getElementById('column-picker-list');
    if (!list || !_schema) return;
    const fields = Object.keys(_schema);
    list.innerHTML = fields.map(f => {
      const checked = !_visibleColumns || _visibleColumns.has(f);
      return `<label class="flex items-center gap-2 text-xs cursor-pointer text-on-surface">
        <input type="checkbox" ${checked ? 'checked' : ''} data-field="${esc(f)}"
          class="w-3 h-3 rounded accent-primary"
          onchange="window.DB._onColumnVisChange('${esc(f)}', this.checked)" />
        <span class="font-mono text-[10px]">${esc(f)}</span>
      </label>`;
    }).join('');
    updateColumnPickerMaster();
  }

  function updateColumnPickerMaster() {
    const master = document.getElementById('column-picker-all');
    if (!master || !_schema) return;
    const fields = Object.keys(_schema);
    const visibleCount = fields.filter(f => !_visibleColumns || _visibleColumns.has(f)).length;
    master.checked = visibleCount === fields.length;
    master.indeterminate = visibleCount > 0 && visibleCount < fields.length;
  }

  function _onColumnVisChange(field, checked) {
    if (!_visibleColumns && _schema) {
      _visibleColumns = new Set(Object.keys(_schema));
    }
    if (!_visibleColumns) _visibleColumns = new Set();
    if (checked) _visibleColumns.add(field);
    else _visibleColumns.delete(field);
    updateColumnPickerMaster();
    renderPreviewTable(_previewRows);
  }

  function _onColumnVisAllChange(checked) {
    _visibleColumns = checked ? null : new Set();
    renderColumnPicker();
    renderPreviewTable(_previewRows);
  }

  function toggleColumnPicker() {
    const dd = document.getElementById('column-picker-dropdown');
    dd.classList.toggle('hidden');
    if (!dd.classList.contains('hidden')) renderColumnPicker();
  }

  // ── Time field & range ─────────────────────────────────────────────────────
  function renderTimeFieldBadge(fieldName) {
    const badge = document.getElementById('time-field-badge');
    badge.textContent = fieldName ? `FIELD: ${fieldName}` : 'FIELD: none';
    badge.classList.toggle('opacity-40', !fieldName);
    badge.classList.remove('hidden');
  }

  function _applyTimestampField(fieldName) {
    _filterState.time_field = fieldName || null;
    _filterState.start = null;
    _filterState.end = null;
    const sel = document.getElementById('time-range-select');
    if (sel) sel.value = '';
    const customRow = document.getElementById('custom-range-row');
    if (customRow) customRow.classList.add('hidden');
    const startInput = document.getElementById('custom-start-input');
    const endInput = document.getElementById('custom-end-input');
    if (startInput) startInput.value = '';
    if (endInput) endInput.value = '';
    renderTimeFieldBadge(fieldName);
    enableTimeRangeSelect(!!fieldName);
    _saveTimestampColumn(fieldName);
    renderSchemaSection(_schema || {});
    loadPreview();
  }

  function setTimestampField(fieldName) {
    _applyTimestampField(_filterState.time_field === fieldName ? null : fieldName);
  }

  async function _saveTimestampColumn(fieldName) {
    if (_activeType !== 'connection') return;
    try {
      await api('PATCH', `/api/v1/connections/${_activeId}`, {
        credentials: { timestamp_column: fieldName || '' },
      });
    } catch (_) { /* non-critical: UI state already reflects the choice */ }
  }

  function cycleTimeField() {
    if (!_schema) return;
    const cycle = [null, ...Object.keys(_schema)];
    const idx = cycle.indexOf(_filterState.time_field);
    _applyTimestampField(cycle[(idx + 1) % cycle.length]);
  }

  function enableTimeRangeSelect(enabled) {
    const sel = document.getElementById('time-range-select');
    sel.disabled = !enabled;
  }

  function onTimeRangeChange(value) {
    const customRow = document.getElementById('custom-range-row');
    if (value === 'custom') {
      customRow.classList.remove('hidden');
      return;
    }
    customRow.classList.add('hidden');
    document.getElementById('custom-start-input').value = '';
    document.getElementById('custom-end-input').value = '';
    if (!value) {
      _filterState.start = null;
      _filterState.end = null;
    } else {
      const now = new Date();
      const units = { h: 3600000, d: 86400000 };
      const n = parseInt(value);
      const unit = units[value.slice(-1)] || 3600000;
      _filterState.end = now.toISOString();
      _filterState.start = new Date(now - n * unit).toISOString();
    }
    loadPreview();
  }

  function onCustomRangeChange() {
    const startVal = document.getElementById('custom-start-input')?.value;
    const endVal = document.getElementById('custom-end-input')?.value;
    _filterState.start = startVal ? new Date(startVal).toISOString() : null;
    _filterState.end = endVal ? new Date(endVal).toISOString() : null;
    if (_filterState.start || _filterState.end) loadPreview();
  }

  // ── Predicate filter ───────────────────────────────────────────────────────
  function onPredicateInput(value) {
    _filterState.query = value;
    if (!value.trim() && _filterRules.length) {
      _filterRules = [];
      renderAdvancedFilterPanel();
    }
    const err = document.getElementById('predicate-error');
    const { valid, message } = validatePredicate(value);
    err.textContent = valid ? '' : message;
    err.classList.toggle('hidden', valid);
    updateClearAllVisibility();
  }

  function validatePredicate(expr) {
    if (!expr) return { valid: true, message: '' };
    // Basic syntax check: balanced parens and quotes
    let depth = 0;
    let inSingle = false, inDouble = false;
    for (const ch of expr) {
      if (ch === "'" && !inDouble) inSingle = !inSingle;
      else if (ch === '"' && !inSingle) inDouble = !inDouble;
      else if (!inSingle && !inDouble) {
        if (ch === '(') depth++;
        else if (ch === ')') depth--;
        if (depth < 0) return { valid: false, message: 'Unmatched closing parenthesis' };
      }
    }
    if (inSingle) return { valid: false, message: 'Unclosed single quote' };
    if (inDouble) return { valid: false, message: 'Unclosed double quote' };
    if (depth !== 0) return { valid: false, message: 'Unmatched parenthesis' };
    return { valid: true, message: '' };
  }

  // ── Advanced filter modal ──────────────────────────────────────────────────
  function toggleAdvancedFilter() {
    const modal = document.getElementById('filter-modal');
    if (modal.classList.contains('hidden')) {
      modal.classList.remove('hidden');
      renderAdvancedFilterPanel();
    } else {
      closeFilterModal();
    }
  }

  function closeFilterModal() {
    document.getElementById('filter-modal').classList.add('hidden');
  }

  function applyFilterModal() {
    syncPredicateFromRules();
    updateClearAllVisibility();
    closeFilterModal();
    loadPreview();
  }

  function addFilterRule() {
    _filterRules.push({ field: '', op: '==', value: '' });
    renderAdvancedFilterPanel();
  }

  function removeFilterRule(n) {
    _filterRules.splice(n, 1);
    renderAdvancedFilterPanel();
    syncPredicateFromRules();
  }

  function onRuleChange(n, key, value) {
    _filterRules[n][key] = value;
    syncPredicateFromRules();
    updateClearAllVisibility();
  }

  function syncPredicateFromRules() {
    if (!_filterRules.length) return;
    const logic = document.getElementById('filter-logic-select')?.value || 'AND';
    const parts = _filterRules
      .filter(r => r.field && r.value)
      .map(r => `${r.field} ${r.op} '${r.value}'`);
    _filterState.query = parts.join(` ${logic} `);
    const input = document.getElementById('predicate-filter-input');
    if (input) input.value = _filterState.query;
  }

  function renderAdvancedFilterPanel() {
    const list = document.getElementById('filter-rules-list');
    const logicSelect = document.getElementById('filter-logic-select');
    logicSelect?.classList.toggle('hidden', _filterRules.length < 2);
    list.innerHTML = (_filterRules.length === 0
      ? '<p class="text-xs text-on-surface-variant/50 italic py-2">No rules yet. Click Add Rule to start.</p>'
      : _filterRules.map((r, n) => `
      <div class="flex items-center gap-2 bg-surface-container-low rounded-xl px-3 py-2" data-testid="filter-rule-${n}">
        <input type="text" value="${esc(r.field)}" placeholder="field name"
          class="flex-1 min-w-0 bg-surface-container-lowest border border-outline-variant/20 rounded-lg px-3 py-1.5 text-xs font-mono focus:border-primary/50 focus:ring-0"
          autocomplete="off"
          onfocus="window.DB._onFieldPickerFocus(this, false)"
          oninput="window.DB._onRuleChange(${n},'field',this.value); window.DB._onFieldPickerInput(this)"
          onblur="window.DB._onFieldPickerBlur(this)" />
        <div class="relative flex-shrink-0">
          <select class="bg-surface-container-lowest border border-outline-variant/20 rounded-lg pl-2 pr-6 py-1.5 text-xs font-mono appearance-none focus:border-primary/50 focus:ring-0"
            onchange="window.DB._onRuleChange(${n},'op',this.value)">
            <option value="==" ${r.op==='==' ? 'selected' : ''}>&equals;&equals;</option>
            <option value="!=" ${r.op==='!=' ? 'selected' : ''}>&ne;</option>
            <option value=">" ${r.op==='>' ? 'selected' : ''}>&gt;</option>
            <option value="<" ${r.op==='<' ? 'selected' : ''}>&lt;</option>
            <option value=">=" ${r.op==='>=' ? 'selected' : ''}>&ge;</option>
            <option value="<=" ${r.op==='<=' ? 'selected' : ''}>&le;</option>
            <option value="contains" ${r.op==='contains' ? 'selected' : ''}>contains</option>
          </select>
          <span class="absolute right-1.5 top-1/2 -translate-y-1/2 material-symbols-outlined text-[12px] text-outline-variant pointer-events-none">expand_more</span>
        </div>
        <input type="text" value="${esc(r.value)}" placeholder="value"
          class="flex-1 min-w-0 bg-surface-container-lowest border border-outline-variant/20 rounded-lg px-3 py-1.5 text-xs font-mono focus:border-primary/50 focus:ring-0"
          oninput="window.DB._onRuleChange(${n},'value',this.value)" />
        <button class="text-error/50 hover:text-error transition-colors flex-shrink-0"
          onclick="window.DB._removeFilterRule(${n})">
          <span class="material-symbols-outlined text-[18px]">delete</span>
        </button>
      </div>`).join(''));
  }

  // ── Preview table ──────────────────────────────────────────────────────────
  function clearPreviewTable() {
    const thead = document.getElementById('preview-thead');
    const tbody = document.getElementById('preview-tbody');
    const empty = document.getElementById('preview-empty-msg');
    const loadMore = document.getElementById('load-more-btn');
    if (thead) thead.innerHTML = '';
    if (tbody) tbody.innerHTML = '';
    if (empty) empty.classList.remove('hidden');
    if (loadMore) loadMore.classList.add('hidden');
    document.getElementById('total-rows-display').textContent = 'TOTAL: —';
    const li = document.getElementById('limit-input'); if (li) li.value = _previewLimit;
  }

  const STATUS_BADGE_CLASS = {
    processed: 'status-badge status-badge--processed',
    completed: 'status-badge status-badge--completed',
    error:     'status-badge status-badge--error',
    failed:    'status-badge status-badge--failed',
    pending:   'status-badge status-badge--pending',
    running:   'status-badge status-badge--running',
  };

  function cellStr(val) {
    if (val !== null && typeof val === 'object') return JSON.stringify(val);
    return String(val ?? '');
  }

  function statusCellHtml(key, val) {
    const str = cellStr(val);
    const lower = str.toLowerCase();
    if (/^status$|^state$/.test(key.toLowerCase()) && STATUS_BADGE_CLASS[lower]) {
      return `<span class="${STATUS_BADGE_CLASS[lower]} font-label">${esc(str)}</span>`;
    }
    return esc(str);
  }

  function renderPreviewTable(rows) {
    const thead = document.getElementById('preview-thead');
    const tbody = document.getElementById('preview-tbody');
    const empty = document.getElementById('preview-empty-msg');
    const loadMore = document.getElementById('load-more-btn');

    if (!rows || rows.length === 0) {
      thead.innerHTML = '';
      tbody.innerHTML = '';
      empty.classList.remove('hidden');
      loadMore.classList.add('hidden');
      return;
    }

    empty.classList.add('hidden');

    let cols = Array.from(new Set(rows.flatMap(r => Object.keys(r))));
    if (_visibleColumns) cols = cols.filter(c => _visibleColumns.has(c));

    thead.innerHTML = `<tr>${cols.map(c => `<th class="py-1 px-2 font-medium text-left whitespace-nowrap">${esc(c)}</th>`).join('')}</tr>`;
    tbody.innerHTML = rows.map((row, n) => `
      <tr data-testid="preview-row-${n}" class="hover:bg-gray-50">
        ${cols.map(c => `<td class="py-1 px-2 font-mono whitespace-nowrap max-w-xs truncate cursor-pointer hover:text-primary transition-colors" data-full="${esc(cellStr(row[c] ?? ''))}" onclick="window.DB._showCellPopover(this)">${statusCellHtml(c, row[c] ?? '')}</td>`).join('')}
      </tr>`).join('');

    loadMore.classList.toggle('hidden', rows.length < _previewLimit);
    const li2 = document.getElementById('limit-input'); if (li2) li2.value = _previewLimit;
  }

  function onLimitChange(value) {
    const n = Math.max(1, Math.min(100000, parseInt(value) || 50));
    _previewLimit = n;
    const li = document.getElementById('limit-input');
    if (li) li.value = n;
    loadPreview();
  }

  // ── Load preview ───────────────────────────────────────────────────────────
  const _PREVIEW_FILTER_CONTROL_IDS = [
    'predicate-filter-input', 'filter-advanced-btn', 'time-range-select',
    'custom-start-input', 'custom-end-input', 'limit-input', 'load-more-btn',
  ];

  function setPreviewLoading(loading) {
    _previewLoading = loading;
    const indicator = document.getElementById('preview-loading-indicator');
    if (indicator) indicator.classList.toggle('hidden', !loading);
    const tableWrap = document.getElementById('preview-table-wrap');
    if (tableWrap) tableWrap.classList.toggle('opacity-50', loading);
    for (const id of _PREVIEW_FILTER_CONTROL_IDS) {
      const el = document.getElementById(id);
      if (el) el.disabled = loading;
    }
    // time-range-select's enabled state depends on whether a time field is set,
    // not just on loading — recompute it rather than force-enabling above.
    if (!loading) enableTimeRangeSelect(!!_filterState.time_field);
  }

  async function loadPreview() {
    if (!_activeId) return;
    if (_previewLoading) return; // a request is already in flight — ignore until it resolves
    const { valid } = validatePredicate(_filterState.query);
    if (!valid) return;

    setPreviewLoading(true);
    try {
      const body = {
        query: _filterState.query,
        limit: _previewLimit,
        time_field: _filterState.time_field || undefined,
        start: _filterState.start || undefined,
        end: _filterState.end || undefined,
      };
      const data = await api('POST', `/api/v1/connections/${_activeId}/preview`, body);
      _previewRows = data.results || [];
      _totalCount = data.total_count || 0;
      if (data.schema_fields && Object.keys(data.schema_fields).length && !_schema) {
        _schema = data.schema_fields;
      }
      if (_schema) {
        if (!_filterState.time_field) {
          const best = _bestTimestampField();
          if (best) {
            _filterState.time_field = best;
            renderTimeFieldBadge(best);
            enableTimeRangeSelect(true);
          }
        }
        renderSchemaSection(_schema);
      }
      renderPreviewTable(_previewRows);
      document.getElementById('total-rows-display').textContent =
        _totalCount > 0 ? `TOTAL: ${_totalCount.toLocaleString()} ROWS` : 'TOTAL: —';
    } catch (e) {
      showError('Preview failed: ' + e.message);
    } finally {
      setPreviewLoading(false);
    }
    updateClearAllVisibility();
  }

  async function loadMoreRows() {
    _previewLimit = Math.min(_previewLimit * 2, 100000);
    await loadPreview();
  }

  // ── Clear All ──────────────────────────────────────────────────────────────
  function updateClearAllVisibility() {
    const btn = document.getElementById('clear-all-btn');
    const hasFilter = _filterState.query || _filterState.start || _filterRules.length;
    btn.classList.toggle('hidden', !hasFilter);
  }

  function clearAll() {
    _filterState = { query: '', start: null, end: null, time_field: _filterState.time_field };
    _filterRules = [];
    _previewLimit = 50;
    const input = document.getElementById('predicate-filter-input');
    if (input) input.value = '';
    const sel = document.getElementById('time-range-select');
    if (sel) sel.value = '';
    updateClearAllVisibility();
    loadPreview();
  }

  // ── Health badge + last synced ─────────────────────────────────────────────
  function updateHealthBadge(text, state) {
    const badge = document.getElementById('health-badge');
    badge.textContent = text;
    badge.className = `health-badge health-badge--${state} font-label text-xs px-2 py-0.5 rounded-full`;
  }

  function updateLastSynced(text) {
    const el = document.getElementById('last-synced-label');
    el.textContent = text;
  }

  // ── Cell value popover ────────────────────────────────────────────────────
  function _ensureCellPopover() {
    if (!_cellPopover) {
      _cellPopover = document.createElement('div');
      _cellPopover.className = 'hidden fixed z-[9999] bg-surface-container-lowest border border-outline-variant/20 rounded-xl shadow-xl p-4 max-w-sm w-max max-h-64 overflow-y-auto no-scrollbar';
      document.body.appendChild(_cellPopover);
    }
    return _cellPopover;
  }

  function _prettyPrintIfJson(text) {
    const trimmed = text.trim();
    if (trimmed[0] !== '{' && trimmed[0] !== '[') return null;
    try {
      return JSON.stringify(JSON.parse(trimmed), null, 2);
    } catch {
      return null;
    }
  }

  // Tokenizes already-pretty-printed JSON into <span>-wrapped, syntax-highlighted HTML.
  // Escapes &/</> first (not quotes) so the string-matching regex below still sees literal " chars.
  function _highlightJson(json) {
    const escaped = json.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    return escaped.replace(
      /("(?:\\u[a-fA-F0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\btrue\b|\bfalse\b|\bnull\b|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/g,
      (match) => {
        let cls = 'jn';
        if (/^"/.test(match)) cls = /:$/.test(match) ? 'jk' : 'js';
        else if (match === 'true' || match === 'false') cls = 'jb';
        else if (match === 'null') cls = 'jz';
        return `<span class="${cls}">${match}</span>`;
      }
    );
  }

  const CELL_POPOVER_COMPACT_CLASSES = ['max-w-sm', 'w-max', 'max-h-64'];
  const CELL_POPOVER_EXPANDED_CLASSES = ['inset-8', 'w-auto', 'h-auto', 'max-w-none', 'max-h-none'];

  function _showCellPopover(td) {
    const text = td.dataset.full ?? '';
    const pop = _ensureCellPopover();
    _cellPopoverTd = td;
    _cellPopoverExpanded = false;
    pop.classList.remove(...CELL_POPOVER_EXPANDED_CLASSES);
    pop.classList.add(...CELL_POPOVER_COMPACT_CLASSES);

    const pretty = _prettyPrintIfJson(text);
    const body = pretty != null
      ? `<pre class="text-xs font-mono whitespace-pre-wrap break-all select-text json-pretty">${_highlightJson(pretty)}</pre>`
      : `<pre class="text-xs font-mono text-on-surface whitespace-pre-wrap break-all select-text">${esc(text)}</pre>`;

    pop.innerHTML = `
      <div class="flex items-center justify-between gap-4 mb-2">
        <span class="text-[10px] font-label font-bold uppercase tracking-widest text-on-surface-variant">Full Value</span>
        <div class="flex items-center gap-1">
          <button onmousedown="event.preventDefault(); window.DB._toggleCellPopoverExpand()" class="text-on-surface-variant hover:text-on-surface" aria-label="Expand">
            <span class="material-symbols-outlined text-[16px] cp-expand-icon">open_in_full</span>
          </button>
          <button onmousedown="event.preventDefault(); window.DB._hideCellPopover()" class="text-on-surface-variant hover:text-on-surface" aria-label="Close">
            <span class="material-symbols-outlined text-[16px]">close</span>
          </button>
        </div>
      </div>
      ${body}`;

    pop.classList.remove('hidden');
    _positionCellPopover(td);
  }

  function _positionCellPopover(td) {
    const pop = _cellPopover;
    if (!pop) return;
    const rect = td.getBoundingClientRect();
    const popW = Math.min(384, window.innerWidth - 32);
    pop.style.maxWidth = popW + 'px';
    const left = Math.min(rect.left, window.innerWidth - popW - 8);
    const spaceBelow = window.innerHeight - rect.bottom - 8;
    const spaceAbove = rect.top - 8;
    const popH = pop.offsetHeight;
    const top = spaceBelow >= popH || spaceBelow >= spaceAbove
      ? rect.bottom + 4
      : rect.top - popH - 4;

    pop.style.left = Math.max(8, left) + 'px';
    pop.style.top = Math.max(8, top) + 'px';
  }

  function _toggleCellPopoverExpand() {
    const pop = _cellPopover;
    if (!pop) return;
    _cellPopoverExpanded = !_cellPopoverExpanded;
    const icon = pop.querySelector('.cp-expand-icon');

    if (_cellPopoverExpanded) {
      pop.classList.remove(...CELL_POPOVER_COMPACT_CLASSES);
      pop.classList.add(...CELL_POPOVER_EXPANDED_CLASSES);
      pop.style.left = '';
      pop.style.top = '';
      pop.style.maxWidth = '';
      if (icon) icon.textContent = 'close_fullscreen';
    } else {
      pop.classList.remove(...CELL_POPOVER_EXPANDED_CLASSES);
      pop.classList.add(...CELL_POPOVER_COMPACT_CLASSES);
      if (icon) icon.textContent = 'open_in_full';
      if (_cellPopoverTd) _positionCellPopover(_cellPopoverTd);
    }
  }

  function _hideCellPopover() {
    _cellPopover?.classList.add('hidden');
    _cellPopoverExpanded = false;
  }

  // ── Field Picker combobox ──────────────────────────────────────────────────
  function _getSchemaFieldNames() {
    return _schema ? Object.keys(_schema) : [];
  }

  function _ensureFpDropdown() {
    if (!_fpDropdown) {
      _fpDropdown = document.createElement('div');
      _fpDropdown.className = 'hidden fixed z-[9999] bg-surface-container-lowest border border-outline-variant/20 rounded-xl shadow-lg max-h-48 overflow-y-auto no-scrollbar py-1';
      document.body.appendChild(_fpDropdown);
    }
    return _fpDropdown;
  }

  function _positionFpDropdown(inputEl) {
    const dd = _ensureFpDropdown();
    const rect = inputEl.getBoundingClientRect();
    dd.style.top = (rect.bottom + 4) + 'px';
    dd.style.left = rect.left + 'px';
    dd.style.width = Math.max(rect.width, 160) + 'px';
  }

  function _renderFpOptions() {
    const dd = _ensureFpDropdown();
    if (!_fpActiveInput) return;

    let query;
    if (_fpIsMulti) {
      const parts = _fpActiveInput.value.split(',');
      query = (parts[parts.length - 1] || '').trim().toLowerCase();
    } else {
      query = _fpActiveInput.value.toLowerCase();
    }

    const fields = _getSchemaFieldNames().filter(f => !query || f.toLowerCase().includes(query));
    if (!fields.length) {
      dd.innerHTML = '<p class="text-xs text-on-surface-variant/50 px-3 py-2 italic">No schema fields available</p>';
    } else {
      dd.innerHTML = fields.map(f =>
        `<button type="button" data-field="${esc(f)}"
          class="w-full text-left px-3 py-1.5 text-xs font-mono hover:bg-surface-container-low transition-colors text-on-surface"
          onmousedown="event.preventDefault(); window.DB._onFieldPickerSelect(this.dataset.field)">${esc(f)}</button>`
      ).join('');
    }
  }

  function _onFieldPickerFocus(inputEl, isMulti) {
    _fpActiveInput = inputEl;
    _fpIsMulti = isMulti;
    _positionFpDropdown(inputEl);
    _renderFpOptions();
    _ensureFpDropdown().classList.remove('hidden');
  }

  function _onFieldPickerInput(inputEl) {
    _fpActiveInput = inputEl;
    _renderFpOptions();
    const dd = _ensureFpDropdown();
    if (dd.classList.contains('hidden')) {
      _positionFpDropdown(inputEl);
      dd.classList.remove('hidden');
    }
  }

  function _onFieldPickerSelect(field) {
    if (!_fpActiveInput) return;
    if (_fpIsMulti) {
      const parts = _fpActiveInput.value.split(',').map(s => s.trim()).filter(Boolean);
      const lastPart = parts[parts.length - 1] || '';
      if (lastPart && field.toLowerCase().includes(lastPart.toLowerCase())) parts.pop();
      parts.push(field);
      _fpActiveInput.value = parts.join(', ');
    } else {
      _fpActiveInput.value = field;
    }
    _fpActiveInput.dispatchEvent(new Event('input', { bubbles: true }));
    _ensureFpDropdown().classList.add('hidden');
    _fpActiveInput.focus();
  }

  function _onFieldPickerBlur(inputEl) {
    setTimeout(() => {
      if (_fpActiveInput === inputEl) {
        _ensureFpDropdown().classList.add('hidden');
      }
    }, 200);
  }

  // ── Data Masking Card ──────────────────────────────────────────────────────
  function onMaskingToggle(checked) {
    document.getElementById('masking-body').classList.toggle('hidden', !checked);
    document.getElementById('masking-hint').classList.toggle('hidden', checked);
    if (!checked) _maskingRules = [];
  }

  function addMaskingRule() {
    _maskingRules.push({ field_path: '', action: 'mask' });
    renderMaskingRulesTable();
  }

  function removeMaskingRule(n) {
    _maskingRules.splice(n, 1);
    renderMaskingRulesTable();
  }

  function onMaskingRuleChange(n, key, value) {
    _maskingRules[n][key] = value;
  }

  function renderMaskingRulesTable() {
    const tbody = document.getElementById('masking-rules-tbody');
    tbody.innerHTML = _maskingRules.map((r, n) => `
      <tr class="border-b border-outline-variant/10" data-testid="masking-rule-${n}">
        <td class="p-3">
          <input type="text" value="${esc(r.field_path)}" placeholder="select or type field"
            class="w-full bg-surface-container-low border-none rounded-lg px-3 py-1.5 text-xs font-mono text-primary focus:ring-1 focus:ring-primary/20"
            autocomplete="off"
            onfocus="window.DB._onFieldPickerFocus(this, false)"
            oninput="window.DB._onMaskingRuleChange(${n},'field_path',this.value); window.DB._onFieldPickerInput(this)"
            onblur="window.DB._onFieldPickerBlur(this)" />
        </td>
        <td class="p-3">
          <div class="relative">
            <select class="w-full bg-surface-container-low border-none rounded-lg px-3 py-1.5 text-xs font-medium appearance-none focus:ring-1 focus:ring-primary/20 pr-6"
              onchange="window.DB._onMaskingRuleChange(${n},'action',this.value)">
              <option value="mask" ${r.action==='mask'?'selected':''}>Mask</option>
              <option value="hash" ${r.action==='hash'?'selected':''}>Hash</option>
              <option value="drop" ${r.action==='drop'?'selected':''}>Drop</option>
              <option value="redact" ${r.action==='redact'?'selected':''}>Redact</option>
            </select>
            <span class="absolute right-2 top-1/2 -translate-y-1/2 material-symbols-outlined text-[14px] text-outline-variant pointer-events-none">expand_more</span>
          </div>
        </td>
        <td class="p-3 w-8">
          <button class="text-error/60 hover:text-error text-xs transition-colors"
            onclick="window.DB._removeMaskingRule(${n})">✕</button>
        </td>
      </tr>`).join('');
  }

  async function onPiiAutoDetect(checked) {
    if (!checked || !_activeId) return;
    try {
      const data = await api('GET', `/api/v1/connections/${_activeId}/pii-fields`);
      const fields = data.candidate_fields || [];
      fields.forEach(f => {
        if (!_maskingRules.some(r => r.field_path === f)) {
          _maskingRules.push({ field_path: f, action: 'mask' });
        }
      });
      renderMaskingRulesTable();
      if (fields.length === 0) showSuccess('No PII candidate fields found.');
    } catch (e) {
      showError('PII detection failed: ' + e.message);
    }
  }

  function onApplyVisibilityFilter(checked) {
    if (!checked || !_schema) return;
    const allFields = Object.keys(_schema);
    // null _visibleColumns means all visible — nothing to deny
    if (!_visibleColumns || _visibleColumns.size === allFields.length) {
      showSuccess('No hidden columns — all fields are visible.');
      document.getElementById('visibility-filter-toggle').checked = false;
      return;
    }
    const hidden = allFields.filter(f => !_visibleColumns.has(f));
    hidden.forEach(f => {
      if (!_maskingRules.some(r => r.field_path === f)) {
        _maskingRules.push({ field_path: f, action: 'drop' });
      }
    });
    renderMaskingRulesTable();
    if (hidden.length === 0) showSuccess('No hidden columns to apply.');
  }

  // ── Sampling Strategy Card ─────────────────────────────────────────────────
  const SAMPLING_DESCS = {
    random: 'Uniformly samples records at random.',
    systematic: 'Selects every Nth record in sequence.',
    stratified: 'Maintains population subgroup proportions by target column.',
  };

  function onSamplingToggle(checked) {
    document.getElementById('sampling-body').classList.toggle('hidden', !checked);
    document.getElementById('sampling-hint').classList.toggle('hidden', checked);
    if (!checked) _samplingConfig = null;
    else onSamplingConfigChange();
  }

  function onSamplingMethodChange(method) {
    document.getElementById('sampling-method-desc').textContent = SAMPLING_DESCS[method] || '';
    document.getElementById('sampling-target-col-row').classList.toggle('hidden', method !== 'stratified');
    const isStratified = method === 'stratified';
    document.getElementById('stratified-info-btn').classList.toggle('hidden', !isStratified);
    if (!isStratified) document.getElementById('stratified-info-popup').classList.add('hidden');
    onSamplingConfigChange();
  }

  function toggleStratifiedInfo() {
    document.getElementById('stratified-info-popup').classList.toggle('hidden');
  }

  function onSamplingConfigChange() {
    const method = document.getElementById('sampling-method-select')?.value || 'random';
    const ratio = parseFloat(document.getElementById('sampling-ratio')?.value || '0.1');
    const target = document.getElementById('sampling-target-column')?.value || null;
    const maxItemsRaw = document.getElementById('sampling-max-items')?.value;
    const max_items = maxItemsRaw ? parseInt(maxItemsRaw, 10) : null;
    _samplingConfig = { method, ratio_or_size: ratio, target_column: target || null, max_items };
  }

  // ── Export destination ─────────────────────────────────────────────────────
  async function loadDatasinks() {
    try {
      const data = await api('GET', '/api/v1/datasinks');
      _datasinks = data.datasinks || [];
      const sel = document.getElementById('datasink-select');
      sel.innerHTML = '<option value="">Select datasink…</option>' +
        _datasinks.map(s => `<option value="${esc(s.name)}">${esc(s.name)} (${esc(s.type)})</option>`).join('');
    } catch (e) {
      // Non-fatal
    }
  }

  async function onDatasinkChange(name) {
    const localSinkTypes = new Set(['local-zip', 'local-jsonl']);
    const sink = (_datasinks || []).find(s => s.name === name);
    const isLocal = !sink || localSinkTypes.has(sink.type);

    const textInput = document.getElementById('destination-dataset-input');
    const selectWrap = document.getElementById('destination-dataset-select-wrap');
    const sel = document.getElementById('destination-dataset-select');

    if (isLocal) {
      textInput.classList.remove('hidden');
      selectWrap.classList.add('hidden');
      return;
    }

    textInput.classList.add('hidden');
    selectWrap.classList.remove('hidden');
    sel.innerHTML = '<option value="">Loading…</option>';
    document.getElementById('destination-dataset-new-input').classList.add('hidden');
    try {
      const data = await api('GET', `/api/v1/datasinks/${encodeURIComponent(name)}/datasets`);
      const datasets = data.datasets || [];
      sel.innerHTML = '<option value="">Select dataset…</option>' +
        datasets.map(d => {
          const label = d.uid !== d.name
            ? `${esc(d.name)} · ${esc(d.uid.slice(0, 8))}`
            : esc(d.name);
          return `<option value="${esc(d.uid)}">${label}</option>`;
        }).join('') +
        '<option value="__new__">＋ Create new…</option>';
    } catch (e) {
      sel.innerHTML = '<option value="">Failed to load datasets</option>' +
        '<option value="__new__">＋ Create new…</option>';
    }
  }

  function onDestinationDatasetSelectChange(value) {
    const newInput = document.getElementById('destination-dataset-new-input');
    if (value === '__new__') {
      newInput.classList.remove('hidden');
      newInput.focus();
    } else {
      newInput.classList.add('hidden');
      newInput.value = '';
    }
  }

  function onDatasetNameChange(value) {
    const assetInput = document.getElementById('asset-dataset-input');
    if (!assetInput) return;
    // Only auto-fill if the user hasn't manually edited the asset dataset field
    if (!assetInput.dataset.userEdited) {
      assetInput.value = value ? `${value}_assets` : '';
    }
  }

  function onAssetResolutionToggle(checked) {
    _assetResolution = checked;
    document.getElementById('asset-url-fields-section').classList.toggle('hidden', !checked);
    document.getElementById('asset-resolution-hint').classList.toggle('hidden', checked);
    if (!checked) document.getElementById('asset-resolution-results').classList.add('hidden');
  }

  async function testAssetResolution() {
    if (!_activeId) { showError('Select a connection first.'); return; }
    const fieldsRaw = document.getElementById('asset-url-fields-input')?.value || '';
    const urlFields = fieldsRaw.split(',').map(s => s.trim()).filter(Boolean);
    if (!urlFields.length) { showError('Enter at least one URL field.'); return; }
    const urlPrefix = document.getElementById('asset-url-prefix-input')?.value || '';

    const btn = document.getElementById('test-asset-resolution-btn');
    const resultsEl = document.getElementById('asset-resolution-results');
    const bodyEl = document.getElementById('asset-resolution-results-body');
    btn.disabled = true;
    btn.innerHTML = '<span class="material-symbols-outlined text-[16px] animate-spin">progress_activity</span> Testing…';

    try {
      const data = await api('POST', `/api/v1/connections/${_activeId}/test-asset-resolution`, {
        url_fields: urlFields,
        url_prefix: urlPrefix,
      });
      const results = data.results || [];
      if (!results.length) {
        bodyEl.innerHTML = '<p class="px-4 py-3 text-xs text-on-surface-variant/60">No URL values found in the first few records for the specified fields.</p>';
      } else {
        bodyEl.innerHTML = results.map(r => `
          <div class="flex items-start gap-3 px-4 py-3 border-b border-outline-variant/10 last:border-0">
            <span class="mt-0.5 material-symbols-outlined text-[18px] flex-shrink-0 ${r.ok ? 'text-green-500' : 'text-error'}">${r.ok ? 'check_circle' : 'cancel'}</span>
            <div class="min-w-0 flex-1 space-y-0.5">
              <p class="text-[10px] font-label font-bold text-on-surface-variant uppercase tracking-widest">${esc(r.field)}</p>
              <p class="text-xs font-mono text-primary break-all">${esc(r.resolved_url)}</p>
              ${r.raw_value !== r.resolved_url ? `<p class="text-[10px] text-on-surface-variant/50">raw: ${esc(r.raw_value)}</p>` : ''}
              ${r.status_code != null ? `<p class="text-[10px] font-mono ${r.ok ? 'text-green-600' : 'text-error'}">HTTP ${r.status_code}</p>` : ''}
              ${r.error ? `<p class="text-[10px] text-error">${esc(r.error)}</p>` : ''}
            </div>
          </div>`).join('');
      }
      resultsEl.classList.remove('hidden');
    } catch (e) {
      showError('Test failed: ' + e.message);
    } finally {
      btn.disabled = false;
      btn.innerHTML = '<span class="material-symbols-outlined text-[16px]">play_arrow</span> Test Resolution';
    }
  }

  async function startExport() {
    if (!_activeId) { showError('Select a connection first.'); return; }
    const datasinkName = document.getElementById('datasink-select')?.value;
    if (!datasinkName) { showError('Select a datasink.'); return; }
    const selectWrap = document.getElementById('destination-dataset-select-wrap');
    const useSelect = selectWrap && !selectWrap.classList.contains('hidden');
    let dest;
    if (useSelect) {
      const selVal = document.getElementById('destination-dataset-select')?.value;
      if (selVal === '__new__') {
        dest = document.getElementById('destination-dataset-new-input')?.value?.trim();
        if (!dest) { showError('Enter a name for the new dataset.'); return; }
      } else {
        dest = selVal;
      }
    } else {
      dest = document.getElementById('destination-dataset-input')?.value;
    }
    if (!dest) { showError(useSelect ? 'Select a destination dataset.' : 'Enter a destination dataset name.'); return; }

    // Collect asset URL fields if asset resolution enabled
    let assetUrlFields = _assetUrlFields;
    let assetUrlPrefix = _assetUrlPrefix;
    let assetDataset = null;
    if (_assetResolution) {
      const fieldsInput = document.getElementById('asset-url-fields-input')?.value || '';
      assetUrlFields = fieldsInput.split(',').map(s => s.trim()).filter(Boolean);
      assetUrlPrefix = document.getElementById('asset-url-prefix-input')?.value || '';
      assetDataset = document.getElementById('asset-dataset-input')?.value || null;
    }

    const body = {
      datasource_type: _activeType,
      datasource_ref: _activeId,
      datasource_filter: {
        query: _filterState.query,
        start: _filterState.start,
        end: _filterState.end,
        time_field: _filterState.time_field,
        limit: _previewLimit,
      },
      datasink_name: datasinkName,
      destination_dataset: dest,
      asset_resolution: _assetResolution,
      asset_url_fields: assetUrlFields,
      asset_url_prefix: assetUrlPrefix,
      asset_dataset: assetDataset || null,
      masking_rules: document.getElementById('masking-toggle')?.checked ? _maskingRules : [],
      sampling_config: document.getElementById('sampling-toggle')?.checked ? _samplingConfig : null,
      webhook_url: _webhookConfig.url || null,
      webhook_enabled: _webhookConfig.enabled,
      webhook_payload_template: _webhookConfig.payloadTemplate || null,
    };

    try {
      await api('POST', '/api/v1/export-jobs', body);
      showSuccess('Export job started!');
      switchTab('jobs');
    } catch (e) {
      showError('Export failed: ' + e.message);
    }
  }

  // ── Webhook ────────────────────────────────────────────────────────────────
  function _renderWebhookAllowedPrefixes() {
    const el = document.getElementById('webhook-allowed-prefixes');
    if (!el) return;
    if (!_webhookAllowedPrefixes.length) { el.classList.add('hidden'); return; }
    const chips = _webhookAllowedPrefixes.map(p => {
      let host;
      try { host = new URL(p).hostname; } catch { host = p; }
      return `<span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-lg bg-primary/10 text-primary text-[11px] font-mono">${esc(host)}</span>`;
    }).join('');
    el.innerHTML = `<span class="material-symbols-outlined text-[14px] text-on-surface-variant/60">lock</span><span class="text-[11px] text-on-surface-variant/60">Allowed:</span>${chips}`;
    el.classList.remove('hidden');
  }

  function _webhookUrlError(url) {
    if (!_webhookAllowedPrefixes.length) return null;
    if (!url) return null;
    return _webhookAllowedPrefixes.some(p => url.startsWith(p)) ? null
      : 'URL must start with one of the allowed hostnames shown above';
  }

  function onWebhookToggle(checked) {
    _webhookConfig.enabled = checked;
    document.getElementById('webhook-body').classList.toggle('hidden', !checked);
    document.getElementById('webhook-hint').classList.toggle('hidden', checked);
  }

  function onWebhookUrlChange(value) {
    _webhookConfig.url = value;
    const errEl = document.getElementById('webhook-url-error');
    if (!errEl) return;
    const msg = _webhookUrlError(value);
    errEl.textContent = msg || '';
    errEl.classList.toggle('hidden', !msg);
  }

  function onWebhookPayloadChange(value) { _webhookConfig.payloadTemplate = value; }

  async function testWebhook() {
    const url = document.getElementById('webhook-url-input')?.value;
    if (!url) { showError('Enter a webhook URL first.'); return; }
    const err = _webhookUrlError(url);
    if (err) { showError(err); return; }
    const template = _webhookConfig.payloadTemplate || null;
    try {
      await api('POST', '/api/v1/export-jobs/test-webhook', { url, template });
      showSuccess('Webhook test sent successfully.');
    } catch (e) {
      showError('Webhook test failed: ' + e.message);
    }
  }

  // ── Jobs View ──────────────────────────────────────────────────────────────
  const JOB_STATUS_CLASS = {
    pending:   'status-badge status-badge--pending',
    running:   'status-badge status-badge--running',
    completed: 'status-badge status-badge--completed',
    failed:    'status-badge status-badge--failed',
    cancelled: 'status-badge status-badge--cancelled',
  };

  async function renderJobsView() {
    clearInterval(_jobPollTimer);
    await _fetchAndRenderJobs();
    _jobPollTimer = setInterval(_fetchAndRenderJobs, 3000);
  }

  async function _fetchAndRenderJobs() {
    try {
      const data = await api('GET', '/api/v1/export-jobs');
      const jobs = data.items || [];
      const list = document.getElementById('jobs-list');
      const empty = document.getElementById('jobs-empty-msg');

      if (!jobs.length) {
        empty.classList.remove('hidden');
        return;
      }
      empty.classList.add('hidden');

      // Determine local sink job ids for download buttons
      const localSinkTypes = new Set(['local-zip', 'local-jsonl']);
      const localSinkNames = new Set(
        (_datasinks || []).filter(s => localSinkTypes.has(s.type)).map(s => s.name)
      );

      // Only re-render if something changed (avoid flicker)
      const html = jobs.map(job => {
        const statusCls = JOB_STATUS_CLASS[job.status] || 'status-badge';
        const dlUrl = job.download_url || (localSinkNames.has(job.datasink_name) ? `/api/v1/export-jobs/${esc(job.id)}/download` : '');
        const dlBtn = (dlUrl && job.status === 'completed')
          ? `<a href="${esc(dlUrl)}"
               class="text-xs text-indigo-600 hover:underline"
               data-testid="job-download-btn-${esc(job.id)}">Download</a>`
          : '';
        const assetsDlBtn = (job.assets_download_url && job.status === 'completed')
          ? `<a href="${esc(job.assets_download_url)}"
               class="text-xs text-indigo-600 hover:underline"
               data-testid="job-assets-download-btn-${esc(job.id)}">Assets</a>`
          : '';
        const retryBtn = job.status === 'failed'
          ? `<button class="text-xs text-orange-600 hover:underline font-medium"
               data-testid="job-retry-btn-${esc(job.id)}"
               onclick="window.DB._retryJob('${esc(job.id)}')">Retry</button>`
          : '';
        const cancelBtn = (job.status === 'pending' || job.status === 'running')
          ? `<button class="text-xs text-gray-500 hover:text-error hover:underline font-medium"
               data-testid="job-cancel-btn-${esc(job.id)}"
               onclick="window.DB._cancelJob('${esc(job.id)}')">Cancel</button>`
          : '';
        const pulseDot = job.status === 'running'
          ? '<span class="dot-pulse inline-block w-2 h-2 rounded-full bg-blue-500 mr-1"></span>'
          : '';
        return `
<div class="job-row p-5 flex items-center gap-4"
     data-testid="job-row-${esc(job.id)}">
  <div class="flex-1 min-w-0">
    <div class="flex items-center gap-2 flex-wrap mb-1.5">
      ${pulseDot}<span class="${statusCls} font-label" data-testid="job-status-${esc(job.id)}">${esc(job.status.toUpperCase())}</span>
      <span class="text-xs font-mono text-primary" data-testid="job-source-${esc(job.id)}">${esc(job.datasource_ref)}</span>
      <span class="material-symbols-outlined text-[14px] text-on-surface-variant/40">arrow_forward</span>
      <span class="text-xs font-mono text-on-surface-variant" data-testid="job-sink-${esc(job.id)}">${esc(job.datasink_name)}</span>
    </div>
    <div class="text-[11px] text-on-surface-variant/60 font-label" data-testid="job-progress-${esc(job.id)}">
      ${job.records_processed} / ${job.records_total ?? '?'} records
      ${job.destination_dataset ? '&nbsp;·&nbsp;' + esc(job.destination_dataset) : ''}
    </div>
  </div>
  <div class="flex items-center gap-3 shrink-0">
    ${dlBtn}
    ${assetsDlBtn}
    ${retryBtn}
    ${cancelBtn}
    <span class="text-[11px] text-on-surface-variant/40 font-label">${new Date(job.created_at).toLocaleString()}</span>
  </div>
</div>`;
      }).join('');

      list.innerHTML = html + document.getElementById('jobs-empty-msg').outerHTML;
    } catch (e) {
      // Silently ignore polling errors
    }
  }

  async function _retryJob(id) {
    try {
      await api('POST', `/api/v1/export-jobs/${id}/retry`);
      showSuccess('Retry job started.');
      _fetchAndRenderJobs();
    } catch (e) {
      showError('Retry failed: ' + e.message);
    }
  }

  async function _cancelJob(id) {
    try {
      await api('POST', `/api/v1/export-jobs/${id}/cancel`);
      showSuccess('Job cancelled.');
      _fetchAndRenderJobs();
    } catch (e) {
      showError('Cancel failed: ' + e.message);
    }
  }

  // ── Connection modal ───────────────────────────────────────────────────────
  const CRED_FIELDS = {
    s3: [
      { key: 'access_key_id', label: 'Access Key ID', type: 'text' },
      { key: 'secret_access_key', label: 'Secret Access Key', type: 'password' },
      { key: 'bucket', label: 'Bucket', type: 'text' },
      { key: 'region', label: 'Region', type: 'text', placeholder: 'us-east-1' },
      { key: 'key_prefix', label: 'Key Prefix', type: 'text', placeholder: 'optional' },
    ],
    clickhouse: [
      { key: 'user', label: 'User', type: 'text' },
      { key: 'password', label: 'Password', type: 'password' },
      { key: 'database', label: 'Database', type: 'text', placeholder: 'default' },
      { key: 'table', label: 'Table', type: 'text', placeholder: 'llogr_events' },
    ],
    trino: [
      { key: 'user', label: 'User', type: 'text' },
      { key: 'password', label: 'Password', type: 'password' },
      { key: 'catalog', label: 'Catalog', type: 'text' },
      { key: 'schema_name', label: 'Schema', type: 'text' },
    ],
    langfuse: [
      { key: 'public_key', label: 'Public Key', type: 'text' },
      { key: 'secret_key', label: 'Secret Key', type: 'password' },
    ],
    dataset: [
      { key: 'api_token', label: 'API Token', type: 'password', placeholder: 'optional' },
    ],
  };

  function openModal() {
    _editingId = null;
    document.getElementById('modal-title').textContent = 'Add Connection';
    document.getElementById('conn-form').reset();
    updateCredFields();
    document.getElementById('conn-modal').classList.remove('hidden');
    document.getElementById('conn-label-input').focus();
  }

  async function openEditModal(id) {
    const conn = _connections.find(c => c.id === id);
    if (!conn) return;
    _editingId = id;
    document.getElementById('modal-title').textContent = 'Edit Connection';
    document.getElementById('conn-label-input').value = conn.label;
    const typeSelect = document.getElementById('conn-type-select');
    typeSelect.value = conn.type;
    document.getElementById('conn-role-select').value = conn.role;
    document.getElementById('conn-url-input').value = conn.connection_url;
    updateCredFields();
    document.getElementById('conn-modal').classList.remove('hidden');

    try {
      const creds = await api('GET', `/api/v1/connections/${id}/credentials`);
      const fields = CRED_FIELDS[conn.type] || [];
      fields.forEach(f => {
        const el = document.getElementById('cred-' + f.key);
        if (el && creds[f.key] != null) el.value = creds[f.key];
      });
    } catch (_) {}
  }

  function closeModal() {
    document.getElementById('conn-modal').classList.add('hidden');
    _editingId = null;
  }

  function updateCredFields() {
    const type = document.getElementById('conn-type-select')?.value;
    const fields = CRED_FIELDS[type] || [];
    const hide = _config.hide_auth_inputs;
    document.getElementById('cred-fields').innerHTML = hide ? '' : fields.map(f => `
      <div>
        <label class="text-[10px] font-label font-bold uppercase tracking-widest text-on-surface-variant block mb-1.5">${esc(f.label)}</label>
        <input id="cred-${esc(f.key)}" name="${esc(f.key)}" type="${esc(f.type)}"
          class="w-full border border-outline-variant/30 bg-surface-container-low rounded-xl px-4 py-2.5 text-sm focus:border-primary/50 focus:ring-0"
          placeholder="${esc(f.placeholder || '')}" autocomplete="off" />
      </div>`).join('');
  }

  function _gatherCredentials(type) {
    const fields = CRED_FIELDS[type] || [];
    const creds = {};
    fields.forEach(f => {
      const el = document.getElementById('cred-' + f.key);
      if (el) creds[f.key] = el.value;
    });
    return creds;
  }

  async function testConnection() {
    const type = document.getElementById('conn-type-select')?.value;
    const url = document.getElementById('conn-url-input')?.value;
    const creds = _gatherCredentials(type);
    try {
      const r = await api('POST', '/api/v1/connections/test', { type, connection_url: url, credentials: creds });
      if (r.status === 'unreachable') {
        showError(`Unreachable: ${r.error || 'connection refused'}`);
      } else if (r.auth_ok === false) {
        showError(`Reachable but credentials rejected: ${r.auth_error || 'authentication failed'}`);
      } else {
        showSuccess(`Reachable · credentials OK (${r.latency_ms}ms)`);
      }
    } catch (e) {
      showError('Test failed: ' + e.message);
    }
  }

  async function submitConnection() {
    const label = document.getElementById('conn-label-input')?.value;
    const type = document.getElementById('conn-type-select')?.value;
    const role = document.getElementById('conn-role-select')?.value;
    const url = document.getElementById('conn-url-input')?.value;
    const creds = _gatherCredentials(type);
    if (!label || !type || !url) { showError('Fill in all required fields.'); return; }

    try {
      if (_editingId) {
        await api('PATCH', `/api/v1/connections/${_editingId}`, { label, credentials: creds });
        showSuccess('Connection updated.');
      } else {
        await api('POST', '/api/v1/connections', { label, type, role, connection_url: url, credentials: creds });
        showSuccess('Connection created.');
      }
      closeModal();
      await loadConnections();
      renderConnectionTabBar();
    } catch (e) {
      showError('Save failed: ' + e.message);
    }
  }

  async function ping(id, isSystem) {
    try {
      const r = await api('POST', `/api/v1/connections/${id}/ping`);
      showSuccess(`${r.status} (${r.latency_ms}ms)`);
      if (!isSystem) {
        await loadConnections();
        renderConnectionTabBar();
      }
    } catch (e) {
      showError('Ping failed: ' + e.message);
    }
  }

  async function deleteConnection(id) {
    if (!confirm('Delete this connection?')) return;
    try {
      await api('DELETE', `/api/v1/connections/${id}`);
      if (_activeId === id) {
        _activeId = null;
        clearPreviewTable();
      }
      await loadConnections();
      renderConnectionTabBar();
      showSuccess('Connection deleted.');
    } catch (e) {
      showError('Delete failed: ' + e.message);
    }
  }

  // ── Load connections ───────────────────────────────────────────────────────
  async function loadConnections() {
    try {
      const data = await api('GET', '/api/v1/connections');
      _connections = (data.items || []).filter(i => !i.system);
      _systemSources = (data.items || []).filter(i => i.system);
      renderConnectionTabBar();
    } catch (e) {
      showError('Failed to load connections: ' + e.message);
    }
  }

  // ── Close field picker on outside click ───────────────────────────────────
  document.addEventListener('click', function (e) {
    if (_fpDropdown && !_fpDropdown.contains(e.target) && e.target !== _fpActiveInput) {
      _fpDropdown.classList.add('hidden');
    }
    if (_cellPopover && !_cellPopover.contains(e.target) && !e.target.closest('td[data-full]')) {
      if (window.getSelection().toString()) return; // preserve an in-progress text selection
      _cellPopover.classList.add('hidden');
    }
  });

  // ── Keyboard navigation (SC-004) ───────────────────────────────────────────
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') {
      if (_cellPopover && !_cellPopover.classList.contains('hidden')) { _hideCellPopover(); return; }
      const connModal = document.getElementById('conn-modal');
      if (!connModal.classList.contains('hidden')) { closeModal(); return; }
      const filterModal = document.getElementById('filter-modal');
      if (!filterModal.classList.contains('hidden')) { closeFilterModal(); return; }
      const dd = document.getElementById('column-picker-dropdown');
      if (!dd.classList.contains('hidden')) { dd.classList.add('hidden'); return; }
    }
    if (e.key === 'Enter') {
      const focused = document.activeElement;
      if (focused && focused.id === 'predicate-filter-input') {
        loadPreview();
      }
    }
    // Tab key: natural browser focus order (no override needed)
  });

  // ── Init ───────────────────────────────────────────────────────────────────
  async function init() {
    try {
      const cfg = await api('GET', '/api/v1/ui-config');
      _config = cfg;
      const typeSelect = document.getElementById('conn-type-select');
      if (typeSelect) {
        typeSelect.innerHTML = (_config.connection_types || [])
          .map(t => `<option value="${esc(t)}">${esc(t)}</option>`).join('');
        updateCredFields();
      }
      _webhookAllowedPrefixes = cfg.webhook_allowed_url_prefixes || [];
      _renderWebhookAllowedPrefixes();
    } catch (e) { /* ignore */ }

    const payloadEl = document.getElementById('webhook-payload-input');
    if (payloadEl) payloadEl.value = _DEFAULT_WEBHOOK_TEMPLATE;

    await loadConnections();
    await loadDatasinks();
  }

  // ── Public API ─────────────────────────────────────────────────────────────
  window.DB = {
    switchTab,
    openModal,
    closeModal,
    openEditModal,
    updateCredFields,
    testConnection,
    submitConnection,
    deleteConnection,
    ping,
    selectConnection,
    toggleSchemaCollapse,
    refreshSchema,
    toggleColumnPicker,
    _onColumnVisChange,
    _onColumnVisAllChange,
    cycleTimeField,
    setTimestampField,
    onTimeRangeChange,
    onCustomRangeChange,
    onPredicateInput,
    toggleAdvancedFilter,
    closeFilterModal,
    applyFilterModal,
    addFilterRule,
    _removeFilterRule: removeFilterRule,
    _onRuleChange: onRuleChange,
    loadPreview,
    onLimitChange,
    loadMoreRows,
    updateClearAllVisibility,
    clearAll,
    _showCellPopover,
    _hideCellPopover,
    _toggleCellPopoverExpand,
    _onFieldPickerFocus,
    _onFieldPickerInput,
    _onFieldPickerSelect,
    _onFieldPickerBlur,
    onMaskingToggle,
    addMaskingRule,
    _removeMaskingRule: removeMaskingRule,
    _onMaskingRuleChange: onMaskingRuleChange,
    onPiiAutoDetect,
    onApplyVisibilityFilter,
    onSamplingToggle,
    onSamplingMethodChange,
    toggleStratifiedInfo,
    onSamplingConfigChange,
    onAssetResolutionToggle,
    testAssetResolution,
    onDatasinkChange,
    onDestinationDatasetSelectChange,
    onDatasetNameChange,
    startExport,
    onWebhookToggle,
    onWebhookUrlChange,
    onWebhookPayloadChange,
    testWebhook,
    _retryJob,
    _cancelJob,
  };

  document.addEventListener('DOMContentLoaded', init);
})();

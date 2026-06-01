/* databridge browser SPA — all connection management logic */
'use strict';

(function () {
  // ── State ────────────────────────────────────────────────────────────────────
  let _config = { connection_types: [], hide_auth_inputs: false };
  let _connections = [];
  let _systemSources = [];
  let _activeId = null;      // connection ID selected for preview/schema
  let _editingId = null;     // connection ID being edited (null = creating new)

  // ── API helpers ──────────────────────────────────────────────────────────────
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

  // ── Toast helpers ─────────────────────────────────────────────────────────────
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

  // ── Credential field definitions ──────────────────────────────────────────────
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

  // ── Type badge colours ────────────────────────────────────────────────────────
  const TYPE_COLOURS = {
    s3: 'bg-yellow-100 text-yellow-800',
    clickhouse: 'bg-orange-100 text-orange-800',
    trino: 'bg-blue-100 text-blue-800',
    langfuse: 'bg-purple-100 text-purple-800',
    dataset: 'bg-green-100 text-green-800',
  };

  const STATUS_COLOURS = {
    untested:    'bg-gray-100 text-gray-600',
    reachable:   'bg-green-100 text-green-700',
    unreachable: 'bg-red-100 text-red-700',
  };

  function typeBadge(type) {
    const cls = TYPE_COLOURS[type] || 'bg-gray-100 text-gray-700';
    return `<span class="inline-block px-2 py-0.5 rounded text-xs font-medium ${cls}">${type}</span>`;
  }

  function statusBadge(id, status, isSystem) {
    const cls = STATUS_COLOURS[status] || 'bg-gray-100 text-gray-600';
    const idAttr = isSystem ? `sys-status-${id}` : `conn-status-${id}`;
    return `<span id="${idAttr}" class="inline-block px-2 py-0.5 rounded text-xs font-medium ${cls}" data-testid="${idAttr}">${status}</span>`;
  }

  // ── Render user connection card ───────────────────────────────────────────────
  function renderConnectionCard(conn) {
    const { id, label, type, role, connection_url, status, last_tested_at } = conn;
    const tested = last_tested_at ? new Date(last_tested_at).toLocaleString() : 'never';
    return `
<div id="conn-card-${id}" class="bg-white rounded-xl border border-gray-200 p-5 shadow-sm hover:shadow transition-shadow" data-testid="conn-card-${id}">
  <div class="flex items-start justify-between gap-3">
    <div class="min-w-0">
      <div class="flex items-center gap-2 flex-wrap">
        <span class="font-semibold text-gray-900 truncate">${escapeHtml(label)}</span>
        ${typeBadge(type)}
        <span class="text-xs text-gray-400 capitalize">${role}</span>
        ${statusBadge(id, status, false)}
      </div>
      <p class="text-xs text-gray-400 mt-1 truncate">${escapeHtml(connection_url)}</p>
      <p class="text-xs text-gray-300 mt-0.5">tested: ${tested}</p>
    </div>
    <div class="flex gap-1 shrink-0">
      <button id="conn-ping-btn-${id}"
        class="icon-btn text-indigo-600 hover:bg-indigo-50" title="Ping"
        data-testid="conn-ping-btn-${id}"
        onclick="window.DB.ping('${id}', false)">
        <span class="material-symbols-outlined">wifi_tethering</span>
      </button>
      <button id="conn-preview-btn-${id}"
        class="icon-btn text-teal-600 hover:bg-teal-50" title="Preview"
        data-testid="conn-preview-btn-${id}"
        onclick="window.DB.selectConnection('${id}')">
        <span class="material-symbols-outlined">preview</span>
      </button>
      <button id="conn-edit-btn-${id}"
        class="icon-btn text-gray-500 hover:bg-gray-100" title="Edit"
        data-testid="conn-edit-btn-${id}"
        onclick="window.DB.openEditModal('${id}')">
        <span class="material-symbols-outlined">edit</span>
      </button>
      <button id="conn-delete-btn-${id}"
        class="icon-btn text-red-500 hover:bg-red-50" title="Delete"
        data-testid="conn-delete-btn-${id}"
        onclick="window.DB.deleteConnection('${id}')">
        <span class="material-symbols-outlined">delete</span>
      </button>
    </div>
  </div>
</div>`;
  }

  // ── Render system source card ─────────────────────────────────────────────────
  function renderSystemSourceCard(src) {
    const { id, label, type, connection_url, status } = src;
    return `
<div id="sys-card-${id}" class="bg-gray-50 rounded-xl border border-gray-100 p-5 shadow-sm" data-testid="sys-card-${id}">
  <div class="flex items-start justify-between gap-3">
    <div class="min-w-0">
      <div class="flex items-center gap-2 flex-wrap">
        <span class="font-semibold text-gray-800 truncate">${escapeHtml(label)}</span>
        ${typeBadge(type)}
        <span class="text-xs text-gray-400 italic">system</span>
        ${statusBadge(id, status, true)}
      </div>
      <p class="text-xs text-gray-400 mt-1 truncate">${escapeHtml(connection_url)}</p>
    </div>
    <div class="flex gap-1 shrink-0">
      <button id="sys-ping-btn-${id}"
        class="icon-btn text-indigo-600 hover:bg-indigo-50" title="Ping"
        data-testid="sys-ping-btn-${id}"
        onclick="window.DB.ping('${id}', true)">
        <span class="material-symbols-outlined">wifi_tethering</span>
      </button>
      <button id="sys-preview-btn-${id}"
        class="icon-btn text-teal-600 hover:bg-teal-50" title="Preview"
        data-testid="sys-preview-btn-${id}"
        onclick="window.DB.selectConnection('${id}')">
        <span class="material-symbols-outlined">preview</span>
      </button>
    </div>
  </div>
</div>`;
  }

  // ── Connections list view ─────────────────────────────────────────────────────
  function renderConnectionsList() {
    const list = document.getElementById('connections-list');
    const emptyState = document.getElementById('empty-state');
    const sysSection = document.getElementById('system-sources-section');
    const sysList = document.getElementById('system-sources-list');

    if (_connections.length === 0) {
      list.innerHTML = '';
      emptyState.classList.remove('hidden');
    } else {
      emptyState.classList.add('hidden');
      list.innerHTML = _connections.map(renderConnectionCard).join('');
    }

    if (_systemSources.length > 0) {
      sysSection.classList.remove('hidden');
      sysList.innerHTML = _systemSources.map(renderSystemSourceCard).join('');
    } else {
      sysSection.classList.add('hidden');
    }
  }

  // ── Load connections ──────────────────────────────────────────────────────────
  async function loadConnections() {
    try {
      const data = await api('GET', '/api/v1/connections');
      _connections = (data.items || []).filter(i => !i.system);
      _systemSources = (data.items || []).filter(i => i.system);
      renderConnectionsList();
    } catch (e) {
      showError('Failed to load connections: ' + e.message);
    }
  }

  // ── Select connection for preview/schema ──────────────────────────────────────
  function selectConnection(id) {
    _activeId = id;
    document.getElementById('preview-submit-btn').disabled = false;
    document.getElementById('schema-discover-btn').disabled = false;
    // Clear previous results
    document.getElementById('preview-table').classList.add('hidden');
    document.getElementById('preview-empty-msg').classList.add('hidden');
    document.getElementById('schema-fields').innerHTML = '';
    showSuccess('Selected connection for preview/schema.');
  }

  // ── Ping ──────────────────────────────────────────────────────────────────────
  async function ping(id, isSystem) {
    const statusEl = document.getElementById(isSystem ? `sys-status-${id}` : `conn-status-${id}`);
    if (statusEl) statusEl.textContent = '…';
    try {
      const result = await api('POST', `/api/v1/connections/${id}/ping`);
      const status = result.status;
      // Update in-memory list
      const item = [..._connections, ..._systemSources].find(c => c.id === id);
      if (item) item.status = status;
      if (statusEl) {
        const cls = STATUS_COLOURS[status] || 'bg-gray-100 text-gray-600';
        statusEl.className = `inline-block px-2 py-0.5 rounded text-xs font-medium ${cls}`;
        statusEl.textContent = status;
      }
      if (status === 'reachable') showSuccess(`Reachable (${result.latency_ms}ms)`);
      else showError(`Unreachable: ${result.error || 'unknown error'}`);
    } catch (e) {
      showError('Ping failed: ' + e.message);
    }
  }

  // ── Preview ───────────────────────────────────────────────────────────────────
  async function runPreview() {
    if (!_activeId) return;
    const query = document.getElementById('preview-query-input').value;
    const startVal = document.getElementById('preview-start-input').value;
    const endVal = document.getElementById('preview-end-input').value;
    const body = { query, limit: 50 };
    if (startVal) body.start = new Date(startVal).toISOString();
    if (endVal) body.end = new Date(endVal).toISOString();

    const btn = document.getElementById('preview-submit-btn');
    btn.disabled = true;
    btn.textContent = 'Loading…';
    try {
      const result = await api('POST', `/api/v1/connections/${_activeId}/preview`, body);
      renderPreviewTable(result.results);
    } catch (e) {
      showError('Preview failed: ' + e.message);
    } finally {
      btn.disabled = false;
      btn.textContent = 'Run Preview';
    }
  }

  function renderPreviewTable(rows) {
    const table = document.getElementById('preview-table');
    const thead = document.getElementById('preview-thead');
    const tbody = document.getElementById('preview-tbody');
    const emptyMsg = document.getElementById('preview-empty-msg');

    if (!rows || rows.length === 0) {
      table.classList.add('hidden');
      emptyMsg.classList.remove('hidden');
      return;
    }

    const cols = Object.keys(rows[0]);
    thead.innerHTML = `<tr>${cols.map(c => `<th class="px-3 py-2 font-medium">${escapeHtml(c)}</th>`).join('')}</tr>`;
    tbody.innerHTML = rows.map(row =>
      `<tr class="hover:bg-gray-50">${cols.map(c => `<td class="px-3 py-2 max-w-xs truncate">${escapeHtml(String(row[c] ?? ''))}</td>`).join('')}</tr>`
    ).join('');

    table.classList.remove('hidden');
    emptyMsg.classList.add('hidden');
  }

  // ── Schema discovery ──────────────────────────────────────────────────────────
  async function discoverSchema() {
    if (!_activeId) return;
    const btn = document.getElementById('schema-discover-btn');
    btn.disabled = true;
    btn.textContent = 'Discovering…';
    try {
      const result = await api('GET', `/api/v1/connections/${_activeId}/schema`);
      renderSchemaFields(result.fields, result.sample_count);
    } catch (e) {
      showError('Schema discovery failed: ' + e.message);
    } finally {
      btn.disabled = false;
      btn.textContent = 'Discover Schema';
    }
  }

  function renderSchemaFields(fields, sampleCount) {
    const container = document.getElementById('schema-fields');
    const entries = Object.entries(fields || {});
    if (entries.length === 0) {
      container.innerHTML = '<p class="text-xs text-gray-400">No fields discovered.</p>';
      return;
    }
    container.innerHTML = `
<p class="text-xs text-gray-400 mb-2">Sampled ${sampleCount} records</p>
<table class="w-full text-xs">
  <thead class="text-gray-500 bg-gray-50">
    <tr><th class="px-2 py-1 text-left">Field</th><th class="px-2 py-1 text-left">Type</th><th class="px-2 py-1 text-left">Example</th></tr>
  </thead>
  <tbody class="divide-y divide-gray-100">
    ${entries.map(([name, f]) => `
    <tr class="hover:bg-gray-50">
      <td class="px-2 py-1 font-mono text-gray-800 max-w-[8rem] truncate">${escapeHtml(name)}</td>
      <td class="px-2 py-1 text-indigo-600">${escapeHtml(f.type)}</td>
      <td class="px-2 py-1 text-gray-500 max-w-[8rem] truncate">${escapeHtml(String(f.example ?? ''))}</td>
    </tr>`).join('')}
  </tbody>
</table>`;
  }

  // ── Modal helpers ─────────────────────────────────────────────────────────────
  function openModal() {
    document.getElementById('conn-modal').classList.remove('hidden');
  }

  function closeModal() {
    document.getElementById('conn-modal').classList.add('hidden');
    _editingId = null;
    resetForm();
  }

  function resetForm() {
    document.getElementById('conn-label-input').value = '';
    document.getElementById('conn-url-input').value = '';
    document.getElementById('conn-role-select').value = 'source';
    const typeSelect = document.getElementById('conn-type-select');
    if (typeSelect.options.length) typeSelect.selectedIndex = 0;
    updateCredFields();
  }

  function updateCredFields() {
    const type = document.getElementById('conn-type-select').value;
    const defs = CRED_FIELDS[type] || [];
    const container = document.getElementById('cred-fields');
    const hideAuth = _config.hide_auth_inputs;
    container.innerHTML = defs
      .filter(f => !(hideAuth && f.type === 'password'))
      .map(f => `
<div>
  <label class="text-xs font-medium text-gray-600 block mb-1">${escapeHtml(f.label)}</label>
  <input name="cred_${f.key}" type="${f.type}" placeholder="${f.placeholder || ''}"
    class="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400"
    id="cred_${f.key}" />
</div>`).join('');
  }

  function getCredentials() {
    const type = document.getElementById('conn-type-select').value;
    const defs = CRED_FIELDS[type] || [];
    const creds = {};
    for (const f of defs) {
      const el = document.getElementById(`cred_${f.key}`);
      if (el) creds[f.key] = el.value;
    }
    return creds;
  }

  // ── Open add modal ────────────────────────────────────────────────────────────
  function openAddModal() {
    _editingId = null;
    document.getElementById('modal-title').textContent = 'Add Connection';
    resetForm();
    openModal();
  }

  // ── Open edit modal ───────────────────────────────────────────────────────────
  function openEditModal(id) {
    const conn = _connections.find(c => c.id === id);
    if (!conn) return;
    _editingId = id;
    document.getElementById('modal-title').textContent = 'Edit Connection';
    document.getElementById('conn-label-input').value = conn.label;
    document.getElementById('conn-url-input').value = conn.connection_url;
    const typeSelect = document.getElementById('conn-type-select');
    typeSelect.value = conn.type;
    document.getElementById('conn-role-select').value = conn.role;
    updateCredFields();
    openModal();
  }

  // ── Test connection (pre-save) ────────────────────────────────────────────────
  async function testConnection() {
    const type = document.getElementById('conn-type-select').value;
    const connection_url = document.getElementById('conn-url-input').value;
    const credentials = getCredentials();
    const btn = document.getElementById('conn-test-btn');
    btn.disabled = true;
    btn.textContent = 'Testing…';
    try {
      const result = await api('POST', '/api/v1/connections/test', { type, connection_url, credentials });
      if (result.status === 'reachable') showSuccess(`Reachable (${result.latency_ms}ms)`);
      else showError(`Unreachable: ${result.error || 'unknown error'}`);
    } catch (e) {
      showError('Test failed: ' + e.message);
    } finally {
      btn.disabled = false;
      btn.textContent = 'Test';
    }
  }

  // ── Submit connection (create or patch) ───────────────────────────────────────
  async function submitConnection() {
    const label = document.getElementById('conn-label-input').value.trim();
    const type = document.getElementById('conn-type-select').value;
    const role = document.getElementById('conn-role-select').value;
    const connection_url = document.getElementById('conn-url-input').value.trim();
    const credentials = getCredentials();

    if (!label || !connection_url) {
      showError('Label and Connection URL are required.');
      return;
    }

    const btn = document.getElementById('conn-submit-btn');
    btn.disabled = true;
    btn.textContent = 'Saving…';

    try {
      if (_editingId) {
        await api('PATCH', `/api/v1/connections/${_editingId}`, { label, credentials });
        showSuccess('Connection updated.');
      } else {
        await api('POST', '/api/v1/connections', { label, type, role, connection_url, credentials });
        showSuccess('Connection created.');
      }
      closeModal();
      await loadConnections();
    } catch (e) {
      showError('Save failed: ' + e.message);
    } finally {
      btn.disabled = false;
      btn.textContent = 'Save';
    }
  }

  // ── Delete connection ─────────────────────────────────────────────────────────
  async function deleteConnection(id) {
    if (!confirm('Delete this connection?')) return;
    try {
      await api('DELETE', `/api/v1/connections/${id}`);
      if (_activeId === id) {
        _activeId = null;
        document.getElementById('preview-submit-btn').disabled = true;
        document.getElementById('schema-discover-btn').disabled = true;
      }
      showSuccess('Connection deleted.');
      await loadConnections();
    } catch (e) {
      showError('Delete failed: ' + e.message);
    }
  }

  // ── Initialise ────────────────────────────────────────────────────────────────
  async function init() {
    try {
      _config = await api('GET', '/api/v1/ui-config');
    } catch {
      _config = { connection_types: ['s3', 'clickhouse', 'trino', 'langfuse', 'dataset'], hide_auth_inputs: false };
    }

    // Populate type select
    const typeSelect = document.getElementById('conn-type-select');
    typeSelect.innerHTML = _config.connection_types.map(t => `<option value="${t}">${t}</option>`).join('');
    updateCredFields();

    // Wire buttons
    document.getElementById('add-connection-btn').addEventListener('click', openAddModal);
    document.getElementById('preview-submit-btn').addEventListener('click', runPreview);
    document.getElementById('schema-discover-btn').addEventListener('click', discoverSchema);

    // Close modal on backdrop click
    document.getElementById('conn-modal').addEventListener('click', (e) => {
      if (e.target === e.currentTarget) closeModal();
    });

    await loadConnections();
  }

  // ── Utilities ─────────────────────────────────────────────────────────────────
  function escapeHtml(str) {
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  // ── Public API ────────────────────────────────────────────────────────────────
  window.DB = {
    ping,
    selectConnection,
    openEditModal,
    openAddModal,
    closeModal,
    updateCredFields,
    testConnection,
    submitConnection,
    deleteConnection,
  };

  document.addEventListener('DOMContentLoaded', init);
})();

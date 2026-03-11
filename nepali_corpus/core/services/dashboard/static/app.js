/* Nepali Corpus Dashboard Logic */

let statsWS = null;
let logsWS = null;
const logBox = document.getElementById('logBox');
const statusDot = document.querySelector('.status-dot');
const statusText = document.querySelector('.nav-status span:last-child');
const searchInput = document.getElementById('searchInput');
const sourceGrid = document.getElementById('sourceGrid');
const emptyState = document.getElementById('emptyState');
const filterChips = document.getElementById('filterChips');

let sources = [];
let activeFilter = 'all';
let scrapedOnly = false;

// Navigation
function bindNav() {
    const navItems = document.querySelectorAll('.nav-item');
    const pages = document.querySelectorAll('.page');

    navItems.forEach(item => {
        item.addEventListener('click', (e) => {
            e.preventDefault();
            const pageId = item.dataset.page;

            navItems.forEach(n => n.classList.remove('active'));
            item.classList.add('active');

            pages.forEach(p => {
                p.classList.remove('active');
                if (p.id === `page-${pageId}`) p.classList.add('active');
            });
        });
    });
}

// Stats & Logs
function initStatsWS() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    statsWS = new WebSocket(`${protocol}//${window.location.host}/ws/stats`);
    statsWS.onmessage = (event) => {
        const stats = JSON.parse(event.data);
        updateDashboard(stats);
    };
}

function initLogsWS() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    logsWS = new WebSocket(`${protocol}//${window.location.host}/ws/logs`);
    logsWS.onmessage = (event) => {
        const line = document.createElement('div');
        line.className = 'log-line';
        line.textContent = event.data;
        logBox.appendChild(line);
        if (document.getElementById('autoscroll').checked) {
            logBox.scrollTop = logBox.scrollHeight;
        }
    };
}

function updateDashboard(stats) {
    document.getElementById('stat-crawled').textContent = stats.count_crawled || 0;
    document.getElementById('stat-saved').textContent = stats.count_saved || 0;
    document.getElementById('stat-pdf').textContent = stats.count_pdf || 0;
    document.getElementById('stat-failed').textContent = stats.count_failed || 0;
    document.getElementById('stat-speed').textContent = stats.speed || '0.0';
    document.getElementById('stat-elapsed').textContent = stats.elapsed || '00:00:00';

    // Update scraper status
    const badge = document.getElementById('scraper-status');
    const state = stats.state || 'idle';
    badge.textContent = state.charAt(0).toUpperCase() + state.slice(1);

    statusDot.className = `status-dot ${state}`;
    statusText.textContent = badge.textContent;

    // Active task list
    const taskContainer = document.getElementById('active-sources');
    if (stats.active_tasks && stats.active_tasks.length > 0) {
        taskContainer.innerHTML = stats.active_tasks.map(t =>
            `<div class="task-item">➜ ${t}</div>`
        ).join('');
    } else {
        taskContainer.innerHTML = '<div class="empty-state-mini">No active crawls</div>';
    }

    // Recent errors
    const errorBox = document.getElementById('error-box');
    if (stats.recent_errors && stats.recent_errors.length > 0) {
        errorBox.innerHTML = stats.recent_errors.map(e => `<div>${e}</div>`).join('');
    } else {
        errorBox.innerHTML = '<div class="empty-state-mini">All quiet</div>';
    }
}

// Pipeline Controls
async function startScraper() {
    const workers = document.getElementById('workers').value;
    const maxPages = document.getElementById('max-pages').value;
    const pdfEnabled = document.getElementById('pdf-enabled').checked;
    const categories = Array.from(document.querySelectorAll('#category-checks input:checked')).map(c => c.value);

    await fetch('/api/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ workers, max_pages_per_source: maxPages, pdf_enabled: pdfEnabled, groups: categories })
    });
}

function stopScraper() { fetch('/api/stop', { method: 'POST' }); }
function pauseScraper() { fetch('/api/pause', { method: 'POST' }); }

// Sources Explorer
async function loadSources() {
    const resp = await fetch('/api/sources');
    sources = await resp.json();
    document.getElementById('sourceCount').textContent = sources.length;
    renderSources();
}

function renderSources() {
    const query = searchInput.value.toLowerCase();

    // The API might return sources in a wrapper { sources: [], categories: [] }
    const sourcesList = Array.isArray(sources) ? sources : (sources.sources || []);

    const filtered = sourcesList.filter(s => {
        const matchesSearch = (s.name || "").toLowerCase().includes(query) || (s.url || "").toLowerCase().includes(query);
        const sourceCategory = (s.category || "").toLowerCase();
        const matchesFilter = activeFilter === 'all' || sourceCategory === activeFilter.toLowerCase();

        // Show Scraped Only filter
        if (scrapedOnly && (s.crawled || 0) === 0 && (s.saved || 0) === 0) {
            return false;
        }

        return matchesSearch && matchesFilter;
    });

    if (filtered.length) {
        emptyState.style.display = 'none';
        sourceGrid.innerHTML = filtered.map(s => {
            const cat = (s.category || "Misc").toLowerCase();
            const group = (s.group || s.category || "Misc").toLowerCase().replace(/[^a-z0-9]/g, '-');
            const groupLabel = s.group_label || _titleize(s.group || s.category || "General");
            const catLabel = s.category || "Misc";

            return `
                <div class="card">
                    <div class="card-top">
                        <h3 class="card-title">${escapeHtml(s.name)}</h3>
                        <div class="card-badge ${cat}">${catLabel.toUpperCase()}</div>
                    </div>
                    <div class="card-subtitle">${escapeHtml(groupLabel)}</div>
                    <div class="card-url">${escapeHtml(s.url)}</div>
                    
                    <div class="card-stats">
                        <div class="card-stat">
                            <span class="count crawled">${s.crawled || 0}</span>
                            <span class="label">CRAWLED</span>
                        </div>
                        <div class="card-stat">
                            <span class="count saved">${s.saved || 0}</span>
                            <span class="label">SAVED</span>
                        </div>
                        <div class="card-stat">
                            <span class="count failed">${s.failed || 0}</span>
                            <span class="label">FAILED</span>
                        </div>
                    </div>
                    
                    <div class="card-footer">
                        <button class="footer-btn">${escapeHtml(s.scraper || s.type || groupLabel)}</button>
                        <button class="footer-btn view">View</button>
                    </div>
                </div>
            `;
        }).join('');
    } else {
        sourceGrid.innerHTML = '';
        emptyState.style.display = 'block';
    }
}

/* --- Professional Dataset Viewer Logic --- */
const tableSelect = document.getElementById('tableSelect');
const pageSizeInput = document.getElementById('pageSizeInput');
const dataSearchInput = document.getElementById('dataSearchInput');
const tableHead = document.getElementById('tableHead');
const tableBody = document.getElementById('tableBody');
const pageInfo = document.getElementById('pageInfo');
const prevPage = document.getElementById('prevPage');
const nextPage = document.getElementById('nextPage');
const tableRefresh = document.getElementById('tableRefresh');
const viewSelect = document.getElementById('viewSelect');
const xColumnSelect = document.getElementById('xColumnSelect');
const yColumnSelect = document.getElementById('yColumnSelect');
const chartContainer = document.getElementById('chartContainer');
const dataChart = document.getElementById('dataChart');

let tableState = {
    tables: [],
    table: null,
    columns: [],
    rows: [],
    page: 1,
    pageSize: 100,
    totalPages: 1,
    totalCount: 0,
    search: '',
    view: 'table', // table or chart
    xColumn: null,
    yColumn: null,
    sort: null
};

async function loadTables(force = false) {
    if (!tableSelect) return;
    const resp = await fetch('/api/get-tables');
    const tables = await resp.json();
    tableState.tables = tables || [];

    tableSelect.innerHTML = tableState.tables.length
        ? tableState.tables.map(t => `<option value="${escapeHtml(t)}">${escapeHtml(t)}</option>`).join('')
        : '<option value="">No tables</option>';

    if (!tableState.tables.length) {
        tableState.table = null;
        renderTable([]);
        return;
    }

    const selected = tableState.tables[0];
    tableSelect.value = selected;
    tableState.table = selected;
    await loadColumns();
}

async function loadColumns() {
    if (!tableState.table) return;
    const resp = await fetch(`/api/column-names?table_name=${encodeURIComponent(tableState.table)}`);
    const cols = await resp.json();
    tableState.columns = cols.map(c => c.name);

    // Populate X/Y Selects for Charting
    xColumnSelect.innerHTML = tableState.columns.map(c => `<option value="${c}">${c}</option>`).join('');
    yColumnSelect.innerHTML = tableState.columns.map(c => `<option value="${c}">${c}</option>`).join('');

    tableState.xColumn = tableState.columns[0];
    tableState.yColumn = tableState.columns[1] || tableState.columns[0];

    tableState.page = 1;
    await loadTableData();
}

async function loadTableData() {
    if (!tableState.table) return;
    const { page, pageSize, search, view } = tableState;
    let url = '';

    if (search.trim()) {
        url = `/api/search?table_name=${encodeURIComponent(tableState.table)}&search_term=${encodeURIComponent(search)}&page=${page}&page_size=${pageSize}`;
    } else if (view === 'chart') {
        url = `/api/metrics-data?table_name=${encodeURIComponent(tableState.table)}&x_column=${encodeURIComponent(tableState.xColumn)}&y_column=${encodeURIComponent(tableState.yColumn)}&page=${page}&page_size=${pageSize}`;
    } else {
        url = `/api/metrics-data?table_name=${encodeURIComponent(tableState.table)}&full_table=true&page=${page}&page_size=${pageSize}`;
    }

    const resp = await fetch(url);
    const payload = await resp.json();

    tableState.rows = payload.data || [];
    tableState.totalCount = payload.total_count || 0;
    tableState.totalPages = payload.total_pages || 1;

    renderTable(tableState.rows);
    renderChart(tableState.rows);
}

// Formatter Helpers
function formatValue(value, key) {
    if (value === null || value === undefined || value === '') return '<span class="text-secondary">NULL</span>';
    const str = String(value);
    const lowerKey = key.toLowerCase();

    // ID / UUID formatting
    if (/^[0-9a-f-]{36}$/i.test(str)) {
        return `<span class="badge badge-id" title="${str}">${str.slice(0, 8)}...</span>`;
    }

    // Status mapping
    const statusMap = {
        'true': 'badge-success', 'false': 'badge-failure',
        'passed': 'badge-success', 'failed': 'badge-failure',
        'running': 'badge-neutral', 'completed': 'badge-success'
    };
    if (statusMap[str.toLowerCase()]) {
        return `<span class="badge ${statusMap[str.toLowerCase()]}">${str.toUpperCase()}</span>`;
    }

    // Success/Boolean special handling
    if (lowerKey === 'success' || typeof value === 'boolean') {
        const isOk = str.toLowerCase() === 'true';
        return `<span class="badge ${isOk ? 'badge-success' : 'badge-failure'}">${isOk ? 'PASS' : 'FAIL'}</span>`;
    }

    // Date formatting
    if (lowerKey.includes('time') || lowerKey.includes('date') || lowerKey.includes('_at')) {
        const d = new Date(value);
        if (!isNaN(d.getTime())) return `<span class="text-secondary" title="${str}">${d.toLocaleString()}</span>`;
    }

    return escapeHtml(str);
}

function highlightXmlTags(text) {
    if (!text) return '';
    return text
        .replace(/&lt;think&gt;([\s\S]*?)&lt;\/think&gt;/g, '<span class="think-tag">&lt;think&gt;$1&lt;/think&gt;</span>')
        .replace(/(&lt;\/?[a-z0-9_-]+(\s+[a-z0-9_-]+=".*?")*\s*&gt;)/gi, '<span class="xml-tag">$1</span>');
}

function renderTable(rows) {
    if (!tableHead || !tableBody) return;
    const cols = tableState.columns;
    if (!cols.length) {
        tableHead.innerHTML = '<tr><th>No Data</th></tr>';
        tableBody.innerHTML = '<tr><td>Search or select a valid table.</td></tr>';
        return;
    }

    tableHead.innerHTML = `<tr>${cols.map(c => `<th>${escapeHtml(c)}</th>`).join('')}</tr>`;

    if (!rows.length) {
        tableBody.innerHTML = `<tr><td colspan="${cols.length}" class="empty-state">No rows found.</td></tr>`;
        return;
    }

    tableBody.innerHTML = rows.map(row => {
        return `<tr>${cols.map(col => {
            const val = row[col];
            let type = 'default';
            if (col.includes('id')) type = 'contract';
            if (col === 'messages' || col === 'conversation') type = 'conversation';
            else if (String(val).length > 200 || col === 'text' || col === 'content') type = 'medium';

            const rawDisplay = formatValue(val, col);
            const prettyData = typeof val === 'object' ? JSON.stringify(val, null, 2) : String(val);
            const isJson = isJSON(val);
            const isChat = isConversation(val);

            let buttons = '';
            if (isChat) buttons += `<button class="action-btn" onclick="event.stopPropagation(); openConversationModal(this)">CHAT</button>`;
            if (isJson || isChat) buttons += `<button class="action-btn" onclick="event.stopPropagation(); openJsonModal(this)">JSON</button>`;
            else if (String(val).length > 150) buttons += `<button class="action-btn" onclick="event.stopPropagation(); openTextModal(this)">VIEW</button>`;

            return `
                <td class="cell-${type}">
                    <div class="cell-wrapper">
                        <div class="cell-raw">${rawDisplay}</div>
                        <div class="cell-pretty">${highlightXmlTags(escapeHtml(prettyData))}</div>
                        <div class="cell-actions">${buttons}</div>
                        <div class="full-data" data-raw="${encodeURIComponent(JSON.stringify(val))}"></div>
                    </div>
                </td>
            `;
        }).join('')}</tr>`;
    }).join('');

    // Toggle expansion on row click
    tableBody.querySelectorAll('tr').forEach(tr => {
        tr.onclick = (e) => {
            if (e.target.closest('button')) return;
            tr.classList.toggle('expanded');
        };
    });

    pageInfo.textContent = `${tableState.page} / ${tableState.totalPages}`;
    prevPage.disabled = tableState.page <= 1;
    nextPage.disabled = tableState.page >= tableState.totalPages;
}

function renderChart(rows) {
    if (tableState.view !== 'chart' || !window.Plotly || !rows.length) {
        chartContainer.style.display = 'none';
        return;
    }
    chartContainer.style.display = 'block';
    const x = rows.map(r => r[tableState.xColumn]);
    const y = rows.map(r => r[tableState.yColumn]);
    const trace = {
        x, y,
        type: 'scatter',
        mode: 'lines+markers',
        marker: { color: '#564caf' },
        line: { color: '#564caf', width: 2 }
    };
    const layout = {
        title: `${tableState.xColumn} vs ${tableState.yColumn}`,
        margin: { t: 40, l: 60, r: 20, b: 60 },
        font: { family: 'Inter, sans-serif' }
    };
    window.Plotly.newPlot(dataChart, [trace], layout, { responsive: true });
}

// Modal System
function openModal(title, contentHtml) {
    document.getElementById('modalTitle').textContent = title;
    document.getElementById('modalBody').innerHTML = contentHtml;
    document.getElementById('detailModal').style.display = 'flex';
}

function closeModal() {
    document.getElementById('detailModal').style.display = 'none';
}

function openJsonModal(btn) {
    const raw = decodeURIComponent(btn.closest('.cell-wrapper').querySelector('.full-data').dataset.raw);
    const parsed = JSON.parse(raw);
    openModal('JSON View', `<pre class="json-pre-modal">${syntaxHighlight(JSON.stringify(parsed, null, 2))}</pre>`);
}

function openConversationModal(btn) {
    const raw = decodeURIComponent(btn.closest('.cell-wrapper').querySelector('.full-data').dataset.raw);
    const msgs = JSON.parse(raw);
    if (!Array.isArray(msgs)) return openJsonModal(btn);
    const bubbles = msgs.map(m => {
        const role = String(m.role || m.from || 'system').toLowerCase();
        const content = m.content || m.value || '';
        return `
            <div class="bubble role-${role}">
                <span class="role-label">${role}</span>
                <div>${highlightXmlTags(escapeHtml(content))}</div>
            </div>
        `;
    }).join('');
    openModal('Conversation View', `<div class="conversation-container">${bubbles}</div>`);
}

function openTextModal(btn) {
    const raw = decodeURIComponent(btn.closest('.cell-wrapper').querySelector('.full-data').dataset.raw);
    try {
        const txt = JSON.parse(raw);
        openModal('Full Text', `<div class="json-pre-modal">${highlightXmlTags(escapeHtml(String(txt)))}</div>`);
    } catch (e) {
        openModal('Full Text', `<div class="json-pre-modal">${highlightXmlTags(escapeHtml(raw))}</div>`);
    }
}

function syntaxHighlight(json) {
    return json.replace(/("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+\-]?\d+)?)/g, (match) => {
        let cls = 'number';
        if (/^"/.test(match)) {
            if (/:$/.test(match)) {
                cls = 'json-key';
                return `<span class="${cls}">${match.slice(0, -1)}</span>:`;
            }
            cls = 'string';
        } else if (/true|false/.test(match)) cls = 'boolean';
        else if (/null/.test(match)) cls = 'null';
        return `<span class="${cls}">${match}</span>`;
    });
}

function isJSON(val) {
    try {
        const s = typeof val === 'string' ? JSON.parse(val) : val;
        return typeof s === 'object' && s !== null;
    } catch (e) { return false; }
}

function isConversation(val) {
    const s = typeof val === 'string' ? (isJSON(val) ? JSON.parse(val) : null) : val;
    return Array.isArray(s) && s.length > 0 && (s[0].role || s[0].from);
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Bindings
function bindControls() {
    document.getElementById('btn-start').onclick = startScraper;
    document.getElementById('btn-stop').onclick = stopScraper;
    document.getElementById('btn-pause').onclick = pauseScraper;
    document.getElementById('clearLogs').onclick = () => { logBox.innerHTML = ''; };

    searchInput.oninput = renderSources;
    const scrapedBtn = document.getElementById('btn-only-scraped');
    if (scrapedBtn) {
        scrapedBtn.onclick = () => {
            scrapedOnly = !scrapedOnly;
            scrapedBtn.classList.toggle('active', scrapedOnly);
            renderSources();
        };
    }
    filterChips.querySelectorAll('.chip').forEach(c => {
        if (c.id === 'btn-only-scraped') return;
        c.onclick = () => {
            filterChips.querySelectorAll('.chip').forEach(x => x.classList.remove('active'));
            c.classList.add('active');
            activeFilter = c.dataset.filter;
            renderSources();
        };
    });

    // Dataset Bindings
    tableSelect.onchange = () => { tableState.table = tableSelect.value; loadColumns(); };
    pageSizeInput.onchange = () => { tableState.pageSize = parseInt(pageSizeInput.value); tableState.page = 1; loadTableData(); };
    let searchT = null;
    dataSearchInput.oninput = () => {
        clearTimeout(searchT);
        searchT = setTimeout(() => { tableState.search = dataSearchInput.value; tableState.page = 1; loadTableData(); }, 300);
    };
    tableRefresh.onclick = () => loadTables(true);
    prevPage.onclick = () => { if (tableState.page > 1) { tableState.page--; loadTableData(); } };
    nextPage.onclick = () => { if (tableState.page < tableState.totalPages) { tableState.page++; loadTableData(); } };
    viewSelect.onchange = () => { tableState.view = viewSelect.value; loadTableData(); };
    xColumnSelect.onchange = () => { tableState.xColumn = xColumnSelect.value; if (tableState.view === 'chart') loadTableData(); };
    yColumnSelect.onchange = () => { tableState.yColumn = yColumnSelect.value; if (tableState.view === 'chart') loadTableData(); };
    document.getElementById('modalClose').onclick = closeModal;
}

window.onload = () => {
    bindNav();
    bindControls();
    initStatsWS();
    initLogsWS();
    loadSources();
    loadTables();
};

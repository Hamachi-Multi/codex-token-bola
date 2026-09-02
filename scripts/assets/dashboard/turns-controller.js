import {
  TURN_SORT_KEYS,
  TURN_SORT_LABELS,
  defaultTurnSortDir,
  state,
} from './core.js';
import {
  getCachedJSON,
  peekCachedJSON,
  primeCachedJSON,
} from './query-cache.js';
import { esc } from './ui.js';
import {
  clearQueryStatus,
  focusActiveViewRow,
  refreshScrollFades,
  restoreReplacedControlFocus,
  setPanelContent,
  showQueryError,
  table,
} from './dom.js';
import {
  compactDateTime,
  compactNumber,
  compactNumberSpan,
  exactNumber,
  sessionLabel,
  sessionLabelMarkup,
  turnStatusClass,
} from './formatters.js';
import { createListDetailView } from './components/list-detail-view.js';
import { createPager } from './components/pager.js';

export function createTurnsController({
  params,
  detailRoutes,
  saveSettings,
  cachedValue,
  prepareDetail,
  prefetchNextPage,
  renderDetailSummary,
  bindDetailControls,
  turnPromptPreviewMarkup,
}) {
  function normalizeSortKey(value) {
    if (value === 'time' || value === 'clock') return 'date';
    if (value === 'project') return 'session';
    return TURN_SORT_KEYS.has(value) ? value : 'date';
  }

  function normalizeSortDir(value) {
    return value === 'asc' ? 'asc' : 'desc';
  }

  function settingsSnapshot() {
    return {
      turnSortKey: state.turnSortKey,
      turnSortDir: state.turnSortDir,
    };
  }

  function restoreSettings(settings) {
    state.turnSortKey = normalizeSortKey(settings.turnSortKey);
    state.turnSortDir = normalizeSortDir(settings.turnSortDir);
  }

  function sortSummary() {
    const label = TURN_SORT_LABELS[state.turnSortKey] || 'Date';
    return `Sorted by ${label} ${state.turnSortDir === 'asc' ? 'asc' : 'desc'}`;
  }

  function dashboardParams() {
    const query = params();
    query.set('page', String(state.turnPage));
    query.set('per_page', String(state.turnPageSize));
    query.set('sort', state.turnSortKey);
    query.set('sort_dir', state.turnSortDir);
    query.set('sessions_page', String(state.listPages.projects || 1));
    query.set('tools_page', String(state.listPages.tools || 1));
    query.set('session_sort', state.listSorts.projects.key);
    query.set('session_sort_dir', state.listSorts.projects.dir);
    query.set('tool_sort', state.listSorts.tools.key);
    query.set('tool_sort_dir', state.listSorts.tools.dir);
    return query;
  }

  function path(page = state.turnPage) {
    const query = params();
    query.set('page', String(page));
    query.set('per_page', String(state.turnPageSize));
    query.set('sort', state.turnSortKey);
    query.set('sort_dir', state.turnSortDir);
    return '/api/turns?' + query;
  }

  function resetPage() {
    state.turnPage = 1;
  }

  function pageRows(payload) {
    return Array.isArray(payload) ? payload : ((payload || {}).rows || []);
  }

  function targetRow(turns) {
    const rows = pageRows(turns);
    return rows.find(item => state.selected
      && String(item.session_id || '') === String(state.selected.session || '')
      && String(item.turn_id || '') === String(state.selected.turn || '')) || rows[0] || null;
  }

  async function preparePageDetail(turns) {
    const row = targetRow(turns);
    if (!row) return null;
    const detailPath = detailRoutes.turn(row.session_id || '', row.turn_id || '');
    const key = `${row.session_id || ''}\u0000${row.turn_id || ''}`;
    return prepareDetail(key, detailPath);
  }

  function commitRow(row, detail) {
    state.selected = { session: row.dataset.session, turn: row.dataset.turn };
    state.promptExpanded = false;
    state.toolSummaryExpanded = false;
    state.detailData = detail;
    setPanelContent('detail', renderDetailSummary(detail));
    bindDetailControls(() => selectRow(row));
    refreshScrollFades();
  }

  const detailView = createListDetailView({
    rowSelector: '#turn-list tr[data-turn]',
    buttonSelector: '#turn-list tr[data-turn] .row-select-button',
    detailId: 'detail',
    statusId: 'detail-status',
    keyForRow: row => `${row.dataset.session || ''}\u0000${row.dataset.turn || ''}`,
    pathForRow: row => detailRoutes.turn(row.dataset.session || '', row.dataset.turn || ''),
    nextRequestSequence: () => ++state.detailSeq,
    isCurrentRequest: sequence => sequence === state.detailSeq,
    commit: commitRow,
    reset: () => {
      state.selected = null;
      state.detailData = null;
    },
    getCachedJSON,
    peekCachedJSON,
    clearQueryStatus,
    showQueryError,
  });

  function selectRow(row, preparedDetail) {
    return detailView.select(row, preparedDetail);
  }

  function renderPage(turns, prepared = null) {
    state.turnPage = Math.max(1, Number(turns.page || state.turnPage || 1));
    document.getElementById('turn-count').textContent = turns.focused
      ? (turns.total ? 'Linked turn' : 'Linked turn not in scope')
      : `${sortSummary()}: ${compactNumber(turns.total || turns.rows.length)} turns · list uses date/session filters`;
    setPanelContent('turn-list', table(
      [{label:'Date', sort:'date'}, {label:'Session', sort:'session'}, {label:'Prompt', sort:'prompt'}, {label:'Cost Units', sort:'credits', cls:'num'}, {label:'Total Tokens', sort:'raw', cls:'num'}],
      turns.rows.map(row => {
        const label = sessionLabel(row);
        const status = row.turn_status || 'unknown';
        const tokenAvailable = Number(row.token_data_available ?? 1) !== 0;
        const resolutionReason = row.token_resolution_reason || 'Token data unavailable';
        const resolutionMeta = tokenAvailable ? '' : `<span class="status token-unavailable" title="${esc(resolutionReason)}">Token unavailable</span>`;
        const rawValue = tokenAvailable ? compactNumberSpan(row.raw) : `<span class="token-unavailable-value" title="${esc(resolutionReason)}">—</span>`;
        const costAvailable = tokenAvailable && row.credits !== null && row.credits !== undefined;
        const costReason = costAvailable ? '' : (tokenAvailable ? 'Cost rate is not configured for this model and date' : resolutionReason);
        const creditValue = costAvailable ? compactNumberSpan(row.credits, 'money') : `<span class="token-unavailable-value" title="${esc(costReason)}">—</span>`;
        const promptLabel = row.prompt_preview || 'No prompt preview';
        const promptAt = row.started_at || row.captured_at || '';
        const turnLabel = ['Turn', status, compactDateTime(promptAt), label, promptLabel].filter(Boolean).join(' · ');
        return `<tr data-session="${esc(row.session_id)}" data-turn="${esc(row.turn_id)}" data-status="${esc(turnStatusClass(status))}" title="${esc('Status: ' + status)}"><td class="datetime-cell" title="${esc(promptAt)}">${esc(compactDateTime(promptAt))}</td><td class="session-cell session-label-cell" title="${esc(label)}">${sessionLabelMarkup(row)}</td><td class="prompt" title="${esc(row.prompt_preview || '')}"><button type="button" class="row-select-button" aria-pressed="false" aria-label="Select ${esc(turnLabel)}">${turnPromptPreviewMarkup(row)}<span class="row-meta"><span title="${esc(promptAt)}">${esc(compactDateTime(promptAt))}</span><span title="${esc(label)}">${esc(label)}</span><span>${esc(status)}</span>${resolutionMeta}${tokenAvailable ? `<span title="${esc(exactNumber(row.raw))}">${esc(compactNumber(row.raw))} raw</span>` : ''}</span></button></td><td class="num">${creditValue}</td><td class="num">${rawValue}</td></tr>`;
      })
    ));
    document.querySelectorAll('#turn-list [data-turn-sort]').forEach(button => {
      button.addEventListener('click', event => {
        event.preventDefault();
        setSort(button.dataset.turnSort, button);
      });
    });
    detailView.bindRows();
    detailView.activateRendered({
      isSelected: row => state.selected
        && row.dataset.session === state.selected.session
        && row.dataset.turn === state.selected.turn,
      prepared,
    });
    focusActiveViewRow();
    pager.render({
      total: turns.total || 0,
      page: turns.page || 1,
      perPage: turns.per_page || state.turnPageSize,
    });
    refreshScrollFades();
  }

  function prefetchNext(turns) {
    prefetchNextPage(turns, path, row => detailRoutes.turn(row.session_id || '', row.turn_id || ''));
  }

  async function loadPage(page) {
    const requestSeq = state.requestSeq;
    const listSeq = ++state.turnListSeq;
    const pagePath = path(page);
    if (!peekCachedJSON(pagePath).hit) pager.setBusy(true);
    try {
      const turns = await cachedValue(pagePath);
      const first = targetRow(turns);
      if (first && !peekCachedJSON(detailRoutes.turn(first.session_id || '', first.turn_id || '')).hit) {
        pager.setBusy(true);
      }
      const prepared = await preparePageDetail(turns);
      if (requestSeq !== state.requestSeq || listSeq !== state.turnListSeq) return false;
      renderPage(turns, prepared);
      if (prepared?.error) showQueryError(prepared.error.message || prepared.error);
      prefetchNext(turns);
      return true;
    } catch (error) {
      if (requestSeq !== state.requestSeq || listSeq !== state.turnListSeq) return false;
      throw error;
    } finally {
      if (listSeq === state.turnListSeq) pager.setBusy(false);
    }
  }

  function safeLoadPage(page, onError = null, onCommit = null) {
    clearQueryStatus();
    loadPage(page)
      .then(committed => {
        if (committed && typeof onCommit === 'function') onCommit();
      })
      .catch(error => {
        if (typeof onError === 'function') onError();
        showQueryError(error.message || error);
        refreshScrollFades();
      });
  }

  function setSort(key, trigger = null) {
    const previous = {
      key: state.turnSortKey,
      dir: state.turnSortDir,
      page: state.turnPage,
    };
    const nextKey = normalizeSortKey(key);
    if (state.turnSortKey === nextKey) {
      state.turnSortDir = state.turnSortDir === 'asc' ? 'desc' : 'asc';
    } else {
      state.turnSortKey = nextKey;
      state.turnSortDir = defaultTurnSortDir(nextKey);
    }
    resetPage();
    saveSettings();
    safeLoadPage(
      1,
      () => {
        state.turnSortKey = previous.key;
        state.turnSortDir = previous.dir;
        state.turnPage = previous.page;
        saveSettings();
      },
      () => restoreReplacedControlFocus(trigger, `#turn-list [data-turn-sort="${CSS.escape(nextKey)}"]`),
    );
  }

  function beginDashboardLoad() {
    return {
      cachePath: path(state.turnPage),
      listSeq: ++state.turnListSeq,
    };
  }

  async function commitDashboardLoad(turns, { requestSeq, cachePath, listSeq }) {
    if (listSeq !== state.turnListSeq) return false;
    primeCachedJSON(cachePath, turns);
    const prepared = await preparePageDetail(turns);
    if (requestSeq !== state.requestSeq || listSeq !== state.turnListSeq) return false;
    renderPage(turns, prepared);
    if (prepared?.error) showQueryError(prepared.error.message || prepared.error);
    prefetchNext(turns);
    return true;
  }

  function invalidateRequests() {
    state.turnListSeq += 1;
    state.detailSeq += 1;
  }

  function resetSelection() {
    state.selected = null;
    state.detailData = null;
    state.promptExpanded = false;
    state.toolSummaryExpanded = false;
    state.detailSeq += 1;
  }

  const pager = createPager({
    rootId: 'turn-pager',
    previousButtonId: 'prev-page',
    nextButtonId: 'next-page',
    onPageChange: page => safeLoadPage(page),
  });

  return {
    beginDashboardLoad,
    commitDashboardLoad,
    dashboardParams,
    invalidateRequests,
    resetPage,
    resetSelection,
    restoreSettings,
    settingsSnapshot,
  };
}

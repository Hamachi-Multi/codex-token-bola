import {
  ROLLUP_SORT_DEFAULTS,
  ROLLUP_SORT_KEYS,
  state,
} from './core.js';
import { getJSON } from './api.js';
import {
  clearAnalyticsQueryCache,
  getCachedJSON,
  peekCachedJSON,
  prefetchJSON,
  setAnalyticsCacheGeneration,
} from './query-cache.js';
import { esc } from './ui.js';
import { createCleanupController } from './cleanup.js';
import {
  detailGridLoadingPanel,
  metric,
  refreshScrollFades,
  restoreReplacedControlFocus,
  sessionDetailLoadingPanel,
  clearQueryStatus,
  setGlobalError,
  setPanelContent,
  showQueryError,
  tableLoadingPanel,
} from './dom.js';
import {
  compactNumber,
  exactNumber,
  formatBytes,
} from './formatters.js';
import { createSelectedTurnController } from './selected-turn.js';
import { createAnalyzeController } from './analyze.js';
import { createCostRatesController } from './cost-rates.js';
import { createSettingsView } from './settings-view.js';
import { createSessionPickerController } from './session-picker.js';
import { createServiceActivityController } from './service-activity.js';
import { createToolbarController } from './toolbar.js';
import { createDashboardShell } from './app-shell.js';
import { createTurnsController } from './turns-controller.js';
import { createOverviewRenderers } from './overview-render.js';
import { createDialogManager } from './components/dialog.js';
import { createCleanupSummary } from './components/cleanup-summary.js';
import { createDetailRoutes } from './routes.js';

export function initDashboard() {
let initialDataLoadStarted = false;
let shellController = null;
let turnsController = null;

function saveSettings() {
  shellController?.saveSettings();
}

function normalizeListSortKind(value) {
  return Object.prototype.hasOwnProperty.call(ROLLUP_SORT_DEFAULTS, value) ? value : 'projects';
}

function normalizeListSortKey(kind, value) {
  const normalizedKind = normalizeListSortKind(kind);
  return ROLLUP_SORT_KEYS[normalizedKind].has(value) ? value : ROLLUP_SORT_DEFAULTS[normalizedKind].key;
}

function normalizeListSortDir(value) {
  return value === 'asc' ? 'asc' : 'desc';
}

function defaultListSortDir(kind, key) {
  const normalizedKind = normalizeListSortKind(kind);
  if (normalizedKind === 'projects') return key === 'session' ? 'asc' : 'desc';
  if (normalizedKind === 'tools') return key === 'tool_name' ? 'asc' : 'desc';
  if (normalizedKind === 'subagents') return key === 'confidence' ? 'asc' : 'desc';
  return 'desc';
}

function listTableSortState(kind) {
  const normalizedKind = normalizeListSortKind(kind);
  const sort = state.listSorts[normalizedKind] || ROLLUP_SORT_DEFAULTS[normalizedKind];
  return {
    key: sort.key,
    dir: sort.dir,
    attribute: 'data-list-sort',
    defaultDir: key => defaultListSortDir(normalizedKind, key),
  };
}

function sortSettingsSnapshot() {
  return {
    ...turnsController.settingsSnapshot(),
    listSorts: state.listSorts,
  };
}

function restoreSortSettings(settings) {
  turnsController.restoreSettings(settings);
  Object.keys(ROLLUP_SORT_DEFAULTS).forEach(kind => {
    const saved = (settings.listSorts || {})[kind] || {};
    const key = normalizeListSortKey(kind, saved.key);
    state.listSorts[kind] = {
      key,
      dir: saved.dir === 'asc' || saved.dir === 'desc'
        ? normalizeListSortDir(saved.dir)
        : defaultListSortDir(kind, key),
    };
  });
}

function params() {
  const sessionId = sessionFilterValue();
  const q = new URLSearchParams({ days: timeRangeDaysValue() });
  q.set('session_label_mode', state.sessionLabelMode);
  if (sessionId) q.set('session_id', sessionId);
  return q;
}

function sessionsPath(page = state.listPages.projects || 1) {
  const q = params();
  q.set('per_page', String(state.turnPageSize));
  q.set('sessions_page', String(page));
  q.set('session_sort', state.listSorts.projects.key);
  q.set('session_sort_dir', state.listSorts.projects.dir);
  return '/api/sessions?' + q;
}

function toolsPath(page = state.listPages.tools || 1) {
  const q = params();
  q.set('per_page', String(state.turnPageSize));
  q.set('tools_page', String(page));
  q.set('tool_sort', state.listSorts.tools.key);
  q.set('tool_sort_dir', state.listSorts.tools.dir);
  return '/api/tools?' + q;
}

function subagentsPath() {
  const q = params();
  q.set('subagent_sort', state.listSorts.subagents.key);
  q.set('subagent_sort_dir', state.listSorts.subagents.dir);
  return '/api/subagents?' + q;
}

const detailRoutes = createDetailRoutes(params);

function resetListPages() {
  state.listPages = { projects: 1, tools: 1 };
}

function resetAllPages() {
  turnsController.resetPage();
  resetListPages();
}

function setListSort(kind, key, trigger = null) {
  const normalizedKind = normalizeListSortKind(kind);
  const previous = {
    sort: {...(state.listSorts[normalizedKind] || ROLLUP_SORT_DEFAULTS[normalizedKind])},
    page: state.listPages[normalizedKind],
  };
  const nextKey = normalizeListSortKey(normalizedKind, key);
  const current = state.listSorts[normalizedKind] || ROLLUP_SORT_DEFAULTS[normalizedKind];
  if (current.key === nextKey) {
    state.listSorts[normalizedKind] = { key: nextKey, dir: current.dir === 'asc' ? 'desc' : 'asc' };
  } else {
    state.listSorts[normalizedKind] = { key: nextKey, dir: defaultListSortDir(normalizedKind, nextKey) };
  }
  if (normalizedKind in state.listPages) state.listPages[normalizedKind] = 1;
  saveSettings();
  const rootId = {projects: 'projects', tools: 'tool-output', subagents: 'subagent-rollups'}[normalizedKind];
  safeLoadListPage(
    normalizedKind,
    1,
    () => {
      state.listSorts[normalizedKind] = previous.sort;
      if (normalizedKind in state.listPages) state.listPages[normalizedKind] = previous.page || 1;
      saveSettings();
    },
    () => restoreReplacedControlFocus(trigger, `#${rootId} [data-list-sort="${CSS.escape(nextKey)}"]`),
  );
}

function setLoading() {
  setPanelContent('projects', tableLoadingPanel('Loading session rows.', 14, 4), 'loading');
  setPanelContent('session-detail', sessionDetailLoadingPanel('Loading session detail.'), 'loading');
  setPanelContent('turn-list', tableLoadingPanel('Loading turn rows.', 16, 5), 'loading');
  setPanelContent('tool-output', tableLoadingPanel('Loading tool rows.', 16, 4), 'loading');
  setPanelContent('tool-detail', detailGridLoadingPanel('Loading tool detail.'), 'loading');
  setPanelContent('subagent-rollups', tableLoadingPanel('Loading attribution rows.', 5, 4), 'loading');
  setPanelContent('subagent-mix', detailGridLoadingPanel('Loading attribution detail.', 6, 4, 4, 4), 'loading');
  state.selectedSession = null;
  state.sessionSeq += 1;
  document.getElementById('session-detail-status').textContent = 'select a session';
  state.selectedTool = null;
  state.toolSeq += 1;
  document.getElementById('tool-detail-status').textContent = 'select a row';
  state.selectedSubagentConfidence = null;
  state.subagentSeq += 1;
  document.getElementById('subagent-detail-status').textContent = 'select a row';
  document.getElementById('turn-pager').innerHTML = '';
  turnsController.resetSelection();
  document.getElementById('turn-count').textContent = '';
  document.getElementById('detail-status').textContent = 'none';
  setPanelContent('detail', 'Select a row to inspect details.', 'empty');
  setPanelContent('subagent-mix', 'Select a row to inspect details.', 'empty');
  clearListPagers();
  document.getElementById('summary').innerHTML = [
    metric('Analyzed Turns', '...'),
    metric('Cost Units', '...'),
    metric('Total Tokens', '...'),
    metric('Cached Input', '...'),
    metric('Non-Cached Input', '...'),
    metric('Model Calls', '...'),
    metric('Tool Calls', '...'),
  ].join('');
  refreshScrollFades();
}

function freshnessIndicator(freshness) {
  const data = freshness || {};
  const status = String(data.status || 'unknown');
  const pendingRows = Number(data.pending_raw_rows || 0);
  const pendingAnalysisRows = Number(data.pending_analysis_rows ?? pendingRows);
  const pendingRecoveryFiles = Number(data.pending_recovery_files || 0);
  let title = '';
  if (status === 'needs_analyze' && pendingAnalysisRows > 0) {
    title = `${compactNumber(pendingAnalysisRows)} rows pending`;
  } else if (status === 'needs_analyze' && pendingRecoveryFiles > 0) {
    title = `${compactNumber(pendingRecoveryFiles)} files pending recovery`;
  } else if (status === 'degraded' || data.data_health === 'degraded') {
    const warnings = Array.isArray(data.warnings) ? data.warnings : [];
    const firstWarning = warnings.length ? String(warnings[0].code || '') : '';
    title = firstWarning || 'Data warning';
  } else {
    if (status !== 'current') return '';
    title = 'global current';
  }
  return `<span class="metric-freshness-dot" data-freshness-state="${esc(status)}" data-tooltip="${esc(title)}" aria-label="${esc(title)}" tabindex="0"></span>`;
}

function pageRows(payload) {
  return Array.isArray(payload) ? payload : ((payload || {}).rows || []);
}

function targetRow(rows, key, selected) {
  return rows.find(row => String(row[key] || '') === String(selected || '')) || rows[0] || null;
}

async function cachedValue(path) {
  const cached = peekCachedJSON(path);
  return cached.hit ? cached.data : getCachedJSON(path);
}

async function prepareDetail(key, path) {
  try {
    return { key, data: await cachedValue(path) };
  } catch (error) {
    return { key, error };
  }
}

async function prepareSessionDetail(payload) {
  const row = targetRow(pageRows(payload), 'session_id', state.selectedSession);
  if (!row) return null;
  const key = row.session_id || '';
  return prepareDetail(key, detailRoutes.session(key));
}

async function prepareToolDetail(payload) {
  const row = targetRow(pageRows(payload), 'tool_name', state.selectedTool);
  if (!row) return null;
  const key = row.tool_name || '';
  return prepareDetail(key, detailRoutes.tool(key));
}

async function prepareSubagentDetail(payload) {
  const row = targetRow(pageRows(payload), 'confidence', state.selectedSubagentConfidence);
  if (!row) return null;
  const key = row.confidence || '';
  return prepareDetail(key, detailRoutes.subagent(key));
}

function prefetchNextPage(payload, pathForPage, detailPathForRow) {
  const total = Number((payload || {}).total || 0);
  const perPage = Math.max(1, Number((payload || {}).per_page || state.turnPageSize));
  const page = Math.max(1, Number((payload || {}).page || 1));
  if (page >= Math.max(1, Math.ceil(total / perPage))) return;
  prefetchJSON(pathForPage(page + 1)).then(nextPayload => {
    const first = pageRows(nextPayload)[0];
    if (first) prefetchJSON(detailPathForRow(first));
  });
}

async function loadRollupData({
  seq,
  sequenceKey,
  path,
  busy = false,
  busyKind = null,
  prepare,
  render,
  prefetch = null,
}) {
  const listSeq = ++state[sequenceKey];
  if (busyKind && busy && !peekCachedJSON(path).hit) setListPagerBusy(busyKind, true);
  try {
    const payload = await cachedValue(path);
    const prepared = await prepare(payload);
    if (seq !== state.requestSeq || listSeq !== state[sequenceKey]) return false;
    render(payload, prepared);
    if (prepared?.error) showQueryError(prepared.error.message || prepared.error);
    if (typeof prefetch === 'function') prefetch(payload);
    refreshScrollFades();
    return true;
  } catch (error) {
    if (seq !== state.requestSeq || listSeq !== state[sequenceKey]) return false;
    throw error;
  } finally {
    if (busyKind && listSeq === state[sequenceKey]) setListPagerBusy(busyKind, false);
  }
}

async function loadOverviewData(seq = state.requestSeq, page = state.listPages.projects || 1, busy = false) {
  const path = sessionsPath(page);
  return loadRollupData({
    seq,
    sequenceKey: 'sessionListSeq',
    path,
    busy,
    busyKind: 'projects',
    prepare: prepareSessionDetail,
    render: renderSessionList,
    prefetch: sessions => prefetchNextPage(sessions, sessionsPath, row => detailRoutes.session(row.session_id || '')),
  });
}

async function loadToolsData(seq = state.requestSeq, page = state.listPages.tools || 1, busy = false) {
  const path = toolsPath(page);
  return loadRollupData({
    seq,
    sequenceKey: 'toolListSeq',
    path,
    busy,
    busyKind: 'tools',
    prepare: prepareToolDetail,
    render: renderToolList,
    prefetch: tools => prefetchNextPage(tools, toolsPath, row => detailRoutes.tool(row.tool_name || '')),
  });
}

async function loadSubagentData(seq = state.requestSeq) {
  const path = subagentsPath();
  return loadRollupData({
    seq,
    sequenceKey: 'subagentListSeq',
    path,
    prepare: prepareSubagentDetail,
    render: (subagents, prepared) => renderSubagentList((subagents || {}).rows || [], prepared),
  });
}

function safeLoadListPage(kind, page, onError = null, onCommit = null) {
  clearQueryStatus();
  const action = kind === 'projects'
    ? loadOverviewData(state.requestSeq, page, true)
    : kind === 'tools'
      ? loadToolsData(state.requestSeq, page, true)
      : loadSubagentData(state.requestSeq);
  action
    .then(committed => {
      if (committed && typeof onCommit === 'function') onCommit();
    })
    .catch(err => {
      if (typeof onError === 'function') onError();
      showQueryError(err.message || err);
      refreshScrollFades();
    });
}

function requestListPage(kind, page) {
  safeLoadListPage(kind, page);
}

function loadVisibleRollupData(seq = state.requestSeq) {
  if (state.view === 'overview') {
    loadOverviewData(seq).catch(err => {
      if (seq === state.requestSeq) {
        document.getElementById('session-detail-status').textContent = 'error';
        setPanelContent('projects', esc(err.message || err), 'error');
        setPanelContent('session-detail', 'Unable to load session detail.', 'error');
        refreshScrollFades();
      }
    });
  } else if (state.view === 'tools') {
    loadToolsData(seq).catch(err => {
      if (seq === state.requestSeq) {
        document.getElementById('tool-detail-status').textContent = 'error';
        setPanelContent('tool-output', esc(err.message || err), 'error');
        setPanelContent('tool-detail', 'Unable to load tool detail.', 'error');
        refreshScrollFades();
      }
    });
  } else if (state.view === 'subagents') {
    loadSubagentData(seq).catch(err => {
      if (seq === state.requestSeq) {
        document.getElementById('subagent-detail-status').textContent = 'error';
        setPanelContent('subagent-rollups', esc(err.message || err), 'error');
        setPanelContent('subagent-mix', 'Unable to load attribution detail.', 'error');
        refreshScrollFades();
      }
    });
  }
}


async function load() {
  const coldStart = state.requestSeq === 0;
  const seq = ++state.requestSeq;
  const turnRequest = turnsController.beginDashboardLoad();
  state.sessionListSeq += 1;
  state.toolListSeq += 1;
  state.subagentListSeq += 1;
  try {
    if (coldStart) setLoading();
    const tq = turnsController.dashboardParams();
    tq.set('lite', '1');
    const dashboardPath = '/api/dashboard?' + tq;
    const dashboard = await getJSON(dashboardPath);
    if (seq !== state.requestSeq) return;
    setAnalyticsCacheGeneration((dashboard.freshness || {}).analytics_db_mtime_unix ?? 'missing');
    const { summary, turns } = dashboard;
    document.getElementById('summary').innerHTML = [
      metric('Analyzed Turns', compactNumber(summary.turns || 0), '', `${exactNumber(summary.turns || 0)} eligible · ${exactNumber(summary.unavailable_turns || 0)} unavailable`, freshnessIndicator(dashboard.freshness)),
      metric('Cost Units', summary.cost_complete === false ? '—' : compactNumber(summary.weighted_credits || 0, 'money'), '', summary.cost_complete === false ? `${exactNumber(summary.unpriced_turns || 0)} turns need a cost rate` : exactNumber(summary.weighted_credits || 0, 'money')),
      metric('Total Tokens', compactNumber(summary.total_tokens || 0), '', exactNumber(summary.total_tokens || 0)),
      metric('Cached Input', compactNumber(summary.cached_input_tokens || 0), '', exactNumber(summary.cached_input_tokens || 0)),
      metric('Non-Cached Input', compactNumber(summary.non_cached_input_tokens || 0), '', exactNumber(summary.non_cached_input_tokens || 0)),
      metric('Model Calls', compactNumber(summary.model_calls || 0), '', exactNumber(summary.model_calls || 0)),
      metric('Tool Calls', compactNumber(summary.tool_calls || 0), '', exactNumber(summary.tool_calls || 0)),
    ].join('');
    loadVisibleRollupData(seq);
    await turnsController.commitDashboardLoad(turns, {
      requestSeq: seq,
      cachePath: turnRequest.cachePath,
      listSeq: turnRequest.listSeq,
    });
    refreshScrollFades();
  } catch (error) {
    if (seq !== state.requestSeq) return;
    throw error;
  }
}

function safeLoad() {
  const coldStart = state.requestSeq === 0;
  clearQueryStatus();
  load().catch(err => {
    if (coldStart) setGlobalError(err.message || err);
    else showQueryError(err.message || err);
    refreshScrollFades();
  });
}

function invalidateAnalyticsQueries() {
  clearAnalyticsQueryCache();
  state.requestSeq += 1;
  turnsController.invalidateRequests();
  state.sessionListSeq += 1;
  state.toolListSeq += 1;
  state.subagentListSeq += 1;
  state.sessionSeq += 1;
  state.toolSeq += 1;
  state.subagentSeq += 1;
  state.modalSeq += 1;
}

function prepareAnalyticsReload() {
  invalidateAnalyticsQueries();
  resetAllPages();
  setLoading();
}

function setAnalyticsUnavailable(message = 'Analysis data is unavailable. Run Analyze to rebuild it.') {
  const unavailable = esc(message);
  ['projects', 'session-detail', 'turn-list', 'tool-output', 'tool-detail', 'subagent-rollups', 'subagent-mix', 'detail'].forEach(id => {
    setPanelContent(id, unavailable, 'error');
  });
  clearListPagers();
  document.getElementById('turn-pager').innerHTML = '';
  document.getElementById('summary').innerHTML = [
    metric('Analyzed Turns', 'Unavailable'),
    metric('Cost Units', 'N/A'),
    metric('Total Tokens', 'N/A'),
    metric('Cached Input', 'N/A'),
    metric('Non-Cached Input', 'N/A'),
    metric('Model Calls', 'N/A'),
    metric('Tool Calls', 'N/A'),
  ].join('');
  refreshScrollFades();
}

function safeLoadWithSessionOptions() {
  safeLoad();
  loadSessionOptions();
}

function ensureInitialDataLoad() {
  if (initialDataLoadStarted || state.requestSeq > 0) return;
  initialDataLoadStarted = true;
  loadSessionOptions().then(() => safeLoad());
}

const toolbarController = createToolbarController({
  saveSettings,
  resetAllPages,
  safeLoad: safeLoadWithSessionOptions,
});
const {
  bindToolbarControls,
  closeToolbarCustomPopover,
  restoreToolbarSettings,
  timeRangeDaysValue,
} = toolbarController;

const sessionPickerController = createSessionPickerController({
  saveSettings,
  resetAllPages,
  safeLoad,
  timeRangeDaysValue,
});
const {
  bindSessionPickerControls,
  closeSessionPicker,
  loadSessionOptions,
  restoreSessionFilter,
  sessionFilterValue,
} = sessionPickerController;

const dialogManager = createDialogManager();
const cleanupSummary = createCleanupSummary();
let costRatesController = null;
const settingsViewController = createSettingsView({
  onSelectionChange: key => costRatesController?.setActive(key === 'cost-rates'),
});

const cleanupController = createCleanupController({
  load,
  loadSessionOptions,
  prepareAnalyticsReload,
  setAnalyticsUnavailable,
  dialogManager,
  cleanupSummary,
});
const {
  deleteCleanupFiles,
  invalidateCleanupPreview,
  loadCleanup,
  resolveCleanupConfirmModal,
  setCleanupRetentionMode,
} = cleanupController;

const analyzeController = createAnalyzeController({
  load,
  loadCleanup,
  loadSessionOptions,
  prepareAnalyticsReload,
  showQueryError,
  setGlobalError,
  refreshScrollFades,
});
const {
  applyServiceActivity,
  rebuildAndRefresh,
  setAnalyzeButtonState,
} = analyzeController;

async function refreshAnalyticsAfterCostRecalculation() {
  invalidateAnalyticsQueries();
  resetAllPages();
  shellController.markAnalyticsRefreshPending();
  await loadSessionOptions();
}

costRatesController = createCostRatesController({
  refreshAnalytics: refreshAnalyticsAfterCostRecalculation,
  dialogManager,
  onModelSelected: () => settingsViewController.select('cost-rates'),
});
const serviceActivityController = createServiceActivityController();
serviceActivityController.subscribe(applyServiceActivity);
serviceActivityController.subscribe(costRatesController.applyServiceActivity);
costRatesController.setServiceActivityRefresh(serviceActivityController.refresh);

const selectedTurnController = createSelectedTurnController({
  detailRoutes,
  refreshScrollFades,
  dialogManager,
});
const {
  bindDetailControls,
  bindToolTurnLinks,
  openTurnModalFromToolLink,
  renderDetailSummary,
  turnPromptPreviewMarkup,
  updateSelectedTurnPromptOverflow,
} = selectedTurnController;

turnsController = createTurnsController({
  params,
  detailRoutes,
  saveSettings,
  cachedValue,
  prepareDetail,
  prefetchNextPage,
  renderDetailSummary,
  bindDetailControls,
  turnPromptPreviewMarkup,
});

shellController = createDashboardShell({
  restoreToolbarSettings,
  restoreSessionFilter,
  sessionFilterValue,
  setCleanupRetentionMode,
  restoreSortSettings,
  sortSettingsSnapshot,
  resetAllPages,
  safeLoad,
  safeLoadWithSessionOptions,
  loadCleanup,
  activateAnalyticsView: ({refreshPending}) => {
    if (refreshPending) {
      initialDataLoadStarted = true;
      safeLoadWithSessionOptions();
    } else if (state.requestSeq > 0) {
      loadVisibleRollupData(state.requestSeq);
    } else {
      ensureInitialDataLoad();
    }
  },
  updateSelectedTurnPromptOverflow,
});

const overviewRenderers = createOverviewRenderers({
  detailRoutes,
  requestListPage,
  getCachedJSON,
  peekCachedJSON,
  clearQueryStatus,
  showQueryError,
  bindToolTurnLinks,
  listTableSortState,
  setListSort,
});
const {
  clearListPagers,
  renderSessionList,
  renderToolList,
  renderSubagentList,
  setListPagerBusy,
} = overviewRenderers;

document.getElementById('refresh').addEventListener('click', () => {
  saveSettings();
  resetAllPages();
  invalidateAnalyticsQueries();
  safeLoad();
});
document.getElementById('rebuild').addEventListener('click', () => { saveSettings(); rebuildAndRefresh(); });
document.getElementById('cleanup-refresh').addEventListener('click', () => { loadCleanup(); });
document.getElementById('cleanup-delete').addEventListener('click', () => { deleteCleanupFiles(); });
document.querySelectorAll('[data-cleanup-retention-preset]').forEach(button => {
  button.addEventListener('click', () => {
    setCleanupRetentionMode(button.dataset.cleanupRetentionPreset);
    saveSettings();
    invalidateCleanupPreview('Preview loading');
    loadCleanup({preserveRows: true});
  });
});
document.getElementById('cleanup-retention-date').addEventListener('change', () => {
  setCleanupRetentionMode('custom');
  saveSettings();
  invalidateCleanupPreview('Preview loading');
  loadCleanup({preserveRows: true});
});
bindToolbarControls();
bindSessionPickerControls();
document.getElementById('cleanup-detail-modal').addEventListener('click', event => {
  if (event.target.closest('[data-cleanup-modal-delete]')) {
    deleteCleanupFiles();
    return;
  }
});
document.getElementById('cleanup-confirm-delete').addEventListener('click', () => resolveCleanupConfirmModal(true));
Object.assign(window, { compactNumber, formatBytes, setAnalyzeButtonState });
shellController.initialize();
}

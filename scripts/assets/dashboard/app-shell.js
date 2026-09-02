import {
  DEFAULT_TURN_PAGE_SIZE,
  SETTINGS_KEY,
  state,
  views,
} from './core.js';
import { normalizeCleanupRetentionMode } from './cleanup.js';
import { focusActiveViewRow, refreshScrollFades } from './dom.js';
import { normalizeSessionLabelMode } from './formatters.js';
import { selectHasValue } from './toolbar.js';

const THEME_TRANSITION_MS = 160;

function storageAvailable() {
  try {
    const key = SETTINGS_KEY + ':probe';
    localStorage.setItem(key, '1');
    localStorage.removeItem(key);
    return true;
  } catch {
    return false;
  }
}

function normalizeThemeMode(value) {
  return value === 'dark' || value === 'light' ? value : 'system';
}

function systemThemeMode() {
  return typeof window.matchMedia === 'function' && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

export function createDashboardShell({
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
  activateAnalyticsView,
  updateSelectedTurnPromptOverflow,
}) {
const canStoreSettings = storageAvailable();
const pageNav = document.querySelector('.page-nav');
const pageNavFrame = document.querySelector('.page-nav-frame');
let themeCommitTimer = 0;
let systemThemeMedia = null;
let systemThemeSync = null;
let settingsAnalyticsRefreshPending = false;

function updatePageNavOverflow() {
  if (!pageNav || !pageNavFrame) return;
  const maxScrollLeft = Math.max(0, pageNav.scrollWidth - pageNav.clientWidth);
  pageNavFrame.dataset.canScrollLeft = String(pageNav.scrollLeft > 1);
  pageNavFrame.dataset.canScrollRight = String(pageNav.scrollLeft < maxScrollLeft - 1);
}

function revealActivePageNav() {
  const activeButton = pageNav?.querySelector('.nav-btn.active');
  if (!activeButton) return;
  activeButton.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  requestAnimationFrame(updatePageNavOverflow);
}

function unbindSystemThemePreference() {
  if (!systemThemeMedia || !systemThemeSync) return;
  if (typeof systemThemeMedia.removeEventListener === 'function') {
    systemThemeMedia.removeEventListener('change', systemThemeSync);
  } else if (typeof systemThemeMedia.removeListener === 'function') {
    systemThemeMedia.removeListener(systemThemeSync);
  }
  systemThemeMedia = null;
  systemThemeSync = null;
}

function bindSystemThemePreference() {
  unbindSystemThemePreference();
  if (state.themeMode !== 'system' || typeof window.matchMedia !== 'function') return;
  systemThemeMedia = window.matchMedia('(prefers-color-scheme: dark)');
  const sync = () => {
    if (state.themeMode !== 'system') {
      unbindSystemThemePreference();
      return;
    }
    applyThemeMode('system', {suppressTransitions: true});
  };
  systemThemeSync = sync;
  if (typeof systemThemeMedia.addEventListener === 'function') {
    systemThemeMedia.addEventListener('change', sync);
  } else if (typeof systemThemeMedia.addListener === 'function') {
    systemThemeMedia.addListener(sync);
  }
}

function releaseThemeCommit() {
  document.documentElement.classList.remove('theme-commit');
  themeCommitTimer = 0;
}

function commitThemeMode(normalized, {suppressTransitions = false} = {}) {
  const root = document.documentElement;
  if (themeCommitTimer) window.clearTimeout(themeCommitTimer);
  if (suppressTransitions) root.classList.add('theme-commit');
  state.themeMode = normalized;
  document.querySelectorAll('[data-theme-mode]').forEach(button => {
    const active = button.dataset.themeMode === normalized;
    button.classList.toggle('active', active);
    button.setAttribute('aria-pressed', active ? 'true' : 'false');
  });
  root.dataset.theme = normalized === 'system' ? systemThemeMode() : normalized;
  if (suppressTransitions) {
    themeCommitTimer = window.setTimeout(releaseThemeCommit, THEME_TRANSITION_MS);
  } else {
    releaseThemeCommit();
  }
}

function applyThemeMode(mode, {transition = false, suppressTransitions = false} = {}) {
  const normalized = normalizeThemeMode(mode);
  const resolved = normalized === 'system' ? systemThemeMode() : normalized;
  const canViewTransition = transition
    && !suppressTransitions
    && document.documentElement.dataset.theme !== resolved
    && typeof document.startViewTransition === 'function'
    && !window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  document.documentElement.style.setProperty('--theme-transition-duration', `${THEME_TRANSITION_MS}ms`);
  if (canViewTransition) {
    const viewTransition = document.startViewTransition(() => commitThemeMode(normalized, {suppressTransitions: true}));
    return viewTransition.updateCallbackDone.catch(() => {});
  }
  commitThemeMode(normalized, {
    suppressTransitions: suppressTransitions || (transition && document.documentElement.dataset.theme !== resolved),
  });
  return Promise.resolve();
}

function applyThemeModeAndSave(mode) {
  const normalized = normalizeThemeMode(mode);
  unbindSystemThemePreference();
  applyThemeMode(normalized, {transition: true}).then(() => {
    bindSystemThemePreference();
    saveSettings();
  });
}

function readSettings() {
  if (!canStoreSettings) return {};
  try {
    return JSON.parse(localStorage.getItem(SETTINGS_KEY) || '{}') || {};
  } catch {
    return {};
  }
}

function persistedDaysSetting() {
  const value = document.getElementById('days').value;
  if (value === 'custom' && state.appliedDaysMode !== 'custom') return state.appliedDaysMode;
  return value;
}

function saveSettings() {
  if (!canStoreSettings) return;
  const payload = {
    view: state.view,
    days: persistedDaysSetting(),
    customDays: document.getElementById('custom-days').value,
    session_id: sessionFilterValue(),
    turnPageSize: String(state.turnPageSize),
    cleanupRetentionMode: state.cleanupRetentionMode,
    cleanupRetentionDate: document.getElementById('cleanup-retention-date').value,
    sessionLabelMode: state.sessionLabelMode,
    themeMode: state.themeMode,
    ...sortSettingsSnapshot(),
  };
  localStorage.setItem(SETTINGS_KEY, JSON.stringify(payload));
}

function restoreSettings() {
  const settings = readSettings();
  state.sessionLabelMode = normalizeSessionLabelMode(settings.sessionLabelMode);
  document.getElementById('session-label-mode').value = state.sessionLabelMode;
  applyThemeMode(normalizeThemeMode(settings.themeMode), {suppressTransitions: true});
  bindSystemThemePreference();
  restoreToolbarSettings(settings);
  restoreSessionFilter(settings);
  if (selectHasValue('turn-page-size', settings.turnPageSize)) {
    document.getElementById('turn-page-size').value = String(settings.turnPageSize);
    state.turnPageSize = Number(settings.turnPageSize || DEFAULT_TURN_PAGE_SIZE);
  }
  restoreSortSettings(settings);
  const cleanupMode = normalizeCleanupRetentionMode(settings.cleanupRetentionMode);
  if (typeof settings.cleanupRetentionDate === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(settings.cleanupRetentionDate)) {
    document.getElementById('cleanup-retention-date').value = settings.cleanupRetentionDate;
  }
  setCleanupRetentionMode(cleanupMode);
  return views.has(settings.view) ? settings.view : 'overview';
}

function setView(name, updateHash = true, { focusContent = true } = {}) {
  const view = views.has(name) ? name : 'overview';
  state.view = view;
  document.body.dataset.activeView = view;
  document.querySelectorAll('.view').forEach(section => {
    section.classList.toggle('active', section.dataset.view === view);
  });
  document.querySelectorAll('.nav-btn').forEach(button => {
    button.classList.toggle('active', button.dataset.viewTarget === view);
    button.setAttribute('aria-current', button.dataset.viewTarget === view ? 'page' : 'false');
  });
  requestAnimationFrame(revealActivePageNav);
  if (updateHash && location.hash.slice(1) !== view) history.replaceState(null, '', '#' + view);
  saveSettings();
  state.pendingViewFocus = Boolean(focusContent);
  state.pendingViewFocusOwner = focusContent && document.activeElement instanceof HTMLElement
    ? document.activeElement
    : null;
  if (view === 'cleanup') loadCleanup();
  if (view !== 'cleanup' && view !== 'settings') {
    const refreshPending = settingsAnalyticsRefreshPending;
    settingsAnalyticsRefreshPending = false;
    activateAnalyticsView({refreshPending});
  }
  if (view !== 'cleanup' && focusContent) focusActiveViewRow();
  refreshScrollFades();
  requestAnimationFrame(updateSelectedTurnPromptOverflow);
}

function markAnalyticsRefreshPending() {
  settingsAnalyticsRefreshPending = true;
}

function applyAnalyticsSettingChange({reloadSessionOptions = false} = {}) {
  resetAllPages();
  if (state.view === 'settings') {
    markAnalyticsRefreshPending();
  } else if (reloadSessionOptions) {
    safeLoadWithSessionOptions();
  } else {
    safeLoad();
  }
}

function bindControls() {
  document.getElementById('session-label-mode').addEventListener('change', event => {
    state.sessionLabelMode = normalizeSessionLabelMode(event.target.value);
    event.target.value = state.sessionLabelMode;
    saveSettings();
    applyAnalyticsSettingChange({reloadSessionOptions: true});
  });
  document.getElementById('turn-page-size').addEventListener('change', event => {
    state.turnPageSize = Number(event.target.value || DEFAULT_TURN_PAGE_SIZE);
    saveSettings();
    applyAnalyticsSettingChange();
  });
  document.querySelectorAll('[data-theme-mode]').forEach(button => {
    button.addEventListener('click', () => applyThemeModeAndSave(button.dataset.themeMode));
  });
  document.querySelectorAll('.nav-btn').forEach(button => {
    button.addEventListener('click', () => setView(button.dataset.viewTarget));
  });
  pageNav?.addEventListener('scroll', updatePageNavOverflow, { passive: true });
  window.addEventListener('hashchange', () => setView(location.hash.slice(1), false));
  window.addEventListener('resize', () => {
    revealActivePageNav();
    refreshScrollFades();
    updateSelectedTurnPromptOverflow();
    requestAnimationFrame(updateSelectedTurnPromptOverflow);
  });
}

function initialize() {
  bindControls();
  if ('scrollRestoration' in history) history.scrollRestoration = 'manual';
  const restoredView = restoreSettings();
  const hashView = location.hash.slice(1);
  setView(views.has(hashView) ? hashView : restoredView, false, {focusContent: false});
  requestAnimationFrame(() => window.scrollTo(0, 0));
}

return {
  initialize,
  markAnalyticsRefreshPending,
  saveSettings,
  setView,
};
}

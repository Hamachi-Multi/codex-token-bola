import {
  clearInteractiveRowSelection,
  handleListArrowFocus,
  refreshScrollFades,
  setInteractiveRowSelected,
  setPanelContent,
} from '../dom.js';
import { esc } from '../ui.js';

export function createListDetailView({
  rowSelector,
  buttonSelector,
  detailId,
  statusId,
  keyForRow,
  pathForRow,
  nextRequestSequence,
  isCurrentRequest,
  commit,
  reset,
  getCachedJSON,
  peekCachedJSON,
  clearQueryStatus,
  showQueryError,
  emptyMessage = 'No rows for the current filter.',
}) {
  let committedKey = '';
  let committedStatus = '';

  function isMobileDetailLayout() {
    return window.matchMedia('(max-width: 720px)').matches;
  }

  function revealMobileDetail(row) {
    if (!isMobileDetailLayout()) return;
    const panel = document.getElementById(detailId)?.closest('.panel');
    if (!panel) return;
    const bounds = panel.getBoundingClientRect();
    if (bounds.top < 0 || bounds.bottom > window.innerHeight) panel.scrollIntoView({ block: 'start' });
  }

  function returnToMobileList() {
    const rows = [...document.querySelectorAll(rowSelector)];
    const target = rows.find(row => keyForRow(row) === committedKey) || rows[0] || null;
    if (!target) return;
    target.scrollIntoView({ block: 'center' });
    (target.querySelector('.row-select-button') || target).focus({ preventScroll: true });
  }

  function bindMobileReturn() {
    const button = document.querySelector(`[data-mobile-detail-back="${CSS.escape(detailId)}"]`);
    if (!button || button.dataset.mobileDetailBackBound === '1') return;
    button.dataset.mobileDetailBackBound = '1';
    button.addEventListener('click', returnToMobileList);
  }

  function statusElement() {
    return document.getElementById(statusId);
  }

  function rememberRenderedSelection() {
    const selected = document.querySelector(`${rowSelector}.selected`);
    if (!selected || selected.hasAttribute('aria-busy')) return;
    committedKey = keyForRow(selected);
    committedStatus = statusElement().textContent;
  }

  function selectPending(row) {
    rememberRenderedSelection();
    clearInteractiveRowSelection(rowSelector);
    setInteractiveRowSelected(row, true);
  }

  function restoreCommittedSelection() {
    const target = [...document.querySelectorAll(rowSelector)]
      .find(row => keyForRow(row) === committedKey) || null;
    clearInteractiveRowSelection(rowSelector);
    if (!target) return false;
    setInteractiveRowSelected(target, true);
    statusElement().textContent = committedStatus;
    return true;
  }

  function commitSelection(row, detail, { reveal = false } = {}) {
    clearInteractiveRowSelection(rowSelector);
    setInteractiveRowSelected(row, true);
    commit(row, detail);
    committedKey = keyForRow(row);
    committedStatus = statusElement().textContent;
    if (reveal) requestAnimationFrame(() => revealMobileDetail(row));
  }

  function renderEmpty() {
    committedKey = '';
    committedStatus = '';
    reset();
    statusElement().textContent = 'none';
    setPanelContent(detailId, emptyMessage, 'empty');
  }

  function renderError(error) {
    committedKey = '';
    committedStatus = '';
    reset();
    statusElement().textContent = 'error';
    setPanelContent(detailId, esc(error?.message || error), 'error');
  }

  async function select(row, preparedDetail, { reveal = false } = {}) {
    clearQueryStatus();
    const path = pathForRow(row);
    const requestSequence = nextRequestSequence();
    if (preparedDetail !== undefined) {
      commitSelection(row, preparedDetail, { reveal });
      return;
    }
    const cached = peekCachedJSON(path);
    if (cached.hit) {
      commitSelection(row, cached.data, { reveal });
      return;
    }
    selectPending(row);
    row.setAttribute('aria-busy', 'true');
    try {
      const detail = await getCachedJSON(path);
      if (!isCurrentRequest(requestSequence) || !row.isConnected) return;
      commitSelection(row, detail, { reveal });
    } catch (error) {
      if (isCurrentRequest(requestSequence) && row.isConnected) {
        if (!restoreCommittedSelection()) renderError(error);
        showQueryError(error?.message || error);
        refreshScrollFades();
      }
    } finally {
      if (row.isConnected) row.removeAttribute('aria-busy');
    }
  }

  function bindRows() {
    bindMobileReturn();
    document.querySelectorAll(rowSelector).forEach(row => {
      row.addEventListener('click', () => select(row, undefined, { reveal: true }));
    });
    document.querySelectorAll(buttonSelector).forEach(button => {
      button.addEventListener('keydown', event => handleListArrowFocus(event, buttonSelector, true));
    });
  }

  function activateRendered({ isSelected, prepared = null }) {
    const rows = [...document.querySelectorAll(rowSelector)];
    const target = rows.find(isSelected) || rows[0] || null;
    if (!target) {
      renderEmpty();
      return;
    }
    if (prepared?.error && prepared.key === keyForRow(target)) {
      renderError(prepared.error);
      return;
    }
    if (prepared && prepared.key === keyForRow(target)) {
      select(target, prepared.data);
      return;
    }
    select(target);
  }

  return { activateRendered, bindRows, select };
}

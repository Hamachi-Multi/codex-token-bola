"""Mobile browser checks for the Codex Token Bola dashboard."""

from __future__ import annotations

from playwright_dashboard_helpers import assert_true, open_dashboard


def check_mobile_detail_navigation(page, *, view: str, row_selector: str, detail_id: str) -> None:
    page.locator(f'button[data-view-target="{view}"]').click()
    page.wait_for_selector(row_selector, timeout=10_000)
    page.evaluate("window.scrollTo(0, 0)")
    rows = page.locator(row_selector)
    row = rows.nth(1 if rows.count() > 1 else 0)
    row.evaluate("el => { el.dataset.mobileReturnTarget = '1'; }")
    row.locator(".row-select-button").click()
    page.wait_for_function(
        """
        (detailId) => {
          const panel = document.getElementById(detailId)?.closest('.panel');
          if (!panel) return false;
          const bounds = panel.getBoundingClientRect();
          return bounds.top >= -1 && bounds.top < window.innerHeight;
        }
        """,
        arg=detail_id,
        timeout=10_000,
    )
    back = page.locator(f'[data-mobile-detail-back="{detail_id}"]')
    assert_true(back.is_visible(), f"{view} mobile detail should expose a list return control")
    back.click()
    page.wait_for_function(
        """
        (rowSelector) => {
          const active = document.activeElement;
          const row = active?.closest(rowSelector);
          if (!row || !active.matches('.row-select-button')) return false;
          const bounds = row.getBoundingClientRect();
          return row.dataset.mobileReturnTarget === '1'
            && bounds.bottom > 0
            && bounds.top < window.innerHeight;
        }
        """,
        arg=row_selector,
        timeout=10_000,
    )


def check_mobile(page, base_url: str) -> None:
    open_dashboard(page, base_url)
    page.locator('button[data-view-target="turns"]').click()
    page.wait_for_selector("#turn-list tr[data-turn]", timeout=10_000)

    mobile_state = page.evaluate(
        """
        () => {
          const doc = document.documentElement;
          const first = document.querySelector('#turn-list tr[data-turn]');
          const prompt = first ? first.querySelector('td:nth-child(3)') : null;
          const dateTimeCell = first ? first.querySelector('td:nth-child(1)') : null;
          const sessionCell = first ? first.querySelector('td:nth-child(2)') : null;
          const panelHeadHeights = Array.from(document.querySelectorAll('[data-view="turns"] .panel > .panel-head'))
            .map((head) => Math.round(head.getBoundingClientRect().height * 1000) / 1000);
          return {
            scrollWidth: doc.scrollWidth,
            clientWidth: doc.clientWidth,
            promptDisplay: prompt ? getComputedStyle(prompt).display : null,
            dateTimeDisplay: dateTimeCell ? getComputedStyle(dateTimeCell).display : null,
            sessionDisplay: sessionCell ? getComputedStyle(sessionCell).display : null,
            panelHeadHeights,
          };
        }
        """
    )
    assert_true(
        mobile_state["scrollWidth"] <= mobile_state["clientWidth"] + 1,
        f"mobile page overflows horizontally: {mobile_state}",
    )
    assert_true(mobile_state["promptDisplay"] != "none", f"mobile prompt cell hidden: {mobile_state}")
    assert_true(mobile_state["dateTimeDisplay"] == "none", f"mobile date-time column should collapse into row meta: {mobile_state}")
    assert_true(mobile_state["sessionDisplay"] == "none", f"mobile session column should collapse into row meta: {mobile_state}")
    assert_true(
        mobile_state["panelHeadHeights"] == [52, 52],
        f"mobile turn panel headers should share one height: {mobile_state}",
    )

    for detail_case in (
        {"view": "overview", "row_selector": "#projects tr[data-session-id]", "detail_id": "session-detail"},
        {"view": "turns", "row_selector": "#turn-list tr[data-turn]", "detail_id": "detail"},
        {"view": "tools", "row_selector": "#tool-output tr[data-tool]", "detail_id": "tool-detail"},
        {"view": "subagents", "row_selector": "#subagent-rollups tr[data-confidence]", "detail_id": "subagent-mix"},
    ):
        check_mobile_detail_navigation(page, **detail_case)

    page.set_viewport_size({"width": 1280, "height": 844})
    page.locator('button[data-view-target="overview"]').click()
    overview_rows = page.locator("#projects tr[data-session-id]")
    desktop_row = overview_rows.nth(1 if overview_rows.count() > 1 else 0)
    desktop_session = desktop_row.get_attribute("data-session-id") or ""
    desktop_row.locator(".row-select-button").click()
    page.wait_for_function(
        """
        (sessionId) => document.querySelector('#projects tr.selected')?.dataset.sessionId === sessionId
          && !document.querySelector('#projects tr.selected').hasAttribute('aria-busy')
        """,
        arg=desktop_session,
        timeout=10_000,
    )
    page.set_viewport_size({"width": 390, "height": 844})
    page.locator('[data-mobile-detail-back="session-detail"]').click()
    page.wait_for_function(
        """
        (sessionId) => {
          const active = document.activeElement;
          const row = active?.closest('#projects tr[data-session-id]');
          if (!row || row.dataset.sessionId !== sessionId) return false;
          const bounds = row.getBoundingClientRect();
          return bounds.bottom > 0 && bounds.top < window.innerHeight;
        }
        """,
        arg=desktop_session,
        timeout=10_000,
    )

    page.locator('button[data-view-target="settings"]').click()
    settings_mobile_state = page.evaluate(
        """
        () => {
          const doc = document.documentElement;
          const layout = document.querySelector('.settings-master-detail');
          const listPanel = document.querySelector('.settings-list-panel');
          const detailPanel = document.querySelector('.settings-detail-panel');
          const toggle = document.querySelector('.settings-theme-toggle');
          return {
            scrollWidth: doc.scrollWidth,
            clientWidth: doc.clientWidth,
            toolbarDisplay: getComputedStyle(document.querySelector('.toolbar')).display,
            filterDisplay: getComputedStyle(document.querySelector('.custom-filter-control')).display,
            sessionDisplay: getComputedStyle(document.querySelector('.session-control')).display,
            refreshDisplay: getComputedStyle(document.querySelector('#refresh')).display,
            analyzeDisplay: getComputedStyle(document.querySelector('#rebuild')).display,
            layoutColumns: getComputedStyle(layout).gridTemplateColumns,
            listWidth: Math.round(listPanel.getBoundingClientRect().width),
            detailWidth: Math.round(detailPanel.getBoundingClientRect().width),
            listBottom: Math.round(listPanel.getBoundingClientRect().bottom),
            detailTop: Math.round(detailPanel.getBoundingClientRect().top),
            listHeight: Math.round(listPanel.getBoundingClientRect().height),
            listContentBottom: Math.round(listPanel.querySelector('.settings-list-item:last-child').getBoundingClientRect().bottom),
            toggleWidth: Math.round(toggle.getBoundingClientRect().width),
            themeTextDisplay: getComputedStyle(document.querySelector('[data-theme-mode="light"] .theme-toggle-text')).display,
          };
        }
        """
    )
    assert_true(
        settings_mobile_state["scrollWidth"] <= settings_mobile_state["clientWidth"] + 1,
        f"mobile settings page overflows horizontally: {settings_mobile_state}",
    )
    assert_true(settings_mobile_state["toolbarDisplay"] == "grid", f"mobile settings should preserve the shared toolbar: {settings_mobile_state}")
    assert_true(
        {settings_mobile_state[key] for key in ("filterDisplay", "sessionDisplay", "refreshDisplay", "analyzeDisplay")} == {"none"},
        f"mobile settings should hide analytics-only toolbar controls: {settings_mobile_state}",
    )
    assert_true(" " not in settings_mobile_state["layoutColumns"], f"mobile settings should stack list and detail panels: {settings_mobile_state}")
    assert_true(
        settings_mobile_state["detailTop"] >= settings_mobile_state["listBottom"]
        and settings_mobile_state["listWidth"] == settings_mobile_state["detailWidth"]
        and settings_mobile_state["toggleWidth"] <= settings_mobile_state["detailWidth"],
        f"mobile settings list and selected detail should fit one stacked column: {settings_mobile_state}",
    )
    assert_true(
        settings_mobile_state["listBottom"] - settings_mobile_state["listContentBottom"] <= 2,
        f"mobile settings list should not reserve empty vertical space: {settings_mobile_state}",
    )
    assert_true(settings_mobile_state["themeTextDisplay"] != "none", f"mobile settings should keep theme labels visible: {settings_mobile_state}")
    assert_true(
        page.locator('[data-settings-detail="general"]').is_visible()
        and page.locator("#turn-page-size").evaluate("select => select.getBoundingClientRect().width <= select.parentElement.getBoundingClientRect().width"),
        "mobile General settings should keep all controls inside the detail panel",
    )

    page.locator('button[data-view-target="cleanup"]').click()
    page.wait_for_selector("#cleanup-files tr[data-cleanup-file]", timeout=10_000)
    cleanup_mobile_state = page.evaluate(
        """
        () => {
          const doc = document.documentElement;
          const summary = document.querySelector('.cleanup-selection-summary');
          const form = document.querySelector('.cleanup-retention-form');
          const allOption = document.querySelector('.cleanup-all-option');
          const actions = document.querySelector('.cleanup-action-row');
          const presets = document.querySelector('.cleanup-retention-presets');
          const firstRow = document.querySelector('#cleanup-files tbody tr');
          const panelHeadHeights = Array.from(document.querySelectorAll('[data-view="cleanup"] .panel > .panel-head'))
            .map((head) => Math.round(head.getBoundingClientRect().height * 1000) / 1000);
          return {
            scrollWidth: doc.scrollWidth,
            clientWidth: doc.clientWidth,
            filterDisplay: getComputedStyle(document.querySelector('.custom-filter-control')).display,
            sessionDisplay: getComputedStyle(document.querySelector('.session-control')).display,
            refreshDisplay: getComputedStyle(document.querySelector('#refresh')).display,
            analyzeDisplay: getComputedStyle(document.querySelector('#rebuild')).display,
            cleanupRefreshLabel: document.querySelector('#cleanup-refresh')?.textContent?.trim() || '',
            summaryColumns: summary ? getComputedStyle(summary).gridTemplateColumns : '',
            formDisplay: form ? getComputedStyle(form).display : '',
            allOptionDisplay: allOption ? getComputedStyle(allOption).display : '',
            actionColumns: actions ? getComputedStyle(actions).gridTemplateColumns : '',
            presetOverflowX: presets ? getComputedStyle(presets).overflowX : '',
            presetWrap: presets ? getComputedStyle(presets).flexWrap : '',
            firstRowWidth: firstRow ? Math.round(firstRow.getBoundingClientRect().width * 1000) / 1000 : 0,
            tableWidth: document.querySelector('#cleanup-files table')?.getBoundingClientRect().width || 0,
            mobileLabels: document.querySelectorAll('#cleanup-files td[data-label]').length,
            panelHeadHeights,
          };
        }
        """
    )
    assert_true(
        cleanup_mobile_state["scrollWidth"] <= cleanup_mobile_state["clientWidth"] + 1,
        f"mobile cleanup overflows horizontally: {cleanup_mobile_state}",
    )
    assert_true(
        {cleanup_mobile_state[key] for key in ("filterDisplay", "sessionDisplay", "refreshDisplay", "analyzeDisplay")} == {"none"}
        and cleanup_mobile_state["cleanupRefreshLabel"] == "Refresh Preview",
        f"mobile cleanup should expose only cleanup-specific controls: {cleanup_mobile_state}",
    )
    assert_true(len(cleanup_mobile_state["summaryColumns"].split(" ")) == 1, f"mobile cleanup summary should be one column: {cleanup_mobile_state}")
    assert_true(cleanup_mobile_state["formDisplay"] == "grid", f"mobile cleanup controls should stack as grid: {cleanup_mobile_state}")
    assert_true(cleanup_mobile_state["allOptionDisplay"] == "", f"mobile cleanup should not expose a separate all-data control: {cleanup_mobile_state}")
    assert_true(" " in cleanup_mobile_state["actionColumns"], f"mobile cleanup actions should use two columns: {cleanup_mobile_state}")
    assert_true(
        cleanup_mobile_state["presetOverflowX"] == "visible" and cleanup_mobile_state["presetWrap"] == "wrap",
        f"mobile cleanup presets should wrap without hidden options: {cleanup_mobile_state}",
    )
    assert_true(
        cleanup_mobile_state["firstRowWidth"] <= cleanup_mobile_state["clientWidth"]
        and cleanup_mobile_state["tableWidth"] <= cleanup_mobile_state["clientWidth"]
        and cleanup_mobile_state["mobileLabels"] >= 3,
        f"mobile cleanup rows should expose impact details without horizontal scrolling: {cleanup_mobile_state}",
    )
    assert_true(
        cleanup_mobile_state["panelHeadHeights"] == [52, 52],
        f"mobile cleanup panel headers should share one height: {cleanup_mobile_state}",
    )

"use strict";

/*
 * ntfsmnt client behaviour. No dependencies beyond the vendored htmx global.
 *
 * Every listener is delegated from `document` / `document.body`, because the
 * volumes region is replaced wholesale by htmx swaps and SSE refreshes.
 */
(function () {
  const EVENTS_URL = "/api/v1/events";
  const REGION_URL = "/partials/volumes";
  const LOST_MESSAGE = "Lost connection to ntfsmnt. Retrying…";
  const MOBILE_QUERY = "(max-width: 899.98px)";

  let pendingRefresh = false;

  /* The selection the client wants, independent of the DOM. `null` means
     "derive it from the rendered .volume-row.is-selected"; `""` is the
     explicit no-selection state used by the mobile list view. */
  let desiredSelection = null;

  /* Region refreshes are single-flight with one trailing request. A replay
     burst therefore collapses to at most two GETs. */
  let inFlight = false;
  let queuedRefresh = false;
  let queuedSelection;
  let queuedExplicit = false;

  /* True when the in-flight region GET came from a user action (selecting a
     volume or returning to the list) rather than an automatic refresh. */
  let regionExplicit = false;
  let activeSettle = null;
  let regionToken = 0;

  /* Actions (POST) and refreshes must not race each other. */
  let actionInFlight = false;

  let idleCallbacks = [];

  /* ── Small helpers ───────────────────────────────────────────────────── */

  function byId(id) {
    return document.getElementById(id);
  }

  function selectedId() {
    const row = document.querySelector(".volume-row.is-selected");
    return row ? row.dataset.id : null;
  }

  function currentSelection() {
    if (desiredSelection !== null) return desiredSelection;
    return selectedId() ?? "";
  }

  function rows() {
    return Array.from(document.querySelectorAll("#volume-list .volume-row"));
  }

  function isMobileLayout() {
    return (
      typeof window.matchMedia === "function" &&
      window.matchMedia(MOBILE_QUERY).matches
    );
  }

  function hasOpenConfirm() {
    return document.querySelector("[data-confirm-open]") !== null;
  }

  function hasActionError() {
    return document.querySelector("#detail .action-error") !== null;
  }

  function isPost(config) {
    return Boolean(
      config && String(config.verb || "").toLowerCase() === "post"
    );
  }

  /* ── Idle callbacks ──────────────────────────────────────────────────── */

  function runIdleCallbacks() {
    const callbacks = idleCallbacks;
    idleCallbacks = [];
    callbacks.forEach(function (callback) {
      try {
        callback();
      } catch (_) {
        /* A failed focus callback must not block the rest. */
      }
    });
  }

  function whenRegionIdle(callback) {
    if (!inFlight && !queuedRefresh) {
      callback();
      return;
    }
    idleCallbacks.push(callback);
  }

  /* ── Region refresh: single-flight + trailing coalescing ─────────────── */

  function finishRegion() {
    inFlight = false;
    activeSettle = null;

    if (queuedRefresh) {
      queuedRefresh = false;
      const next = queuedSelection;
      const nextExplicit = queuedExplicit;
      queuedSelection = undefined;
      queuedExplicit = false;
      sendRegion(next, nextExplicit);
      return;
    }

    regionExplicit = false;
    runIdleCallbacks();
  }

  function sendRegion(chosen, explicit) {
    inFlight = true;
    regionExplicit = explicit === true;
    const token = ++regionToken;
    let settled = false;

    function settle() {
      if (settled || token !== regionToken) return;
      settled = true;
      if (activeSettle === settle) activeSettle = null;
      finishRegion();
    }
    activeSettle = settle;

    const url =
      REGION_URL + "?selected=" + encodeURIComponent(chosen ?? "");
    try {
      const request = window.htmx.ajax("GET", url, {
        target: "#volumes-region",
        swap: "outerHTML",
      });
      if (request && typeof request.then === "function") {
        request.then(settle, settle);
      } else {
        settle();
      }
    } catch (_) {
      settle();
    }
  }

  function requestRegion(id, explicit) {
    if (!window.htmx) return false;
    if (!byId("volumes-region")) return false;

    const chosen = id === undefined ? currentSelection() : id;
    const isExplicit = explicit === true;

    if (inFlight) {
      queuedRefresh = true;
      queuedSelection = chosen;
      /* Never downgrade a pending explicit request to an automatic one. */
      queuedExplicit = queuedExplicit || isExplicit;
      return true;
    }
    sendRegion(chosen, isExplicit);
    return true;
  }

  /* Guided recovery wins: an inline action error stays until the next
     successful action (or a swap that clears it) flushes the refresh. */
  function refreshIfIdle() {
    if (hasOpenConfirm()) {
      pendingRefresh = true;
      return false;
    }
    if (actionInFlight) {
      pendingRefresh = true;
      return false;
    }
    if (hasActionError()) {
      pendingRefresh = true;
      return false;
    }
    pendingRefresh = false;
    return requestRegion();
  }

  /* ── Focus after swaps ───────────────────────────────────────────────── */

  function focusAfterSelect(id) {
    if (isMobileLayout()) {
      const title = byId("detail-title");
      if (title) {
        title.tabIndex = -1;
        title.focus();
        return;
      }
    }
    const row = rows().find((candidate) => candidate.dataset.id === id);
    if (row) row.focus();
  }

  function focusConfirmTrigger() {
    const trigger = document.querySelector("[data-confirm-trigger]");
    if (trigger) trigger.focus();
  }

  function reconcileDesiredSelection() {
    if (desiredSelection === null || desiredSelection === "") return;
    const exists = rows().some(
      (row) => row.dataset.id === desiredSelection
    );
    if (!exists) desiredSelection = null;
  }

  /* ── Activity filter and result announcements ────────────────────────── */

  /* The chosen filter survives region and detail swaps in this variable. */
  let activityFilter = "volume";

  /* True while an operation holds the lock; it keeps the confirm submit
     disabled and tells the release path what to restore. */
  let operationBusy = false;

  /* The activity entry already announced, so a re-render stays quiet. */
  let announcedActivity = null;

  function activitySection() {
    return document.querySelector("[data-activity-for]");
  }

  function activityRows() {
    const section = activitySection();
    return section ? Array.from(section.querySelectorAll(".log__row")) : [];
  }

  function applyActivityFilter() {
    const section = activitySection();
    if (!section) return;

    const label = section.dataset.activityFor || "";
    const showAll = activityFilter === "all";
    let visible = 0;

    activityRows().forEach(function (row) {
      /* ActivityEntry carries only the volume label, so that is the key. */
      const match = showAll || (row.dataset.activityVolume || "") === label;
      row.hidden = !match;
      if (match) visible += 1;
    });

    Array.from(section.querySelectorAll("[data-activity-filter]")).forEach(
      function (button) {
        const active =
          button.dataset.activityFilter === (showAll ? "all" : "volume");
        button.setAttribute("aria-pressed", active ? "true" : "false");
        button.classList.toggle("is-active", active);
      }
    );

    const empty = section.querySelector("[data-activity-filter-empty]");
    if (empty) {
      const hasList = section.querySelector(".log") !== null;
      empty.hidden = showAll || !hasList || visible > 0;
    }
  }

  /* Text only: the announced string is assembled from rendered text nodes,
     never from HTML. */
  function announceActivityResult() {
    const live = byId("activity-live");
    if (!live) return;

    const row = activityRows().find(function (candidate) {
      return !candidate.hidden;
    });
    if (!row) {
      if (live.textContent !== "") live.textContent = "";
      announcedActivity = null;
      return;
    }

    function textOf(selector) {
      const node = row.querySelector(selector);
      return node ? node.textContent.trim() : "";
    }

    const head =
      textOf(".log__action") + " " + textOf(".log__volume") + ": " +
      textOf(".log__outcome") + ".";
    const summary = textOf(".log__summary");
    const text = summary ? head + " " + summary : head;

    /* The same entry keeps its id across swaps, so a re-render does not
       re-announce the result the user has already heard. */
    const key = row.dataset.activityId || text;
    if (key === announcedActivity) return;
    announcedActivity = key;
    live.textContent = text;
  }

  /* ── Operation notice (one operation at a time) ──────────────────────── */

  function operationNotice() {
    return document.querySelector("[data-operation-notice]");
  }

  function disableDetailActions() {
    const detail = byId("detail");
    if (!detail) return;
    Array.from(detail.querySelectorAll('button[type="submit"]')).forEach(
      function (button) {
        if (button.disabled || button.hasAttribute("data-busy-disabled")) return;
        button.disabled = true;
        button.setAttribute("data-busy-disabled", "");
      }
    );
  }

  /* Only buttons this code disabled come back; a disabled action keeps its
     server-rendered reason. */
  function releaseDetailActions() {
    Array.from(
      document.querySelectorAll("#detail [data-busy-disabled]")
    ).forEach(function (button) {
      button.removeAttribute("data-busy-disabled");
      const form = button.closest("[data-confirm-form]");
      if (form) {
        /* The confirm submit owns its disabled state; recompute it. */
        syncConfirm(form);
      } else {
        button.disabled = false;
      }
    });
  }

  function showOperation(operation) {
    operationBusy = Boolean(operation);
    const notice = operationNotice();
    if (!notice) return;
    const detail = notice.querySelector("[data-operation-detail]");

    if (operation) {
      if (detail) {
        detail.textContent =
          ": " + String(operation.action ?? "") + " " +
          String(operation.volume ?? "");
      }
      notice.hidden = false;
      disableDetailActions();
      return;
    }

    if (detail) detail.textContent = "";
    notice.hidden = true;
    releaseDetailActions();
  }

  /* The server-rendered notice is the source of truth on load and after a
     swap, so a page opened during another tab's operation is busy too. */
  function reconcileOperation() {
    const notice = operationNotice();
    if (!notice) {
      operationBusy = false;
      return;
    }
    operationBusy = !notice.hidden;
    if (notice.hidden) {
      releaseDetailActions();
    } else {
      disableDetailActions();
    }
  }

  /* ── htmx lifecycle ──────────────────────────────────────────────────── */

  function handleBeforeRequest(event) {
    const config = event.detail && event.detail.requestConfig;
    if (isPost(config)) actionInFlight = true;
  }

  function handleAfterRequest(event) {
    const config = event.detail && event.detail.requestConfig;
    if (isPost(config)) actionInFlight = false;
  }

  function handleBeforeSwap(event) {
    const detail = event.detail;
    if (!detail || !detail.target) return;
    if (detail.target.id !== "volumes-region") return;
    if (!detail.requestConfig) return;
    if (String(detail.requestConfig.verb || "").toLowerCase() !== "get") {
      return;
    }
    if (regionExplicit) return;
    if (!hasActionError()) return;

    /* An automatic refresh must not cover the inline error the user is
       reading. Keep the refresh pending and let this request finish. */
    detail.shouldSwap = false;
    pendingRefresh = true;
    const settle = activeSettle;
    if (settle) window.setTimeout(settle, 0);
  }

  function handleAfterSwap() {
    reconcileDesiredSelection();
    applyActivityFilter();
    announceActivityResult();
    reconcileOperation();

    /* Flush a refresh deferred by an open confirm (e.g. the confirm-submit
       POST swap). Clear the flag first so this cannot loop; with a request
       already in flight the flush becomes the single trailing refresh. */
    if (pendingRefresh && !hasOpenConfirm() && !hasActionError()) {
      pendingRefresh = false;
      requestRegion();
    }
  }

  if (document.body) {
    document.body.addEventListener("htmx:beforeRequest", handleBeforeRequest);
    document.body.addEventListener("htmx:afterRequest", handleAfterRequest);
    document.body.addEventListener("htmx:beforeSwap", handleBeforeSwap);
    document.body.addEventListener("htmx:afterSwap", handleAfterSwap);
  }

  /* ── Selection ───────────────────────────────────────────────────────── */

  function selectVolume(id) {
    if (id === null || id === undefined) return;
    desiredSelection = id;
    requestRegion(id, true);
    whenRegionIdle(function () {
      focusAfterSelect(id);
    });
  }

  function showList() {
    const region = byId("volumes-region");
    if (region) region.dataset.selected = "false";

    rows().forEach(function (row) {
      row.classList.remove("is-selected");
      row.removeAttribute("aria-current");
    });

    desiredSelection = "";
    requestRegion("", true);
    whenRegionIdle(function () {
      const first = rows()[0];
      if (first) first.focus();
    });
  }

  function moveFocus(row, delta) {
    const list = rows();
    const index = list.indexOf(row);
    if (index === -1) return;
    const next = list[Math.max(0, Math.min(list.length - 1, index + delta))];
    if (next) next.focus();
  }

  /* ── Inline confirm ──────────────────────────────────────────────────── */

  function formFor(trigger) {
    const id = trigger.getAttribute("aria-controls");
    if (id) {
      const form = document.getElementById(id);
      if (form) return form;
    }
    const option = trigger.parentElement;
    return option ? option.querySelector("[data-confirm-form]") : null;
  }

  function triggerFor(form) {
    const id = form.id;
    if (!id) return null;
    const triggers = Array.from(
      document.querySelectorAll("[data-confirm-trigger]")
    );
    return (
      triggers.find(
        (trigger) => trigger.getAttribute("aria-controls") === id
      ) || null
    );
  }

  function resetConfirm(form) {
    form.hidden = true;
    form.removeAttribute("data-confirm-open");
    const trigger = triggerFor(form);
    if (trigger) trigger.setAttribute("aria-expanded", "false");
    const input = form.querySelector("[data-confirm-input]");
    if (input) input.value = "";
    const submit = form.querySelector("[data-confirm-submit]");
    if (submit) submit.disabled = true;
    return trigger;
  }

  function openConfirm(trigger) {
    if (trigger.disabled) return;
    const form = formFor(trigger);
    if (!form) return;

    const current = document.querySelector("[data-confirm-open]");
    if (current && current !== form) resetConfirm(current);

    form.hidden = false;
    form.setAttribute("data-confirm-open", "");
    trigger.setAttribute("aria-expanded", "true");

    const input = form.querySelector("[data-confirm-input]");
    if (input) {
      input.value = "";
      input.focus();
    }
    syncConfirm(form);
  }

  function closeConfirm(form, restoreFocus) {
    const trigger = resetConfirm(form);
    const shouldFocus =
      restoreFocus !== false && trigger !== null && trigger.isConnected;

    if (!pendingRefresh) {
      if (shouldFocus) trigger.focus();
      return;
    }

    if (refreshIfIdle()) {
      /* Focus the trigger only after the last region swap has landed. */
      if (shouldFocus) whenRegionIdle(focusConfirmTrigger);
    } else {
      /* Suppressed by an inline action error: restore focus immediately. */
      if (shouldFocus) trigger.focus();
    }
  }

  function syncConfirm(form) {
    const input = form.querySelector("[data-confirm-input]");
    const submit = form.querySelector("[data-confirm-submit]");
    if (!input || !submit) return;

    const label = form.dataset.label ?? "";
    const matches = input.value !== "" && input.value === label;
    /* A busy operation keeps the confirm closed. */
    submit.disabled = operationBusy || label === "" || !matches;
  }

  /* ── Copy buttons ────────────────────────────────────────────────────── */

  function flashCopied(button) {
    const original = button.textContent;
    button.dataset.copied = "true";
    button.textContent = "Copied";
    window.setTimeout(function () {
      button.textContent = original;
      delete button.dataset.copied;
    }, 1200);
  }

  function copyFrom(button) {
    const text = button.dataset.copy;
    if (!text || button.dataset.copied === "true") return;
    try {
      navigator.clipboard.writeText(text).then(
        function () {
          flashCopied(button);
        },
        function () {
          /* Clipboard unavailable — leave the button alone. */
        }
      );
    } catch (_) {
      /* navigator.clipboard may be missing outside a secure context. */
    }
  }

  /* ── SSE ─────────────────────────────────────────────────────────────── */

  function clearLostMessage() {
    const alert = byId("alert");
    if (!alert) return;
    alert.querySelectorAll("[data-live-message]").forEach(function (node) {
      node.remove();
    });
  }

  function showLostMessage() {
    const alert = byId("alert");
    if (!alert || alert.querySelector("[data-live-message]")) return;
    const message = document.createElement("p");
    message.className = "alert__message";
    message.setAttribute("data-live-message", "");
    message.textContent = LOST_MESSAGE;
    alert.appendChild(message);
  }

  function connect() {
    let stream;
    try {
      stream = new EventSource(EVENTS_URL);
    } catch (_) {
      const live = byId("live");
      if (live) live.dataset.live = "closed";
      showLostMessage();
      return;
    }

    stream.addEventListener("open", function () {
      const live = byId("live");
      if (live) live.dataset.live = "open";
      clearLostMessage();
    });

    stream.addEventListener("error", function () {
      const live = byId("live");
      if (live) live.dataset.live = "closed";
      showLostMessage();
    });

    stream.addEventListener("state", function () {
      refreshIfIdle();
    });

    /* The action publishes `activity` right before `state`, and the state
       refresh re-renders the log. Building HTML from JSON here would be an
       XSS vector, so this event is deliberately ignored. */
    stream.addEventListener("activity", function () {});

    /* `operation` carries {id, action, volume, started_at} on acquire and
       null on release. Only text is written, never markup. */
    stream.addEventListener("operation", function (event) {
      let operation = null;
      try {
        operation = JSON.parse(event.data);
      } catch (_) {
        return;
      }
      if (operation !== null && typeof operation !== "object") return;
      showOperation(operation);
    });
  }

  /* ── Delegated events ────────────────────────────────────────────────── */

  document.addEventListener("click", function (event) {
    if (!(event.target instanceof Element)) return;

    const copyButton = event.target.closest("[data-copy]");
    if (copyButton) {
      copyFrom(copyButton);
      return;
    }

    const filter = event.target.closest("[data-activity-filter]");
    if (filter) {
      activityFilter =
        filter.dataset.activityFilter === "all" ? "all" : "volume";
      applyActivityFilter();
      return;
    }

    const cancel = event.target.closest("[data-confirm-cancel]");
    if (cancel) {
      const form = cancel.closest("[data-confirm-form]");
      if (form) closeConfirm(form);
      return;
    }

    const trigger = event.target.closest("[data-confirm-trigger]");
    if (trigger) {
      openConfirm(trigger);
      return;
    }

    const back = event.target.closest("[data-show-list]");
    if (back) {
      event.preventDefault();
      showList();
      return;
    }

    const row = event.target.closest(".volume-row");
    if (row) selectVolume(row.dataset.id);
  });

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") {
      const openForm = document.querySelector("[data-confirm-open]");
      if (openForm) {
        event.preventDefault();
        closeConfirm(openForm);
        return;
      }
      const region = byId("volumes-region");
      if (region && region.dataset.selected === "true" && isMobileLayout()) {
        event.preventDefault();
        showList();
      }
      return;
    }

    if (!(event.target instanceof Element)) return;
    const row = event.target.closest(".volume-row");
    if (!row) return;

    if (event.key === "ArrowUp" || event.key === "ArrowDown") {
      event.preventDefault();
      moveFocus(row, event.key === "ArrowUp" ? -1 : 1);
      return;
    }

    if (event.key === "Enter") {
      /* Native activation is suppressed so the region loads exactly once. */
      event.preventDefault();
      selectVolume(row.dataset.id);
    }
  });

  document.addEventListener("input", function (event) {
    if (!(event.target instanceof Element)) return;
    const input = event.target.closest("[data-confirm-input]");
    if (!input) return;
    const form = input.closest("[data-confirm-form]");
    if (form) syncConfirm(form);
  });

  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible") refreshIfIdle();
  });

  applyActivityFilter();
  reconcileOperation();
  connect();
})();

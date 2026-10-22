"use strict";

/*
 * ntfsmnt client behaviour. No dependencies beyond the vendored htmx global.
 *
 * Every listener is delegated from `document`, because the volumes region is
 * replaced wholesale by htmx swaps and SSE-triggered refreshes.
 */
(function () {
  const EVENTS_URL = "/api/v1/events";
  const REGION_URL = "/partials/volumes";
  const LOST_MESSAGE = "Lost connection to ntfsmnt. Retrying…";
  const MOBILE_QUERY = "(max-width: 899.98px)";

  let pendingRefresh = false;

  /* ── Small helpers ───────────────────────────────────────────────────── */

  function byId(id) {
    return document.getElementById(id);
  }

  function selectedId() {
    const row = document.querySelector(".volume-row.is-selected");
    return row ? row.dataset.id : null;
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

  /* ── Region refresh ──────────────────────────────────────────────────── */

  function refreshRegion(id) {
    if (!window.htmx) return null;
    if (!byId("volumes-region")) return null;

    const chosen = id === undefined ? selectedId() : id;
    const url = REGION_URL + "?selected=" + encodeURIComponent(chosen ?? "");
    try {
      const request = window.htmx.ajax("GET", url, {
        target: "#volumes-region",
        swap: "outerHTML",
      });
      return request && typeof request.then === "function" ? request : null;
    } catch (_) {
      return null;
    }
  }

  function refreshIfIdle() {
    if (hasOpenConfirm()) {
      pendingRefresh = true;
      return;
    }
    pendingRefresh = false;
    refreshRegion();
  }

  function afterRequest(request, callback) {
    if (!request) {
      callback();
      return;
    }
    Promise.resolve(request).then(callback, callback);
  }

  /* ── Selection ───────────────────────────────────────────────────────── */

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

  function selectVolume(id) {
    if (id === null || id === undefined) return;
    const request = refreshRegion(id);
    afterRequest(request, function () {
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

    const first = rows()[0];
    if (first) first.focus();
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
  }

  function closeConfirm(form, restoreFocus) {
    const trigger = resetConfirm(form);
    if (restoreFocus !== false && trigger && trigger.isConnected) {
      trigger.focus();
    }
    if (pendingRefresh) {
      pendingRefresh = false;
      refreshIfIdle();
    }
  }

  function syncConfirm(form) {
    const input = form.querySelector("[data-confirm-input]");
    const submit = form.querySelector("[data-confirm-submit]");
    if (!input || !submit) return;
    submit.disabled = input.value !== (form.dataset.label ?? "");
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
  }

  /* ── Delegated events ────────────────────────────────────────────────── */

  document.addEventListener("click", function (event) {
    if (!(event.target instanceof Element)) return;

    const copyButton = event.target.closest("[data-copy]");
    if (copyButton) {
      copyFrom(copyButton);
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

  connect();
})();

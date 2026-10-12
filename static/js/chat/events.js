// chat/events.js – global event wiring for chat subsystem

import { dbg } from "../util.js?v=2";
// ---------------------------------------------------------------------------
// Session cleanup – tell backend to drop ChatState when the page unloads.
// ---------------------------------------------------------------------------
//
import { pendingBuffers, startTypingIndicator, finalizeBubble, loadTranscript } from "./stream.js?v=2";
import { getModalParts, setControlsEnabled, scrollToBottom, enhanceModal } from "./ui.js?v=2";
import { renderStaticMarkdown, ensureMarkdownLibs } from "./markdown.js?v=2";

// ---------------------------------------------------------------------------
// Chat session lifecycle
//   • Keep chat across a *simple reload* (navigation type === "reload").
//   • Purge chat when the user opens a new tab/window, navigates away, or
//     performs a hard refresh (navigation type === "navigate").
//
// We detect the navigation type on page load via the PerformanceNavigationTiming
// entry. If it's not "reload" we immediately send a beacon asking the backend
// to clear the ChatState for the existing session cookie.
// ---------------------------------------------------------------------------

(() => {
  const navEntry = performance.getEntriesByType("navigation")[0];
  const navType = navEntry && navEntry.type ? navEntry.type : "navigate";
  const hardFlag = sessionStorage.getItem("ohHardReload") === "1";

  // Conditions that SHOULD wipe the ChatState:
  //   • Fresh navigate (new tab, link, back/forward) → navType !== "reload"
  //   • Hard reload indicated by shift-key snapshot flag
  if (navType !== "reload" || hardFlag) {
    try {
      if (navigator.sendBeacon) {
        navigator.sendBeacon("/chat/clear_session");
      } else {
        fetch("/chat/clear_session", { method: "POST", keepalive: true });
      }
    } catch (_) {
      /* no-op */
    }
  }

  // Clear flag so next soft reload can restore state again.
  if (hardFlag) sessionStorage.removeItem("ohHardReload");
})();

// Submit on Enter, Shift+Enter for newline
document.addEventListener("keydown", (ev) => {
  if (ev.key !== "Enter" || ev.shiftKey) return;
  const ta = ev.target;
  if (!(ta instanceof HTMLTextAreaElement) || ta.disabled) return;

  const form = ta.closest("form");
  if (!form || form.getAttribute("hx-post") !== "/chat") return;
  ev.preventDefault();
  if (typeof form.requestSubmit === "function") form.requestSubmit();
  else form.dispatchEvent(new Event("submit", { cancelable: true }));
});

// Before HTMX sends /chat – guard empty, busy states
document.body.addEventListener("htmx:beforeRequest", (evt) => {
  const form = evt.target;
  if (!(form instanceof HTMLFormElement)) return;
  if (form.getAttribute("hx-post") !== "/chat") return;

  const textarea = form.querySelector("textarea[name='message']");
  const trimmed = (textarea?.value || "").trim();
  if (trimmed.length === 0) {
    evt.preventDefault();
    textarea?.focus();
    return;
  }
  const modal = form.closest(".modal");

  // Basic debounce – ignore submits within 600ms window
  const now = Date.now();
  if (form.dataset.lastSubmit && now - parseInt(form.dataset.lastSubmit, 10) < 600) {
    evt.preventDefault();
    return;
  }
  form.dataset.lastSubmit = String(now);

  // If an SSE stream is still open, block.
  const activeStream = modal?.querySelector("[data-stream-url]:not([data-finalised='1'])");
  if (activeStream) {
    if (typeof showToast === "function") showToast("Assistant is still replying…");
    evt.preventDefault();
    textarea?.focus();
    return;
  }

  // Note: older transcript messages may lack data-finalised attr, so we only
  // block when an assistant bubble is actively *typing* (has .typing class) or
  // a live SSE stream is open (handled above).
  const typing = modal?.querySelector(".assistant-message.typing");
  if (typing) {
    if (typeof showToast === "function") showToast("Assistant is still replying…");
    evt.preventDefault();
    return;
  }
  // Ensure stop button exists & disable controls
  getModalParts(modal); // inserts stopBtn if missing
  setControlsEnabled(modal, false);
});

// Skeleton loader for Ask-LLM standalone form
document.body.addEventListener("htmx:beforeRequest", (ev) => {
  const form = ev.target;
  if (!(form instanceof HTMLFormElement)) return;
  if (form.getAttribute("hx-post") !== "/ask-llm") return;

  const targetSel = form.getAttribute("hx-target");
  if (!targetSel) return;
  const target = document.querySelector(targetSel);
  if (!target) return;

  target.innerHTML = "<div class='skeleton' style='width:100%;height:4rem;border-radius:4px;'></div>";
});

// Clear textarea & scroll after user message sent
document.body.addEventListener("htmx:afterRequest", (evt) => {
  const elt = evt.target;
  if (elt instanceof HTMLFormElement && elt.getAttribute("hx-post") === "/chat") {
    const ta = elt.querySelector("textarea[name='message']");
    if (ta) ta.value = "";
    const modal = elt.closest(".modal");
    const chatLog = modal && modal.querySelector(".chat-log-container");
    if (chatLog) scrollToBottom(chatLog);
  }
});

// Remove skeleton after response swap
document.body.addEventListener("htmx:afterSwap", (ev) => {
  const target = ev.target;
  if (target && target.querySelector && target.querySelector(".skeleton")) {
    // If actual response is non-empty, skeleton will be overwritten already. Ensure gone.
    const skel = target.querySelector(".skeleton");
    if (skel) skel.remove();
  }
});

// Ensure cleanUp runs even when modal is removed via ESC or overlay click
document.body.addEventListener("htmx:beforeSwap", (evt) => {
  const swapEl = evt.detail?.target;
  if (!swapEl || swapEl.id !== "modal-container") return;
  const existingModal = swapEl.querySelector(".modal");
  if (existingModal && typeof existingModal._ohCleanUp === "function") {
    existingModal._ohCleanUp();
  }
});

// Chat session restore helper
// Close open modal on ESC key for accessibility
document.addEventListener("keydown", (ev) => {
  if (ev.key !== "Escape") return;
  const modal = document.querySelector(".modal");
  if (modal) {
    const closeBtn = modal.querySelector(".modal-close-btn");
    if (closeBtn) closeBtn.click();
    else modal.remove();
  }
});

// Activate close button on Enter/Space when focused (keyboard a11y)
document.addEventListener("keydown", (ev) => {
  if (!["Enter", " ", "Spacebar"].includes(ev.key)) return;
  const target = ev.target;
  if (target && target.classList && target.classList.contains("modal-close-btn")) {
    ev.preventDefault();
    target.click();
  }
});

export function initChat(sessionId, paperId, chatLogId) {
  const chatLog = document.getElementById(chatLogId || `chat-log-${paperId}`);
  if (!chatLog) return;

  // Hide during hydration to avoid raw-markdown flash & fade body later
  const prevVis = chatLog.style.visibility;
  chatLog.style.visibility = "hidden";
  const modalBody = chatLog.closest(".modal")?.querySelector(".modal-body");
  if (modalBody) {
    modalBody.style.opacity = "0";
  }

  // Pick a stable cache key: explicit data-chat-key > data-chat-pid > paperId
  const stableKey = chatLog.dataset.chatPid || chatLog.dataset.chatKey || paperId;
  const html = loadTranscript(sessionId, stableKey);
  console.debug('[oh] initChat', { stableKey, cachedChars: (html || '').length });
  if (typeof html === "string" && html.trim()) {
    chatLog.innerHTML = html;

    // Wait until markdown libs loaded before rendering & reveal
    ensureMarkdownLibs()
      .then(() => {
        renderStaticMarkdown(chatLog);
      })
      .finally(() => {
        scrollToBottom(chatLog, 50);
        chatLog.style.visibility = "visible";
        if (modalBody) {
          modalBody.style.transition = "opacity 0.3s ease";
          requestAnimationFrame(() => {
            modalBody.style.opacity = "1";
          });
        }
      });
  } else {
    // Wait until markdown libs loaded before rendering & reveal
    ensureMarkdownLibs()
      .then(() => {
        renderStaticMarkdown(chatLog);
      })
      .finally(() => {
        scrollToBottom(chatLog, 50);
        chatLog.style.visibility = "visible";
        if (modalBody) {
          modalBody.style.transition = "opacity 0.3s ease";
          requestAnimationFrame(() => {
            modalBody.style.opacity = "1";
          });
        }
      });
  }
}
window.initChat = initChat;

// AfterSwap – start typing indicator for auto-inserted assistant bubble & restore modals
document.body.addEventListener("htmx:afterSwap", (evt) => {
  const root = evt.detail?.target;
  if (!(root instanceof HTMLElement)) return;

  // Start typing indicator on .assistant-message.typing elements
  root.querySelectorAll(".assistant-message.typing").forEach((el) => {
    if (!pendingBuffers.has(el) && !el.classList.contains("interrupted")) {
      const ms = parseInt(el.dataset.batchMs || "20", 10);
      startTypingIndicator(el, ms);
    }
  });
  // If chat modal inserted without inline init, restore transcript
  root.querySelectorAll(".chat-log-container").forEach((log) => {
    if (log.dataset.initDone === "1") return;
    const pid = log.dataset.chatKey || log.dataset.chatPid || (log.id ? log.id.replace("chat-log-", "") : "");
    if (!pid) return;
    try {
      initChat(null, pid, log.id);
      log.dataset.initDone = "1";
    } catch (err) {
      console.error("initChat error", err);
    }
  });
});

// Call enhanceModal whenever a chat modal is swapped in so autosize & sanitize run
document.body.addEventListener("htmx:afterSwap", (evt) => {
  const tgt = evt.detail?.target;
  if (!tgt) return;
  // When /chat-modal returns, target will be #modal-container. The modal is its first child.
  const modal = tgt.querySelector?.(".modal");
  if (modal) enhanceModal(modal);
});

document.body.addEventListener("htmx:responseError", (e) => {
  const banner = document.getElementById("status-banner");
  if (banner) {
    banner.innerHTML = e?.detail?.xhr?.responseText || "Request failed";
    banner.style.display = "block";
    banner.setAttribute("role", "alert");
  }
});

console.debug("[oh] chat/events.js wired listeners");

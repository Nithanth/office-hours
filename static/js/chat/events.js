// chat/events.js – global event wiring for chat subsystem

import { dbg } from "../util.js";
import { pendingBuffers, startTypingIndicator, finalizeBubble, loadTranscript } from "./stream.js";
import { getModalParts, setControlsEnabled, scrollToBottom } from "./ui.js";
import { renderStaticMarkdown } from "./markdown.js";

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
  const activeStream = modal?.querySelector("[sse-connect]");
  if (activeStream) {
    evt.preventDefault();
    textarea?.focus();
    return;
  }
  // Ensure stop button exists & disable controls
  getModalParts(modal); // inserts stopBtn if missing
  setControlsEnabled(modal, false);
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

// Chat session restore helper
export function initChat(sessionId, paperId) {
  const chatLog = document.getElementById(`chat-log-${paperId}`);
  if (!chatLog) return;
  const stableKey = chatLog.dataset.chatKey || paperId;
  const html = loadTranscript(sessionId, stableKey);
  if (typeof html === "string" && html.trim()) {
    chatLog.innerHTML = html;
    renderStaticMarkdown(chatLog);
    scrollToBottom(chatLog);
  } else {
    renderStaticMarkdown(chatLog);
    scrollToBottom(chatLog);
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
      initChat(null, pid);
      log.dataset.initDone = "1";
    } catch (err) {
      console.error("initChat error", err);
    }
  });
});

console.debug("[lux] chat/events.js wired listeners");


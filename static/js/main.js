// main.js – ES module entrypoint for Lux Scholar frontend
// This file bootstraps split modules to keep the bundle manageable and ensure
// shared helpers are loaded site-wide.

// Controls for shuffle/reset buttons (defines window.disableShuffle etc.)
import "./shuffle_controls.js";

// Ensure HTMX opens multiple SSE streams concurrently (chat + report, etc.)
// Script is idempotent; importing here avoids separate <script> tag.
import "./htmx-sse-pool.js";

// Shared utilities (dbg, debounce, etc.) – side-effect free
import "./util.js";

// Chat subsystem (streams, UI, events)
import "./chat/stream.js";
import "./chat/ui.js";
import "./chat/events.js";
import "./citation.js";

// Markdown helpers – only pull the symbol we need (module still executes once)
import { ensureMarkdownLibs } from "./chat/markdown.js";
// Kick off background load early; no await needed.
ensureMarkdownLibs();

// Report page helpers (copy button, etc.)
import "./report.js";

window.__LUX_MODULES_ACTIVE = true;
console.debug("[lux] main.js loaded (ESM)");

// main.js – ES module entrypoint for Lux Scholar frontend
// This file bootstraps split modules to keep the bundle manageable.

// Controls for shuffle/reset buttons (defines window.disableShuffle etc.)
import "./shuffle_controls.js";

// Shared utilities (dbg, debounce, etc.) – side-effect free
import "./util.js";

// Chat subsystem (currently placeholders; will be populated during refactor)
import "./chat/stream.js";
import "./chat/markdown.js";
import "./chat/ui.js";
import "./chat/events.js";
import "./citation.js";

window.__LUX_MODULES_ACTIVE = true;
console.debug("[lux] main.js loaded (ESM)");

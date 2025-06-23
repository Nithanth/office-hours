// Minimal JavaScript if needed.
// For now, HTMX will handle most dynamic interactions.
// console.log("Custom app.js loaded.");

// Utility: temporarily disable/enable shuffle controls while a report is generating
const shuffleBtnIds = ["shuffle-btn", "shuffle-all-btn", "reset-btn"];
let reportBusy = false;

function disableShuffle() {
  reportBusy = true;
  shuffleBtnIds.forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.disabled = true;
  });
}

function enableShuffle() {
  reportBusy = false;
  shuffleBtnIds.forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.disabled = false;
  });
}

// Global click guard to prevent any shuffle-related requests while report is busy
document.addEventListener(
  "click",
  (evt) => {
    if (reportBusy && evt.target.closest("#shuffle-btn, #shuffle-all-btn, #reset-btn")) {
      evt.preventDefault();
      evt.stopPropagation();
    }
  },
  true
);

console.log("app.js loaded");

// Set up Lux dark-mode toggle
function setupLuxDarkToggle() {
  const toggle = document.getElementById("lux-dark-toggle");
  if (!toggle) return;
  toggle.addEventListener("click", () => {
    console.log("Dark mode toggle clicked!");
    const isDarkMode = document.body.classList.contains("lux-dark");
    if (isDarkMode) {
      document.body.classList.remove('lux-dark');
      document.body.style.backgroundColor = '';
    } else {
      document.body.classList.add('lux-dark');
      document.body.style.backgroundColor = '#1a1a1a';
    }

    if (document.body.classList.contains("lux-dark")) {
      toggle.textContent = '☀️ Light Mode';
    } else {
      toggle.textContent = '🌙 Dark Mode';
    }
  });
}

document.addEventListener("DOMContentLoaded", setupLuxDarkToggle);

// Minimal JavaScript if needed.
// For now, HTMX will handle most dynamic interactions.
// console.log("Custom app.js loaded.");
console.log("app.js loaded");

// Set up Lux dark-mode toggle
function setupLuxDarkToggle() {
  const toggle = document.getElementById("lux-dark-toggle");
  if (!toggle) return;
  toggle.addEventListener("click", () => {
    console.log("Dark mode toggle clicked!");
    document.body.classList.toggle("lux-dark");

    if (document.body.classList.contains("lux-dark")) {
      toggle.textContent = '☀️ Light Mode';
    } else {
      toggle.textContent = '🌙 Dark Mode';
    }
  });
}

document.addEventListener("DOMContentLoaded", setupLuxDarkToggle);

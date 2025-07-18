// Ensure the browser never stays on a URL containing ?search_query=…
// 1. On first page load: if the param is present, immediately redirect to '/'
//    so the server renders a clean empty page.
// 2. After any HTMX swap: if param sneaks back in, replaceState it away.

const stripSearchParam = () => {
  if (window.location.search.includes('search_query=')) {
    // Replace the current URL in history without reloading the page
    history.replaceState(null, '', '/');
  }
};

// Run once on load
stripSearchParam();

// Also run after every HTMX swap (defensive)
document.body.addEventListener('htmx:afterSwap', () => {
  if (window.location.search.includes('search_query=')) {
    history.replaceState(null, '', '/');
  }
});

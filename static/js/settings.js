// settings.js – toolbar persistence, theme toggle & form validation helpers
// Ensures UI state (audience, model, theme) persists across HTMX swaps and
// validates the report generator form before submit.
// All functionality runs inside an IIFE to avoid leaking globals (except
// explicitly exposed helpers like `syncHidden`).

(() => {
  const LS = window.localStorage;

  // ------------------------------------------------------------------
  // Helper: sync hidden inputs (debounced via rAF to avoid thrashing)
  // ------------------------------------------------------------------
  let _syncPending = null;
  function _doSyncHidden() {
    const audSel = document.getElementById('aud-select');
    const modelSel = document.getElementById('model-select');
    const audHidden = document.getElementById('aud-hidden');
    const modelHidden = document.getElementById('model-hidden');
    if (audHidden && audSel) audHidden.value = audSel.value;
    if (modelHidden && modelSel) modelHidden.value = modelSel.value;
  }
  window.syncHidden = function syncHidden() {
    if (_syncPending) return;
    _syncPending = requestAnimationFrame(() => {
      _syncPending = null;
      _doSyncHidden();
    });
  };

  function init() {
    const audSel = document.getElementById('aud-select');
    const modelSel = document.getElementById('model-select');
    const selects = [audSel, modelSel];

    const placeholderClass = 'placeholder';

    const setPlaceholderState = el => {
      if (!el) return;
      if (!el.value) {
        el.classList.add(placeholderClass);
      } else {
        el.classList.remove(placeholderClass);
      }
    };

    // ensure initial class assignment before restoring localStorage
    selects.forEach(el => el && el.classList.add(placeholderClass));

    // Debug: indicate script executed
    console.debug('[lux] settings.js initialized');

    // Do NOT restore past Audience/Model choices – always start at placeholder.

    // Sync hidden inputs once after restore
    window.syncHidden();

    selects.forEach(setPlaceholderState);

    // Initialise flatpickr date picker (moved from inline script)
    if (window.flatpickr) {
      try {
        window.flatpickr('#start_published_date', { dateFormat: 'Y-m-d' });
      } catch (e) {
        console.warn('[lux] flatpickr init failed', e);
      }
    }

    // Initialise Choices.js multiselect for domain filter
    if (window.Choices) {
      try {
        new Choices('#include_domains', {
          removeItemButton: true,
          searchEnabled: true,
          shouldSort: false,
        });
        console.debug('[lux] Choices multiselect initialised');
      } catch (e) {
        console.warn('[lux] Choices init failed', e);
      }
    }

    // Persist on change
    selects.forEach(el => {
      if (!el) return;
      el.addEventListener('change', () => {
        document.dispatchEvent(
          new CustomEvent('lux:settings-changed', { detail: { id: el.id, value: el.value } }),
        );
        setPlaceholderState(el);
        window.syncHidden();
      });
    });

    // Clear-domain button functionality
    const clearDomainsBtn = document.getElementById('clear-domains-btn');
    if (clearDomainsBtn) {
      clearDomainsBtn.addEventListener('click', () => {
        const sel = document.getElementById('include_domains');
        if (!sel) return;
        Array.from(sel.options).forEach(opt => (opt.selected = false));
        sel.dispatchEvent(new Event('change'));
        console.debug('[lux] Domain selections cleared');
      });
    }

    // ------------------------------------------------------------------
    // Simple toast utility
    // ------------------------------------------------------------------

    // Inject toast CSS once
    (function injectToastCss() {
      if (document.getElementById('lux-toast-style')) return;
      const s = document.createElement('style');
      s.id = 'lux-toast-style';
      s.textContent = `
        .lux-toast{position:fixed;bottom:24px;left:50%;transform:translateX(-50%);padding:10px 16px;border-radius:6px;z-index:9999;font-size:.9rem;font-family:Arial,sans-serif;border:1px solid transparent;opacity:0;transition:opacity .25s ease;}
        .lux-toast.show{opacity:1;}
        body:not(.lux-dark) .lux-toast{background:#ffffff;color:#000;border-color:#000}
        body.lux-dark .lux-toast{background:#333;color:#ef4444;border-color:#666}
      `;
      document.head.appendChild(s);
    })();

    function showToast(msg, ms = 3500) {
      const existing = document.querySelector('.lux-toast');
      if (existing) existing.remove();

      const toast = document.createElement('div');
      toast.className = 'lux-toast';
      toast.textContent = msg;
      document.body.appendChild(toast);

      requestAnimationFrame(() => toast.classList.add('show'));
      setTimeout(() => {
        toast.classList.remove('show');
        toast.addEventListener('transitionend', () => toast.remove());
      }, ms);
    }

    // Expose globally for manual debugging if needed
    window.showToast = showToast;

    // ------------------------------------------------------------------
    // 3. Front-end validation for the report generation form
    // ------------------------------------------------------------------

    document.addEventListener('submit', evt => {
      console.debug('[lux] submit intercept');
      let form = evt.target;
      if (!(form instanceof HTMLFormElement)) return;

      // Workaround for nested forms: sometimes the outer wrapper (results-and-report-form)
      // receives the submit event while the *inner* actual report form carries hx-post.
      // Determine if this submission is ultimately destined for the incremental report endpoint.
      const isReportForm =
        form.getAttribute('hx-post') === '/generate-report-incremental' ||
        form.querySelector('[hx-post="/generate-report-incremental"]');

      if (!isReportForm) return; // ignore unrelated forms

      // Reset per-page chat transcript cache so chats start fresh for this report.
      if (window.__luxChatCache && typeof window.__luxChatCache.clear === 'function') {
        console.debug('[lux] settings.js: clearing chat cache for new report');
        window.__luxChatCache.clear();
      }

      // Ensure hidden inputs reflect latest toolbar state
      if (typeof window.syncHidden === 'function') window.syncHidden();

      // ------------------------------------------------------------------
      // Clone selected paper inputs into the form so FastAPI sees them.
      // ------------------------------------------------------------------

      // Clean previous clones
      form.querySelectorAll('input[data-lux-paper-clone="1"]').forEach(el => el.remove());

      const paperSources = document.querySelectorAll(
        '[name="selected_papers_json"]:checked, li input[name="selected_papers_json"][type="hidden"]'
      );

      paperSources.forEach(src => {
        const clone = document.createElement('input');
        clone.type = 'hidden';
        clone.name = 'selected_papers_json';
        clone.value = src.value;
        clone.dataset.luxPaperClone = '1';
        form.appendChild(clone);
      });

      // ------------------------------------------------------------------
      // Client-side validation – block submit & toast missing parts.
      // ------------------------------------------------------------------

      const audVal = audSel?.value || '';
      const modelVal = modelSel?.value || '';

      const isPlaceholder = v => !v || /^\s*$/.test(v) || v.startsWith('Select ');

      const sizeVal = form.querySelector('#summary_size_select')?.value || '';

      const papersCount = paperSources.length;

      if (isPlaceholder(audVal) || isPlaceholder(modelVal) || isPlaceholder(sizeVal) || papersCount === 0) {
        evt.preventDefault();
        evt.stopPropagation();

        const missing = [];
        if (isPlaceholder(audVal)) missing.push('Audience');
        if (isPlaceholder(modelVal)) missing.push('Model');
        if (isPlaceholder(sizeVal)) missing.push('Report Size');
        if (papersCount === 0) missing.push('≥1 paper');

        console.debug('[lux] showToast:', `Please select ${missing.join(', ')}.`);
        showToast(`Please select: ${missing.join(', ')}.`);
        return;
      }
    }, true); // capture phase to run before HTMX grabs the event

    // Theme toggle – reuse existing or inject
    let themeBtn = document.getElementById('lux-dark-toggle');
    let clearBtn = document.getElementById('clear-prefs-btn');
    if (!themeBtn) {
      themeBtn = document.createElement('button');
      themeBtn.id = 'lux-dark-toggle';
      themeBtn.style.border = 'none';
      themeBtn.style.background = 'transparent';
      const parentFlex = audSel?.parentElement;
      if (parentFlex) parentFlex.appendChild(themeBtn);
      if (!clearBtn) {
        clearBtn = document.createElement('button');
        clearBtn.id = 'clear-prefs-btn';
        clearBtn.textContent = 'Clear';
        clearBtn.style.marginRight = '0.75rem';
        clearBtn.style.border = 'none';
        clearBtn.style.background = 'transparent';
        if (parentFlex) parentFlex.appendChild(clearBtn);
      }
    }

    const updateThemeBtnText = () => {
      const isDark = document.body.classList.contains('lux-dark');
      themeBtn.textContent = isDark ? '☀️ Light' : '🌙 Dark';
    };

    const applyTheme = theme => {
      document.body.classList.toggle('lux-dark', theme === 'dark');
      updateThemeBtnText();
    };

    const savedTheme = LS.getItem('theme');

    // Force default to light on first load even if legacy value persisted
    if (savedTheme === 'dark') {
      // Comment out next line if you want to retain dark across sessions
      LS.removeItem('theme');
    }

    applyTheme('light');

    // ------------------------------------------------------------------
    // Summary size persistence (S/M/L/XL)
    // ------------------------------------------------------------------

    const SIZE_KEY = "summary_size";

    function restoreSummarySize() {
      const saved = LS.getItem(SIZE_KEY);
      if (!saved) return;
      document.querySelectorAll('#summary_size_select').forEach(sel => {
        if (sel && sel.value !== saved) sel.value = saved;
      });
    }

    // On change, persist value
    document.body.addEventListener('change', evt => {
      const t = evt.target;
      if (t && t.id === 'summary_size_select') {
        LS.setItem(SIZE_KEY, t.value);
      }
    });

    // Initial restore (in case form already present)
    restoreSummarySize();

    // Re-sync after HTMX swaps
    document.addEventListener('htmx:afterSwap', evt => {
      if (typeof syncHidden === 'function') syncHidden();
      setPlaceholderState(document.getElementById('aud-select'));
      setPlaceholderState(document.getElementById('model-select'));

      // Re-apply saved report size after new form injected
      restoreSummarySize();

      // Convert any injected error blocks to toasts and clean them up.
      document.querySelectorAll('.alert-error, .error-message').forEach(el => {
        console.debug('[lux] converted server error to toast:', el.textContent.trim());
        showToast(el.textContent.trim());
        el.remove();
      });
    });

    // Global handler: show toast on HTMX 4xx/5xx errors (e.g., missing selections)
    document.body.addEventListener('htmx:responseError', evt => {
      const xhr = evt.detail.xhr;
      let msg = 'Request failed.';
      if (xhr && xhr.responseText) {
        // Strip HTML tags for toast readability
        msg = xhr.responseText.replace(/<[^>]+>/g, '').trim() || msg;
      }
      console.debug('[lux] showToast:', msg);
      showToast(msg);
    });

    themeBtn.addEventListener('click', () => {
      const newTheme = document.body.classList.contains('lux-dark') ? 'light' : 'dark';
      LS.setItem('theme', newTheme);
      applyTheme(newTheme);
    });

    if (clearBtn) {
      clearBtn.addEventListener('click', e => {
        e.preventDefault();
        // No localStorage persistence now; just reset selects.
        selects.forEach(el => {
          if (el) {
            el.value = '';
            setPlaceholderState(el);
          }
        });
        window.syncHidden();
        LS.clear();
        showToast('Preferences cleared');
      });
    }
  }

  // Ensure DOM is ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();

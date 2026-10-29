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
    hydrateTopAuthStatus();

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
    console.debug('[oh] settings.js initialized');

    // Do NOT restore past Audience/Model choices – always start at placeholder.

    // Sync hidden inputs once after restore
    window.syncHidden();

    selects.forEach(setPlaceholderState);

    // Initialise flatpickr date picker — allowInput lets users type manually
    if (window.flatpickr) {
      try {
        window.flatpickr('#start_published_date', {
          dateFormat: 'Y-m-d',
          allowInput: true,
        });
      } catch (e) {
        console.warn('[oh] flatpickr init failed', e);
      }
    }

    // Sources dropdown-checkbox toggle
    (function initSourcesDropdown() {
      const btn = document.getElementById('sources-dropdown-btn');
      const menu = document.getElementById('sources-dropdown-menu');
      if (!btn || !menu) return;

      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const open = menu.style.display !== 'none';
        menu.style.display = open ? 'none' : 'block';
      });

      // Close on outside click
      document.addEventListener('click', () => {
        menu.style.display = 'none';
      });
      menu.addEventListener('click', (e) => e.stopPropagation());

      // Update button label when checkboxes change
      function updateLabel() {
        const checked = menu.querySelectorAll('.source-checkbox:checked');
        if (checked.length === 0) {
          btn.textContent = 'All sources ▾';
        } else {
          const names = Array.from(checked).map(cb => {
            const span = cb.parentElement.querySelector('span');
            return span ? span.textContent.trim() : cb.value;
          });
          btn.textContent = names.join(', ') + ' ▾';
        }
      }
      menu.addEventListener('change', updateLabel);
      console.debug('[oh] sources dropdown initialised');
    })();

    // Persist on change
    selects.forEach(el => {
      if (!el) return;
      el.addEventListener('change', () => {
        document.dispatchEvent(
          new CustomEvent('oh:settings-changed', { detail: { id: el.id, value: el.value } }),
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
        console.debug('[oh] Domain selections cleared');
      });
    }

    // ------------------------------------------------------------------
    // Simple toast utility
    // ------------------------------------------------------------------

    // Inject toast CSS once
    (function injectToastCss() {
      if (document.getElementById('oh-toast-style')) return;
      const s = document.createElement('style');
      s.id = 'oh-toast-style';
      s.textContent = `
        .oh-toast{position:fixed;bottom:24px;left:50%;transform:translateX(-50%);padding:10px 16px;border-radius:6px;z-index:9999;font-size:.9rem;font-family:Arial,sans-serif;border:1px solid transparent;opacity:0;transition:opacity .25s ease;}
        .oh-toast.show{opacity:1;}
        html:not(.oh-dark) .oh-toast{background:#ffffff;color:#000;border-color:#000}
        html.oh-dark .oh-toast{background:#333;color:#ef4444;border-color:#666}
      `;
      document.head.appendChild(s);
    })();

    function showToast(msg, ms = 3500) {
      const existing = document.querySelector('.oh-toast');
      if (existing) existing.remove();

      const toast = document.createElement('div');
      toast.className = 'oh-toast';
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
      console.debug('[oh] submit intercept');
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
      if (window.__ohChatCache && typeof window.__ohChatCache.clear === 'function') {
        console.debug('[oh] settings.js: clearing chat cache for new report');
        window.__ohChatCache.clear();
      }

      // Ensure hidden inputs reflect latest toolbar state
      if (typeof window.syncHidden === 'function') window.syncHidden();

      // ------------------------------------------------------------------
      // Clone selected paper inputs into the form so FastAPI sees them.
      // ------------------------------------------------------------------

      // Clean previous clones
      form.querySelectorAll('input[data-oh-paper-clone="1"]').forEach(el => el.remove());

      const paperSources = document.querySelectorAll(
        '[name="selected_papers_json"]:checked, li input[name="selected_papers_json"][type="hidden"]'
      );

      paperSources.forEach(src => {
        const clone = document.createElement('input');
        clone.type = 'hidden';
        clone.name = 'selected_papers_json';
        clone.value = src.value;
        clone.dataset.ohPaperClone = '1';
        form.appendChild(clone);
      });

      // Ensure exactly ONE is_url_input flag travels with the submit
      const urlFlagInput = document.getElementById('is_url_input_source_hidden');
      if (urlFlagInput) {
        // Remove any prior duplicates
        form.querySelectorAll('input[name="is_url_input"]').forEach(el => el.remove());

        const cloned = document.createElement('input');
        cloned.type = 'hidden';
        cloned.name = 'is_url_input';
        cloned.value = urlFlagInput.value;
        cloned.dataset.ohCopied = '1';
        form.appendChild(cloned);
      }

      // Same carry-forward for the "Search as a topic" override
      const topicFlagInput = document.getElementById('forced_topic_source_hidden');
      if (topicFlagInput) {
        form.querySelectorAll('input[name="forced_topic"]').forEach(el => el.remove());

        const clonedTopic = document.createElement('input');
        clonedTopic.type = 'hidden';
        clonedTopic.name = 'forced_topic';
        clonedTopic.value = topicFlagInput.value;
        clonedTopic.dataset.ohCopied = '1';
        form.appendChild(clonedTopic);
      }

      // ------------------------------------------------------------------
      // Client-side validation – block submit & toast missing parts.
      // ------------------------------------------------------------------

      const audVal = audSel?.value || '';
      const modelVal = modelSel?.value || '';

      const isPlaceholder = v => !v || /^\s*$/.test(v) || v.startsWith('Select ');

      // Report length now lives in the (sticky) top bar — outside the
      // form — so query document, not the form. The submit's hx_include
      // posts its value via the hidden #size-select selector.
      const sizeVal = document.querySelector('#size-select')?.value || '';

      const papersCount = paperSources.length;

      if (isPlaceholder(audVal) || isPlaceholder(modelVal) || isPlaceholder(sizeVal) || papersCount === 0) {
        evt.preventDefault();
        evt.stopPropagation();

        const missing = [];
        if (isPlaceholder(audVal)) missing.push('Audience');
        if (isPlaceholder(modelVal)) missing.push('Model');
        if (isPlaceholder(sizeVal)) missing.push('Report Size');
        if (papersCount === 0) missing.push('≥1 paper');

        console.debug('[oh] showToast:', `Please select ${missing.join(', ')}.`);
        showToast(`Please select: ${missing.join(', ')}.`);
        return;
      }
    }, true); // capture phase to run before HTMX grabs the event

    // Global handler: show toast on HTMX 4xx/5xx errors (e.g., missing selections)
    document.body.addEventListener('htmx:responseError', evt => {
      const xhr = evt.detail.xhr;
      let msg = 'Request failed.';
      if (xhr && xhr.responseText) {
        // Strip HTML tags for toast readability
        msg = xhr.responseText.replace(/<[^>]+>/g, '').trim() || msg;
      }
      console.debug('[oh] showToast:', msg);
      showToast(msg);
    });

    // Theme toggle – reuse existing or inject
    let themeBtn = document.getElementById('oh-dark-toggle');
    let clearBtn = document.getElementById('clear-prefs-btn');
    if (!themeBtn) {
      themeBtn = document.createElement('button');
      themeBtn.id = 'oh-dark-toggle';
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
      const isDark = document.documentElement.classList.contains("oh-dark");
      themeBtn.innerHTML = isDark
        ? '<i class="fa-solid fa-sun"></i>'
        : '<i class="fa-solid fa-moon"></i>';
    };

    const applyTheme = theme => {
      document.documentElement.classList.toggle("oh-dark", theme === 'dark');
      updateThemeBtnText();
    };

    const savedTheme = LS.getItem('theme');
    // Restore the user's saved theme — the inline <head> script already
    // applied it pre-paint; this syncs the button icon + class.
    applyTheme(savedTheme === 'dark' ? 'dark' : 'light');

    // ------------------------------------------------------------------
    // Summary size persistence (S/M/L)
    // ------------------------------------------------------------------

    const SIZE_KEY = "summary_size";

    function restoreSummarySize() {
      let saved = LS.getItem(SIZE_KEY);
      if (saved === 'XL') {
        // Backwards-compat with older builds that offered an XL option
        saved = 'L';
        LS.setItem(SIZE_KEY, saved);
      }
      if (!saved) return;
      document.querySelectorAll('#summary_size_select, #size-select').forEach(sel => {
        if (sel && sel.value !== saved) sel.value = saved;
      });
    }

    // Persist + keep the toolbar select and report-form selects in sync
    function syncSize(value, source) {
      LS.setItem(SIZE_KEY, value);
      document.querySelectorAll('#summary_size_select, #size-select').forEach(sel => {
        if (sel && sel !== source && sel.value !== value) sel.value = value;
      });
    }
    document.body.addEventListener('change', evt => {
      const t = evt.target;
      if (t && (t.id === 'summary_size_select' || t.id === 'size-select') && t.value) {
        syncSize(t.value, t);
      }
    });

    // Initial restore (in case form already present)
    restoreSummarySize();
    { const saved = LS.getItem(SIZE_KEY); if (saved) syncSize(saved); }

    // Re-sync after HTMX swaps
    document.addEventListener('htmx:afterSwap', evt => {
      if (typeof syncHidden === 'function') syncHidden();
      setPlaceholderState(document.getElementById('aud-select'));
      setPlaceholderState(document.getElementById('model-select'));

      // Re-apply saved report size after new form injected
      restoreSummarySize();

      // Retain the card border after dynamic content loads – previously removed for full-width expansion.
      // If you need to expand content in the future, consider toggling specific width classes instead.

      // Convert any injected error blocks to toasts and clean them up.
      document.querySelectorAll('.alert-error, .error-message').forEach(el => {
        console.debug('[oh] converted server error to toast:', el.textContent.trim());
        showToast(el.textContent.trim());
        el.remove();
      });
    });

    themeBtn.addEventListener('click', () => {
      const newTheme = document.documentElement.classList.contains("oh-dark") ? 'light' : 'dark';
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

    /* =====================================================================
     * Page snapshot – persist minimal state across normal reloads
     * ===================================================================== */

    const SNAP_KEY = 'ohSnapshot';

    function saveSnapshot(ev) {
      try {
        // If user initiated a hard reload (Shift key), skip saving snapshot.
        if (ev && ev.shiftKey) {
          sessionStorage.removeItem(SNAP_KEY);
          return;
        }
      } catch (_) {
        /* ignore */
      }

      try {
        const resultsEl = document.getElementById('search-results-container');
        if (!resultsEl || !resultsEl.children.length) {
          sessionStorage.removeItem(SNAP_KEY);
          return; // nothing to save
        }

        const snapshot = {
          html: resultsEl.innerHTML,
          scrollY: window.scrollY,
          selectedIds: Array.from(document.querySelectorAll('.paper-select-checkbox:checked'))
            .map(cb => cb.closest('li')?.dataset.paperId)
            .filter(Boolean),
          timestamp: Date.now(), // for future expiry if needed
        };
        sessionStorage.setItem(SNAP_KEY, JSON.stringify(snapshot));
        console.debug('[oh] snapshot saved');
      } catch (e) {
        console.warn('[oh] snapshot save failed', e);
      }
    }

    const HARD_FLAG = 'ohHardReload';

    function restoreSnapshot() {
      try {
        // Respect hard reload flag set by main.js – skip restoration entirely.
        const hardReload = sessionStorage.getItem(HARD_FLAG) === '1';
        if (hardReload) {
          sessionStorage.removeItem(HARD_FLAG);
          sessionStorage.removeItem(SNAP_KEY);
          return false;
        }

        const raw = sessionStorage.getItem(SNAP_KEY);
        if (!raw) return false;
        const snap = JSON.parse(raw);

        // Only restore when we are still on a results page – i.e. the query field
        // contains text or search params include `search_query=`. Prevents showing
        // stale papers on a fresh homepage load/hard refresh.
        const queryPresent = location.search.includes('search_query=') ||
          (document.getElementById('search_query')?.value.trim() ?? '').length > 0;
        if (!queryPresent) {
          sessionStorage.removeItem(SNAP_KEY);
          return false;
        }

        const resultsEl = document.getElementById('search-results-container');
        if (!resultsEl) return false;

        resultsEl.innerHTML = snap.html;

        // Re-apply checkbox selections
        snap.selectedIds?.forEach(id => {
          const li = document.querySelector(`li[data-paper-id="${id}"]`);
          const cb = li?.querySelector('.paper-select-checkbox');
          if (cb) cb.checked = true;
        });

        // Restore scroll
        requestAnimationFrame(() => window.scrollTo(0, snap.scrollY || 0));

        console.debug('[oh] snapshot restored');
        return true;
      } catch (e) {
        console.warn('[oh] snapshot restore failed', e);
        return false;
      }
    }

    // Snapshot disabled – we always start fresh on reload.
    sessionStorage.removeItem(SNAP_KEY);

    // Clear snapshot on brand-new search
    document.body.addEventListener('htmx:beforeRequest', e => {
      const path = e.detail?.requestConfig?.path || '';
      if (path.startsWith('/search')) {
        sessionStorage.removeItem(SNAP_KEY);

        // Clear previous search results immediately so the user sees a blank slate
        const container = document.getElementById('search-results-container');
        if (container) container.innerHTML = '';
      }
    });

    // ------------------------------------------------------------------
    // Cancel in-flight search — remember the element that issued the
    // current request; the indicator's Cancel button aborts it.
    // ------------------------------------------------------------------
    let ohActiveTrigger = null;

    // Staged progress text — searches run several seconds; rotating phases
    // make it feel alive instead of a dead spinner. Illustrative, not real
    // pipeline telemetry; it just cycles and parks on the last stage.
    const OH_SEARCH_STAGES = [
      'Searching the PaperVerse…',
      'Fetching papers…',
      'Enriching citations…',
      'Ranking results…',
    ];
    let ohStageTimer = null;
    function ohStopStages() {
      if (ohStageTimer) { clearInterval(ohStageTimer); ohStageTimer = null; }
    }
    function ohStartStages() {
      ohStopStages();
      const el = document.getElementById('oh-loading-text');
      if (!el) return;
      let i = 0;
      el.textContent = OH_SEARCH_STAGES[0];
      ohStageTimer = setInterval(() => {
        if (i < OH_SEARCH_STAGES.length - 1) {
          i += 1;
          const cur = document.getElementById('oh-loading-text');
          if (cur) cur.textContent = OH_SEARCH_STAGES[i];
        }
      }, 2500);
    }

    document.body.addEventListener('htmx:beforeRequest', e => {
      ohActiveTrigger = e.detail?.elt || null;
      const ind = e.detail?.elt?.getAttribute?.('hx-indicator')
        || e.detail?.elt?.closest?.('[hx-indicator]')?.getAttribute?.('hx-indicator');
      if (ind === '#loading-indicator') ohStartStages();
    });
    document.body.addEventListener('htmx:afterRequest', () => {
      ohActiveTrigger = null;
      ohStopStages();
    });
    document.addEventListener('click', e => {
      const btn = e.target.closest('#oh-cancel-search');
      if (!btn) return;
      e.preventDefault();
      const trig = ohActiveTrigger;
      ohActiveTrigger = null;
      ohStopStages();
      if (trig && window.htmx) htmx.trigger(trig, 'htmx:abort');
      const container = document.getElementById('search-results-container');
      if (container) {
        container.innerHTML =
          '<div class="empty-state-card" style="padding:0.8rem 1rem;">' +
          'Search cancelled.</div>';
      }
    });

    // ------------------------------------------------------------------
    // Account + Notion panels on /settings — hydrated on page load.
    // (Replaces the old gear-icon modal; /settings is the single surface.)
    // ------------------------------------------------------------------
    function renderAccountPanel(accountEl) {
      if (!accountEl) return;
      fetch('/api/me')
            .then(r => r.json())
            .then(data => {
              if (data.authenticated) {
                const name = data.user.name || data.user.email;
                accountEl.innerHTML =
                  '<div><strong>' + escapeHtml(name) + '</strong></div>' +
                  '<div style="margin-top:6px;display:flex;gap:8px;flex-wrap:wrap;">' +
                  '<a href="/reports">My Reports</a>' +
                  '<button type="button" id="logout-btn" class="btn-secondary">Log out</button>' +
                  '</div>';
                document.getElementById('logout-btn')?.addEventListener('click', () => {
                  fetch('/auth/logout', { method: 'POST' }).then(() => { window.location.href = '/'; });
                });
              } else {
                accountEl.innerHTML = '<a class="btn-secondary" href="/auth/google/login">Sign in with Google</a>';
              }
            })
            .catch(() => {
              accountEl.innerHTML = '<span style="color:#eab308;">Could not check account status</span>';
            });
    }

    function renderNotionPanel(statusEl) {
      if (!statusEl) return;
      statusEl.innerHTML = '<span class="oh-spinner small" style="display:inline-block;vertical-align:middle;"></span> Checking…';
          fetch('/api/notion-status')
            .then(r => r.json())
            .then(data => {
              if (data.connected) {
                statusEl.innerHTML =
                  '<span style="color:#22c55e;font-weight:600;">● Connected</span>' +
                  '<div style="margin-top:6px;font-size:0.85rem;color:var(--fg-muted,#888);">' +
                  (data.workspace_name ? 'Workspace: ' + escapeHtml(data.workspace_name) + '<br>' : '') +
                  'Syncs into: ' + (data.target_type === 'database' ? 'a database' : data.target_type === 'page' ? 'a page' : 'nothing yet — pick a location below') +
                  (data.database_id ? ' <code>' + escapeHtml(data.database_id) + '</code>' : '') +
                  '</div>' +
                  (data.oauth_connected
                    ? '<div style="margin-top:8px;font-size:0.85rem;color:var(--fg-muted,#888);">' +
                      'Papers, topics and reading status sync automatically; comments on synced pages get answered on sync.' +
                      '</div>' +
                      '<details style="margin-top:8px;">' +
                      '<summary style="cursor:pointer;font-size:0.8rem;color:var(--fg-muted,#888);">Manage</summary>' +
                      '<div style="margin-top:8px;display:flex;gap:8px;flex-wrap:wrap;">' +
                      '<button type="button" id="notion-refresh-destinations" class="btn-secondary">' +
                      (data.target_type === 'none' ? 'Pick where to sync…' : 'Change sync location…') +
                      '</button>' +
                      '<button type="button" id="notion-disconnect" class="btn-secondary">Disconnect</button>' +
                      '</div><div id="notion-destinations" style="margin-top:8px;"></div>' +
                      '</details>'
                    : '<div style="margin-top:8px;font-size:0.85rem;color:var(--fg-muted,#888);">Using server env Notion integration.</div>');
                if (data.oauth_connected) wireNotionControls(statusEl);
              } else if (data.authenticated === false) {
                // Not signed in: keep the connect action inside the modal —
                // a Google sign-in that returns here, not a bare 401 page.
                statusEl.innerHTML =
                  '<div style="padding:10px 12px;border:1px solid var(--container-border,#888);border-radius:6px;background:var(--container-bg,#f8f8f8);">' +
                  '<div style="font-weight:600;margin-bottom:4px;">Sign in to connect Notion</div>' +
                  '<div style="font-size:0.85rem;color:var(--fg-muted,#888);margin-bottom:8px;">' +
                  'Syncing your library to your own Notion workspace needs an account.' +
                  '</div>' +
                  '<a class="btn-secondary" href="/auth/google/login?next=' +
                  encodeURIComponent(window.location.pathname) +
                  '">Sign in with Google</a>' +
                  '</div>';
              } else {
                let detail = data.oauth_connected === false
                  ? '<div>Connect your own Notion workspace — papers, topics and reading status sync automatically.</div>'
                  : '';
                if (!data.has_api_key) detail += '<div>✗ No Notion connection configured</div>';
                if (!data.has_parent && data.has_api_key) detail += '<div>✗ No database or page selected</div>';
                statusEl.innerHTML =
                  '<span style="color:#ef4444;font-weight:600;">● Not configured</span>' +
                  '<div style="margin-top:6px;font-size:0.85rem;color:var(--fg-muted,#888);">' +
                  detail +
                  '</div>' +
                  '<div style="margin-top:8px;"><a class="btn-secondary" href="/integrations/notion/connect">Connect Notion</a></div>';
              }
            })
            .catch(() => {
              statusEl.innerHTML = '<span style="color:#eab308;">● Could not check status</span>';
            });
    }

    function refreshNotionPanel() {
      renderNotionPanel(document.getElementById('settings-notion'));
    }

    renderAccountPanel(document.getElementById('settings-account'));
    renderNotionPanel(document.getElementById('settings-notion'));

    function hydrateTopAuthStatus() {
      const authEl = document.getElementById('oh-auth-status');
      if (!authEl) return;
      fetch('/api/me')
        .then(r => r.json())
        .then(data => {
          if (data.authenticated) {
            const name = data.user.name || data.user.email || 'Account';
            authEl.innerHTML =
              '<span class="top-auth-user" title="' + escapeHtml(name) + '">' + escapeHtml(name) + '</span>';
          } else {
            authEl.innerHTML = '<a href="/auth/google/login" title="Sign in">Sign in</a>';
          }
        })
        .catch(() => {
          authEl.innerHTML = '<a href="/auth/google/login" title="Sign in">Sign in</a>';
        });
    }

    function escapeHtml(value) {
      return String(value || '').replace(/[&<>"']/g, ch => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;',
      }[ch]));
    }

    function wireNotionControls(statusEl) {
      const disconnect = statusEl.querySelector('#notion-disconnect');
      if (disconnect) {
        disconnect.addEventListener('click', () => {
          fetch('/integrations/notion/disconnect', { method: 'POST' }).then(() => {
            if (typeof window.showToast === 'function') window.showToast('Notion disconnected');
            refreshNotionPanel();
          });
        });
      }
      const choose = statusEl.querySelector('#notion-refresh-destinations');
      if (!choose) return;
      choose.addEventListener('click', () => {
        const destEl = statusEl.querySelector('#notion-destinations');
        if (!destEl) return;
        destEl.innerHTML = '<span class="oh-spinner small"></span> Loading your Notion pages…';
        fetch('/api/notion/destinations')
          .then(r => r.json())
          .then(data => {
            if (!data.ok) {
              destEl.textContent = data.detail || 'Could not load destinations.';
              return;
            }
            destEl.innerHTML =
              '<div style="font-size:0.8rem;color:var(--fg-muted,#888);margin-bottom:6px;">' +
              'Office Hours creates two databases, <em>Office Hours Papers</em> and ' +
              '<em>Office Hours Topics</em>, under whichever page or database you pick:' +
              '</div>' +
              data.destinations.map(dest =>
                '<button type="button" class="notion-destination" data-id="' + escapeHtml(dest.id) +
                '" data-type="' + escapeHtml(dest.type) + '" ' +
                'style="display:block;width:100%;text-align:left;margin:3px 0;padding:5px 8px;border:1px solid var(--container-border,#aaa);border-radius:4px;background:var(--container-bg);color:var(--fg);cursor:pointer;font-size:0.8rem;">' +
                (dest.type === 'database' ? '🗃 ' : '📄 ') + escapeHtml(dest.title) +
                '</button>'
              ).join('');
            destEl.querySelectorAll('.notion-destination').forEach(btn => {
              btn.addEventListener('click', () => {
                const fd = new FormData();
                fd.append('destination_id', btn.dataset.id);
                fd.append('destination_type', btn.dataset.type);
                fetch('/api/notion/destination', { method: 'POST', body: fd })
                  .then(r => r.json())
                  .then(() => {
                    if (typeof window.showToast === 'function') window.showToast('Notion sync location saved');
                    refreshNotionPanel();
                  });
              });
            });
          })
          .catch(() => { destEl.textContent = 'Could not load Notion destinations.'; });
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

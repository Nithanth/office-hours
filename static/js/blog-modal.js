// blog-modal.js — shared customization modal for the blog feature.
//
// Used by:
//   * static/js/report.js   – initial blog generation from the report page
//   * static/js/blog-view.js – "Revise" button on the rendered blog page
//
// Exposes `window.luxBlog.openCustomizeModal({ mode, current, onSubmit })`.
// The modal is lazily injected into <body> the first time it's opened.
//   mode='initial'    – first-time generation. 500 char free-text cap, optional.
//   mode='regenerate' – revise existing blog. 1000 char free-text cap, required.

(function () {
  const BLOG_CUSTOMIZE_DEFAULTS = {
    audience: "technical",
    length: "medium",
    tone: "distill",
    extra: "",
  };

  function ensureBlogCustomizeModal() {
    let modal = document.getElementById("blog-customize-modal");
    if (modal) return modal;
    modal = document.createElement("div");
    modal.id = "blog-customize-modal";
    modal.className = "modal hidden";
    modal.setAttribute("aria-hidden", "true");
    modal.innerHTML =
      '<div class="modal-content lux-blog-modal" role="dialog" aria-modal="true" aria-labelledby="blog-modal-title">' +
        '<div class="modal-header">' +
          '<h2 id="blog-modal-title" class="text-xl font-bold">Customize your blog</h2>' +
          '<button type="button" class="modal-close-btn" aria-label="Close">&times;</button>' +
        '</div>' +
        '<form id="blog-customize-form" class="space-y-4">' +
          '<p id="blog-modal-subtitle" class="text-sm opacity-80"></p>' +
          '<div class="lux-blog-modal-grid">' +
            '<fieldset class="lux-blog-modal-fs">' +
              '<legend>Audience</legend>' +
              '<label><input type="radio" name="audience" value="technical" checked> Technical</label>' +
              '<label><input type="radio" name="audience" value="mixed"> Mixed</label>' +
              '<label><input type="radio" name="audience" value="layperson"> Layperson</label>' +
            '</fieldset>' +
            '<fieldset class="lux-blog-modal-fs">' +
              '<legend>Length</legend>' +
              '<label><input type="radio" name="length" value="short"> Short (~600 w)</label>' +
              '<label><input type="radio" name="length" value="medium" checked> Medium (~1500 w)</label>' +
              '<label><input type="radio" name="length" value="long"> Long (~3000 w)</label>' +
            '</fieldset>' +
            '<fieldset class="lux-blog-modal-fs">' +
              '<legend>Tone</legend>' +
              '<label><input type="radio" name="tone" value="distill" checked> Distill.pub</label>' +
              '<label><input type="radio" name="tone" value="casual"> Casual blog</label>' +
              '<label><input type="radio" name="tone" value="lecture"> Lecture notes</label>' +
            '</fieldset>' +
          '</div>' +
          '<label class="block">' +
            '<span class="font-medium block mb-1" id="blog-extra-label">Additional instructions (optional)</span>' +
            '<textarea name="extra" rows="3" maxlength="1000" ' +
              'class="lux-blog-modal-textarea" ' +
              'placeholder="e.g. emphasise reproducibility / keep it under 1000 words / no jargon"></textarea>' +
            '<small class="opacity-60 block mt-1"><span data-blog-extra-count>0</span>/<span data-blog-extra-max>500</span> chars</small>' +
          '</label>' +
          '<footer class="lux-blog-modal-footer">' +
            '<button type="button" class="btn-secondary" data-action="cancel">Cancel</button>' +
            '<button type="submit" class="btn-primary" data-action="submit">Generate blog</button>' +
          '</footer>' +
        '</form>' +
      '</div>';
    document.body.appendChild(modal);

    const ta = modal.querySelector('textarea[name="extra"]');
    const counter = modal.querySelector("[data-blog-extra-count]");
    if (ta && counter) {
      ta.addEventListener("input", () => { counter.textContent = String(ta.value.length); });
    }

    return modal;
  }

  function openBlogCustomizeModal({ mode = "initial", current = null, onSubmit } = {}) {
    const modal = ensureBlogCustomizeModal();
    const titleEl = modal.querySelector("#blog-modal-title");
    const subtitleEl = modal.querySelector("#blog-modal-subtitle");
    const extraLabelEl = modal.querySelector("#blog-extra-label");
    const submitBtn = modal.querySelector('[data-action="submit"]');
    const extraMaxEl = modal.querySelector("[data-blog-extra-max]");
    const extraCountEl = modal.querySelector("[data-blog-extra-count]");
    const ta = modal.querySelector('textarea[name="extra"]');
    const form = modal.querySelector("form");

    const isRegen = mode === "regenerate";
    if (isRegen) {
      titleEl.textContent = "Revise your blog";
      subtitleEl.textContent =
        "Tweak any setting and / or describe what you'd like changed. We'll feed your previous draft to the model and ask it to revise.";
      extraLabelEl.textContent = "What would you like to change? (required for a meaningful revision)";
      submitBtn.textContent = "Regenerate blog";
      ta.maxLength = 1000;
      extraMaxEl.textContent = "1000";
      ta.placeholder = "e.g. shorten it, drop the section on X, add a real-world analogy, more diagrams";
    } else {
      titleEl.textContent = "Customize your blog";
      subtitleEl.textContent =
        "Pick how the model should write this blog. Defaults work well — change them only if you want to.";
      extraLabelEl.textContent = "Additional instructions (optional)";
      submitBtn.textContent = "Generate blog";
      ta.maxLength = 500;
      extraMaxEl.textContent = "500";
      ta.placeholder = "e.g. emphasise reproducibility / keep it under 1000 words / no jargon";
    }

    const init = Object.assign({}, BLOG_CUSTOMIZE_DEFAULTS, current || {});
    for (const k of ["audience", "length", "tone"]) {
      const radio = modal.querySelector(`input[name="${k}"][value="${CSS.escape(init[k])}"]`);
      if (radio) radio.checked = true;
    }
    ta.value = init.extra || "";
    if (extraCountEl) extraCountEl.textContent = String(ta.value.length);

    modal.classList.remove("hidden");
    modal.setAttribute("aria-hidden", "false");
    document.body.classList.add("lux-modal-open");

    const previousFocus = document.activeElement;
    const firstInput = modal.querySelector('input[type="radio"]:checked, input[type="radio"], textarea');
    if (firstInput) firstInput.focus({ preventScroll: true });

    function close() {
      modal.classList.add("hidden");
      modal.setAttribute("aria-hidden", "true");
      document.body.classList.remove("lux-modal-open");
      modal.removeEventListener("click", onBackdrop);
      document.removeEventListener("keydown", onKey);
      form.removeEventListener("submit", onSubmitInner);
      if (previousFocus && previousFocus.focus) previousFocus.focus({ preventScroll: true });
    }
    function onBackdrop(ev) {
      if (ev.target === modal) close();
      if (ev.target.matches && ev.target.matches(".modal-close-btn, [data-action=cancel]")) close();
    }
    function onKey(ev) {
      if (ev.key === "Escape") close();
    }
    function onSubmitInner(ev) {
      ev.preventDefault();
      const fd = new FormData(form);
      const cap = isRegen ? 1000 : 500;
      const opts = {
        audience: String(fd.get("audience") || "").trim() || BLOG_CUSTOMIZE_DEFAULTS.audience,
        length:   String(fd.get("length")   || "").trim() || BLOG_CUSTOMIZE_DEFAULTS.length,
        tone:     String(fd.get("tone")     || "").trim() || BLOG_CUSTOMIZE_DEFAULTS.tone,
        extra:    String(fd.get("extra")    || "").trim().slice(0, cap),
      };
      if (isRegen && !opts.extra) {
        // eslint-disable-next-line no-alert
        alert("Please tell us what you'd like changed.");
        const t = form.querySelector('textarea[name="extra"]');
        if (t) t.focus();
        return;
      }
      close();
      try {
        if (typeof onSubmit === "function") onSubmit(opts);
      } catch (e) {
        console.error("[lux] blog modal submit handler threw", e);
      }
    }
    modal.addEventListener("click", onBackdrop);
    document.addEventListener("keydown", onKey);
    form.addEventListener("submit", onSubmitInner);
  }

  window.luxBlog = window.luxBlog || {};
  window.luxBlog.openCustomizeModal = openBlogCustomizeModal;
  window.luxBlog.DEFAULTS = BLOG_CUSTOMIZE_DEFAULTS;
})();

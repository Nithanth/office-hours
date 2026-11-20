/* Client-rendered controls use the same class contract as pages/controls.py.
 * No hydration or DOM rewriting: callers keep their native events and hooks. */
(() => {
  "use strict";
  const escapeAttr = (value) =>
    String(value).replace(
      /[&"<>']/g,
      (char) =>
        ({
          "&": "&amp;",
          '"': "&quot;",
          "<": "&lt;",
          ">": "&gt;",
          "'": "&#x27;",
        })[char],
    );
  function buttonClasses({
    className = "",
    variant = "secondary",
    size = "md",
    iconLabel = "",
  } = {}) {
    return [
      "oh-button",
      `oh-button--${variant}`,
      `oh-control--${size}`,
      iconLabel ? "oh-button--icon" : "",
      className,
    ]
      .filter(Boolean)
      .join(" ");
  }
  function fieldClasses({ className = "", kind = "text", size = "md" } = {}) {
    if (kind === "hidden") return className;
    return [
      ["checkbox", "radio"].includes(kind) ? "oh-choice" : "oh-field",
      `oh-control--${size}`,
      className,
    ]
      .filter(Boolean)
      .join(" ");
  }
  function buttonAttrs(options = {}) {
    return (
      `class="${escapeAttr(buttonClasses(options))}"` +
      (options.iconLabel
        ? ` aria-label="${escapeAttr(options.iconLabel)}"`
        : "")
    );
  }
  function fieldAttrs(options = {}) {
    return `class="${escapeAttr(fieldClasses(options))}"`;
  }
  function button(text, options = {}) {
    const element = document.createElement("button");
    element.type = "button";
    element.className = buttonClasses(options);
    element.textContent = text;
    if (options.iconLabel) {
      element.setAttribute("aria-label", options.iconLabel);
      element.title = options.iconLabel;
    }
    return element;
  }
  window.OHControls = Object.freeze({
    button,
    buttonClasses,
    buttonAttrs,
    fieldClasses,
    fieldAttrs,
  });
})();

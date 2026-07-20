// Law Card disclosure toggles (LC-2b) — Rule 4: paced disclosure via real
// <button aria-expanded> elements, not <div onclick>. Vanilla JS, no build
// step (matches the rest of this repo's dashboard scripting).
(function () {
  function toggle(btn) {
    var targetId = btn.getAttribute("aria-controls");
    var target = targetId ? document.getElementById(targetId) : null;
    if (!target) return;
    var expanded = btn.getAttribute("aria-expanded") === "true";
    btn.setAttribute("aria-expanded", String(!expanded));
    if (expanded) {
      target.hidden = true;
    } else {
      target.hidden = false;
    }
  }

  document.addEventListener("click", function (event) {
    var btn = event.target.closest("[data-lc-toggle]");
    if (!btn) return;
    toggle(btn);
  });

  // Keyboard-complete per Rule 4: real buttons already get Enter/Space from
  // the browser, so no extra key handling is needed here — this comment
  // exists so a future edit doesn't reach for a <div> and think it needs one.

  // LC-3b: unsaved-edit navigation guard. A field row swapped into edit
  // mode carries class "lc-field-row-editing" (see lc_field_editor.html);
  // if any are still open when the tab is about to close/navigate away,
  // warn rather than silently discard an in-progress correction. htmx
  // swaps replace the row wholesale on Save/Cancel, so this check is
  // always reading live DOM state, not a stale snapshot.
  window.addEventListener("beforeunload", function (event) {
    if (document.querySelector(".lc-field-row-editing")) {
      event.preventDefault();
      event.returnValue = "";
    }
  });
})();

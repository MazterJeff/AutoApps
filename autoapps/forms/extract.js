// Runs inside the application page (via Playwright's page.evaluate) and describes every
// fillable control. Mirrors forms/html_form.py, but sees client-rendered forms too.
() => {
  const clean = (t) => (t || "").replace(/\s+/g, " ").replace(/✱/g, "").trim().replace(/^[\s*:]+|[\s*:]+$/g, "");
  const SKIP = new Set(["hidden", "submit", "button", "reset", "image", "search", "password"]);
  const CONTAINER_CLASSES = new Set(["form-group", "form-field", "field", "input-group", "form-item"]);
  // Text of a node without the text of controls inside it (e.g. <option>s of a nested <select>).
  const text = (node) => {
    const c = node.cloneNode(true);
    c.querySelectorAll("select, option, textarea, script, style").forEach((n) => n.remove());
    return clean(c.textContent);
  };

  const visible = (el) => {
    if (el.type === "file") return true; // upload inputs are usually hidden behind a styled button
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return s.display !== "none" && s.visibility !== "hidden" && (r.width > 0 || r.height > 0);
  };
  const isContainer = (p) =>
    p.tagName === "LI" || p.tagName === "FIELDSET" ||
    [...p.classList].some((c) => c.toLowerCase().includes("question") || CONTAINER_CLASSES.has(c.toLowerCase()));
  // The element wrapping one question; for radio/checkbox groups it must hold the whole group.
  const container = (el, group) => {
    for (let p = el.parentElement; p && p.tagName !== "FORM" && p.tagName !== "BODY"; p = p.parentElement) {
      if (!isContainer(p)) continue;
      if (group && p.querySelectorAll(`input[name="${CSS.escape(group)}"]`).length < 2) continue;
      return p;
    }
    return group ? container(el, "") : null;
  };
  const labelFor = (el, grouped) => {
    if (!grouped && el.id) {
      const lab = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (lab && text(lab)) return text(lab);
    }
    if (!grouped && el.getAttribute("aria-labelledby")) {
      const ids = el.getAttribute("aria-labelledby").split(/\s+/);
      const t = ids.map((i) => document.getElementById(i)?.innerText || "").join(" ");
      if (clean(t)) return clean(t);
    }
    const box = container(el, grouped ? el.name : "");
    if (box) {
      for (const sel of [".application-label", "legend", ".text", "label"]) {
        const n = box.querySelector(sel);
        if (n && text(n)) return text(n);
      }
    }
    if (!grouped) {
      const pl = el.closest("label");
      if (pl && text(pl)) return text(pl);
    }
    return clean(el.getAttribute("aria-label") || el.placeholder || el.name || el.id || "");
  };
  const optionLabel = (el) => {
    if (el.id) {
      const lab = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (lab && clean(lab.innerText)) return clean(lab.innerText);
    }
    const pl = el.closest("label");
    if (pl && clean(pl.innerText)) return clean(pl.innerText);
    return el.value || "";
  };
  const required = (el) => {
    const box = container(el, el.type === "radio" || el.type === "checkbox" ? el.name : "");
    const txt = box ? box.innerText.slice(0, 300) : "";
    return el.required || el.getAttribute("aria-required") === "true" || txt.includes("✱") || /\*\s*$/.test(labelFor(el, false));
  };
  const selectorFor = (el) => {
    if (el.id && document.querySelectorAll(`[id="${el.id}"]`).length === 1) return `[id="${el.id}"]`;
    if (el.name && document.querySelectorAll(`[name="${el.name}"]`).length === 1) return `[name="${el.name}"]`;
    return "";
  };

  const forms = [...document.querySelectorAll("form")];
  const root = forms.length
    ? forms.reduce((a, b) => (b.querySelectorAll("input,textarea,select").length > a.querySelectorAll("input,textarea,select").length ? b : a))
    : document.body;

  const fields = [];
  const groups = {};
  let anon = 0;
  for (const el of root.querySelectorAll("input, textarea, select")) {
    const tag = el.tagName.toLowerCase();
    const itype = tag === "input" ? (el.type || "text").toLowerCase() : tag;
    if (tag === "input" && SKIP.has(itype)) continue;
    if (!visible(el)) continue;
    const name = el.name || el.id || `field_${anon++}`;

    if (itype === "radio" || itype === "checkbox") {
      const opt = optionLabel(el);
      if (groups[name]) { groups[name].options.push(opt); continue; }
      const f = { key: name, label: labelFor(el, true) || opt, type: itype, options: [opt], required: required(el),
                  selector: el.name ? `[name="${el.name}"]` : selectorFor(el) };
      groups[name] = f;
      fields.push(f);
      continue;
    }
    let type, options = [];
    if (tag === "select") {
      type = el.multiple ? "multiselect" : "select";
      options = [...el.options].filter((o) => o.value !== "").map((o) => clean(o.text)).filter(Boolean);
    } else if (el.getAttribute("role") === "combobox") {
      type = "select"; // custom dropdown; options appear only when opened
    } else {
      type = { textarea: "textarea", email: "email", tel: "tel", url: "url", number: "number", date: "date", file: "file" }[itype] || "text";
    }
    fields.push({ key: name, label: labelFor(el, false), type, options, required: required(el), selector: selectorFor(el) });
  }
  for (const f of fields) if (f.type === "checkbox" && f.options.length === 1) f.options = [];
  return { fields, text: (document.body.innerText || "").slice(0, 20000) };
}

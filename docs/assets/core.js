/* core.js: the small toolkit every page builds on (no dependencies, plain script).
 *
 *   Docs.el(tag, props, ...children)     create an HTML element
 *   Docs.svg(tag, attrs, ...children)    create an SVG element
 *   Docs.reconcile(parent, items, opts)  keyed update: an item keeps its element across steps,
 *                                        so it can move / recolor / fade instead of reappearing
 *   Docs.Stepper                         ◀ ▶ player that walks a scene through its steps
 *   Docs.tween(ms, frame)                requestAnimationFrame animation with smooth easing
 */
(function (Docs) {
  "use strict";

  const SVG_NS = "http://www.w3.org/2000/svg";

  // page.html?still turns motion off too (handy for screenshots and printing)
  if (new URLSearchParams(location.search).has("still")) document.documentElement.classList.add("no-motion");
  Docs.reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches ||
    document.documentElement.classList.contains("no-motion");

  /* ---------------------------------------------------------- elements */

  /** props: class, style (object, kebab-case keys incl. --vars), html, on<event>, other attributes. */
  function applyProps(node, props) {
    for (const [k, v] of Object.entries(props || {})) {
      if (v == null || v === false) continue;
      if (k === "class") node.setAttribute("class", v);
      else if (k === "style" && typeof v === "object") {
        for (const [p, val] of Object.entries(v)) node.style.setProperty(p, val);
      } else if (k === "html") node.innerHTML = v;
      else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v === true ? "" : v);
    }
  }

  function append(node, children) {
    for (const c of children.flat(Infinity)) {
      if (c == null || c === false) continue;
      node.append(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return node;
  }

  Docs.el = (tag, props, ...children) => {
    const node = document.createElement(tag);
    applyProps(node, props);
    return append(node, children);
  };

  Docs.svg = (tag, attrs, ...children) => {
    const node = document.createElementNS(SVG_NS, tag);
    applyProps(node, attrs);
    return append(node, children);
  };

  Docs.escape = (s) =>
    String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

  /* ---------------------------------------------------------- keyed updates */

  /**
   * Make the keyed children of `parent` match `items`, in order.
   *   key(item)          identity: the same key keeps the same element (and so it animates)
   *   create(item)       build the element for a new key; it starts with class "enter" for a frame
   *   update(el, item)   refresh an element (called for new and existing ones)
   * Elements whose key is gone get class "leave" and are removed after `leaveMs`.
   * `order: false` leaves DOM order alone (SVG layers where position comes from coordinates).
   */
  Docs.reconcile = (parent, items, { key, create, update = () => {}, leaveMs = 420, order = true }) => {
    const live = parent.__keyed || (parent.__keyed = new Map());
    const keep = new Set();
    const born = [];
    let prev = null;
    for (const item of items) {
      const k = key(item);
      keep.add(k);
      let node = live.get(k);
      if (node) update(node, item);
      else {
        node = create(item);
        live.set(k, node);
        update(node, item); // before "enter": update may rewrite the class attribute
        node.classList.add("enter");
        born.push(node);
      }
      if (order) {
        const want = prev ? prev.nextSibling : parent.firstChild;
        if (node !== want) parent.insertBefore(node, want);
      } else if (!node.parentNode) parent.append(node);
      prev = node;
    }
    if (born.length) { // flush styles once so "enter" is the starting point of the transition
      parent.getBoundingClientRect();
      born.forEach((n) => n.classList.remove("enter"));
    }
    for (const [k, node] of live) {
      if (keep.has(k)) continue;
      live.delete(k);
      node.classList.add("leave");
      setTimeout(() => node.remove(), Docs.reducedMotion() ? 0 : leaveMs);
    }
  };

  /* ---------------------------------------------------------- motion */

  const smooth = (t) => t * t * (3 - 2 * t); // ease in and out, like manim's default

  /** Call frame(p) with p eased from 0 to 1 over `ms`; returns a cancel function. */
  Docs.tween = (ms, frame) => {
    if (ms <= 0 || Docs.reducedMotion()) {
      frame(1);
      return () => {};
    }
    let raf = 0;
    const t0 = performance.now();
    const tick = (now) => {
      const t = Math.min(1, (now - t0) / ms);
      frame(smooth(t));
      if (t < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  };

  Docs.lerp = (a, b, p) => a + (b - a) * p;

  /* ---------------------------------------------------------- stepper */

  /**
   * A scene that advances one step at a time (buttons, dots, ← → keys when focused).
   *
   *   new Docs.Stepper(sceneEl, steps, render)
   *     steps    [{ caption: "<html>", ...whatever render needs }]
   *     render   render(step, index, previousIndex): draw the whole scene for this step.
   *              Rendering is declarative (full state per step), so going back just works.
   * A link to page.html#<scene id>=<n> opens that scene at step n (1-based).
   */
  Docs.steppers = []; // every scene on the page (page.js ?selftest walks them all)

  Docs.Stepper = class Stepper {
    constructor(root, steps, render) {
      const { el } = Docs;
      Docs.steppers.push(this);
      this.root = root;
      this.steps = steps;
      this.render = render;
      this.index = -1;
      root.tabIndex = 0;
      root.setAttribute("aria-roledescription", "interactive explanation");
      this.prevBtn = el("button", { class: "prev", "aria-label": "Previous step", onclick: () => this.go(this.index - 1) }, "◀");
      this.nextBtn = el("button", { class: "next", "aria-label": "Next step", onclick: () => this.go(this.index + 1) }, "▶");
      this.caption = el("div", { class: "caption", "aria-live": "polite" });
      this.dots = el("div", { class: "dots" },
        steps.map((_, i) => el("button", { "aria-label": `Step ${i + 1}`, onclick: () => this.go(i) })));
      root.append(el("div", { class: "stepper" }, this.prevBtn, this.caption, this.nextBtn, this.dots));
      root.addEventListener("keydown", (e) => {
        if (e.target.closest("input, textarea, select")) return;
        const to = { ArrowRight: this.index + 1, ArrowLeft: this.index - 1, Home: 0, End: steps.length - 1 }[e.key];
        if (to === undefined) return;
        e.preventDefault();
        this.go(to);
      });
      const linked = new URLSearchParams(location.hash.slice(1)).get(root.id);
      this.go(linked ? Number(linked) - 1 || 0 : 0);
    }

    /** Redraw the current step (after a control outside the steps changed). */
    refresh() {
      this.render(this.steps[this.index], this.index, this.index);
    }

    go(i) {
      i = Math.max(0, Math.min(this.steps.length - 1, i));
      if (i === this.index) return;
      const prev = this.index;
      this.index = i;
      const step = this.steps[i];
      this.caption.innerHTML = step.caption || "";
      this.prevBtn.disabled = i === 0;
      this.nextBtn.disabled = i === this.steps.length - 1;
      [...this.dots.children].forEach((d, j) => d.classList.toggle("on", j === i));
      this.render(step, i, prev);
    }
  };

  /** A row of mutually exclusive buttons. onPick(value) fires on click and once initially. */
  Docs.segmented = (options, initial, onPick) => {
    const { el } = Docs;
    const root = el("div", { class: "segmented", role: "group" });
    const pick = (value) => {
      [...root.children].forEach((b) => b.classList.toggle("on", b.dataset.value === String(value)));
      onPick(value);
    };
    for (const o of options) {
      const [value, label] = Array.isArray(o) ? o : [o, o];
      root.append(el("button", { "data-value": String(value), onclick: () => pick(value) }, label));
    }
    queueMicrotask(() => pick(initial));
    return root;
  };
})((window.Docs = window.Docs || {}));

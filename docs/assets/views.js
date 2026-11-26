/* views.js: the visual vocabulary shared by all chapters.
 *
 *   Docs.palette              stable color per function name (same name = same hue on every page)
 *   Docs.highlight(source)    tiny Python tokenizer for syntax colors
 *   Docs.CodeView             source with line numbers, focus lines, marks, notes, regions
 *   Docs.tokenChip(tok)       one abstract value token as a chip (shape = token kind)
 *   Docs.chipSet(toks)        a set of tokens: { chip chip }
 *   Docs.EnvView              "name ↦ {tokens}" table of one node, keyed so chips animate
 *   Docs.GraphView            call graph with a layered layout; nodes glide between steps
 *   Docs.Tethers              curved connectors between any two elements of a scene
 *   Docs.example(key, k)      the generated analysis of docs/examples/<key>.py (see build.py)
 */
(function (Docs) {
  "use strict";
  const { el, svg } = Docs;

  /* ---------------------------------------------------------- data access */

  /**
   * Generated data of one example for max_contexts = k: { file, source, program, run, node, edge }.
   * node(id) and edge(caller, callee) throw when the analyzer no longer produces them, so a
   * scene whose story stopped being true fails loudly instead of showing something false.
   */
  Docs.example = (key, k = 16) => {
    const ex = window.DOCS_DATA.examples[key];
    if (!ex) throw new Error(`unknown example ${key}: add it to EXAMPLES in docs/build.py`);
    const run = ex.runs[String(k)];
    const must = (x, what) => {
      if (!x) throw new Error(`${ex.file}: the analyzer no longer produces ${what}; update this chapter`);
      return x;
    };
    return {
      ...ex,
      run,
      node: (id) => must(run.nodes.find((n) => n.id === id), `node ${id}`),
      edge: (caller, callee) => must(run.edges.find((e) => e.caller === caller && e.callee === callee),
        `edge ${caller} → ${callee}`),
    };
  };

  /** A titled panel inside a scene. */
  Docs.panel = (title, ...children) =>
    el("div", { class: "panel" }, title ? el("div", { class: "panel-title" }, title) : null, ...children);

  /* ---------------------------------------------------------- palette */

  const PALETTE_SIZE = 8;
  const assigned = new Map();

  Docs.palette = {
    /** Give names their hues in this order (call with the example's functions, top to bottom). */
    seed(names) {
      names.forEach((n) => this.color(n));
    },
    color(name) {
      if (!name || name === "<module>") return "var(--fn-module)";
      const base = name.replace(/\[.*$/, ""); // "apply[f={square}]" → "apply"
      if (!assigned.has(base)) assigned.set(base, assigned.size % PALETTE_SIZE);
      return `var(--fn-${assigned.get(base)})`;
    },
  };

  /** Seed the palette from an example: functions in definition order, then classes. */
  Docs.seedPalette = (ex) => {
    const fns = ex.program.functions.filter((f) => f.kind !== "module").map((f) => f.id);
    const classes = ex.program.classes.map((c) => c.id);
    Docs.palette.seed([...fns, ...classes].sort((a, b) => lineOf(ex, a) - lineOf(ex, b)));
  };
  const lineOf = (ex, id) =>
    (ex.program.functions.find((f) => f.id === id) || ex.program.classes.find((c) => c.id === id) || { line: 0 }).line;

  /* ---------------------------------------------------------- Python highlighting */

  const KEYWORDS = new Set(("False None True and as assert async await break class continue def del elif else " +
    "except finally for from global if import in is lambda nonlocal not or pass raise return try while with yield").split(" "));
  const BUILTINS = new Set(("print len map filter sorted min max list dict set tuple iter next super property " +
    "classmethod staticmethod isinstance range str repr bool hash type object enumerate zip reversed abs self cls").split(" "));

  /**
   * Split Python source into lines of [text, cls] pieces. cls: k keyword, s string, c comment,
   * n number, d decorator, b builtin, f defined name, "" plain. Handles multi-line strings.
   */
  Docs.highlight = (source) => {
    const lines = [];
    let open = null; // closing quote of a string that continues on the next line
    let defNext = false;
    for (const line of source.split("\n")) {
      const out = [];
      let i = 0;
      const push = (text, cls) => text && out.push([text, cls]);
      if (open) {
        const end = line.indexOf(open);
        if (end < 0) { push(line, "s"); lines.push(out); continue; }
        push(line.slice(0, end + open.length), "s");
        i = end + open.length;
        open = null;
      }
      while (i < line.length) {
        const rest = line.slice(i);
        let m;
        if (rest[0] === "#") { push(rest, "c"); break; }
        if ((m = /^[rbfuRBFU]{0,2}("""|'''|"|')/.exec(rest))) {
          const q = m[1];
          let j = m[0].length;
          while (j < rest.length && rest.slice(j, j + q.length) !== q) j += rest[j] === "\\" ? 2 : 1;
          if (j >= rest.length && q.length === 3) { push(rest, "s"); open = q; break; }
          push(rest.slice(0, j + q.length), "s");
          i += j + q.length;
          continue;
        }
        if ((m = /^@[\w.]+/.exec(rest)) && !line.slice(0, i).trim()) { push(m[0], "d"); i += m[0].length; continue; }
        if ((m = /^\d[\w.]*/.exec(rest))) { push(m[0], "n"); i += m[0].length; continue; }
        if ((m = /^[A-Za-z_]\w*/.exec(rest))) {
          const w = m[0];
          const cls = defNext ? "f" : KEYWORDS.has(w) ? "k" : BUILTINS.has(w) ? "b" : "";
          defNext = w === "def" || w === "class";
          push(w, cls);
          i += w.length;
          continue;
        }
        m = /^\s+|^./.exec(rest);
        push(m[0], "");
        i += m[0].length;
      }
      lines.push(out);
    }
    return lines;
  };

  /** HTML of one highlighted line; marks = [{from, to, cls}] (column ranges, non-overlapping). */
  function lineHTML(pieces, marks) {
    const esc = Docs.escape;
    const sorted = [...marks].sort((a, b) => a.from - b.from);
    let html = "";
    let col = 0;
    let open = null;
    for (const [text, cls] of pieces) {
      for (let i = 0; i < text.length; ) {
        const at = col + i;
        if (open && at === open.to) { html += "</mark>"; open = null; }
        if (!open) {
          const m = sorted.find((r) => r.from === at);
          if (m) { html += `<mark class="${m.cls}">`; open = m; }
        }
        const stops = [text.length - i];
        if (open) stops.push(open.to - at);
        const next = sorted.find((r) => r.from > at);
        if (!open && next) stops.push(next.from - at);
        const n = Math.max(1, Math.min(...stops));
        const chunk = esc(text.slice(i, i + n));
        html += cls ? `<span class="${cls}">${chunk}</span>` : chunk;
        i += n;
      }
      col += text.length;
    }
    if (open) html += "</mark>";
    return html || " ";
  }

  /* ---------------------------------------------------------- code view */

  /**
   * Python source with line numbers. Call set(state) per step; everything not in `state` resets.
   *   focus    [line, …]                     highlighted lines (others dim when dim: true)
   *   marks    [{line, text, cls, nth?}]     box a piece of a line (cls: accent | warn | ok);
   *            [{line, col, len, cls}]          …or a column range (overlapping marks are dropped)
   *   notes    {line: [Node, …]}             chips shown at the end of a line
   *   regions  [{from, to, color}]           colored bar for a line range (inner ranges last)
   */
  Docs.CodeView = class CodeView {
    constructor(root, source, { firstLine = 1, onLineClick } = {}) {
      this.raw = source.replace(/\n$/, "").split("\n");
      this.pieces = Docs.highlight(this.raw.join("\n"));
      this.firstLine = firstLine;
      this.pre = el("pre", { class: "code" });
      this.lines = this.raw.map((_, i) => {
        const n = firstLine + i;
        const src = el("span", { class: "src", html: lineHTML(this.pieces[i], []) });
        const notes = el("span", { class: "notes" });
        const row = el("div", { class: "ln", "data-line": n }, el("span", { class: "no" }, n), src, notes);
        if (onLineClick) { row.dataset.click = ""; row.addEventListener("click", () => onLineClick(n)); }
        this.pre.append(row);
        return { row, src, notes, marked: false };
      });
      root.append(this.pre);
    }

    line(n) {
      return this.lines[n - this.firstLine];
    }

    set({ focus = [], dim = false, marks = [], notes = {}, regions = [] } = {}) {
      this.pre.classList.toggle("dimmed", dim && focus.length > 0);
      const byLine = new Map();
      for (const m of marks) {
        const raw = this.raw[m.line - this.firstLine] || "";
        let from = m.col ?? -1;
        if (m.col === undefined) for (let k = 0; k <= (m.nth || 0); k++) from = raw.indexOf(m.text, from + 1);
        if (from < 0) continue;
        const to = from + (m.len ?? m.text.length);
        const list = byLine.get(m.line) || [];
        if (list.some((r) => from < r.to && r.from < to)) continue; // overlaps an earlier mark
        list.push({ from, to, cls: m.cls || "accent" });
        byLine.set(m.line, list);
      }
      this.lines.forEach((l, i) => {
        const n = this.firstLine + i;
        l.row.classList.toggle("focus", focus.includes(n));
        const lineMarks = byLine.get(n) || [];
        if (lineMarks.length || l.marked) l.src.innerHTML = lineHTML(this.pieces[i], lineMarks);
        l.marked = lineMarks.length > 0;
        l.notes.replaceChildren(...(notes[n] || []));
        l.row.classList.remove("region");
        l.row.style.removeProperty("--region");
      });
      for (const r of regions) {
        for (let n = r.from; n <= r.to; n++) {
          const l = this.line(n);
          if (!l) continue;
          l.row.classList.add("region");
          l.row.style.setProperty("--region", r.color);
        }
      }
    }

    scrollTo(n) {
      const l = this.line(n);
      if (l && this.pre.scrollHeight > this.pre.clientHeight) l.row.scrollIntoView({ block: "nearest" });
    }
  };

  /* ---------------------------------------------------------- tokens */

  const KIND_TITLE = {
    Closure: "function token (Closure)",
    Builtin: "builtin / stub token",
    ClassObj: "class object",
    Instance: "abstract instance (named by allocation site)",
    Box: "container (named by allocation site)",
    BoundMethod: "bound method (receiver pre-filled)",
    ClassMethod: "classmethod descriptor",
    StaticMethod: "staticmethod descriptor",
    Super: "super() proxy",
    Property: "property descriptor",
  };

  /** One token from generated data ({kind, label, name, …}) as a chip. */
  Docs.tokenChip = (t, { cls = "" } = {}) => {
    let body = [t.label];
    if (t.kind === "Instance" || t.kind === "Box") {
      const m = /^<?(.*?)(@\d+:\d+)>?$/.exec(t.kind === "Box" ? t.label.slice(1, -1) + ">" : t.label);
      if (m) body = [m[1], el("small", null, m[2])];
    } else if (t.kind === "BoundMethod" && t.receiver) {
      body = [t.name, el("small", null, "@" + t.receiver)];
    } else if (t.kind === "ClassObj") {
      body = ["class ", t.name];
    }
    return el("span", {
      class: `tok tok-${t.kind} ${cls}`.trim(),
      style: { "--c": t.kind === "Box" ? "var(--muted)" : Docs.palette.color(t.name) },
      title: `${KIND_TITLE[t.kind] || t.kind}: ${t.label}`,
    }, ...body);
  };

  /** Shorthand token for hand-written steps: Docs.fn("square") → a Closure chip. */
  Docs.fn = (name) => Docs.tokenChip({ kind: "Closure", label: name, name });

  /** A set of tokens, keyed by label so that adding one animates only the new chip. Empty: ∅. */
  Docs.chipSet = (toks, { into, flash = [] } = {}) => {
    const list = into || el("span", { class: "chip-list" });
    list.querySelector(":scope > .set-empty")?.remove();
    Docs.reconcile(list, toks, {
      key: (t) => t.label,
      create: (t) => Docs.tokenChip(t),
      update: (node, t) => {
        if (flash.includes(t.label)) {
          node.classList.remove("flash");
          void node.offsetWidth; // restart the animation
          node.classList.add("flash");
        }
      },
    });
    if (!toks.length) list.append(el("span", { class: "set-brace set-empty" }, "∅"));
    return list;
  };

  /* ---------------------------------------------------------- environments */

  /**
   * The env of one node: name ↦ {tokens}. render(env, {hot, only, title, active}) redraws in place.
   *   only    show just these names (in this order)
   *   hot     names to highlight (row background + chip flash on new tokens)
   */
  Docs.EnvView = class EnvView {
    constructor(root, title, { empty = "(nothing known yet)" } = {}) {
      this.empty = empty;
      this.titleEl = el("div", { class: "env-title" }, title || "");
      this.rows = el("div", { class: "env-rows" });
      this.root = el("div", { class: "env" }, this.titleEl, this.rows);
      root.append(this.root);
      this.seen = new Map(); // name → labels shown last time, to flash only what is new
    }

    /** The chip element showing token `label` in row `name` (for tethers), or null. */
    chip(name, label) {
      const row = this.rows.__keyed && this.rows.__keyed.get(name);
      return (row && row.lastChild.__keyed && row.lastChild.__keyed.get(label)) || null;
    }

    render(env, { only, hot = [], title, active = false } = {}) {
      if (title !== undefined) this.titleEl.replaceChildren(...[].concat(title));
      this.root.classList.toggle("active", active);
      const names = only || Object.keys(env);
      const items = names.map((name) => ({ name, toks: env[name] || [] }));
      Docs.reconcile(this.rows, items, {
        key: (r) => r.name,
        create: (r) => el("div", { class: "env-row" },
          el("span", null, r.name), el("span", { class: "mapsto" }, "↦"), el("span", { class: "chip-list" })),
        update: (row, r) => {
          const before = this.seen.get(r.name) || [];
          const fresh = r.toks.map((t) => t.label).filter((l) => !before.includes(l));
          row.classList.toggle("hot", hot.includes(r.name));
          Docs.chipSet(r.toks, { into: row.lastChild, flash: hot.includes(r.name) ? fresh : [] });
          this.seen.set(r.name, r.toks.map((t) => t.label));
        },
      });
      if (!items.length) this.rows.replaceChildren(el("div", { class: "env-empty" }, this.empty));
      else this.rows.querySelector(".env-empty")?.remove();
    }
  };

  /* ---------------------------------------------------------- graph */

  const NODE_H = 30;
  const GAP_X = 18;
  const GAP_Y = 52;
  const PAD = 12;
  let graphCount = 0;
  let measureCtx = null;

  function textWidth(s) {
    measureCtx = measureCtx || document.createElement("canvas").getContext("2d");
    const mono = getComputedStyle(document.documentElement).getPropertyValue("--font-mono");
    measureCtx.font = `12.5px ${mono}`;
    return measureCtx.measureText(s).width;
  }

  /**
   * Rank = longest path from a root (callers sit above everything they call), ignoring back edges
   * found by a DFS from the roots so that recursion cannot stretch the picture. Order inside a
   * rank by the mean position of callers (one barycenter pass). Rows are centered.
   */
  function layout(nodes, edges) {
    const ids = nodes.map((n) => n.id);
    const preds = new Map(ids.map((id) => [id, []]));
    const succs = new Map(ids.map((id) => [id, []]));
    for (const e of edges) {
      if (e.from === e.to || !preds.has(e.to) || !succs.has(e.from)) continue;
      preds.get(e.to).push(e.from);
      succs.get(e.from).push(e.to);
    }
    // DFS from the roots (then from anything left); an edge into the current path is a back edge
    const back = new Set(), state = new Map(), topo = [];
    const visit = (id) => {
      state.set(id, "open");
      for (const s of succs.get(id)) {
        if (state.get(s) === "open") back.add(`${id}→${s}`);
        else if (!state.has(s)) visit(s);
      }
      state.set(id, "done");
      topo.push(id);
    };
    ids.filter((id) => preds.get(id).length === 0).forEach(visit);
    ids.filter((id) => !state.has(id)).forEach(visit);
    const rank = new Map(ids.map((id) => [id, 0]));
    for (const id of topo.reverse()) {
      for (const s of succs.get(id)) {
        if (!back.has(`${id}→${s}`)) rank.set(s, Math.max(rank.get(s), rank.get(id) + 1));
      }
    }
    const rows = [];
    nodes.forEach((n) => (rows[rank.get(n.id)] = rows[rank.get(n.id)] || []).push(n));
    const pos = new Map();
    let width = 0;
    rows.forEach((row, r) => {
      if (r > 0) {
        const center = (id) => {
          const ps = preds.get(id).map((p) => pos.get(p)).filter(Boolean);
          return ps.length ? ps.reduce((a, p) => a + p.x, 0) / ps.length : Infinity;
        };
        row.sort((a, b) => center(a.id) - center(b.id));
      }
      let x = 0;
      for (const n of row) {
        const w = Math.ceil(textWidth(n.label)) + 24;
        pos.set(n.id, { x: x + w / 2, y: r * (NODE_H + GAP_Y) + NODE_H / 2, w, rank: r });
        x += w + GAP_X;
      }
      row.width = x - GAP_X;
      width = Math.max(width, row.width);
    });
    rows.forEach((row) => row.forEach((n) => (pos.get(n.id).x += (width - row.width) / 2)));
    return pos;
  }

  /** Edge path between two positioned nodes (down, sideways, back up, or a self loop). */
  function edgePath(a, b, self) {
    const h = NODE_H / 2;
    if (self) {
      const x = a.x + a.w / 2;
      return `M${x},${a.y - 7} C${x + 34},${a.y - 26} ${x + 34},${a.y + 26} ${x + 2},${a.y + 9}`;
    }
    if (b.y > a.y + 1) {
      const y1 = a.y + h, y2 = b.y - h - 2, dy = (y2 - y1) / 2;
      return `M${a.x},${y1} C${a.x},${y1 + dy} ${b.x},${y2 - dy} ${b.x},${y2}`;
    }
    if (Math.abs(b.y - a.y) <= 1) { // same row: arc over the top
      const lift = 26 + Math.abs(b.x - a.x) * 0.12;
      return `M${a.x},${a.y - h} C${a.x},${a.y - h - lift} ${b.x},${b.y - h - lift} ${b.x},${b.y - h - 2}`;
    }
    const x1 = a.x + a.w / 2, x2 = b.x + b.w / 2 + 2, bulge = 40 + (a.y - b.y) * 0.15; // back up
    return `M${x1},${a.y} C${x1 + bulge},${a.y} ${x2 + bulge},${b.y} ${x2},${b.y}`;
  }

  /**
   * render({nodes, edges, exitTo}) animates from the previous picture to this one.
   *   nodes  [{id, label, name?, cls?}]   name picks the color (defaults to label); cls: active dim merged ghost
   *   edges  [{id?, from, to, cls?, title?}]  cls: new warn dim pending tether
   *   exitTo {oldNodeId: newNodeId}       leaving nodes glide into a new node (merging)
   */
  Docs.GraphView = class GraphView {
    constructor(root) {
      this.uid = `g${++graphCount}`;
      const marker = (cls) => svg("marker", {
        id: `${this.uid}-${cls}`, class: cls, viewBox: "0 0 10 10", refX: 8, refY: 5,
        markerWidth: 7, markerHeight: 7, orient: "auto-start-reverse",
      }, svg("path", { d: "M0,0 L10,5 L0,10 z" }));
      this.edgeLayer = svg("g");
      this.nodeLayer = svg("g");
      this.svg = svg("svg", { class: "graph", role: "img" },
        svg("defs", null, ["base", "new", "warn", "pending"].map(marker)), this.edgeLayer, this.nodeLayer);
      root.append(el("div", { class: "graph-wrap" }, this.svg));
      this.pos = new Map(); // id → {x, y, w}: where each node is drawn right now
      this.box = null;
      this.cancel = () => {};
    }

    render({ nodes, edges = [], exitTo = {} }) {
      this.cancel();
      const target = layout(nodes, edges);
      const before = new Map(this.pos); // where things are now (mid-animation if interrupted)
      const from = new Map(before);
      for (const [old, into] of Object.entries(exitTo)) { // a merged node starts where its parts were
        if (!from.has(into) && from.has(old)) from.set(into, { ...from.get(old), w: target.get(into)?.w || 60 });
      }
      for (const [id, p] of target) if (!from.has(id)) from.set(id, p);
      const leaving = [...before.keys()].filter((id) => !target.has(id));

      Docs.reconcile(this.nodeLayer, nodes, {
        key: (n) => n.id,
        order: false,
        create: (n) => svg("g", { class: "node" }, svg("rect", { rx: 8, height: NODE_H }), svg("text"), svg("title")),
        update: (g, n) => {
          g.setAttribute("class", `node ${n.cls || ""}`.trim());
          g.style.setProperty("--c", Docs.palette.color(n.name || n.label));
          g.childNodes[1].textContent = n.label;
          g.childNodes[2].textContent = n.title || n.label;
        },
      });
      const edgeKey = (e) => e.id || `${e.from}→${e.to}`;
      Docs.reconcile(this.edgeLayer, edges, {
        key: edgeKey,
        order: false,
        create: () => svg("path", { class: "edge" }, svg("title")),
        update: (p, e) => {
          const cls = e.cls || "base";
          p.setAttribute("class", `edge ${e.cls || ""}`.trim());
          p.setAttribute("marker-end", `url(#${this.uid}-${["new", "warn", "pending"].includes(cls) ? cls : "base"})`);
          p.firstChild.textContent = e.title || `${e.from} → ${e.to}`;
          p.__edge = e;
        },
      });

      const nodeEls = new Map([...this.nodeLayer.__keyed].map(([k, g]) => [k, g]));
      const edgeEls = [...this.edgeLayer.__keyed.values()];
      const bounds = (pos) => {
        let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
        for (const p of pos.values()) {
          x0 = Math.min(x0, p.x - p.w / 2); x1 = Math.max(x1, p.x + p.w / 2 + 40);
          y0 = Math.min(y0, p.y - NODE_H / 2 - 30); y1 = Math.max(y1, p.y + NODE_H / 2);
        }
        return x0 === Infinity ? [0, 0, 100, 40] : [x0 - PAD, y0 - PAD, x1 - x0 + 2 * PAD, y1 - y0 + 2 * PAD];
      };
      const box0 = this.box || bounds(target);
      const box1 = bounds(target);
      const lerp = Docs.lerp;

      const frame = (p) => {
        const now = new Map();
        for (const [id, t] of target) {
          const f = from.get(id);
          now.set(id, { x: lerp(f.x, t.x, p), y: lerp(f.y, t.y, p), w: lerp(f.w, t.w, p) });
        }
        for (const id of leaving) { // glide into the node it merges with, else stay put
          const f = before.get(id), t = target.get(exitTo[id]) || f;
          now.set(id, { x: lerp(f.x, t.x, p), y: lerp(f.y, t.y, p), w: f.w });
        }
        for (const [id, q] of now) {
          const g = nodeEls.get(id) || this.nodeLayer.querySelector(`[data-id="${CSS.escape(id)}"]`);
          if (!g) continue;
          g.dataset.id = id;
          g.setAttribute("transform", `translate(${q.x - q.w / 2},${q.y - NODE_H / 2})`);
          g.firstChild.setAttribute("width", q.w);
          g.childNodes[1].setAttribute("x", q.w / 2);
          g.childNodes[1].setAttribute("y", NODE_H / 2);
        }
        for (const path of edgeEls) {
          const e = path.__edge, a = now.get(e.from), b = now.get(e.to);
          if (a && b) path.setAttribute("d", edgePath(a, b, e.from === e.to));
        }
        const box = box0.map((v, i) => lerp(v, box1[i], p));
        this.svg.setAttribute("viewBox", box.join(" "));
        this.svg.setAttribute("width", box[2]);
        this.box = box;
        this.pos = new Map([...now].filter(([id]) => target.has(id)));
      };
      // leaving nodes are still in the DOM for one fade; give them their ids for the frame loop
      for (const id of leaving) {
        const g = [...this.nodeLayer.children].find((c) => c.dataset.id === id);
        if (g) nodeEls.set(id, g);
      }
      this.cancel = Docs.tween(before.size ? 650 : 0, frame);
    }
  };

  /* ---------------------------------------------------------- tethers */

  /**
   * Curved connectors drawn over `host` between elements inside it (a closure and the node that
   * created it, a name and the box it points to). draw([{key, from, to, cls}]) with elements;
   * cls: hot | warn. Redrawn when the host resizes and once more after transitions settle.
   */
  Docs.Tethers = class Tethers {
    constructor(host) {
      this.host = host;
      host.classList.add("tether-host");
      this.svg = svg("svg", { class: "tethers", "aria-hidden": "true" });
      host.append(this.svg);
      this.pairs = [];
      new ResizeObserver(() => this.redraw()).observe(host);
    }

    draw(pairs) {
      this.pairs = pairs;
      this.redraw();
      clearTimeout(this.timer);
      this.timer = setTimeout(() => this.redraw(), 480);
    }

    redraw() {
      const box = this.host.getBoundingClientRect();
      this.svg.setAttribute("width", box.width);
      this.svg.setAttribute("height", box.height);
      const live = this.pairs.filter((p) => p.from.isConnected && p.to.isConnected);
      Docs.reconcile(this.svg, live, {
        key: (p) => p.key,
        order: false,
        create: () => svg("path"),
        update: (path, p) => {
          const a = p.from.getBoundingClientRect(), b = p.to.getBoundingClientRect();
          const ax = a.left + a.width / 2 - box.left, ay = a.top + a.height / 2 - box.top;
          const bx = b.left + b.width / 2 - box.left, by = b.top + b.height / 2 - box.top;
          let d;
          if (Math.abs(bx - ax) > Math.abs(by - ay)) { // mostly sideways: leave and enter by the sides
            const s = Math.sign(bx - ax), x1 = ax + (s * a.width) / 2, x2 = bx - (s * b.width) / 2;
            const h = (x2 - x1) / 2;
            d = `M${x1},${ay} C${x1 + h},${ay} ${x2 - h},${by} ${x2},${by}`;
          } else { // mostly vertical (stacked boxes): a bracket through the right-hand gutter
            const x1 = a.right - box.left, x2 = b.right - box.left;
            const gx = Math.min(Math.max(x1, x2) + 26, box.width - 3);
            d = `M${x1},${ay} C${gx},${ay} ${gx},${by} ${x2},${by}`;
          }
          path.setAttribute("d", d);
          path.setAttribute("class", p.cls || "");
        },
      });
    }
  };

  /**
   * Graph data from generated edges, function-level ({caller, callee}) or context-level (+ site).
   * An edge's class is cls(edge) if given, else its own `cls` field.
   */
  Docs.graphFrom = (edges, { nodes: extraNodes = [], cls = (e) => e.cls || "" } = {}) => {
    const ids = [];
    const add = (id) => !ids.includes(id) && ids.push(id);
    extraNodes.forEach(add);
    edges.forEach((e) => { add(e.caller); add(e.callee); });
    return {
      nodes: ids.map((id) => ({ id, label: id, cls: id.endsWith("[*]") ? "merged" : "" })),
      edges: edges.map((e) => ({ from: e.caller, to: e.callee, cls: cls(e), title: e.site ? `${e.caller} → ${e.callee}  (line ${e.site.line}: ${e.site.text})` : undefined })),
    };
  };
})((window.Docs = window.Docs || {}));

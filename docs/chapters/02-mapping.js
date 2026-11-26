/* Chapter 2: mapping the program (extract.py).  Example: docs/examples/mapping.py
 *
 * One step per function of the generated `program` inventory: its region in the code, its
 * FuncInfo card (id in the chosen style, parent, params, locals, call sites) and its place in
 * the nesting tree.
 */
(function () {
  "use strict";
  const { el, panel, example, CodeView, GraphView, Stepper } = Docs;

  const ex = example("mapping");
  Docs.seedPalette(ex);
  const funcs = ex.program.functions;
  const byId = (id) => {
    const f = funcs.find((x) => x.id === id);
    if (!f) throw new Error(`${ex.file}: no function ${id} any more; update this chapter`);
    return f;
  };
  const children = (f) => funcs.filter((c) => c.parent === f.id);
  const descendants = (f) => children(f).flatMap((c) => [c, ...descendants(c)]);
  const color = (f) => Docs.palette.color(f.id);

  /** Lines a function executes itself: its range minus the bodies of nested functions. */
  function ownLines(f) {
    const nested = descendants(f).filter((c) => c.end > c.line);
    const lines = [];
    for (let n = f.line; n <= f.end; n++) if (!nested.some((c) => n > c.line && n <= c.end)) lines.push(n);
    return lines;
  }

  const CAPTIONS = {
    "<module>": "The module body is a function too: <code>&lt;module&gt;</code>. Its locals are the " +
      "top-level names. Its call sites include <code>@log</code>: a decorator runs where its " +
      "<code>def</code> is.",
    log: "Every <code>def</code> gets a <strong>FuncId</strong>: file, line, column and qualname, its " +
      "dotted scope path. Try the id styles above.",
    "log.wrapper": "A nested <code>def</code> is a child in the nesting tree. <code>fn</code> is not " +
      "one of its locals, so it will be looked up in the parent, <code>log</code> (chapter 5). The " +
      "decorator on line 4 runs in <code>log</code>: it is one of <em>log's</em> call sites.",
    "Greeter.greet": "Methods are named after their class, but a class is not a scope for lookup: " +
      "the lexical parent of <code>greet</code> is <code>&lt;module&gt;</code>.",
    "Greeter.greet.<lambda>@11": "A lambda is named by its line. Its only local is <code>s</code>.",
    main: "Notice what the inventory does <em>not</em> know: that <code>fn(*args)</code> on line 6 " +
      "will call <code>main</code>. That takes values, which is the rest of this story.",
  };

  const root = document.getElementById("inventory");
  const codeBox = el("div");
  const cardBox = el("div");
  const treeBox = el("div");
  let style = "short";
  const controls = el("div", { class: "controls" }, "id style",
    Docs.segmented(["full", "module", "short"], style, (s) => {
      style = s;
      if (stepper) stepper.refresh();
    }));
  root.append(controls, el("div", { class: "panels code-wide" },
    panel("Program", codeBox),
    el("div", { class: "stack" }, panel("FuncInfo", cardBox), panel("Nesting tree (lexical parent → child)", treeBox))));

  const steps = Object.entries(CAPTIONS).map(([id, caption]) => ({ f: byId(id), caption }));
  const code = new CodeView(codeBox, ex.source, {
    onLineClick: (n) => { // innermost function whose range holds line n
      const inner = steps.map((s, i) => [s.f, i]).filter(([f]) => f.line <= n && n <= f.end)
        .sort(([a], [b]) => b.line - a.line)[0];
      if (inner) stepper.go(inner[1]);
    },
  });
  const tree = new GraphView(treeBox);

  function card(f) {
    const row = (key, ...value) => el("div", { class: "row" }, el("span", { class: "key" }, key), ...value);
    const mono = (xs) => (xs.length ? xs.join(", ") : "–");
    return el("div", { class: "card active", style: { "--c": color(f) } },
      el("div", { class: "card-title", style: { color: color(f) } }, f.ids[style]),
      row("kind", f.kind),
      row("parent", f.parent ? funcs.find((p) => p.id === f.parent).ids[style] : "–"),
      row("params", mono(f.params)),
      row("locals", mono(f.locals)),
      row("call sites", f.sites.length ? "" : "–"),
      ...f.sites.map((s) => el("div", { class: "row" },
        el("span", { class: "muted" }, `L${s.line}:${s.col}`), s.text,
        el("span", { class: `badge ${s.kind === "decorator" ? "accent" : ""}` }, s.kind))));
  }

  const stepper = new Stepper(root, steps, ({ f }) => {
    const sub = descendants(f);
    code.set({
      focus: ownLines(f),
      dim: true,
      regions: [f, ...sub].filter((x) => x.kind !== "module").map((x) => ({ from: x.line, to: x.end, color: color(x) })),
      marks: f.sites.map((s) => s.kind === "decorator"
        ? { line: s.line, col: s.col - 1, len: s.text.length }
        : { line: s.line, col: s.col, len: s.text.length })
        .sort((a, b) => b.len - a.len), // outermost first: nested calls overlap it and are dropped
    });
    cardBox.replaceChildren(card(f));
    tree.render({
      nodes: funcs.map((x) => ({ id: x.id, label: x.ids.short, cls: x === f ? "active" : sub.includes(x) ? "" : "dim" })),
      edges: funcs.filter((x) => x.parent).map((x) => ({ from: x.parent, to: x.id })),
    });
  });
})();

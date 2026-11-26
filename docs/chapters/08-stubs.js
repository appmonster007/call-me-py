/* Chapter 8: stubs.  Example: docs/examples/stubs.py
 *
 * Each step pairs a line of the example with the real source of the stub that handles it
 * (Docs.snippet reads generated refs; the page's "In the code" list makes build.py collect them).
 */
(function () {
  "use strict";
  const { el, panel, example, CodeView, GraphView, Stepper } = Docs;

  const ex = example("stubs");
  Docs.seedPalette(ex);
  const env = ex.node("<module>").env;
  const builtin = (name) => ({ kind: "Builtin", label: name, name });
  const edge = (c, d, cls = "") => ({ ...ex.edge(c, d), cls });

  const root = document.getElementById("stubs");
  const codeBox = el("div");
  const stubTitle = el("span");
  const stubBox = el("div");
  const graphBox = el("div");
  root.append(el("div", { class: "panels" },
    panel("Program", codeBox),
    el("div", { class: "stack" }, panel(stubTitle, stubBox), panel("Call graph", graphBox))));
  const code = new CodeView(codeBox, ex.source);
  const g = new GraphView(graphBox);

  const steps = [
    {
      caption: "<code>map</code> is not defined in this file. <code>lookup</code> falls through to the " +
        "builtins and finds a <strong>Builtin</strong> token: a name with a stub attached.",
      code: { focus: [12], dim: true, marks: [{ line: 12, text: "map" }], notes: { 12: [Docs.tokenChip(builtin("map"))] } },
      edges: [],
    },
    {
      caption: "Calling a Builtin runs its <strong>stub</strong>: a few lines of Python over abstract " +
        "values. <code>_map</code> calls its first argument with the rest.",
      code: { focus: [12], dim: true, marks: [{ line: 12, text: "map(square, [1, 2, 3])" }] },
      stub: "_map",
      edges: [],
    },
    {
      caption: "The stub makes that call <em>at the map call site</em>, so the edge is " +
        "<code>&lt;module&gt; → square</code>. That's what a traced run reports too: C frames are invisible.",
      code: { focus: [12, 3, 4], dim: true, marks: [{ line: 12, text: "map(square, [1, 2, 3])", cls: "ok" }] },
      stub: "_map",
      edges: [edge("<module>", "square", "new")],
    },
    {
      caption: "<code>sorted(…, key=by_len)</code>: the stub calls the key function. The same pattern " +
        "covers <code>min</code>, <code>max</code>, <code>filter</code>, <code>reduce</code>, …",
      code: { focus: [13, 6, 7], dim: true, marks: [{ line: 13, text: "key=by_len", cls: "ok" }] },
      stub: "_sorted",
      edges: [edge("<module>", "square"), edge("<module>", "by_len", "new")],
    },
    {
      caption: "<code>functools.partial(add, 1)</code> returns a token that <em>carries</em> " +
        "<code>add</code> and the bound argument.",
      code: { focus: [14], dim: true, marks: [{ line: 14, text: "functools.partial(add, 1)" }], notes: { 14: env.inc.map((t) => Docs.tokenChip(t)) } },
      stub: "_partial",
      brief: true, // a long stub: link to it instead of showing it
      edges: [edge("<module>", "square"), edge("<module>", "by_len")],
    },
    {
      caption: "Calling it runs the <code>&lt;partial&gt;</code> stub, which passes the bound arguments " +
        "first and calls <code>add</code>.",
      code: { focus: [15, 9, 10], dim: true, marks: [{ line: 15, text: "inc(41)", cls: "ok" }] },
      stub: "_call_partial",
      edges: [edge("<module>", "square"), edge("<module>", "by_len"), edge("<module>", "add", "new")],
    },
  ];

  let shown = null;
  new Stepper(root, steps, (step) => {
    code.set(step.code);
    if (step.stub !== shown) {
      shown = step.stub;
      stubTitle.textContent = step.stub ? `The stub: stubs.py · ${step.stub}` : "The stub";
      stubBox.replaceChildren();
      const sym = `callme/stubs.py::${step.stub}`;
      if (step.brief) stubBox.append(el("p", { class: "muted small" }, "Long, mostly bookkeeping for partials of partials: ", el("code-ref", { sym })));
      else if (step.stub) {
        const ref = Docs.snippet(sym);
        new CodeView(stubBox, ref.code, { firstLine: ref.line });
      } else stubBox.append(el("div", { class: "env-empty" }, "(none yet)"));
    }
    g.render(Docs.graphFrom(step.edges, { nodes: ["<module>"] }));
  });
})();

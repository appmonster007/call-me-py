/* Chapter 9: from state to graph, and checking it.
 *
 * Scene "collapse": the context-level graph of docs/examples/basics.py merges into the
 *                   function-level graph (GraphView exitTo), next to the real JSON / DOT text.
 * Scene "check":    for each case in build.py's CASES, EXPECTED vs. found as a Venn diagram.
 */
(function () {
  "use strict";
  const { el, svg, panel, example, GraphView, Stepper } = Docs;
  const fnOf = (id) => id.replace(/\[.*/, "");

  /* ---------------------------------------------------------- scene 1: collapse */
  {
    const ex = example("basics");
    Docs.seedPalette(ex);
    const ctxEdges = [];
    for (const e of ex.run.context_edges) {
      if (!ctxEdges.some((x) => x.caller === e.caller && x.callee === e.callee)) ctxEdges.push(e);
    }
    const contextGraph = Docs.graphFrom(ctxEdges, { nodes: ["<module>"] });
    const functionGraph = Docs.graphFrom(ex.run.edges, { nodes: ["<module>"] });
    const exitTo = Object.fromEntries(ex.run.nodes.map((n) => [n.id, fnOf(n.id)]));
    const json = (x) => JSON.stringify(x, null, 1);

    const root = document.getElementById("collapse");
    const graphBox = el("div");
    const textTitle = el("span");
    const text = el("pre", { class: "text" });
    root.append(el("div", { class: "panels" }, panel("Call graph", graphBox), panel(textTitle, text)));
    const g = new GraphView(graphBox);

    new Stepper(root, [
      {
        caption: "What the solver produced: one box per (function, context), one arrow per calling pair. " +
          "Both <code>apply</code> nodes are there.",
        graph: contextGraph, title: "JSON · context_edges",
        text: json(ex.run.context_edges.slice(0, 4)) + "\n…",
      },
      {
        caption: "<code>function_edges()</code> forgets contexts: the nodes of one function merge, and " +
          "duplicate arrows collapse into one.",
        graph: functionGraph, exitTo, title: "JSON · edges",
        text: json(ex.run.edges),
      },
      {
        caption: "<code>export.py</code> writes it out: JSON (functions, call sites with their targets, edges, " +
          "contexts) or Graphviz DOT, here with call-site lines on the arrows.",
        graph: functionGraph, title: "DOT · python -m callme basics.py --format dot",
        text: ex.run.dot,
      },
    ], (step) => {
      g.render({ ...step.graph, exitTo: step.exitTo || {} });
      textTitle.textContent = step.title;
      text.textContent = step.text;
    });
  }

  /* ---------------------------------------------------------- scene 2: check */
  {
    const cases = window.DOCS_DATA.cases;
    const root = document.getElementById("check");
    const venn = svg("svg", { viewBox: "0 0 440 230", class: "venn", role: "img" });
    const badges = el("div", { class: "controls" });
    const lists = el("div", { class: "cards" });
    const key = ([a, b]) => `${a} → ${b}`;

    const show = (name) => {
      const c = cases[name];
      const expected = new Set(c.expected.map(key));
      const allowed = new Set(Object.values(c.allowed_extra).flat().map(key));
      const found = new Set(c.found.map(key));
      const missing = [...expected].filter((e) => !found.has(e));
      const matched = [...expected].filter((e) => found.has(e));
      const extra = [...found].filter((e) => !expected.has(e));
      const allowedExtra = extra.filter((e) => allowed.has(e));
      const unexpected = extra.filter((e) => !allowed.has(e));

      const count = (x, y, n, label, bad) => [
        svg("text", { x, y, class: `venn-n ${bad ? "bad" : ""}` }, String(n)),
        svg("text", { x, y: y + 22, class: "venn-label" }, label),
      ];
      venn.replaceChildren(
        svg("circle", { cx: 165, cy: 125, r: 95, class: "venn-e" }),
        svg("circle", { cx: 275, cy: 125, r: 95, class: "venn-f" }),
        svg("text", { x: 120, y: 18, class: "venn-title" }, "EXPECTED (traced run)"),
        svg("text", { x: 320, y: 18, class: "venn-title" }, "found (callme)"),
        ...count(112, 122, missing.length, "missing", missing.length > 0),
        ...count(220, 122, matched.length, "matched", false),
        ...count(322, 104, allowedExtra.length, "allowed extra", false),
        ...count(322, 160, unexpected.length, "unexpected", unexpected.length > 0),
      );
      const verdict = (name, ok) => el("span", { class: `badge ${ok ? "ok" : "warn"}` }, `${name} ${ok ? "✓" : "✗"}`);
      badges.replaceChildren(el("span", { class: "mono small" }, c.file),
        verdict("test_sound", !missing.length), verdict("test_precise", !unexpected.length));
      const list = (title, edges, cls) => el("div", { class: "card" },
        el("div", { class: "card-title" }, `${title} (${edges.length})`),
        ...(edges.length ? edges.map((e) => el("div", { class: `row ${cls}` }, e)) : [el("div", { class: "muted" }, "none")]));
      lists.replaceChildren(
        list("missing: unsound", missing, "warn-text"),
        list("matched", matched, ""),
        list("allowed extra", allowedExtra, "muted"),
        list("unexpected: imprecise", unexpected, "warn-text"));
    };

    const names = Object.keys(cases);
    root.append(
      el("div", { class: "controls" }, "case", Docs.segmented(names.map((n) => [n, n.split("/")[1]]), names[0], show)),
      badges,
      el("div", { class: "graph-wrap" }, venn),
      lists);
  }
})();

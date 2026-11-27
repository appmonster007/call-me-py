/* Chapter 3: contexts.  Example: info/examples/contexts.py, analysed with max_contexts 0, 16, 1.
 *
 * Each step shows one real run: its nodes (with parameter values and return values) and its
 * context-level call graph. Edges that the default run (16) does not have are drawn red.
 */
(function () {
  "use strict";
  const { el, panel, example, CodeView, GraphView, Stepper } = Docs;

  const precise = example("contexts", 16);
  Docs.seedPalette(precise);
  const fnKey = (e) => `${e.caller.replace(/\[.*/, "")}→${e.callee.replace(/\[.*/, "")}`;
  const truth = new Set(precise.run.edges.map((e) => `${e.caller}→${e.callee}`));
  const params = (fn) => (precise.program.functions.find((f) => f.id === fn) || { params: [] }).params;

  const root = document.getElementById("contexts");
  const codeBox = el("div");
  const badge = el("span", { class: "badge accent" });
  const cards = el("div", { class: "cards" });
  const graphBox = el("div");
  root.append(el("div", { class: "panels code-wide" },
    panel("Program", codeBox),
    el("div", { class: "stack" },
      panel(el("span", null, "Nodes · ", badge), cards),
      panel("Call graph (one box per node)", graphBox))));
  const code = new CodeView(codeBox, precise.source);
  const g = new GraphView(graphBox);

  /** A node card: its parameters' values (context, or the merged entry) and its return value. */
  function nodeCard(n) {
    const rows = params(n.function).map((p) => el("div", { class: "row" },
      el("span", { class: "key" }, `${p} ↦`), Docs.chipSet(n.env[p] || [])));
    return el("div", { class: `card ${n.merged ? "merged" : ""}`.trim(), title: n.merged ? "widened node: every overflow context lands here" : null },
      el("div", { class: "card-title", style: { color: Docs.palette.color(n.function) } }, n.id),
      ...rows,
      el("div", { class: "row" }, el("span", { class: "key" }, "returns"), Docs.chipSet(n.ret)));
  }

  const steps = [
    {
      k: 0,
      caption: "<strong>max_contexts = 0</strong>: one summary per function. Both calls feed the same " +
        "node <code>ident[*]</code>, so <code>f</code> may be either function, and so may the return " +
        "value. Line 10 then calls both. The red edge to <code>double</code> never happens in a real run.",
      marks: [{ line: 10, text: "ident(square)(3)", cls: "warn" }],
    },
    {
      k: 16,
      caption: "<strong>max_contexts = 16</strong> (the default): one node per <em>context</em>, the " +
        "function's entry state, i.e. what each parameter may hold. <code>ident[f={square}]</code> " +
        "returns {square}, so line 10 calls exactly <code>square</code>.",
      marks: [{ line: 10, text: "ident(square)(3)", cls: "ok" }],
    },
    {
      k: 1,
      caption: "<strong>max_contexts = 1</strong>: every node is re-analysed each round, so contexts " +
        "are capped. Past the cap, new contexts go into one widened node, <code>ident[*]</code>. " +
        "Precision degrades gracefully; no call is lost.",
      marks: [{ line: 11, text: "ident(double)" }],
    },
  ];

  new Stepper(root, steps, (step) => {
    const ex = example("contexts", step.k);
    badge.textContent = `max_contexts = ${step.k}`;
    code.set({ focus: [7, 8, 10, 11], marks: step.marks });
    const wrong = (e) => !truth.has(fnKey(e));
    Docs.reconcile(cards, ex.run.nodes.filter((n) => n.function !== "<module>"), {
      key: (n) => n.id,
      create: () => el("div"),
      update: (holder, n) => holder.replaceChildren(nodeCard(n)),
    });
    const edges = [];
    for (const e of ex.run.context_edges) { // one arrow per caller/callee pair
      if (!edges.some((x) => x.caller === e.caller && x.callee === e.callee)) edges.push(e);
    }
    g.render(Docs.graphFrom(edges, { nodes: ["<module>"], cls: (e) => (wrong(e) ? "warn" : "") }));
  });
})();

/* Chapter 1: who does f() call?  Example: info/examples/basics.py
 *
 * Scene "names":  reading names dead-ends at the parameter f.
 * Scene "values": evaluating on sets of tokens resolves it (envs and edges from the real run).
 */
(function () {
  "use strict";
  const { el, panel, example, CodeView, EnvView, GraphView, Stepper } = Docs;

  const ex = example("basics");
  Docs.seedPalette(ex);
  const moduleEnv = ex.node("<module>").env;
  const applySq = ex.node("apply[f={square}]");
  const applyDb = ex.node("apply[f={double}]");
  const chips = (toks) => toks.map((t) => Docs.tokenChip(t));
  const edge = (caller, callee, cls = "") => ({ ...ex.edge(caller, callee), cls });
  const graph = (edges) => Docs.graphFrom(edges, { nodes: ["<module>"] });

  /* ---------------------------------------------------------- scene 1: names */
  {
    const root = document.getElementById("names");
    const codeBox = el("div");
    const graphBox = el("div");
    root.append(el("div", { class: "panels code-wide" },
      panel("Program", codeBox), panel("Call graph", graphBox)));
    const code = new CodeView(codeBox, ex.source);
    const g = new GraphView(graphBox);
    const known = graph([edge("<module>", "apply")]);
    const stuck = { // apply calls *something*: a ghost node we cannot name
      nodes: [...known.nodes, { id: "?", label: "?", cls: "ghost" }],
      edges: [...known.edges, { from: "apply", to: "?", cls: "pending" }],
    };

    new Stepper(root, [
      {
        caption: "A call graph has an arrow <em>caller → callee</em> for every call that can happen. " +
          "Line 13 is easy: it names its callee.",
        code: { focus: [13], marks: [{ line: 13, text: "apply" }] },
        graph: known,
      },
      {
        caption: "Inside <code>apply</code>, line 8 calls <code>f</code>. But no function is " +
          "<em>called</em> <code>f</code>: it is a parameter. Reading names, we are stuck.",
        code: { focus: [7, 8], marks: [{ line: 8, text: "f(v)", cls: "warn" }, { line: 7, text: "f", nth: 1, cls: "warn" }] },
        graph: stuck,
      },
      {
        caption: "Python decides at run time: <code>f</code> is whatever the caller passed in. So don't ask " +
          "<em>what is it called?</em>; ask <em>what could it be?</em> Follow the values.",
        code: { focus: [8, 13, 14], marks: [{ line: 13, text: "square" }, { line: 14, text: "double" }, { line: 8, text: "f(v)", cls: "warn" }] },
        graph: stuck,
      },
    ], (step) => {
      code.set(step.code);
      g.render(step.graph);
    });
  }

  /* ---------------------------------------------------------- scene 2: values */
  {
    const root = document.getElementById("values");
    const codeBox = el("div");
    const envBox = el("div", { class: "stack" });
    const graphBox = el("div");
    root.append(el("div", { class: "panels code-wide" },
      panel("Program", codeBox),
      el("div", { class: "stack" }, panel("Environments", envBox), panel("Call graph", graphBox))));
    const code = new CodeView(codeBox, ex.source);
    const modView = new EnvView(envBox, "<module>");
    const callView = new EnvView(envBox, "");
    const g = new GraphView(graphBox);
    const defs = ["square", "double", "apply", "pick"];

    // Edges that only line 15 produces: they need chapter 4's idea, so they stay dim here.
    const line15 = new Set(ex.run.context_edges.filter((e) => e.site.line === 15)
      .map((e) => `${e.caller.replace(/\[.*/, "")}→${e.callee.replace(/\[.*/, "")}`));
    const otherSites = new Set(ex.run.context_edges.filter((e) => e.site.line !== 15)
      .map((e) => `${e.caller.replace(/\[.*/, "")}→${e.callee.replace(/\[.*/, "")}`));

    const steps = [
      {
        caption: "The module body runs top to bottom. A <code>def</code> binds its name to a " +
          "<strong>token</strong> standing for that function.",
        code: { focus: [1, 4, 7, 10], dim: true },
        mod: { only: defs, hot: defs },
        call: null,
        edges: [],
      },
      {
        caption: "Line 13: the callee <code>apply</code> evaluates to {apply}, the argument " +
          "<code>square</code> to {square}.",
        code: { focus: [13], dim: true, marks: [{ line: 13, text: "apply" }, { line: 13, text: "square" }],
          notes: { 13: chips(moduleEnv.square) } },
        mod: { only: defs, hot: ["apply", "square"] },
        call: null,
        edges: [],
      },
      {
        caption: "Entering <code>apply</code>, each parameter gets its argument's value: " +
          "<code>f ↦ {square}</code>. The edge <code>&lt;module&gt; → apply</code> is recorded.",
        code: { focus: [7], dim: true, marks: [{ line: 7, text: "f", nth: 1 }] },
        mod: { only: defs },
        call: { node: applySq, hot: ["f"] },
        edges: [edge("<module>", "apply", "new")],
      },
      {
        caption: "Now line 8 has an answer: <code>f</code> evaluates to {square}, so " +
          "<code>f(v)</code> calls <code>square</code>.",
        code: { focus: [8], dim: true, marks: [{ line: 8, text: "f(v)", cls: "ok" }], notes: { 8: chips(applySq.env.f) } },
        mod: { only: defs },
        call: { node: applySq, hot: ["f"] },
        edges: [edge("<module>", "apply"), edge("apply", "square", "new")],
      },
      {
        caption: "Line 14 tells the same story with <code>double</code>, in a separate context " +
          "<code>apply[f={double}]</code>. Why keep the two calls apart is chapter 3.",
        code: { focus: [14, 8], dim: true, marks: [{ line: 14, text: "double" }, { line: 8, text: "f(v)", cls: "ok" }],
          notes: { 8: chips(applyDb.env.f) } },
        mod: { only: defs },
        call: { node: applyDb, hot: ["f"] },
        edges: [edge("<module>", "apply"), edge("apply", "square"), edge("apply", "double", "new")],
      },
      {
        caption: "That's the core idea: every expression evaluates to a <strong>set of tokens</strong>, " +
          "the functions it might be, and a call calls every token in its callee's set. " +
          "Line 15 (dimmed) needs one more idea, in chapter 4.",
        code: { focus: [15], marks: [{ line: 15, text: "pick(True)(3)", cls: "warn" }] },
        mod: { only: Object.keys(moduleEnv) },
        call: null,
        edges: ex.run.edges.map((e) => {
          const k = `${e.caller}→${e.callee}`;
          return { ...e, cls: line15.has(k) && !otherSites.has(k) ? "dim" : "" };
        }),
      },
    ];

    new Stepper(root, steps, (step) => {
      code.set(step.code);
      modView.render(moduleEnv, { ...step.mod, title: "<module>", active: !step.call });
      if (step.call) {
        callView.root.hidden = false;
        callView.render(step.call.node.env, { hot: step.call.hot, title: step.call.node.id, active: true });
      } else callView.root.hidden = true;
      g.render(graph(step.edges));
    });
  }
})();

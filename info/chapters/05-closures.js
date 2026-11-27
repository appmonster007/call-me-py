/* Chapter 5: closures.  Example: info/examples/closures.py
 *
 * Node environments come from the real run; a closure chip's tether is drawn to the card of the
 * node in its `parent` field. The last step is a labelled what-if (closures without tethers).
 */
(function () {
  "use strict";
  const { el, panel, example, CodeView, EnvView, GraphView, Stepper, Tethers } = Docs;

  const ex = example("closures");
  Docs.seedPalette(ex);
  const mod = ex.node("<module>");
  const wrapInc = ex.node("wrap[f={inc}]");
  const wrapDec = ex.node("wrap[f={dec}]");
  const run = ex.node("wrap.run");
  const callF = ex.run.sites.find((s) => s.text === "f(x)");

  const root = document.getElementById("closures");
  const codeBox = el("div");
  const diagram = el("div", { class: "diagram" });
  const graphBox = el("div");
  root.append(el("div", { class: "panels code-wide" },
    panel("Program", codeBox),
    el("div", { class: "stack" }, panel("Nodes and their environments", diagram), panel("Call graph", graphBox))));
  const code = new CodeView(codeBox, ex.source);
  const left = el("div", { class: "stack" });
  const right = el("div", { class: "stack" });
  diagram.append(left, right);
  const modV = new EnvView(left, mod.id);
  const incV = new EnvView(right, wrapInc.id);
  const decV = new EnvView(right, wrapDec.id);
  // wrap.run has no tokens in its env; its card shows the context's parent (the inherited tether)
  const parentPill = el("span", { class: "tok", style: { "--c": "var(--fn-3)" } }, run.parent);
  const calls = el("span", { class: "chip-list" });
  const runCard = el("div", { class: "env" },
    el("div", { class: "env-title" }, run.id),
    el("div", { class: "env-row" }, el("span", null, "parent"), el("span", { class: "mapsto" }, "↦"), parentPill),
    el("div", { class: "env-row" }, el("span", null, "f(x)"), el("span", { class: "mapsto" }, "→"), calls));
  left.append(runCard);
  const tethers = new Tethers(diagram);
  const g = new GraphView(graphBox);
  const edge = (c, d, cls = "") => ({ ...ex.edge(c, d), cls });

  const steps = [
    {
      caption: "Line 12 calls <code>wrap(inc)</code>, which creates the node <code>wrap[f={inc}]</code>. " +
        "Inside it, <code>def run</code> makes a token for <code>run</code>. The token remembers which " +
        "node made it: a <strong>tether</strong>.",
      code: { focus: [12, 8], dim: true, marks: [{ line: 12, text: "wrap(inc)" }] },
      show: { mod: ["wrap", "inc", "dec", "a"], inc: true }, hot: ["a"],
      ties: [["a", wrapInc]],
      edges: [edge("<module>", "wrap", "new")],
    },
    {
      caption: "Line 13 does it again: a second node, and a second <code>run</code> token tethered to " +
        "it. Same function, different token: a <code>Closure</code> is equal to another only if both " +
        "<code>func</code> and <code>parent</code> match.",
      code: { focus: [13, 8], dim: true, marks: [{ line: 13, text: "wrap(dec)" }] },
      show: { mod: ["wrap", "inc", "dec", "a", "b"], inc: true, dec: true }, hot: ["b"],
      ties: [["a", wrapInc], ["b", wrapDec]],
      edges: [edge("<module>", "wrap")],
    },
    {
      caption: "Line 14 calls the token in <code>a</code>. The new node <code>wrap.run</code> takes over the " +
        "token's tether as its context's <em>parent</em>.",
      code: { focus: [14], dim: true, marks: [{ line: 14, text: "a(1)" }] },
      show: { mod: ["wrap", "inc", "dec", "a", "b"], inc: true, dec: true, run: true }, hot: ["a"],
      ties: [["a", wrapInc, "hot"], ["b", wrapDec], [parentPill, wrapInc, "hot"]],
      edges: [edge("<module>", "wrap"), edge("<module>", "wrap.run", "new")],
    },
    {
      caption: "Line 9: <code>f</code> is not a local of <code>run</code>. <code>lookup</code> follows the " +
        "parent to <code>wrap[f={inc}]</code> and reads <code>f ↦ {inc}</code>. So <code>f(x)</code> " +
        "calls <code>inc</code>, and only <code>inc</code>.",
      code: { focus: [9], dim: true, marks: [{ line: 9, text: "f(x)", cls: "ok" }] },
      show: { mod: ["wrap", "inc", "dec", "a", "b"], inc: true, dec: true, run: true, calls: true }, hotInc: ["f"],
      ties: [["b", wrapDec], [parentPill, wrapInc, "hot"]],
      edges: [edge("<module>", "wrap"), edge("<module>", "wrap.run"), edge("wrap.run", "inc", "new")],
    },
    {
      caption: "<em>What if</em> tokens had no tethers? Then <code>f</code> would be the union over every " +
        "<code>wrap</code> node, {inc, dec}, and the red edge would appear: a call that never happens. " +
        "(Not the analyzer's output; a thought experiment.)",
      code: { focus: [9], dim: true, marks: [{ line: 9, text: "f(x)", cls: "warn" }] },
      show: { mod: ["wrap", "inc", "dec", "a", "b"], inc: true, dec: true, run: true, calls: true }, hotInc: ["f"],
      ties: [[parentPill, wrapInc, "warn"], [parentPill, wrapDec, "warn"]],
      edges: [edge("<module>", "wrap"), edge("<module>", "wrap.run"), edge("wrap.run", "inc"),
        { caller: "wrap.run", callee: "dec", cls: "warn" }],
    },
  ];

  new Stepper(root, steps, (step) => {
    const s = step.show;
    code.set(step.code);
    modV.render(mod.env, { only: s.mod, hot: step.hot || [] });
    incV.root.hidden = !s.inc;
    decV.root.hidden = !s.dec;
    runCard.hidden = !s.run;
    incV.render(wrapInc.env, { hot: step.hotInc || [], active: !!step.hotInc });
    decV.render(wrapDec.env, {});
    Docs.chipSet(s.calls ? callF.targets.map((name) => ({ kind: "Closure", label: name, name })) : [], { into: calls });
    const card = (node) => (node === wrapInc ? incV.root : decV.root);
    tethers.draw(step.ties.map(([from, node, cls], i) => ({
      key: `${typeof from === "string" ? from : "parent"}→${node.id}`,
      from: typeof from === "string" ? modV.chip(from, "wrap.run") : from,
      to: card(node),
      cls,
    })).filter((t) => t.from && !t.to.hidden));
    g.render(Docs.graphFrom(step.edges, { nodes: ["<module>"] }));
  });
})();

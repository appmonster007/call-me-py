/* Chapter 4: the fixpoint.  Example: info/examples/basics.py with its recorded solver trace.
 *
 * run.rounds[i] holds only what round i added (see rounds_json in info/build.py); this script
 * accumulates them so that step i shows the solver's whole state after round i.
 */
(function () {
  "use strict";
  const { el, panel, example, CodeView, GraphView, Stepper } = Docs;

  const ex = example("basics");
  Docs.seedPalette(ex);
  const rounds = ex.run.rounds;
  if (!rounds) throw new Error("basics needs trace=True in info/build.py");

  // Hand-written meaning of each round; the badges (+nodes, +edges) are computed from the trace.
  const NOTES = [
    { caption: "Round 0 analyses <code>&lt;module&gt;</code>. Its calls create three nodes. " +
        "<code>pick(True)</code> returns… nothing known yet, so <code>pick(True)(3)</code> calls nothing, for now.",
      marks: [{ line: 15, text: "pick(True)(3)", cls: "warn" }] },
    { caption: "Round 1 analyses all four. The <code>apply</code> nodes call <code>square</code> and " +
        "<code>double</code>, and <code>pick</code> learns its return value: {square, double}." },
    { caption: "Round 2 analyses <code>&lt;module&gt;</code> again, now knowing what round 1 learned: " +
        "<code>pick(True)</code> is {square, double}, so line 15 gains two edges.",
      marks: [{ line: 15, text: "pick(True)(3)", cls: "ok" }] },
    { caption: "Round 3 changes nothing: the lamp stays off and the solver stops. Analysing again " +
        "would reproduce the same state. That is a <strong>fixpoint</strong>." },
  ];

  const root = document.getElementById("rounds");
  const codeBox = el("div");
  const lamp = el("span", { class: "lamp" }, "changed");
  const counter = el("span", { class: "badge accent" });
  const delta = el("span", { class: "muted small" });
  const cards = el("div", { class: "cards" });
  const graphBox = el("div");
  root.append(el("div", { class: "panels code-wide" },
    panel("Program", codeBox),
    el("div", { class: "stack" },
      el("div", { class: "controls" }, counter, lamp, delta),
      panel("Nodes and what they return", cards),
      panel("Call graph (one box per node)", graphBox))));
  const code = new CodeView(codeBox, ex.source);
  const g = new GraphView(graphBox);
  const fnLines = (id) => {
    const f = ex.program.functions.find((x) => x.id === id.replace(/\[.*/, ""));
    return f ? Array.from({ length: f.end - f.line + 1 }, (_, i) => f.line + i) : [];
  };

  // step 0 = before the first round; step i+1 = after round i
  const steps = [{ round: -1, caption: "The solver starts with a single node, <code>&lt;module&gt;</code>, " +
    "and knows nothing else. A <em>round</em> analyses every known node, in order." }];
  rounds.forEach((r, i) => steps.push({ round: i, ...(NOTES[i] || { caption: `Round ${i}.` }) }));

  new Stepper(root, steps, (step) => {
    const done = rounds.slice(0, step.round + 1);
    const now = done[done.length - 1];
    const nodes = ["<module>", ...done.flatMap((r) => r.nodes).filter((n) => n !== "<module>")];
    const ret = {};
    done.forEach((r) => Object.entries(r.ret).forEach(([n, toks]) => (ret[n] = [...(ret[n] || []), ...toks])));
    const edges = done.flatMap((r) => r.edges.map((e) => ({ ...e, cls: r === now ? "new" : "" })));

    counter.textContent = step.round < 0 ? "before round 0" : `after round ${step.round}`;
    lamp.classList.toggle("on", !!(now && now.changed));
    delta.textContent = now
      ? `+${now.nodes.length} nodes · +${now.edges.length} edges · ` +
        `${Object.keys(now.ret).length ? "returns learned: " + Object.keys(now.ret).join(", ") : "no new returns"}`
      : "";

    const focus = now ? [...new Set([...now.edges.map((e) => e.site.line), ...Object.keys(now.ret).flatMap(fnLines)])] : [];
    code.set({ focus, dim: focus.length > 0, marks: step.marks || [] });

    Docs.reconcile(cards, nodes, {
      key: (id) => id,
      create: () => el("div"),
      update: (holder, id) => {
        const fresh = now && (now.nodes.includes(id) || id in now.ret);
        holder.replaceChildren(el("div", { class: `card ${fresh ? "active" : ""}`.trim() },
          el("div", { class: "card-title", style: { color: Docs.palette.color(id) } }, id),
          el("div", { class: "row" }, el("span", { class: "key" }, "returns"), Docs.chipSet(ret[id] || []))));
      },
    });

    const pairs = [];
    for (const e of edges) { // one arrow per pair; "new" wins if any site of it is new
      const old = pairs.find((x) => x.caller === e.caller && x.callee === e.callee);
      if (!old) pairs.push(e);
      else if (e.cls) old.cls = e.cls;
    }
    g.render(Docs.graphFrom(pairs, { nodes }));
  });
})();

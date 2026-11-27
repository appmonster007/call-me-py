/* Chapter 6: the heap.  Example: info/examples/heap.py
 *
 * Names on the left (the module env), the Box and its heap contents on the right, tethers from
 * every Box chip to the box. All values, heap contents and call targets come from the real run.
 */
(function () {
  "use strict";
  const { el, panel, example, CodeView, EnvView, GraphView, Stepper, Tethers } = Docs;

  const ex = example("heap");
  Docs.seedPalette(ex);
  const env = ex.node("<module>").env;
  const [boxLabel, stored] = Object.entries(ex.run.heap)[0] || [];
  if (!boxLabel) throw new Error(`${ex.file}: the analyzer stored nothing in the heap; update this chapter`);
  const boxTok = env.a.find((t) => t.kind === "Box");
  const read = ex.run.sites.find((s) => s.text === "a[0]()");

  const root = document.getElementById("heap");
  const codeBox = el("div");
  const diagram = el("div", { class: "diagram wide-left" });
  const graphBox = el("div");
  root.append(el("div", { class: "panels code-wide" },
    panel("Program", codeBox),
    el("div", { class: "stack" }, panel("Names → values · the heap", diagram), panel("Call graph", graphBox))));
  const code = new CodeView(codeBox, ex.source);
  const names = new EnvView(diagram, "<module>");
  const contents = el("span", { class: "chip-list" });
  const result = el("span", { class: "chip-list" });
  const box = el("div", { class: "env" },
    el("div", { class: "env-title" }, "heap ", Docs.tokenChip(boxTok)),
    el("div", { class: "env-row" }, el("span", null, "stored"), el("span", { class: "mapsto" }, "↦"), contents));
  const readRow = el("div", { class: "env-row hot" }, el("span", null, "a[0]"), el("span", { class: "mapsto" }, "↦"), result);
  names.root.append(readRow);
  diagram.append(box);
  const tethers = new Tethers(diagram);
  const g = new GraphView(graphBox);

  const steps = [
    {
      caption: "A list literal evaluates to its elements <em>plus</em> a <strong>Box</strong>: a token for " +
        "the list itself, named after where it was allocated (line 7, column 4).",
      line: 7, mark: "a = [g]", only: ["a"], hot: ["a"], heap: [], ties: { a: "" },
    },
    {
      caption: "<code>b = a</code> copies the value, Box included. Two names now hold the same Box: " +
        "an alias, for free.",
      line: 8, mark: "b = a", only: ["a", "b"], hot: ["b"], heap: [], ties: { a: "", b: "hot" },
    },
    {
      caption: "<code>b.append(f)</code> stores into the container: <code>heap[box] ∪= {f}</code>. The " +
        "value of <code>b</code> is unchanged; the box changed, and every alias sees the box.",
      line: 9, mark: "b.append(f)", only: ["a", "b"], hot: [], heap: stored, ties: { a: "", b: "hot" },
    },
    {
      caption: "<code>a[0]</code> reads an element: <code>deref</code> gives a's value plus everything " +
        "stored in its boxes. Both <code>g</code> and <code>f</code> are called.",
      line: 10, mark: "a[0]()", cls: "ok", only: ["a", "b"], hot: ["a"], heap: stored, ties: { a: "hot", b: "" },
      read: true,
    },
  ];

  new Stepper(root, steps, (step) => {
    code.set({ focus: [step.line], dim: true, marks: [{ line: step.line, text: step.mark, cls: step.cls }] });
    names.render(env, { only: step.only, hot: step.hot });
    names.root.append(readRow); // keep the read row last
    readRow.hidden = !step.read;
    Docs.chipSet(step.read ? read.targets.map((n) => ({ kind: "Closure", label: n, name: n })) : [], { into: result });
    Docs.chipSet(step.heap, { into: contents, flash: step.heap.map((t) => t.label) });
    tethers.draw(Object.entries(step.ties).map(([name, cls]) => ({
      key: name, from: names.chip(name, boxTok.label), to: box, cls,
    })).filter((t) => t.from));
    g.render(Docs.graphFrom(step.read ? read.targets.map((t) => ({ ...ex.edge("<module>", t), cls: "new" })) : [],
      { nodes: ["<module>"] }));
  });
})();

/* Chapter 7: objects and hidden calls.
 *
 * Scene "mro"    (info/examples/objects.py): class attribute tables in MRO order; a lookup walks
 *                them left to right. Tables, MRO, instance and edges come from the real run.
 * Scene "hidden" (info/examples/dunders.py): one step per implicit call site, found in the real
 *                run's `sites` by line and kind.
 */
(function () {
  "use strict";
  const { el, panel, example, CodeView, EnvView, GraphView, Stepper } = Docs;
  const lines = (f) => Array.from({ length: f.end - f.line + 1 }, (_, i) => f.line + i);

  /* ---------------------------------------------------------- scene 1: MRO */
  {
    const ex = example("objects");
    Docs.seedPalette(ex);
    const cls = (id) => ex.run.classes.find((c) => c.id === id);
    const start = cls("Puppy");
    const speakNode = ex.node("Animal.speak[self={Puppy()@15:0}]");
    const inst = speakNode.context.self[0];
    const bound = { kind: "BoundMethod", label: `Animal.speak@${inst.label}`, name: "Animal.speak", receiver: inst.label };

    /** The MRO walk for `attr`: classes passed over, and the one that has it. */
    const search = (attr) => {
      const passed = [];
      for (const c of start.mro) {
        if (attr in cls(c).attrs) return { passed, hit: c };
        passed.push(c);
      }
      throw new Error(`no ${attr} in ${start.mro}: update this chapter`);
    };

    const root = document.getElementById("mro");
    const codeBox = el("div");
    const strip = el("div", { class: "mro" });
    const graphBox = el("div");
    root.append(el("div", { class: "panels code-wide" },
      panel("Program", codeBox),
      el("div", { class: "stack" }, panel("Lookup order (MRO) from Puppy", strip), panel("Call graph", graphBox))));
    const code = new CodeView(codeBox, ex.source);
    const views = start.mro.map((c, i) => {
      if (i) strip.append(el("span", { class: "arrow", "aria-hidden": "true" }, "↓"));
      return [c, new EnvView(strip, el("span", { style: { color: Docs.palette.color(c) } }, `class ${c}`), { empty: "(nothing of its own)" })];
    });
    const g = new GraphView(graphBox);
    const edge = (c, d, cls = "") => ({ ...ex.edge(c, d), cls });

    const steps = [
      {
        caption: "A class body runs like a little function; what it binds becomes the class's " +
          "<strong>attribute table</strong>. The bases give the <strong>MRO</strong>, the order in which " +
          "lookups search: " + start.mro.join(" → ") + ".",
        code: { focus: [1, 8, 12], dim: true },
        edges: [],
      },
      {
        caption: "<code>Puppy()</code> calls the class. The result is an <strong>Instance</strong> token, " +
          `named by its class and allocation site: <code>${inst.label}</code>.`,
        code: { focus: [15], dim: true, marks: [{ line: 15, text: "Puppy()" }], notes: { 15: [Docs.tokenChip(inst)] } },
        edges: [],
      },
      {
        caption: "<code>.speak</code> is searched along the MRO: not in Puppy, not in Dog, found in Animal.",
        code: { focus: [15, 2], dim: true, marks: [{ line: 15, text: "speak" }] },
        lookup: "speak",
        edges: [],
      },
      {
        caption: "Read through an instance, a function becomes a <strong>bound method</strong>, with the " +
          `receiver pre-filled as <code>self</code>. Calling it creates <code>${speakNode.id}</code>.`,
        code: { focus: [15, 2, 3], dim: true, marks: [{ line: 15, text: "Puppy().speak()" }], notes: { 15: [Docs.tokenChip(bound)] } },
        lookup: "speak",
        edges: [edge("<module>", "Animal.speak", "new")],
      },
      {
        caption: "Inside, <code>self.sound()</code> searches self's class again, <em>from the start</em>: " +
          "Puppy, then Dog, which has <code>sound</code>. Dog's version wins. That's overriding.",
        code: { focus: [3, 9, 10], dim: true, marks: [{ line: 3, text: "self.sound()", cls: "ok" }] },
        lookup: "sound",
        edges: [edge("<module>", "Animal.speak"), edge("Animal.speak", "Dog.sound", "new")],
      },
    ];

    new Stepper(root, steps, (step) => {
      code.set(step.code);
      const found = step.lookup ? search(step.lookup) : null;
      for (const [c, view] of views) {
        const passed = found && found.passed.includes(c);
        const after = found && !passed && c !== found.hit;
        view.render(cls(c).attrs, { hot: found && c === found.hit ? [step.lookup] : [], active: !!found && c === found.hit });
        view.root.classList.toggle("miss", !!passed);
        view.root.style.opacity = after ? "0.35" : "";
      }
      g.render(Docs.graphFrom(step.edges, { nodes: ["<module>"] }));
    });
  }

  /* ---------------------------------------------------------- scene 2: hidden calls */
  {
    const ex = example("dunders");
    Docs.seedPalette(ex);
    const fn = (id) => ex.program.functions.find((f) => f.id === id);
    const site = (line, kind) => {
      const s = ex.run.sites.find((x) => x.line === line && x.kind === kind && x.caller === "<module>");
      if (!s) throw new Error(`${ex.file}: no ${kind} site on line ${line} any more; update this chapter`);
      return s;
    };

    const SYNTAX = [ // [line, site kind, text to box, what Python does]
      [21, "call", "Vec(1)", "Calling a class runs <code>__init__</code> on the new object (after <code>__new__</code>, if the class has one)."],
      [21, "op", "Vec(1) + Vec(2)", "<code>a + b</code> calls <code>a.__add__(b)</code>, or <code>b.__radd__(a)</code> when that is missing."],
      [22, "iter", "for x in v", "<code>for x in v</code> calls <code>__iter__</code>, then <code>__next__</code> on whatever it returns."],
      [24, "with", "with v", "<code>with v:</code> calls <code>__enter__</code> on the way in and <code>__exit__</code> on the way out."],
      [26, "attr", "v.size", "Reading <code>v.size</code> runs the property's getter: an attribute read is a call."],
    ];
    const steps = SYNTAX.map(([line, kind, text, caption]) => ({ s: site(line, kind), text, caption }));
    const implicit = ex.run.context_edges.filter((e) => !["call", "decorator"].includes(e.site.kind));
    const pairs = new Set(implicit.map((e) => `${e.caller.replace(/\[.*/, "")}→${e.callee.replace(/\[.*/, "")}`));
    steps.push({
      caption: `The whole graph: ${pairs.size} of its ${ex.run.edges.length} edges come from lines with no call ` +
        "syntax at all.",
      all: true,
    });

    const root = document.getElementById("hidden");
    const codeBox = el("div");
    const siteBox = el("div");
    const graphBox = el("div");
    root.append(el("div", { class: "panels code-wide" },
      panel("Program", codeBox),
      el("div", { class: "stack" }, panel("Call site", siteBox), panel("Call graph", graphBox))));
    const code = new CodeView(codeBox, ex.source);
    const g = new GraphView(graphBox);

    new Stepper(root, steps, (step, i) => {
      if (step.all) {
        code.set({});
        siteBox.replaceChildren(el("div", { class: "muted small" }, "every site, every kind"));
        g.render(Docs.graphFrom(ex.run.edges, { nodes: ["<module>"] }));
        return;
      }
      const { s } = step;
      code.set({
        focus: [s.line, ...s.targets.flatMap((t) => lines(fn(t)))],
        dim: true,
        marks: [{ line: s.line, text: step.text }],
      });
      siteBox.replaceChildren(el("div", { class: "card active" },
        el("div", { class: "row" }, el("span", { class: "muted" }, `line ${s.line}`), el("code", null, step.text),
          el("span", { class: "badge accent" }, s.kind)),
        el("div", { class: "row" }, el("span", { class: "key" }, "calls"),
          Docs.chipSet(s.targets.map((t) => ({ kind: "Closure", label: t, name: t }))))));
      const shown = new Map(); // callee → edge; this step's edges are "new"
      for (const x of steps.slice(0, i + 1)) {
        for (const t of x.s ? x.s.targets : []) shown.set(t, { ...ex.edge("<module>", t), cls: x === step ? "new" : "" });
      }
      g.render(Docs.graphFrom([...shown.values()], { nodes: ["<module>"] }));
    });
  }
})();

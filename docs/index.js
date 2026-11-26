/* Overview page: hero scene (program → functions → call graph), pipeline, contents, legend. */
(function () {
  "use strict";
  const { el, panel, example, CodeView, GraphView, Stepper } = Docs;

  const ex = example("basics");
  Docs.seedPalette(ex);

  /* ---------------------------------------------------------- hero */
  {
    const root = document.getElementById("hero");
    const codeBox = el("div");
    const graphBox = el("div");
    root.append(el("div", { class: "panels" }, panel("A program", codeBox), panel("Its call graph", graphBox)));
    const code = new CodeView(codeBox, ex.source);
    const g = new GraphView(graphBox);
    const functions = ex.program.functions.map((f) => f.id);
    const sites = [...new Set(ex.program.functions.flatMap((f) => f.sites.map((s) => s.line)))];

    new Stepper(root, [
      { caption: "A program: four functions and three calls at the bottom.", focus: [] },
      { caption: "Every function becomes a node. The module body counts as one too: <code>&lt;module&gt;</code>.",
        focus: ex.program.functions.filter((f) => f.kind === "def").map((f) => f.line), nodes: functions },
      { caption: "Every call that can happen becomes an arrow. Line 8 calls a parameter, so who does " +
          "<code>f(v)</code> call? <a href=\"chapters/01-who-is-f.html\">Chapter 1</a> starts there.",
        focus: sites, nodes: functions, edges: ex.run.edges },
    ], (step) => {
      code.set({ focus: step.focus, dim: step.focus.length > 0 });
      const graph = Docs.graphFrom(step.edges || [], { nodes: step.nodes || ["<module>"] });
      g.render(graph);
    });
  }

  /* ---------------------------------------------------------- pipeline: chapter links per stage */
  for (const card of document.querySelectorAll("[data-stage]")) {
    const links = Docs.CHAPTERS.filter((c) => c.stage.includes(card.dataset.stage))
      .map((c) => el("a", { href: `chapters/${c.slug}.html`, title: c.title }, `ch. ${Docs.CHAPTERS.indexOf(c) + 1}`));
    card.append(el("div", { class: "small" }, links.flatMap((a, k) => (k ? [" · ", a] : [a]))));
  }

  /* ---------------------------------------------------------- contents */
  document.getElementById("toc").append(...Docs.CHAPTERS.map((c) => el("li", null,
    el("a", { href: `chapters/${c.slug}.html` }, c.title), el("span", { class: "muted" }, ` · ${c.blurb}`))));

  /* ---------------------------------------------------------- legend */
  {
    const t = (kind, label, name = label, extra = {}) => Docs.tokenChip({ kind, label, name, ...extra });
    const row = (thing, meaning) => el("div", { class: "legend-row" }, el("div", null, thing), el("div", { class: "muted" }, meaning));
    const edge = (cls) => Docs.svg("svg", { width: 54, height: 12, class: "graph" },
      Docs.svg("path", { d: "M2,6 L52,6", class: `edge ${cls}` }));
    document.getElementById("legend").append(
      row([t("Closure", "square"), " ", t("Closure", "double")], "a user function (a Closure token); each function keeps its color everywhere"),
      row(t("Builtin", "map"), "a builtin or stdlib callable, modelled by a stub"),
      row([t("ClassObj", "<class Dog>", "Dog"), " ", t("Instance", "Dog()@15:0", "Dog")], "a class, and an instance named by its allocation site (line:col)"),
      row(t("Box", "<list@7:4>"), "a list / dict / set, named by its allocation site"),
      row(t("BoundMethod", "Dog.sound@Dog()@15:0", "Dog.sound", { receiver: "Dog()@15:0" }), "a method with its receiver (self) pre-filled"),
      row([edge("new")], "what this step added"),
      row([edge("warn")], "imprecision: an edge no real run produces"),
      row(el("span", { class: "lamp on" }, ""), "the solver's “changed” flag"),
    );
  }
})();

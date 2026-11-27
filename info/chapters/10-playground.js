/* Chapter 10: playground. Runs callme on Pyodide (Python in WebAssembly).
 *
 * The callme sources come from generated data (DOCS_DATA.sources), so the playground always runs
 * the code of the last docs build. Until Pyodide is ready, a preset shows its build-time result,
 * converted to the same shape as export.to_dict.
 */
(function () {
  "use strict";
  const { el, panel, GraphView } = Docs;

  const PYODIDE = "https://cdn.jsdelivr.net/pyodide/v0.28.3/full/"; // Python 3.13, like the project
  const DATA = window.DOCS_DATA;
  const presets = Object.entries(DATA.examples).map(([key, ex]) => [key, ex.source]);

  /* ---------------------------------------------------------- UI */
  const root = document.getElementById("playground");
  const editor = el("textarea", { class: "editor", spellcheck: "false", "aria-label": "Python source" });
  const preset = el("select", { "aria-label": "example" },
    presets.map(([key]) => el("option", { value: key }, `${key}.py`)));
  const maxCtx = el("input", { type: "number", min: "0", max: "64", value: "16", "aria-label": "max_contexts" });
  let view = "functions";
  const status = el("div", { class: "status" }, "Loading Python…");
  const graphBox = el("div");
  const sites = el("div", { class: "sites" });
  root.append(
    el("div", { class: "controls" }, "example", preset, "max_contexts", maxCtx, "graph",
      Docs.segmented([["functions", "functions"], ["contexts", "contexts"]], view, (v) => { view = v; draw(); })),
    el("div", { class: "panels" },
      panel("Python", editor),
      el("div", { class: "stack" }, status, panel("Call graph", graphBox), panel("Call sites → targets", sites))));
  const g = new GraphView(graphBox);

  editor.addEventListener("keydown", (e) => { // Tab indents instead of leaving the editor
    if (e.key !== "Tab") return;
    e.preventDefault();
    editor.setRangeText("    ", editor.selectionStart, editor.selectionEnd, "end");
    editor.dispatchEvent(new Event("input"));
  });

  /* ---------------------------------------------------------- drawing a to_dict result */
  let result = null;

  function draw() {
    if (!result) return;
    if (view === "functions") {
      const unreachable = result.functions.filter((f) => !f.reachable).map((f) => f.id);
      const graph = Docs.graphFrom(result.edges, { nodes: ["<module>"] });
      unreachable.forEach((id) => graph.nodes.push({ id, label: id, cls: "dim", title: `${id} (never called)` }));
      g.render(graph);
    } else {
      const pairs = [];
      for (const e of result.context_edges) {
        if (!pairs.some((p) => p.caller === e.caller && p.callee === e.callee)) pairs.push(e);
      }
      const graph = Docs.graphFrom(pairs, { nodes: result.contexts.map((c) => c.id) });
      graph.nodes.forEach((n) => (n.name = n.id.replace(/\[.*/, "")));
      g.render(graph);
    }
    sites.replaceChildren(...result.sites.map((s) => el("div", { class: "row" },
      el("span", { class: "muted" }, `L${s.line}`), s.text, el("span", { class: "badge" }, s.kind), "→",
      s.targets.length ? Docs.chipSet(s.targets.map((t) => ({ kind: "Closure", label: t, name: t })))
        : el("span", { class: "muted" }, "nothing"))));
  }

  /** A build-time run (info/build.py) in the shape of export.to_dict. */
  function recorded(key) {
    const ex = DATA.examples[key];
    const run = ex.runs["16"] || Object.values(ex.runs)[0];
    const reachable = new Set(run.nodes.map((n) => n.function));
    return {
      functions: ex.program.functions.map((f) => ({ id: f.id, reachable: reachable.has(f.id) })),
      sites: run.sites, edges: run.edges, context_edges: run.context_edges,
      contexts: run.nodes.map((n) => ({ id: n.id, function: n.function })),
    };
  }

  /* ---------------------------------------------------------- Pyodide */
  let py = null;

  function loadScript(src) {
    return new Promise((resolve, reject) => {
      const s = el("script", { src });
      s.onload = resolve;
      s.onerror = () => reject(new Error(`could not load ${src}`));
      document.head.append(s);
    });
  }

  async function boot() {
    await loadScript(`${PYODIDE}pyodide.js`);
    const pyodide = await window.loadPyodide({ indexURL: PYODIDE });
    for (const [path, text] of Object.entries(DATA.sources)) {
      pyodide.FS.mkdirTree(`/lib/${path.split("/").slice(0, -1).join("/")}`);
      pyodide.FS.writeFile(`/lib/${path}`, text);
    }
    pyodide.runPython("import sys; sys.path.insert(0, '/lib')");
    return pyodide;
  }

  function analyze() {
    if (!py) return;
    const t0 = performance.now();
    py.globals.set("SOURCE", editor.value);
    py.globals.set("MAX_CONTEXTS", Number(maxCtx.value) || 0);
    try {
      result = JSON.parse(py.runPython(`
import json
from callme import analyze_source, to_dict
json.dumps(to_dict(analyze_source(SOURCE, "playground.py", "short", max_contexts=MAX_CONTEXTS)))
`));
      status.className = "status";
      status.textContent = `Live analysis · ${Math.round(performance.now() - t0)} ms`;
      draw();
    } catch (err) {
      const lines = String(err.message || err).trim().split("\n");
      status.className = "status error";
      status.textContent = lines[lines.length - 1]; // the Python exception, without the traceback
    }
  }

  /* ---------------------------------------------------------- wiring */
  let timer = 0;
  const later = () => { clearTimeout(timer); timer = setTimeout(analyze, 350); };
  editor.addEventListener("input", later);
  maxCtx.addEventListener("input", later);
  preset.addEventListener("change", () => {
    Docs.seedPalette(DATA.examples[preset.value]); // same colors as the chapter that uses it
    editor.value = DATA.examples[preset.value].source;
    if (py) analyze();
    else { result = recorded(preset.value); draw(); }
  });

  Docs.seedPalette(DATA.examples[presets[0][0]]);
  editor.value = presets[0][1];
  result = recorded(presets[0][0]);
  draw();
  status.textContent = "Showing the build-time result · loading Python (first time ≈10 MB)…";
  boot().then((pyodide) => {
    py = pyodide;
    analyze();
  }).catch((err) => {
    status.className = "status error";
    status.textContent = `Python could not be loaded (${err.message}). Are you offline? ` +
      "The build-time results of the presets still work.";
  });
})();

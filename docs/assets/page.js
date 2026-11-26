/* page.js: what every page has around its content.
 *
 *   Docs.CHAPTERS   the table of contents (add a chapter here: slug = file name in chapters/)
 *   Docs.STAGES     the analyzer pipeline shown in the header; a chapter highlights its stage(s)
 *   header, prev/next pager, theme toggle (built on DOMContentLoaded from <body data-chapter>)
 *   <code-ref sym="callme/transfer.py::Interp.apply">   link to real code; the snippet comes from
 *                    generated data, and docs/build.py fails if the symbol no longer exists
 *   <code-snippet sym="callme/stubs.py::_map">         the same real code, shown inline
 *
 * Load order on a page: core.js, views.js, page.js in <head>; generated/data.js and the chapter
 * script at the end of <body>.
 */
(function (Docs) {
  "use strict";
  const { el } = Docs;

  Docs.CHAPTERS = [
    { slug: "01-who-is-f", title: "Who does f() call?", stage: ["transfer"],
      blurb: "Follow values, not names: abstract values are sets of tokens." },
    { slug: "02-mapping", title: "Mapping the program", stage: ["extract"],
      blurb: "Step 1: every function gets an id, a scope and a list of call sites." },
    { slug: "03-contexts", title: "One function, many lives", stage: ["transfer", "solver"],
      blurb: "Contexts keep separate calls of the same function apart." },
    { slug: "04-fixpoint", title: "Ask again until nothing changes", stage: ["solver"],
      blurb: "The solver repeats rounds until a whole round learns nothing new." },
    { slug: "05-closures", title: "Functions that remember", stage: ["transfer"],
      blurb: "A function token is tethered to the call that created it." },
    { slug: "06-heap", title: "Values that wait", stage: ["transfer"],
      blurb: "Containers are boxes shared by every alias." },
    { slug: "07-objects", title: "Objects and hidden calls", stage: ["transfer"],
      blurb: "Classes, the MRO, bound methods, and the calls Python makes for you." },
    { slug: "08-stubs", title: "Teaching it the builtins", stage: ["transfer"],
      blurb: "Stubs replay what map, sorted and partial do with their arguments." },
    { slug: "09-output", title: "From state to graph", stage: ["callgraph", "export"],
      blurb: "Collapse contexts, print JSON / DOT, and check against ground truth." },
    { slug: "10-playground", title: "Playground", stage: [],
      blurb: "Write Python, watch the real analyzer build its graph." },
  ];

  Docs.STAGES = [
    { id: "extract", file: "callme/extract.py", what: "finds every function, scope and call site" },
    { id: "solver", file: "callme/solver.py", what: "re-analyses all (function, context) nodes until stable" },
    { id: "transfer", file: "callme/transfer.py", what: "evaluates one function body on abstract values (stubs.py: builtins)" },
    { id: "callgraph", file: "callme/callgraph.py", what: "collapses the solver state into a call graph" },
    { id: "export", file: "callme/export.py", what: "writes JSON or Graphviz DOT" },
  ];

  /** The pipeline as a row of stages; solver ⟲ transfer is drawn as a loop. */
  Docs.pipeline = (current = [], root = "..") => {
    const first = (id) => Docs.CHAPTERS.find((c) => c.stage.includes(id));
    const stage = (id) => {
      const s = Docs.STAGES.find((x) => x.id === id);
      const ch = first(id);
      return el(ch ? "a" : "span", {
        class: `stage ${current.includes(id) ? "current" : ""}`,
        href: ch ? `${root}/chapters/${ch.slug}.html` : null,
        title: `${s.file}: ${s.what}`,
      }, id);
    };
    const arrow = () => el("span", { class: "arrow", "aria-hidden": "true" }, "→");
    return el("nav", { class: "pipeline", "aria-label": "analyzer pipeline" },
      stage("extract"), arrow(), el("span", { class: "loop" }, stage("solver"), stage("transfer")),
      arrow(), stage("callgraph"), arrow(), stage("export"));
  };

  /* ---------------------------------------------------------- errors */

  // A broken scene says so on the page (usually: the analyzer changed, rebuild and update the text).
  window.addEventListener("error", (e) => {
    const banner = el("div", { class: "error-banner", role: "alert" },
      el("strong", null, "This page hit an error: "), e.message,
      el("div", { class: "small" }, "If the analyzer changed, run python docs/build.py and update the chapter."));
    (document.querySelector("main") || document.body).prepend(banner);
  });

  /**
   * page.html?selftest walks every scene through all its steps, forwards and back, and reports
   * at the top of the page. Use it after changing a chapter or the analyzer.
   */
  function selftest() {
    const failures = [];
    window.addEventListener("error", (e) => failures.push(e.message));
    let steps = 0;
    for (const s of Docs.steppers) {
      const order = [...s.steps.keys(), ...[...s.steps.keys()].reverse()];
      for (const i of order) {
        try { s.go(i); steps++; } catch (err) { failures.push(`#${s.root.id} step ${i + 1}: ${err.message}`); }
      }
    }
    const report = () => {
      const ok = !failures.length;
      document.getElementById("selftest")?.remove();
      document.querySelector("main").prepend(el("div", { class: ok ? "selftest ok" : "error-banner", id: "selftest" },
        ok ? `selftest passed: ${Docs.steppers.length} scene(s), ${steps} steps` : `selftest failed: ${failures.join(" · ")}`));
    };
    report();
    setTimeout(report, 700); // late redraws (tethers, tweens) may still throw
  }

  /* ---------------------------------------------------------- theme */

  const THEME_KEY = "callme-docs-theme";
  const storage = {
    get: () => { try { return localStorage.getItem(THEME_KEY); } catch { return null; } },
    set: (v) => { try { localStorage.setItem(THEME_KEY, v); } catch { /* private mode: fine */ } },
  };
  const applyTheme = (t) => (document.documentElement.dataset.theme = t);
  // ?theme=light|dark in the address wins, then the reader's last choice; dark by default
  applyTheme(new URLSearchParams(location.search).get("theme") || storage.get() || "dark"); // in <head>: before first paint

  /* ---------------------------------------------------------- page chrome */

  function header(chapter, root) {
    const toggle = el("button", {
      class: "icon-btn", title: "Toggle light / dark",
      onclick: () => {
        const t = document.documentElement.dataset.theme === "light" ? "dark" : "light";
        applyTheme(t);
        storage.set(t);
      },
    }, "◐");
    return el("header", { class: "topbar" },
      el("a", { class: "home", href: `${root}/index.html` }, "call-me-py ", el("span", null, "explained")),
      el("span", { class: "spacer" }),
      Docs.pipeline(chapter ? chapter.stage : [], root),
      toggle);
  }

  function chapterHead(i) {
    return el("div", { class: "chapter-head prose" },
      el("div", { class: "kicker" }, `Chapter ${i + 1}`));
  }

  function pager(i, root) {
    const link = (c, cls, label) => c && el("a", { class: cls, href: `${root}/chapters/${c.slug}.html` },
      el("small", null, label), c.title);
    const prev = i > 0 ? Docs.CHAPTERS[i - 1] : null;
    const next = Docs.CHAPTERS[i + 1];
    return el("nav", { class: "pager prose" },
      prev ? link(prev, "prev", "← Previous") : el("a", { class: "prev", href: `${root}/index.html` }, el("small", null, "← Overview"), "The big picture"),
      link(next, "next", "Next →"));
  }

  document.addEventListener("DOMContentLoaded", () => {
    const slug = document.body.dataset.chapter;
    const root = document.body.dataset.root || ".";
    const i = Docs.CHAPTERS.findIndex((c) => c.slug === slug);
    const chapter = Docs.CHAPTERS[i];
    document.body.prepend(header(chapter, root));
    const main = document.querySelector("main");
    if (chapter && main) {
      main.prepend(chapterHead(i));
      main.append(pager(i, root));
      document.title = `${chapter.title} · call-me-py explained`;
    }
    customElements.define("code-ref", CodeRef); // after data.js has loaded
    customElements.define("code-snippet", CodeSnippet);
    if (new URLSearchParams(location.search).has("selftest")) selftest();
  });

  /* ---------------------------------------------------------- code refs */

  let dialog = null;

  function openRef(sym) {
    const ref = window.DOCS_DATA.refs[sym];
    const repo = window.DOCS_DATA.repo;
    if (!dialog) {
      dialog = el("dialog", { class: "ref-dialog" });
      dialog.addEventListener("click", (e) => e.target === dialog && dialog.close()); // backdrop
      document.body.append(dialog);
    }
    const where = ref.end ? `${ref.file}:${ref.line}` : ref.file;
    const range = ref.end && ref.end !== ref.line ? `${ref.file}:${ref.line}–${ref.end}` : where;
    const gh = `${repo.url}/blob/${repo.branch}/${ref.file}${ref.end ? `#L${ref.line}-L${ref.end}` : ""}`;
    const copy = el("button", {
      class: "icon-btn", title: "Copy path:line (paste into your editor's open-file box)",
      onclick: () => navigator.clipboard?.writeText(where).then(() => (copy.textContent = "copied")),
    }, "copy");
    const body = el("div", { class: "body" });
    dialog.replaceChildren(
      el("header", null, el("span", { class: "path" }, range), copy,
        el("a", { href: gh, target: "_blank", rel: "noopener" }, "GitHub ↗"),
        el("button", { class: "icon-btn", onclick: () => dialog.close(), "aria-label": "Close" }, "✕")),
      body);
    if (ref.code) {
      new Docs.CodeView(body, ref.code, { firstLine: ref.line });
      if (ref.truncated) body.append(el("p", { class: "muted small" }, "… (cut here, see the file for the rest)"));
    } else body.append(el("p", { class: "muted" }, "Whole file: open it in your editor or on GitHub."));
    dialog.showModal();
  }

  /** Real source of a symbol, from generated data (scripts may read it too: Docs.snippet). */
  Docs.snippet = (sym) => {
    const ref = window.DOCS_DATA.refs[sym];
    if (!ref || !ref.code) throw new Error(`no snippet for ${sym}: is it named in some page's sym attribute? run python docs/build.py`);
    return ref;
  };

  class CodeSnippet extends HTMLElement {
    connectedCallback() {
      if (this.__done) return;
      this.__done = true;
      const sym = this.getAttribute("sym");
      const ref = Docs.snippet(sym);
      this.replaceChildren();
      new Docs.CodeView(this, ref.code, { firstLine: ref.line });
      this.append(el("div", { class: "snippet-ref" }, el("code-ref", { sym })));
    }
  }

  class CodeRef extends HTMLElement {
    connectedCallback() {
      if (this.__done) return;
      this.__done = true;
      const sym = this.getAttribute("sym");
      const [path, qual] = sym.split("::");
      const label = this.textContent.trim() || qual;
      const ref = window.DOCS_DATA && window.DOCS_DATA.refs[sym];
      const btn = el("button", { class: `ref-btn ${ref ? "" : "missing"}`.trim(), type: "button",
        title: ref ? `${ref.file}:${ref.line}` : "not in generated data: run python docs/build.py" },
        el("span", { class: "ref-file" }, path.split("/").pop()),
        label ? el("span", { class: "ref-sym" }, label) : null);
      if (ref) btn.addEventListener("click", () => openRef(sym));
      this.replaceChildren(btn);
    }
  }
})((window.Docs = window.Docs || {}));

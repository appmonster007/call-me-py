# call-me-py, explained

Interactive explanations of how the analyzer works: one idea per chapter, shown with scenes you
step through, each tied to the files and functions that implement it.

**Open [`index.html`](index.html) in a browser.** No server and no build step are needed. Only
the playground (chapter 10) needs internet, to download Pyodide.

| Address option | Effect |
|---|---|
| `#<scene id>=3` | open a scene at step 3, e.g. `04-fixpoint.html#rounds=3` |
| `?theme=light` | light theme (the ◐ button does the same and remembers it) |
| `?still` | no animation (screenshots, printing) |
| `?selftest` | step every scene forwards and back, then report at the top of the page |

## Keeping it true

The pages never hard-code analyzer results. `info/build.py` runs the real analyzer on the
programs in `info/examples/` and writes everything the scenes show to `generated/data.js`:
contexts, environments, edges, the solver's rounds, class tables, the heap. It also stores the
source of every code reference and the `callme` package for the playground.

```sh
python info/build.py           # after changing callme/ or info/
python info/build.py --check   # what tests/test_info.py runs
```

`tests/test_info.py` fails when `data.js` is stale, or when a page references a function that no
longer exists. After rebuilding, open the affected chapters with `?selftest`. A scene whose story
the analyzer no longer produces throws an error instead of drawing something false (see
`Docs.example(...).edge/node`). Then update that chapter's captions.

## Layout

```
index.html, index.js      overview: hero, pipeline, contents, legend
chapters/NN-slug.html/.js one page per idea: prose in the HTML, scenes in the JS
examples/*.py             the programs the chapters animate
build.py                  generator / checker (EXAMPLES and CASES registries at the top)
generated/data.js         window.DOCS_DATA (committed; never edit by hand)
assets/core.js            el / svg helpers, keyed reconcile, Stepper, tween
assets/views.js           palette, CodeView, tokenChip, EnvView, GraphView, Tethers
assets/page.js            chapter list, header, pager, theme, <code-ref>, <code-snippet>, ?selftest
assets/style.css          design tokens (both themes) and every component
```

## Adding a chapter

1. Add `{ slug, title, stage, blurb }` to `Docs.CHAPTERS` in `assets/page.js`. The order there is
   the reading order, and `stage` lights up the pipeline in the header.
2. Copy an existing chapter's HTML. Keep the `<head>` scripts and the
   `<body data-chapter="…" data-root="..">`. Write the prose, then one `<div class="scene wide" id="…">`
   per scene, then an "In the code" list.
3. If it needs a new program, add it to `examples/` and register it in `EXAMPLES` in `build.py`.
   Use `max_contexts=(…)` to get several runs, `trace=True` for the solver rounds.
4. Write `chapters/NN-slug.js`. The usual shape:

   ```js
   const ex = Docs.example("basics");          // generated data of examples/basics.py
   Docs.seedPalette(ex);                       // function colors in definition order
   const code = new Docs.CodeView(box, ex.source);
   new Docs.Stepper(sceneEl, [{ caption: "…", … }, …], (step) => {
     code.set({ focus: [8], marks: [{ line: 8, text: "f(v)", cls: "ok" }] });
     graph.render(Docs.graphFrom([ex.edge("apply", "square")]));
   });
   ```

5. Run `python info/build.py` and open the page with `?selftest`.

## Referencing code

```html
<code-ref sym="callme/analysis/interp/calls.py::CallLayer.apply"></code-ref>  a button that opens the real source
<code-ref sym="examples/cases/README.md">case format</code-ref>               a whole file, with a label
<code-snippet sym="callme/stubs/itertools.py::_starmap"></code-snippet>       the source, inline
```

`sym` is `path::Dotted.name`: a function, class, method, or module-level assignment. A method
of `Interp` is named by the layer class that defines it, such as `CallLayer.apply`.
`build.py` collects every `sym="…"` in `info/`, so a rename in `callme/` makes the test fail and
names the page to fix. Scripts can read a collected symbol's source with `Docs.snippet(sym)`.

## Style

- One idea per chapter, at most about 300 words of prose. The scene makes the argument; captions
  say what to look at.
- Puzzle first: open with a concrete question, and name the concept after the reader has seen it.
- Meaning is fixed across pages:
  - a function keeps its color everywhere
  - accent (blue) = what this step added
  - red = imprecision
  - yellow = "pause and ponder"
  - token shape = token kind
- Elements are keyed, so they move and fade between steps instead of being redrawn. Give
  reconciled items stable keys.
- A "what if" that is not analyzer output must say so in its caption (see chapter 5, last step).

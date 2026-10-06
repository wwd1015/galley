// Galley app shell: sidebar, progress stepper and the Papers, Convert, Build
// and Verify pages, drawn from the state snapshot. The Review page is handed
// to galley-review.js. Every button sends an action through `store-action`.
(function () {
  "use strict";

  const A = { state: null, signatures: {}, messageSerial: 0, lastTab: null };

  const ICONS = {
    papers: "M4 5a2 2 0 0 1 2-2h8l6 6v10a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2zM14 3v6h6M8 13h8M8 17h5",
    convert: "M7 4h7l5 5v3M14 4v5h5M7 4a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h4M15 17h6m0 0-2.5-2.5M21 17l-2.5 2.5",
    build: "M4 7l8-4 8 4-8 4zM4 7v10l8 4M20 7v10l-8 4M12 11v10",
    verify: "M12 3l7 3v6c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6zM9 12l2.2 2.2L15.5 10",
    review: "M4 5h16v11H9l-5 4zM8 9h8M8 12h5",
    check: "M5 12.5l4.5 4.5L19 7.5",
    cross: "M6 6l12 12M18 6L6 18",
    upload: "M12 16V5m0 0-4 4m4-4 4 4M5 15v3a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-3",
    arrow: "M5 12h14m0 0-5-5m5 5-5 5",
    plus: "M12 5v14M5 12h14",
    pulse: "M3 12h4l2.5-6 4 12 2.5-6H21",
    external: "M14 5h5v5M19 5l-8 8M11 7H7a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2v-4",
    folder: "M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z",
  };
  const TABS = [
    ["papers", "Papers", "Your workspace"],
    ["convert", "Convert", "Bring a paper in"],
    ["build", "Build", "Render the PDF"],
    ["verify", "Verify", "Check nothing was lost"],
    ["review", "Review", "Comment on a pull request"],
  ];

  // Actions go to the server in order, one batch at a time; the next batch waits
  // for the server to acknowledge the last, so a quick second click (or a save
  // followed at once by a comment) can never replace the first.
  const Q = { pending: [], inflight: null, sentAt: 0 };

  function flush() {
    if (!Q.pending.length || !window.dash_clientside || !window.dash_clientside.set_props) return;
    if (Q.inflight && Date.now() - Q.sentAt < 30000) return;
    Q.inflight = Date.now() + ":" + Math.random();
    Q.sentAt = Date.now();
    const actions = Q.pending.splice(0);
    window.dash_clientside.set_props("store-action", {
      data: { type: "batch", nonce: Q.inflight, actions: actions },
    });
  }

  function send(action) {
    Q.pending.push(action);
    setTimeout(flush, 0);
  }

  function acknowledge(nonce) {
    if (Q.inflight && nonce === Q.inflight) {
      Q.inflight = null;
      flush();
    }
  }

  window.galleySend = send;

  // ---- small builders ----------------------------------------------------

  function el(tag, attrs, children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key === "html") node.innerHTML = value;
      else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else if (value !== null && value !== undefined && value !== false) node.setAttribute(key, value);
    }
    for (const child of children || []) {
      if (child) node.append(typeof child === "string" ? document.createTextNode(child) : child);
    }
    return node;
  }

  function icon(name, size) {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("width", size || 18);
    svg.setAttribute("height", size || 18);
    svg.setAttribute("class", "wb-icon");
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", ICONS[name]);
    svg.append(path);
    return svg;
  }

  function badge(text, kind) {
    return el("span", { class: "wb-badge " + (kind || "neutral"), text: text });
  }

  function statusBadge(status) {
    if (status === "pass" || status === true) return badge("Passed", "ok");
    if (status === "warn") return badge("Passed with notes", "ok");
    if (status === "fail" || status === false) return badge("Failed", "bad");
    if (status === "advisory") return badge("Advisory", "warn");
    if (status === "skipped") return badge("Skipped", "neutral");
    return badge("Not run", "neutral");
  }

  function button(label, kind, onclick, options) {
    const opts = options || {};
    return el("button", { class: "wb-btn " + (kind || ""), disabled: opts.disabled, onclick: onclick, title: opts.title }, [
      opts.icon ? icon(opts.icon, 16) : null,
      el("span", { text: label }),
    ]);
  }

  function card(title, children, options) {
    const opts = options || {};
    return el("section", { class: "wb-card " + (opts.class || "") }, [
      title
        ? el("header", { class: "wb-card-head" }, [
            el("h3", { text: title }),
            opts.aside || null,
          ])
        : null,
      el("div", { class: "wb-card-body" }, children),
    ]);
  }

  function stat(label, value, kind) {
    return el("div", { class: "wb-stat " + (kind || "") }, [
      el("div", { class: "wb-stat-value", text: String(value) }),
      el("div", { class: "wb-stat-label", text: label }),
    ]);
  }

  function table(headers, rows) {
    return el("div", { class: "wb-table-wrap" }, [
      el("table", { class: "wb-table" }, [
        el("thead", {}, [el("tr", {}, headers.map((h) => el("th", { text: h })))]),
        el(
          "tbody",
          {},
          rows.map((row) =>
            el(
              "tr",
              {},
              row.map((cell) =>
                cell instanceof Node ? el("td", {}, [cell]) : el("td", { text: cell === null || cell === undefined ? "" : String(cell) })
              )
            )
          )
        ),
      ]),
    ]);
  }

  function field(id, label, placeholder, value, hint) {
    const input = el("input", { id: id, placeholder: placeholder || "", class: "wb-input" });
    if (value) input.value = value;
    return el("label", { class: "wb-field" }, [
      el("span", { class: "wb-label", text: label }),
      input,
      hint ? el("span", { class: "wb-hint", text: hint }) : null,
    ]);
  }

  function valueOf(id) {
    const node = document.getElementById(id);
    return node ? node.value.trim() : "";
  }

  function pageHead(title, subtitle, actions) {
    return el("div", { class: "wb-pagehead" }, [
      el("div", {}, [el("h1", { text: title }), subtitle ? el("p", { text: subtitle }) : null]),
      el("div", { class: "wb-actions" }, actions || []),
    ]);
  }

  function empty(iconName, title, text, action) {
    return el("div", { class: "wb-empty" }, [
      el("div", { class: "wb-empty-icon" }, [icon(iconName, 26)]),
      el("h3", { text: title }),
      el("p", { text: text }),
      action || null,
    ]);
  }

  // Re-draw a panel only when what it shows has changed, so typing is never lost.
  function draw(id, signature, build) {
    const key = JSON.stringify(signature);
    if (A.signatures[id] === key) return;
    const host = document.getElementById(id);
    if (!host) return;
    if (host.contains(document.activeElement) && A.signatures[id] !== undefined &&
        ["INPUT", "TEXTAREA"].includes(document.activeElement.tagName)) return;
    A.signatures[id] = key;
    host.replaceChildren(...build().filter(Boolean));
  }

  function busy(state) {
    return state.job.state === "running";
  }

  function currentPaper(state) {
    return state.papers.find((paper) => paper.slug === state.paper) || null;
  }

  // ---- sidebar -----------------------------------------------------------

  function stepState(id, paper) {
    if (!paper) return "";
    if (id === "convert") return paper.source ? "ok" : "";
    if (id === "build") return paper.build_ok === true ? "ok" : paper.build_ok === false ? "bad" : "";
    if (id === "verify") return paper.verify === "pass" ? "ok" : paper.verify === "fail" ? "bad" : "";
    return "";
  }

  function drawNav(state) {
    draw("wb-nav", [state.tab, state.papers, state.paper, state.workspace, !!state.demo], function () {
      const paper = currentPaper(state);
      const select = el("select", {
        class: "wb-select",
        title: "Paper",
        onchange: (event) => event.target.value && send({ type: "wb_select_paper", slug: event.target.value }),
      });
      select.append(el("option", { value: "", text: state.papers.length ? "Select a paper…" : "No papers yet" }));
      for (const item of state.papers) select.append(el("option", { value: item.slug, text: item.slug }));
      select.value = state.paper || "";
      return [
        el("div", { class: "wb-brand" }, [
          el("span", { class: "wb-logo", text: "G" }),
          el("div", {}, [el("b", { text: "Galley" }), el("small", { text: state.demo ? "Demo workspace" : "Whitepaper build system" })]),
        ]),
        el("div", { class: "wb-navgroup" }, [
          el("span", { class: "wb-navlabel", text: "Current paper" }),
          select,
        ]),
        el("span", { class: "wb-navlabel pad", text: "Workflow" }),
        el(
          "nav",
          {},
          TABS.map(([id, label, hint], index) => {
            const mark = stepState(id, paper);
            return el(
              "button",
              {
                class: "wb-navitem" + (state.tab === id ? " active" : ""),
                onclick: () => send({ type: "wb_tab", tab: id }),
              },
              [
                el("span", { class: "wb-navicon" }, [icon(id)]),
                el("span", { class: "wb-navtext" }, [
                  el("b", { text: (index ? index + ". " : "") + label }),
                  el("small", { text: hint }),
                ]),
                mark ? el("span", { class: "wb-dot " + mark, title: mark === "ok" ? "Done" : "Failed" }, [icon(mark === "ok" ? "check" : "cross", 12)]) : null,
              ]
            );
          })
        ),
        el("div", { class: "wb-navfoot" }, [
          el("div", { class: "wb-navfoot-row", title: state.workspace }, [icon("folder", 14), el("span", { text: state.workspace })]),
          el("div", { class: "wb-navfoot-row" }, [el("span", { class: "wb-live" }), el("span", { text: "Running on this computer only" })]),
        ]),
      ];
    });
  }

  // ---- progress stepper --------------------------------------------------

  function drawJob(state) {
    const job = state.job;
    const key = job.kind + ":" + job.started;
    if (job.state === "running") A.jobOpen = key; // a new run always shows its steps
    const open = A.jobOpen === key;
    const hidden = A.jobHidden === key && job.state !== "running";
    draw("wb-job", [job.kind, job.state, job.steps, job.error, job.summary, open, hidden, job.state === "running" ? Math.floor(job.elapsed) : job.elapsed], function () {
      if (job.state === "idle" || hidden) return [];
      const steps = job.steps.map((step, index) => {
        const last = index === job.steps.length - 1;
        const status = job.state === "running" && last ? "current" : job.state === "failed" && last ? "bad" : "ok";
        return el("li", { class: status }, [
          el("span", { class: "wb-stepmark" }, [
            status === "current" ? el("span", { class: "wb-spinner" }) : icon(status === "bad" ? "cross" : "check", 13),
          ]),
          el("span", { class: "wb-steplabel", text: step }),
        ]);
      });
      const kind = job.state === "running" ? "info" : job.state === "done" ? "ok" : "bad";
      const label = job.state === "running" ? "Running" : job.state === "done" ? "Done" : "Failed";
      const tone = kind === "ok" && /FAIL/.test(job.summary) ? "warn" : kind;
      const redraw = () => drawJob(A.state);
      return [
        el("div", { class: "wb-jobhead" }, [
          el("b", { text: job.title }),
          badge(label, kind),
          el("span", { class: "wb-muted", text: job.elapsed + " s" }),
          job.summary && !open ? el("span", { class: "wb-jobline " + tone, text: job.summary }) : null,
          el("span", { style: "flex:1" }),
          job.state !== "running"
            ? el("button", { class: "gl-link", text: open ? "Hide steps" : "Show " + job.steps.length + " steps", onclick: function () { A.jobOpen = open ? null : key; redraw(); } })
            : null,
          job.state !== "running"
            ? el("button", { class: "gl-link", text: "Dismiss", onclick: function () { A.jobHidden = key; redraw(); } })
            : null,
        ]),
        open ? el("ol", { class: "wb-stepper" }, steps) : null,
        job.state === "running" ? el("div", { class: "wb-progress" }, [el("span")]) : null,
        job.summary && open ? el("div", { class: "wb-jobsummary " + tone, text: job.summary }) : null,
        job.error ? el("pre", { class: "wb-error", text: job.error }) : null,
      ];
    });
  }

  // ---- demo walkthrough --------------------------------------------------

  function demoBox(state) {
    const demo = state.demo;
    const converted = state.papers.some((paper) => paper.slug === demo.slug);
    const open = (tab) => function () {
      send({ type: "wb_select_paper", slug: demo.slug });
      send({ type: "wb_tab", tab: tab });
    };
    const step = (number, title, text, label, onclick, enabled) =>
      el("li", { class: enabled ? "" : "locked" }, [
        el("span", { class: "wb-tour-num", text: String(number) }),
        el("div", { class: "wb-tour-text" }, [el("b", { text: title }), el("p", { html: text })]),
        button(label, enabled && number === (converted ? 2 : 1) ? "primary" : "", onclick, { disabled: !enabled, icon: "arrow" }),
      ]);
    return el("section", { class: "wb-card wb-tour" }, [
      el("header", { class: "wb-card-head" }, [
        el("h3", { text: "Guided demo" }),
        badge("Nothing leaves this computer", "info"),
      ]),
      el("ol", {}, [
        step(1, "Convert the sample whitepaper",
          "A Word file with a styled callout, a picture, a table, a native chart and citations. Verify <b>fails on purpose</b>: one citation cannot be matched, so its year is missing from the PDF.",
          converted ? "Open the report" : "Convert the sample",
          function () {
            send({ type: "wb_tab", tab: "convert" });
            if (converted) send({ type: "wb_select_paper", slug: demo.slug });
            else send({ type: "wb_convert", source: demo.sample, slug: demo.slug, bib: demo.bib, source_url: "" });
          },
          !busy(state)),
        step(2, "Build the PDF", "See the data manifest, the build checks and the typeset PDF, with the chart redrawn from the data embedded in the Word file.", "Open Build", open("build"), converted),
        step(3, "Verify against the original", "Every finding, with the original and the PDF side by side. Accepting the missing number needs a reason and your name.", "Open Verify", open("verify"), converted),
        step(4, "Review with a teammate", "A pull request is simulated here. Alice has left two comments and answers yours within seconds. Use the blue <b>+</b> beside a line number to comment.", "Open Review", open("review"), converted),
      ]),
    ]);
  }

  // ---- Papers ------------------------------------------------------------

  function paperCard(paper, selected) {
    const go = (tab) => function () {
      send({ type: "wb_select_paper", slug: paper.slug });
      send({ type: "wb_tab", tab: tab });
    };
    return el("article", { class: "wb-paper" + (selected ? " selected" : "") }, [
      el("header", {}, [
        el("div", { class: "wb-paper-icon" }, [icon("papers", 20)]),
        el("div", {}, [
          el("h4", { text: paper.slug }),
          el("small", { text: paper.source ? "Converted from " + paper.source : "Written in Galley" }),
        ]),
        selected ? badge("Selected", "info") : null,
      ]),
      el("dl", {}, [
        el("div", {}, [el("dt", { text: "Build" }), el("dd", {}, [statusBadge(paper.build_ok === null ? undefined : paper.build_ok)])]),
        el("div", {}, [el("dt", { text: "Verify" }), el("dd", {}, [paper.source ? statusBadge(paper.verify || undefined) : badge("No original", "neutral")])]),
        el("div", {}, [el("dt", { text: "Git" }), el("dd", {}, [paper.git ? badge("Repository", "neutral") : badge("Not yet", "neutral")])]),
      ]),
      el("footer", {}, [
        button("Build", "subtle", go("build")),
        button("Verify", "subtle", go("verify")),
        button("Review", "subtle", go("review")),
        selected ? null : button("Select", "", () => send({ type: "wb_select_paper", slug: paper.slug })),
      ]),
    ]);
  }

  function drawPapers(state) {
    draw("wb-tab-papers", [state.papers, state.paper, state.workspace, state.doctor, busy(state), !!state.demo], function () {
      const doctor = state.doctor;
      const built = state.papers.filter((p) => p.build_ok === true).length;
      const verified = state.papers.filter((p) => p.verify === "pass").length;
      return [
        pageHead("Papers", "Every paper is its own folder and Git repository in your workspace.", [
          button("Convert a document", "primary", () => send({ type: "wb_tab", tab: "convert" }), { icon: "convert" }),
        ]),
        state.demo ? demoBox(state) : null,
        el("div", { class: "wb-stats" }, [
          stat("Papers", state.papers.length),
          stat("Built", built, built ? "ok" : ""),
          stat("Verified", verified, verified ? "ok" : ""),
          stat("Need attention", state.papers.filter((p) => p.build_ok === false || p.verify === "fail").length, state.papers.some((p) => p.build_ok === false || p.verify === "fail") ? "bad" : ""),
        ]),
        state.papers.length
          ? el("div", { class: "wb-papers" }, state.papers.map((paper) => paperCard(paper, paper.slug === state.paper)))
          : empty("papers", "No papers yet", "Convert an existing Word, Google Docs or PDF whitepaper, or start a new one below.",
              button("Convert a document", "primary", () => send({ type: "wb_tab", tab: "convert" }), { icon: "convert" })),
        el("div", { class: "wb-grid2" }, [
          card("Start a new paper", [
            el("p", { class: "wb-muted", text: "Creates a paper with an example chart and table, ready to build." }),
            el("div", { class: "wb-inline" }, [
              field("wb-new-slug", "Paper id", "liquidity-2026", "", "Lowercase letters, digits and hyphens."),
              button("Create", "primary", () => send({ type: "wb_new_paper", slug: valueOf("wb-new-slug") }), { icon: "plus" }),
            ]),
          ]),
          card("Toolchain", [
            doctor
              ? el("div", {}, [
                  doctor.problems.length
                    ? el("ul", { class: "wb-problems" }, doctor.problems.map((p) => el("li", { text: p })))
                    : el("p", {}, [badge("Everything needed is installed", "ok")]),
                  table(["Tool", "Version"], Object.entries(doctor.versions).map(([k, v]) => [k, v || "Not found"])),
                ])
              : el("p", { class: "wb-muted", text: "Check that Quarto, TeX, Git and gh are installed and which versions will be recorded in build reports." }),
          ], { aside: button("Run check", "", () => send({ type: "wb_doctor" }), { icon: "pulse" }) }),
        ]),
      ];
    });
  }

  // ---- Convert -----------------------------------------------------------

  function drawConvert(state) {
    draw("wb-convert-intro", ["static"], function () {
      return [
        pageHead("Convert", "Bring an existing whitepaper into Galley. The original is copied into the paper and never modified."),
        el("div", { class: "wb-callout" }, [
          el("b", { text: "Prefer the Word file when there is one. " }),
          "It keeps styles, footnotes and chart data that a PDF has lost.",
        ]),
      ];
    });
    const dropLabel = document.querySelector("#wb-upload > div");
    if (dropLabel && !dropLabel.dataset.styled) {
      dropLabel.dataset.styled = "1";
      dropLabel.className = "wb-drop-inner";
      dropLabel.replaceChildren(
        el("div", { class: "wb-drop-icon" }, [icon("upload", 24)]),
        el("b", { text: "Drop a .docx or .pdf here" }),
        el("span", { text: "or click to choose a file. A .bib file can be dropped too." })
      );
    }
    const detail = state.detail;
    draw("wb-convert-body", [state.upload, state.demo, busy(state), detail && detail.conversion_html, detail && detail.slug], function () {
      const demo = state.demo && !(state.upload || {}).name ? state.demo : null;
      const upload = demo ? { name: demo.slug + ".docx", path: demo.sample } : state.upload || {};
      const guess = upload.name ? upload.name.replace(/\.[^.]+$/, "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") : "";
      const isBib = (upload.name || "").endsWith(".bib");
      return [
        card("Document", [
          demo
            ? el("p", {}, [badge("Demo", "info"), " The sample whitepaper is filled in. Press Convert."])
            : upload.name
              ? el("p", {}, [badge("Uploaded", "ok"), " ", el("b", { text: upload.name })])
              : null,
          el("div", { class: "wb-form" }, [
            field("wb-conv-source", "File path or Google Docs URL", "/path/to/paper.docx", isBib ? "" : upload.path, "A link-shared Google Doc is exported as Word."),
            field("wb-conv-slug", "Paper id", "lowercase-with-hyphens", isBib ? "" : guess, "The name of the new paper folder."),
            field("wb-conv-bib", "Bibliography (.bib)", "/path/to/references.bib", demo ? demo.bib : isBib ? upload.path : "", "Optional. Without it every citation is flagged."),
            field("wb-conv-url", "Where the document lives online", "https://…", "", "Optional. Recorded in the paper's front matter."),
          ]),
          el("div", { class: "wb-actions left" }, [
            button("Convert", "primary", () =>
              send({
                type: "wb_convert",
                source: valueOf("wb-conv-source"),
                slug: valueOf("wb-conv-slug"),
                bib: valueOf("wb-conv-bib"),
                source_url: valueOf("wb-conv-url"),
              }), { disabled: busy(state), icon: "convert" }),
            el("span", { class: "wb-muted", text: "Extracts the content, builds the PDF and verifies it against the original." }),
          ]),
        ]),
        detail && detail.conversion_html
          ? card("Conversion report · " + detail.slug, [el("div", { class: "wb-report", html: detail.conversion_html })], {
              aside: el("div", { class: "wb-actions" }, [
                button("Build", "", () => send({ type: "wb_tab", tab: "build" }), { icon: "arrow" }),
                button("Verify", "", () => send({ type: "wb_tab", tab: "verify" }), { icon: "arrow" }),
              ]),
            })
          : null,
      ];
    });
  }

  // ---- Build -------------------------------------------------------------

  function needPaper(title, subtitle) {
    return [
      pageHead(title, subtitle),
      empty("papers", "No paper selected", "Choose a paper in the sidebar, or create one on the Papers page.",
        button("Go to Papers", "", () => send({ type: "wb_tab", tab: "papers" }), { icon: "arrow" })),
    ];
  }

  function drawBuild(state) {
    const detail = state.detail;
    draw("wb-tab-build", [detail, busy(state)], function () {
      if (!detail) return needPaper("Build", "Render the PDF through the team template.");
      const report = detail.build;
      const checks = report ? report.checks : {};
      const count = (items) => (items ? items.length : 0);
      const list = (items) => (items && items.length ? items.join(", ") : "None");
      const data = detail.data;
      const unlisted = data.problems.filter((p) => p.startsWith("not in manifest: ")).map((p) => p.slice(17));
      const issues = count(checks["unresolved-references"]) + count(checks["undefined-citations"]) + count(checks["chart-fonts"]);
      return [
        pageHead("Build · " + detail.slug, "Data is checked against its manifest, then the PDF is rendered through the team template.", [
          report ? statusBadge(report.ok) : badge("Not built yet", "neutral"),
          button("Build PDF", "primary", () => send({ type: "wb_build" }), { disabled: busy(state), icon: "build" }),
        ]),
        el("div", { class: "wb-stats" }, [
          stat("Pages", report ? report.output.pages : "–"),
          stat("Data files pinned", data.entries.length),
          stat("Data problems", data.problems.length, data.problems.length ? "bad" : "ok"),
          stat("Check failures", report ? issues : "–", report ? (issues ? "bad" : "ok") : ""),
          stat("Figures needing data", report ? count(checks["needs-data-figures"]) : "–", report && count(checks["needs-data-figures"]) ? "warn" : ""),
        ]),
        el("div", { class: "wb-split" }, [
          el("div", { class: "wb-stack" }, [
            card("Data manifest", [
              data.problems.length
                ? el("ul", { class: "wb-problems" }, data.problems.map((p) => el("li", { text: p })))
                : el("p", {}, [badge("Every data file matches its hash", "ok")]),
              ...unlisted.map((path) => button("Add " + path + " to the manifest", "", () => send({ type: "wb_data_add", path: path }), { icon: "plus" })),
              data.entries.length
                ? table(["File", "SHA-256", "Source", "As of"], data.entries.map((e) => [e.path, e.sha256.slice(0, 12) + "…", e.source, e.as_of]))
                : el("p", { class: "wb-muted", text: "No data files yet." }),
            ]),
            report
              ? card("Checks", [
                  table(["Check", "Result"], [
                    ["Unresolved cross-references", list(checks["unresolved-references"])],
                    ["Undefined citations", list(checks["undefined-citations"])],
                    ["Charts not in the template font", list(checks["chart-fonts"])],
                    ["Figures still needing data (not a failure)", list(checks["needs-data-figures"])],
                  ]),
                ])
              : null,
            report
              ? card("Reproducibility", [
                  table(["", ""], [
                    ["Template", report.template],
                    ["Git commit", (report.git && report.git.sha) || "Not a Git repository"],
                    ["Built", report["generated-at"]],
                    ...Object.entries(report.tools).map(([k, v]) => [k, v]),
                  ]),
                ])
              : null,
          ]),
          card("PDF", [
            detail.pdf
              ? el("iframe", { class: "wb-pdf", src: detail.pdf, title: "Built PDF" })
              : empty("build", "No PDF yet", "Press Build PDF to render this paper."),
          ], {
            class: "flush",
            aside: detail.pdf ? el("a", { class: "wb-btn subtle", href: detail.pdf, target: "_blank" }, [icon("external", 16), el("span", { text: "Open in a new tab" })]) : null,
          }),
        ]),
      ];
    });
  }

  // ---- Verify ------------------------------------------------------------

  function checkTile(check) {
    const kind = check.status === "fail" ? "bad" : check.status === "advisory" ? "warn" : check.status === "skipped" ? "" : "ok";
    return el("div", { class: "wb-check " + kind }, [
      el("span", { class: "wb-check-mark" }, [icon(kind === "bad" ? "cross" : "check", 14)]),
      el("div", {}, [
        el("b", { text: check.title + (check.hard ? "" : " (advisory)") }),
        el("p", { text: check.summary }),
      ]),
    ]);
  }

  function drawVerify(state) {
    const detail = state.detail;
    draw("wb-tab-verify", [detail && [detail.slug, detail.verify, detail.source], busy(state)], function () {
      if (!detail) return needPaper("Verify", "Compare a converted paper with its original.");
      const report = detail.verify;
      const parts = [
        pageHead("Verify · " + detail.slug, "Compares the original document with the rendered PDF, so it checks what readers will see.", [
          report ? statusBadge(report.status) : badge("Not run yet", "neutral"),
          button("Run verify", "primary", () => send({ type: "wb_verify", source: valueOf("wb-verify-source") }), { disabled: busy(state), icon: "verify" }),
        ]),
        card("Original document", [
          field("wb-verify-source", "Path", detail.source ? "source/" + detail.source : "/path/to/original.docx or .pdf", "",
            detail.source ? "Leave blank to use the original kept with the paper." : "This paper has no original; give the path of the document to compare against."),
          report ? el("p", { class: "wb-muted", text: "Last run: " + report.source + " against " + report.pdf + ", " + report["generated-at"] }) : null,
        ]),
      ];
      if (!report) return parts;
      parts.push(el("div", { class: "wb-checks" }, report.checks.map(checkTile)));
      for (const check of report.checks) {
        if (!check.findings.length) continue;
        parts.push(
          card(check.title + " · " + check.findings.length + " finding" + (check.findings.length === 1 ? "" : "s"), [
            table(
              ["What", "Where", "Original", "Galley PDF", ""],
              check.findings.map((f) => [
                el("div", {}, [
                  el("div", { text: f.message }),
                  el("code", { text: f.id }),
                  f.accepted ? el("div", {}, [badge("Accepted: " + f.accepted_reason, "ok")]) : !f.fails ? el("div", { class: "wb-muted", text: "Does not fail the check" }) : null,
                ]),
                f.location,
                f.source,
                f.candidate,
                f.accepted || !f.fails
                  ? ""
                  : button("Accept…", "", function (event) {
                      const cell = event.currentTarget.parentNode;
                      const reason = el("input", { class: "wb-input", placeholder: "Reason" });
                      const who = el("input", {
                        class: "wb-input",
                        placeholder: f.numeric ? "Approved by (your name, required)" : "Approved by (optional)",
                      });
                      cell.replaceChildren(
                        el("div", { class: "wb-stack tight" }, [
                          reason,
                          who,
                          button("Accept and re-check", "primary", () => send({ type: "wb_accept", id: f.id, reason: reason.value, approved_by: who.value })),
                        ])
                      );
                      reason.focus();
                    }),
              ])
            ),
          ])
        );
      }
      if (report["rejected-acceptances"].length) {
        parts.push(card("Acceptances not applied", [el("ul", { class: "wb-problems" }, report["rejected-acceptances"].map((r) => el("li", { text: r })))]));
      }
      if (report.visual.length) {
        parts.push(
          card("Visual comparison (advisory)", [
            el("p", { class: "wb-muted", text: "Layout changes on purpose, so these are for a person to compare." }),
            el(
              "div",
              { class: "wb-visual" },
              report.visual.map((name) =>
                el("figure", {}, [
                  el("img", { src: "/paper/" + detail.slug + "/" + name + "?v=" + encodeURIComponent(report["generated-at"]), alt: name }),
                  el("figcaption", { text: name.split("/").pop() }),
                ])
              )
            ),
          ])
        );
      }
      return parts;
    });
  }

  // ---- Review ------------------------------------------------------------

  function drawReview(state) {
    const available = !!state.review;
    draw("wb-review-empty", [available, state.review_error, state.paper, !!state.demo], function () {
      if (available) {
        return state.demo
          ? [el("div", { class: "wb-notice" }, [badge("Demo", "info"), " Simulated GitHub on this computer. You are “you”; alice is a simulated teammate who answers your comments within a few seconds."])]
          : [];
      }
      return [
        pageHead("Review", "Comment on a pull request with a live typeset preview."),
        empty(
          "review",
          state.paper ? "This paper is not ready for review" : "No paper selected",
          state.review_error ||
            (state.paper
              ? "The paper must be a Git repository pushed to GitHub with an open pull request that changes a .qmd file, and `gh auth status` must succeed."
              : "Choose a paper in the sidebar first."),
          state.paper ? button("Try again", "", () => send({ type: "wb_open_review" }), { icon: "pulse" }) : null
        ),
      ];
    });
    for (const id of ["gl-topbar", "gl-banner", "gl-panes"]) {
      document.getElementById(id).style.display = available ? "" : "none";
    }
  }

  function showMessage(message) {
    if (!message || !message.text || message.serial === A.messageSerial) return;
    A.messageSerial = message.serial;
    const toast = document.getElementById("gl-toast");
    toast.textContent = message.text;
    toast.className = message.kind === "error" ? "error" : "";
    toast.style.display = "block";
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => (toast.style.display = "none"), message.kind === "error" ? 12000 : 5000);
  }

  function update(state, file) {
    if (!state) return { version: "", epoch: -2 };
    const first = A.state === null;
    A.state = state;
    acknowledge(state.ack);
    for (const [id] of TABS) {
      document.getElementById("wb-tab-" + id).classList.toggle("active", state.tab === id);
    }
    drawNav(state);
    drawJob(state);
    drawPapers(state);
    drawConvert(state);
    drawBuild(state);
    drawVerify(state);
    drawReview(state);
    // A message from before this window opened is old news.
    if (first) A.messageSerial = state.message.serial;
    showMessage(state.message);
    let epoch = file ? file.epoch : -1;
    if (state.review && window.galleyReview) {
      const seen = window.galleyReview.update(state.review, file);
      epoch = seen.epoch;
      if (A.lastTab !== state.tab && state.tab === "review") window.galleyReview.refresh();
    }
    A.lastTab = state.tab;
    return { version: state.version, epoch: epoch };
  }

  window.galleyApp = { update: update, state: A };
})();

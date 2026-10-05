// Galley app shell: the tabs around the review page. Draws Papers, Convert,
// Build and Verify from the state snapshot; hands the Review tab to
// galley-review.js. Every button sends an action through `store-action`.
(function () {
  "use strict";

  const A = { state: null, tab: null, signatures: {}, messageSerial: 0, lastTab: null };
  const TABS = [
    ["papers", "Papers"],
    ["convert", "1 Convert"],
    ["build", "2 Build"],
    ["verify", "3 Verify"],
    ["review", "4 Review"],
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

  function pill(text, kind) {
    return el("span", { class: "gl-pill " + (kind || ""), text: text });
  }

  function statusPill(status) {
    if (status === "pass" || status === true) return pill("pass", "ok");
    if (status === "warn") return pill("pass, see notes", "ok");
    if (status === "fail" || status === false) return pill("fail", "bad");
    if (status === "advisory") return pill("advisory", "busy");
    if (status === "skipped") return pill("skipped", "");
    return pill("not run", "");
  }

  function table(headers, rows) {
    return el("table", { class: "wb-table" }, [
      el("thead", {}, [el("tr", {}, headers.map((h) => el("th", { text: h })))]),
      el(
        "tbody",
        {},
        rows.map((row) =>
          el(
            "tr",
            {},
            row.map((cell) => (cell instanceof Node ? el("td", {}, [cell]) : el("td", { text: cell === null || cell === undefined ? "" : String(cell) })))
          )
        )
      ),
    ]);
  }

  function field(id, label, placeholder, value) {
    const input = el("input", { id: id, placeholder: placeholder || "", class: "wb-input" });
    if (value) input.value = value;
    return el("label", { class: "wb-field" }, [el("span", { text: label }), input]);
  }

  function valueOf(id) {
    const node = document.getElementById(id);
    return node ? node.value.trim() : "";
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

  // ---- navigation --------------------------------------------------------

  function drawNav(state) {
    draw("wb-nav", [state.tab, state.papers, state.paper, state.workspace], function () {
      const select = el("select", {
        class: "wb-select",
        title: "Paper",
        onchange: (event) => event.target.value && send({ type: "wb_select_paper", slug: event.target.value }),
      });
      select.append(el("option", { value: "", text: state.papers.length ? "Select a paper…" : "No papers yet" }));
      for (const paper of state.papers) select.append(el("option", { value: paper.slug, text: paper.slug }));
      select.value = state.paper || "";
      return [
        el("span", { class: "wb-brand", text: "Galley" }),
        el(
          "nav",
          {},
          TABS.map(([id, label]) =>
            el("button", {
              class: "wb-tabbtn" + (state.tab === id ? " active" : ""),
              text: label,
              onclick: () => send({ type: "wb_tab", tab: id }),
            })
          )
        ),
        el("span", { style: "flex:1" }),
        el("span", { class: "wb-muted", text: "Paper" }),
        select,
      ];
    });
  }

  function drawJob(state) {
    const job = state.job;
    draw("wb-job", [job.kind, job.state, job.steps, job.error, job.summary, job.state === "running" ? Math.floor(job.elapsed) : job.elapsed], function () {
      if (job.state === "idle") return [];
      const steps = job.steps.map((step, index) => {
        const last = index === job.steps.length - 1;
        const mark = job.state === "running" && last ? "▸" : job.state === "failed" && last ? "✕" : "✓";
        return el("li", { class: job.state === "running" && last ? "current" : "", text: mark + " " + step });
      });
      const kind = job.state === "running" ? "busy" : job.state === "done" ? "ok" : "bad";
      const label = job.state === "running" ? "running" : job.state === "done" ? "done" : "failed";
      return [
        el("div", { class: "wb-jobhead" }, [
          el("b", { text: job.title }),
          pill(label + " · " + job.elapsed + " s", kind),
        ]),
        el("ol", { class: "wb-steps" }, steps),
        job.summary ? el("div", { class: "wb-jobsummary", text: job.summary }) : null,
        job.error ? el("pre", { class: "wb-error", text: job.error }) : null,
      ];
    });
  }

  // ---- Papers ------------------------------------------------------------

  function drawPapers(state) {
    draw("wb-tab-papers", [state.papers, state.paper, state.workspace, state.doctor, busy(state), !!state.demo], function () {
      const rows = state.papers.map((paper) => [
        el("button", {
          class: "gl-link wb-strong",
          text: paper.slug,
          onclick: () => send({ type: "wb_select_paper", slug: paper.slug }),
        }),
        paper.source ? "converted from " + paper.source : "written in Galley",
        statusPill(paper.build_ok === null ? undefined : paper.build_ok),
        statusPill(paper.verify || undefined),
        paper.git ? "yes" : "no",
        paper.slug === state.paper ? pill("selected", "new") : "",
      ]);
      const doctor = state.doctor;
      return [
        el("h2", { text: "Papers" }),
        el("p", { class: "wb-muted", text: "Workspace: " + state.workspace + ". Each paper is its own folder (and Git repo) here." }),
        el("ol", { class: "wb-flow" }, [
          el("li", { html: "<b>Convert</b> an existing Word, Google Docs or PDF whitepaper, or start a new paper below." }),
          el("li", { html: "<b>Build</b> the PDF through the team template; data is checked against its manifest first." }),
          el("li", { html: "<b>Verify</b> a converted paper against its original: text, numbers, headings, exhibits." }),
          el("li", { html: "<b>Review</b> on a GitHub pull request with a live typeset preview." }),
        ]),
        state.demo
          ? el("div", { class: "wb-demo" }, [
              el("b", { text: "Demo walkthrough" }),
              el("ol", {}, [
                el("li", { html: "Open <b>1 Convert</b>. The sample Word whitepaper and its bibliography are already filled in. Press <b>Convert</b> and watch the stages in the strip above." }),
                el("li", { html: "Read the conversion report: one style has no mapping, one figure needs data, one citation could not be matched. Verify <b>fails</b> on purpose, because that citation's year (1999) is missing from the PDF." }),
                el("li", { html: "Open <b>2 Build</b> to see the manifest, the checks and the PDF, including a chart rebuilt from the data embedded in the Word file." }),
                el("li", { html: "Open <b>3 Verify</b> and look at each finding. To accept the missing number you must give a reason and your name." }),
                el("li", { html: "Open <b>4 Review</b>. A pull request is simulated on your machine and a teammate (alice) has left two comments. Reply, add a comment with the blue <b>+</b>, resolve, edit the text, and watch alice answer within a few seconds." }),
              ]),
              el("p", { class: "wb-muted", text: "Nothing in the demo is sent to GitHub or anywhere else." }),
            ])
          : null,
        state.papers.length
          ? table(["Paper", "Origin", "Build", "Verify", "Git repo", ""], rows)
          : el("p", { text: "No papers in this workspace yet." }),
        el("h3", { text: "New paper" }),
        el("div", { class: "wb-row" }, [
          field("wb-new-slug", "Paper id", "liquidity-2026"),
          el("button", {
            class: "gl-btn primary",
            text: "Create",
            onclick: () => send({ type: "wb_new_paper", slug: valueOf("wb-new-slug") }),
          }),
        ]),
        el("h3", { text: "Toolchain" }),
        el("button", { class: "gl-btn", text: "Run doctor", onclick: () => send({ type: "wb_doctor" }) }),
        doctor
          ? el("div", {}, [
              table(["Tool", "Version"], Object.entries(doctor.versions).map(([k, v]) => [k, v || "NOT FOUND"])),
              doctor.problems.length
                ? el("ul", { class: "wb-problems" }, doctor.problems.map((p) => el("li", { text: p })))
                : el("p", {}, [pill("everything needed is installed", "ok")]),
            ])
          : null,
      ];
    });
  }

  // ---- Convert -----------------------------------------------------------

  function drawConvert(state) {
    draw("wb-convert-intro", ["static"], function () {
      return [
        el("h2", { text: "Convert a legacy whitepaper" }),
        el("p", {
          class: "wb-muted",
          text: "Word keeps styles, footnotes and chart data that a PDF has lost, so prefer the .docx when there is one. The original is copied into the paper and never modified.",
        }),
      ];
    });
    const detail = state.detail;
    draw("wb-convert-body", [state.upload, state.demo, busy(state), detail && detail.conversion_html, detail && detail.slug], function () {
      const demo = state.demo && !(state.upload || {}).name ? state.demo : null;
      const upload = demo ? { name: demo.slug + ".docx", path: demo.sample } : state.upload || {};
      const guess = upload.name ? upload.name.replace(/\.[^.]+$/, "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") : "";
      const isBib = (upload.name || "").endsWith(".bib");
      return [
        demo
          ? el("p", {}, [pill("demo", "new"), " The sample whitepaper is filled in below. Press Convert."])
          : upload.name
            ? el("p", {}, ["Uploaded: ", el("b", { text: upload.name })])
            : null,
        el("div", { class: "wb-form" }, [
          field("wb-conv-source", "File path or Google Docs URL", "/path/to/paper.docx or https://docs.google.com/document/d/…", isBib ? "" : upload.path),
          field("wb-conv-slug", "Paper id", "lowercase-with-hyphens", isBib ? "" : guess),
          field("wb-conv-bib", "Bibliography (.bib), optional", "/path/to/references.bib", demo ? demo.bib : isBib ? upload.path : ""),
          field("wb-conv-url", "Where the document lives online, optional", "https://…"),
        ]),
        el("button", {
          class: "gl-btn primary",
          text: "Convert",
          disabled: busy(state),
          onclick: () =>
            send({
              type: "wb_convert",
              source: valueOf("wb-conv-source"),
              slug: valueOf("wb-conv-slug"),
              bib: valueOf("wb-conv-bib"),
              source_url: valueOf("wb-conv-url"),
            }),
        }),
        detail && detail.conversion_html
          ? el("div", {}, [
              el("h3", { text: "Conversion report for " + detail.slug }),
              el("div", { class: "wb-report", html: detail.conversion_html }),
              el("div", { class: "wb-row" }, [
                el("button", { class: "gl-btn", text: "Go to Build", onclick: () => send({ type: "wb_tab", tab: "build" }) }),
                el("button", { class: "gl-btn", text: "Go to Verify", onclick: () => send({ type: "wb_tab", tab: "verify" }) }),
              ]),
            ])
          : null,
      ];
    });
  }

  // ---- Build -------------------------------------------------------------

  function needPaper(title) {
    return [el("h2", { text: title }), el("p", { text: "Select a paper at the top right, or create one on the Papers tab." })];
  }

  function drawBuild(state) {
    const detail = state.detail;
    draw("wb-tab-build", [detail, busy(state)], function () {
      if (!detail) return needPaper("Build");
      const report = detail.build;
      const checks = report ? report.checks : {};
      const list = (items) => (items && items.length ? items.join(", ") : "none");
      const data = detail.data;
      const unlisted = data.problems.filter((p) => p.startsWith("not in manifest: ")).map((p) => p.slice(17));
      return [
        el("h2", { text: "Build " + detail.slug }),
        el("div", { class: "wb-row" }, [
          el("button", { class: "gl-btn primary", text: "Build PDF", disabled: busy(state), onclick: () => send({ type: "wb_build" }) }),
          report ? statusPill(report.ok) : pill("not built yet", ""),
          report ? el("span", { class: "wb-muted", text: "last built " + report["generated-at"] }) : null,
        ]),
        el("div", { class: "wb-split" }, [
          el("div", {}, [
            el("h3", { text: "Data manifest" }),
            data.problems.length
              ? el("ul", { class: "wb-problems" }, data.problems.map((p) => el("li", { text: p })))
              : el("p", {}, [pill("every data file matches its hash", "ok")]),
            ...unlisted.map((path) =>
              el("button", { class: "gl-btn small", text: "Add " + path + " to the manifest", onclick: () => send({ type: "wb_data_add", path: path }) })
            ),
            data.entries.length
              ? table(["File", "SHA-256", "Source", "As of"], data.entries.map((e) => [e.path, e.sha256.slice(0, 12) + "…", e.source, e.as_of]))
              : null,
            report
              ? el("div", {}, [
                  el("h3", { text: "Checks" }),
                  table(["Check", "Result"], [
                    ["Unresolved cross-references", list(checks["unresolved-references"])],
                    ["Undefined citations", list(checks["undefined-citations"])],
                    ["Charts not in the template font", list(checks["chart-fonts"])],
                    ["Figures still needing data (not a failure)", list(checks["needs-data-figures"])],
                  ]),
                  el("h3", { text: "Reproducibility" }),
                  table(["", ""], [
                    ["Template", report.template],
                    ["Git commit", (report.git && report.git.sha) || "not a Git repo"],
                    ...Object.entries(report.tools).map(([k, v]) => [k, v]),
                  ]),
                ])
              : null,
          ]),
          el("div", {}, [
            detail.pdf ? el("p", {}, [el("a", { href: detail.pdf, target: "_blank", text: "Open the PDF in a new tab" })]) : null,
            detail.pdf
              ? el("iframe", { class: "wb-pdf", src: detail.pdf, title: "Built PDF" })
              : el("p", { class: "wb-muted", text: "The PDF appears here after a build." }),
          ]),
        ]),
      ];
    });
  }

  // ---- Verify ------------------------------------------------------------

  function drawVerify(state) {
    const detail = state.detail;
    draw("wb-tab-verify", [detail && [detail.slug, detail.verify, detail.source], busy(state)], function () {
      if (!detail) return needPaper("Verify");
      const report = detail.verify;
      const parts = [
        el("h2", { text: "Verify " + detail.slug }),
        el("p", {
          class: "wb-muted",
          text: "Compares the original document with the rendered PDF, so it checks what readers will see. The paper is rebuilt first.",
        }),
        el("div", { class: "wb-form" }, [
          field("wb-verify-source", "Original document", detail.source ? "source/" + detail.source + " (leave blank to use it)" : "/path/to/original.docx or .pdf"),
        ]),
        el("div", { class: "wb-row" }, [
          el("button", {
            class: "gl-btn primary",
            text: "Run verify",
            disabled: busy(state),
            onclick: () => send({ type: "wb_verify", source: valueOf("wb-verify-source") }),
          }),
          report ? statusPill(report.status) : pill("not run yet", ""),
          report ? el("span", { class: "wb-muted", text: report.source + " against " + report.pdf + ", " + report["generated-at"] }) : null,
        ]),
      ];
      if (!report) return parts;
      parts.push(
        table(
          ["Check", "Result", "Summary"],
          report.checks.map((c) => [c.title + (c.hard ? "" : " (advisory)"), statusPill(c.status), c.summary])
        )
      );
      for (const check of report.checks) {
        if (!check.findings.length) continue;
        parts.push(el("h3", { text: check.title }));
        parts.push(
          table(
            ["What", "Where", "Original", "Galley PDF", ""],
            check.findings.map((f) => [
              el("div", {}, [
                el("div", { text: f.message }),
                el("code", { text: f.id }),
                f.accepted ? el("div", {}, [pill("accepted: " + f.accepted_reason, "ok")]) : !f.fails ? el("div", { class: "wb-muted", text: "does not fail the check" }) : null,
              ]),
              f.location,
              f.source,
              f.candidate,
              f.accepted || !f.fails
                ? ""
                : el("button", {
                    class: "gl-btn small",
                    text: "Accept…",
                    onclick: function (event) {
                      const cell = event.target.parentNode;
                      const reason = el("input", { class: "wb-input", placeholder: "Reason" });
                      const who = el("input", {
                        class: "wb-input",
                        placeholder: f.numeric ? "Approved by (your name, required)" : "Approved by (optional)",
                      });
                      cell.replaceChildren(
                        reason,
                        who,
                        el("button", {
                          class: "gl-btn small primary",
                          text: "Accept and re-check",
                          onclick: () => send({ type: "wb_accept", id: f.id, reason: reason.value, approved_by: who.value }),
                        })
                      );
                      reason.focus();
                    },
                  }),
            ])
          )
        );
      }
      if (report["rejected-acceptances"].length) {
        parts.push(el("h3", { text: "Acceptances not applied" }));
        parts.push(el("ul", { class: "wb-problems" }, report["rejected-acceptances"].map((r) => el("li", { text: r }))));
      }
      if (report.visual.length) {
        parts.push(el("h3", { text: "Visual comparison (advisory)" }));
        parts.push(
          el(
            "div",
            { class: "wb-visual" },
            report.visual.map((name) =>
              el("figure", {}, [
                el("img", { src: "/paper/" + detail.slug + "/" + name + "?v=" + encodeURIComponent(report["generated-at"]), alt: name }),
                el("figcaption", { text: name.split("/").pop() }),
              ])
            )
          )
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
          ? [el("div", { class: "wb-demo slim" }, [pill("demo", "new"), " Simulated GitHub on this machine. You are “you”; alice is a simulated teammate who answers your comments within a few seconds."])]
          : [];
      }
      return [
        el("h2", { text: "Review" }),
        el("p", {
          text: state.review_error || (state.paper ? "Opening the review…" : "Select a paper at the top right first."),
        }),
        state.paper
          ? el("p", { class: "wb-muted", text: "The paper must be a Git repo pushed to GitHub (github.com or Enterprise) with an open pull request that changes a .qmd file, and `gh auth status` must succeed." })
          : null,
        state.paper ? el("button", { class: "gl-btn", text: "Try again", onclick: () => send({ type: "wb_open_review" }) }) : null,
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

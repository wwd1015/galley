// Galley Review: draws the page from the state snapshot the server publishes.
// Everything the user does is sent back as an action through the
// `store-action` Dash store; the server applies it and publishes new state.
(function () {
  "use strict";

  const G = {
    cm: null,
    state: null,
    path: null,
    fileEpoch: null,
    loading: false,
    saveTimer: null,
    widgets: new Map(), // thread id -> {widget, node, signature}
    drafts: new Map(), // thread id -> unsent reply text
    expanded: new Set(), // resolved threads the user opened
    composer: null,
    hoverLine: null,
    renderSerial: null,
    renderMode: null,
    messageSerial: 0,
    syncing: 0,
    lastActivity: Date.now(),
    idle: false,
    topbarBuilt: false,
  };

  function send(action) {
    action.nonce = Date.now() + ":" + Math.random();
    window.dash_clientside.set_props("store-action", { data: action });
  }

  function el(tag, attrs, children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else if (value !== null && value !== undefined && value !== false) node.setAttribute(key, value);
    }
    for (const child of children || []) {
      if (child) node.append(child);
    }
    return node;
  }

  function ago(iso) {
    const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
    if (!isFinite(seconds)) return "";
    if (seconds < 60) return "just now";
    if (seconds < 3600) return Math.floor(seconds / 60) + " min ago";
    if (seconds < 86400) return Math.floor(seconds / 3600) + " h ago";
    return new Date(iso).toLocaleDateString();
  }

  // ---- editor -----------------------------------------------------------

  function ensureEditor() {
    if (G.cm || !window.CodeMirror) return;
    const host = document.getElementById("gl-editor");
    if (!host) return;
    G.cm = window.CodeMirror(host, {
      lineNumbers: true,
      lineWrapping: true,
      mode: "markdown",
      gutters: ["CodeMirror-linenumbers", "galley-gutter"],
    });
    G.cm.on("change", function () {
      if (G.loading) return;
      clearTimeout(G.saveTimer);
      G.saveTimer = setTimeout(saveNow, 400);
    });
    G.cm.on("scroll", function () {
      syncScroll("editor");
    });
    const wrapper = G.cm.getWrapperElement();
    wrapper.addEventListener("mousemove", function (event) {
      if (event.target.closest(".gl-thread, .gl-composer")) return;
      showAddButton(G.cm.lineAtHeight(event.clientY, "window"));
    });
  }

  function saveNow() {
    clearTimeout(G.saveTimer);
    G.saveTimer = null;
    if (G.cm && G.path) send({ type: "edit", path: G.path, text: G.cm.getValue() });
  }

  function showAddButton(line) {
    if (!G.cm || line === G.hoverLine || !G.state || !G.state.pr) return;
    if (G.hoverLine !== null) G.cm.setGutterMarker(G.hoverLine, "galley-gutter", null);
    G.hoverLine = line;
    const button = el("button", {
      class: "gl-add",
      title: "Comment on line " + (line + 1),
      text: "+",
      onclick: function () {
        openComposer(line);
      },
    });
    G.cm.setGutterMarker(line, "galley-gutter", button);
  }

  function closeComposer() {
    if (G.composer) G.composer.clear();
    G.composer = null;
  }

  function openComposer(line) {
    closeComposer();
    const area = el("textarea", { placeholder: "Leave a comment on line " + (line + 1) });
    const node = el("div", { class: "gl-thread gl-composer" }, [
      area,
      el("div", { class: "gl-row" }, [
        el("button", {
          class: "gl-btn primary small",
          text: "Comment",
          onclick: function () {
            if (!area.value.trim()) return;
            // The server must see the text the line numbers refer to.
            if (G.saveTimer) saveNow();
            const handle = G.composer.line;
            send({ type: "comment", line: G.cm.getLineNumber(handle) + 1, body: area.value });
            closeComposer();
          },
        }),
        el("button", { class: "gl-btn small", text: "Cancel", onclick: closeComposer }),
      ]),
    ]);
    G.composer = G.cm.addLineWidget(line, node, { coverGutter: false, noHScroll: true });
    area.focus();
  }

  // ---- threads ----------------------------------------------------------

  function commentNode(thread, comment) {
    const body = el("div", { class: "gl-comment-body" });
    body.innerHTML = comment.body_html; // rendered server-side with raw HTML disabled
    const actions = [];
    if (comment.mine) {
      actions.push(
        el("button", {
          class: "gl-link",
          text: "Edit",
          onclick: function () {
            const area = el("textarea", {});
            area.value = comment.body;
            const form = el("div", { class: "gl-reply" }, [
              area,
              el("div", { class: "gl-row" }, [
                el("button", {
                  class: "gl-btn primary small",
                  text: "Update",
                  onclick: function () {
                    send({ type: "edit_comment", id: comment.id, body: area.value });
                  },
                }),
                el("button", {
                  class: "gl-btn small",
                  text: "Cancel",
                  onclick: function () {
                    form.replaceWith(body);
                    refreshWidgets();
                  },
                }),
              ]),
            ]);
            body.replaceWith(form);
            refreshWidgets();
            area.focus();
          },
        }),
        el("button", {
          class: "gl-link",
          text: "Delete",
          onclick: function () {
            send({ type: "delete_comment", id: comment.id });
          },
        })
      );
    }
    return el("div", { class: "gl-comment" }, [
      el("img", { src: comment.avatar, alt: "", referrerpolicy: "no-referrer" }),
      el("div", { class: "gl-comment-main" }, [
        el("div", { class: "gl-comment-meta" }, [
          el("b", { text: comment.author }),
          document.createTextNode(" " + ago(comment.created) + (comment.edited ? " (edited)" : "")),
          ...actions,
        ]),
        body,
      ]),
    ]);
  }

  function threadNode(thread) {
    const collapsed = thread.resolved && !G.expanded.has(thread.id);
    const area = el("textarea", { placeholder: "Reply…" });
    area.value = G.drafts.get(thread.id) || "";
    area.addEventListener("input", function () {
      G.drafts.set(thread.id, area.value);
    });
    const label = thread.outdated
      ? "Outdated: the commented text is no longer in the file"
      : thread.kind === "file"
        ? "Comment on the file"
        : thread.resolved
          ? "Resolved"
          : "";
    const node = el("div", {
      class: "gl-thread" + (thread.resolved ? " resolved" : "") + (collapsed ? " collapsed" : ""),
      "data-thread": thread.id,
    });
    const head = el("div", { class: "gl-thread-head" }, [
      el("span", {
        class: "gl-badge",
        text: String(thread.n),
        title: "Show this comment in the preview",
        onclick: function () {
          showInPreview(thread.ref);
        },
      }),
      el("span", { text: label }),
      el("span", { style: "flex:1" }),
      thread.resolved
        ? el("button", {
            class: "gl-link",
            text: collapsed ? "Show" : "Hide",
            onclick: function () {
              if (G.expanded.has(thread.id)) G.expanded.delete(thread.id);
              else G.expanded.add(thread.id);
              renderThreads(G.state.threads, true);
            },
          })
        : null,
    ]);
    const bodyNode = el("div", { class: "gl-thread-body" }, [
      ...thread.comments.map(function (comment) {
        return commentNode(thread, comment);
      }),
      el("div", { class: "gl-reply" }, [
        area,
        el("div", { class: "gl-row" }, [
          el("button", {
            class: "gl-btn primary small",
            text: "Reply",
            onclick: function () {
              if (!area.value.trim()) return;
              send({ type: "reply", thread: thread.id, body: area.value });
              G.drafts.delete(thread.id);
            },
          }),
          thread.can_resolve
            ? el("button", {
                class: "gl-btn small",
                text: thread.resolved ? "Unresolve conversation" : "Resolve conversation",
                onclick: function () {
                  send({ type: "resolve", thread: thread.id, resolved: !thread.resolved });
                },
              })
            : null,
        ]),
      ]),
    ]);
    node.append(head, bodyNode);
    return node;
  }

  function refreshWidgets() {
    for (const entry of G.widgets.values()) entry.widget.changed();
  }

  function renderThreads(threads, force) {
    if (!G.cm) return;
    const placed = threads.filter(function (t) {
      return t.line !== null && !t.outdated;
    });
    const loose = threads.filter(function (t) {
      return t.line === null || t.outdated;
    });
    const keep = new Set(placed.map((t) => t.id));
    for (const [id, entry] of G.widgets) {
      if (!keep.has(id)) {
        entry.widget.clear();
        G.widgets.delete(id);
      }
    }
    for (const thread of placed) {
      const signature = JSON.stringify(thread);
      const existing = G.widgets.get(thread.id);
      const typing = existing && existing.node.contains(document.activeElement);
      if (existing && (existing.signature === signature || typing) && !force) continue;
      // An unchanged widget follows its line as the text is edited; a changed
      // one is rebuilt where it already sits, else where the server placed it.
      let line = thread.line - 1;
      if (existing) {
        const current = G.cm.getLineNumber(existing.widget.line);
        if (current !== null && existing.serverLine === thread.line) line = current;
        existing.widget.clear();
      }
      line = Math.min(Math.max(line, 0), G.cm.lineCount() - 1);
      const node = threadNode(thread);
      const widget = G.cm.addLineWidget(line, node, { coverGutter: false, noHScroll: true });
      G.widgets.set(thread.id, { widget, node, signature, serverLine: thread.line });
    }
    const panel = document.getElementById("gl-loose");
    const looseSignature = JSON.stringify(loose);
    if (panel.dataset.signature !== looseSignature || force) {
      if (!panel.contains(document.activeElement) || force) {
        panel.dataset.signature = looseSignature;
        panel.replaceChildren();
        if (loose.length) {
          panel.append(el("h4", { text: "Outdated and file comments" }));
          for (const thread of loose) panel.append(threadNode(thread));
        }
      }
    }
  }

  function jumpToThread(ref) {
    const thread = (G.state.threads || []).find((t) => t.ref === ref);
    if (!thread) return;
    const entry = G.widgets.get(thread.id);
    let node;
    if (entry) {
      const line = G.cm.getLineNumber(entry.widget.line);
      G.syncing = Date.now();
      G.cm.scrollIntoView({ line: line, ch: 0 }, 120);
      node = entry.node;
    } else {
      node = document.querySelector('#gl-loose [data-thread="' + thread.id + '"]');
      if (node) node.scrollIntoView({ block: "center" });
    }
    if (node) {
      node.classList.add("flash");
      setTimeout(() => node.classList.remove("flash"), 1500);
    }
  }

  // ---- preview ----------------------------------------------------------

  function frame() {
    return document.getElementById("gl-preview");
  }

  function frameDocument() {
    try {
      return frame().contentDocument;
    } catch (error) {
      return null;
    }
  }

  function showInPreview(ref) {
    const doc = frameDocument();
    const note = doc && doc.querySelector('[data-ref="' + ref + '"]');
    if (!note) return;
    G.syncing = Date.now();
    note.scrollIntoView({ block: "center" });
    note.classList.add("galley-flash");
    setTimeout(() => note.classList.remove("galley-flash"), 1500);
  }

  function scrollFraction(position, total, visible) {
    return total > visible ? position / (total - visible) : 0;
  }

  function syncScroll(from) {
    if (Date.now() - G.syncing < 250) return;
    const doc = frameDocument();
    if (!G.cm || !doc || !doc.documentElement || G.renderMode !== "html") return;
    const info = G.cm.getScrollInfo();
    const root = doc.scrollingElement || doc.documentElement;
    G.syncing = Date.now();
    if (from === "editor") {
      const fraction = scrollFraction(info.top, info.height, info.clientHeight);
      root.scrollTop = fraction * (root.scrollHeight - root.clientHeight);
    } else {
      const fraction = scrollFraction(root.scrollTop, root.scrollHeight, root.clientHeight);
      G.cm.scrollTo(null, fraction * (info.height - info.clientHeight));
    }
  }

  function wirePreview() {
    const doc = frameDocument();
    if (!doc || G.renderMode !== "html") return;
    doc.addEventListener("click", function (event) {
      const note = event.target.closest && event.target.closest(".galley-note");
      if (note) jumpToThread(note.dataset.ref);
    });
    doc.defaultView.addEventListener("scroll", function () {
      syncScroll("preview");
    });
  }

  function updatePreview(render) {
    const empty = document.getElementById("gl-preview-empty");
    const view = frame();
    if (!render.url) {
      empty.textContent =
        render.state === "error"
          ? "The preview could not be rendered:\n\n" + render.error
          : render.state === "idle"
            ? "Select a pull request to see the preview."
            : "Rendering the preview…";
      return;
    }
    empty.textContent = "";
    if (render.serial === G.renderSerial) return;
    G.renderSerial = render.serial;
    G.renderMode = render.url.endsWith(".pdf") ? "pdf" : "html";
    const doc = frameDocument();
    const root = doc && (doc.scrollingElement || doc.documentElement);
    const keep = root && G.renderMode === "html" ? root.scrollTop : 0;
    view.onload = function () {
      const fresh = frameDocument();
      const freshRoot = fresh && (fresh.scrollingElement || fresh.documentElement);
      if (freshRoot && keep) {
        G.syncing = Date.now();
        freshRoot.scrollTop = keep;
      }
      wirePreview();
    };
    view.src = render.url + "?v=" + render.serial;
  }

  // ---- top bar ----------------------------------------------------------

  function setOptions(select, options, value, placeholder) {
    const signature = JSON.stringify([options, placeholder]);
    if (select.dataset.signature !== signature) {
      select.dataset.signature = signature;
      select.replaceChildren();
      if (placeholder) select.append(el("option", { value: "", text: placeholder }));
      for (const [optionValue, label] of options) {
        select.append(el("option", { value: optionValue, text: label }));
      }
    }
    if (document.activeElement !== select) select.value = value === null ? "" : String(value);
  }

  function buildTopbar() {
    const bar = document.getElementById("gl-topbar");
    bar.replaceChildren(
      el("span", { class: "gl-brand", text: "Galley Review" }),
      el("select", {
        id: "gl-pr",
        title: "Pull request",
        onchange: function (event) {
          if (event.target.value) send({ type: "select_pr", number: Number(event.target.value) });
        },
      }),
      el("button", {
        class: "gl-btn small",
        id: "gl-new-pr",
        text: "New PR from this branch",
        onclick: function () {
          send({ type: "open_pr" });
        },
      }),
      el("select", {
        id: "gl-file",
        title: "File",
        onchange: function (event) {
          if (G.saveTimer) saveNow();
          send({ type: "select_file", path: event.target.value });
        },
      }),
      el("span", { class: "gl-branch", id: "gl-branch" }),
      el("span", { class: "gl-pill", id: "gl-sync" }),
      el("span", { class: "gl-pill new", id: "gl-new", onclick: () => send({ type: "seen" }) }),
      el("span", { class: "gl-pill", id: "gl-verify" }),
      el("span", { class: "gl-pill", id: "gl-render" }),
      el("span", { class: "gl-spacer" }),
      el("span", { class: "gl-modes" }, [
        el("label", {}, [
          el("input", {
            type: "radio",
            name: "gl-mode",
            value: "html",
            checked: "checked",
            onchange: () => send({ type: "preview_mode", mode: "html" }),
          }),
          document.createTextNode(" Fast preview"),
        ]),
        el("label", {}, [
          el("input", {
            type: "radio",
            name: "gl-mode",
            value: "pdf",
            onchange: () => send({ type: "preview_mode", mode: "pdf" }),
          }),
          document.createTextNode(" PDF proof"),
        ]),
      ]),
      el("button", {
        class: "gl-btn",
        id: "gl-commit",
        text: "Commit & push",
        onclick: function () {
          if (G.saveTimer) saveNow();
          send({ type: "commit" });
        },
      }),
      el("select", { id: "gl-review-event", title: "Review" }, [
        el("option", { value: "COMMENT", text: "Comment" }),
        el("option", { value: "APPROVE", text: "Approve" }),
        el("option", { value: "REQUEST_CHANGES", text: "Request changes" }),
      ]),
      el("input", { id: "gl-review-body", placeholder: "Review summary", size: "18" }),
      el("button", {
        class: "gl-btn primary",
        id: "gl-review-submit",
        text: "Submit review",
        onclick: function () {
          const body = document.getElementById("gl-review-body");
          send({
            type: "submit_review",
            event: document.getElementById("gl-review-event").value,
            body: body.value,
          });
          body.value = "";
        },
      })
    );
    G.topbarBuilt = true;
  }

  function pill(id, text, kind) {
    const node = document.getElementById(id);
    node.textContent = text;
    node.className = "gl-pill" + (kind ? " " + kind : "");
    node.style.display = text ? "" : "none";
  }

  function renderTopbar(state) {
    if (!G.topbarBuilt) buildTopbar();
    setOptions(
      document.getElementById("gl-pr"),
      state.prs.map((pr) => [String(pr.number), "#" + pr.number + " " + pr.title]),
      state.pr ? state.pr.number : null,
      state.prs.length ? "Select a pull request…" : "No open pull requests touch .qmd files"
    );
    setOptions(
      document.getElementById("gl-file"),
      state.files.map((path) => [path, path]),
      state.path,
      state.files.length ? "" : "No .qmd files"
    );
    document.getElementById("gl-branch").textContent = state.branch ? "⎇ " + state.branch : "";
    const sync = state.sync;
    if (sync.error) pill("gl-sync", "Sync failed", "bad");
    else if (sync.last_poll) {
      const seconds = Math.max(0, Math.round(Date.now() / 1000 - sync.last_poll));
      pill("gl-sync", "Synced " + (seconds < 3 ? "just now" : seconds + " s ago"), "ok");
    } else pill("gl-sync", state.pr ? "Syncing…" : "", "busy");
    document.getElementById("gl-sync").title = sync.error || "";
    pill("gl-new", sync.new_comments ? sync.new_comments + " new comment(s) ✕" : "", "new");
    document.getElementById("gl-new").className = "gl-pill new";
    if (state.verify) pill("gl-verify", "verify: " + state.verify, state.verify === "pass" ? "ok" : "bad");
    else pill("gl-verify", "", "");
    const render = state.render;
    const names = { idle: "", waiting: "Preview queued", rendering: "Rendering…", ok: "Preview up to date", error: "Render failed" };
    pill("gl-render", names[render.state] || "", render.state === "ok" ? "ok" : render.state === "error" ? "bad" : "busy");
    document.getElementById("gl-render").title = render.error || "";
    document.getElementById("gl-commit").disabled = !state.dirty;
    for (const id of ["gl-review-submit", "gl-review-event", "gl-review-body"]) {
      document.getElementById(id).disabled = !state.pr;
    }
  }

  function renderBanner(state) {
    const banner = document.getElementById("gl-banner");
    const rows = [];
    if (state.pr && !state.on_pr_branch) {
      rows.push(
        el("div", {}, [
          el("span", {
            text:
              "You are on " + state.branch + ", but this pull request is on " + state.pr.branch + ".",
          }),
          el("button", {
            class: "gl-btn small",
            text: "Check out " + state.pr.branch,
            onclick: () => send({ type: "checkout" }),
          }),
        ])
      );
    }
    if (state.sync.head_changed) {
      rows.push(
        el("div", {}, [
          el("span", { text: "A teammate pushed new commits to this pull request." }),
          el("button", {
            class: "gl-btn small primary",
            text: "Pull and refresh",
            onclick: function () {
              if (G.saveTimer) saveNow();
              send({ type: "pull" });
            },
          }),
        ])
      );
    }
    if (state.sync.error) {
      rows.push(el("div", { class: "error" }, [el("span", { text: "GitHub sync failed: " + state.sync.error })]));
    }
    if (state.render.state === "error" && state.render.url) {
      rows.push(
        el("div", { class: "error" }, [
          el("span", { text: "The last render failed, so the preview shows the previous version. " + state.render.error.split("\n").slice(-1)[0] }),
        ])
      );
    }
    const signature = rows.map((row) => row.textContent).join("|");
    if (banner.dataset.signature !== signature) {
      banner.dataset.signature = signature;
      banner.replaceChildren(...rows);
      if (G.cm) G.cm.refresh();
    }
  }

  function showMessage(message) {
    if (!message || !message.text || message.serial === G.messageSerial) return;
    G.messageSerial = message.serial;
    const toast = document.getElementById("gl-toast");
    toast.textContent = message.text;
    toast.className = message.kind === "error" ? "error" : "";
    toast.style.display = "block";
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => (toast.style.display = "none"), message.kind === "error" ? 12000 : 5000);
  }

  // ---- state in ---------------------------------------------------------

  function loadFile(file) {
    G.fileEpoch = file.epoch;
    G.path = file.path;
    for (const entry of G.widgets.values()) entry.widget.clear();
    G.widgets.clear();
    closeComposer();
    G.hoverLine = null;
    G.loading = true;
    const scroll = G.cm.getScrollInfo();
    G.cm.setValue(file.text || "");
    G.cm.clearHistory();
    G.cm.scrollTo(scroll.left, scroll.top);
    G.loading = false;
  }

  function update(state, file) {
    ensureEditor();
    if (!state || !G.cm) {
      return { version: "", epoch: -1 };
    }
    G.state = state;
    if (file && file.epoch !== G.fileEpoch) loadFile(file);
    renderTopbar(state);
    renderBanner(state);
    renderThreads(state.threads, false);
    updatePreview(state.render);
    showMessage(state.message);
    return { version: state.version, epoch: G.fileEpoch === null ? -1 : G.fileEpoch };
  }

  // ---- idle detection ---------------------------------------------------

  for (const name of ["mousemove", "keydown", "focus", "scroll"]) {
    window.addEventListener(name, () => (G.lastActivity = Date.now()), true);
  }
  setInterval(function () {
    const idle = document.hidden || Date.now() - G.lastActivity > 120000;
    if (idle !== G.idle && window.dash_clientside && window.dash_clientside.set_props) {
      G.idle = idle;
      send({ type: "activity", idle: idle });
    }
  }, 5000);

  window.galleyReview = { update: update, state: G };
})();

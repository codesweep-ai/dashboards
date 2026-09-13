/* Render the dependencies page from deps.json.
 *
 * deps.json is written by this site's own build (python3 -m collector) and
 * published beside this file, so it is same-origin and needs no token. It
 * carries verdicts as well as facts: every dependency arrives with a status and
 * a level, decided by the rules in SPEC.md, so nothing here re-derives whether
 * something is behind. This file turns the verdicts into actions, groups them,
 * and draws.
 */
(function () {
  "use strict";

  var DATA = "deps.json";
  // The collector runs daily. Past this, the page says the data is old.
  var STALE_AFTER_H = 36;
  // Inventory rows drawn before a "show more" button, so a view of every
  // indirect dependency does not build thousands of rows up front.
  var PAGE_ROWS = 300;

  var LEVELS = ["idle", "good", "info", "warning", "serious", "critical"];
  var FLAGGED = ["critical", "serious", "warning", "info"];
  var INDIRECT = { indirect: true, transitive: true };

  var STATUS_LABEL = {
    vulnerable: "vulnerable", eol: "end of life", "eol-soon": "EOL soon", major: "major behind",
    minor: "minor behind", patch: "patch behind", behind: "commits behind", deprecated: "deprecated",
    current: "current", floating: "floating", unknown: "unknown", untracked: "not compared"
  };
  var STATE_ICON = { critical: "vulnerable", serious: "eol-soon", warning: "major", info: "minor", good: "current", idle: "current" };

  var ECO_LABEL = {
    runtime: "Toolchains", image: "Images & runners", "native": "Native & binaries", "package": "System packages",
    go: "Go modules", npm: "npm packages", pypi: "Python packages", actions: "GitHub Actions"
  };
  var ECO_SHORT = {
    go: "Go", npm: "npm", pypi: "PyPI", actions: "Action", runtime: "Toolchain", image: "Image",
    "package": "Package", "native": "Native"
  };
  var ECO_ORDER = ["runtime", "image", "native", "package", "go", "npm", "pypi", "actions"];

  var PRODUCT = {
    go: "Go", nodejs: "Node.js", python: "Python", "eclipse-temurin": "Java (Temurin)", "apache-maven": "Maven",
    fedora: "Fedora", debian: "Debian", ubuntu: "Ubuntu", alpine: "Alpine", linux: "Linux kernel",
    "windows-server": "Windows Server", macos: "macOS", firecracker: "Firecracker", react: "React",
    eslint: "ESLint", nvm: "nvm"
  };
  var LIFECYCLE_GROUPS = [
    { title: "Toolchains and runtimes", products: ["go", "nodejs", "python", "eclipse-temurin", "apache-maven"] },
    { title: "Operating systems, runners and kernels", products: ["fedora", "debian", "ubuntu", "alpine", "macos", "windows-server", "linux"] },
    { title: "Native binaries", products: ["firecracker"] },
    { title: "Libraries and tools", products: null }
  ];

  // Tiers are urgency and nothing else: when to do the work. What kind of work
  // it is travels as a tag on the action, so "end of life" or "license" never
  // competes with "fix now" for the same place in the ladder.
  var TIERS = [
    { id: "fix", level: "critical", title: "Fix now",
      blurb: "An advisory, an end of life reached or within 90 days, a major out a year, or a fix-by date already passed." },
    { id: "plan", level: "warning", title: "Plan",
      blurb: "A major version behind, a denied license, an abandoned package, or an advisory no code calls." },
    { id: "routine", level: "info", title: "Routine",
      blurb: "Minor and patch releases, sibling pins to move, licenses to review. Batch them." }
  ];
  var TIER_INDEX = { fix: 0, plan: 1, routine: 2 };
  var TYPE_LABEL = { security: "security", eol: "end of life", license: "license", supply: "supply chain",
    sync: "keep in sync", update: "update" };

  var P = function (d) { return '<svg viewBox="0 0 16 16" aria-hidden="true">' + d + "</svg>"; };
  var ICON = {
    vulnerable: P('<path d="M8 1.8l5 2v4c0 3-2.2 5.3-5 6.4-2.8-1.1-5-3.4-5-6.4v-4z"/><path d="M8 5.5v3m0 2.2v.3"/>'),
    eol: P('<circle cx="8" cy="8" r="5.5"/><path d="M8 5v3.2l2 1.3"/>'),
    "eol-soon": P('<path d="M4.5 2.5h7M4.5 13.5h7M5 2.5c0 3 6 3.5 6 5.5S5 10.5 5 13.5M11 2.5c0 1.5-1.5 2.4-3 3"/>'),
    major: P('<path d="M4 8.5l4-4 4 4M4 12.5l4-4 4 4"/>'),
    minor: P('<path d="M4 10l4-4 4 4"/>'),
    patch: P('<path d="M4 10l4-4 4 4"/>'),
    behind: P('<circle cx="8" cy="8" r="2.2"/><path d="M1.5 8h4.3m4.4 0h4.3"/>'),
    deprecated: P('<circle cx="8" cy="8" r="5.5"/><path d="M4.2 11.8l7.6-7.6"/>'),
    current: P('<path d="M3.5 8.5l3 3 6-6"/>'),
    floating: P('<path d="M2.5 9c1.5-2 2.8-2 4.2 0s2.7 2 4.1 0 2.2-1.6 2.7-1"/>'),
    untracked: P('<path d="M2.5 9c1.5-2 2.8-2 4.2 0s2.7 2 4.1 0 2.2-1.6 2.7-1"/>'),
    unknown: P('<path d="M6 6a2 2 0 1 1 2.7 1.9c-.5.2-.7.6-.7 1.1v.5m0 2v.3"/>'),
    unreachable: P('<path d="M8 4.5v4m0 2.5v.5"/>'),
    external: P('<path d="M6.5 3.5h-3v9h9v-3M9 3.5h3.5V7M12.5 3.5L7 9"/>'),
    back: P('<path d="M9.5 3.5L5 8l4.5 4.5"/>'),
    chevron: P('<path d="M6 3.5L10.5 8 6 12.5"/>')
  };

  // --- small helpers ------------------------------------------------------

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function rank(level) { var i = LEVELS.indexOf(level); return i < 0 ? 0 : i; }
  function byId(id) { return document.getElementById(id); }
  function plural(n, one, many) { return n + " " + (n === 1 ? one : (many || one + "s")); }
  function uniq(list) { return list.filter(function (x, i) { return list.indexOf(x) === i; }); }
  function slug(s) { return String(s).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 90); }

  function daysSince(iso) {
    if (!iso) return null;
    var t = Date.parse(iso);
    return isNaN(t) ? null : (Date.now() - t) / 864e5;
  }
  function fmtSpan(days) {
    var d = Math.abs(days);
    if (d < 1) return Math.max(1, Math.round(d * 24)) + "h";
    if (d < 45) return Math.round(d) + "d";
    if (d < 540) return Math.round(d / 30.4) + " months";
    return (d / 365.25).toFixed(1) + " years";
  }
  function fmtAgo(iso) {
    var d = daysSince(iso);
    if (d == null) return "—";
    if (Math.abs(d) * 24 * 60 < 90) return "just now";
    return d >= 0 ? fmtSpan(d) + " ago" : "in " + fmtSpan(d);
  }
  function fmtDate(iso) { return iso ? String(iso).slice(0, 10) : "—"; }

  function chip(level, status, text) {
    return '<span class="ci-chip ci-chip--' + esc(level) + '">' + (ICON[status] || "") +
      esc(text || STATUS_LABEL[status] || status) + "</span>";
  }
  function name(d) { return d.label || d.name; }
  function isIndirect(d) { return !!INDIRECT[d.scope]; }
  function srcPath(d) { return (d.sources && d.sources[0] && d.sources[0].path) || ""; }
  function dirOf(path) { return path.indexOf("/") < 0 ? "." : path.slice(0, path.lastIndexOf("/")); }

  function shortVersion(d) {
    if (d.lag && d.lag.pinned) return d.lag.pinned.slice(0, 7);
    var v = d.version;
    if (v == null) return d.constraint || (d.floating ? "floating" : "unpinned");
    v = String(v);
    return v.length > 22 ? v.slice(0, 9) + "…" + v.slice(-8) : v;
  }
  function target(d) {
    var up = d.upstream || {};
    if (d.lag && d.lag.head) return d.lag.head.slice(0, 7);
    if (d.status === "vulnerable" && d.fix && !up.latest) return d.fix;
    return up.latest || d.fix || null;
  }
  function clip(s, n) { s = String(s == null ? "" : s); return s.length > n ? s.slice(0, n - 1) + "…" : s; }

  function sourceUrl(project, src) {
    if (!project.repo || !project.repo.sha || !src || !src.path) return project.repo ? project.repo.url : "#";
    return project.repo.url + "/blob/" + project.repo.sha + "/" + src.path + (src.line ? "#L" + src.line : "");
  }
  function sourceLink(project, d, text) {
    var s = d.sources[0] || {};
    var all = d.sources.map(function (x) { return x.path + (x.line ? ":" + x.line : ""); }).join("\n");
    return '<a class="dp-src" href="' + esc(sourceUrl(project, s)) + '" target="_blank" rel="noopener" data-tip="' + esc(all) + '">' +
      esc(text || ((s.path || "").split("/").pop() + (s.line ? ":" + s.line : ""))) +
      (d.sources.length > 1 ? " +" + (d.sources.length - 1) : "") + "</a>";
  }
  function depId(d) { return "dep-" + slug(d.ecosystem + "-" + d.name + "-" + (d.version || d.constraint || "")); }
  function projectHref(pname, hash) { return "?project=" + encodeURIComponent(pname) + (hash ? "#" + hash : ""); }
  function navLink(href, html, cls) {
    return '<a data-nav href="' + esc(href) + '"' + (cls ? ' class="' + cls + '"' : "") + ">" + html + "</a>";
  }

  // --- state and routing ---------------------------------------------------

  var DATA_SET = null;
  var state = { view: "projects", project: null, eco: [], bad: false, indirect: false, q: "", limit: PAGE_ROWS };

  function readUrl() {
    state = { view: "projects", project: null, eco: [], bad: false, indirect: false, q: "", limit: PAGE_ROWS };
    try {
      var q = new URLSearchParams(location.search);
      if (["projects", "upgrades", "internal", "lifecycle", "inventory"].indexOf(q.get("view")) >= 0) state.view = q.get("view");
      state.project = q.get("project");
      if (q.get("eco")) state.eco = q.get("eco").split(",").filter(function (e) { return ECO_LABEL[e]; });
      state.bad = q.get("attention") === "1";
      state.indirect = q.get("indirect") === "1";
      state.q = q.get("q") || "";
    } catch (e) { /* keep the defaults */ }
  }
  function urlFor(changes) {
    var s = Object.assign({}, state, changes || {});
    var url = new URL(location.href);
    ["view", "project", "eco", "attention", "indirect", "q"].forEach(function (k) { url.searchParams.delete(k); });
    if (s.project) url.searchParams.set("project", s.project);
    else if (s.view !== "projects") url.searchParams.set("view", s.view);
    if (s.eco.length && (s.view === "upgrades" || s.view === "inventory")) url.searchParams.set("eco", s.eco.join(","));
    if (s.bad && s.view === "inventory") url.searchParams.set("attention", "1");
    if (s.indirect && s.view === "inventory") url.searchParams.set("indirect", "1");
    if (s.q && s.view === "inventory") url.searchParams.set("q", s.q);
    return url;
  }
  function replaceUrl() {
    try { var u = urlFor(); u.hash = location.hash; history.replaceState(null, "", u.toString()); } catch (e) { /* ignore */ }
  }
  function go(href) {
    try { history.pushState(null, "", href); } catch (e) { location.href = href; return; }
    route();
  }

  // --- the model -----------------------------------------------------------
  //
  // The collector decides every action, as it decides every verdict, so an
  // agent reading deps-actions.json and a person reading this page follow the
  // same work. Here each action's items are joined to the records they name.

  function prepare(data) {
    data.projects.forEach(function (p) {
      (p.dependencies || []).forEach(function (d) {
        Object.defineProperty(d, "_project", { value: p, enumerable: false });
        d.sources = d.sources || [];
      });
    });
    data.live = data.projects.filter(function (p) { return !p.error; });
    data.byName = {};
    data.projects.forEach(function (p) { data.byName[p.name] = p; });
    var join = function (actions) {
      return (actions || []).map(function (a) {
        a.evidence = a.evidence || [];
        a.items = a.items.map(function (i) {
          var p = data.byName[i.project];
          return { project: p, dep: p.dependencies[i.record], reason: i.reason || null, tier: i.tier, to: i.to };
        });
        return a;
      });
    };
    data.actions = join(data.actions);
    data.live.forEach(function (p) { p._actions = join(p.actions); });
  }

  function acceptedNow(d) { return d.accepted && !d.accepted.lapsed; }
  function exploited(d) { return (d.vulnerabilities || []).some(function (v) { return v.exploited; }); }
  function pct(x) { return x >= 0.1 ? Math.round(x * 100) + "%" : x >= 0.01 ? (x * 100).toFixed(1) + "%" : "<1%"; }

  function attention(d) {
    if (acceptedNow(d)) return false;
    if (isIndirect(d)) return d.status === "vulnerable";
    return rank(d.level) >= rank("info");
  }

  function ageText(iso) { var d = daysSince(iso); return d == null ? "" : "open " + fmtSpan(d); }
  function slaText(w) {
    if (!w) return "";
    var left = -daysSince(w.due);
    return left < 0 ? fmtSpan(-left) + " past due" : "due in " + fmtSpan(left);
  }
  function productName(lc) { return (PRODUCT[lc.product] || lc.product) + " " + lc.cycle; }
  function compatText(c) {
    var t = c.target || {}, dist = c.distribution || {};
    var s = [c.with + " validates " + ((c.validated || []).join(", ") || "none")];
    if (t.line) s.push("move to " + t.line + (t.lts ? " LTS" : "") + (t.eol ? " (to " + t.eol + ")" : "") + (t.min_firecracker ? ", needs " + t.min_firecracker + "+" : ""));
    if (dist.name && !dist.has_target) s.push(dist.name + " ships only " + dist.line);
    return s.join(" · ");
  }
  // One step of an action: a command, an edit at a file and line, or a task in words.
  function stepText(s) {
    if (s.run) return "<code>" + esc((s.cwd && s.cwd !== "." ? "cd " + s.cwd + " && " : "") + s.run) + "</code>";
    if (s.edit) return esc(s.text.charAt(0).toUpperCase() + s.text.slice(1)) + ' <span class="dp-muted">at</span> <code>' + esc(s.edit + (s.line ? ":" + s.line : "")) + "</code>";
    return esc(s.do || "");
  }
  // What an action says about itself, with a release's age read at view time.
  function whyText(a) {
    var released = a.released ? (a.released.version ? a.released.version + " " : "") + "released " + fmtAgo(a.released.date) : "";
    return [a.result, a.why, released].filter(Boolean).join(" · ");
  }

  // --- pieces shared by several views ---------------------------------------

  function meter(levels, total) {
    // One bar, one colour: the status tokens are too close to one another to
    // sit side by side (red and amber fail a deuteranope, severe and warning
    // fail everyone). The fill is the share that needs attention, in the
    // colour of the worst of it, and the counts beside it say the rest.
    var flagged = FLAGGED.reduce(function (n, l) { return n + (levels[l] || 0); }, 0);
    var worst = FLAGGED.filter(function (l) { return levels[l]; })[0] || "good";
    var share = total ? (flagged || (levels.good ? total : 0)) / total : 0;
    var label = flagged ? flagged + " of " + total + " need attention" : (levels.good || 0) + " of " + total + " current";
    return '<span class="dp-meter dp-meter--' + worst + '" role="img" aria-label="' + esc(label) + '" data-tip="' + esc(label) +
      '"><i style="width:' + Math.max(share ? 6 : 0, Math.round(share * 100)) + '%"></i></span>';
  }
  // What a project declares in one ecosystem, plus any indirect record that
  // needs attention: the indirect rest is counted, not measured.
  function declaredLevels(p, eco) {
    var out = { total: 0, indirect: 0, levels: {}, tiers: {} };
    p.dependencies.forEach(function (d) {
      if (d.ecosystem !== eco) return;
      if (isIndirect(d)) { out.indirect++; if (!attention(d)) return; }
      out.total++;
      out.levels[d.level] = (out.levels[d.level] || 0) + 1;
      if (attention(d) && d.tier) out.tiers[d.tier] = (out.tiers[d.tier] || 0) + 1;
    });
    return out;
  }

  function counts(tiers) {
    var parts = TIERS.filter(function (t) { return tiers[t.id]; }).map(function (t) {
      return "<span><b>" + tiers[t.id] + "</b> " + esc(t.title.toLowerCase()) + "</span>";
    });
    return parts.length ? parts.join(" · ") : '<span class="dp-muted">nothing to do</span>';
  }

  // A project's badge: the most urgent tier it has work in, coloured by how
  // severe that work is.
  function projectChip(p, actions) {
    var c = tierCounts(actions);
    var lvl = c.fix ? actions.filter(function (a) { return a.tier === "fix"; }).reduce(function (l, a) { return rank(a.level) > rank(l) ? a.level : l; }, "serious")
      : c.plan ? "warning" : c.routine ? "info" : "good";
    var t = c.fix ? "Fix now" : c.plan ? "Plan" : c.routine ? "Routine" : "Current";
    return { level: lvl, html: chip(lvl, STATE_ICON[lvl], t) };
  }

  function sparkline(values, label) {
    if (values.length < 2) return "";
    var w = 72, h = 18, max = Math.max.apply(null, values), min = Math.min.apply(null, values);
    var span = max - min || 1;
    var pts = values.map(function (v, i) {
      return (i / (values.length - 1) * (w - 4) + 2).toFixed(1) + "," + (h - 3 - (v - min) / span * (h - 6)).toFixed(1);
    });
    var last = pts[pts.length - 1].split(",");
    return '<svg class="dp-spark" viewBox="0 0 ' + w + " " + h + '" role="img" aria-label="' + esc(label) + '">' +
      '<polyline points="' + pts.join(" ") + '"/><circle cx="' + last[0] + '" cy="' + last[1] + '" r="2.5"/></svg>';
  }

  function ecoChips() {
    var live = DATA_SET.live;
    return '<span class="dp-chips" role="group" aria-label="Kinds">' + ECO_ORDER.filter(function (e) {
      return live.some(function (p) { return p.summary.by_ecosystem[e]; });
    }).map(function (e) {
      return '<button class="dp-chip" data-component="Chip" data-eco="' + e + '" aria-pressed="' + (state.eco.indexOf(e) >= 0) + '">' +
        '<span data-chip-label>' + esc(ECO_SHORT[e]) + "</span></button>";
    }).join("") + "</span>";
  }

  // --- action cards --------------------------------------------------------

  var ROWS_SHOWN = 6;

  function actionItems(a) {
    if (a.kind === "actions") {
      var by = {};
      a.items.forEach(function (i) { (by[i.dep.name] = by[i.dep.name] || []).push(i); });
      return '<div class="dp-action__scroll"><table class="dp-action__items"><thead><tr><th class="text-label-upper">Action</th><th class="text-label-upper">Pinned in</th><th class="text-label-upper">Move to</th></tr></thead><tbody>' +
        Object.keys(by).sort().map(function (n) {
          var its = by[n], d0 = its[0].dep;
          return '<tr><td class="dp-action__dep">' + esc(n) + "</td><td>" + its.map(function (i) {
            return navLink(projectHref(i.project.name, depId(i.dep)), esc(i.project.name) + " <code>" + esc(i.dep.version) + "</code>", "dp-proj");
          }).join(" ") + '</td><td class="dp-action__to"><code>' + esc(target(d0) ? "v" + String(target(d0)).split(".")[0] : "") + "</code></td></tr>";
        }).join("") + "</tbody></table></div>";
    }
    var showName = uniq(a.items.map(function (i) { return i.dep.name; })).length > 1;
    var rows = a.items.slice(0, ROWS_SHOWN).map(function (i) {
      var d = i.dep, t = i.to;
      var verdict = i.reason === "unlisted" ? chip("info", "unknown", "not in policy")
        : i.reason === "license" ? chip(d.license.verdict === "denied" ? "serious" : "warning", "deprecated", "license " + d.license.verdict)
        : i.reason === "abandoned" ? chip("warning", "deprecated", "abandoned") : chip(d.level, d.status);
      return "<tr><td>" + navLink(projectHref(i.project.name, depId(d)), esc(i.project.name)) + "</td>" +
        (showName ? '<td class="dp-action__dep">' + esc(clip(name(d), 36)) + "</td>" : "") +
        '<td class="dp-action__to"><code>' + esc(shortVersion(d)) + "</code>" + (t && !i.reason ? " → <code>" + esc(clip(t, 20)) + "</code>" : "") + "</td>" +
        "<td>" + verdict + "</td><td>" + sourceLink(i.project, d) + "</td></tr>";
    }).join("");
    var more = a.items.length > ROWS_SHOWN
      ? '<tr><td colspan="5">' + navLink("?view=inventory&attention=1&indirect=1&q=" + encodeURIComponent(a.items[ROWS_SHOWN].dep.name),
        "and " + (a.items.length - ROWS_SHOWN) + " more in the inventory →") + "</td></tr>" : "";
    return '<div class="dp-action__scroll"><table class="dp-action__items"><tbody>' + rows + more + "</tbody></table></div>";
  }

  function actionMeta(a) {
    var left = a.ends ? -daysSince(a.ends) : null;
    var when = a.sla ? '<span class="dp-due dp-due--' + a.sla.state + '" data-tip="' + esc("open since " + fmtDate(a.sla.since) +
      ", fix by " + fmtDate(a.sla.due) + " (" + a.sla.days + "-day policy in deps-config.json)") + '">' + esc(slaText(a.sla)) + "</span>"
      : left != null && left > 0 ? '<span class="dp-age" data-tip="' + esc("support ends " + fmtDate(a.ends)) + '">ends in ' + esc(fmtSpan(left)) + "</span>"
      : a.opened ? '<span class="dp-age" data-tip="' + esc("the gap opened " + fmtDate(a.opened) + ": the advisory, end of life or first sighting it dates from") + '">' +
        esc(ageText(a.opened)) + "</span>" : "";
    return '<span class="dp-type">' + esc(TYPE_LABEL[a.type]) + "</span>" + when;
  }

  function actionCard(a) {
    var vulns = [];
    a.items.forEach(function (i) { (i.dep.vulnerabilities || []).forEach(function (v) { if (!vulns.some(function (x) { return x.id === v.id; })) vulns.push(v); }); });
    var refs = vulns.slice(0, 3).map(function (v) {
      return '<a href="' + esc(v.url) + '" target="_blank" rel="noopener" data-tip="' + esc(v.severity + ": " + (v.summary || v.id)) + '">' + esc(v.id) + "</a>";
    });
    if (vulns.length > 3) refs.push("+" + (vulns.length - 3));
    var up = ["lock", "rebuild", "actions", "routine", "devtools", "sync", "license", "unlisted", "replace"].indexOf(a.kind) >= 0 ? null
      : (a.items[0].dep.upstream || {}).url || (a.items[0].dep.lifecycle || {}).url;
    var how = a.steps && a.steps.length
      ? '<div class="dp-how dp-how--steps"><span class="text-label-upper">Steps</span><ol class="dp-steps">' +
        a.steps.map(function (s) { return "<li>" + stepText(s) + "</li>"; }).join("") + "</ol></div>"
      : a.how ? '<div class="dp-how"><span class="text-label-upper">How</span><code>' + esc(a.how) + "</code></div>" : "";
    var evidence = a.evidence.length ? '<p class="dp-action__evidence"><span class="text-label-upper">Also</span> ' + esc(a.evidence.join(" · ")) + "</p>" : "";
    // The actions to make first, linked where they are shown.
    var first = (a.requires || []).map(function (id) {
      var other = (state.project ? (DATA_SET.byName[state.project]._actions || []) : DATA_SET.actions).filter(function (x) { return x.id === id; })[0];
      return other ? '<a href="' + (state.project ? "#" : "?view=upgrades#") + esc(id) + '">' + esc(other.title) + "</a>" : "";
    }).filter(Boolean);
    evidence += first.length ? '<p class="dp-action__evidence"><span class="text-label-upper">First</span> ' + first.join(" · ") + "</p>" : "";
    // A choice for a person to make: the collector recommends one and picks none.
    var options = a.options ? '<div class="dp-options"><span class="text-label-upper">Options</span><ul>' + a.options.map(function (o) {
      return "<li>" + (o.recommended ? chip("good", "current", "recommended") + " " : "") + esc(o.text) + "</li>";
    }).join("") + "</ul></div>" : "";
    return '<article class="ci-card dp-action dp-card--' + esc(a.level) + '" id="' + esc(a.id) + '" data-eco="' +
      esc(uniq(a.items.map(function (i) { return i.dep.ecosystem; })).join(" ")) + '">' +
      '<div class="dp-action__meta">' + actionMeta(a) + "</div>" +
      '<h3 class="dp-action__title">' + esc(a.title) + "</h3>" +
      '<p class="dp-action__why">' + esc(whyText(a)) + "</p>" +
      actionItems(a) + evidence + how + options +
      ((refs.length || up) ? '<footer class="ci-card__foot">' + (refs.length ? "Advisories " + refs.join(" · ") : "") +
        (up ? (refs.length ? " · " : "") + '<a href="' + esc(up) + '" target="_blank" rel="noopener">upstream ' + ICON.external + "</a>" : "") + "</footer>" : "") +
      "</article>";
  }

  // A routine action as one line that opens onto its card: routine work is
  // batched, so it is scanned as a list and read one at a time.
  function actionRow(a) {
    return '<details class="dp-action-row" id="' + esc(a.id) + '" data-eco="' + esc(uniq(a.items.map(function (i) { return i.dep.ecosystem; })).join(" ")) + '">' +
      "<summary>" + ICON.chevron + '<span class="dp-action-row__title">' + esc(a.title) + "</span>" + actionMeta(a) +
      '<span class="dp-muted dp-action-row__result">' + esc(a.result || "") + "</span></summary>" +
      '<div class="dp-action-row__body">' + actionCard(a).replace(' id="' + esc(a.id) + '"', "") + "</div></details>";
  }

  function tierSections(actions) {
    return TIERS.map(function (t) {
      var list = actions.filter(function (a) { return a.tier === t.id; });
      var body = !list.length ? '<p class="dp-empty">' + (t.id === "fix" ? "Nothing to fix now." : "Nothing here.") + "</p>"
        : t.id === "routine" ? '<div class="dp-action-rows">' + list.map(actionRow).join("") + "</div>"
        : '<div class="ci-grid dp-grid--actions">' + list.map(actionCard).join("") + "</div>";
      return '<section class="dp-tier" id="tier-' + t.id + '"><header class="dp-tier__head">' + chip(t.level, STATE_ICON[t.level], t.title) +
        '<span class="dp-tier__count">' + plural(list.length, "action") + "</span>" +
        '<span class="dp-tier__blurb">' + esc(t.blurb) + "</span></header>" + body + "</section>";
    }).join("");
  }

  function tierCounts(actions) {
    var c = {};
    TIERS.forEach(function (t) { c[t.id] = actions.filter(function (a) { return a.tier === t.id; }).length; });
    return c;
  }

  // --- dependency detail: the expandable row everything links to ------------

  function detail(p, d) {
    var up = d.upstream || {}, lc = d.lifecycle, rows = [];
    var kv = function (k, v) { if (v) rows.push("<dt>" + esc(k) + "</dt><dd>" + v + "</dd>"); };
    var ext = function (url, text) { return url ? ' <a href="' + esc(url) + '" target="_blank" rel="noopener">' + esc(text) + " " + ICON.external + "</a>" : ""; };
    kv("Declared in", d.sources.map(function (s) {
      return '<a href="' + esc(sourceUrl(p, s)) + '" target="_blank" rel="noopener">' + esc(s.path + (s.line ? ":" + s.line : "")) + "</a>";
    }).join("<br>"));
    kv("Pinned", "<code>" + esc(d.version == null ? "nothing pinned" : d.version) + "</code>" +
      (d.constraint ? ' <span class="dp-muted">as <code>' + esc(d.constraint) + "</code></span>" : "") +
      (up.effective ? ' <span class="dp-muted">resolves to <code>' + esc(up.effective) + "</code></span>" : "") +
      (up.version_date ? ' <span class="dp-muted">· released ' + esc(fmtAgo(up.version_date)) + "</span>" : ""));
    if (up.latest) kv("Newest", "<code>" + esc(up.latest) + "</code>" + (up.latest_date ? ' <span class="dp-muted">· released ' + esc(fmtAgo(up.latest_date)) + "</span>" : "") +
      (up.line_latest ? ' <span class="dp-muted">· newest on its line <code>' + esc(up.line_latest) + "</code></span>" : "") +
      (up.next_major ? ' <span class="dp-muted">· next major <code>' + esc(up.next_major) + "</code></span>" : "") + ext(up.url, "releases"));
    if (d.opened) kv("Open since", esc(fmtDate(d.opened) + " (" + fmtSpan(daysSince(d.opened)) + ")"));
    if (d.sla) kv("Fix by", esc(fmtDate(d.sla.due) + " · " + slaText(d.sla) + " (" + d.sla.days + "-day policy)"));
    if (d.vulnerabilities) kv("Advisories", d.vulnerabilities.map(function (v) {
      var reach = v.reachable ? ' <span class="dp-muted">· ' + esc({ called: "called by this project's code", imported: "package imported, vulnerable code not called",
        required: "module required, package not imported", "not-in-build": "not in what the project builds" }[v.reachable] || v.reachable) + "</span>" : "";
      var threat = (v.exploited ? ' <span class="dp-exploited">exploited in the wild since ' + esc(v.exploited) + " (CISA KEV)</span>" : "") +
        (v.epss ? ' <span class="dp-muted">· EPSS ' + esc(pct(v.epss.probability)) + ", higher than " + esc(Math.floor(v.epss.percentile * 100)) + "% of CVEs</span>" : "");
      return '<span class="dp-sev dp-sev--' + esc(v.severity) + '">' + esc(v.severity) + '</span> <a href="' + esc(v.url) + '" target="_blank" rel="noopener">' +
        esc(v.id) + "</a> " + esc(v.summary || "") + (v.fixed ? ' <span class="dp-muted">· fixed in ' + esc(v.fixed) + "</span>" : "") + reach + threat;
    }).join("<br>"));
    if (d.fix && d.vulnerabilities) kv("Fix", "<code>" + esc(d.fix) + "</code>" + (d.fix_advisories ? ' <span class="dp-muted">· steps past ' + esc(d.fix_advisories.join(", ")) +
      ", which affect" + (d.fix_advisories.length === 1 ? "s" : "") + " the first fixed release</span>" : "") +
      (d.fix_partial ? ' <span class="dp-muted">· no release fixes every advisory yet</span>' : ""));
    if (lc) kv("Release line", esc(productName(lc)) + " · " + esc({ active: "supported", maintenance: "security fixes only", "eol-soon": "support closing", eol: "past its end of life", unknown: "no support data" }[lc.phase] || lc.phase) +
      (lc.eol ? " · " + (lc.eol_is_floor ? "supported at least until " : "end of life ") + esc(fmtDate(lc.eol)) : "") + ext(lc.url, "policy"));
    if (d.compat) kv("Compatibility", esc(compatText(d.compat)) + ext(d.compat.url, "policy"));
    if (d.license) kv("License", (d.license.expression ? "<code>" + esc(d.license.expression) + "</code> · " : "") +
      esc({ allowed: "allowed by the policy", aggregate: "a separate program the image redistributes", "not-shipped": "not in what the project ships",
        unknown: "no license found", review: "the policy asks for a review", denied: "the policy denies it" }[d.license.verdict] || d.license.verdict) +
      (d.license.found ? '<br><span class="dp-muted">its files also carry ' + esc(d.license.found.join(", ")) + " (" + esc(d.license.found_verdict) + ")</span>" +
        ext(d.license.found_url, "scan") : "") +
      (d.license.score != null ? '<br><span class="dp-muted">ClearlyDefined license score ' + esc(d.license.score) + "/100</span>" : ""));
    if (d.purl) kv("Package URL", "<code>" + esc(d.purl) + "</code>");
    if (d.signals) kv("Supply chain", d.signals.map(function (x) { return esc(x.text); }).join("<br>"));
    var sc = (d.repo_signals || {}).scorecard;
    if (sc && sc.score != null) kv("Scorecard", esc(sc.score + "/10 on " + sc.date) + ext("https://scorecard.dev/viewer/?uri=" + (d.upstream_repo || ""), "report"));
    if (d.provenance) kv("Provenance", "published with a build attestation");
    if (d.runner) kv("Runner image", esc(d.runner.image + " · " + d.runner.arch + (d.runner.deprecated ? " · deprecated" : "") + (d.runner.preview ? " · preview" : "")));
    if (d.lag) kv("Behind", d.lag.builds != null ? plural(d.lag.builds, "newer build") :
      plural(d.lag.commits, "commit") + (d.lag.commits_touching != null ? " (" + d.lag.commits_touching + " in " + esc(d.lag.paths.join(", ")) + ")" : "") +
      (d.lag.days ? ", " + esc(fmtSpan(d.lag.days)) : "") + ext(up.url, "compare"));
    if (d.libyears) kv("Libyears", d.libyears.toFixed(2) + ' <span class="dp-muted">years between the pinned release and the newest</span>');
    if (d.deprecated) kv("Deprecated", esc(d.deprecated));
    if (d.accepted) kv("Accepted", esc(d.accepted.reason.replace(/_/g, " ") + (d.accepted.note ? ": " + d.accepted.note : "") +
      (d.accepted.until ? (d.accepted.lapsed ? " · lapsed " : " · until ") + fmtDate(d.accepted.until) : "")));
    if (d.note) kv("Note", esc(d.note));
    if (d.error) kv("Not resolved", esc(d.error));
    var how = attention(d) ? d.how : null;
    if (how) kv("How", "<code>" + esc(how) + "</code>");
    return '<dl class="dp-kv">' + rows.join("") + "</dl>";
  }

  function depRows(p, deps, withProject) {
    return deps.map(function (d) {
      var up = d.upstream || {}, t = target(d), id = depId(d) + (withProject ? "-" + slug(p.name) : "");
      var ver = "<code>" + esc(shortVersion(d)) + "</code>" + (up.effective ? '<span class="dp-sub">' + esc(clip(up.effective, 24)) + "</span>" : "");
      var vcount = (d.vulnerabilities || []).length;
      return '<tr class="dp-dep" id="' + esc(id) + '" tabindex="0" aria-expanded="false" data-eco="' + esc(d.ecosystem) + '" data-level="' + esc(d.level) +
        '" data-text="' + esc([p.name, d.name, d.label, d.version, d.constraint, up.latest, up.effective, d.status, d.scope, srcPath(d)].join(" ").toLowerCase()) + '">' +
        '<td class="dp-dep__name">' + ICON.chevron + "<span><b>" + esc(clip(name(d), 60)) + '</b><span class="dp-sub">' + esc(ECO_SHORT[d.ecosystem]) + " · " + esc(d.scope) +
        (d.dev ? " · dev" : "") + "</span></span></td>" +
        (withProject ? "<td>" + navLink(projectHref(p.name), esc(p.name)) + "</td>" : "") +
        "<td>" + ver + "</td>" +
        "<td>" + (t ? "<code>" + esc(clip(t, 24)) + "</code>" + (up.latest_date ? '<span class="dp-sub">' + esc(fmtAgo(up.latest_date)) + "</span>" : "") : '<span class="dp-muted">' + (d.error ? "not resolved" : "—") + "</span>") + "</td>" +
        "<td>" + chip(d.level, d.status) + '<span class="dp-sub">' + esc([vcount ? plural(vcount, "advisory", "advisories") : "",
          exploited(d) ? "exploited" : "",
          d.reachability && d.reachability !== "called" && vcount ? "not called" : "",
          (d.license || {}).verdict === "denied" || (d.license || {}).verdict === "review" ? "license " + d.license.verdict : "",
          (d.license || {}).found ? "license found in files" : "",
          d.sla && d.sla.state !== "within" ? slaText(d.sla) : "", acceptedNow(d) ? "accepted" : ""].filter(Boolean).join(" · ")) + "</span></td>" +
        "<td>" + sourceLink(p, d) + "</td></tr>" +
        '<tr class="dp-dep__detail" hidden><td colspan="' + (withProject ? 6 : 5) + '">' + "" + "</td></tr>";
    }).join("");
  }
  function depTable(p, deps, withProject) {
    var heads = ["Dependency"].concat(withProject ? ["Project"] : [], ["Pinned", "Newest", "Status", "Where"]);
    return '<div class="ci-table__wrap"><table class="ci-table dp-deps' + (withProject ? " dp-deps--wide" : "") + '"><thead><tr>' +
      heads.map(function (h) { return '<th class="text-label-upper">' + h + "</th>"; }).join("") + "</tr></thead><tbody>" +
      depRows(p, deps, withProject) + "</tbody></table></div>";
  }

  // --- projects (the default view) --------------------------------------------

  function renderProjects() {
    var data = DATA_SET;
    var next = data.actions[0];
    var then = data.actions.slice(1, 6);
    var c = tierCounts(data.actions);
    var html = '<section class="dp-start" aria-labelledby="start-title"><div class="dp-start__head"><h2 class="dp-h2" id="start-title">Next action</h2>' +
      '<span class="dp-muted">' + TIERS.map(function (t) { return navLink("?view=upgrades#tier-" + t.id, "<b>" + c[t.id] + "</b> " + esc(t.title.toLowerCase())); }).join(" · ") +
      "</span></div>" + (next ? '<div class="dp-start__next">' + actionCard(next) + "</div>" +
        (then.length ? '<ol class="dp-start__list" start="2">' + then.map(function (a) {
          return '<li><span class="dp-start__chip">' + chip(a.level, STATE_ICON[a.level] || a.status, TIERS[TIER_INDEX[a.tier]].title) + "</span>" +
            navLink("?view=upgrades#" + a.id, esc(a.title), "dp-start__title") + '<span class="dp-start__where">' + actionMeta(a) + "</span></li>";
        }).join("") + "</ol>" : "") +
        (data.actions.length > 6 ? '<p class="dp-start__more">' + navLink("?view=upgrades", (data.actions.length - 6) + " more actions →") + "</p>" : "")
        : '<p class="dp-empty">Nothing to do across the org. Every tracked dependency is current, supported and clean.</p>') + "</section>";
    var withWork = [], clear = [];
    data.projects.forEach(function (p) {
      if (p.error) { withWork.push(p); return; }
      var own = p._actions;
      if (own.some(function (a) { return a.tier !== "routine"; })) withWork.push(p); else clear.push(p);
    });
    withWork.sort(function (a, b) {
      var ca = a._actions ? tierCounts(a._actions) : {}, cb = b._actions ? tierCounts(b._actions) : {};
      return (a.error ? 1 : 0) - (b.error ? 1 : 0) || (b._actions ? rank(projectChip(b, b._actions).level) : 0) - (a._actions ? rank(projectChip(a, a._actions).level) : 0) ||
        (cb.fix || 0) - (ca.fix || 0) || (cb.plan || 0) - (ca.plan || 0) || a.name.localeCompare(b.name);
    });
    html += '<h2 class="dp-h2">Projects</h2><div class="ci-grid" id="cards">' + withWork.map(card).join("") + "</div>";
    if (clear.length) {
      html += '<p class="dp-clear">' + chip("good", "current", "clear") + " Nothing to fix or plan in " +
        clear.map(function (p) { return navLink(projectHref(p.name), esc(p.name)) + ' <span class="dp-muted">(' + plural(p._actions.length, "routine action") + ")</span>"; }).join(", ") + ".</p>";
    }
    return html;
  }

  function card(p) {
    if (p.error) {
      return '<article class="ci-card ci-card--unreachable" data-state="unreachable"><header class="ci-card__head"><h2>' +
        esc(p.name) + "</h2>" + chip("idle", "unreachable", "not read") + "</header>" +
        '<p class="ci-card__desc">The collector could not read this repository: ' + esc(p.error) + "</p></article>";
    }
    var s = p.summary;
    var own = p._actions || [];
    var c = tierCounts(own);
    var badge = projectChip(p, own), state = badge.level;
    var rows = ECO_ORDER.filter(function (e) { return s.by_ecosystem[e]; }).map(function (e) {
      var b = declaredLevels(p, e);
      return '<a data-nav class="dp-eco-row" href="' + esc(projectHref(p.name, "eco-" + e)) + '"><span class="dp-eco-row__name">' + esc(ECO_LABEL[e]) +
        '</span><span class="dp-eco-row__count" data-tip="' + esc(b.total + " declared" + (b.indirect ? ", " + b.indirect + " indirect" : "")) + '">' + b.total + "</span>" +
        meter(b.levels, b.total) + '<span class="dp-eco-row__note">' + counts(b.tiers) + "</span></a>";
    }).join("");
    var top = own.slice(0, 3).map(function (a) {
      return "<li>" + chip(a.level, STATE_ICON[a.level] || a.status, TIERS[TIER_INDEX[a.tier]].title) + " " +
        navLink(projectHref(p.name, a.id), esc(a.title), "dp-top__name") + (a.sla && a.sla.state !== "within" ? ' <span class="dp-due dp-due--' + a.sla.state + '">' + esc(slaText(a.sla)) + "</span>" : "") + "</li>";
    }).join("");
    var committed = p.repo.committed ? " · committed " + fmtAgo(p.repo.committed) : "";
    return '<article class="ci-card dp-card--' + esc(state) + '" data-state="' + esc(state) + '">' +
      '<header class="ci-card__head"><h2>' + navLink(projectHref(p.name), esc(p.name)) + "</h2>" +
      badge.html + "</header>" +
      '<p class="ci-card__desc">' + esc(p.repo.description) + "</p>" +
      '<div class="dp-card__stats">' + TIERS.map(function (t) { return navLink(projectHref(p.name, "tier-" + t.id), "<b>" + c[t.id] + "</b> " + esc(t.title.toLowerCase())); }).join("") + "</div>" +
      (top ? '<ul class="dp-top dp-top--first">' + top + (own.length > 3 ? '<li class="dp-muted">' + navLink(projectHref(p.name, "what-to-do"), (own.length - 3) + " more →") + "</li>" : "") + "</ul>" : "") +
      '<div class="dp-eco-rows">' + rows + "</div>" +
      '<footer class="ci-card__foot dp-card__foot"><span>' + esc(p.repo.branch || "") + " @ <code>" + esc((p.repo.sha || "").slice(0, 7)) + "</code>" + esc(committed) + "</span>" +
      navLink(projectHref(p.name), "Details →") + "</footer></article>";
  }

  // --- one project -----------------------------------------------------------

  function renderProject(pname) {
    var p = DATA_SET.byName[pname];
    if (!p) return '<p class="ci-note ci-note--error">' + ICON.unreachable + "<span>No project named <b>" + esc(pname) + "</b>. " + navLink("?", "All projects") + "</span></p>";
    var back = '<div class="dp-back">' + navLink("?", ICON.back + "All projects") + "</div>";
    if (p.error) return back + card(p);
    var s = p.summary;
    var actions = p._actions;
    var c = tierCounts(actions);
    var vulns = p.dependencies.filter(function (d) { return d.status === "vulnerable"; });
    var breached = actions.filter(function (a) { return a.sla && a.sla.state === "breached"; }).length;
    var verdict = c.fix ? plural(c.fix, "action") + " to fix now" : c.plan ? "nothing to fix now, " + plural(c.plan, "action") + " to plan" : "nothing to fix or plan";
    var head = '<section class="ci-hero dp-project-head"><div><div class="dp-project-head__title"><h2>' + esc(p.name) + "</h2>" +
      projectChip(p, actions).html + "</div>" +
      '<div class="dp-verdict">' + esc(verdict[0].toUpperCase() + verdict.slice(1)) + (breached ? " · " + plural(breached, "fix-by date") + " passed" : "") +
      ' <span class="dp-muted">· read ' + esc(fmtAgo(DATA_SET.generated)) + "</span></div>" +
      '<div class="ci-hero__note">' + esc(p.repo.description) + "</div>" +
      '<div class="ci-hero__note"><a href="' + esc(p.repo.url) + '" target="_blank" rel="noopener">' + esc(p.repo.full_name) + " " + ICON.external + "</a> · " +
      esc(p.repo.branch) + " @ <code>" + esc((p.repo.sha || "").slice(0, 7)) + "</code> · committed " + esc(fmtAgo(p.repo.committed)) + "</div></div>" +
      '<div class="ci-hero__tiles dp-tiles">' + TIERS.map(function (t) {
        return '<a class="dp-tile" href="#tier-' + t.id + '"><div class="ci-stat__value">' + c[t.id] + '</div><div class="ci-stat__label text-label-upper">' + esc(t.title) + "</div></a>";
      }).join("") + "</div></section>";

    var html = back + head;
    html += '<section class="dp-section" id="what-to-do"><h2 class="dp-h2">What to do</h2>' + tierSections(actions) + "</section>";
    // Reference below the actions: each opens on its own, one level deep.
    if (vulns.length) html += reference("vulnerabilities", "Advisories", plural(uniq([].concat.apply([], vulns.map(function (d) {
      return d.vulnerabilities.map(function (v) { return v.id; }); }))).length, "advisory", "advisories") + " on " + plural(vulns.length, "dependency", "dependencies"),
      depTable(p, vulns.sort(function (a, b) { return rank(b.level) - rank(a.level); })));
    var licensed = p.dependencies.filter(function (d) { return d.license && (d.license.verdict === "denied" || d.license.verdict === "review" || d.license.found || (d.license.verdict === "unknown" && !isIndirect(d))); });
    var lv = s.licenses || {};
    html += reference("licenses", "Licenses", ["allowed", "aggregate", "review", "denied", "unknown", "not-shipped"].filter(function (k) { return lv[k]; })
      .map(function (k) { return lv[k] + " " + k.replace("-", " "); }).join(" · ") || "no license data",
      licensed.length ? depTable(p, licensed) : '<p class="dp-empty">Every license the policy grades is allowed.</p>');
    var flagged = p.dependencies.filter(function (d) { return (d.signals || []).length && !isIndirect(d); });
    if (flagged.length) html += reference("supply-chain", "Supply chain", plural(flagged.length, "dependency", "dependencies") + " with a signal", depTable(p, flagged));
    var internal = internalCard(p, true);
    if (internal) html += reference("internal", "Internal pins", "pins on siblings, and who pins " + p.name, internal);
    var lc = DATA_SET.lifecycle.filter(function (r) { return r.projects.indexOf(p.name) >= 0; });
    if (lc.length) html += reference("lifecycle", "Release lines", plural(lc.length, "release line"), timeline(lc));
    var groups = ECO_ORDER.filter(function (e) { return s.by_ecosystem[e]; }).map(function (e) {
      var deps = p.dependencies.filter(function (d) { return d.ecosystem === e; });
      var direct = deps.filter(function (d) { return !isIndirect(d); }), indirect = deps.filter(isIndirect);
      var b = declaredLevels(p, e);
      return '<details class="dp-group" id="eco-' + e + '"><summary>' + ICON.chevron + "<b>" + esc(ECO_LABEL[e]) + '</b><span class="dp-muted">' +
        direct.length + " declared" + (indirect.length ? " · " + indirect.length + " indirect" : "") + "</span>" + meter(b.levels, b.total) +
        '<span class="dp-eco-row__note">' + counts(b.tiers) + "</span></summary>" +
        (direct.length ? depTable(p, direct) : "") +
        (indirect.length ? '<div class="dp-more"><button class="ci-btn" data-indirect="' + e + '">Show ' + plural(indirect.length, "indirect dependency", "indirect dependencies") +
          '</button> <span class="dp-muted">kept current by their tools</span></div>' : "") + "</details>";
    }).join("");
    html += '<section class="dp-section" id="inventory"><h2 class="dp-h2">All dependencies</h2>' + groups + "</section>";
    return html;
  }

  function reference(id, title, summary, body) {
    return '<details class="dp-group dp-reference" id="' + id + '"><summary>' + ICON.chevron + "<b>" + esc(title) + '</b><span class="dp-muted">' + esc(summary) + "</span></summary>" +
      '<div class="dp-reference__body">' + body + "</div></details>";
  }

  // --- upgrades ----------------------------------------------------------------

  function renderUpgrades() {
    var actions = DATA_SET.actions;
    var tiles = TIERS.map(function (t) {
      var list = actions.filter(function (a) { return a.tier === t.id; });
      var projects = uniq([].concat.apply([], list.map(function (a) { return a.projects; })));
      var late = list.filter(function (a) { return a.sla && a.sla.state === "breached"; }).length;
      return '<a class="dp-tier-tile dp-card--' + (list.length ? t.level : "good") + '" href="#tier-' + t.id + '">' +
        '<span class="ci-stat__value">' + list.length + '</span><span class="text-label-upper">' + esc(t.title) + "</span>" +
        '<span class="dp-muted">' + (list.length ? plural(projects.length, "project") + (late ? " · " + late + " past due" : "") : "nothing") + "</span></a>";
    }).join("");
    var accepted = [];
    DATA_SET.live.forEach(function (p) { p.dependencies.forEach(function (d) { if (d.accepted) accepted.push({ p: p, d: d }); }); });
    var nextReview = accepted.map(function (x) { return x.d.accepted.until; }).filter(Boolean).sort()[0];
    return '<p class="dp-view__intro">Every change the dependencies call for, as work: fix now, plan, routine. The same change across ' +
      "projects is one card. A tag says what kind of work it is, and a date says when it is due.</p>" +
      '<div class="dp-tier-tiles">' + tiles + "</div>" +
      '<div class="ci-toolbar" id="filters">' + ecoChips() + (accepted.length ? '<span class="dp-muted dp-accepted-note">' +
        navLink("#accepted", plural(accepted.length, "accepted exception")) + (nextReview ? " · next review " + esc(fmtDate(nextReview)) : "") + "</span>" : "") + "</div>" +
      tierSections(actions) +
      (accepted.length ? reference("accepted", "Accepted", plural(accepted.length, "exception") + " recorded in deps-config.json",
        '<ul class="dp-untracked">' + accepted.map(function (x) {
          return "<li>" + navLink(projectHref(x.p.name, depId(x.d)), esc(x.p.name + ": " + name(x.d))) + ' <span class="dp-muted">· ' + esc(x.d.accepted.reason.replace(/_/g, " ") +
            (x.d.accepted.note ? ", " + x.d.accepted.note : "") + (x.d.accepted.until ? (x.d.accepted.lapsed ? ", lapsed " : ", until ") + fmtDate(x.d.accepted.until) : "")) + "</span></li>";
        }).join("") + "</ul>") : "");
  }

  // --- internal -------------------------------------------------------------

  function internalCard(p, embedded) {
    var out = (p.dependencies || []).filter(function (d) { return d.internal && (d.lag || d.provider); });
    var incoming = [];
    DATA_SET.live.forEach(function (q) {
      q.dependencies.forEach(function (d) { if (d.provider === p.name && (d.lag || d.error)) incoming.push({ project: q, dep: d }); });
    });
    if (!out.length && !incoming.length) return "";
    var lagText = function (d) {
      var l = d.lag || {};
      if (d.error) return '<span class="dp-muted">' + esc(clip(d.error, 50)) + "</span>";
      if (l.builds != null) return l.builds ? plural(l.builds, "newer build") + ", " + fmtSpan(l.days) : "newest build";
      var n = l.commits_touching != null ? l.commits_touching : l.commits;
      return n ? plural(n, "commit") + (l.commits_touching != null ? " in " + l.paths.join(", ") : "") + (l.days ? ", " + fmtSpan(l.days) : "") : "in sync";
    };
    var row = function (other, d) {
      var up = d.upstream || {};
      return '<li class="dp-pin">' + chip(d.level, d.status === "behind" ? "behind" : d.status, d.status === "behind" ? "behind" : d.status === "current" ? "in sync" : STATUS_LABEL[d.status]) +
        navLink(projectHref(other), esc(other === p.name ? other + " (itself)" : other), "dp-pin__who") + '<span class="dp-eco">' + esc(ECO_SHORT[d.ecosystem]) + "</span>" +
        '<span class="dp-pin__what">' + esc(clip(name(d), 40)) + "</span>" +
        '<span class="dp-pin__lag">' + lagText(d) + "</span>" +
        (up.url && d.lag ? '<a class="dp-pin__cmp" href="' + esc(up.url) + '" target="_blank" rel="noopener" data-tip="' + esc((d.lag.pinned || "") + " → " + (d.lag.head || "")) + '">compare ' + ICON.external + "</a>" : "") + "</li>";
    };
    var worst = out.reduce(function (l, d) { return rank(d.level) > rank(l) && d.status === "behind" ? d.level : l; }, "good");
    var behindOut = out.filter(function (d) { return d.status === "behind"; }).length;
    var behindIn = incoming.filter(function (i) { return i.dep.status === "behind"; }).length;
    // A pin in sync needs nothing, so it is named once rather than given a row.
    var block = function (title, list) {
      if (!list.length) return "";
      var lagging = list.filter(function (x) { return x.dep.status !== "current"; });
      var synced = list.filter(function (x) { return x.dep.status === "current"; });
      return '<div class="dp-pins"><span class="text-label-upper">' + title + "</span>" +
        (lagging.length ? "<ul>" + lagging.sort(function (x, y) { return rank(y.dep.level) - rank(x.dep.level); }).map(function (x) {
          return row(x.other, x.dep);
        }).join("") + "</ul>" : "") +
        (synced.length ? '<p class="dp-pins__sync">' + chip("good", "current", "in sync") + " " +
          uniq(synced.map(function (x) { return x.other; })).map(function (n) {
            return navLink(projectHref(n, "internal"), esc(n === p.name ? n + " (itself)" : n));
          }).join(", ") + "</p>" : "") + "</div>";
    };
    var body = block("Pins on siblings", out.map(function (d) { return { other: d.provider || "?", dep: d }; })) +
      block("Pinned by", incoming.map(function (i) { return { other: i.project.name, dep: i.dep }; }));
    var goBehind = out.some(function (d) { return d.status === "behind" && d.ecosystem === "go"; });
    if (embedded) return '<div class="dp-internal-embedded">' + body + (goBehind ? '<div class="dp-how"><span class="text-label-upper">How</span><code>make repin</code></div>' : "") + "</div>";
    return '<article class="ci-card dp-card--' + (behindOut ? worst : "good") + '" data-state="' + (behindOut ? worst : "good") + '">' +
      '<header class="ci-card__head"><h2>' + navLink(projectHref(p.name, "internal"), esc(p.name)) + "</h2>" +
      chip(behindOut ? worst : "good", behindOut ? "behind" : "current", behindOut ? plural(behindOut, "pin") + " behind" : "in sync") + "</header>" +
      '<p class="ci-card__desc">Pins ' + plural(uniq(out.map(function (d) { return d.provider; })).length, "sibling") + " · pinned by " +
      plural(uniq(incoming.map(function (i) { return i.project.name; })).length, "project") + (behindIn ? ", " + behindIn + " of those pins behind" : "") + "</p>" +
      body + (goBehind ? '<footer class="ci-card__foot"><span class="text-label-upper">How</span> <code>make repin</code> moves every Go tool pin to its newest commit</footer>' : "") + "</article>";
  }

  function renderInternal() {
    var cards = DATA_SET.live.map(function (p) { return { p: p, html: internalCard(p, false) }; }).filter(function (c) { return c.html; });
    cards.sort(function (a, b) {
      var la = a.p.dependencies.filter(function (d) { return d.status === "behind"; }).length;
      var lb = b.p.dependencies.filter(function (d) { return d.status === "behind"; }).length;
      return lb - la || a.p.name.localeCompare(b.p.name);
    });
    return '<p class="dp-view__intro">How far each project\'s pins on its siblings trail their default branches, and who pins it in turn. ' +
      "Go pseudo-versions, the org's npm dev builds, actions pinned by commit and image tiers all count.</p>" +
      '<div class="ci-grid" id="internal-cards">' + (cards.length ? cards.map(function (c) { return c.html; }).join("") : '<p class="dp-muted">No project pins another.</p>') + "</div>";
  }

  // --- lifecycle ---------------------------------------------------------------

  function phaseLevel(r) { return r.phase === "eol" ? "critical" : r.phase === "eol-soon" ? "serious" : r.phase === "maintenance" ? "warning" : r.phase === "unknown" ? "idle" : "good"; }
  function phaseStatus(r) { return r.phase === "eol" ? "eol" : r.phase === "eol-soon" ? "eol-soon" : r.phase === "maintenance" ? "floating" : r.phase === "unknown" ? "unknown" : "current"; }
  function phaseWords(r) {
    if (r.phase === "eol") return "ended " + fmtAgo(r.eol);
    if (!r.eol) return r.phase === "unknown" ? "no support data" : r.phase === "maintenance" ? "security fixes only" : "supported, no end date";
    if (r.eol_is_floor) return (r.phase === "eol-soon" ? "guaranteed only " : "supported at least ") + "until " + fmtDate(r.eol);
    if (r.phase === "eol-soon") return "ends " + fmtAgo(r.eol);
    if (r.phase === "maintenance") return "security fixes only, ends " + fmtAgo(r.eol);
    return "supported, ends " + fmtAgo(r.eol);
  }

  function timeline(rows) {
    var now = Date.now(), DAY = 864e5;
    var start = now - 365 * DAY, end = now + 3 * 365 * DAY;
    function pct(iso) {
      var t = Date.parse(iso);
      if (isNaN(t)) return null;
      return Math.max(0, Math.min(100, (t - start) / (end - start) * 100));
    }
    var ticks = "";
    for (var y = new Date(start).getUTCFullYear(); y <= new Date(end).getUTCFullYear() + 1; y++) {
      [0, 6].forEach(function (m) {
        var t = Date.UTC(y, m, 1);
        if (t < start || t > end || Math.abs(t - now) < 45 * DAY) return;
        ticks += '<span class="dp-tl__tick" style="left:' + pct(new Date(t).toISOString()) + '%">' + (m ? "Jul" : y) + "</span>";
      });
    }
    var today = pct(new Date(now).toISOString());
    var lines = rows.map(function (r) {
      var a = r.release && pct(r.release) != null ? pct(r.release) : 0;
      var s = r.support ? pct(r.support) : null;
      var e = r.eol ? pct(r.eol) : 100;
      var eolShown = r.eol && Date.parse(r.eol) >= start && Date.parse(r.eol) <= end;
      var tip = (PRODUCT[r.product] || r.product) + " " + r.cycle + "\nreleased " + fmtDate(r.release) +
        (r.support ? "\nactive support until " + fmtDate(r.support) : "") +
        "\n" + (r.eol_is_floor ? "supported at least until " : "end of life ") + (r.eol ? fmtDate(r.eol) : "not announced") +
        (r.latest ? "\nnewest " + r.latest : "") + "\nused by " + r.projects.join(", ") + "\nfor " + r.dependencies.join(", ");
      var active = '<i class="dp-tl__active" style="left:' + a + "%;width:" + Math.max(0, (s != null ? s : e) - a) + '%"></i>';
      var maint = s != null && e > s ? '<i class="dp-tl__maint" style="left:' + s + "%;width:" + (e - s) + '%"></i>' : "";
      var cap = eolShown ? '<i class="dp-tl__eol dp-tl__eol--' + phaseLevel(r) + (r.eol_is_floor ? " dp-tl__eol--floor" : "") + '" style="left:' + e + '%"></i>' : "";
      return '<div class="dp-tl__row"><div class="dp-tl__label"><b>' + esc(PRODUCT[r.product] || r.product) + " " + esc(r.cycle) +
        '</b><span class="dp-muted">' + r.projects.map(function (n) { return navLink(projectHref(n, "lifecycle"), esc(n)); }).join(", ") + "</span></div>" +
        '<div class="dp-tl__track" tabindex="0" data-tip="' + esc(tip) + '">' + active + maint + cap +
        '<i class="dp-tl__today" style="left:' + today + '%"></i></div>' +
        '<div class="dp-tl__state">' + chip(phaseLevel(r), phaseStatus(r), phaseWords(r)) + "</div></div>";
    }).join("");
    return '<div class="dp-timeline"><div class="dp-tl__row dp-tl__row--axis"><div></div><div class="dp-tl__axis">' + ticks +
      '<span class="dp-tl__tick dp-tl__tick--today" style="left:' + today + '%">today</span></div><div></div></div>' + lines + "</div>";
  }

  function renderLifecycle() {
    var rows = DATA_SET.lifecycle || [];
    var used = {};
    var html = '<p class="dp-view__intro">Every release line in use, on one calendar: runtimes, operating systems and CI runners, kernels, and the ' +
      "native binaries and libraries whose publishers state a support window. The bar runs from a release to the end of its support; " +
      "the striped end is security fixes only; the line is today.</p>";
    LIFECYCLE_GROUPS.forEach(function (g) {
      var list = rows.filter(function (r) {
        if (used[r.product + r.cycle]) return false;
        var hit = g.products ? g.products.indexOf(r.product) >= 0 : true;
        if (hit) used[r.product + r.cycle] = true;
        return hit;
      });
      if (list.length) html += '<h2 class="dp-h2">' + esc(g.title) + "</h2>" + timeline(list);
    });
    // What has no release line here, said out loud rather than left out.
    var untracked = {};
    DATA_SET.live.forEach(function (p) {
      p.dependencies.forEach(function (d) {
        var key;
        if (d.self_hosted) key = "Self-hosted runner: " + name(d).replace(/^self-hosted /, "") + " declares no OS version";
        else if ((d.ecosystem === "native" || d.ecosystem === "runtime") && !d.lifecycle && !isIndirect(d)) key = name(d) + " publishes no support window";
        if (!key) return;
        (untracked[key] = untracked[key] || []).push(p.name);
      });
    });
    var keys = Object.keys(untracked).sort();
    if (keys.length) {
      html += '<h2 class="dp-h2">No release line to show</h2><ul class="dp-untracked">' + keys.map(function (k) {
        return "<li>" + chip("idle", "unknown", "untracked") + " " + esc(k) + ' <span class="dp-muted">· ' +
          uniq(untracked[k]).map(function (n) { return navLink(projectHref(n, "lifecycle"), esc(n)); }).join(", ") + "</span></li>";
      }).join("") + "</ul>";
    }
    html += '<h2 class="dp-h2">As a table</h2><div class="ci-table__wrap"><table class="ci-table"><thead><tr>' +
      ["Release", "State", "Released", "Active support", "End of life", "Newest", "Used by"].map(function (h) { return '<th class="text-label-upper">' + h + "</th>"; }).join("") +
      "</tr></thead><tbody>" + rows.map(function (r) {
        return '<tr><td><a href="' + esc(r.url) + '" target="_blank" rel="noopener">' + esc((PRODUCT[r.product] || r.product) + " " + r.cycle) + "</a>" +
          '<span class="dp-sub">' + esc(r.dependencies.join(", ")) + "</span></td><td>" +
          chip(phaseLevel(r), phaseStatus(r), r.phase === "maintenance" ? "security only" : r.phase === "active" ? "supported" : STATUS_LABEL[phaseStatus(r)]) +
          "</td><td>" + fmtDate(r.release) + "</td><td>" + fmtDate(r.support) + "</td><td>" + (r.eol_is_floor ? "≥ " : "") + fmtDate(r.eol) + "</td><td>" + esc(r.latest || "—") +
          '</td><td class="dp-wrap">' + r.projects.map(function (n) { return navLink(projectHref(n, "lifecycle"), esc(n)); }).join(", ") + "</td></tr>";
      }).join("") + "</tbody></table></div>";
    return html;
  }

  // --- inventory ----------------------------------------------------------------

  function renderInventory() {
    var live = DATA_SET.live;
    var indirectTotal = 0;
    live.forEach(function (p) { indirectTotal += p.summary.indirect; });
    return '<div class="ci-toolbar" id="filters">' +
      '<div class="dp-search" data-component="SearchInput"><input id="search" data-search-input type="search" placeholder="Filter by name, version or project" aria-label="Filter the inventory" value="' +
      esc(state.q) + '"><span class="dp-search__count" id="search-count" role="status" data-search-status></span></div>' +
      '<button class="ci-btn" id="only-bad" aria-pressed="' + state.bad + '">Needs attention only</button>' +
      '<button class="ci-btn" id="with-indirect" aria-pressed="' + state.indirect + '">Include ' + indirectTotal.toLocaleString() + " indirect</button>" +
      ecoChips() + "</div>" +
      '<div class="ci-table__wrap"><table class="ci-table dp-deps dp-deps--wide" id="inventory"><thead><tr>' +
      ["Dependency", "Project", "Pinned", "Newest", "Status", "Where"].map(function (h) { return '<th class="text-label-upper">' + h + "</th>"; }).join("") +
      '</tr></thead><tbody id="inventory-rows"></tbody></table></div><div class="dp-more" id="inventory-more"></div>';
  }

  function fillInventory() {
    var q = state.q.trim().toLowerCase();
    var rows = [];
    DATA_SET.live.forEach(function (p) {
      p.dependencies.forEach(function (d) {
        if (!state.indirect && isIndirect(d)) return;
        if (state.eco.length && state.eco.indexOf(d.ecosystem) < 0) return;
        if (state.bad && !attention(d)) return;
        rows.push({ p: p, d: d });
      });
    });
    if (q) {
      rows = rows.filter(function (r) {
        var d = r.d, up = d.upstream || {};
        return [r.p.name, d.name, d.label, d.version, d.constraint, up.latest, up.effective, d.status, d.scope, srcPath(d)].join(" ").toLowerCase().indexOf(q) >= 0;
      });
    }
    rows.sort(function (a, b) { return rank(b.d.level) - rank(a.d.level) || (isIndirect(a.d) - isIndirect(b.d)) || a.p.name.localeCompare(b.p.name) || name(a.d).localeCompare(name(b.d)); });
    var shown = rows.slice(0, state.limit);
    byId("inventory-rows").innerHTML = shown.map(function (r) { return depRows(r.p, [r.d], true); }).join("");
    byId("search-count").textContent = rows.length.toLocaleString() + " dependencies";
    byId("inventory-more").innerHTML = rows.length > shown.length
      ? '<button class="ci-btn" id="show-more">Show ' + Math.min(PAGE_ROWS, rows.length - shown.length) + " more of " + (rows.length - shown.length).toLocaleString() + "</button>" : "";
  }

  // --- the page shell -------------------------------------------------------

  function renderHero() {
    var data = DATA_SET, live = data.live;
    var needFix = live.filter(function (p) { return p._actions.some(function (a) { return a.tier === "fix"; }); });
    var c = tierCounts(data.actions);
    var late = data.actions.filter(function (a) { return a.sla && a.sla.state === "breached"; }).length;
    byId("hero").innerHTML = needFix.length + '<span class="ci-hero__of">of ' + live.length + "</span>";
    byId("hero-note").innerHTML = "projects have something to fix now" +
      (needFix.length ? ": " + needFix.map(function (p) { return navLink(projectHref(p.name, "tier-fix"), esc(p.name)); }).join(", ") : "") +
      (late ? " · " + plural(late, "action") + " past its fix-by date" : "") +
      ' · <span title="' + esc(data.generated) + '">read ' + esc(fmtAgo(data.generated)) + "</span>";
    var advisories = {};
    live.forEach(function (p) { p.dependencies.forEach(function (d) { (d.vulnerabilities || []).forEach(function (v) { advisories[v.id] = 1; }); }); });
    var hist = data.history || [];
    var series = function (k) { return hist.slice(-60).map(function (h) { return h[k] || 0; }); };
    byId("tiles").innerHTML = TIERS.map(function (t) {
      return '<a data-nav class="dp-tile" href="?view=upgrades#tier-' + t.id + '"><div class="ci-stat__value">' + c[t.id] + "</div>" +
        '<div class="ci-stat__label text-label-upper">' + esc(t.title) + "</div></a>";
    }).join("") + '<div class="dp-trends">' + [[Object.keys(advisories).length, "advisories", series("vulnerabilities")],
      [live.reduce(function (s, p) { return s + p.summary.libyears; }, 0).toFixed(1), "libyears", series("libyears")]].map(function (t) {
        return '<span class="dp-trend" data-tip="' + esc(t[1] === "libyears" ? "years between each pinned release and the newest, summed" : "distinct advisories across every project") + '">' +
          "<b>" + esc(t[0]) + "</b> " + esc(t[1]) + sparkline(t[2], t[1] + " over the last " + t[2].length + " days") + "</span>";
      }).join("") + "</div>";
    byId("generated").textContent = fmtAgo(data.generated);
    byId("generated").setAttribute("title", data.generated);
  }

  function renderNotices() {
    var data = DATA_SET, live = data.live, notices = "";
    var age = daysSince(data.generated);
    if (age != null && age * 24 > STALE_AFTER_H) {
      notices += '<p class="ci-note ci-note--error">' + ICON.eol + "<span>This data was collected <b>" + esc(fmtAgo(data.generated)) +
        "</b>. The collector runs daily, so it has stopped: the page below is as the org stood then.</span></p>";
    }
    var failed = (data.sources || []).filter(function (s) { return s.failures; });
    if (failed.length) {
      // Named by source, as the sources table names them; the host says which part.
      var sourceOf = function (host) { return ((data.data_sources || []).filter(function (c) { return (c.hosts || []).indexOf(host) >= 0; })[0] || {}).name || host; };
      notices += '<p class="ci-note">' + ICON.unknown + "<span>Some lookups failed, and the dependencies they served read as unknown: " +
        failed.map(function (s) { return "<b>" + esc(sourceOf(s.host)) + "</b> (" + esc(s.host) + ((s.errors || [])[0] ? ": " + esc(s.errors[0]) : "") + ")"; }).join(", ") +
        '. ' + navLink("#sources", "Sources") + " lists what each one gives.</span></p>";
    }
    var unread = data.projects.filter(function (p) { return p.error; });
    if (unread.length) {
      notices += '<p class="ci-note ci-note--error">' + ICON.unreachable + "<span>Not read: " +
        unread.map(function (p) { return "<b>" + esc(p.name) + "</b> (" + esc(p.error) + ")"; }).join(", ") + ".</span></p>";
    }
    var unmatched = [];
    live.forEach(function (p) { (p.unmatched || []).forEach(function (u) { unmatched.push("<b>" + esc(p.name) + "</b> " + esc(u.name || u.path) + " in <code>" + esc(u.path) + "</code> (" + esc(u.reason) + ")"); }); });
    if (unmatched.length) notices += '<p class="ci-note">' + ICON.unknown + "<span>Declared in deps-config.json but no longer found, so not tracked: " + unmatched.join("; ") + ".</span></p>";
    var notes = [];
    live.forEach(function (p) { (p.notes || []).forEach(function (n) { notes.push("<b>" + esc(p.name) + "</b>: " + esc(n)); }); });
    if (notes.length) notices += '<p class="ci-note">' + ICON.unknown + "<span>" + notes.join("; ") + "</span></p>";
    byId("notices").innerHTML = notices;
  }

  function renderChrome() {
    var data = DATA_SET;
    var LEVEL_ICON = { critical: "vulnerable", serious: "eol-soon", warning: "major", info: "minor", good: "current", idle: "floating" };
    var WORD = { critical: "Fix now", serious: "Fix now", warning: "Plan", info: "Routine", good: "Current", idle: "Not compared" };
    byId("legend").innerHTML = '<span class="text-label-upper">Urgency</span>' + ["critical", "serious", "warning", "info", "good", "idle"].map(function (l) {
      var ex = { critical: "a vulnerability that ships or is exploited in the wild, or an end-of-life release", serious: "a dev-only or unrated advisory, support ending within 90 days, a major out a year",
        warning: "a major behind, a minor out three months, an advisory no code calls", info: "a newer minor or patch, a sibling a few commits ahead",
        good: "the newest release", idle: "floating, indirect, or nothing to compare against" }[l];
      return '<span class="dp-legend__item">' + chip(l, LEVEL_ICON[l], WORD[l]) + " " + esc(ex) + "</span>";
    }).join("");
    // Where the data comes from: every source, what it gives, and its terms.
    // A source whose lookups failed this run says so beside its name.
    var failed = {};
    (data.sources || []).forEach(function (s) { if (s.failures) failed[s.host] = true; });
    var groups = { projects: "The projects", versions: "Versions and releases", support: "Support windows",
      security: "Security", licenses: "Packages and licenses" };
    var catalog = data.data_sources || [];
    var rows = Object.keys(groups).map(function (g) {
      var list = catalog.filter(function (c) { return c.group === g; });
      if (!list.length) return "";
      return '<tr class="dp-sources__group"><th colspan="3" class="text-label-upper">' + esc(groups[g]) + "</th></tr>" + list.map(function (c) {
        var down = (c.hosts || []).some(function (h) { return failed[h]; });
        return '<tr><td><a href="' + esc(c.url) + '" target="_blank" rel="noopener">' + esc(c.name) + "</a>" +
          (down ? " " + chip("warning", "unknown", "failed this run") : "") + "</td><td>" + esc(c.gives) + '</td><td class="dp-muted">' + esc(c.license) + "</td></tr>";
      }).join("");
    }).join("");
    byId("footer").innerHTML = (catalog.length ? '<details class="dp-group dp-sources" id="sources"><summary>' + ICON.chevron +
      "<b>Sources</b><span class=\"dp-muted\">" + plural(catalog.length, "public source") + ", no API key</span></summary>" +
      '<div class="ci-table__wrap"><table class="ci-table dp-sources__table"><thead><tr><th class="text-label-upper">Source</th>' +
      '<th class="text-label-upper">What it gives</th><th class="text-label-upper">Data terms</th></tr></thead><tbody>' + rows + "</tbody></table></div></details>" : "") +
      "<p>Collected " + esc(data.generated) + ". GitHub read " + (data.authenticated ? "with the build's own token" : "anonymously") + ". " +
      'As JSON: <a href="deps.json">deps.json</a> · <a href="deps-actions.json">deps-actions.json</a> for agents · ' +
      '<a href="deps.cdx.json">CycloneDX SBOM</a> · <a href="deps-feed.xml">feed</a>.</p>';
  }

  function route() {
    readUrl();
    var main = byId("main");
    var inProject = !!state.project;
    byId("hero-wrap").hidden = inProject;
    document.querySelectorAll("#views [role=radio]").forEach(function (b) {
      var on = b.dataset.segmentedOption === (inProject ? "projects" : state.view);
      b.setAttribute("aria-checked", String(on));
      b.tabIndex = on ? 0 : -1;
      if (on) b.setAttribute("data-segmented-active", ""); else b.removeAttribute("data-segmented-active");
    });
    byId("crumb-tail").innerHTML = inProject ? " / " + navLink("?", "deps") + " / " + esc(state.project) : " / deps";
    document.title = (inProject ? state.project + " · " : "") + "Dependencies · codesweep-ai dashboards";
    if (inProject) main.innerHTML = renderProject(state.project);
    else if (state.view === "upgrades") main.innerHTML = renderUpgrades();
    else if (state.view === "internal") main.innerHTML = renderInternal();
    else if (state.view === "lifecycle") main.innerHTML = renderLifecycle();
    else if (state.view === "inventory") { main.innerHTML = renderInventory(); fillInventory(); }
    else main.innerHTML = renderProjects();
    replaceUrl();
    masonry();
    reveal();
  }

  // Open whatever the hash names: a tier, an ecosystem group, a dependency row.
  function reveal() {
    var id = decodeURIComponent((location.hash || "").slice(1));
    if (!id) { window.scrollTo(0, 0); return; }
    var el = byId(id);
    if (!el) return;
    for (var n = el; n; n = n.parentElement) { if (n.tagName === "DETAILS") n.open = true; }
    if (el.classList.contains("dp-dep")) toggleRow(el, true);
    if (el.tagName === "DETAILS") el.open = true;
    el.classList.add("dp-flash");
    setTimeout(function () { el.classList.remove("dp-flash"); }, 1600);
    el.scrollIntoView({ block: "start" });
    masonry();
  }

  function toggleRow(tr, open) {
    var next = tr.nextElementSibling;
    if (!next || !next.classList.contains("dp-dep__detail")) return;
    var show = open == null ? next.hidden : open;
    if (show && !next.firstChild.innerHTML) {
      var p = DATA_SET.byName[tr.closest("[data-project]") ? tr.closest("[data-project]").dataset.project : ""] || null;
      var hit = findDep(tr.id);
      if (hit) next.firstChild.innerHTML = detail(hit.p, hit.d);
      else if (p) next.firstChild.textContent = "";
    }
    next.hidden = !show;
    tr.setAttribute("aria-expanded", String(show));
  }

  var DEP_INDEX = null;
  function findDep(id) {
    if (!DEP_INDEX) {
      DEP_INDEX = {};
      DATA_SET.live.forEach(function (p) {
        p.dependencies.forEach(function (d) {
          DEP_INDEX[depId(d)] = DEP_INDEX[depId(d)] || { p: p, d: d };
          DEP_INDEX[depId(d) + "-" + slug(p.name)] = { p: p, d: d };
        });
      });
    }
    if (state.project) {
      var p = DATA_SET.byName[state.project];
      var own = p && p.dependencies.filter(function (d) { return depId(d) === id; })[0];
      if (own) return { p: p, d: own };
    }
    return DEP_INDEX[id] || null;
  }

  // Cards differ in height, so they pack by row span rather than by row.
  function masonry() {
    var ROW = 4, GAP = 16; // must match .ci-grid--masonry and --space-4
    document.querySelectorAll("#main .ci-grid").forEach(function (grid) {
      grid.classList.add("ci-grid--masonry");
      Array.prototype.forEach.call(grid.children, function (c) {
        c.style.gridRowEnd = "";
        if (c.hidden || !c.offsetParent) return;
        c.style.gridRowEnd = "span " + Math.ceil((c.getBoundingClientRect().height + GAP) / ROW);
      });
    });
  }
  window.__ciLayout = masonry;

  function wire() {
    document.addEventListener("click", function (e) {
      var a = e.target.closest && e.target.closest("a[data-nav]");
      if (a && !e.metaKey && !e.ctrlKey && !e.shiftKey && e.button === 0) {
        e.preventDefault();
        go(a.getAttribute("href"));
        return;
      }
      var hashLink = e.target.closest && e.target.closest('a[href^="#"]');
      if (hashLink) {
        e.preventDefault();
        history.replaceState(null, "", hashLink.getAttribute("href"));
        reveal();
        return;
      }
      var chipBtn = e.target.closest && e.target.closest(".dp-chip[data-eco]");
      if (chipBtn) {
        var i = state.eco.indexOf(chipBtn.dataset.eco);
        if (i >= 0) state.eco.splice(i, 1); else state.eco.push(chipBtn.dataset.eco);
        chipBtn.setAttribute("aria-pressed", String(i < 0));
        applyFilters();
        return;
      }
      if (e.target.id === "only-bad" || e.target.id === "with-indirect") {
        var key = e.target.id === "only-bad" ? "bad" : "indirect";
        state[key] = !state[key];
        state.limit = PAGE_ROWS;
        e.target.setAttribute("aria-pressed", String(state[key]));
        applyFilters();
        return;
      }
      if (e.target.id === "show-more") { state.limit += PAGE_ROWS; fillInventory(); return; }
      if (e.target.dataset && e.target.dataset.indirect) {
        // Indirect rows join the table above rather than opening a level below it.
        var eco = e.target.dataset.indirect, pr = DATA_SET.byName[state.project];
        var tbody = e.target.closest("details").querySelector("tbody");
        var extra = pr.dependencies.filter(function (d) { return d.ecosystem === eco && isIndirect(d); });
        if (tbody) tbody.insertAdjacentHTML("beforeend", depRows(pr, extra));
        else e.target.closest(".dp-more").insertAdjacentHTML("beforebegin", depTable(pr, extra));
        e.target.closest(".dp-more").remove();
        return;
      }
      var row = e.target.closest && e.target.closest("tr.dp-dep");
      if (row && !e.target.closest("a")) { toggleRow(row); masonry(); }
    });
    document.addEventListener("keydown", function (e) {
      if ((e.key === "Enter" || e.key === " ") && e.target.classList && e.target.classList.contains("dp-dep")) {
        e.preventDefault();
        toggleRow(e.target);
      }
    });
    document.addEventListener("input", function (e) {
      if (e.target.id === "search") { state.q = e.target.value; state.limit = PAGE_ROWS; applyFilters(); }
    });
    document.addEventListener("toggle", function (e) {
      if (e.target.tagName === "DETAILS") masonry();
    }, true);
    document.querySelectorAll("#views [role=radio]").forEach(function (b, i, all) {
      var pick = function (btn) { go(urlFor({ view: btn.dataset.segmentedOption, project: null }).toString().replace(location.origin, "").replace(/#.*$/, "")); };
      b.addEventListener("click", function () { pick(b); });
      b.addEventListener("keydown", function (e) {
        var step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key];
        var idx = e.key === "Home" ? 0 : e.key === "End" ? all.length - 1 : step ? (i + step + all.length) % all.length : null;
        if (idx == null) return;
        e.preventDefault();
        pick(all[idx]);
        all[idx].focus();
      });
    });
    addEventListener("popstate", route);
    addEventListener("resize", masonry);
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(masonry);
    tooltips();
  }

  function applyFilters() {
    if (state.view === "inventory" && !state.project) fillInventory();
    if (state.view === "upgrades" && !state.project) {
      document.querySelectorAll("#main .dp-action").forEach(function (c) {
        c.hidden = state.eco.length > 0 && !c.dataset.eco.split(" ").some(function (e) { return state.eco.indexOf(e) >= 0; });
      });
      document.querySelectorAll("#main .dp-tier").forEach(function (t) {
        t.hidden = !t.querySelector(".dp-action:not([hidden])");
      });
    }
    replaceUrl();
    masonry();
  }

  // One bubble for the page, after the package's Tooltip: shown on hover and on
  // focus, dismissed by Escape, positioned to stay on screen. It only ever
  // repeats what a table or a detail row also shows.
  function tooltips() {
    var tip = byId("tooltip"), timer = null, current = null;
    function show(el) {
      current = el;
      tip.textContent = el.getAttribute("data-tip");
      tip.hidden = false;
      var r = el.getBoundingClientRect(), t = tip.getBoundingClientRect();
      var left = Math.min(Math.max(8, r.left + r.width / 2 - t.width / 2), innerWidth - t.width - 8);
      var top = r.top - t.height - 6;
      tip.setAttribute("data-side", top < 8 ? "bottom" : "top");
      if (top < 8) top = r.bottom + 6;
      tip.style.left = left + "px";
      tip.style.top = top + "px";
    }
    function hide() { clearTimeout(timer); tip.hidden = true; current = null; }
    document.addEventListener("pointerover", function (e) {
      var el = e.target.closest && e.target.closest("[data-tip]");
      if (!el || el === current) return;
      clearTimeout(timer);
      timer = setTimeout(function () { show(el); }, 250);
    });
    document.addEventListener("pointerout", function (e) {
      var el = e.target.closest && e.target.closest("[data-tip]");
      if (el && (!e.relatedTarget || !el.contains(e.relatedTarget))) hide();
    });
    document.addEventListener("focusin", function (e) {
      var el = e.target.closest && e.target.closest("[data-tip]");
      if (el) show(el); else hide();
    });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") hide(); });
    addEventListener("scroll", hide, { passive: true });
  }

  // --- theme: the three modes the package's useTheme hook defines ----------

  function theme() {
    var root = document.documentElement;
    var btn = byId("theme");
    var media = matchMedia("(prefers-color-scheme: dark)");
    var KEY = "ci-dashboard-theme";
    var MODES = ["system", "light", "dark"];
    // lucide Monitor / Sun / Moon, the icons the component pairs with each mode.
    var ICONS = {
      system: '<rect width="20" height="14" x="2" y="3" rx="2"/>' +
        '<line x1="8" x2="16" y1="21" y2="21"/><line x1="12" x2="12" y1="17" y2="21"/>',
      light: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2"/><path d="M12 20v2"/>' +
        '<path d="m4.93 4.93 1.41 1.41"/><path d="m17.66 17.66 1.41 1.41"/>' +
        '<path d="M2 12h2"/><path d="M20 12h2"/><path d="m6.34 17.66-1.41 1.41"/>' +
        '<path d="m19.07 4.93-1.41 1.41"/>',
      dark: '<path d="M20.985 12.486a9 9 0 1 1-9.473-9.472c.405-.022.617.46.402.803a6 6 0' +
        ' 0 0 8.268 8.268c.344-.215.825-.004.803.401"/>'
    };
    function apply(mode) {
      root.setAttribute("data-theme", mode === "system" ? (media.matches ? "dark" : "light") : mode);
      root.setAttribute("data-theme-mode", mode);
      btn.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true">' + ICONS[mode] + "</svg>";
      btn.setAttribute("aria-label", "Toggle theme. Current: " + mode);
      // The component leaves the pointer hint to its Tooltip; this page has no
      // Tooltip component, so the hint rides on title instead of being lost.
      btn.setAttribute("title", "Theme: " + mode + ". Click to cycle.");
    }
    function mode() { return root.getAttribute("data-theme-mode") || "system"; }
    apply(mode());
    btn.addEventListener("click", function () {
      var next = MODES[(MODES.indexOf(mode()) + 1) % MODES.length];
      try { localStorage.setItem(KEY, next); } catch (e) { /* may be unavailable */ }
      apply(next);
      masonry();
    });
    media.addEventListener("change", function () { if (mode() === "system") apply("system"); });
  }

  // --- go -----------------------------------------------------------------

  theme();
  fetch(DATA, { cache: "no-cache" })
    .then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    })
    .then(function (data) {
      if (!data || !Array.isArray(data.projects)) throw new Error("unrecognised file");
      DATA_SET = data;
      prepare(data);
      renderHero();
      renderNotices();
      renderChrome();
      byId("loading").hidden = true;
      wire();
      route();
    })
    .catch(function (err) {
      byId("hero-note").textContent = "no dependencies file to read";
      byId("loading").innerHTML = "Could not load <code>" + esc(DATA) + "</code>: " + esc(err.message || err) +
        ". The site's build writes it; until that has run there is nothing to show.";
    });
})();

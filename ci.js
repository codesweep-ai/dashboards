/* Assemble the dashboard in the browser.
 *
 * projects.json lists where each project publishes its own ci-status.json. Those
 * paths are relative, so they resolve on whichever host serves this page, and a
 * fork reads its own owner's projects. Being same-origin, this needs no CORS, no
 * API token and none of the GitHub API's 60-requests-an-hour anonymous budget.
 * Each file is written by that project's own CI, so it is fresh as of that
 * project's last build — which is why staleness is shown per card rather than
 * once for the page.
 */
(function () {
  "use strict";

  var INDEX = "projects.json";
  // A workflow passing right now but below this rate across the window is flaky —
  // the state a green tip would otherwise hide.
  var FLAKY_BELOW = 85;
  // Past this since its latest ci run, a project has been quiet long enough to say
  // so out loud. The run dates the project rather than the file does, because a
  // Pages build may also follow a scheduled workflow and rewrite the file daily
  // with nothing built. A file that stops being published freezes the run too.
  var STALE_AFTER_H = 72;
  var PRIMARY_GATE = "ci";

  var PASSED = ["success"];
  var FAILED = ["failure", "timed_out", "startup_failure"];

  var STATE_LABEL = {
    success: "passing", failure: "failing", timed_out: "timed out",
    startup_failure: "startup failure", cancelled: "cancelled", skipped: "skipped",
    neutral: "neutral", stale: "stale", action_required: "action required",
    in_progress: "running", queued: "queued", requested: "queued",
    waiting: "queued", pending: "queued"
  };

  var ICON = {
    good: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3.5 8.5l3 3 6-6"/></svg>',
    critical: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4.5 4.5l7 7m0-7l-7 7"/></svg>',
    running: '<svg viewBox="0 0 16 16" aria-hidden="true" class="ci-spin"><path d="M8 2.5a5.5 5.5 0 1 1-5.5 5.5"/></svg>',
    flaky: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2.5 11l3.5-4 4 3 3.5-5"/></svg>',
    idle: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3.5 8h9"/></svg>',
    unreachable: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 4.5v4m0 2.5v.5"/></svg>',
    chevron: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M6 3.5L10.5 8 6 12.5"/></svg>',
    recovered: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4.5 11.5l7-7m-5 0h5v5"/></svg>'
  };

  var REPO_LABEL = {
    good: "all green", critical: "failing", running: "running", flaky: "flaky",
    recovered: "recovered", idle: "no runs", unreachable: "unreachable"
  };

  // --- small helpers ------------------------------------------------------

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function has(list, v) { return list.indexOf(v) !== -1; }

  function bucket(state) {
    if (has(PASSED, state)) return "good";
    if (has(FAILED, state)) return "critical";
    if (has(["in_progress", "queued", "requested", "waiting", "pending"], state)) return "running";
    return "idle";
  }

  function fmtDuration(secs) {
    if (secs == null) return "—";
    if (secs < 60) return secs + "s";
    if (secs < 3600) {
      var m = Math.floor(secs / 60), s = secs % 60;
      return (s && m < 10) ? m + "m " + s + "s" : m + "m";
    }
    var h = Math.floor(secs / 3600), rm = Math.floor((secs % 3600) / 60);
    return rm ? h + "h " + rm + "m" : h + "h";
  }

  function hoursSince(iso) {
    if (!iso) return null;
    var t = Date.parse(iso);
    return isNaN(t) ? null : (Date.now() - t) / 36e5;
  }

  function fmtAgo(iso) {
    var h = hoursSince(iso);
    if (h == null) return "never";
    var secs = h * 3600;
    if (secs < 90) return "just now";
    if (secs < 3600) return Math.round(secs / 60) + "m ago";
    if (secs < 172800) return Math.round(h) + "h ago";
    return Math.round(h / 24) + "d ago";
  }

  // --- load ---------------------------------------------------------------

  function loadProject(entry, base) {
    var url = new URL(entry.status, base).href;
    return fetch(url, { cache: "no-cache" })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (data) {
        if (!data || !data.repo || !data.workflows) throw new Error("unrecognised file");
        data.name = data.repo.name || entry.name;
        return data;
      })
      .catch(function (err) {
        // A project that has never published, or whose Pages build has not run
        // yet, is a fact about that project — not a reason to fail the page.
        return { name: entry.name, unreachable: String(err.message || err), url: url,
                 repo: { name: entry.name, description: "", url: "", branch: "" },
                 workflows: [] };
      });
  }

  // --- roll-up ------------------------------------------------------------

  // When the project last built: its latest ci run, or the file itself for a
  // project with no ci run to read.
  function lastBuilt(p) {
    var gate = p.workflows.filter(function (w) {
      return w.name.toLowerCase() === PRIMARY_GATE && w.latest && w.latest.started;
    })[0];
    return gate ? { at: gate.latest.started, label: "last ci run" }
      : { at: p.generated, label: "status published" };
  }

  // The newest run of a workflow that reached a verdict. A run in flight, cancelled
  // or skipped says nothing about whether the tip builds, so it is looked past.
  function verdict(w) {
    var runs = w.runs || [];
    for (var i = 0; i < runs.length; i++) {
      var b = bucket(runs[i].state);
      if (b === "good" || b === "critical") return b;
    }
    return null;
  }

  // Why a workflow sits under the flaky line, from its runs in the window. A commit
  // that both failed and passed, or passed on a rerun, is flakiness. A commit that
  // never passed was broken, and when the workflow is green a later commit fixed
  // it. The file keeps only a run's last attempt, so a rerun of a run that passed
  // looks the same as a rerun of one that failed, and both count as flaky here.
  function health(w) {
    if (w.pass_rate == null || w.pass_rate >= FLAKY_BELOW) return null;
    var failed = {}, passed = {}, reruns = [];
    (w.runs || []).forEach(function (r) {
      var b = bucket(r.state);
      if (b === "critical" && r.sha) failed[r.sha] = r;
      if (b === "good" && r.sha) passed[r.sha] = r;
      if (b === "good" && r.attempt > 1) reruns.push(r);
    });
    var shas = Object.keys(failed);
    var both = shas.filter(function (s) { return passed[s]; }).map(function (s) { return failed[s]; });
    var broke = shas.length - both.length;
    var fixed = verdict(w) === "good";
    return {
      kind: both.length || reruns.length ? "flaky" : fixed ? "recovered" : null,
      both: both, reruns: reruns, broke: broke, fixed: fixed
    };
  }

  function summarise(p) {
    var verdicts = [], running = false, rates = [];
    p.workflows.forEach(function (w) {
      w.health = health(w);
      var v = verdict(w);
      if (v) verdicts.push(v);
      if (w.latest && bucket(w.latest.state) === "running") running = true;
      if (w.pass_rate != null) rates.push(w.pass_rate);
    });
    // The weakest workflow sets the tone: a repo is only as good as its worst gate.
    p.pass_rate = rates.length ? Math.min.apply(null, rates) : null;
    p.flaky = p.workflows.filter(function (w) { return w.health && w.health.kind === "flaky"; });
    p.recovered = p.workflows.filter(function (w) {
      return w.health && w.health.kind === "recovered";
    });
    p.built = lastBuilt(p);
    p.staleHours = hoursSince(p.built.at);
    // Green right now is what a badge says: every workflow's newest verdict passed.
    // A flaky repo is green, and so is one building on top of a green verdict.
    p.green = !p.unreachable && verdicts.length > 0 && !has(verdicts, "critical");
    p.state = p.unreachable ? "unreachable"
      : has(verdicts, "critical") ? "critical"
      : running ? "running"
      : p.flaky.length ? "flaky"
      : p.recovered.length ? "recovered"
      : verdicts.length ? "good"
      : "idle";
    return p;
  }

  var ORDER = { critical: 0, flaky: 1, running: 2, unreachable: 3, idle: 4, recovered: 5, good: 6 };

  // --- render -------------------------------------------------------------

  function strip(w) {
    if (!w.runs || !w.runs.length) return '<span class="ci-strip ci-strip--empty">no runs</span>';
    var cells = w.runs.slice().reverse().map(function (run) {
      var b = bucket(run.state);
      var tip = (STATE_LABEL[run.state] || run.state) + " · " + fmtAgo(run.started) +
        " · " + fmtDuration(run.duration) + "\n" + (run.title || "") +
        "\n" + (run.sha || "") + " · " + (run.event || "") +
        (run.attempt > 1 ? " · attempt " + run.attempt : "");
      return '<a class="ci-strip__run ci-strip__run--' + b + '" href="' + esc(run.url) +
        '" target="_blank" rel="noopener" title="' + esc(tip) + '" aria-label="' +
        esc(tip) + '"><i></i></a>';
    });
    return '<span class="ci-strip">' + cells.join("") + "</span>";
  }

  function workflowRow(w) {
    if (!w.latest) {
      return '<div class="ci-wf ci-wf--idle" data-never="1">' +
        '<span class="ci-wf__name">' + esc(w.name) + "</span>" +
        '<span class="ci-chip ci-chip--idle">' + ICON.idle + "never run</span>" +
        '<span class="ci-wf__path">' + esc(w.path || "declared in the repo") + "</span></div>";
    }
    var b = bucket(w.latest.state), stats = [], kind = w.health && w.health.kind;
    if (w.pass_rate != null) {
      stats.push('<span class="ci-wf__stat' + (kind ? " ci-wf__stat--" + kind : "") +
        '"><b>' + w.pass_rate + "%</b> of " + w.counted + "</span>");
    }
    if (w.median_duration != null) {
      stats.push('<span class="ci-wf__stat">~' + fmtDuration(w.median_duration) + "</span>");
    }
    if (w.green_streak >= 3) {
      stats.push('<span class="ci-wf__stat">' + w.green_streak + " green in a row</span>");
    }
    var fail = w.last_failure ? '<div class="ci-wf__fail">last failure ' +
      esc(fmtAgo(w.last_failure.started)) + ' · <a href="' + esc(w.last_failure.url) +
      '" target="_blank" rel="noopener">' + esc((w.last_failure.title || "").slice(0, 70)) +
      "</a></div>" : "";

    return '<div class="ci-wf">' +
      '<span class="ci-wf__name">' + esc(w.name) + "</span>" +
      '<a class="ci-chip ci-chip--' + b + '" href="' + esc(w.latest.url) +
      '" target="_blank" rel="noopener">' + ICON[b] +
      (STATE_LABEL[w.latest.state] || w.latest.state) + "</a>" +
      '<span class="ci-wf__when">' + esc(fmtAgo(w.latest.started)) + "</span>" +
      strip(w) +
      '<span class="ci-wf__stats">' + stats.join("") + "</span>" + why(w.health) + fail + "</div>";
  }

  // The evidence behind a flaky or recovered rate, so a reader can check the call.
  function why(h) {
    if (!h || !h.kind) return "";
    function commit(r) {
      return '<a href="' + esc(r.url) + '" target="_blank" rel="noopener"><code>' +
        esc(String(r.sha).slice(0, 7)) + "</code></a>";
    }
    var parts = h.both.slice(0, 3).map(function (r) { return commit(r) + " failed and passed"; })
      .concat(h.reruns.slice(0, 3).map(function (r) { return commit(r) + " passed on a rerun"; }));
    if (h.broke) {
      parts.push(h.broke + (h.broke === 1 ? " failed commit" : " failed commits") +
        (h.fixed ? (h.broke === 1 ? ", fixed by a later one" : ", each fixed by a later one") : ""));
    }
    return '<div class="ci-wf__why">' + parts.join(" · ") + "</div>";
  }

  // Steady: the newest verdict passed, nothing is in flight, and the pass rate
  // clears the flaky line. Such a row says nothing a reader has to act on.
  function steady(w) {
    return !!w.latest && bucket(w.latest.state) !== "running" && verdict(w) === "good" &&
      w.pass_rate != null && w.pass_rate >= FLAKY_BELOW;
  }

  // The steady rows fold into one line, so a card is as tall as what needs reading.
  function more(list, after) {
    return '<details class="ci-more"><summary>' + ICON.chevron +
      '<span class="ci-more__count">' + ICON.good + list.length +
      (after ? " more" : list.length === 1 ? " workflow" : " workflows") + ", all steady</span>" +
      '<span class="ci-more__names">' + esc(list.map(function (w) { return w.name; }).join(", ")) +
      '</span></summary><div class="ci-more__body">' + list.map(workflowRow).join("") +
      "</div></details>";
  }

  function card(p) {
    if (p.unreachable) {
      return '<article class="ci-card ci-card--unreachable" data-state="unreachable" data-name="' +
        esc(p.name) + '"><header class="ci-card__head"><h2>' + esc(p.name) + "</h2>" +
        '<span class="ci-chip ci-chip--unreachable">' + ICON.unreachable + "no status</span></header>" +
        '<p class="ci-card__desc">This project has not published a ci-status.json yet, or it could ' +
        "not be read (" + esc(p.unreachable) + ").</p>" +
        '<footer class="ci-card__foot"><code>' + esc(p.url) + "</code></footer></article>";
    }
    // The primary gate stays out, so every card keeps one strip to read at a glance.
    var shown = [], folded = [], never = [];
    p.workflows.forEach(function (w) {
      if (!w.latest) never.push(w);
      else if (steady(w) && w.name.toLowerCase() !== PRIMARY_GATE) folded.push(w);
      else shown.push(w);
    });
    var rows = shown.map(workflowRow);
    if (folded.length) rows.push(more(folded, shown.length));
    rows = rows.concat(never.map(workflowRow));
    if (p.state === "idle") {
      rows.push('<div class="ci-wf__empty" data-empty="1" hidden>every workflow here has never run</div>');
    }
    var stale = p.staleHours != null && p.staleHours > STALE_AFTER_H;
    return '<article class="ci-card ci-card--' + p.state + '" data-state="' + p.state +
      '" data-name="' + esc(p.name) + '">' +
      '<header class="ci-card__head"><h2><a href="' + esc(p.repo.url) +
      '/actions" target="_blank" rel="noopener">' + esc(p.name) + "</a></h2>" +
      '<span class="ci-chip ci-chip--' + p.state + '">' + ICON[p.state] +
      REPO_LABEL[p.state] + "</span></header>" +
      '<p class="ci-card__desc">' + esc(p.repo.description) + "</p>" +
      '<div class="ci-card__body">' + rows.join("") + "</div>" +
      '<footer class="ci-card__foot' + (stale ? " ci-card__foot--stale" : "") +
      '">branch <code>' + esc(p.repo.branch) + "</code> · " +
      (p.built.at === p.generated ? "" : esc(p.built.label + " " + fmtAgo(p.built.at)) + " · ") +
      "status published " + esc(fmtAgo(p.generated)) + "</footer></article>";
  }

  function tableRow(p, w) {
    var state = w.latest ? w.latest.state : "never run";
    var b = w.latest ? bucket(w.latest.state) : "idle";
    var kind = w.health && w.health.kind;
    var rate = w.pass_rate == null ? "—"
      : w.pass_rate + "%" + (kind ? '<span class="ci-flag ci-flag--' + kind + '"> ' + kind + "</span>" : "");
    return '<tr data-state="' + p.state + '"' + (w.latest ? "" : ' data-never="1"') + ">" +
      "<td>" + esc(p.name) + "</td><td>" + esc(w.name) + "</td>" +
      '<td><span class="ci-chip ci-chip--' + b + '">' + ICON[b] +
      esc(STATE_LABEL[state] || state) + "</span></td>" +
      '<td class="ci-table__num">' + rate + "</td>" +
      '<td class="ci-table__num">' + (w.counted || "—") + "</td>" +
      '<td class="ci-table__num">' + (w.counted ? w.failures : "—") + "</td>" +
      '<td class="ci-table__num">' + esc(fmtDuration(w.median_duration)) + "</td>" +
      "<td>" + (w.latest ? esc(fmtAgo(w.latest.started)) : "—") + "</td>" +
      "<td>" + (w.last_failure ? esc(fmtAgo(w.last_failure.started)) : "—") + "</td></tr>";
  }

  // The figure answers one question: is every repository green right now? A flaky
  // repo is, so it counts. The line under the figure splits the green into steady
  // and flaky and names whatever is not green, so the figure carries one claim.
  function hero(live, window_) {
    var green = live.filter(function (p) { return p.green; });
    var flaky = green.filter(function (p) { return p.flaky.length; });
    var recovered = green.filter(function (p) { return !p.flaky.length && p.recovered.length; });
    var steady = green.length - flaky.length - recovered.length;
    var failing = live.filter(function (p) { return p.state === "critical"; });
    var unbuilt = live.filter(function (p) { return !p.green && p.state !== "critical"; });
    var building = green.filter(function (p) { return p.state === "running"; });

    function group(state, label, names) {
      return '<span class="ci-hero__group"><span class="ci-chip ci-chip--' + state + '">' +
        ICON[state] + esc(label) + "</span>" + (names ? "<span>" + names + "</span>" : "") +
        "</span>";
    }
    function named(list) {
      return list.map(function (p) { return "<b>" + esc(p.name) + "</b>"; }).join(", ");
    }
    // Each repo with the workflows that put it in its group, and their pass rates.
    function rates(list, key) {
      return list.map(function (p) {
        return "<b>" + esc(p.name) + "</b> " + p[key].map(function (w) {
          return esc(w.name) + " " + w.pass_rate + "%";
        }).join(", ");
      }).join(" · ");
    }

    var groups = [], foot = [], under = "under " + FLAKY_BELOW + "% of the last " + window_ + " runs";
    if (failing.length) groups.push(group("critical", failing.length + " failing", named(failing)));
    if (unbuilt.length) groups.push(group("idle", unbuilt.length + " not built yet", named(unbuilt)));
    if (flaky.length) {
      groups.push(group("flaky", flaky.length + " flaky", rates(flaky, "flaky")));
      foot.push("Flaky: " + under + ", and a commit both failed and passed, or passed on a rerun.");
    }
    if (recovered.length) {
      groups.push(group("recovered", recovered.length + " recovered", rates(recovered, "recovered")));
      foot.push("Recovered: " + (flaky.length ? "under " + FLAKY_BELOW + "% too" : under) +
        ", but each failed commit stayed red until a later one fixed it.");
    }
    if (steady) groups.push(group("good", steady + " steady"));
    if (live.length && steady === live.length) {
      foot.push("Every workflow passed at least " + FLAKY_BELOW + "% of its last " +
        window_ + " runs.");
    }
    if (building.length) {
      foot.push(building.length + " building now, counted by " +
        (building.length === 1 ? "its last finished run." : "their last finished runs."));
    }

    var fig = document.getElementById("hero");
    fig.className = "ci-hero__figure" + (!live.length ? ""
      : failing.length ? " ci-hero__figure--critical"
      : green.length === live.length ? " ci-hero__figure--good" : "");
    fig.innerHTML = green.length + '<span class="ci-hero__of">of ' + live.length + "</span>";
    document.getElementById("hero-note").textContent = live.length
      ? "repositories green right now" : "no project published a status";
    document.getElementById("hero-breakdown").innerHTML = groups.join("");
    document.getElementById("hero-foot").textContent = foot.join(" ");
  }

  function render(projects) {
    projects.sort(function (a, b) {
      return (ORDER[a.state] - ORDER[b.state]) ||
        ((a.pass_rate == null ? 101 : a.pass_rate) - (b.pass_rate == null ? 101 : b.pass_rate)) ||
        a.name.localeCompare(b.name);
    });

    var live = projects.filter(function (p) { return !p.unreachable; });
    // The tiles measure the primary gate alone. Housekeeping workflows such as
    // pages pass nearly every time, and counting them would lift the rate.
    var runs = 0, fails = 0, window_ = null;
    live.forEach(function (p) {
      window_ = window_ || p.window;
      p.workflows.forEach(function (w) {
        if (w.name.toLowerCase() === PRIMARY_GATE && w.counted) {
          runs += w.counted;
          fails += w.failures;
        }
      });
    });

    hero(live, window_);
    document.getElementById("stat-pass").textContent =
      runs ? Math.round(100 * (runs - fails) / runs) + "%" : "—";
    document.getElementById("stat-failed").innerHTML =
      runs ? fails + '<span class="ci-stat__of">of ' + runs + "</span>" : "—";
    document.getElementById("window").textContent = window_ || "—";
    document.getElementById("generated").textContent = new Date().toLocaleString();

    // Notices are for what a reader has to act on. A workflow that has never run
    // is not that: it is a standing fact, and the show never-run toggle is where
    // it belongs.
    var notices = "";
    var missing = projects.filter(function (p) { return p.unreachable; });
    if (missing.length) {
      notices += '<p class="ci-note ci-note--error">' + ICON.critical + "<span>No status published by: " +
        missing.map(function (p) { return "<b>" + esc(p.name) + "</b>"; }).join(", ") +
        ". Those projects have not run the ci-status workflow yet.</span></p>";
    }
    var staleProjects = live.filter(function (p) {
      return p.staleHours != null && p.staleHours > STALE_AFTER_H;
    });
    if (staleProjects.length) {
      notices += '<p class="ci-note">' + ICON.idle + "<span>Nothing built in " +
        STALE_AFTER_H + "h — these projects have not built recently: " +
        staleProjects.map(function (p) {
          return "<b>" + esc(p.name) + "</b> (" + esc(p.built.label + " " + fmtAgo(p.built.at)) + ")";
        }).join(", ") + ".</span></p>";
    }
    document.getElementById("notices").innerHTML = notices;

    document.getElementById("cards").innerHTML = projects.map(card).join("");
    var trows = [];
    live.forEach(function (p) {
      p.workflows.forEach(function (w) { trows.push(tableRow(p, w)); });
    });
    document.getElementById("rows").innerHTML = trows.join("");

    document.getElementById("loading").hidden = true;
    wire();
  }

  // --- interaction --------------------------------------------------------

  function wire() {
    var cards = document.getElementById("cards");
    var table = document.getElementById("table");
    var vCards = document.getElementById("v-cards");
    var vTable = document.getElementById("v-table");
    var onlyBad = document.getElementById("only-bad");
    var showNever = document.getElementById("show-never");
    var rows = table.querySelectorAll("tbody tr");
    var ROW = 4, GAP = 16;               // must match .ci-grid--masonry and --space-4
    var placed = null;                   // the column each card went to, by name

    // Place every visible card in a column, in sorted order, each spanning rows to
    // match its height, so cards pack upwards instead of each row growing to its
    // tallest member. A card goes to the leftmost column that is level with the
    // shortest, give or take half a card, so the cards read left to right as rows
    // do. A column clearly shorter than the rest takes the next card, which keeps
    // holes out of the middle. With more cards than a row holds, some column ends
    // short, and this puts it at the bottom right, where it reads as the end.
    //
    // keep leaves each card in its column and only restacks the columns, so that
    // opening a card's steady rows does not send its neighbours elsewhere.
    function layout(keep) {
      if (cards.hidden) return;
      var cols = getComputedStyle(cards).gridTemplateColumns.split(" ").length;
      var shown = Array.prototype.filter.call(cards.children, function (c) {
        if (c.hidden) c.style.gridColumn = c.style.gridRow = "";
        return !c.hidden;
      });
      var spans = shown.map(function (c) {
        return Math.ceil((c.getBoundingClientRect().height + GAP) / ROW);
      });
      var slack = spans.slice().sort(function (a, b) { return a - b; })[spans.length >> 1] / 2;
      var tops = [], reuse = keep && placed && placed.cols === cols, next = { cols: cols };
      for (var i = 0; i < cols; i++) tops.push(0);
      shown.forEach(function (c, i) {
        var col = reuse ? placed[c.dataset.name] : null;
        if (col == null) {
          var low = Math.min.apply(null, tops);
          for (col = 0; tops[col] > low + slack; col++) { /* the leftmost level column */ }
        }
        next[c.dataset.name] = col;
        c.style.gridColumn = String(col + 1);
        c.style.gridRow = (tops[col] + 1) + " / span " + spans[i];
        tops[col] += spans[i];
      });
      placed = next;
    }

    function view(showTable) {
      cards.hidden = showTable;
      table.hidden = !showTable;
      vCards.setAttribute("aria-pressed", String(!showTable));
      vTable.setAttribute("aria-pressed", String(showTable));
      layout();
    }
    vCards.addEventListener("click", function () { view(false); });
    vTable.addEventListener("click", function () { view(true); });

    // A recovered repo is green with its failures fixed, so it needs no attention.
    function calm(el) { return el.dataset.state === "good" || el.dataset.state === "recovered"; }

    // One filter row scopes both views, so switching between them keeps the slice.
    function applyFilters() {
      var bad = onlyBad.getAttribute("aria-pressed") === "true";
      // Off by default: never-run rows are collapsed until the toggle is pressed.
      var never = showNever.getAttribute("aria-pressed") !== "true";
      Array.prototype.forEach.call(cards.children, function (c) {
        c.hidden = bad && calm(c);
        var shown = 0;
        c.querySelectorAll(".ci-wf").forEach(function (wf) {
          wf.hidden = never && wf.dataset.never === "1";
          if (!wf.hidden) shown++;
        });
        c.querySelectorAll("[data-empty]").forEach(function (el) { el.hidden = shown > 0; });
      });
      Array.prototype.forEach.call(rows, function (r) {
        r.hidden = (bad && calm(r)) || (never && r.dataset.never === "1");
      });
      layout();
    }
    [onlyBad, showNever].forEach(function (btn) {
      btn.addEventListener("click", function () {
        btn.setAttribute("aria-pressed", String(btn.getAttribute("aria-pressed") !== "true"));
        applyFilters();
      });
    });

    cards.classList.add("ci-grid--masonry");
    applyFilters();
    addEventListener("resize", function () { layout(); });
    // Opening a card's steady rows changes its height. toggle does not bubble, so
    // the grid listens for it on the way down.
    cards.addEventListener("toggle", function () { layout(true); }, true);
    if (document.fonts && document.fonts.ready) {
      document.fonts.ready.then(function () { layout(); });
    }
    window.__ciLayout = function () { layout(); };
  }

  // --- theme: the three modes the package's useTheme hook defines ----------

  function theme() {
    var root = document.documentElement;
    var btn = document.getElementById("theme");
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
      if (window.__ciLayout) window.__ciLayout();
    });
    media.addEventListener("change", function () { if (mode() === "system") apply("system"); });
  }

  // --- go -----------------------------------------------------------------

  theme();
  var base = new URL(INDEX, location.href).href;
  fetch(INDEX, { cache: "no-cache" })
    .then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    })
    .then(function (index) {
      if (index.title) document.title = "CI · " + index.title;
      var list = index.projects || [];
      if (!list.length) throw new Error("projects.json lists no projects");
      return Promise.all(list.map(function (e) { return loadProject(e, base); }));
    })
    .then(function (projects) { render(projects.map(summarise)); })
    .catch(function (err) {
      document.getElementById("loading").innerHTML =
        "Could not load <code>" + esc(INDEX) + "</code>: " + esc(err.message || err);
    });
})();

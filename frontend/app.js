"use strict";

(() => {
  const $ = (sel) => document.querySelector(sel);
  const VIEWS = ["home", "candidates", "loading", "message", "result", "compare"];
  const QUICK_PICKS = ["The Castilian", "26 West", "Lark Austin", "Villas on Rio"];
  const LOADING_LINES = [
    "Taking a deeeep breath…",
    "Reading what reviewers say…",
    "Sniffing around for mold…",
    "Counting roaches (allegedly)…",
    "Knocking on the leasing office door…",
    "Checking under the floorboards…",
  ];
  const HOUSE_ICON = { brick: "🧱", sticks: "🪵", straw: "🌾", not_enough_data: "🤷" };
  const VERDICT_WORD = { brick: "Brick", sticks: "Sticks", straw: "Straw" };
  const TRAY_KEY = "bbw-pick-your-pig";

  let current = null; // last inspect payload
  let timers = [];
  let loadingTimer = null;

  // ---------- helpers ----------

  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v);
    }
    for (const child of children.flat()) {
      if (child == null) continue;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  }

  function show(id) {
    for (const v of VIEWS) $("#" + v).hidden = v !== id;
    if (id !== "loading") stopLoadingLines();
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  async function api(path) {
    let res;
    try {
      res = await fetch(path);
    } catch {
      throw new Error("The wolf can't reach his den. Check your connection and try again.");
    }
    let body = null;
    try { body = await res.json(); } catch { /* fall through */ }
    if (!res.ok) throw new Error((body && body.error) || "The wolf sneezed. Something went wrong.");
    return body;
  }

  function fmtDate(iso) {
    if (!iso) return "?";
    const d = new Date(iso + "T00:00:00");
    return isNaN(d) ? iso : d.toLocaleDateString(undefined, { month: "short", year: "numeric" });
  }

  function signed(n) {
    const v = Math.round(n * 10) / 10;
    return (v > 0 ? "+" : "") + v;
  }

  function toast(msg) {
    const t = $("#toast");
    t.textContent = msg;
    t.hidden = false;
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => { t.hidden = true; }, 2400);
  }

  function setUrl(params) {
    const url = new URL(location.href);
    url.search = "";
    for (const [k, v] of Object.entries(params)) url.searchParams.set(k, v);
    if (url.href !== location.href) history.pushState({}, "", url);
  }

  // ---------- wolf drawings ----------

  function mountWolves() {
    const tpl = $("#wolf-template");
    document.querySelectorAll("[data-wolf]").forEach((slot) => slot.append(tpl.content.cloneNode(true)));
    const sceneWolf = $("#scene-wolf");
    const svg = tpl.content.querySelector("svg").cloneNode(true);
    sceneWolf.append(...svg.childNodes);
  }

  // ---------- search + autocomplete ----------

  async function runSearch(q) {
    q = q.trim();
    if (q.length < 2) return;
    $("#q").value = q;
    hideSuggestions();
    startLoading(["Sniffing around Austin…", "Following the trail…"]);
    try {
      const { candidates } = await api("/api/search?q=" + encodeURIComponent(q));
      if (!candidates.length) {
        showMessage("I sniffed all over Austin and found nothing by that name.", "Try the name as it appears on Google Maps.");
        return;
      }
      renderCandidates(candidates);
    } catch (err) {
      showMessage("Huff… puff… something went wrong on the trail.", err.message);
    }
  }

  function renderCandidates(list) {
    const box = $("#candidate-list");
    box.replaceChildren(
      ...list.map((c) =>
        el("button", { class: "candidate", type: "button", onclick: () => inspect(c.data_id) },
          c.thumbnail
            ? el("img", { src: c.thumbnail, alt: "", loading: "lazy", referrerpolicy: "no-referrer" })
            : el("div", { class: "thumb-fallback", "aria-hidden": "true", text: "🏠" }),
          el("div", {},
            el("strong", { text: c.title }),
            el("span", { text: [c.address, c.rating != null ? `★ ${c.rating}` : null, c.reviews != null ? `${c.reviews} reviews` : null].filter(Boolean).join(" · ") })
          )
        )
      )
    );
    show("candidates");
  }

  let suggestTimer = null;
  let suggestSeq = 0;
  let activeSuggestion = -1;

  function hideSuggestions() {
    $("#suggestions").hidden = true;
    activeSuggestion = -1;
    $("#q").removeAttribute("aria-activedescendant");
  }

  function onType() {
    clearTimeout(suggestTimer);
    const q = $("#q").value.trim();
    if (q.length < 3) return hideSuggestions();
    suggestTimer = setTimeout(async () => {
      const seq = ++suggestSeq;
      try {
        const { suggestions } = await api("/api/autocomplete?q=" + encodeURIComponent(q));
        if (seq !== suggestSeq || document.activeElement !== $("#q")) return;
        const ul = $("#suggestions");
        ul.replaceChildren(
          ...suggestions.map((s, i) =>
            el("li", { role: "option", id: "sugg-" + i, onmousedown: (e) => { e.preventDefault(); runSearch(s.value); } },
              s.value, s.subtext ? el("small", { text: s.subtext }) : null)
          )
        );
        ul.hidden = suggestions.length === 0;
        activeSuggestion = -1;
      } catch {
        hideSuggestions(); // suggestions are optional; stay quiet
      }
    }, 400);
  }

  function onKey(e) {
    const items = [...$("#suggestions").children];
    if ($("#suggestions").hidden || !items.length) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      activeSuggestion = (activeSuggestion + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
      items.forEach((li, i) => li.setAttribute("aria-selected", String(i === activeSuggestion)));
      $("#q").setAttribute("aria-activedescendant", items[activeSuggestion].id);
    } else if (e.key === "Enter" && activeSuggestion >= 0) {
      e.preventDefault();
      runSearch(items[activeSuggestion].firstChild.textContent);
    } else if (e.key === "Escape") {
      hideSuggestions();
    }
  }

  // ---------- loading + messages ----------

  function startLoading(lines = LOADING_LINES) {
    show("loading");
    let i = 0;
    $("#loading-line").textContent = lines[0];
    stopLoadingLines();
    loadingTimer = setInterval(() => {
      i = (i + 1) % lines.length;
      $("#loading-line").textContent = lines[i];
    }, 1800);
  }

  function stopLoadingLines() {
    clearInterval(loadingTimer);
    loadingTimer = null;
  }

  function showMessage(line, detail = "") {
    $("#message-line").textContent = line;
    $("#message-detail").textContent = detail;
    show("message");
  }

  // ---------- inspect ----------

  async function inspect(dataId, { push = true } = {}) {
    if (push) setUrl({ id: dataId });
    startLoading();
    try {
      const data = await api("/api/inspect?data_id=" + encodeURIComponent(dataId));
      current = data;
      document.title = `${data.place.title} · Big Bad Wolf Inspection Agency`;
      if (data.verdict === "not_enough_data") {
        const n = data.sample.reviews_used;
        showMessage(
          `${data.narration.wolf_opening} ${data.narration.verdict_line}`,
          `${data.place.title}: only ${n} usable review${n === 1 ? "" : "s"} found. ${data.narration.closing_threat}`
        );
        return;
      }
      renderResult(data);
    } catch (err) {
      showMessage("The wolf huffed and puffed and… tripped.", err.message);
    }
  }

  function renderResult(d) {
    $("#r-title").textContent = d.place.title;
    $("#r-address").textContent = [d.place.address, d.place.rating != null ? `★ ${d.place.rating} on Google` : null].filter(Boolean).join(" · ");
    const thumb = $("#r-thumb");
    thumb.hidden = !d.place.thumbnail;
    if (d.place.thumbnail) { thumb.src = d.place.thumbnail; thumb.referrerPolicy = "no-referrer"; }

    $("#r-opening").textContent = `"${d.narration.wolf_opening}"`;
    $("#r-verdict-line").textContent = d.narration.verdict_line;
    $("#r-closing").textContent = `"${d.narration.closing_threat}"`;
    $("#r-sample").textContent =
      `Based on ${d.sample.reviews_used} reviews written ${fmtDate(d.sample.oldest)} – ${fmtDate(d.sample.newest)}.` +
      (d.cached ? " Served fresh from the wolf's den (cached)." : "");
    $("#meter").setAttribute("aria-valuenow", d.huff_risk);

    renderThemes(d.themes);
    renderWhy(d);
    updateCompareButton();
    show("result");
    playScene(d.verdict, d.huff_risk);
  }

  function renderThemes(themes) {
    const box = $("#r-themes");
    if (!themes.length) {
      box.replaceChildren(el("p", { class: "empty-themes", text: "Reviewers didn't mention anything on the wolf's checklist. Suspiciously boring." }));
      return;
    }
    box.replaceChildren(
      ...themes.map((t) =>
        el("article", { class: "theme-card" },
          el("header", {},
            el("h4", { text: t.theme }),
            el("span", { class: "tone " + t.tone, text: t.tone === "good" ? "Sturdy" : "Drafty" })
          ),
          t.wolf_comment ? el("p", { class: "wolf-comment", text: `🐺 ${t.wolf_comment}` }) : null,
          ...t.snippets.map((s) =>
            el("blockquote", { class: "snippet" },
              `"${s.text}"`,
              el("footer", { text: ["A reviewer", s.rating != null ? "★".repeat(Math.round(s.rating)) : null, s.date ? fmtDate(s.date) : null].filter(Boolean).join(" · ") })
            )
          )
        )
      )
    );
  }

  function renderWhy(d) {
    const c = d.components || {};
    const rows = d.signals_fired.map((s) =>
      el("tr", {},
        el("td", { text: s.term }),
        el("td", { class: "num", text: s.count }),
        el("td", { class: "num " + (s.weight > 0 ? "pos" : "neg"), text: signed(s.weight) }),
        el("td", { class: "num " + (s.impact > 0 ? "pos" : "neg"), text: s.impact != null ? signed(s.impact) : "" })
      )
    );
    const parts = [
      ["Starting point", c.base],
      ["Review signals", c.signals],
      [`Recent stars${c.star_average != null ? ` (avg ${c.star_average}★)` : ""}`, c.stars],
      ["Google topic chips", c.topics],
      ["Owner replies", c.owner_replies],
    ].filter(([, v]) => v != null);
    $("#r-why").replaceChildren(
      el("p", { text: "The verdict is computed by a fixed formula, not by the AI. Each review is weighted by how recent it is and how many people found it helpful; then matched phrases add or subtract risk." }),
      rows.length
        ? el("table", {},
            el("thead", {}, el("tr", {}, el("th", { text: "Phrase reviewers used" }), el("th", { class: "num", text: "Reviews" }), el("th", { class: "num", text: "Weight" }), el("th", { class: "num", text: "Impact" }))),
            el("tbody", {}, rows))
        : el("p", { text: "No checklist phrases matched." }),
      el("p", { text: parts.map(([k, v]) => `${k} ${k === "Starting point" ? v : signed(v)}`).join(" · ") + ` → ${d.huff_risk}/100 (clamped to 0–100).` }),
      el("p", { text: "Thresholds: under 35 is brick, 35–65 is sticks, over 65 is straw." })
    );
  }

  // ---------- the scene ----------

  function clearTimers() {
    timers.forEach(clearTimeout);
    timers = [];
  }

  function later(ms, fn) { timers.push(setTimeout(fn, ms)); }

  function playScene(verdict, risk) {
    clearTimers();
    const scene = $("#scene");
    const houses = document.querySelectorAll(".house");
    const stamp = $("#r-stamp");
    scene.classList.remove("has-target", "inhaling-scene", "blowing");
    houses.forEach((h) => h.classList.remove("target", "shake", "fall", "wobble", "stand"));
    stamp.className = "stamp";
    stamp.textContent = VERDICT_WORD[verdict] || "";
    $("#meter-fill").style.transition = "none";
    $("#meter-fill").style.width = "0%";
    $("#meter-number").textContent = "0";
    void scene.getBoundingClientRect(); // restart CSS animations

    const target = $("#house-" + verdict);
    scene.classList.add("has-target", "inhaling-scene");
    if (target) target.classList.add("target");

    later(1400, () => {
      scene.classList.remove("inhaling-scene");
      scene.classList.add("blowing");
      if (target) target.classList.add("shake");
      fillMeter(risk);
    });
    later(2500, () => {
      if (target) {
        target.classList.remove("shake");
        target.classList.add(verdict === "straw" ? "fall" : verdict === "sticks" ? "wobble" : "stand");
      }
      stamp.classList.add("show", verdict);
    });
  }

  function fillMeter(risk) {
    const fill = $("#meter-fill");
    fill.style.transition = "";
    fill.style.width = risk + "%";
    // Stretch the gradient over the full track so 100 reaches the red end.
    fill.style.backgroundSize = risk > 0 ? `${10000 / risk}% 100%` : "100% 100%";
    const start = performance.now();
    const duration = 1600;
    const step = (now) => {
      const t = Math.min(1, (now - start) / duration);
      $("#meter-number").textContent = Math.round(risk * (1 - Math.pow(1 - t, 3)));
      if (t < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  // ---------- share ----------

  async function share() {
    if (!current) return;
    const url = `${location.origin}/?id=${encodeURIComponent(current.data_id)}`;
    const text = `The Big Bad Wolf inspected ${current.place.title}: ${VERDICT_WORD[current.verdict] || "?"} (${current.huff_risk}/100). Satire!`;
    try {
      await navigator.clipboard.writeText(url);
      toast("Link copied. Spread the howl.");
    } catch {
      if (navigator.share) {
        try { await navigator.share({ title: "Big Bad Wolf Inspection", text, url }); } catch { /* user closed it */ }
      } else {
        window.prompt("Copy this link:", url);
      }
    }
  }

  // ---------- Pick Your Pig ----------

  function readTray() {
    try { return JSON.parse(localStorage.getItem(TRAY_KEY)) || []; } catch { return []; }
  }

  function writeTray(items) {
    try { localStorage.setItem(TRAY_KEY, JSON.stringify(items)); } catch { /* storage blocked: tray lives in memory only */ }
    trayMemory = items;
    renderTray();
  }

  let trayMemory = null;
  function tray() { return trayMemory || (trayMemory = readTray()); }

  function renderTray() {
    const items = tray();
    $("#tray").hidden = items.length === 0;
    $("#tray-chips").replaceChildren(
      ...items.map((it) =>
        el("span", { class: "chip" }, it.title,
          el("button", { type: "button", "aria-label": `Remove ${it.title}`, text: "×",
            onclick: () => writeTray(tray().filter((x) => x.data_id !== it.data_id)) }))
      )
    );
    $("#tray-go").disabled = items.length < 2;
    updateCompareButton();
  }

  function updateCompareButton() {
    const btn = $("#btn-compare");
    if (!current) return;
    const inTray = tray().some((x) => x.data_id === current.data_id);
    btn.disabled = inTray || tray().length >= 3;
    btn.textContent = inTray ? "🐷 In Pick Your Pig" : tray().length >= 3 ? "🐷 Tray full (3 max)" : "🐷 Add to Pick Your Pig";
  }

  function addToTray() {
    if (!current) return;
    const items = tray();
    if (items.length >= 3 || items.some((x) => x.data_id === current.data_id)) return;
    writeTray([...items, { data_id: current.data_id, title: current.place.title }]);
    toast(items.length + 1 >= 2 ? "Added. Ready to huff them all!" : "Added. Inspect another building to compare.");
  }

  async function runCompare(ids, { push = true } = {}) {
    if (push) setUrl({ compare: ids.join(",") });
    startLoading(["Taking the biggest breath of my life…", "Huffing at every door on the block…", ...LOADING_LINES.slice(1)]);
    try {
      const { results, survivor } = await api("/api/compare?ids=" + encodeURIComponent(ids.join(",")));
      $("#compare-list").replaceChildren(
        ...results.map((r) => {
          if (r.error) {
            return el("div", { class: "compare-card" }, el("div", { class: "house-icon", text: "❓" }), el("p", { text: r.error }));
          }
          const isSurvivor = r.data_id === survivor;
          return el("button", { class: "compare-card" + (isSurvivor ? " survivor" : ""), type: "button", onclick: () => inspect(r.data_id) },
            el("div", { class: "house-icon", text: HOUSE_ICON[r.verdict] || "🏠" }),
            el("h4", { text: r.place.title }),
            el("div", { class: "risk", text: r.huff_risk != null ? `${r.huff_risk}/100` : "Not enough data" }),
            el("p", { text: VERDICT_WORD[r.verdict] || "The trail went cold" }),
            isSurvivor ? el("p", { class: "crown", text: "👑 This little pig survives" }) : null
          );
        })
      );
      show("compare");
    } catch (err) {
      showMessage("Too much huffing at once. Even wolves get winded.", err.message);
    }
  }

  // ---------- routing ----------

  function route() {
    const params = new URLSearchParams(location.search);
    clearTimers();
    if (params.get("id")) return inspect(params.get("id"), { push: false });
    if (params.get("compare")) return runCompare(params.get("compare").split(",").filter(Boolean).slice(0, 3), { push: false });
    document.title = "Big Bad Wolf Inspection Agency";
    show("home");
  }

  function goHome() {
    history.pushState({}, "", location.pathname);
    route();
    $("#q").focus();
  }

  // ---------- boot ----------

  mountWolves();
  $("#quick-picks").replaceChildren(
    ...QUICK_PICKS.map((name) => el("button", { class: "btn btn-small", type: "button", text: name, onclick: () => runSearch(name) }))
  );
  $("#search-form").addEventListener("submit", (e) => { e.preventDefault(); runSearch($("#q").value); });
  $("#q").addEventListener("input", onType);
  $("#q").addEventListener("keydown", onKey);
  $("#q").addEventListener("blur", () => setTimeout(hideSuggestions, 150));
  $("#btn-replay").addEventListener("click", () => current && playScene(current.verdict, current.huff_risk));
  $("#btn-share").addEventListener("click", share);
  $("#btn-compare").addEventListener("click", addToTray);
  $("#tray-go").addEventListener("click", () => runCompare(tray().map((x) => x.data_id)));
  $("#message-back").addEventListener("click", goHome);
  $("#compare-back").addEventListener("click", goHome);
  window.addEventListener("popstate", route);
  renderTray();
  route();
})();

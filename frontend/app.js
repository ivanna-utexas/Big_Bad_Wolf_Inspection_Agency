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

    for (const id of ["#r-google", "#r-google-2"]) {
      const link = $(id);
      link.hidden = !d.place.google_url;
      if (d.place.google_url) link.href = d.place.google_url;
    }

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
            el("span", { class: "tone " + t.tone, text: t.tone === "good" ? "Strong" : "Weak" })
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
    houses.forEach((h) => h.classList.remove("target", "shake", "stand"));
    resetCrumble();
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
        if (verdict === "brick") target.classList.add("stand");
        else crumble(target, verdict === "straw");
      }
      stamp.classList.add("show", verdict);
    });
  }

  // ---------- crumbling houses ----------

  const GROUND = 248; // y of the grass line in the scene
  const SVG_NS = "http://www.w3.org/2000/svg";
  const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const rand = (lo, hi) => lo + Math.random() * (hi - lo);

  function resetCrumble() {
    document.querySelectorAll("#scene [data-piece]").forEach((piece) => piece.getAnimations().forEach((a) => a.cancel()));
    $("#debris").replaceChildren();
  }

  // straw: the whole house collapses into a pile. sticks: walls buckle and the roof slides off.
  function crumble(house, total) {
    const speed = reducedMotion() ? 0.01 : 1;
    const shiftX = house.transform.baseVal.numberOfItems ? house.transform.baseVal.getItem(0).matrix.e : 0;
    const pieces = [...house.querySelectorAll("[data-piece]")];
    const wall = pieces.find((p) => p.dataset.piece === "wall").getBBox();
    const pileHeight = total ? 16 : wall.height * 0.7;

    // One set of random values per kind, so a roof and its trim move together.
    const plan = {
      wall: total
        ? { skew: rand(-25, 25), dx: rand(-8, 8), delay: 0 }
        : { skew: rand(-14, -8), dx: -4, delay: 0 },
      roof: total
        ? { rot: rand(-22, 22), dx: rand(-20, 20), delay: 140 }
        : { rot: rand(-26, -18), dx: rand(-38, -28), delay: 260 },
      door: { rot: Math.random() < 0.5 ? -88 : 88, delay: 80 },
    };

    for (const piece of pieces) {
      const box = piece.getBBox();
      const kind = piece.dataset.piece;
      const p = plan[kind];
      piece.style.transformBox = "fill-box";
      piece.style.transformOrigin = kind === "wall" && !total ? "0% 100%" : "50% 100%";
      let end;
      if (kind === "wall") {
        const squash = Math.min(1, pileHeight / wall.height);
        end = `translate(${p.dx}px, 0px) skewX(${p.skew}deg) scaleY(${squash})`;
      } else if (kind === "roof") {
        const landY = total ? GROUND - pileHeight * 0.4 : GROUND;
        const dy = landY - (box.y + box.height);
        end = `translate(${p.dx}px, ${dy}px) rotate(${p.rot}deg) scaleY(${total ? 0.55 : 0.85})`;
      } else if (!total) {
        continue; // doors and windows ride along with the leaning walls... mostly
      } else {
        end = `translate(${rand(-10, 10)}px, ${GROUND - (box.y + box.height)}px) rotate(${p.rot}deg)`;
      }
      piece.animate(
        [
          { transform: "none" },
          { transform: `translate(0px, -4px) rotate(${(p.rot || p.skew || 0) * 0.15}deg)`, offset: 0.2 },
          { transform: end },
        ],
        { duration: 1100 * speed, delay: p.delay * speed, easing: "cubic-bezier(.45,0,.75,1)", fill: "forwards" }
      );
    }
    if (!total && pieces.some((p) => p.dataset.piece === "door")) {
      // When sticks buckle, the door leans with the walls.
      pieces.filter((p) => p.dataset.piece === "door").forEach((door) => {
        door.style.transformBox = "view-box";
        const box = wall;
        door.style.transformOrigin = `${box.x}px ${box.y + box.height}px`;
        door.animate([{ transform: "none" }, { transform: `skewX(${plan.wall.skew}deg) scaleY(${pileHeight / wall.height})` }],
          { duration: 1100 * speed, easing: "cubic-bezier(.45,0,.75,1)", fill: "forwards" });
      });
    }
    scatterDebris(house.dataset.kind, wall, shiftX, total ? 22 : 10, speed);
    puffDust(wall, shiftX, total ? 6 : 3, speed);
  }

  // Bits of straw or sticks blown left by the wolf, landing on the grass.
  function scatterDebris(kind, wall, shiftX, count, speed) {
    const layer = $("#debris");
    const color = kind === "straw" ? "var(--straw-dark)" : "var(--sticks-dark)";
    for (let i = 0; i < count; i++) {
      const bit = document.createElementNS(SVG_NS, "path");
      const len = kind === "straw" ? rand(8, 14) : rand(10, 18);
      bit.setAttribute("d", `M${-len / 2} 0 L${len / 2} 0`);
      bit.setAttribute("stroke", color);
      bit.setAttribute("stroke-width", kind === "straw" ? 2.5 : 4);
      bit.setAttribute("stroke-linecap", "round");
      layer.append(bit);
      const x0 = shiftX + rand(wall.x, wall.x + wall.width);
      const y0 = rand(wall.y - 40, wall.y + wall.height * 0.6);
      const x1 = Math.max(12, x0 + rand(-120, 20)); // stay inside the scene
      const y1 = GROUND - rand(0, 5);
      const spin = rand(-540, 540);
      bit.animate(
        [
          { transform: `translate(${x0}px, ${y0}px) rotate(0deg)`, opacity: 0 },
          { transform: `translate(${(x0 + x1) / 2}px, ${y0 - rand(15, 45)}px) rotate(${spin / 2}deg)`, opacity: 1, offset: 0.4 },
          { transform: `translate(${x1}px, ${y1}px) rotate(${spin}deg)`, opacity: 1 },
        ],
        { duration: rand(900, 1500) * speed, delay: rand(0, 300) * speed, easing: "ease-in", fill: "both" }
      );
    }
  }

  function puffDust(wall, shiftX, count, speed) {
    const layer = $("#debris");
    for (let i = 0; i < count; i++) {
      const cloud = document.createElementNS(SVG_NS, "circle");
      cloud.setAttribute("r", "14");
      cloud.setAttribute("fill", "var(--dust)");
      layer.append(cloud);
      const x = shiftX + wall.x + (wall.width * (i + 0.5)) / count;
      cloud.animate(
        [
          { transform: `translate(${x}px, ${GROUND - 4}px) scale(0.2)`, opacity: 0 },
          { transform: `translate(${x + rand(-10, 4)}px, ${GROUND - 14}px) scale(1.2)`, opacity: 0.85, offset: 0.35 },
          { transform: `translate(${x + rand(-30, -8)}px, ${GROUND - 26}px) scale(1.9)`, opacity: 0 },
        ],
        { duration: 1400 * speed, delay: (500 + i * 60) * speed, easing: "ease-out", fill: "both" }
      );
    }
  }

  function fillMeter(risk) {
    const fill = $("#meter-fill");
    fill.style.transition = "";
    // The bar grows from straw (left) toward brick (right): sturdier buildings fill further.
    const sturdy = 100 - risk;
    fill.style.width = sturdy + "%";
    // Stretch the gradient over the full track so the bar's tip shows the color of its zone.
    fill.style.backgroundSize = sturdy > 0 ? `${10000 / sturdy}% 100%` : "100% 100%";
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
  $("#btn-share").addEventListener("click", share);
  $("#btn-compare").addEventListener("click", addToTray);
  $("#tray-go").addEventListener("click", () => runCompare(tray().map((x) => x.data_id)));
  $("#message-back").addEventListener("click", goHome);
  $("#compare-back").addEventListener("click", goHome);
  window.addEventListener("popstate", route);
  renderTray();
  route();
})();

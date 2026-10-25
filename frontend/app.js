"use strict";

(() => {
  const $ = (sel) => document.querySelector(sel);
  const VIEWS = ["home", "candidates", "loading", "message", "result", "compare", "map"];
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
  const LIST_KEY = "bbw-my-list";

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

  let enterTimer = null;

  function show(id) {
    for (const v of VIEWS) $("#" + v).hidden = v !== id;
    const view = $("#" + id);
    view.classList.remove("enter");
    void view.offsetWidth; // restart the animation
    view.classList.add("enter");
    clearTimeout(enterTimer);
    enterTimer = setTimeout(() => view.classList.remove("enter"), 800);
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

  // The API returns huff_risk (higher = weaker). We show a quality score instead (higher = sturdier).
  function quality(risk) {
    return risk == null ? null : 100 - risk;
  }

  function fmtRent(rent) {
    const usd = (n) => "$" + Math.round(n).toLocaleString("en-US");
    return (rent.low === rent.high ? usd(rent.low) : `${usd(rent.low)}–${usd(rent.high)}`) + "/mo";
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
    if (url.href !== location.href) history.pushState({ inApp: true }, "", url);
  }

  // ---------- wolf drawings ----------

  const WOLF_IMG = { idle: "/static/img/wolf-idle.webp", inhale: "/static/img/wolf-inhale.webp", blow: "/static/img/wolf-blow.webp" };

  // Small wolves on the home, loading and message screens. Slots with class "inhaling" get the inhale pose.
  function mountWolves() {
    document.querySelectorAll("[data-wolf]").forEach((slot) => {
      const pose = slot.classList.contains("inhaling") ? "inhale" : "idle";
      slot.append(el("img", { class: "wolf", src: WOLF_IMG[pose], alt: "", decoding: "async" }));
    });
    const portrait = $(".wolf-slot.portrait");
    if (portrait) portrait.addEventListener("click", () => huffAtPicks(portrait));
    // Warm the cache so pose swaps in the scene don't flash empty.
    Object.values(WOLF_IMG).forEach((src) => { new Image().src = src; });
  }

  // Easter egg: click the home wolf and he blows on the quick-pick buttons.
  let huffing = false;
  function huffAtPicks(slot) {
    if (huffing) return;
    huffing = true;
    const img = slot.querySelector(".wolf");
    const picks = $("#quick-picks");
    img.src = WOLF_IMG.inhale;
    setTimeout(() => {
      img.src = WOLF_IMG.blow;
      slot.classList.add("huff");
      picks.classList.add("gusted");
    }, 450);
    setTimeout(() => {
      img.src = WOLF_IMG.idle;
      slot.classList.remove("huff");
      picks.classList.remove("gusted");
      huffing = false;
    }, 1250);
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
        showMessage("I sniffed all over Austin and found no homes by that name.", "I only inspect apartments, condos and other places people live in Austin. Try the name as it appears on Google Maps.");
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
    const rent = d.place.rent;
    $("#r-rent").hidden = !rent;
    $("#r-rent").textContent = rent ? `💵 Rent: ${fmtRent(rent)}${rent.source ? ` (as listed on ${rent.source})` : ""}` : "";
    $("#meter").setAttribute("aria-valuenow", quality(d.huff_risk));

    renderThemes(d.themes);
    renderWhy(d);
    updateListButton();
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
      el("p", { text: parts.map(([k, v]) => `${k} ${k === "Starting point" ? v : signed(v)}`).join(" · ") + ` → risk ${d.huff_risk}/100 (clamped to 0–100). Quality score = 100 − risk = ${quality(d.huff_risk)}/100.` }),
      el("p", { text: "Thresholds: quality over 65 is brick, 35–65 is sticks, under 35 is straw." })
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
    scene.classList.add("has-target");
    if (target) target.classList.add("target");

    // Wolf: idle (sizing up the house) -> inhale -> blow -> back to idle.
    const IDLE = 900, INHALE = 1400;
    later(IDLE, () => scene.classList.add("inhaling-scene"));
    later(IDLE + INHALE, () => {
      scene.classList.remove("inhaling-scene");
      scene.classList.add("blowing");
      if (target) target.classList.add("shake");
      fillMeter(risk);
    });
    later(IDLE + 2500, () => {
      if (target) {
        target.classList.remove("shake");
        if (verdict === "brick") target.classList.add("stand");
        else crumble(target, verdict === "straw");
      }
      stamp.classList.add("show", verdict);
    });
    // Gust is over: back to the smug idle pose.
    later(IDLE + 3200, () => scene.classList.remove("blowing"));
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
        : { skew: rand(-14, -8), dx: 4, delay: 0 }, // top leans right, with the wind
      roof: total
        ? { rot: rand(-22, 22), dx: rand(-20, 20), delay: 140 }
        : { rot: rand(18, 26), dx: rand(28, 38), delay: 260 },
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

  // Bits of straw or sticks blown right by the wolf, landing on the grass.
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
      const x1 = Math.min(688, x0 + rand(-20, 120)); // blown right, but stay inside the scene
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
          { transform: `translate(${x + rand(-4, 10)}px, ${GROUND - 14}px) scale(1.2)`, opacity: 0.85, offset: 0.35 },
          { transform: `translate(${x + rand(8, 30)}px, ${GROUND - 26}px) scale(1.9)`, opacity: 0 },
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
      $("#meter-number").textContent = Math.round(sturdy * (1 - Math.pow(1 - t, 3)));
      if (t < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  // ---------- share ----------

  async function share() {
    if (!current) return;
    const url = `${location.origin}/?id=${encodeURIComponent(current.data_id)}`;
    const text = `The Big Bad Wolf inspected ${current.place.title}: ${VERDICT_WORD[current.verdict] || "?"} (${quality(current.huff_risk)}/100). Satire!`;
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

  // ---------- My List ----------

  const LIST_MAX = 10;
  let listMemory = null;

  function readList() {
    try { return JSON.parse(localStorage.getItem(LIST_KEY)) || []; } catch { return []; }
  }

  function myList() { return listMemory || (listMemory = readList()); }

  function writeList(items) {
    listMemory = items;
    try { localStorage.setItem(LIST_KEY, JSON.stringify(items)); } catch { /* storage blocked: list lasts for this visit only */ }
    renderList();
  }

  function renderList() {
    refreshMapHearts();
    const items = myList();
    const count = $("#list-count");
    count.hidden = items.length === 0;
    count.textContent = items.length;
    $("#list-empty").hidden = items.length > 0;
    $("#list-items").replaceChildren(
      ...items.map((it) =>
        el("li", { class: "list-item" },
          el("button", { class: "open", type: "button", onclick: () => { setListOpen(false); inspect(it.data_id); } },
            el("span", { class: "house-icon", "aria-hidden": "true", text: HOUSE_ICON[it.verdict] || "🏠" }),
            el("span", { class: "info" },
              el("strong", { text: it.title }),
              el("small", { text: it.huff_risk != null ? `${VERDICT_WORD[it.verdict]} · ${quality(it.huff_risk)}/100` : "Not enough data" })
            )
          ),
          el("button", { class: "remove", type: "button", "aria-label": `Remove ${it.title} from My List`, text: "×",
            onclick: () => writeList(myList().filter((x) => x.data_id !== it.data_id)) })
        )
      )
    );
    $("#list-compare").hidden = items.length === 0;
    $("#list-compare").disabled = items.length < 2;
    $("#list-hint").textContent = items.length === 1 ? "Add one more home to compare." : "";
    updateListButton();
  }

  function updateListButton() {
    if (!current) return;
    const btn = $("#btn-list");
    const entry = myList().find((x) => x.data_id === current.data_id);
    const saved = Boolean(entry);
    if (entry && (entry.verdict !== current.verdict || entry.huff_risk !== current.huff_risk)) {
      // Keep the saved verdict in sync if the score changed since it was added.
      writeList(myList().map((x) => (x === entry ? { ...x, verdict: current.verdict, huff_risk: current.huff_risk } : x)));
      return;
    }
    btn.setAttribute("aria-pressed", String(saved));
    btn.textContent = saved ? "♥ In your list" : "♡ Add to list";
  }

  function toggleCurrentInList() {
    if (!current) return;
    const items = myList();
    if (items.some((x) => x.data_id === current.data_id)) {
      writeList(items.filter((x) => x.data_id !== current.data_id));
      toast("Removed from My List.");
      return;
    }
    if (items.length >= LIST_MAX) {
      toast(`My List holds ${LIST_MAX} homes. Remove one first.`);
      return;
    }
    writeList([...items, { data_id: current.data_id, title: current.place.title, verdict: current.verdict, huff_risk: current.huff_risk }]);
    const btn = $("#btn-list");
    btn.classList.remove("pop");
    void btn.offsetWidth;
    btn.classList.add("pop");
    const count = $("#list-count");
    count.classList.remove("bump");
    void count.offsetWidth;
    count.classList.add("bump");
    toast("Added to My List ♥");
  }

  function setListOpen(open) {
    $("#my-list").classList.toggle("open", open); // CSS slides it in/out
    const toggle = $("#list-toggle");
    toggle.setAttribute("aria-expanded", String(open));
    toggle.setAttribute("aria-label", open ? "Close My List" : "Open My List");
    toggle.hidden = open; // the panel sits where the button is, so hide the button while it's open
    if (open) $("#list-close").focus();
  }

  let compareResults = [];

  async function runCompare(ids, { push = true } = {}) {
    if (push) setUrl({ compare: ids.join(",") });
    startLoading(["Taking the biggest breath of my life…", "Huffing at every door on the block…", ...LOADING_LINES.slice(1)]);
    try {
      ({ results: compareResults } = await api("/api/compare?ids=" + encodeURIComponent(ids.join(","))));
      renderCompare();
      show("compare");
    } catch (err) {
      showMessage("Too much huffing at once. Even wolves get winded.", err.message);
    }
  }

  function renderCompare() {
    const filter = readRentFilter($("#cmp-rent-filter"));
    const visible = compareResults.filter((r) => rentMatches(r.place && r.place.rent, filter));
    // Same rule as the server: the lowest risk survives. Re-picked among the homes the filter leaves.
    const scored = visible.filter((r) => !r.error && r.huff_risk != null);
    const survivor = scored.length
      ? scored.reduce((a, b) => (b.huff_risk < a.huff_risk || (b.huff_risk === a.huff_risk && b.data_id < a.data_id) ? b : a)).data_id
      : null;
    $("#compare-list").replaceChildren(
      ...visible.map((r) => {
        if (r.error) {
          return el("div", { class: "compare-card" }, el("div", { class: "house-icon", text: "❓" }), el("p", { text: r.error }));
        }
        const isSurvivor = r.data_id === survivor;
        const rent = r.place.rent;
        return el("button", { class: "compare-card" + (isSurvivor ? " survivor" : ""), type: "button", onclick: () => inspect(r.data_id) },
          el("div", { class: "house-icon", text: HOUSE_ICON[r.verdict] || "🏠" }),
          el("h4", { text: r.place.title }),
          el("div", { class: "risk", text: r.huff_risk != null ? `${quality(r.huff_risk)}/100` : "Not enough data" }),
          el("p", { text: VERDICT_WORD[r.verdict] || "The trail went cold" }),
          el("p", { class: "rent", text: rent ? `💵 ${fmtRent(rent)}` : "💵 Rent unknown" }),
          isSurvivor ? el("p", { class: "crown", text: "👑 This house stays standing" }) : null
        );
      })
    );
    const active = filter.min != null || filter.max != null;
    $("#cmp-rent-count").textContent = active ? `Showing ${visible.length} of ${compareResults.length} homes` : "";
    if (active && !visible.length) {
      $("#compare-list").append(el("p", { class: "sub compare-empty", text: "No homes on your list fit that rent. Try a wider range." }));
    }
  }

  // ---------- neighborhood map ----------

  const LEAFLET_CSS = "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css";
  const LEAFLET_JS = "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js";
  const WEST_CAMPUS = [30.2862, -97.747];

  // Tiny versions of the three houses from the scene (32x32).
  const PIN_SVG = {
    straw: `<svg viewBox="0 0 32 32" aria-hidden="true">
      <rect x="8" y="15" width="16" height="13" fill="var(--straw)" stroke="var(--ink)" stroke-width="1.6"/>
      <path d="M11 17v9M14 16v10M18 16v10M21 17v9" stroke="var(--straw-dark)" stroke-width="1"/>
      <path d="M4 17 L16 5 L28 17 Z" fill="var(--straw-dark)" stroke="var(--ink)" stroke-width="1.6" stroke-linejoin="round"/>
      <rect x="14" y="21" width="5" height="7" rx="2.5" fill="var(--door)" stroke="var(--ink)" stroke-width="1"/></svg>`,
    sticks: `<svg viewBox="0 0 32 32" aria-hidden="true">
      <rect x="7" y="14" width="18" height="14" fill="var(--sticks)" stroke="var(--ink)" stroke-width="1.6"/>
      <path d="M10 14v14M13 14v14M19 14v14M22 14v14" stroke="var(--sticks-dark)" stroke-width="1.4"/>
      <path d="M4 15 L16 5 L28 15 Z" fill="var(--sticks-dark)" stroke="var(--ink)" stroke-width="1.6" stroke-linejoin="round"/>
      <rect x="14" y="21" width="5" height="7" fill="var(--door)" stroke="var(--ink)" stroke-width="1"/></svg>`,
    brick: `<svg viewBox="0 0 32 32" aria-hidden="true">
      <rect x="6" y="13" width="20" height="15" fill="var(--brick)" stroke="var(--ink)" stroke-width="1.6"/>
      <path d="M6 18h20M6 23h20M11 13v5M21 13v5M16 18v5M11 23v5M21 23v5" stroke="var(--mortar)" stroke-width="1"/>
      <rect x="20" y="4" width="4" height="7" fill="var(--brick)" stroke="var(--ink)" stroke-width="1.2"/>
      <path d="M3 14 L16 4 L29 14 Z" fill="var(--roof)" stroke="var(--ink)" stroke-width="1.6" stroke-linejoin="round"/>
      <rect x="13.5" y="20" width="5" height="8" fill="var(--door)" stroke="var(--ink)" stroke-width="1"/></svg>`,
  };

  let leafletLoading = null;
  let mapState = null; // { map, layer, markers: Map<data_id, marker> }

  function loadLeaflet() {
    if (window.L) return Promise.resolve();
    if (!leafletLoading) {
      leafletLoading = new Promise((resolve, reject) => {
        document.head.append(el("link", { rel: "stylesheet", href: LEAFLET_CSS }));
        const script = el("script", { src: LEAFLET_JS });
        script.onload = resolve;
        script.onerror = () => { leafletLoading = null; reject(new Error("The map paper blew away. Try again in a moment.")); };
        document.head.append(script);
      });
    }
    return leafletLoading;
  }

  function mapStatus(text) {
    $("#map-status").hidden = !text;
    $("#map-status").textContent = text || "";
  }

  function ensureMap(tileUrl) {
    if (mapState) return mapState;
    const map = L.map("map-canvas", { scrollWheelZoom: false, zoomControl: true }).setView(WEST_CAMPUS, 15);
    L.tileLayer(tileUrl, {
      maxZoom: 19,
      subdomains: "abcd",
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
    }).addTo(map);
    mapState = { map, layer: L.layerGroup().addTo(map), markers: new Map() };
    return mapState;
  }

  function popupFor(b) {
    return el("div", { class: "map-popup" },
      el("strong", { text: b.title }),
      el("p", { class: "verdict", text: `${HOUSE_ICON[b.verdict]} ${VERDICT_WORD[b.verdict]} · ${quality(b.huff_risk)}/100` }),
      el("p", { class: "rent", text: b.rent ? `💵 ${fmtRent(b.rent)}` : "💵 Rent unknown" }),
      el("button", { class: "btn btn-small", type: "button", text: "Read the inspection", onclick: () => inspect(b.data_id) })
    );
  }

  // Small heart badge for homes on My List (same heart as the list icon).
  const PIN_HEART = `<svg class="pin-heart" viewBox="13.5 21 21 17" aria-hidden="true">
    <path d="M24 37 C 15.5 31, 14.5 26.5, 17 23.8 C 19.6 21, 23 22.4, 24 25.2 C 25 22.4, 28.4 21, 31 23.8 C 33.5 26.5, 32.5 31, 24 37 Z"
          fill="var(--accent)" stroke="var(--ink)" stroke-width="1.5" stroke-linejoin="round"/></svg>`;

  function pinIcon(b, onList) {
    return L.divIcon({
      className: "house-pin",
      html: PIN_SVG[b.verdict] + (onList ? PIN_HEART : ""),
      iconSize: [30, 30], iconAnchor: [15, 28], popupAnchor: [0, -26],
    });
  }

  // Re-draw pins so hearts match My List (called whenever the list changes).
  function refreshMapHearts() {
    if (!mapState) return;
    const ids = new Set(myList().map((x) => x.data_id));
    for (const [id, marker] of mapState.markers) marker.setIcon(pinIcon(marker.building, ids.has(id)));
  }

  // ---- rent filter ----

  // Works for any rent filter form (map or compare page): fields named min, max, unknown.
  function readRentFilter(form) {
    const num = (name) => {
      const v = form.elements[name].value.trim();
      return v === "" || isNaN(+v) ? null : Math.max(0, +v);
    };
    return { min: num("min"), max: num("max"), unknown: form.elements.unknown.checked };
  }

  function clearRentForm(form) {
    form.elements.min.value = "";
    form.elements.max.value = "";
    form.elements.unknown.checked = true;
  }

  function rentMatches(rent, { min, max, unknown }) {
    if (min == null && max == null) return true;
    if (!rent) return unknown;
    // Show a home if any part of its rent range falls inside the chosen range.
    return (min == null || rent.high >= min) && (max == null || rent.low <= max);
  }

  function applyMapFilter() {
    if (!mapState) return;
    const filter = readRentFilter($("#rent-filter"));
    let shown = 0;
    for (const [id, marker] of mapState.markers) {
      const keep = rentMatches(marker.building.rent, filter) || id === mapState.focusId;
      if (keep) { shown += 1; if (!mapState.layer.hasLayer(marker)) mapState.layer.addLayer(marker); }
      else mapState.layer.removeLayer(marker);
    }
    const total = mapState.markers.size;
    const active = filter.min != null || filter.max != null;
    $("#rent-count").textContent = active && total ? `Showing ${shown} of ${total} homes` : "";
  }

  function clearRentFilter() {
    clearRentForm($("#rent-filter"));
    applyMapFilter();
  }

  async function openMap(focusId = null, { push = true } = {}) {
    if (push) setUrl({ map: "1" });
    document.title = "Neighborhood map · Big Bad Wolf Inspection Agency";
    show("map");
    mapStatus("Unrolling the map…");
    let buildings, tileUrl;
    try {
      [, { buildings, tile_url: tileUrl }] = await Promise.all([loadLeaflet(), api("/api/map")]);
    } catch (err) {
      mapStatus(err.message);
      return;
    }
    const { map, layer, markers } = ensureMap(tileUrl);
    map.invalidateSize(); // the section was hidden when Leaflet first measured it
    layer.clearLayers();
    markers.clear();
    const listIds = new Set(myList().map((x) => x.data_id));
    for (const b of buildings) {
      const onList = listIds.has(b.data_id);
      const marker = L.marker([b.lat, b.lng], {
        icon: pinIcon(b, onList),
        title: b.title,
        alt: `${b.title}: ${VERDICT_WORD[b.verdict]}${onList ? " (on My List)" : ""}`,
      })
        .bindPopup(() => popupFor(b))
        .addTo(layer);
      marker.building = b;
      markers.set(b.data_id, marker);
    }
    mapState.focusId = focusId; // the home you came from stays visible even if the rent filter would hide it
    applyMapFilter();

    const focus = focusId && markers.get(focusId);
    if (focus) {
      map.setView(focus.getLatLng(), 17);
      focus.openPopup();
    } else if (buildings.length) {
      map.fitBounds(L.latLngBounds(buildings.map((b) => [b.lat, b.lng])), { padding: [30, 30], maxZoom: 16 });
    } else {
      map.setView(WEST_CAMPUS, 15);
    }
    mapStatus(
      !buildings.length ? "No homes on the map yet. Inspect one and it will move in here."
        : focusId && !focus ? "This home isn't on the map yet. Find it through search to pin it."
        : ""
    );
  }

  async function shareMap() {
    const url = `${location.origin}/?map=1`;
    try {
      await navigator.clipboard.writeText(url);
      toast("Map link copied. Show everyone the straw huts.");
    } catch {
      window.prompt("Copy this link:", url);
    }
  }

  // ---------- routing ----------

  function route() {
    const params = new URLSearchParams(location.search);
    clearTimers();
    if (params.get("id")) return inspect(params.get("id"), { push: false });
    if (params.get("map")) return openMap(null, { push: false });
    if (params.get("compare")) return runCompare(params.get("compare").split(",").filter(Boolean).slice(0, LIST_MAX), { push: false });
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
  $("#btn-list").addEventListener("click", toggleCurrentInList);
  $("#list-toggle").addEventListener("click", () => setListOpen(!$("#my-list").classList.contains("open")));
  $("#list-close").addEventListener("click", () => { setListOpen(false); $("#list-toggle").focus(); });
  $("#list-compare").addEventListener("click", () => {
    setListOpen(false);
    runCompare(myList().map((x) => x.data_id));
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && $("#my-list").classList.contains("open")) { setListOpen(false); $("#list-toggle").focus(); }
  });
  $("#message-back").addEventListener("click", goHome);
  $("#compare-back").addEventListener("click", goHome);
  $("#btn-map-home").addEventListener("click", () => openMap());
  $("#btn-map").addEventListener("click", () => current && openMap(current.data_id));
  $("#map-share").addEventListener("click", shareMap);
  $("#rent-filter").addEventListener("input", applyMapFilter);
  $("#rent-filter").addEventListener("submit", (e) => e.preventDefault());
  $("#rent-clear").addEventListener("click", clearRentFilter);
  $("#cmp-rent-filter").addEventListener("input", renderCompare);
  $("#cmp-rent-filter").addEventListener("submit", (e) => e.preventDefault());
  $("#cmp-rent-clear").addEventListener("click", () => { clearRentForm($("#cmp-rent-filter")); renderCompare(); });
  // Go back to wherever the map was opened from; visitors who arrived on a shared map link go home instead.
  $("#map-back").addEventListener("click", () => (history.state && history.state.inApp ? history.back() : goHome()));
  window.addEventListener("popstate", route);
  renderList();
  route();
})();

"use strict";

const THRESHOLD = 140;   // px of dragging needed to lock in a decision
const FLY_MS = 280;      // keep equal to the transform transition in #card (CSS)

// Sidebar bars. `warn`: "low" = red when nearly empty, "high" = red when nearly full.
const STATS = [
  { key: "health",             label: "Health",    min: 0,   max: 50,  warn: "low" },
  { key: "stamina",            label: "Stamina",   min: 0,   max: 100, warn: "low" },
  { key: "supplies",           label: "Supplies",  min: 0,   max: 20 },
  { key: "trust",              label: "Trust",     min: -20, max: 20 },
  { key: "infection_exposure", label: "Infection", min: 0,   max: 100, warn: "high" },
];

const $ = (id) => document.getElementById(id);
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const TOOLS = ["radio", "diary"];   // buttons that appear once the item is picked up
const PAGE_TEXT = {
  radio: { title: "Radio", empty: "Nothing but static." },
  diary: { title: "Diary", empty: "The pages are still blank." },
};

const card = $("card");
const statEls = {};       // key -> { box, val, fill }
let scene = null;         // the scene currently shown (server payload)
let lastState = null;     // previous stats, to flash changes
let busy = false;         // true while a card is flying away / loading
let dragging = false, startX = 0, dx = 0;
const seen = { radio: null, diary: null };   // what the player has already read
let openedByUs = false;                      // did WE push the #radio / #diary history entry?

// ---- server ------------------------------------------------------------------

async function api(path, body) {
  const options = body === undefined ? undefined : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
  const res = await fetch(path, options);
  return { ok: res.ok, data: await res.json() };
}

// ---- rendering -----------------------------------------------------------------

function replay(el) {            // restart a CSS animation
  el.classList.remove("in");
  void el.offsetWidth;
  el.classList.add("in");
}

function buildStats() {
  for (const stat of STATS) {
    const box = document.createElement("div");
    box.className = "stat";
    box.innerHTML =
      `<div class="stat-head"><span>${stat.label}</span><span class="val">0</span></div>` +
      `<div class="bar"><div class="fill"></div></div>`;
    $("stats").appendChild(box);
    statEls[stat.key] = { box, val: box.querySelector(".val"), fill: box.querySelector(".fill") };
  }
}

function renderStats(state) {
  for (const stat of STATS) {
    const value = state[stat.key] ?? 0;
    const pct = Math.max(0, Math.min(1, (value - stat.min) / (stat.max - stat.min)));
    const el = statEls[stat.key];

    el.val.textContent = value;
    el.fill.style.width = `${pct * 100}%`;
    el.box.classList.toggle(
      "danger",
      (stat.warn === "low" && pct <= 0.25) || (stat.warn === "high" && pct >= 0.5)
    );

    if (lastState && lastState[stat.key] !== value) {
      const rose = value > lastState[stat.key];
      const good = stat.warn === "high" ? !rose : rose;   // more infection is bad
      el.box.classList.remove("good", "bad");
      void el.box.offsetWidth;
      el.box.classList.add(good ? "good" : "bad");
      setTimeout(() => el.box.classList.remove("good", "bad"), 1200);
    }
  }
  lastState = { ...state };
}

const signature = (items) => items.join("\u0001");

function renderTools(s) {
  for (const key of TOOLS) {
    const unlocked = s[`has_${key}`];
    const items = s[key] || [];
    const button = $(`btn-${key}`);
    button.hidden = !unlocked;
    const isNew = unlocked && items.length > 0 && signature(items) !== seen[key];
    button.querySelector(".dot").hidden = !isNew;
  }
}

function syncPage() {                 // show / hide the full-screen page from the URL hash
  const key = location.hash.slice(1);
  const page = $("page");
  if (!TOOLS.includes(key) || !scene || !scene[`has_${key}`]) {
    page.hidden = true;
    return;
  }
  const items = scene[key];
  page.className = key;
  $("page-title").textContent = PAGE_TEXT[key].title;

  const rows = key === "radio" ? [...items].reverse() : items;   // radio: newest first
  $("page-list").replaceChildren(...(rows.length ? rows : [PAGE_TEXT[key].empty]).map((text) => {
    const li = document.createElement("li");
    if (!rows.length) {
      li.className = "empty";
      li.textContent = text;
    } else if (key === "radio" && /^\[.+?\]/.test(text)) {   // "[stamp] message"
      const [, stamp, body] = text.match(/^\[(.+?)\]\s*(.*)$/);
      const s1 = document.createElement("span");
      s1.className = "stamp";
      s1.textContent = stamp;
      li.append(s1, body);
    } else {
      li.textContent = text;          // textContent: never parsed as HTML
    }
    return li;
  }));
  page.hidden = false;
  page.scrollTop = 0;
  seen[key] = signature(items);
  renderTools(scene);                 // clears the "new" dot
}

function openPage(key) {
  openedByUs = true;
  if (location.hash === `#${key}`) syncPage(); else location.hash = key;
}

function closePage() {
  if (openedByUs) {
    openedByUs = false;
    history.back();                   // pops the hash we pushed
  } else {
    history.replaceState(null, "", location.pathname + location.search);
    syncPage();
  }
}

function render(s) {
  scene = s;
  $("clock").textContent = s.clock;
  $("title").textContent = s.title;
  $("text").replaceChildren(...s.text.map((t) => {
    const p = document.createElement("p");
    p.textContent = t;                 // textContent: story text is never parsed as HTML
    return p;
  }));

  const img = $("scene-image");
  img.hidden = !s.image;
  if (s.image) img.src = s.image;
  replay($("story"));

  $("reveal-left").textContent = s.left || "";
  $("reveal-right").textContent = s.right || "";
  card.hidden = s.ending;
  $("hint").hidden = s.ending;
  $("ending").hidden = !s.ending;
  if (s.ending) {
    $("ending-days").textContent = `Days survived: ${s.state.days}`;
    $("ending-best").textContent = `BEST: ${s.best}   |   RUN #${s.runs}`;
  }

  $("day-line").innerHTML = "";
  $("day-line").append(`Day ${s.state.days}`);
  const small = document.createElement("small");
  small.textContent = `${s.clock}   |   ${s.state.height} m above ground`;
  $("day-line").appendChild(small);

  renderStats(s.state);
  renderTools(s);
  $("record").textContent = s.runs ? `Best: ${s.best} days  |  Runs: ${s.runs}` : "";
}

// ---- the card -------------------------------------------------------------------

function setReveal(x) {
  // Dragging left reveals the LEFT decision, dragging right reveals the RIGHT one.
  const p = Math.min(Math.abs(x) / THRESHOLD, 1);
  $("reveal-left").style.opacity = x < 0 ? p : 0;
  $("reveal-right").style.opacity = x > 0 ? p : 0;
}

function applyDrag(x) {
  card.style.transform = `translateX(${x}px) rotate(${x * 0.04}deg)`;
  setReveal(x);
}

function springBack() {
  card.style.transform = "";
  card.style.opacity = "";
  setReveal(0);
}

function enterCard() {             // new blank card grows in from below
  card.style.transition = "none";
  card.style.transform = "translateY(24px) scale(0.94)";
  card.style.opacity = "0";
  setReveal(0);
  void card.offsetWidth;
  card.style.transition = "";
  card.style.transform = "";
  card.style.opacity = "";
}

async function commit(side) {
  busy = true;
  const dir = side === "left" ? -1 : 1;
  card.classList.remove("dragging");
  setReveal(dir * THRESHOLD);                                   // decision fully visible
  card.style.transform = `translateX(${dir * window.innerWidth * 0.6}px) rotate(${dir * 18}deg)`;
  card.style.opacity = "0";

  try {
    // Animation and network run together, so the next card is usually ready on arrival.
    const [res] = await Promise.all([
      api("/api/choose", { scene_id: scene.scene_id, side }),
      sleep(FLY_MS),
    ]);
    if (res.data && res.data.scene_id) {   // success, or 409 with the true current scene
      render(res.data);
      enterCard();
    } else {
      springBack();
    }
  } catch (err) {
    console.error(err);
    springBack();
  }
  busy = false;
}

card.addEventListener("pointerdown", (e) => {
  if (busy || !scene || scene.ending) return;
  dragging = true;
  startX = e.clientX;
  dx = 0;
  card.setPointerCapture(e.pointerId);   // keep receiving moves outside the card
  card.classList.add("dragging");
});

card.addEventListener("pointermove", (e) => {
  if (!dragging) return;
  dx = e.clientX - startX;
  applyDrag(dx);
});

function release() {
  if (!dragging) return;
  dragging = false;
  card.classList.remove("dragging");
  if (Math.abs(dx) >= THRESHOLD) {
    commit(dx < 0 ? "left" : "right");
  } else {
    springBack();                        // not far enough: cancel
  }
  dx = 0;
}
card.addEventListener("pointerup", release);
card.addEventListener("pointercancel", release);

// ---- buttons + start --------------------------------------------------------------

$("toggle").addEventListener("click", () => {
  const closed = $("app").classList.toggle("closed");
  $("toggle").setAttribute("aria-expanded", String(!closed));
});

for (const key of TOOLS) $(`btn-${key}`).addEventListener("click", () => openPage(key));
$("page-back").addEventListener("click", closePage);
window.addEventListener("hashchange", () => {
  if (!location.hash) openedByUs = false;
  syncPage();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$("page").hidden) closePage();
});

$("restart").addEventListener("click", async () => {
  lastState = null;
  const res = await api("/api/restart", {});
  render(res.data);
  enterCard();
});

buildStats();
api("/api/scene").then((res) => {
  render(res.data);
  syncPage();                         // handles reloading while on #radio / #diary
});

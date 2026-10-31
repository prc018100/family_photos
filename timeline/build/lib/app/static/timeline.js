"use strict";

const boot = JSON.parse(document.getElementById("boot").textContent);
const scroller = document.getElementById("scroller");
const strip = document.getElementById("strip");
const empty = document.getElementById("empty");
const banner = document.getElementById("banner");
const filters = document.getElementById("filters");
const scrub = document.getElementById("scrub");
const scrubReadout = document.getElementById("scrub-readout");
const dialog = document.getElementById("lightbox");

const state = {
  days: [],
  prefix: [],
  total: 0,
  olderCursor: null,
  newerCursor: null,
  bounds: null,
  busy: false,
  seen: new Set(),
  edge: "latest",
};

let layout = { rows: 2, gap: 8, dayGap: 22, label: 128, tile: 180 };
let renderedKey = "";
let scrubReady = false;
let lightboxIndex = -1;

function showBanner(text) {
  banner.hidden = !text;
  banner.textContent = text || "";
}

function columnWidth(count) {
  const cols = Math.max(1, Math.ceil(count / layout.rows));
  const grid = cols * layout.tile + Math.max(0, cols - 1) * layout.gap;
  return layout.label + layout.gap + grid + layout.dayGap;
}

function gridWidth(count) {
  const cols = Math.max(1, Math.ceil(count / layout.rows));
  return cols * layout.tile + Math.max(0, cols - 1) * layout.gap;
}

function measure() {
  const narrow = window.matchMedia("(max-width: 720px)").matches;
  const height = scroller.clientHeight || 480;
  // One row keeps the date beside the pictures. A day with many photos grows wider.
  const rows = 1;
  const gap = narrow ? 8 : 10;
  const dayGap = narrow ? 16 : 28;
  const label = narrow ? 88 : 132;
  const available = Math.max(160, height - 36);
  const tile = Math.max(160, Math.min(available, narrow ? 280 : 460));
  layout = { rows, gap, dayGap, label, tile };
  strip.style.setProperty("--rows", String(rows));
  strip.style.setProperty("--gap", gap + "px");
  strip.style.setProperty("--day-gap", dayGap + "px");
  strip.style.setProperty("--label", label + "px");
  strip.style.setProperty("--tile", tile + "px");
}

function layoutStrip() {
  const prefix = [];
  let total = 0;
  for (const day of state.days) {
    prefix.push(total);
    total += columnWidth(day.photos.length);
  }
  state.prefix = prefix;
  state.total = total;
  strip.style.width = Math.max(total, 1) + "px";
  empty.hidden = state.days.length !== 0;
  strip.hidden = state.days.length === 0;
}

function mergeDays(current, incoming) {
  const byKey = new Map(current.map((day) => [day.key, { ...day, photos: day.photos.slice() }]));
  for (const day of incoming) {
    const existing = byKey.get(day.key);
    if (!existing) {
      byKey.set(day.key, { ...day, photos: day.photos.slice() });
      continue;
    }
    const seen = new Set(existing.photos.map((photo) => photo.id));
    for (const photo of day.photos) {
      if (!seen.has(photo.id)) existing.photos.push(photo);
    }
    existing.photos.sort((a, b) => a.taken_at.localeCompare(b.taken_at) || a.id.localeCompare(b.id));
  }
  return [...byKey.values()].sort((a, b) => a.key.localeCompare(b.key));
}

function anchorAtScroll() {
  if (!state.days.length) return null;
  const x = scroller.scrollLeft;
  for (let index = 0; index < state.days.length; index += 1) {
    const start = state.prefix[index];
    const width = columnWidth(state.days[index].photos.length);
    if (x < start + width) return { key: state.days[index].key, offset: x - start };
  }
  const last = state.days.length - 1;
  return { key: state.days[last].key, offset: 0 };
}

function restoreAnchor(anchor) {
  if (!anchor) return;
  const index = state.days.findIndex((day) => day.key === anchor.key);
  if (index < 0) return;
  scroller.scrollLeft = state.prefix[index] + anchor.offset;
}

function visibleIndexes() {
  const left = scroller.scrollLeft - 1200;
  const right = scroller.scrollLeft + scroller.clientWidth + 1200;
  const indexes = [];
  for (let index = 0; index < state.days.length; index += 1) {
    const start = state.prefix[index];
    const end = start + columnWidth(state.days[index].photos.length);
    if (end >= left && start <= right) indexes.push(index);
  }
  return indexes;
}

function renderDay(index) {
  const day = state.days[index];
  const column = document.createElement("section");
  column.className = "day" + (index === 0 || day.year !== state.days[index - 1].year ? " is-new-year" : "");
  column.style.width = columnWidth(day.photos.length) + "px";

  const label = document.createElement("div");
  label.className = "day-label";
  const year = document.createElement("p");
  year.className = "year";
  year.textContent = String(day.year);
  const weekday = document.createElement("p");
  weekday.className = "weekday";
  weekday.textContent = day.weekday;
  const daynum = document.createElement("p");
  daynum.className = "daynum";
  daynum.textContent = String(Number(day.key.slice(8)));
  const month = document.createElement("p");
  month.className = "month";
  month.textContent = day.month_label.replace(` ${day.year}`, "");
  label.append(year, weekday, daynum, month);

  const grid = document.createElement("div");
  grid.className = "day-grid";
  grid.style.width = gridWidth(day.photos.length) + "px";
  for (const item of day.photos) grid.append(renderTile(item, day));

  column.append(label, grid);
  return column;
}

function renderTile(item, day) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "tile";
  const image = document.createElement("img");
  image.src = item.thumbnail_url;
  image.alt = [item.filename || "Photo", day.label, item.live ? "Live Photo" : "", item.type === "VIDEO" ? "Video" : ""]
    .filter(Boolean)
    .join(", ");
  image.decoding = "async";
  button.append(image);
  if (item.live || item.type === "VIDEO") {
    const badge = document.createElement("span");
    badge.className = "badge";
    badge.textContent = item.live ? "Live" : "Video";
    button.append(badge);
  }
  button.addEventListener("click", () => openLightbox(item));
  return button;
}

function renderSlice() {
  if (!state.days.length) {
    strip.replaceChildren();
    renderedKey = "";
    return;
  }
  const indexes = visibleIndexes();
  const key = indexes.join(",") + ":" + layout.tile + ":" + layout.rows + ":" + state.days.length;
  if (key === renderedKey) return;
  renderedKey = key;
  if (!indexes.length) return;
  const first = indexes[0];
  const last = indexes[indexes.length - 1];
  const left = document.createElement("div");
  left.className = "spacer";
  left.style.width = state.prefix[first] + "px";
  const right = document.createElement("div");
  right.className = "spacer";
  const lastEnd = state.prefix[last] + columnWidth(state.days[last].photos.length);
  right.style.width = Math.max(0, state.total - lastEnd) + "px";
  const columns = indexes.map(renderDay);
  strip.replaceChildren(left, ...columns, right);
}

function flatPhotos() {
  return state.days.flatMap((day) => day.photos);
}

function utcDay(iso) {
  const [year, month, day] = iso.split("-").map(Number);
  return Date.UTC(year, month - 1, day);
}

function isoFromUtc(ms) {
  const value = new Date(ms);
  const month = String(value.getUTCMonth() + 1).padStart(2, "0");
  const day = String(value.getUTCDate()).padStart(2, "0");
  return `${value.getUTCFullYear()}-${month}-${day}`;
}

function formatWhen(item) {
  const [datePart, timePart] = item.taken_at.split("T");
  const [year, month, day] = datePart.split("-").map(Number);
  const [hourText, minute] = timePart.split(":");
  const hour = Number(hourText);
  const suffix = hour >= 12 ? "PM" : "AM";
  const hour12 = hour % 12 || 12;
  const label = new Date(year, month - 1, day).toLocaleDateString(undefined, {
    weekday: "long",
    month: "long",
    day: "numeric",
    year: "numeric",
  });
  return `${label} · ${hour12}:${minute} ${suffix}`;
}

function placeLine(item) {
  return [item.city, item.state, item.country].filter(Boolean).join(", ");
}

function setBounds(bounds, position) {
  state.bounds = bounds;
  if (!bounds || !bounds.earliest || !bounds.latest) {
    scrub.disabled = true;
    scrubReadout.textContent = "No photos yet";
    scrub.setAttribute("aria-valuetext", "No photos yet");
    return;
  }
  const span = Math.max(0, Math.round((utcDay(bounds.latest) - utcDay(bounds.earliest)) / 86400000));
  scrub.disabled = false;
  scrub.min = "0";
  scrub.max = String(span);
  if (position && position.kind === "latest") scrub.value = String(span);
  if (position && position.kind === "date") {
    const offset = Math.round((utcDay(position.date) - utcDay(bounds.earliest)) / 86400000);
    scrub.value = String(Math.min(span, Math.max(0, offset)));
  }
  const jump = document.getElementById("jump-date");
  jump.min = bounds.earliest;
  jump.max = bounds.latest;
  updateScrubLabel();
  scrubReady = true;
}

function scrubDate() {
  if (!state.bounds || !state.bounds.earliest) return "";
  return isoFromUtc(utcDay(state.bounds.earliest) + Number(scrub.value) * 86400000);
}

function updateScrubLabel() {
  const label = scrub.disabled ? "No photos yet" : scrubDate();
  scrubReadout.textContent = label || "No photos yet";
  scrub.setAttribute("aria-valuetext", scrubReadout.textContent);
}

function formCriteria() {
  return {
    person_id: [...document.querySelectorAll("input[name=person]:checked")].map((input) => input.value),
    city: document.getElementById("city").value,
    state: document.getElementById("state").value,
    country: document.getElementById("country").value,
    start: document.getElementById("start").value,
    end: document.getElementById("end").value,
    q: document.getElementById("q").value.trim(),
  };
}

function paramsFromForm(extra) {
  const params = new URLSearchParams();
  const criteria = formCriteria();
  for (const id of criteria.person_id) params.append("person_id", id);
  for (const key of ["city", "state", "country", "start", "end", "q"]) {
    if (criteria[key]) params.set(key, criteria[key]);
  }
  if (extra) {
    for (const [key, value] of Object.entries(extra)) {
      if (value) params.set(key, value);
    }
  }
  return params;
}

function remember(params) {
  const visible = new URLSearchParams(params);
  visible.delete("edge");
  visible.delete("cursor");
  const next = visible.toString();
  history.replaceState(null, "", next ? `?${next}` : location.pathname);
}

async function fetchJson(url) {
  const response = await fetch(url);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || "The photo library could not be reached");
  return body;
}

async function loadFresh(extra) {
  state.busy = true;
  state.seen = new Set();
  showBanner("");
  try {
    const params = paramsFromForm(extra);
    remember(params);
    const page = await fetchJson("/api/timeline?" + params.toString());
    state.days = page.groups;
    state.olderCursor = page.older_cursor;
    state.newerCursor = page.newer_cursor;
    measure();
    layoutStrip();
    if (extra.jump) {
      state.edge = "jump";
      setBounds(page.bounds, { kind: "date", date: extra.jump });
      scroller.scrollLeft = 0;
    } else if (extra.edge === "earliest") {
      state.edge = "earliest";
      setBounds(page.bounds, { kind: "date", date: page.bounds.earliest });
      scroller.scrollLeft = 0;
    } else {
      state.edge = "latest";
      setBounds(page.bounds, { kind: "latest" });
      scroller.scrollLeft = Math.max(0, state.total - scroller.clientWidth);
    }
    renderedKey = "";
    renderSlice();
  } catch (error) {
    showBanner(error.message);
    state.days = [];
    layoutStrip();
  } finally {
    state.busy = false;
  }
  if (!state.days.length && !banner.hidden) return;
  if (state.edge === "latest") {
    await fill("older");
    scroller.scrollLeft = Math.max(0, state.total - scroller.clientWidth);
  } else {
    await fill("newer");
    scroller.scrollLeft = 0;
  }
  renderedKey = "";
  renderSlice();
}

async function loadSide(side) {
  const cursor = side === "older" ? state.olderCursor : state.newerCursor;
  if (!cursor || state.busy || state.seen.has(cursor)) return false;
  state.busy = true;
  state.seen.add(cursor);
  const anchor = anchorAtScroll();
  const beforeCount = state.days.reduce((sum, day) => sum + day.photos.length, 0);
  try {
    const page = await fetchJson("/api/timeline?cursor=" + encodeURIComponent(cursor));
    state.days = mergeDays(state.days, page.groups);
    if (side === "older") state.olderCursor = page.older_cursor;
    else state.newerCursor = page.newer_cursor;
    layoutStrip();
    restoreAnchor(anchor);
    renderedKey = "";
    renderSlice();
    const afterCount = state.days.reduce((sum, day) => sum + day.photos.length, 0);
    return afterCount > beforeCount;
  } catch (error) {
    showBanner(error.message);
    return false;
  } finally {
    state.busy = false;
  }
}

async function fill(side) {
  let guard = 0;
  while (guard < 6 && state.total < scroller.clientWidth + 240) {
    const added = await loadSide(side);
    if (!added) break;
    guard += 1;
  }
}

function maybeLoadMore() {
  if (state.busy || !state.days.length) return;
  if (scroller.scrollLeft < 700) loadSide("older");
  if (scroller.scrollLeft + scroller.clientWidth > state.total - 700) loadSide("newer");
}

function fillSelect(id, values, placeholder) {
  const select = document.getElementById(id);
  select.replaceChildren();
  const any = document.createElement("option");
  any.value = "";
  any.textContent = placeholder;
  select.append(any);
  for (const value of values) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value;
    select.append(option);
  }
}

function ensureOption(id, value) {
  if (!value) return;
  const select = document.getElementById(id);
  if (![...select.options].some((option) => option.value === value)) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value;
    select.append(option);
  }
  select.value = value;
}

function renderPeople(people) {
  const host = document.getElementById("people");
  host.replaceChildren();
  for (const person of people) {
    if (!person.name.trim()) continue;
    const input = document.createElement("input");
    input.type = "checkbox";
    input.name = "person";
    input.value = person.id;
    input.id = "person-" + person.id;
    input.hidden = true;
    const chip = document.createElement("label");
    chip.className = "person";
    chip.htmlFor = input.id;
    const image = document.createElement("img");
    image.src = person.thumbnail_url;
    image.alt = "";
    image.addEventListener("error", () => {
      image.replaceWith(Object.assign(document.createElement("span"), { className: "avatar" }));
    });
    const name = document.createElement("span");
    name.textContent = person.name;
    chip.append(image, name);
    input.addEventListener("change", () => chip.classList.toggle("selected", input.checked));
    host.append(input, chip);
  }
}

function applyParamsToForm(params) {
  document.getElementById("q").value = params.get("q") || "";
  document.getElementById("start").value = params.get("start") || "";
  document.getElementById("end").value = params.get("end") || "";
  ensureOption("city", params.get("city") || "");
  ensureOption("state", params.get("state") || "");
  ensureOption("country", params.get("country") || "");
  const selected = new Set(params.getAll("person_id"));
  for (const input of document.querySelectorAll("input[name=person]")) {
    input.checked = selected.has(input.value);
    input.dispatchEvent(new Event("change"));
  }
}

async function loadFilters() {
  const data = await fetchJson("/api/filters");
  renderPeople(data.people);
  const unnamed = data.people.filter((person) => !person.name.trim()).length;
  document.getElementById("unnamed").textContent = unnamed
    ? `${unnamed} unnamed ${unnamed === 1 ? "face is" : "faces are"} hidden until you name them in Immich.`
    : "";
  fillSelect("city", data.cities, "Any city");
  fillSelect("state", data.states, "Any state");
  fillSelect("country", data.countries, "Any country");
}

function paintLightbox(item) {
  const image = document.getElementById("lightbox-img");
  image.src = item.preview_url;
  image.alt = item.filename || "Photo";
  const meta = document.getElementById("lightbox-meta");
  meta.replaceChildren();
  const heading = document.createElement("h2");
  heading.textContent = formatWhen(item);
  const place = document.createElement("p");
  place.textContent = placeLine(item) || "No place recorded";
  const people = document.createElement("p");
  const names = item.people.map((person) => person.name.trim() || "Unnamed");
  people.textContent = names.length ? names.join(", ") : "No people tagged";
  const file = document.createElement("p");
  const notes = [item.filename, item.live ? "Live Photo" : "", item.type === "VIDEO" ? "Video" : ""].filter(Boolean);
  file.textContent = notes.join(" · ");
  if (item.type === "VIDEO") {
    const play = document.createElement("p");
    play.textContent = "Playback stays in Immich. This view uses the preview frame.";
    meta.append(heading, place, people, file, play);
  } else {
    meta.append(heading, place, people, file);
  }
  const link = document.getElementById("lightbox-link");
  const safe = typeof item.immich_url === "string" && /^https?:\/\//.test(item.immich_url);
  link.hidden = !safe;
  if (safe) link.href = item.immich_url;
}

async function openLightbox(item) {
  const photos = flatPhotos();
  lightboxIndex = photos.findIndex((photo) => photo.id === item.id);
  paintLightbox(item);
  if (!dialog.open) dialog.showModal();
  const url = new URL(location.href);
  url.searchParams.set("photo", item.id);
  history.replaceState(null, "", url);
  const requested = item.id;
  try {
    const detail = await fetchJson("/api/assets/" + requested);
    const current = lightboxIndex >= 0 ? flatPhotos()[lightboxIndex] : null;
    if (dialog.open && (!current || current.id === requested)) paintLightbox(detail);
  } catch (_error) {
    // The tile already has the date, place, and people Immich returned with the page.
  }
}

function stepLightbox(delta) {
  const photos = flatPhotos();
  if (!photos.length || lightboxIndex < 0) return;
  lightboxIndex = (lightboxIndex + delta + photos.length) % photos.length;
  openLightbox(photos[lightboxIndex]);
}

let scrollScheduled = false;
scroller.addEventListener("scroll", () => {
  if (scrollScheduled) return;
  scrollScheduled = true;
  requestAnimationFrame(() => {
    scrollScheduled = false;
    renderSlice();
    maybeLoadMore();
  });
}, { passive: true });

window.addEventListener("resize", () => {
  const anchor = anchorAtScroll();
  measure();
  layoutStrip();
  restoreAnchor(anchor);
  renderedKey = "";
  renderSlice();
});

document.addEventListener("keydown", (event) => {
  if (event.target.closest("input, select, textarea")) return;
  if (dialog.open && event.key === "ArrowRight") {
    event.preventDefault();
    stepLightbox(1);
  } else if (dialog.open && event.key === "ArrowLeft") {
    event.preventDefault();
    stepLightbox(-1);
  } else if (event.key === "ArrowRight") {
    scroller.scrollLeft += layout.tile;
  } else if (event.key === "ArrowLeft") {
    scroller.scrollLeft -= layout.tile;
  }
});

filters.addEventListener("submit", (event) => {
  event.preventDefault();
  filters.classList.remove("open");
  document.getElementById("filter-toggle").setAttribute("aria-expanded", "false");
  loadFresh({ edge: "latest" });
});

document.getElementById("clear").addEventListener("click", () => {
  for (const id of ["q", "start", "end", "city", "state", "country", "person-filter"]) {
    document.getElementById(id).value = "";
  }
  document.getElementById("person-filter").dispatchEvent(new Event("input"));
  for (const input of document.querySelectorAll("input[name=person]")) {
    input.checked = false;
    input.dispatchEvent(new Event("change"));
  }
  loadFresh({ edge: "latest" });
});

document.getElementById("filter-toggle").addEventListener("click", () => {
  const open = filters.classList.toggle("open");
  document.getElementById("filter-toggle").setAttribute("aria-expanded", open ? "true" : "false");
});

document.getElementById("person-filter").addEventListener("input", (event) => {
  const query = event.target.value.trim().toLowerCase();
  for (const chip of document.querySelectorAll(".person")) {
    const name = chip.querySelector("span").textContent.toLowerCase();
    chip.hidden = query !== "" && !name.includes(query);
  }
});

scrub.addEventListener("input", updateScrubLabel);
scrub.addEventListener("change", () => {
  if (!scrubReady || scrub.disabled) return;
  const date = scrubDate();
  document.getElementById("jump-date").value = date;
  loadFresh({ jump: date });
});

document.getElementById("jump").addEventListener("click", () => {
  const date = document.getElementById("jump-date").value;
  if (!date) return;
  loadFresh({ jump: date });
});

document.getElementById("lightbox-close").addEventListener("click", () => dialog.close());
document.getElementById("lightbox-prev").addEventListener("click", () => stepLightbox(-1));
document.getElementById("lightbox-next").addEventListener("click", () => stepLightbox(1));
dialog.addEventListener("click", (event) => {
  if (event.target === dialog) dialog.close();
});
dialog.addEventListener("close", () => {
  const url = new URL(location.href);
  url.searchParams.delete("photo");
  history.replaceState(null, "", url);
});

async function start() {
  measure();
  if (!boot.configured) {
    showBanner("Set IMMICH_API_KEY and recreate the timeline container. Create the key in Immich under Account settings → API Keys.");
    scrubReadout.textContent = "Waiting for an API key";
    return;
  }
  try {
    await loadFilters();
  } catch (error) {
    showBanner(error.message);
    return;
  }
  const params = new URLSearchParams(location.search);
  applyParamsToForm(params);
  if (params.get("jump")) await loadFresh({ jump: params.get("jump") });
  else await loadFresh({ edge: params.get("edge") === "earliest" ? "earliest" : "latest" });
  const photoId = params.get("photo");
  if (photoId) {
    const found = flatPhotos().find((item) => item.id === photoId);
    if (found) openLightbox(found);
    else {
      try {
        openLightbox(await fetchJson("/api/assets/" + photoId));
      } catch (error) {
        showBanner(error.message);
      }
    }
  }
}

start();

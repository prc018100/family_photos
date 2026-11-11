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
  layouts: [],
  textSearch: false,
  busy: false,
  seen: new Set(),
  edge: "latest",
};

// The collage band fills the window top to bottom. Search, filters and the
// date controls live in the corner menu. Columns hold one to three photos stacked at their own
// shape, so portraits and landscapes pack without cropping.
const BAND = 720;
let layout = { band: BAND, gap: 6, dayGap: 18, minDay: 112 };
let renderedKey = "";
let scrubReady = false;
let lightboxIndex = -1;
let peopleFirst = readFocus();
const measuredRatio = new Map();

function readFocus() {
  const fromUrl = new URLSearchParams(location.search).get("focus");
  if (fromUrl === "all") return false;
  if (fromUrl === "people") return true;
  try {
    return localStorage.getItem("timeline-focus") !== "all";
  } catch (_error) {
    return true;
  }
}

function showBanner(text) {
  banner.hidden = !text;
  banner.textContent = text || "";
}

function hasPeople(item) {
  return Array.isArray(item.people) && item.people.length > 0;
}

function ratioOf(item) {
  const raw = measuredRatio.get(item.id) || item.ratio || 1;
  // Panoramas and very tall crops would swallow or vanish from a column.
  return Math.min(2.2, Math.max(0.5, raw));
}

// People first: every photo with a recognized person, plus a few scenery
// shots so a trip or a holiday without faces still shows up.
function shownPhotos(day) {
  if (!peopleFirst || state.textSearch) return day.photos;
  const people = day.photos.filter(hasPeople);
  const others = day.photos.filter((item) => !hasPeople(item));
  const allowance = Math.min(others.length, people.length ? Math.floor(people.length / 4) : 2);
  const keep = new Set(people.map((item) => item.id));
  if (allowance > 0) {
    const step = others.length / allowance;
    for (let index = 0; index < allowance; index += 1) {
      keep.add(others[Math.floor(index * step + step / 2)].id);
    }
  }
  return day.photos.filter((item) => keep.has(item.id));
}

function packColumns(photos) {
  const height = layout.band;
  const gap = layout.gap;
  const columns = [];
  let index = 0;
  while (index < photos.length) {
    // Most columns are stacks of two or three. When an odd number of photos
    // is left, one photo with people gets a column to itself, so a day ends
    // on a full stack instead of a stray single.
    const lead = photos[index];
    const left = photos.length - index;
    const feature = left % 2 === 1 && (hasPeople(lead) || left === 1);
    const target = height * (feature ? 0.75 : 0.4);
    let best = null;
    let inverse = 0;
    for (let count = 1; count <= 3 && index + count <= photos.length; count += 1) {
      const item = photos[index + count - 1];
      inverse += 1 / ratioOf(item);
      const width = (height - gap * (count - 1)) / inverse;
      const slice = photos.slice(index, index + count);
      // Faces stay readable: a photo with people never lands in a three-high stack.
      if (count === 3 && slice.some(hasPeople)) break;
      const score = Math.abs(Math.log(width / target));
      if (!best || score < best.score) best = { count, width, score };
    }
    const items = photos.slice(index, index + best.count);
    columns.push({ width: Math.round(best.width), items });
    index += best.count;
  }
  return columns;
}

function layoutDay(day) {
  const shown = shownPhotos(day);
  const columns = packColumns(shown);
  const grid = columns.reduce((sum, column) => sum + column.width, 0) + Math.max(0, columns.length - 1) * layout.gap;
  return { shown, columns, grid, width: Math.max(grid, layout.minDay) + layout.dayGap };
}

function dayWidth(index) {
  return state.layouts[index].width;
}

function measure() {
  const narrow = window.matchMedia("(max-width: 720px)").matches;
  const height = scroller.clientHeight || window.innerHeight || BAND;
  const edge = narrow ? 6 : 10;
  const band = Math.max(200, height - edge * 2);
  layout = {
    band,
    gap: narrow ? 4 : 6,
    dayGap: narrow ? 6 : 10,
    minDay: narrow ? 96 : 112,
  };
  strip.style.setProperty("--band", band + "px");
  strip.style.setProperty("--gap", layout.gap + "px");
  strip.style.setProperty("--day-gap", layout.dayGap + "px");
}

function layoutStrip() {
  const prefix = [];
  const layouts = [];
  let total = 0;
  for (const day of state.days) {
    const placed = layoutDay(day);
    prefix.push(total);
    layouts.push(placed);
    total += placed.width;
  }
  state.prefix = prefix;
  state.layouts = layouts;
  state.total = total;
  strip.style.width = Math.max(total, 1) + "px";
  empty.hidden = state.days.length !== 0;
  strip.hidden = state.days.length === 0;
}

function copyKey(item) {
  if (!item.moment) return "id:" + item.id;
  const shape = item.ratio == null ? "?" : item.ratio >= 1 ? "wide" : "tall";
  return item.moment + "|" + shape;
}

// The server drops copies within a page. A copy can land on the next page,
// so the browser applies the same rule when pages are merged.
function dropCopies(photos) {
  const best = new Map();
  for (const item of photos) {
    const key = copyKey(item);
    const current = best.get(key);
    if (!current || item.people.length > current.people.length) best.set(key, item);
  }
  const keep = new Set([...best.values()].map((item) => item.id));
  const seen = new Set();
  return photos.filter((item) => {
    if (!keep.has(item.id) || seen.has(item.id)) return false;
    seen.add(item.id);
    return true;
  });
}

function mergeDays(current, incoming) {
  const byKey = new Map(current.map((day) => [day.key, { ...day, photos: day.photos.slice() }]));
  for (const day of incoming) {
    const existing = byKey.get(day.key);
    if (!existing) {
      byKey.set(day.key, { ...day, photos: dropCopies(day.photos) });
      continue;
    }
    existing.photos = dropCopies(existing.photos.concat(day.photos));
    existing.photos.sort((a, b) => a.taken_at.localeCompare(b.taken_at) || a.id.localeCompare(b.id));
  }
  return [...byKey.values()].sort((a, b) => a.key.localeCompare(b.key));
}

function anchorAtScroll() {
  if (!state.days.length || !state.layouts.length) return null;
  const x = scroller.scrollLeft;
  for (let index = 0; index < state.days.length; index += 1) {
    const start = state.prefix[index];
    if (x < start + dayWidth(index)) return { key: state.days[index].key, offset: x - start };
  }
  const last = state.days.length - 1;
  return { key: state.days[last].key, offset: 0 };
}

function restoreAnchor(anchor) {
  if (!anchor) return;
  const index = state.days.findIndex((day) => day.key === anchor.key);
  if (index < 0) return;
  scroller.scrollLeft = state.prefix[index] + Math.min(anchor.offset, dayWidth(index));
}

function visibleIndexes() {
  const left = scroller.scrollLeft - 1200;
  const right = scroller.scrollLeft + scroller.clientWidth + 1200;
  const indexes = [];
  for (let index = 0; index < state.days.length; index += 1) {
    const start = state.prefix[index];
    const end = start + dayWidth(index);
    if (end >= left && start <= right) indexes.push(index);
  }
  return indexes;
}

function renderDay(index) {
  const day = state.days[index];
  const placed = state.layouts[index];
  const newYear = index === 0 || day.year !== state.days[index - 1].year;
  const column = document.createElement("section");
  column.className = "day" + (newYear ? " is-new-year" : "");
  column.style.width = placed.width + "px";

  const label = document.createElement("h2");
  label.className = "day-label";
  const when = document.createElement("span");
  when.className = "when";
  const month = day.month_label.replace(` ${day.year}`, "").slice(0, 3);
  when.textContent = `${day.weekday.slice(0, 3)} ${month} ${Number(day.key.slice(8))}`;
  label.append(when);
  if (newYear || day.show_year) {
    const year = document.createElement("span");
    year.className = "year";
    year.textContent = String(day.year);
    label.prepend(year);
  }

  const band = document.createElement("div");
  band.className = "band";
  band.style.width = placed.grid + "px";
  for (const stack of placed.columns) {
    const holder = document.createElement("div");
    holder.className = "stack";
    holder.style.width = stack.width + "px";
    for (const item of stack.items) {
      const cellHeight = (stack.width / ratioOf(item));
      holder.append(renderTile(item, day, stack.width, cellHeight));
    }
    band.append(holder);
  }

  column.append(label, band);
  return column;
}

let relayoutTimer = 0;
function relayoutSoon() {
  clearTimeout(relayoutTimer);
  relayoutTimer = setTimeout(() => {
    const anchor = anchorAtScroll();
    layoutStrip();
    restoreAnchor(anchor);
    renderedKey = "";
    renderSlice();
  }, 180);
}

function renderTile(item, day, width, height) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "tile" + (hasPeople(item) ? " has-people" : "");
  // Scaled up: a lone tile with a grow factor under 1 would not fill its column.
  button.style.flexGrow = String(100 / ratioOf(item));
  const image = document.createElement("img");
  // Thumbnails are about 250px on the short side. Bigger cells get the preview.
  const short = Math.min(width, height) * (window.devicePixelRatio || 1);
  image.src = short > 320 ? item.preview_url : item.thumbnail_url;
  const names = (item.people || []).map((person) => person.name.trim()).filter(Boolean);
  image.alt = [names.join(", ") || item.filename || "Photo", day.label, item.live ? "Live Photo" : "", item.type === "VIDEO" ? "Video" : ""]
    .filter(Boolean)
    .join(", ");
  image.decoding = "async";
  image.loading = "lazy";
  image.addEventListener("load", () => {
    if (!image.naturalWidth || !image.naturalHeight || measuredRatio.has(item.id)) return;
    const actual = image.naturalWidth / image.naturalHeight;
    // Trust the pixels when Immich's recorded size disagrees (or is missing).
    if (!item.ratio || Math.abs(Math.log(actual / item.ratio)) > 0.1) {
      measuredRatio.set(item.id, actual);
      relayoutSoon();
    }
  });
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
  const key = indexes.join(",") + ":" + layout.band + ":" + state.days.length + ":" + state.total;
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
  const lastEnd = state.prefix[last] + dayWidth(last);
  right.style.width = Math.max(0, state.total - lastEnd) + "px";
  const columns = indexes.map(renderDay);
  strip.replaceChildren(left, ...columns, right);
}

function flatPhotos() {
  if (state.layouts.length === state.days.length) return state.layouts.flatMap((placed) => placed.shown);
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
  if (!peopleFirst) visible.set("focus", "all");
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
    state.textSearch = Boolean(params.get("q"));
    remember(params);
    const page = await fetchJson("/api/timeline?" + params.toString());
    state.days = page.groups.map((day) => ({ ...day, photos: dropCopies(day.photos) }));
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
    scroller.scrollLeft += layout.band;
  } else if (event.key === "ArrowLeft") {
    scroller.scrollLeft -= layout.band;
  }
});

filters.addEventListener("submit", (event) => {
  event.preventDefault();
  closeMenu();
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

const menu = document.getElementById("menu");
const menuToggle = document.getElementById("menu-toggle");

function openMenu() {
  menu.hidden = false;
  menuToggle.setAttribute("aria-expanded", "true");
  document.getElementById("q").focus({ preventScroll: true });
}

function closeMenu() {
  if (menu.hidden) return;
  menu.hidden = true;
  menuToggle.setAttribute("aria-expanded", "false");
}

menuToggle.addEventListener("click", () => (menu.hidden ? openMenu() : closeMenu()));
document.getElementById("menu-close").addEventListener("click", closeMenu);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !menu.hidden && !dialog.open) closeMenu();
});
scroller.addEventListener("pointerdown", closeMenu);

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
  closeMenu();
  loadFresh({ jump: date });
});

document.getElementById("jump").addEventListener("click", () => {
  const date = document.getElementById("jump-date").value;
  if (!date) return;
  closeMenu();
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

function paintFocus() {
  for (const button of document.querySelectorAll("#focus button")) {
    const active = (button.dataset.focus === "people") === peopleFirst;
    button.setAttribute("aria-pressed", active ? "true" : "false");
  }
}

document.getElementById("focus").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-focus]");
  if (!button) return;
  const next = button.dataset.focus === "people";
  if (next === peopleFirst) return;
  peopleFirst = next;
  try {
    localStorage.setItem("timeline-focus", next ? "people" : "all");
  } catch (_error) {
    // Private windows can refuse storage. The URL still carries the choice.
  }
  paintFocus();
  const url = new URL(location.href);
  if (next) url.searchParams.delete("focus");
  else url.searchParams.set("focus", "all");
  history.replaceState(null, "", url);
  const anchor = anchorAtScroll();
  layoutStrip();
  restoreAnchor(anchor);
  renderedKey = "";
  renderSlice();
  maybeLoadMore();
});

async function start() {
  paintFocus();
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

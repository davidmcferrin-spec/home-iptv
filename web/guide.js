const VIEW_KEY = "home-iptv-view";
const ADULT_KEY = "home-iptv-adult";
const FAV_KEY = "home-iptv-favorites";
const COUNTS_KEY = "home-iptv-tune-counts";
const PREV_KEY = "home-iptv-previous";
const SLOT_MS = 30 * 60 * 1000;

const video = document.getElementById("video");
const stage = document.getElementById("stage");

let channels = [];
let catalog = [];
let programs = [];
let focus = { row: 0, col: 0 };
let chip = "all";
let windowStart = floorHalfHour(Date.now());
let currentId = null;
let numberBuffer = "";
let numberTimer = 0;
let peer = null;
let hlsPlayer = null;
let started = false;

function floorHalfHour(ms) {
  return ms - (ms % SLOT_MS);
}

function loadView() {
  try {
    const saved = JSON.parse(localStorage.getItem(VIEW_KEY) || "{}");
    return {
      country: saved.country || "",
      languages: Array.isArray(saved.languages) ? saved.languages : [],
      tags: Array.isArray(saved.tags) ? saved.tags : [],
      sort: saved.sort || "number",
      order: saved.order || "asc",
      startChannelId: saved.startChannelId || null,
    };
  } catch (err) {
    if (err instanceof SyntaxError) {
      return { country: "", languages: [], tags: [], sort: "number", order: "asc", startChannelId: null };
    }
    throw err;
  }
}

function showAdult() {
  return sessionStorage.getItem(ADULT_KEY) === "1";
}

function favorites() {
  try {
    const value = JSON.parse(localStorage.getItem(FAV_KEY) || "[]");
    return Array.isArray(value) ? value : [];
  } catch (err) {
    if (err instanceof SyntaxError) return [];
    throw err;
  }
}

function tuneCounts() {
  try {
    const value = JSON.parse(localStorage.getItem(COUNTS_KEY) || "{}");
    return value && typeof value === "object" ? value : {};
  } catch (err) {
    if (err instanceof SyntaxError) return {};
    throw err;
  }
}

function isPhone() {
  const w = window.innerWidth;
  const h = window.innerHeight;
  return w <= 720 || (h <= 500 && w <= 980);
}

function slotCount() {
  if (isPhone()) return 2;
  if (window.innerWidth < 1100) return 3;
  return 4;
}

function syncLayout() {
  document.documentElement.classList.toggle("is-phone", isPhone());
  const hint = document.getElementById("hint-text");
  hint.textContent = isPhone()
    ? "Tap a channel. Swipe the grid."
    : "Arrows move · Enter watch · 0-9 tune · PgUp channel";
}

function displayName(kind, code) {
  try {
    const name = new Intl.DisplayNames(["en"], { type: kind }).of(code);
    return name || code.toUpperCase();
  } catch (err) {
    if (err instanceof RangeError) return code.toUpperCase();
    throw err;
  }
}

function toast(message) {
  const node = document.getElementById("toast");
  node.hidden = false;
  node.textContent = message;
  window.clearTimeout(toast.timer);
  toast.timer = window.setTimeout(() => {
    node.hidden = true;
  }, 3200);
}

function clockTick() {
  const now = new Date();
  let hours = now.getHours();
  const meridian = hours >= 12 ? "PM" : "AM";
  hours = hours % 12 || 12;
  document.getElementById("clock").textContent = `${hours}:${String(now.getMinutes()).padStart(2, "0")}`;
  document.getElementById("meridian").textContent = meridian;
  document.getElementById("weekday").textContent = now.toLocaleDateString("en-US", { weekday: "long" });
  document.getElementById("monthday").textContent = now.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  placeNowLine();
}

function formatWhen(ms) {
  const date = new Date(ms);
  let hours = date.getHours();
  const meridian = hours >= 12 ? "PM" : "AM";
  hours = hours % 12 || 12;
  return `${hours}:${String(date.getMinutes()).padStart(2, "0")} ${meridian}`;
}

function episodeLine(program) {
  if (!program) return "";
  const bits = [];
  if (program.season != null && program.episode != null) bits.push(`S${program.season} E${program.episode}`);
  else if (program.episode != null) bits.push(`E${program.episode}`);
  if (program.episode_title) bits.push(program.episode_title);
  return bits.join("  ");
}

async function fetchChannels(params) {
  const query = new URLSearchParams(params);
  if (showAdult()) query.set("include_adult", "true");
  query.set("limit", "500");
  const response = await fetch(`/api/channels?${query}`);
  if (!response.ok) throw new Error(`channel list returned ${response.status}`);
  return (await response.json()).channels;
}

function activeFilters() {
  const view = loadView();
  if (chip === "all") return { country: "", languages: [], tags: [], sort: view.sort, order: view.order };
  if (chip === "saved") {
    return view;
  }
  if (chip.startsWith("tag:")) {
    return { country: "", languages: [], tags: [chip.slice(4)], sort: view.sort, order: view.order };
  }
  if (chip.startsWith("lang:")) {
    return { country: "", languages: [chip.slice(5)], tags: [], sort: view.sort, order: view.order };
  }
  if (chip.startsWith("country:")) {
    return { country: chip.slice(8), languages: [], tags: [], sort: view.sort, order: view.order };
  }
  return { country: "", languages: [], tags: [], sort: view.sort, order: view.order };
}

async function loadGuide() {
  const filters = activeFilters();
  const params = { sort: filters.sort, order: filters.order };
  if (filters.country) params.country = filters.country;
  const list = await fetchChannels(params);
  let next = list.filter((channel) => {
    if (filters.languages.length && !filters.languages.includes(channel.language)) return false;
    if (filters.tags.length && !channel.tags.some((tag) => filters.tags.includes(tag.name))) return false;
    return true;
  });
  if (chip === "favorites") {
    const favs = new Set(favorites());
    next = next.filter((channel) => favs.has(channel.id));
  }
  if (chip === "popular") {
    const counts = tuneCounts();
    next.sort((a, b) => (counts[b.id] || 0) - (counts[a.id] || 0) || a.guide_number.localeCompare(b.guide_number));
  }
  channels = next;
  if (focus.row >= channels.length) focus.row = 0;
  await loadPrograms();
  if (!catalog.length) catalog = await fetchChannels({ sort: "number", order: "asc" });
  render();
  if (!started) {
    started = true;
    const wanted = loadView().startChannelId;
    const match = channels.find((channel) => channel.id === wanted);
    if (match) tune(match);
  }
}

async function loadPrograms() {
  const slots = slotCount();
  const start = new Date(windowStart).toISOString();
  const end = new Date(windowStart + slots * SLOT_MS + 2 * 60 * 60 * 1000).toISOString();
  const query = new URLSearchParams({ start, end });
  if (showAdult()) query.set("include_adult", "true");
  const response = await fetch(`/api/programs?${query}`);
  if (!response.ok) throw new Error(`programs returned ${response.status}`);
  programs = (await response.json()).programs;
}

function programsFor(channel) {
  return programs.filter((program) => program.channel_id === channel.id);
}

function currentProgram(channel) {
  const now = Date.now();
  return programsFor(channel).find((program) => Date.parse(program.start_at) <= now && Date.parse(program.stop_at) > now) || null;
}

function nextProgram(channel) {
  const now = Date.now();
  return programsFor(channel)
    .filter((program) => Date.parse(program.start_at) > now)
    .sort((a, b) => Date.parse(a.start_at) - Date.parse(b.start_at))[0] || null;
}

function layoutRow(channel, slots) {
  const end = windowStart + slots * SLOT_MS;
  const visible = programsFor(channel)
    .filter((program) => Date.parse(program.stop_at) > windowStart && Date.parse(program.start_at) < end)
    .sort((a, b) => Date.parse(a.start_at) - Date.parse(b.start_at));
  if (!visible.length) {
    return [{ title: channel.title, sub: channel.group_title || "Live", col: 2, span: slots, left: false, right: false, program: null }];
  }
  return visible.map((program) => {
    const start = Math.max(Date.parse(program.start_at), windowStart);
    const stop = Math.min(Date.parse(program.stop_at), end);
    const col = 2 + Math.max(0, Math.round((start - windowStart) / SLOT_MS));
    const span = Math.max(1, Math.round((stop - start) / SLOT_MS));
    return {
      title: program.title,
      sub: episodeLine(program) || `${formatWhen(Date.parse(program.start_at))} – ${formatWhen(Date.parse(program.stop_at))}`,
      col,
      span: Math.max(1, Math.min(span, slots - (col - 2))),
      left: Date.parse(program.start_at) < windowStart - 1000,
      right: Date.parse(program.stop_at) > end + 1000,
      program,
    };
  });
}

function heartSvg(on) {
  return `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 20s-7-4.4-7-9a4 4 0 0 1 7-2 4 4 0 0 1 7 2c0 4.6-7 9-7 9z" fill="${on ? "currentColor" : "none"}" stroke="currentColor" stroke-width="1.6"/></svg>`;
}

function replaySvg() {
  return `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 8V4L3 9l5 5V10a6 6 0 1 1-2 4.6" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
}

function logoNode(channel) {
  if (channel.logo_url) {
    const img = document.createElement("img");
    img.className = "logo";
    img.alt = "";
    img.src = channel.logo_url;
    return img;
  }
  const fallback = document.createElement("span");
  fallback.className = "logo-fallback";
  fallback.textContent = (channel.title || "?").slice(0, 2).toUpperCase();
  return fallback;
}

function renderChips() {
  const host = document.getElementById("chips");
  host.innerHTML = "";
  const view = loadView();
  const items = [{ id: "all", label: "All channels" }];
  if (view.country || view.languages.length || view.tags.length) items.push({ id: "saved", label: "My view" });
  items.push({ id: "favorites", label: "Favorites" }, { id: "popular", label: "Most Popular" });
  const tags = new Set();
  const languages = new Set();
  const countries = new Set();
  catalog.forEach((channel) => {
    channel.tags.forEach((tag) => tags.add(tag.name));
    if (channel.language) languages.add(channel.language);
    if (channel.country) countries.add(channel.country);
  });
  [...tags].sort().forEach((name) => items.push({ id: `tag:${name}`, label: name }));
  [...languages].sort().forEach((code) => items.push({ id: `lang:${code}`, label: displayName("language", code) }));
  [...countries].sort().forEach((code) => items.push({ id: `country:${code}`, label: displayName("region", code) }));
  items.forEach((item) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "chip";
    button.setAttribute("role", "tab");
    button.setAttribute("aria-selected", item.id === chip ? "true" : "false");
    button.textContent = item.label;
    button.addEventListener("click", () => {
      chip = item.id;
      focus = { row: 0, col: 0 };
      loadGuide().catch((err) => toast(err.message));
    });
    host.appendChild(button);
  });
}

function renderInfo() {
  const channel = channels[focus.row];
  const program = channel ? currentProgram(channel) || layoutRow(channel, slotCount())[focus.col]?.program : null;
  document.getElementById("info-kicker").textContent = channel ? `${channel.guide_number}  ${channel.title}` : "";
  document.getElementById("info-title").textContent = program?.title || channel?.title || "No channels";
  document.getElementById("info-episode").textContent = episodeLine(program) || (channel ? channel.group_title || "Live" : "");
  const meta = document.getElementById("info-meta");
  meta.innerHTML = "";
  if (channel && channel.height >= 720) {
    const badge = document.createElement("span");
    badge.className = "badge";
    badge.textContent = "HD";
    meta.appendChild(badge);
  }
  if (program) {
    meta.appendChild(document.createTextNode(`${formatWhen(Date.parse(program.start_at))} – ${formatWhen(Date.parse(program.stop_at))}`));
  }
  document.getElementById("info-desc").textContent = program?.description || "";
}

function renderGuide() {
  const guide = document.getElementById("guide");
  const slots = slotCount();
  guide.style.setProperty("--slots", String(slots));
  guide.innerHTML = "";
  const day = document.createElement("div");
  day.className = "slot-head day-head";
  day.textContent = "Today";
  day.style.gridColumn = "1";
  day.style.gridRow = "1";
  guide.appendChild(day);
  for (let index = 0; index < slots; index += 1) {
    const head = document.createElement("div");
    head.className = "slot-head";
    head.textContent = formatWhen(windowStart + index * SLOT_MS);
    head.style.gridColumn = String(index + 2);
    head.style.gridRow = "1";
    guide.appendChild(head);
  }
  const favs = new Set(favorites());
  const previous = localStorage.getItem(PREV_KEY);
  channels.forEach((channel, row) => {
    const cell = document.createElement("div");
    cell.className = "channel-cell";
    cell.style.gridColumn = "1";
    cell.style.gridRow = String(row + 2);
    const tuneButton = document.createElement("button");
    tuneButton.type = "button";
    tuneButton.className = "channel-tune";
    tuneButton.appendChild(Object.assign(document.createElement("span"), { className: "channel-num", textContent: channel.guide_number }));
    tuneButton.appendChild(logoNode(channel));
    tuneButton.appendChild(Object.assign(document.createElement("span"), { className: "channel-name", textContent: channel.title }));
    tuneButton.addEventListener("click", () => tune(channel));
    cell.appendChild(tuneButton);
    const heart = document.createElement("button");
    heart.type = "button";
    heart.className = `icon-button${favs.has(channel.id) ? " on" : ""}`;
    heart.innerHTML = heartSvg(favs.has(channel.id));
    heart.setAttribute("aria-label", favs.has(channel.id) ? "Remove favorite" : "Favorite");
    heart.addEventListener("click", (event) => {
      event.stopPropagation();
      toggleFavorite(channel.id);
    });
    cell.appendChild(heart);
    const onAir = currentProgram(channel);
    const started = onAir && Date.parse(onAir.start_at) < Date.now();
    const isPrevious = previous && Number(previous) === channel.id && currentId !== channel.id;
    if (started || isPrevious) {
      const replay = document.createElement("button");
      replay.type = "button";
      replay.className = "icon-button replay";
      replay.innerHTML = replaySvg();
      replay.setAttribute("aria-label", isPrevious ? "Previous channel" : "Already started");
      replay.addEventListener("click", (event) => {
        event.stopPropagation();
        tune(channel);
      });
      cell.appendChild(replay);
    }
    guide.appendChild(cell);
    layoutRow(channel, slots).forEach((block, col) => {
      const program = document.createElement("button");
      program.type = "button";
      program.className = "program";
      if (row === focus.row && col === focus.col) program.classList.add("focused");
      if (block.left) program.classList.add("has-left");
      if (block.right) program.classList.add("has-right");
      program.style.gridColumn = `${block.col} / span ${block.span}`;
      program.style.gridRow = String(row + 2);
      program.dataset.row = String(row);
      program.dataset.col = String(col);
      program.innerHTML = `<span class="title"></span><span class="sub"></span>${block.left ? '<span class="edge left">&lsaquo;</span>' : ""}${block.right ? '<span class="edge right">&rsaquo;</span>' : ""}`;
      program.querySelector(".title").textContent = block.title;
      program.querySelector(".sub").textContent = block.sub;
      program.addEventListener("click", () => {
        focus = { row, col };
        renderInfo();
        tune(channel);
      });
      program.addEventListener("focus", () => {
        focus = { row, col };
        renderInfo();
      });
      guide.appendChild(program);
    });
  });
  const line = document.createElement("div");
  line.id = "now-line";
  guide.appendChild(line);
  placeNowLine();
}

function renderPhone() {
  const list = document.getElementById("phone-list");
  list.innerHTML = "";
  channels.forEach((channel) => {
    const row = document.createElement("button");
    row.type = "button";
    row.className = "phone-row";
    const now = currentProgram(channel);
    const next = nextProgram(channel);
    row.innerHTML = `<span class="channel-num"></span><span class="text"><span class="name"></span><span class="now"></span><span class="next"></span></span>`;
    row.querySelector(".channel-num").textContent = channel.guide_number;
    row.querySelector(".name").textContent = channel.title;
    row.querySelector(".now").textContent = `Now  ${now ? now.title : channel.title}`;
    row.querySelector(".next").textContent = `Next  ${next ? next.title : "No schedule"}`;
    row.prepend(logoNode(channel));
    row.addEventListener("click", () => tune(channel));
    list.appendChild(row);
  });
}

function placeDim() {
  const dim = document.getElementById("dim");
  const chrome = document.getElementById("stage-chrome");
  if (isPhone()) {
    dim.hidden = true;
    chrome.style.cssText = "";
    return;
  }
  const box = document.getElementById("preview").getBoundingClientRect();
  dim.style.cssText = `top:${box.top}px;left:${box.left}px;width:${box.width}px;height:${box.height}px;`;
  dim.hidden = false;
  chrome.style.cssText = `position:fixed;z-index:6;top:${box.top}px;left:${box.left}px;width:${box.width}px;height:${box.height}px;`;
}

function render() {
  syncLayout();
  renderChips();
  renderInfo();
  renderGuide();
  renderPhone();
  placeDim();
  const adult = document.getElementById("adult-toggle");
  adult.textContent = showAdult() ? "Hide adult content" : "Show adult content";
  const setAdult = document.getElementById("set-adult");
  if (setAdult) setAdult.checked = showAdult();
}

function placeNowLine() {
  const line = document.getElementById("now-line");
  const head = document.querySelector("#guide .slot-head:not(.day-head)");
  const guide = document.getElementById("guide");
  if (!line || !head || !guide) return;
  const slots = slotCount();
  const guideRect = guide.getBoundingClientRect();
  const headRect = head.getBoundingClientRect();
  const fraction = (Date.now() - windowStart) / (slots * SLOT_MS);
  if (fraction < 0 || fraction > 1) {
    line.hidden = true;
    return;
  }
  line.hidden = false;
  line.style.left = `${headRect.left - guideRect.left + headRect.width * slots * fraction}px`;
}

function move(deltaRow, deltaCol) {
  if (!channels.length) return;
  const slots = slotCount();
  let row = focus.row + deltaRow;
  let col = focus.col + deltaCol;
  if (row < 0) row = 0;
  if (row >= channels.length) row = channels.length - 1;
  const blocks = layoutRow(channels[row], slots);
  if (col < 0) {
    windowStart -= SLOT_MS;
    col = 0;
    loadGuide().catch((err) => toast(err.message));
    return;
  }
  if (col >= blocks.length) {
    windowStart += SLOT_MS;
    col = 0;
    loadGuide().catch((err) => toast(err.message));
    return;
  }
  focus = { row, col };
  renderGuide();
  renderInfo();
  document.querySelector(".program.focused")?.scrollIntoView({ block: "nearest", inline: "nearest" });
}

function toggleFavorite(id) {
  const current = new Set(favorites());
  if (current.has(id)) current.delete(id);
  else current.add(id);
  localStorage.setItem(FAV_KEY, JSON.stringify([...current]));
  render();
}

function rememberTune(channel) {
  if (currentId && currentId !== channel.id) localStorage.setItem(PREV_KEY, String(currentId));
  currentId = channel.id;
  const counts = tuneCounts();
  counts[channel.id] = (counts[channel.id] || 0) + 1;
  localStorage.setItem(COUNTS_KEY, JSON.stringify(counts));
}

async function tune(channel) {
  rememberTune(channel);
  if (isPhone()) document.documentElement.classList.add("video-full");
  document.getElementById("back-guide").hidden = !isPhone();
  const response = await fetch(`/api/channels/${channel.id}/watch${showAdult() ? "?include_adult=true" : ""}`, { method: "POST" });
  if (response.status === 503) {
    toast("All tuners are busy");
    return;
  }
  if (!response.ok) {
    let detail = "This channel did not start";
    try {
      const body = await response.json();
      if (body.detail) detail = body.detail;
    } catch (err) {
      if (!(err instanceof SyntaxError)) throw err;
    }
    toast(detail);
    return;
  }
  const info = await response.json();
  try {
    await playWhep(info.whep_url);
  } catch (err) {
    if (!(err instanceof Error)) throw err;
    try {
      await playHls(info.llhls_url);
    } catch (hlsErr) {
      if (!(hlsErr instanceof Error)) throw hlsErr;
      toast(`${err.message}. ${hlsErr.message}`);
    }
  }
  render();
}

function closePlayback() {
  if (peer) {
    peer.close();
    peer = null;
  }
  if (hlsPlayer) {
    hlsPlayer.destroy();
    hlsPlayer = null;
  }
  video.srcObject = null;
  video.removeAttribute("src");
  stage.classList.remove("has-video");
}

function waitForIce(connection) {
  if (connection.iceGatheringState === "complete") return Promise.resolve();
  return new Promise((resolve) => {
    const timer = window.setTimeout(resolve, 1500);
    connection.addEventListener("icegatheringstatechange", () => {
      if (connection.iceGatheringState === "complete") {
        window.clearTimeout(timer);
        resolve();
      }
    });
  });
}

async function playWhep(url) {
  closePlayback();
  const connection = new RTCPeerConnection();
  peer = connection;
  connection.addTransceiver("video", { direction: "recvonly" });
  connection.addTransceiver("audio", { direction: "recvonly" });
  connection.ontrack = (event) => {
    video.srcObject = event.streams[0];
    stage.classList.add("has-video");
    video.play().catch((err) => {
      if (err.name !== "NotAllowedError") toast("The picture was blocked by the browser");
    });
  };
  const offer = await connection.createOffer();
  await connection.setLocalDescription(offer);
  await waitForIce(connection);
  let response;
  try {
    response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/sdp" },
      body: connection.localDescription.sdp,
    });
  } catch (err) {
    throw new Error(`WHEP handshake failed: ${err.message}`);
  }
  if (!response.ok) throw new Error(`WHEP handshake failed: HTTP ${response.status}`);
  try {
    await connection.setRemoteDescription({ type: "answer", sdp: await response.text() });
  } catch (err) {
    throw new Error(`WHEP handshake failed: ${err.message}`);
  }
  await waitForConnected(connection);
}

function waitForConnected(connection) {
  if (connection.connectionState === "connected") return Promise.resolve();
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(() => reject(new Error("WebRTC ICE failed: timed out")), 8000);
    const check = () => {
      if (connection.connectionState === "connected") {
        window.clearTimeout(timer);
        resolve();
      } else if (connection.connectionState === "failed" || connection.iceConnectionState === "failed") {
        window.clearTimeout(timer);
        reject(new Error("WebRTC ICE failed"));
      }
    };
    connection.addEventListener("connectionstatechange", check);
    connection.addEventListener("iceconnectionstatechange", check);
  });
}

async function playHls(url) {
  closePlayback();
  if (window.Hls && window.Hls.isSupported()) {
    const player = new window.Hls({ lowLatencyMode: true, backBufferLength: 0 });
    hlsPlayer = player;
    player.loadSource(url);
    player.attachMedia(video);
    await new Promise((resolve, reject) => {
      player.on(window.Hls.Events.MANIFEST_PARSED, resolve);
      player.on(window.Hls.Events.ERROR, (_event, data) => {
        if (data.fatal) reject(new Error(`HLS fallback failed: ${data.details || data.type || "error"}`));
      });
    });
    stage.classList.add("has-video");
    await video.play();
    return;
  }
  if (video.canPlayType("application/vnd.apple.mpegurl")) {
    video.src = url;
    stage.classList.add("has-video");
    await video.play();
    return;
  }
  throw new Error("HLS fallback failed: this browser cannot play LL-HLS");
}

function tuneRelative(step) {
  if (!channels.length) return;
  let index = channels.findIndex((channel) => channel.id === currentId);
  if (index < 0) index = focus.row;
  index = (index + step + channels.length) % channels.length;
  focus.row = index;
  tune(channels[index]);
}

function queueNumber(key) {
  numberBuffer += key;
  const node = document.getElementById("number-entry");
  node.hidden = false;
  node.textContent = numberBuffer;
  window.clearTimeout(numberTimer);
  numberTimer = window.setTimeout(() => {
    const match = channels.find((channel) => channel.guide_number === numberBuffer);
    numberBuffer = "";
    node.hidden = true;
    if (match) tune(match);
    else toast("No channel with that number");
  }, 1200);
}

function openSettings() {
  const view = loadView();
  const country = document.getElementById("set-country");
  const languages = document.getElementById("set-languages");
  const tags = document.getElementById("set-tags");
  const start = document.getElementById("set-start");
  country.innerHTML = `<option value="">Any</option>`;
  languages.innerHTML = "";
  tags.innerHTML = "";
  start.innerHTML = `<option value="">Stay on the guide</option>`;
  const countries = new Set();
  const langs = new Set();
  const tagNames = new Set();
  catalog.forEach((channel) => {
    if (channel.country) countries.add(channel.country);
    if (channel.language) langs.add(channel.language);
    channel.tags.forEach((tag) => tagNames.add(tag.name));
  });
  [...countries].sort().forEach((code) => {
    const option = document.createElement("option");
    option.value = code;
    option.textContent = code;
    option.selected = code === view.country;
    country.appendChild(option);
  });
  [...langs].sort().forEach((code) => {
    const label = document.createElement("label");
    label.className = "check";
    label.innerHTML = `<input type="checkbox" value="${code}"> ${code.toUpperCase()}`;
    label.querySelector("input").checked = view.languages.includes(code);
    languages.appendChild(label);
  });
  [...tagNames].sort().forEach((name) => {
    const label = document.createElement("label");
    label.className = "check";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = name;
    input.checked = view.tags.includes(name);
    label.appendChild(input);
    label.appendChild(document.createTextNode(` ${name}`));
    tags.appendChild(label);
  });
  catalog.forEach((channel) => {
    const option = document.createElement("option");
    option.value = String(channel.id);
    option.textContent = `${channel.guide_number}  ${channel.title}`;
    option.selected = channel.id === view.startChannelId;
    start.appendChild(option);
  });
  document.getElementById("set-sort").value = view.sort;
  document.getElementById("set-adult").checked = showAdult();
  document.getElementById("settings").hidden = false;
}

function saveSettings(event) {
  event.preventDefault();
  const languages = [...document.querySelectorAll("#set-languages input:checked")].map((input) => input.value);
  const tags = [...document.querySelectorAll("#set-tags input:checked")].map((input) => input.value);
  const startValue = document.getElementById("set-start").value;
  const view = {
    country: document.getElementById("set-country").value,
    languages,
    tags,
    sort: document.getElementById("set-sort").value,
    order: "asc",
    startChannelId: startValue ? Number(startValue) : null,
  };
  localStorage.setItem(VIEW_KEY, JSON.stringify(view));
  sessionStorage.setItem(ADULT_KEY, document.getElementById("set-adult").checked ? "1" : "0");
  document.getElementById("settings").hidden = true;
  chip = view.country || view.languages.length || view.tags.length ? "saved" : "all";
  catalog = [];
  loadGuide().catch((err) => toast(err.message));
}

function setAdult(on) {
  sessionStorage.setItem(ADULT_KEY, on ? "1" : "0");
  catalog = [];
  loadGuide().catch((err) => toast(err.message));
}

document.getElementById("adult-toggle").addEventListener("click", () => setAdult(!showAdult()));
document.getElementById("open-settings").addEventListener("click", openSettings);
document.getElementById("open-import").addEventListener("click", openImport);
document.getElementById("close-import").addEventListener("click", closeImport);
document.getElementById("choose-file").addEventListener("click", () => {
  document.getElementById("upload-file").click();
});
document.getElementById("upload-file").addEventListener("change", () => {
  const file = document.getElementById("upload-file").files[0];
  document.getElementById("upload-filename").textContent = file ? file.name : "";
  const name = document.getElementById("upload-name");
  if (file && !name.value.trim()) name.value = file.name.replace(/\.(m3u8|m3u)$/i, "");
});
document.getElementById("upload-form").addEventListener("submit", (event) => {
  event.preventDefault();
  uploadPlaylist().catch((err) => showImportError(err.message));
});
document.getElementById("url-form").addEventListener("submit", (event) => {
  event.preventDefault();
  addPlaylistUrl().catch((err) => showImportError(err.message));
});
document.getElementById("hdhr-form").addEventListener("submit", (event) => {
  event.preventDefault();
  addTuner().catch((err) => showImportError(err.message));
});
document.getElementById("close-settings").addEventListener("click", () => {
  document.getElementById("settings").hidden = true;
});
document.getElementById("settings-form").addEventListener("submit", saveSettings);
document.getElementById("ch-up").addEventListener("click", () => tuneRelative(1));
document.getElementById("ch-down").addEventListener("click", () => tuneRelative(-1));
document.getElementById("view-grid").addEventListener("click", () => {
  document.documentElement.classList.add("show-grid");
});
document.getElementById("view-list").addEventListener("click", () => {
  document.documentElement.classList.remove("show-grid");
});
document.getElementById("back-guide").addEventListener("click", () => {
  document.documentElement.classList.remove("video-full");
  document.getElementById("back-guide").hidden = true;
});
document.getElementById("sound").addEventListener("click", () => {
  video.muted = !video.muted;
  document.getElementById("sound").textContent = video.muted ? "Sound" : "Mute";
});

document.addEventListener("keydown", (event) => {
  const settingsOpen = !document.getElementById("settings").hidden;
  const importOpen = !document.getElementById("import").hidden;
  if (event.key === "Escape") {
    document.getElementById("settings").hidden = true;
    closeImport();
    document.documentElement.classList.remove("video-full");
    document.getElementById("back-guide").hidden = true;
    return;
  }
  if (event.target.closest("input, textarea, select")) return;
  if (settingsOpen || importOpen) return;
  if (event.key === "ArrowDown") {
    event.preventDefault();
    if (document.documentElement.classList.contains("video-full")) tuneRelative(1);
    else move(1, 0);
  } else if (event.key === "ArrowUp") {
    event.preventDefault();
    if (document.documentElement.classList.contains("video-full")) tuneRelative(-1);
    else move(-1, 0);
  } else if (event.key === "ArrowRight") {
    event.preventDefault();
    move(0, 1);
  } else if (event.key === "ArrowLeft") {
    event.preventDefault();
    move(0, -1);
  } else if (event.key === "Enter") {
    const channel = channels[focus.row];
    if (channel) tune(channel);
  } else if (event.key === "PageUp") {
    event.preventDefault();
    tuneRelative(-1);
  } else if (event.key === "PageDown") {
    event.preventDefault();
    tuneRelative(1);
  } else if (event.key === "f" || event.key === "F") {
    const channel = channels[focus.row];
    if (channel) toggleFavorite(channel.id);
  } else if (event.key === "s" || event.key === "S") {
    openSettings();
  } else if (event.key === "i" || event.key === "I") {
    openImport();
  } else if (event.key === "a" || event.key === "A") {
    setAdult(!showAdult());
  } else if (event.key === "g" || event.key === "G") {
    document.documentElement.classList.toggle("show-grid");
  } else if (/^[0-9.]$/.test(event.key)) {
    queueNumber(event.key);
  }
});

window.addEventListener("resize", () => {
  syncLayout();
  render();
});

function showImportError(message) {
  document.getElementById("import-error").textContent = message || "";
}

function closeImport() {
  document.getElementById("import").hidden = true;
  window.clearInterval(closeImport.timer);
}

function openImport() {
  document.getElementById("import").hidden = false;
  showImportError("");
  document.getElementById("choose-file").focus();
  refreshSources().catch((err) => showImportError(err.message));
  window.clearInterval(closeImport.timer);
  closeImport.timer = window.setInterval(() => {
    if (document.getElementById("import").hidden) return;
    refreshSources().catch((err) => showImportError(err.message));
  }, 2000);
}

async function errorDetail(response) {
  try {
    const body = await response.json();
    if (typeof body.detail === "string") return body.detail;
  } catch (err) {
    if (!(err instanceof SyntaxError)) throw err;
  }
  return `request failed (${response.status})`;
}

async function uploadPlaylist() {
  const file = document.getElementById("upload-file").files[0];
  if (!file) {
    showImportError("Choose an M3U file");
    return;
  }
  const named = document.getElementById("upload-name").value.trim();
  const body = new FormData();
  body.set("name", named || file.name.replace(/\.(m3u8|m3u)$/i, ""));
  body.set("file", file);
  const response = await fetch("/api/playlists/upload", { method: "POST", body });
  if (!response.ok) {
    showImportError(await errorDetail(response));
    return;
  }
  showImportError("");
  document.getElementById("upload-form").reset();
  document.getElementById("upload-filename").textContent = "";
  await refreshSources();
  loadGuide().catch((err) => toast(err.message));
}

async function addPlaylistUrl() {
  const url = document.getElementById("playlist-url").value.trim();
  const name = document.getElementById("url-name").value.trim();
  if (!url || !name) {
    showImportError("Playlist URL and name are both required");
    return;
  }
  const payload = { name, url };
  const minutes = document.getElementById("url-interval").value.trim();
  if (minutes) payload.interval_minutes = Number(minutes);
  const response = await fetch("/api/playlists", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    showImportError(await errorDetail(response));
    return;
  }
  showImportError("");
  document.getElementById("url-form").reset();
  await refreshSources();
  loadGuide().catch((err) => toast(err.message));
}

async function addTuner() {
  const host = document.getElementById("hdhr-host").value.trim();
  if (!host) {
    showImportError("HDHomeRun address is required");
    return;
  }
  const response = await fetch("/api/hdhomerun/devices", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ host }),
  });
  if (!response.ok) {
    showImportError(await errorDetail(response));
    return;
  }
  showImportError("");
  document.getElementById("hdhr-host").value = "";
  await refreshSources();
  loadGuide().catch((err) => toast(err.message));
}

function progressText(playlist) {
  if (playlist.status === "checking") {
    const done = playlist.channel_count - playlist.pending;
    return `Checking ${done} of ${playlist.channel_count}`;
  }
  if (playlist.status === "empty") return "No channels";
  if (playlist.status === "online") return "Online";
  if (playlist.status === "offline") return "Offline";
  return `${playlist.online} online, ${playlist.offline} offline`;
}

function sourceRow(title, meta, actions) {
  const row = document.createElement("div");
  row.className = "source-row";
  const text = document.createElement("div");
  text.className = "text";
  const name = document.createElement("span");
  name.className = "name";
  name.textContent = title;
  const detail = document.createElement("span");
  detail.className = "meta";
  detail.textContent = meta;
  text.append(name, detail);
  row.appendChild(text);
  actions.forEach((action) => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = action.label;
    button.addEventListener("click", () => {
      action.run().catch((err) => showImportError(err.message));
    });
    row.appendChild(button);
  });
  return row;
}

async function refreshSources() {
  const [playlistsResponse, devicesResponse] = await Promise.all([
    fetch("/api/playlists"),
    fetch("/api/hdhomerun/devices"),
  ]);
  if (!playlistsResponse.ok) throw new Error(await errorDetail(playlistsResponse));
  if (!devicesResponse.ok) throw new Error(await errorDetail(devicesResponse));
  const playlists = (await playlistsResponse.json()).playlists;
  const devices = (await devicesResponse.json()).devices;
  const list = document.getElementById("playlist-list");
  list.innerHTML = "";
  playlists.forEach((playlist) => {
    const when = playlist.last_imported_at ? playlist.last_imported_at.replace("T", " ").replace("Z", " UTC") : "Not imported";
    list.appendChild(sourceRow(
      playlist.name,
      `${playlist.channel_count} channels · ${when} · ${progressText(playlist)}`,
      [
        { label: "Re-import", run: () => reimportPlaylist(playlist.id) },
        { label: "Delete", run: () => deletePlaylist(playlist.id) },
      ],
    ));
  });
  const deviceList = document.getElementById("device-list");
  deviceList.innerHTML = "";
  devices.forEach((device) => {
    deviceList.appendChild(sourceRow(
      device.friendly_name || device.host,
      `${device.host} · ${device.tuner_count} tuners`,
      [
        { label: "Refresh", run: () => refreshDevice(device.id) },
        { label: "Delete", run: () => deleteDevice(device.id) },
      ],
    ));
  });
}

async function reimportPlaylist(id) {
  const response = await fetch(`/api/playlists/${id}/import`, { method: "POST" });
  if (!response.ok) {
    showImportError(await errorDetail(response));
    return;
  }
  showImportError("");
  await refreshSources();
  loadGuide().catch((err) => toast(err.message));
}

async function deletePlaylist(id) {
  const response = await fetch(`/api/playlists/${id}`, { method: "DELETE" });
  if (!response.ok && response.status !== 204) {
    showImportError(await errorDetail(response));
    return;
  }
  showImportError("");
  await refreshSources();
  loadGuide().catch((err) => toast(err.message));
}

async function refreshDevice(id) {
  const response = await fetch(`/api/hdhomerun/devices/${id}/import`, { method: "POST" });
  if (!response.ok) {
    showImportError(await errorDetail(response));
    return;
  }
  showImportError("");
  await refreshSources();
  loadGuide().catch((err) => toast(err.message));
}

async function deleteDevice(id) {
  const response = await fetch(`/api/hdhomerun/devices/${id}`, { method: "DELETE" });
  if (!response.ok && response.status !== 204) {
    showImportError(await errorDetail(response));
    return;
  }
  showImportError("");
  await refreshSources();
  loadGuide().catch((err) => toast(err.message));
}

const saved = loadView();
chip = saved.country || saved.languages.length || saved.tags.length ? "saved" : "all";
clockTick();
window.setInterval(clockTick, 1000);
loadGuide().catch((err) => toast(err.message));

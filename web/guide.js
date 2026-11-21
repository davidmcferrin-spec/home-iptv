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
let selectedIds = new Set();
let editorTags = [];
let knownTags = [];

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
  if (chip.startsWith("status:")) {
    return { country: "", languages: [], tags: [], status: chip.slice(7), sort: view.sort, order: view.order };
  }
  return { country: "", languages: [], tags: [], sort: view.sort, order: view.order };
}

async function loadGuide() {
  const filters = activeFilters();
  const params = { sort: filters.sort, order: filters.order };
  if (filters.country) params.country = filters.country;
  if (filters.status) params.status = filters.status;
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
  const img = document.createElement("img");
  img.className = "logo";
  img.alt = "";
  img.src = `/api/logo/${channel.id}`;
  return img;
}

function renderChips() {
  const host = document.getElementById("chips");
  host.innerHTML = "";
  const view = loadView();
  const items = [{ id: "all", label: "All channels" }];
  if (view.country || view.languages.length || view.tags.length) items.push({ id: "saved", label: "My view" });
  items.push(
    { id: "favorites", label: "Favorites" },
    { id: "popular", label: "Most Popular" },
    { id: "status:online", label: "Online" },
    { id: "status:offline", label: "Offline" },
    { id: "status:unknown", label: "Unchecked" },
  );
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
  if (channel) {
    const facts = document.createElement("span");
    facts.className = `facts ${channel.status || "unknown"}`;
    facts.textContent = channelFacts(channel);
    meta.appendChild(facts);
  }
  if (program) {
    meta.appendChild(document.createTextNode(`${formatWhen(Date.parse(program.start_at))} – ${formatWhen(Date.parse(program.stop_at))}`));
  }
  document.getElementById("info-desc").textContent = program?.description || "";
}

function statusLabel(channel) {
  if (channel.status === "online") return "Online";
  if (channel.status === "offline") return "Offline";
  return "Unchecked";
}

function channelFacts(channel) {
  const bits = [statusLabel(channel)];
  if (channel.width && channel.height) bits.push(`${channel.width}x${channel.height}`);
  if (channel.video_codec) bits.push(channel.video_codec);
  if (channel.audio_codec) bits.push(channel.audio_codec);
  bits.push(channel.last_checked_at ? `checked ${channel.last_checked_at.replace("T", " ").replace("Z", " UTC")}` : "not checked");
  if (channel.guide_source) bits.push(channel.guide_source);
  if (channel.programme_source) bits.push(channel.programme_source);
  return bits.join(" · ");
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
    tuneButton.appendChild(Object.assign(document.createElement("span"), { className: `channel-status ${channel.status || "unknown"}`, textContent: statusLabel(channel) }));
    tuneButton.addEventListener("click", () => tune(channel));
    cell.appendChild(pickBox(channel));
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
    row.querySelector(".now").textContent = `${statusLabel(channel)}  ${now ? now.title : channel.title}`;
    row.querySelector(".next").textContent = `Next  ${next ? next.title : "No schedule"}`;
    row.prepend(logoNode(channel));
    row.addEventListener("click", () => tune(channel));
    const wrap = document.createElement("div");
    wrap.className = "phone-wrap";
    wrap.appendChild(pickBox(channel));
    wrap.appendChild(row);
    list.appendChild(wrap);
  });
}

function pickBox(channel) {
  const pick = document.createElement("input");
  pick.type = "checkbox";
  pick.className = "pick";
  pick.checked = selectedIds.has(channel.id);
  pick.setAttribute("aria-label", `Select ${channel.title}`);
  pick.addEventListener("click", (event) => event.stopPropagation());
  pick.addEventListener("change", () => {
    if (pick.checked) selectedIds.add(channel.id);
    else selectedIds.delete(channel.id);
    const button = document.getElementById("open-editor");
    button.textContent = selectedIds.size > 1 ? `Edit ${selectedIds.size}` : "Edit";
  });
  return pick;
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
  const setAdultBox = document.getElementById("set-adult");
  if (setAdultBox) setAdultBox.checked = showAdult();
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

function showStarting(on) {
  document.getElementById("starting").hidden = !on;
}

function sleep(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

let stallTimer = 0;

function stopStallWatch() {
  window.clearInterval(stallTimer);
  stallTimer = 0;
}

function armStallWatch(onStall) {
  stopStallWatch();
  let last = -1;
  let stillSince = 0;
  stallTimer = window.setInterval(() => {
    const now = video.currentTime;
    if (now <= 0.05) return;
    if (last < 0 || Math.abs(now - last) > 0.05) {
      last = now;
      stillSince = performance.now();
      return;
    }
    if (performance.now() - stillSince >= 4000) {
      stopStallWatch();
      onStall();
    }
  }, 500);
}

async function tune(channel) {
  rememberTune(channel);
  if (isPhone()) document.documentElement.classList.add("video-full");
  document.getElementById("back-guide").hidden = !isPhone();
  showStarting(true);
  stopStallWatch();
  let response;
  try {
    response = await fetch(`/api/channels/${channel.id}/watch${showAdult() ? "?include_adult=true" : ""}`, { method: "POST" });
  } catch (err) {
    showStarting(false);
    toast(`Starting stream failed: ${err.message}`);
    return;
  }
  if (response.status === 503) {
    showStarting(false);
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
    showStarting(false);
    toast(detail);
    return;
  }
  const info = await response.json();
  const useHls = info.playback === "hls";
  let whepError = null;
  try {
    if (useHls) await playHls(info.llhls_url);
    else await playWhep(info.whep_url);
  } catch (err) {
    if (!(err instanceof Error)) throw err;
    whepError = err;
    if (useHls) {
      showStarting(false);
      toast(err.message);
      render();
      return;
    }
    try {
      await playHls(info.llhls_url);
    } catch (hlsErr) {
      if (!(hlsErr instanceof Error)) throw hlsErr;
      showStarting(false);
      toast(`${err.message}. ${hlsErr.message}`);
      render();
      return;
    }
  }
  showStarting(false);
  armStallWatch(async () => {
    if (hlsPlayer || video.canPlayType("application/vnd.apple.mpegurl") && video.src) {
      toast("HLS playback stalled");
      return;
    }
    try {
      await playHls(info.llhls_url);
      showStarting(false);
      armStallWatch(() => toast("HLS playback stalled"));
    } catch (hlsErr) {
      toast(hlsErr instanceof Error ? hlsErr.message : "HLS playback stalled");
    }
  });
  if (whepError) toast(`WebRTC stalled, using HLS. ${whepError.message}`);
  render();
}

function closePlayback() {
  stopStallWatch();
  showStarting(false);
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
      if (err.name === "NotAllowedError") toast("The picture was blocked by the browser");
    });
  };
  const offer = await connection.createOffer();
  await connection.setLocalDescription(offer);
  await waitForIce(connection);
  const response = await postWhep(url, connection.localDescription.sdp);
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

async function postWhep(url, sdp) {
  const deadline = performance.now() + 15000;
  let wait = 400;
  while (true) {
    let response;
    try {
      response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/sdp" },
        body: sdp,
      });
    } catch (err) {
      throw new Error(`WHEP handshake failed: ${err.message}`);
    }
    if (response.ok) return response;
    let body = "";
    try {
      body = await response.text();
    } catch (err) {
      body = "";
    }
    const missing = response.status === 404 && body.toLowerCase().includes("no stream");
    if (missing && performance.now() < deadline) {
      await sleep(wait);
      wait = Math.min(wait + 300, 1500);
      continue;
    }
    throw new Error(`WHEP handshake failed: HTTP ${response.status}`);
  }
}

async function playHls(url) {
  closePlayback();
  if (window.Hls && window.Hls.isSupported()) {
    const player = new window.Hls({ lowLatencyMode: true, backBufferLength: 0 });
    hlsPlayer = player;
    player.attachMedia(video);
    const deadline = performance.now() + 15000;
    let wait = 400;
    while (true) {
      player.loadSource(url);
      try {
        await new Promise((resolve, reject) => {
          const onParsed = () => {
            player.off(window.Hls.Events.MANIFEST_PARSED, onParsed);
            player.off(window.Hls.Events.ERROR, onError);
            resolve();
          };
          const onError = (_event, data) => {
            if (!data.fatal) return;
            player.off(window.Hls.Events.MANIFEST_PARSED, onParsed);
            player.off(window.Hls.Events.ERROR, onError);
            const status = data.response && data.response.code;
            reject(Object.assign(new Error(`HLS fallback failed: ${data.details || data.type || "error"}`), { status }));
          };
          player.on(window.Hls.Events.MANIFEST_PARSED, onParsed);
          player.on(window.Hls.Events.ERROR, onError);
        });
        break;
      } catch (err) {
        if (err.status === 404 && performance.now() < deadline) {
          await sleep(wait);
          wait = Math.min(wait + 300, 1500);
          continue;
        }
        throw err;
      }
    }
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
  loadServerSettings().catch((err) => toast(err.message));
}

async function loadServerSettings() {
  const response = await fetch("/api/settings");
  if (!response.ok) throw new Error(`settings returned ${response.status}`);
  const body = await response.json();
  document.getElementById("set-http-base").value = body.public_http_base || "";
  document.getElementById("set-http-base").placeholder = body.effective_http_base || "Detected from the browser";
  document.getElementById("set-webrtc-base").value = body.public_webrtc_base || "";
  document.getElementById("set-webrtc-base").placeholder = body.effective_webrtc_base || "Detected from the browser";
  document.getElementById("set-hls-base").value = body.public_hls_base || "";
  document.getElementById("set-hls-base").placeholder = body.effective_hls_base || "Detected from the browser";
  document.getElementById("set-webrtc-host").value = body.webrtc_additional_host || "";
  document.getElementById("set-webrtc-host").placeholder = body.effective_webrtc_host || "Detected LAN address";
  document.getElementById("set-verify-concurrency").value = body.verify_concurrency;
  document.getElementById("set-verify-timeout").value = body.verify_timeout_seconds;
  document.getElementById("set-verify-interval").value = body.verify_interval_minutes;
  document.getElementById("set-idle").value = body.idle_timeout_seconds;
  document.getElementById("set-emulation").checked = body.hdhr_emulation;
  document.getElementById("port-http").value = body.ports.http;
  document.getElementById("port-webrtc").value = body.ports.webrtc;
  document.getElementById("port-webrtc-udp").value = body.ports.webrtc_udp;
  document.getElementById("port-hls").value = body.ports.hls;
  document.getElementById("port-note").textContent = body.ports_note;
  document.getElementById("set-playlist-group").value = body.playlist_group || "tag";
  const share = await fetch("/api/share");
  if (!share.ok) throw new Error(`share returned ${share.status}`);
  const links = await share.json();
  document.getElementById("vlc-playlist").value = links.playlist_url;
  document.getElementById("vlc-guide").value = links.guide_url;
  document.getElementById("vlc-qr").innerHTML = links.playlist_qr;
  await loadFeeds();
}

async function loadFeeds() {
  const response = await fetch("/api/epg/feeds");
  if (!response.ok) throw new Error(`guide feeds returned ${response.status}`);
  const body = await response.json();
  const list = document.getElementById("feed-list");
  list.innerHTML = "";
  body.feeds.forEach((feed) => {
    const row = document.createElement("label");
    row.className = "check feed-row";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.checked = feed.enabled;
    input.addEventListener("change", () => {
      fetch(`/api/epg/feeds/${feed.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: input.checked }),
      }).then((result) => {
        if (!result.ok) toast(`guide feed returned ${result.status}`);
      });
    });
    row.appendChild(input);
    const text = document.createElement("span");
    text.className = "feed-text";
    const country = feed.country || "Any country";
    const title = document.createElement("span");
    title.textContent = `${country}  ${feed.url}`;
    const status = document.createElement("span");
    status.className = "meta";
    status.textContent = feed.last_status ? `${feed.last_status}, ${feed.last_matched || 0} matched` : "not fetched";
    text.append(title, status);
    row.appendChild(text);
    list.appendChild(row);
  });
}

async function addFeed() {
  const input = document.getElementById("feed-url");
  const url = input.value.trim();
  if (!url) return;
  const response = await fetch("/api/epg/feeds", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  });
  if (!response.ok) {
    let detail = `guide feed returned ${response.status}`;
    try {
      const body = await response.json();
      if (typeof body.detail === "string") detail = body.detail;
    } catch (err) {
      if (!(err instanceof SyntaxError)) throw err;
    }
    toast(detail);
    return;
  }
  input.value = "";
  await loadFeeds();
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
  saveServerSettings()
    .then(() => {
      document.getElementById("settings").hidden = true;
      chip = view.country || view.languages.length || view.tags.length ? "saved" : "all";
      catalog = [];
      loadGuide().catch((err) => toast(err.message));
    })
    .catch((err) => toast(err.message));
}

async function saveServerSettings() {
  const payload = {
    public_http_base: document.getElementById("set-http-base").value.trim(),
    public_webrtc_base: document.getElementById("set-webrtc-base").value.trim(),
    public_hls_base: document.getElementById("set-hls-base").value.trim(),
    webrtc_additional_host: document.getElementById("set-webrtc-host").value.trim(),
    verify_concurrency: Number(document.getElementById("set-verify-concurrency").value),
    verify_timeout_seconds: Number(document.getElementById("set-verify-timeout").value),
    verify_interval_minutes: Number(document.getElementById("set-verify-interval").value),
    idle_timeout_seconds: Number(document.getElementById("set-idle").value),
    hdhr_emulation: document.getElementById("set-emulation").checked,
    playlist_group: document.getElementById("set-playlist-group").value,
  };
  const response = await fetch("/api/settings", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    let detail = `settings returned ${response.status}`;
    try {
      const body = await response.json();
      if (typeof body.detail === "string") detail = body.detail;
    } catch (err) {
      if (!(err instanceof SyntaxError)) throw err;
    }
    throw new Error(detail);
  }
}

function setAdult(on) {
  sessionStorage.setItem(ADULT_KEY, on ? "1" : "0");
  catalog = [];
  loadGuide().catch((err) => toast(err.message));
}

document.getElementById("open-settings").addEventListener("click", openSettings);
document.getElementById("feed-add").addEventListener("click", () => {
  addFeed().catch((err) => toast(err.message));
});
document.getElementById("feed-url").addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    addFeed().catch((err) => toast(err.message));
  }
});
document.getElementById("open-editor").addEventListener("click", openEditor);
document.getElementById("close-editor").addEventListener("click", () => {
  document.getElementById("editor").hidden = true;
});
document.getElementById("edit-add-tag").addEventListener("click", addEditorTag);
document.getElementById("editor-form").addEventListener("submit", saveEditor);
document.getElementById("copy-playlist").addEventListener("click", () => copyField("vlc-playlist"));
document.getElementById("copy-guide").addEventListener("click", () => copyField("vlc-guide"));
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

function toggleFullscreen() {
  const stageNode = document.getElementById("stage");
  const videoNode = document.getElementById("video");
  if (document.fullscreenElement) {
    document.exitFullscreen();
    return;
  }
  if (stageNode.requestFullscreen) {
    stageNode.requestFullscreen().catch(() => {
      if (videoNode.webkitEnterFullscreen) videoNode.webkitEnterFullscreen();
    });
    return;
  }
  if (videoNode.webkitEnterFullscreen) videoNode.webkitEnterFullscreen();
}

function toggleWindow() {
  document.documentElement.classList.toggle("video-full");
  document.getElementById("back-guide").hidden = !document.documentElement.classList.contains("video-full");
}

document.getElementById("fullscreen").addEventListener("click", toggleFullscreen);
document.getElementById("expand").addEventListener("click", toggleWindow);
document.getElementById("video").addEventListener("dblclick", toggleFullscreen);

function copyField(id) {
  const input = document.getElementById(id);
  input.focus();
  input.select();
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(input.value).then(() => toast("Copied")).catch(() => {});
  }
}

function fillDataList(id, values) {
  const list = document.getElementById(id);
  list.innerHTML = "";
  values.forEach((value) => {
    const option = document.createElement("option");
    option.value = value;
    list.appendChild(option);
  });
}

function drawEditorTags() {
  const box = document.getElementById("edit-tag-list");
  box.innerHTML = "";
  editorTags.forEach((tag) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "tag-chip";
    button.textContent = `${tag.name}  Remove`;
    button.addEventListener("click", () => {
      editorTags = editorTags.filter((item) => item.id !== tag.id);
      drawEditorTags();
    });
    box.appendChild(button);
  });
}

async function openEditor() {
  const ids = selectedIds.size ? [...selectedIds] : (channels[focus.row] ? [channels[focus.row].id] : []);
  if (!ids.length) {
    toast("No channel to edit");
    return;
  }
  const many = ids.length > 1;
  document.getElementById("editor-title").textContent = many ? `Edit ${ids.length} channels` : "Edit channel";
  document.getElementById("editor-single").hidden = many;
  document.getElementById("edit-adult-one").hidden = many;
  document.getElementById("edit-adult-many").hidden = !many;
  document.getElementById("editor-error").textContent = "";
  document.getElementById("edit-language").value = "";
  document.getElementById("edit-language").placeholder = many ? "Leave unchanged" : "";
  document.getElementById("edit-country").value = "";
  document.getElementById("edit-country").placeholder = many ? "Leave unchanged" : "";
  document.getElementById("edit-adult-bulk").value = "";
  const tagResponse = await fetch("/api/tags?include_adult=true");
  knownTags = tagResponse.ok ? (await tagResponse.json()).tags : [];
  fillDataList("tag-list", knownTags.map((tag) => tag.name));
  fillDataList("language-list", [...new Set(catalog.map((item) => item.language).filter(Boolean))]);
  fillDataList("country-list", [...new Set(catalog.map((item) => item.country).filter(Boolean))]);
  const one = catalog.find((item) => item.id === ids[0]) || channels.find((item) => item.id === ids[0]);
  if (!many && one) {
    document.getElementById("edit-title").value = one.title || "";
    document.getElementById("edit-logo").value = one.logo_url || "";
    document.getElementById("edit-guide").value = one.tvg_id || "";
    document.getElementById("edit-language").value = one.language || "";
    document.getElementById("edit-country").value = one.country || "";
    document.getElementById("edit-adult").checked = Boolean(one.adult);
    document.getElementById("edit-playback").value = one.playback_mode || "auto";
    editorTags = (one.tags || []).map((tag) => ({ id: tag.id, name: tag.name }));
  } else {
    editorTags = [];
  }
  document.getElementById("editor").dataset.ids = ids.join(",");
  drawEditorTags();
  document.getElementById("editor").hidden = false;
  document.getElementById(many ? "edit-language" : "edit-title").focus();
}

async function addEditorTag() {
  const input = document.getElementById("edit-tag");
  const name = input.value.trim();
  if (!name) return;
  let tag = knownTags.find((item) => item.name.toLowerCase() === name.toLowerCase());
  if (!tag) {
    const response = await fetch("/api/tags", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    if (response.status === 409) {
      const listed = await fetch("/api/tags?include_adult=true");
      knownTags = (await listed.json()).tags;
      tag = knownTags.find((item) => item.name.toLowerCase() === name.toLowerCase());
    } else if (!response.ok) {
      document.getElementById("editor-error").textContent = await errorText(response);
      return;
    } else {
      tag = await response.json();
      knownTags.push(tag);
    }
  }
  if (tag && !editorTags.some((item) => item.id === tag.id)) editorTags.push({ id: tag.id, name: tag.name });
  input.value = "";
  drawEditorTags();
}

async function errorText(response) {
  try {
    const body = await response.json();
    if (typeof body.detail === "string") return body.detail;
  } catch (err) {
    if (!(err instanceof SyntaxError)) throw err;
  }
  return `request returned ${response.status}`;
}

async function saveEditor(event) {
  event.preventDefault();
  const ids = document.getElementById("editor").dataset.ids.split(",").filter(Boolean).map(Number);
  const many = ids.length > 1;
  const error = document.getElementById("editor-error");
  error.textContent = "";
  try {
    if (many) {
      const body = { ids, add_tag_ids: editorTags.map((tag) => tag.id) };
      const language = document.getElementById("edit-language").value.trim();
      const country = document.getElementById("edit-country").value.trim();
      const adult = document.getElementById("edit-adult-bulk").value;
      if (language) body.language = language;
      if (country) body.country = country;
      if (adult !== "") body.adult = adult === "1";
      const response = await fetch("/api/channels/bulk", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!response.ok) throw new Error(await errorText(response));
    } else {
      const channel = catalog.find((item) => item.id === ids[0]) || channels.find((item) => item.id === ids[0]);
      const current = new Set((channel && channel.tags ? channel.tags : []).map((tag) => tag.id));
      const next = new Set(editorTags.map((tag) => tag.id));
      const response = await fetch(`/api/channels/${ids[0]}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: document.getElementById("edit-title").value.trim(),
          logo_url: document.getElementById("edit-logo").value.trim(),
          tvg_id: document.getElementById("edit-guide").value.trim(),
          language: document.getElementById("edit-language").value.trim(),
          country: document.getElementById("edit-country").value.trim(),
          adult: document.getElementById("edit-adult").checked,
          playback_mode: document.getElementById("edit-playback").value,
          add_tag_ids: [...next].filter((id) => !current.has(id)),
          remove_tag_ids: [...current].filter((id) => !next.has(id)),
        }),
      });
      if (!response.ok) throw new Error(await errorText(response));
    }
    document.getElementById("editor").hidden = true;
    selectedIds = new Set();
    catalog = [];
    await loadGuide();
  } catch (err) {
    error.textContent = err.message;
  }
}

document.addEventListener("keydown", (event) => {
  const settingsOpen = !document.getElementById("settings").hidden;
  const importOpen = !document.getElementById("import").hidden;
  const editorOpen = !document.getElementById("editor").hidden;
  if (event.key === "Escape") {
    document.getElementById("settings").hidden = true;
    document.getElementById("editor").hidden = true;
    closeImport();
    document.documentElement.classList.remove("video-full");
    document.getElementById("back-guide").hidden = true;
    return;
  }
  if (event.target.closest("input, textarea, select")) return;
  if (settingsOpen || importOpen || editorOpen) return;
  if (event.key === "e" || event.key === "E") {
    openEditor();
    return;
  }
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
    event.preventDefault();
    toggleFullscreen();
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
  const checked = Math.max(0, (playlist.channel_count || 0) - (playlist.pending || 0));
  const counts = `${checked} checked, ${playlist.online || 0} online, ${playlist.offline || 0} offline`;
  if (playlist.status === "checking") return `Checking ${checked} of ${playlist.channel_count}. ${counts}`;
  if (playlist.status === "empty") return "No channels";
  return counts;
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
        { label: "Check now", run: () => checkPlaylist(playlist.id) },
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

async function checkPlaylist(id) {
  const response = await fetch(`/api/playlists/${id}/verify`, { method: "POST" });
  if (!response.ok) {
    showImportError(await errorDetail(response));
    return;
  }
  showImportError("");
  await refreshSources();
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

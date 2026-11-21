const errorNode = document.getElementById("library-error");
const grid = document.getElementById("poster-grid");
const showPage = document.getElementById("show-page");
const details = document.getElementById("details");
const settings = document.getElementById("library-settings");
const video = document.getElementById("player-video");
let refreshTimer = 0;
let kind = "show";
let query = "";
let hls = null;
let playState = null;
let applyingSeek = false;

function showError(message) {
  errorNode.hidden = !message;
  errorNode.textContent = message || "";
}

async function readJson(response) {
  const text = await response.text();
  let body = {};
  if (text) {
    try {
      body = JSON.parse(text);
    } catch (err) {
      throw new Error(text.slice(0, 180));
    }
  }
  if (!response.ok) {
    const detail = body.detail;
    throw new Error(typeof detail === "string" ? detail : response.statusText);
  }
  return body;
}

function kindLabel(value) {
  if (value === "tv") return "TV";
  if (value === "movies") return "Movies";
  return "Misc";
}

function posterNode(item, onClick) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "poster";
  const frame = document.createElement("span");
  frame.className = "poster-frame";
  if (item.has_poster) {
    const image = document.createElement("img");
    image.alt = "";
    image.src = `/api/media/items/${item.id}/poster`;
    image.addEventListener("error", () => image.remove());
    frame.appendChild(image);
  }
  const fallback = document.createElement("span");
  fallback.className = "poster-fallback";
  fallback.textContent = (item.title || "?").slice(0, 2).toUpperCase();
  frame.appendChild(fallback);
  const title = document.createElement("span");
  title.className = "poster-title";
  title.textContent = item.year ? `${item.title} (${item.year})` : item.title;
  button.append(frame, title);
  button.addEventListener("click", onClick);
  return button;
}

function formatTime(seconds) {
  const whole = Math.max(0, Math.floor(seconds || 0));
  const minutes = Math.floor(whole / 60);
  const remain = whole % 60;
  return `${minutes}:${String(remain).padStart(2, "0")}`;
}

async function loadBrowse() {
  showPage.hidden = true;
  details.hidden = true;
  grid.hidden = false;
  grid.innerHTML = "";
  if (kind === "continue") {
    const body = await readJson(await fetch("/api/media/continue"));
    if (!body.items.length) {
      grid.appendChild(Object.assign(document.createElement("p"), { className: "library-note", textContent: "Nothing to continue." }));
      return;
    }
    body.items.forEach((item) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "poster";
      button.innerHTML = `<span class="poster-frame"><span class="poster-fallback"></span></span><span class="poster-title"></span>`;
      button.querySelector(".poster-fallback").textContent = (item.title || "?").slice(0, 2).toUpperCase();
      button.querySelector(".poster-title").textContent = `${item.title} · ${formatTime(item.position)}`;
      button.addEventListener("click", () => openItem(item.item_id, item.file_id));
      grid.appendChild(button);
    });
    return;
  }
  const body = await readJson(await fetch(`/api/media/items?kind=${encodeURIComponent(kind)}&q=${encodeURIComponent(query)}`));
  if (!body.items.length) {
    grid.appendChild(Object.assign(document.createElement("p"), { className: "library-note", textContent: "Nothing in this library yet." }));
    return;
  }
  body.items.forEach((item) => {
    grid.appendChild(posterNode(item, () => openItem(item.id)));
  });
}

async function openItem(itemId, fileId) {
  const item = await readJson(await fetch(`/api/media/items/${itemId}`));
  if (item.kind === "show") {
    renderShow(item);
    return;
  }
  if (item.kind === "season") {
    renderShow(await readJson(await fetch(`/api/media/items/${item.parent_id || itemId}`)));
    return;
  }
  renderDetails(item, fileId);
}

function renderShow(show) {
  grid.hidden = true;
  details.hidden = true;
  showPage.hidden = false;
  showPage.innerHTML = "";
  const back = document.createElement("button");
  back.type = "button";
  back.textContent = "Back";
  back.addEventListener("click", () => loadBrowse().catch((err) => showError(err.message)));
  const heading = document.createElement("h2");
  heading.textContent = show.title;
  const overview = document.createElement("p");
  overview.className = "library-note";
  overview.textContent = show.overview || "";
  showPage.append(back, heading);
  if (show.has_poster) {
    const image = document.createElement("img");
    image.className = "show-poster";
    image.alt = "";
    image.src = `/api/media/items/${show.id}/poster`;
    showPage.appendChild(image);
  }
  showPage.appendChild(overview);
  (show.children || []).forEach((season) => {
    const block = document.createElement("section");
    const title = document.createElement("h3");
    title.textContent = season.title;
    block.appendChild(title);
    const list = document.createElement("div");
    list.className = "episode-list";
    (season.children || []).forEach((episode) => {
      const row = document.createElement("button");
      row.type = "button";
      row.className = "episode";
      const file = (episode.files || [])[0];
      const watched = file && file.watched ? "Watched" : file && file.position > 5 ? `Resume ${formatTime(file.position)}` : "";
      row.textContent = `${episode.episode ? episode.episode + ". " : ""}${episode.title}${watched ? " · " + watched : ""}`;
      row.addEventListener("click", () => renderDetails(episode));
      list.appendChild(row);
    });
    block.appendChild(list);
    showPage.appendChild(block);
  });
}

function renderDetails(item, fileId) {
  const file = (item.files || []).find((entry) => entry.id === fileId) || (item.files || [])[0];
  details.hidden = false;
  details.innerHTML = "";
  const title = document.createElement("h2");
  title.textContent = item.year ? `${item.title} (${item.year})` : item.title;
  const meta = document.createElement("p");
  meta.className = "library-note";
  if (file) {
    meta.textContent = [file.video_codec, file.container, file.width && file.height ? `${file.width}x${file.height}` : "", formatTime(file.duration)].filter(Boolean).join(" · ");
  }
  const overview = document.createElement("p");
  overview.textContent = item.overview || "";
  const actions = document.createElement("div");
  actions.className = "detail-actions";
  if (file) {
    const play = document.createElement("button");
    play.type = "button";
    play.textContent = file.position > 5 && !file.watched ? "Resume" : "Play";
    play.addEventListener("click", () => startPlayback(file, item, file.position > 5 && !file.watched ? file.position : 0));
    const watched = document.createElement("button");
    watched.type = "button";
    watched.textContent = file.watched ? "Watched" : "Mark watched";
    watched.addEventListener("click", () => markWatched(file, item));
    actions.append(play, watched);
  }
  const close = document.createElement("button");
  close.type = "button";
  close.textContent = "Close";
  close.addEventListener("click", () => { details.hidden = true; });
  actions.appendChild(close);
  details.append(title, meta, overview, actions);
}

async function markWatched(file, item) {
  await readJson(await fetch(`/api/media/files/${file.id}/progress`, {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ position: file.duration || 0, duration: file.duration, watched: true }),
  }));
  file.watched = true;
  renderDetails(item, file.id);
}

async function startPlayback(file, item, start) {
  const audio = Number(document.getElementById("player-audio").value || 0);
  const subtitle = document.getElementById("player-subs").value;
  const params = new URLSearchParams({ start: String(start || 0), audio: String(audio) });
  if (subtitle !== "") params.set("subtitle", subtitle);
  const info = await readJson(await fetch(`/api/media/files/${file.id}/play?${params}`));
  await attachPlayback(info, file, item);
}

async function attachPlayback(info, file, item) {
  await closePlayback(false);
  playState = { info, file, item, fileStart: info.file_start || 0, mode: info.mode };
  document.getElementById("player").hidden = false;
  fillTrackSelects(info);
  [...video.querySelectorAll("track")].forEach((track) => track.remove());
  if (info.mode === "direct") {
    video.src = info.url;
    video.addEventListener("loadedmetadata", function seek() {
      video.removeEventListener("loadedmetadata", seek);
      if (info.start > 1) video.currentTime = info.start;
    });
  } else if (window.Hls && window.Hls.isSupported()) {
    hls = new window.Hls();
    hls.loadSource(info.url);
    hls.attachMedia(video);
  } else {
    video.src = info.url;
  }
  if (info.vtt) {
    const track = document.createElement("track");
    track.kind = "subtitles";
    track.label = "Subtitles";
    track.src = info.vtt;
    track.default = true;
    video.appendChild(track);
  }
  try {
    await video.play();
  } catch (err) {
    showError(err.message);
  }
}

function fillTrackSelects(info) {
  const audio = document.getElementById("player-audio");
  const subs = document.getElementById("player-subs");
  audio.innerHTML = "";
  subs.innerHTML = "";
  const none = document.createElement("option");
  none.value = "";
  none.textContent = "Off";
  subs.appendChild(none);
  (info.audio || []).forEach((track, index) => {
    const option = document.createElement("option");
    option.value = String(index);
    option.textContent = track.language || track.title || `Audio ${index + 1}`;
    audio.appendChild(option);
  });
  (info.subtitles || []).forEach((track, index) => {
    const option = document.createElement("option");
    option.value = String(index);
    option.textContent = track.language || track.title || `Subtitle ${index + 1}`;
    subs.appendChild(option);
  });
}

function filePosition() {
  if (!playState) return 0;
  if (playState.mode === "hls") return playState.fileStart + video.currentTime;
  return video.currentTime;
}

async function saveProgress() {
  if (!playState || video.paused) return;
  const position = filePosition();
  if (!Number.isFinite(position)) return;
  await fetch(`/api/media/files/${playState.file.id}/progress`, {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ position, duration: playState.info.duration, watched: false }),
  });
}

async function closePlayback(hide = true) {
  window.clearInterval(playState && playState.timer);
  if (playState) await saveProgress().catch(() => {});
  if (playState && playState.info.session) {
    await fetch(`/api/media/sessions/${playState.info.session}`, { method: "DELETE" }).catch(() => {});
  }
  if (hls) {
    hls.destroy();
    hls = null;
  }
  video.pause();
  video.removeAttribute("src");
  video.innerHTML = "";
  playState = null;
  if (hide) document.getElementById("player").hidden = true;
}

video.addEventListener("seeking", () => {
  if (!playState || playState.mode !== "hls" || applyingSeek) return;
  const end = video.buffered.length ? video.buffered.end(video.buffered.length - 1) : 0;
  if (video.currentTime <= end + 2) return;
  const target = playState.fileStart + video.currentTime;
  applyingSeek = true;
  startPlayback(playState.file, playState.item, target).catch((err) => showError(err.message)).finally(() => {
    applyingSeek = false;
  });
});

document.getElementById("player-close").addEventListener("click", () => closePlayback().catch((err) => showError(err.message)));
document.getElementById("player-watched").addEventListener("click", () => {
  if (!playState) return;
  markWatched(playState.file, playState.item).catch((err) => showError(err.message));
});
document.getElementById("player-audio").addEventListener("change", () => {
  if (!playState) return;
  startPlayback(playState.file, playState.item, filePosition()).catch((err) => showError(err.message));
});
document.getElementById("player-subs").addEventListener("change", () => {
  if (!playState) return;
  startPlayback(playState.file, playState.item, filePosition()).catch((err) => showError(err.message));
});
window.setInterval(() => saveProgress().catch(() => {}), 5000);

async function loadSuggestions() {
  const body = await readJson(await fetch("/api/media/suggestions"));
  const host = document.getElementById("suggestions");
  host.innerHTML = "";
  body.suggestions.forEach((item) => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = `Add ${item.name}`;
    button.addEventListener("click", () => addLibrary(item.name, item.path, item.kind));
    host.appendChild(button);
  });
}

async function loadLibraries() {
  const body = await readJson(await fetch("/api/media/libraries"));
  const host = document.getElementById("library-list");
  host.innerHTML = "";
  let running = false;
  body.libraries.forEach((library) => {
    if (library.scan.state === "running") running = true;
    const row = document.createElement("div");
    row.className = "library-row";
    const title = document.createElement("strong");
    title.textContent = `${library.name} · ${kindLabel(library.kind)} · /media/${library.path}`;
    const progress = document.createElement("span");
    const scan = library.scan;
    progress.textContent = scan.state === "running" ? `Scanning ${scan.done}/${scan.total}` : scan.message || `${scan.total} files`;
    const rescan = document.createElement("button");
    rescan.type = "button";
    rescan.textContent = "Rescan";
    rescan.addEventListener("click", () => postAction(`/api/media/libraries/${library.id}/rescan`));
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "Remove";
    remove.addEventListener("click", () => postAction(`/api/media/libraries/${library.id}`, "DELETE"));
    row.append(title, progress, rescan, remove);
    host.appendChild(row);
  });
  if (!body.libraries.length) settings.hidden = false;
  return running;
}

async function loadSettings() {
  const body = await readJson(await fetch("/api/media/settings"));
  document.getElementById("tmdb-state").textContent = body.tmdb_configured ? "Key saved" : "No key";
  const accel = await readJson(await fetch("/api/media/accel"));
  document.getElementById("accel-status").textContent = accel.available
    ? `Hardware acceleration: ${accel.reason} (${accel.device})`
    : `Hardware acceleration: ${accel.reason}`;
}

async function refreshSettings() {
  window.clearTimeout(refreshTimer);
  await loadSuggestions();
  const running = await loadLibraries();
  await loadSettings();
  if (running) refreshTimer = window.setTimeout(() => refreshSettings().then(loadBrowse).catch((err) => showError(err.message)), 1000);
}

async function addLibrary(name, path, libraryKind) {
  const response = await fetch("/api/media/libraries", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ name, path, kind: libraryKind }),
  });
  await readJson(response);
  await refreshSettings();
  await loadBrowse();
}

async function postAction(url, method = "POST") {
  await readJson(await fetch(url, { method }));
  await refreshSettings();
  await loadBrowse();
}

document.getElementById("library-form").addEventListener("submit", (event) => {
  event.preventDefault();
  addLibrary(
    document.getElementById("lib-name").value.trim(),
    document.getElementById("lib-path").value.trim(),
    document.getElementById("lib-kind").value,
  ).catch((err) => showError(err.message));
});

document.getElementById("tmdb-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await readJson(await fetch("/api/media/settings", {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ tmdb_api_key: document.getElementById("tmdb-key").value }),
    }));
    document.getElementById("tmdb-key").value = "";
    await loadSettings();
  } catch (err) {
    showError(err.message);
  }
});

document.getElementById("tmdb-clear").addEventListener("click", () => {
  document.getElementById("tmdb-key").value = "";
  fetch("/api/media/settings", {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ tmdb_api_key: "" }),
  }).then(readJson).then(loadSettings).catch((err) => showError(err.message));
});

document.getElementById("open-library-settings").addEventListener("click", () => {
  settings.hidden = !settings.hidden;
});

document.getElementById("library-tabs").addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (!button) return;
  kind = button.dataset.kind;
  [...document.querySelectorAll("#library-tabs button")].forEach((item) => {
    item.setAttribute("aria-selected", item === button ? "true" : "false");
  });
  loadBrowse().catch((err) => showError(err.message));
});

let searchTimer = 0;
document.getElementById("library-search").addEventListener("input", (event) => {
  query = event.target.value.trim();
  window.clearTimeout(searchTimer);
  searchTimer = window.setTimeout(() => loadBrowse().catch((err) => showError(err.message)), 200);
});

refreshSettings().then(loadBrowse).catch((err) => showError(err.message));

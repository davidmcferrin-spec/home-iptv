const errorNode = document.getElementById("library-error");

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

function kindLabel(kind) {
  if (kind === "tv") return "TV";
  if (kind === "movies") return "Movies";
  return "Misc";
}

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
    progress.textContent = scan.state === "running"
      ? `Scanning ${scan.done}/${scan.total}`
      : scan.message || `${scan.total} files`;
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
  return running;
}

async function loadItems() {
  const body = await readJson(await fetch("/api/media/items"));
  const list = document.getElementById("item-list");
  list.innerHTML = "";
  body.items.forEach((item) => {
    const row = document.createElement("li");
    const year = item.year ? ` (${item.year})` : "";
    row.textContent = `${item.title}${year}`;
    list.appendChild(row);
  });
}

async function loadSettings() {
  const body = await readJson(await fetch("/api/media/settings"));
  document.getElementById("tmdb-state").textContent = body.tmdb_configured ? "Key saved" : "No key";
}

let refreshTimer = 0;

async function refresh() {
  window.clearTimeout(refreshTimer);
  try {
    await loadSuggestions();
    const running = await loadLibraries();
    await loadItems();
    await loadSettings();
    showError("");
    if (running) refreshTimer = window.setTimeout(refresh, 1000);
  } catch (err) {
    showError(err.message);
  }
}

async function addLibrary(name, path, kind) {
  showError("");
  const response = await fetch("/api/media/libraries", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ name, path, kind }),
  });
  try {
    await readJson(response);
  } catch (err) {
    showError(err.message);
    return;
  }
  refresh();
}

async function postAction(url, method = "POST") {
  showError("");
  const response = await fetch(url, { method });
  try {
    await readJson(response);
  } catch (err) {
    showError(err.message);
    return;
  }
  refresh();
}

document.getElementById("library-form").addEventListener("submit", (event) => {
  event.preventDefault();
  addLibrary(
    document.getElementById("lib-name").value.trim(),
    document.getElementById("lib-path").value.trim(),
    document.getElementById("lib-kind").value,
  );
});

document.getElementById("tmdb-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const response = await fetch("/api/media/settings", {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ tmdb_api_key: document.getElementById("tmdb-key").value }),
  });
  try {
    await readJson(response);
    document.getElementById("tmdb-key").value = "";
  } catch (err) {
    showError(err.message);
    return;
  }
  refresh();
});

document.getElementById("tmdb-clear").addEventListener("click", async () => {
  document.getElementById("tmdb-key").value = "";
  const response = await fetch("/api/media/settings", {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ tmdb_api_key: "" }),
  });
  try {
    await readJson(response);
  } catch (err) {
    showError(err.message);
    return;
  }
  refresh();
});

refresh();

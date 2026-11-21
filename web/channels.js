const pageSize = 100;
const FAV_KEY = "home-iptv-favorites";
let page = 0;
let total = 0;
let selected = new Set();
let current = [];
let cursor = 0;
let anchor = null;
let favoriteUndo = null;

const rows = document.getElementById("rows");

function favorites() {
  try {
    const value = JSON.parse(localStorage.getItem(FAV_KEY) || "[]");
    return new Set(Array.isArray(value) ? value : []);
  } catch (err) {
    if (err instanceof SyntaxError) return new Set();
    throw err;
  }
}

function storeFavorites(ids) {
  localStorage.setItem(FAV_KEY, JSON.stringify([...ids]));
}

function filters() {
  const params = new URLSearchParams({
    include_hidden: "true",
    sort: document.getElementById("f-sort").value || "number",
    order: "asc",
  });
  const q = document.getElementById("q").value.trim();
  if (q) params.set("q", q);
  const status = document.getElementById("f-status").value;
  if (status) params.set("status", status);
  const group = document.getElementById("f-group").value;
  if (group) params.set("group", group);
  const country = document.getElementById("f-country").value;
  if (country) params.set("country", country);
  const tag = document.getElementById("f-tag").value;
  if (tag) params.set("tag", tag);
  if (document.getElementById("f-adult").checked) params.set("include_adult", "true");
  return params;
}

function query() {
  const params = filters();
  params.set("limit", String(pageSize));
  params.set("offset", String(page * pageSize));
  return params;
}

function report(message) {
  document.getElementById("result").textContent = message || "";
}

async function load() {
  const loading = document.getElementById("loading");
  loading.hidden = false;
  try {
    const response = await fetch(`/api/channels?${query()}`);
    if (!response.ok) throw new Error(`channel list returned ${response.status}`);
    const body = await response.json();
    total = body.total;
    current = body.channels;
    if (cursor >= current.length) cursor = 0;
    draw();
  } finally {
    loading.hidden = true;
  }
}

function statusLabel(status) {
  if (status === "online") return "Online";
  if (status === "offline") return "Offline";
  return "Unchecked";
}

function draw() {
  rows.innerHTML = "";
  current.forEach((channel, index) => {
    const tr = document.createElement("tr");
    tr.dataset.id = String(channel.id);
    if (selected.has(channel.id)) tr.classList.add("selected");
    if (index === cursor) tr.classList.add("cursor");
    const pick = document.createElement("input");
    pick.type = "checkbox";
    pick.className = "pick";
    pick.checked = selected.has(channel.id);
    pick.setAttribute("aria-label", `Select ${channel.title}`);
    pick.addEventListener("click", (event) => togglePick(index, pick.checked, event.shiftKey));
    const number = inlineInput(channel, "guide_number", channel.guide_number || "", "Number");
    const name = inlineInput(channel, "title", channel.title || "", "Name");
    const status = document.createElement("span");
    status.className = `status-pill ${channel.status || "unknown"}`;
    status.textContent = statusLabel(channel.status);
    const tags = document.createElement("span");
    tags.className = "tag-line";
    (channel.tags || []).forEach((tag) => {
      const pill = document.createElement("span");
      pill.className = "tag-pill";
      pill.textContent = tag.name;
      tags.appendChild(pill);
    });
    [pick, logo(channel), number, name, text(channel.group_title || ""), status, tags, editButton(channel)].forEach((node) => {
      const td = document.createElement("td");
      td.appendChild(node);
      tr.appendChild(td);
    });
    tr.addEventListener("dblclick", (event) => {
      if (event.target.closest("button, a")) return;
      openDrawer(channel);
    });
    tr.addEventListener("click", (event) => {
      if (event.target.closest("input, button, a, label")) return;
      cursor = index;
      rows.querySelectorAll("tr").forEach((row, rowIndex) => {
        row.classList.toggle("cursor", rowIndex === cursor);
      });
    });
    rows.appendChild(tr);
  });
  const empty = document.getElementById("empty-state");
  const table = document.getElementById("channel-table");
  empty.hidden = total !== 0;
  table.hidden = total === 0;
  document.getElementById("channel-count").textContent = total === 1 ? "1 channel" : `${total} channels`;
  const start = total ? page * pageSize + 1 : 0;
  const end = Math.min(total, (page + 1) * pageSize);
  document.getElementById("page-label").textContent = total ? `${start}–${end} of ${total}` : "0 channels";
  document.getElementById("prev-page").disabled = page === 0;
  document.getElementById("next-page").disabled = end >= total;
  const pageBox = document.getElementById("select-page");
  pageBox.checked = current.length > 0 && current.every((channel) => selected.has(channel.id));
  pageBox.indeterminate = !pageBox.checked && current.some((channel) => selected.has(channel.id));
  updateBulk();
  const focused = rows.querySelector("tr.cursor");
  if (focused) focused.scrollIntoView({ block: "nearest" });
}

function togglePick(index, on, shift) {
  if (shift && anchor != null) {
    const start = Math.min(anchor, index);
    const end = Math.max(anchor, index);
    for (let item = start; item <= end; item += 1) {
      if (on) selected.add(current[item].id);
      else selected.delete(current[item].id);
    }
  } else if (on) selected.add(current[index].id);
  else selected.delete(current[index].id);
  anchor = index;
  cursor = index;
  draw();
}

function text(value) {
  const span = document.createElement("span");
  span.textContent = value;
  return span;
}

function initials(channel) {
  const mark = document.createElement("span");
  mark.className = "logo-fallback";
  const letters = (channel.title || "").replace(/[^A-Za-z0-9]/g, "").slice(0, 2).toUpperCase();
  mark.textContent = letters || "TV";
  return mark;
}

function logo(channel) {
  if (!channel.logo_url) return initials(channel);
  const img = document.createElement("img");
  img.className = "logo";
  img.alt = "";
  img.src = `/api/logo/${channel.id}`;
  img.addEventListener("error", () => img.replaceWith(initials(channel)));
  return img;
}

function inlineInput(channel, field, value, label) {
  const input = document.createElement("input");
  input.value = value;
  input.className = "cell-edit";
  input.setAttribute("aria-label", `${label} for ${channel.title}`);
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") input.blur();
    if (event.key === "Escape") {
      input.value = value;
      input.blur();
    }
  });
  input.addEventListener("change", () => saveField(channel, field, input.value.trim(), input));
  return input;
}

function editButton(channel) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "edit-row";
  button.setAttribute("aria-label", `Edit ${channel.title}`);
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("width", "16");
  svg.setAttribute("height", "16");
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("fill", "currentColor");
  path.setAttribute("d", "M4 16.5 L14.5 6 L18 9.5 L7.5 20 H4 Z M15.2 5.3 L16.7 3.8 L20.2 7.3 L18.7 8.8 Z");
  svg.appendChild(path);
  button.append(svg, document.createTextNode(" Edit"));
  button.addEventListener("click", () => openDrawer(channel));
  return button;
}

async function saveField(channel, field, value, input) {
  const response = await fetch(`/api/channels/${channel.id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ [field]: value }),
  });
  if (!response.ok) {
    report(`Could not save ${channel.title}`);
    input.value = channel[field] || "";
    return;
  }
  Object.assign(channel, await response.json());
  report(`Saved ${channel.title}`);
}

function updateBulk() {
  const count = selected.size;
  document.getElementById("bulk-count").textContent = count === 1 ? "1 selected" : `${count} selected`;
  document.getElementById("open-bulk").disabled = count === 0;
  document.getElementById("clear-selection").disabled = count === 0;
}

function openBulk() {
  document.getElementById("bulk-panel").hidden = false;
  document.getElementById("bulk-confirm").hidden = true;
  document.getElementById("bulk-error").textContent = "";
  report("");
  refreshPreview();
}

function refreshPreview() {
  const bits = [];
  if (document.getElementById("b-clear-group").checked) bits.push("clear group");
  else if (document.getElementById("b-group").value.trim()) bits.push("set group");
  if (document.getElementById("b-add-tags").value.trim()) bits.push("add tags");
  if (document.getElementById("b-remove-tags").value.trim()) bits.push("remove tags");
  if (document.getElementById("b-hidden").value) bits.push("change visibility");
  if (document.getElementById("b-favorite").value) bits.push("change favorites");
  if (document.getElementById("b-clear-guide").checked || document.getElementById("b-guide").value.trim()) bits.push("change guide match");
  if (document.getElementById("b-renumber").value.trim()) bits.push("renumber");
  const detail = bits.length ? `: ${bits.join(", ")}` : "";
  const countLabel = selected.size === 1 ? "1 channel" : `${selected.size} channels`;
  document.getElementById("bulk-preview").textContent = `${countLabel} will change${detail}.`;
}

function closeBulk() {
  document.getElementById("bulk-panel").hidden = true;
}

async function tagIds(names) {
  const ids = [];
  if (!names.length) return ids;
  const listed = await fetch("/api/tags?include_adult=true");
  if (!listed.ok) throw new Error(`tags returned ${listed.status}`);
  let known = (await listed.json()).tags;
  for (const name of names) {
    let tag = known.find((item) => item.name.toLowerCase() === name.toLowerCase());
    if (!tag) {
      const created = await fetch("/api/tags", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      if (created.status === 409) {
        const again = await fetch("/api/tags?include_adult=true");
        known = again.ok ? (await again.json()).tags : known;
        tag = known.find((item) => item.name.toLowerCase() === name.toLowerCase());
      } else if (!created.ok) {
        throw new Error(`tag ${name} was not created`);
      } else {
        tag = await created.json();
        known.push(tag);
      }
    }
    if (tag) ids.push(tag.id);
  }
  return ids;
}

function splitTags(value) {
  return value.split(",").map((item) => item.trim()).filter(Boolean);
}

async function applyBulk(event) {
  event.preventDefault();
  const error = document.getElementById("bulk-error");
  error.textContent = "";
  const ids = [...selected];
  if (!ids.length) return;
  const body = { ids };
  const changes = [];
  if (document.getElementById("b-clear-group").checked) {
    body.group_title = "";
    changes.push("clear group");
  } else if (document.getElementById("b-group").value.trim()) {
    body.group_title = document.getElementById("b-group").value.trim();
    changes.push(`group ${body.group_title}`);
  }
  const hidden = document.getElementById("b-hidden").value;
  if (hidden === "hide") {
    body.hidden = true;
    changes.push("hide");
  } else if (hidden === "show") {
    body.hidden = false;
    changes.push("show");
  }
  if (document.getElementById("b-clear-guide").checked) {
    body.tvg_id = "";
    changes.push("clear guide match");
  } else if (document.getElementById("b-guide").value.trim()) {
    body.tvg_id = document.getElementById("b-guide").value.trim();
    changes.push("set guide match");
  }
  const renumber = document.getElementById("b-renumber").value.trim();
  if (renumber) {
    body.renumber_from = Number(renumber);
    changes.push(`renumber from ${renumber}`);
  }
  const addNames = splitTags(document.getElementById("b-add-tags").value);
  const removeNames = splitTags(document.getElementById("b-remove-tags").value);
  try {
    if (addNames.length) {
      body.add_tag_ids = await tagIds(addNames);
      changes.push("add tags");
    }
    if (removeNames.length) {
      body.remove_tag_ids = await tagIds(removeNames);
      changes.push("remove tags");
    }
  } catch (err) {
    error.textContent = err.message;
    return;
  }
  const favorite = document.getElementById("b-favorite").value;
  const serverChange = Object.keys(body).length > 1;
  if (!serverChange && !favorite) {
    error.textContent = "Choose at least one change";
    return;
  }
  document.getElementById("bulk-preview").textContent = `${ids.length} channels will change: ${changes.concat(favorite === "on" ? ["favorite"] : favorite === "off" ? ["unfavorite"] : []).join(", ")}.`;
  favoriteUndo = [...favorites()];
  if (favorite) {
    const next = favorites();
    ids.forEach((id) => {
      if (favorite === "on") next.add(id);
      else next.delete(id);
    });
    storeFavorites(next);
  }
  if (!serverChange) {
    report(`Updated ${ids.length}. Failed 0.`);
    closeBulk();
    return;
  }
  const response = await fetch("/api/channels/bulk", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    let detail = `bulk edit returned ${response.status}`;
    try {
      const payload = await response.json();
      if (typeof payload.detail === "string") detail = payload.detail;
    } catch (err) {
      if (!(err instanceof SyntaxError)) throw err;
    }
    error.textContent = detail;
    return;
  }
  const payload = await response.json();
  const failed = payload.errors || [];
  const detail = failed.length ? ` ${failed.map((item) => `${item.id} ${item.error}`).join("; ")}` : "";
  report(`Updated ${payload.succeeded}. Failed ${payload.failed}.${detail}`);
  closeBulk();
  await loadFacets();
  await load();
}

async function deleteSelected() {
  favoriteUndo = [...favorites()];
  const response = await fetch("/api/channels/bulk", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ids: [...selected], delete: true }),
  });
  if (!response.ok) {
    document.getElementById("bulk-error").textContent = `delete returned ${response.status}`;
    return;
  }
  const payload = await response.json();
  selected = new Set();
  report(`Deleted ${payload.succeeded}. Failed ${payload.failed}.`);
  closeBulk();
  await load();
}

async function undoLast() {
  const response = await fetch("/api/channels/undo", { method: "POST" });
  const hadFavorites = favoriteUndo != null;
  if (favoriteUndo) {
    storeFavorites(new Set(favoriteUndo));
    favoriteUndo = null;
  }
  if (!response.ok && response.status !== 404) {
    report(`Undo returned ${response.status}`);
    return;
  }
  if (!response.ok) report(hadFavorites ? "Restored favorites" : "Nothing to undo");
  else report("Undone");
  selected = new Set();
  await load();
}

function openDrawer(channel) {
  document.getElementById("drawer").hidden = false;
  document.getElementById("drawer").dataset.id = String(channel.id);
  document.getElementById("drawer-title").textContent = `Edit ${channel.title || "channel"}`;
  document.getElementById("d-title").value = channel.title || "";
  document.getElementById("d-url").value = channel.url || "";
  document.getElementById("d-number").value = channel.guide_number || "";
  document.getElementById("d-group").value = channel.group_title || "";
  document.getElementById("d-logo").value = channel.logo_url || "";
  document.getElementById("d-logo-preview").src = `/api/logo/${channel.id}`;
  document.getElementById("d-guide").value = channel.tvg_id || "";
  document.getElementById("d-tags").value = (channel.tags || []).map((tag) => tag.name).join(", ");
  document.getElementById("d-language").value = channel.language || "";
  document.getElementById("d-country").value = channel.country || "";
  document.getElementById("d-adult").checked = Boolean(channel.adult);
  document.getElementById("d-hidden").checked = Boolean(channel.hidden);
  document.getElementById("d-playback").value = channel.playback_mode || "auto";
  document.getElementById("d-notes").value = channel.notes || "";
  document.getElementById("d-probe").textContent = [statusLabel(channel.status), channel.status_detail].filter(Boolean).join(" · ");
  document.getElementById("d-error").textContent = "";
  fillMatches(channel.title || "");
  document.getElementById("d-title").focus();
}

async function fillMatches(q) {
  const list = document.getElementById("guide-matches");
  list.innerHTML = "";
  if (!q) return;
  const response = await fetch(`/api/guide/channels?q=${encodeURIComponent(q)}`);
  if (!response.ok) return;
  const body = await response.json();
  (body.channels || body.matches || []).slice(0, 20).forEach((item) => {
    const option = document.createElement("option");
    option.value = item.id || item.tvg_id || item.name;
    list.appendChild(option);
  });
}

document.getElementById("drawer-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const id = document.getElementById("drawer").dataset.id;
  const channel = current.find((item) => String(item.id) === id) || { tags: [] };
  const names = splitTags(document.getElementById("d-tags").value);
  const error = document.getElementById("d-error");
  let nextIds = [];
  try {
    nextIds = await tagIds(names);
  } catch (err) {
    error.textContent = err.message;
    return;
  }
  const currentIds = new Set((channel.tags || []).map((tag) => tag.id));
  const response = await fetch(`/api/channels/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      title: document.getElementById("d-title").value.trim(),
      guide_number: document.getElementById("d-number").value.trim(),
      group_title: document.getElementById("d-group").value.trim(),
      logo_url: document.getElementById("d-logo").value.trim(),
      tvg_id: document.getElementById("d-guide").value.trim(),
      language: document.getElementById("d-language").value.trim(),
      country: document.getElementById("d-country").value.trim(),
      adult: document.getElementById("d-adult").checked,
      hidden: document.getElementById("d-hidden").checked,
      playback_mode: document.getElementById("d-playback").value,
      notes: document.getElementById("d-notes").value.trim(),
      add_tag_ids: nextIds.filter((tagId) => !currentIds.has(tagId)),
      remove_tag_ids: [...currentIds].filter((tagId) => !nextIds.includes(tagId)),
    }),
  });
  if (!response.ok) {
    error.textContent = `That channel was not saved (${response.status})`;
    return;
  }
  document.getElementById("drawer").hidden = true;
  await loadFacets();
  await load();
});

function fillFacet(id, values, blank) {
  const select = document.getElementById(id);
  const currentValue = select.value;
  select.replaceChildren();
  const first = document.createElement("option");
  first.value = "";
  first.textContent = blank;
  select.appendChild(first);
  values.forEach((value) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value;
    select.appendChild(option);
  });
  if (id === "f-group") {
    const none = document.createElement("option");
    none.value = "__none__";
    none.textContent = "No group";
    select.appendChild(none);
  }
  if ([...select.options].some((option) => option.value === currentValue)) select.value = currentValue;
}

async function loadFacets() {
  const response = await fetch("/api/channels/facets?include_hidden=true&include_adult=true");
  if (!response.ok) return;
  const body = await response.json();
  fillFacet("f-group", body.groups || [], "Any group");
  fillFacet("f-country", body.countries || [], "Any country");
  fillFacet("f-tag", body.tags || [], "Any tag");
}

["f-status", "f-group", "f-tag", "f-country", "f-sort", "f-adult"].forEach((id) => {
  document.getElementById(id).addEventListener("change", () => {
    page = 0;
    load().catch((err) => report(err.message));
  });
});
let searchTimer = 0;
document.getElementById("q").addEventListener("input", () => {
  window.clearTimeout(searchTimer);
  searchTimer = window.setTimeout(() => {
    page = 0;
    load().catch((err) => report(err.message));
  }, 200);
});
document.getElementById("prev-page").addEventListener("click", () => {
  page = Math.max(0, page - 1);
  load().catch((err) => report(err.message));
});
document.getElementById("next-page").addEventListener("click", () => {
  page += 1;
  load().catch((err) => report(err.message));
});
document.getElementById("select-page").addEventListener("change", (event) => {
  current.forEach((channel) => {
    if (event.target.checked) selected.add(channel.id);
    else selected.delete(channel.id);
  });
  anchor = 0;
  draw();
});
document.getElementById("select-matching").addEventListener("click", async () => {
  const params = filters();
  params.set("limit", "1000");
  let offset = 0;
  const ids = [];
  while (true) {
    params.set("offset", String(offset));
    const response = await fetch(`/api/channels?${params}`);
    if (!response.ok) {
      report(`channel list returned ${response.status}`);
      return;
    }
    const body = await response.json();
    body.channels.forEach((channel) => ids.push(channel.id));
    offset += body.channels.length;
    if (!body.channels.length || offset >= body.total) break;
  }
  selected = new Set(ids);
  report(`Selected ${ids.length} matching channels`);
  draw();
});
document.getElementById("clear-selection").addEventListener("click", () => {
  selected = new Set();
  draw();
});
document.getElementById("open-bulk").addEventListener("click", openBulk);
document.getElementById("close-bulk").addEventListener("click", closeBulk);
["b-group", "b-add-tags", "b-remove-tags", "b-guide", "b-renumber"].forEach((id) => {
  document.getElementById(id).addEventListener("input", refreshPreview);
});
["b-clear-group", "b-clear-guide", "b-hidden", "b-favorite"].forEach((id) => {
  document.getElementById(id).addEventListener("change", refreshPreview);
});
document.getElementById("bulk-form").addEventListener("submit", (event) => {
  applyBulk(event).catch((err) => {
    document.getElementById("bulk-error").textContent = err.message;
  });
});
document.getElementById("bulk-ask-delete").addEventListener("click", () => {
  document.getElementById("bulk-confirm").hidden = false;
  document.getElementById("bulk-confirm-text").textContent = `Delete ${selected.size} channels? Undo can put them back.`;
});
document.getElementById("bulk-cancel-delete").addEventListener("click", () => {
  document.getElementById("bulk-confirm").hidden = true;
});
document.getElementById("bulk-delete").addEventListener("click", () => {
  deleteSelected().catch((err) => {
    document.getElementById("bulk-error").textContent = err.message;
  });
});
document.getElementById("undo").addEventListener("click", () => {
  undoLast().catch((err) => report(err.message));
});
document.getElementById("close-drawer").addEventListener("click", () => {
  document.getElementById("drawer").hidden = true;
});
document.addEventListener("keydown", (event) => {
  const drawerOpen = !document.getElementById("drawer").hidden;
  const bulkOpen = !document.getElementById("bulk-panel").hidden;
  if (event.key === "Escape") {
    document.getElementById("drawer").hidden = true;
    if (bulkOpen) closeBulk();
    return;
  }
  if (drawerOpen && event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
    event.preventDefault();
    document.getElementById("drawer-form").requestSubmit();
    return;
  }
  if (event.target.closest("input, textarea, select")) return;
  if (drawerOpen || bulkOpen) return;
  if (event.key === "ArrowDown") {
    event.preventDefault();
    cursor = Math.min(current.length - 1, cursor + 1);
    draw();
  } else if (event.key === "ArrowUp") {
    event.preventDefault();
    cursor = Math.max(0, cursor - 1);
    draw();
  } else if (event.key === "x" || event.key === "X") {
    if (!current[cursor]) return;
    const id = current[cursor].id;
    if (selected.has(id)) selected.delete(id);
    else selected.add(id);
    anchor = cursor;
    draw();
  } else if (event.key === "e" || event.key === "E") {
    if (current[cursor]) openDrawer(current[cursor]);
  } else if (event.key === "/") {
    event.preventDefault();
    document.getElementById("q").focus();
  }
});

loadFacets().catch((err) => report(err.message));
load().catch((err) => report(err.message));

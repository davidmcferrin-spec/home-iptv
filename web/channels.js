const pageSize = 100;
let page = 0;
let total = 0;
let selected = new Set();
let current = [];

const rows = document.getElementById("rows");

function query() {
  const params = new URLSearchParams({
    limit: String(pageSize),
    offset: String(page * pageSize),
    include_hidden: "true",
    sort: "number",
    order: "asc",
  });
  const q = document.getElementById("q").value.trim();
  if (q) params.set("q", q);
  const status = document.getElementById("f-status").value;
  if (status) params.set("status", status);
  const playback = document.getElementById("f-playback").value;
  if (playback) params.set("playback_mode", playback);
  const source = document.getElementById("f-source").value;
  if (source) params.set("source", source);
  const language = document.getElementById("f-language").value.trim();
  if (language) params.set("language", language);
  const country = document.getElementById("f-country").value.trim();
  if (country) params.set("country", country);
  const tag = document.getElementById("f-tag").value.trim();
  if (tag) params.set("tag", tag);
  if (document.getElementById("f-adult").checked) params.set("include_adult", "true");
  return params;
}

async function load() {
  const response = await fetch(`/api/channels?${query()}`);
  if (!response.ok) throw new Error("channel list failed");
  const body = await response.json();
  total = body.total;
  current = body.channels;
  draw();
}

function draw() {
  rows.innerHTML = "";
  current.forEach((channel) => {
    const tr = document.createElement("tr");
    tr.dataset.id = String(channel.id);
    tr.draggable = true;
    const pick = document.createElement("input");
    pick.type = "checkbox";
    pick.className = "pick";
    pick.checked = selected.has(channel.id);
    pick.setAttribute("aria-label", `Select ${channel.title}`);
    pick.addEventListener("change", () => {
      if (pick.checked) selected.add(channel.id);
      else selected.delete(channel.id);
      updateBulk();
    });
    const cells = [
      pick,
      logo(channel),
      inputCell(channel, "guide_number", channel.guide_number || ""),
      inputCell(channel, "title", channel.title || ""),
      text(channel.group_title || ""),
      text((channel.tags || []).map((tag) => tag.name).join(", ")),
      inputCell(channel, "language", channel.language || ""),
      inputCell(channel, "country", channel.country || ""),
      adultCell(channel),
      text(channel.status || "unknown"),
      text(channel.last_checked_at || ""),
      text(channel.tvg_id || ""),
      playbackCell(channel),
    ];
    cells.forEach((node) => {
      const td = document.createElement("td");
      td.appendChild(node);
      tr.appendChild(td);
    });
    tr.addEventListener("dblclick", () => openDrawer(channel));
    rows.appendChild(tr);
  });
  const start = total ? page * pageSize + 1 : 0;
  const end = Math.min(total, (page + 1) * pageSize);
  document.getElementById("page-label").textContent = `${start}–${end} of ${total}`;
  document.getElementById("prev-page").disabled = page === 0;
  document.getElementById("next-page").disabled = end >= total;
  updateBulk();
}

function text(value) {
  const span = document.createElement("span");
  span.textContent = value;
  return span;
}

function logo(channel) {
  const img = document.createElement("img");
  img.className = "logo";
  img.alt = "";
  img.src = `/api/logo/${channel.id}`;
  return img;
}

function inputCell(channel, field, value) {
  const input = document.createElement("input");
  input.value = value;
  input.setAttribute("aria-label", `${field} for ${channel.title}`);
  input.addEventListener("change", () => saveField(channel, field, input.value.trim()));
  return input;
}

function adultCell(channel) {
  const input = document.createElement("input");
  input.type = "checkbox";
  input.checked = Boolean(channel.adult);
  input.setAttribute("aria-label", `Adult ${channel.title}`);
  input.addEventListener("change", () => saveField(channel, "adult", input.checked));
  return input;
}

function playbackCell(channel) {
  const select = document.createElement("select");
  select.setAttribute("aria-label", `Playback for ${channel.title}`);
  ["auto", "webrtc", "hls", "transcode"].forEach((mode) => {
    const option = document.createElement("option");
    option.value = mode;
    option.textContent = mode;
    select.appendChild(option);
  });
  select.value = channel.playback_mode || "auto";
  select.addEventListener("change", () => saveField(channel, "playback_mode", select.value));
  return select;
}

async function saveField(channel, field, value) {
  const body = {};
  body[field] = value;
  const response = await fetch(`/api/channels/${channel.id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) window.alert("That change was not saved");
  else Object.assign(channel, await response.json());
}

function updateBulk() {
  const bar = document.getElementById("bulk-bar");
  bar.hidden = selected.size === 0;
  document.getElementById("bulk-count").textContent = `${selected.size} selected`;
}

async function bulk(body) {
  const response = await fetch("/api/channels/bulk", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ids: [...selected], ...body }),
  });
  if (!response.ok) {
    window.alert("That change was not saved");
    return;
  }
  selected = new Set();
  await load();
}

function openDrawer(channel) {
  document.getElementById("drawer").hidden = false;
  document.getElementById("drawer").dataset.id = String(channel.id);
  document.getElementById("d-title").value = channel.title || "";
  document.getElementById("d-number").value = channel.guide_number || "";
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
  const probe = [channel.video_codec, channel.video_profile, channel.has_b_frames != null ? `${channel.has_b_frames} b-frames` : "", channel.status_detail]
    .filter(Boolean)
    .join(" · ");
  document.getElementById("d-probe").textContent = probe;
  document.getElementById("d-error").textContent = "";
  fillMatches(channel.title || "");
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
  const channel = current.find((item) => String(item.id) === id);
  const names = document.getElementById("d-tags").value.split(",").map((item) => item.trim()).filter(Boolean);
  const currentIds = new Set((channel.tags || []).map((tag) => tag.id));
  const nextIds = [];
  for (const name of names) {
    let tag = null;
    const listed = await fetch("/api/tags?include_adult=true");
    const known = listed.ok ? (await listed.json()).tags : [];
    tag = known.find((item) => item.name.toLowerCase() === name.toLowerCase());
    if (!tag) {
      const created = await fetch("/api/tags", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      if (!created.ok && created.status !== 409) {
        document.getElementById("d-error").textContent = "A tag was not saved";
        return;
      }
      tag = created.ok ? await created.json() : known.find((item) => item.name.toLowerCase() === name.toLowerCase());
    }
    if (tag) nextIds.push(tag.id);
  }
  const response = await fetch(`/api/channels/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      title: document.getElementById("d-title").value.trim(),
      guide_number: document.getElementById("d-number").value.trim(),
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
    document.getElementById("d-error").textContent = "That channel was not saved";
    return;
  }
  document.getElementById("drawer").hidden = true;
  await load();
});

["q", "f-status", "f-playback", "f-source", "f-language", "f-country", "f-tag", "f-adult"].forEach((id) => {
  document.getElementById(id).addEventListener("change", () => {
    page = 0;
    load();
  });
});
document.getElementById("q").addEventListener("input", () => {
  page = 0;
  load();
});
document.getElementById("prev-page").addEventListener("click", () => {
  page = Math.max(0, page - 1);
  load();
});
document.getElementById("next-page").addEventListener("click", () => {
  page += 1;
  load();
});
document.getElementById("bulk-verify").addEventListener("click", () => bulk({ verify: true }));
document.getElementById("bulk-hide").addEventListener("click", () => bulk({ hidden: true }));
document.getElementById("bulk-show").addEventListener("click", () => bulk({ hidden: false }));
document.getElementById("bulk-playback").addEventListener("change", (event) => {
  if (event.target.value) bulk({ playback_mode: event.target.value });
  event.target.value = "";
});
document.getElementById("bulk-delete").addEventListener("click", () => {
  if (window.confirm(`Delete ${selected.size} channels? Undo can put them back.`)) bulk({ delete: true });
});
document.getElementById("undo").addEventListener("click", async () => {
  const response = await fetch("/api/channels/undo", { method: "POST" });
  if (!response.ok) window.alert("Nothing to undo");
  else {
    selected = new Set();
    await load();
  }
});
document.getElementById("close-drawer").addEventListener("click", () => {
  document.getElementById("drawer").hidden = true;
});

rows.addEventListener("dragover", (event) => event.preventDefault());
let dragId = null;
rows.addEventListener("dragstart", (event) => {
  const row = event.target.closest("tr");
  if (!row) return;
  dragId = Number(row.dataset.id);
});
rows.addEventListener("drop", async (event) => {
  const row = event.target.closest("tr");
  if (!row || dragId == null) return;
  const targetId = Number(row.dataset.id);
  const order = current.map((channel) => channel.id);
  const from = order.indexOf(dragId);
  const to = order.indexOf(targetId);
  if (from < 0 || to < 0 || from === to) return;
  order.splice(to, 0, order.splice(from, 1)[0]);
  const start = page * pageSize;
  const assignments = order.map((id, index) => ({ id, guide_number: String(start + index + 1) }));
  await fetch("/api/channels/renumber", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ assignments }),
  });
  dragId = null;
  await load();
});

renderFilterEditor(document.getElementById("filter-editor"));
load().catch((err) => {
  rows.innerHTML = "";
  const tr = document.createElement("tr");
  const td = document.createElement("td");
  td.colSpan = 13;
  td.textContent = err.message;
  tr.appendChild(td);
  rows.appendChild(tr);
});

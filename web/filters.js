async function loadGuideFilters(includeAdult) {
  const query = includeAdult ? "?include_adult=true" : "";
  const response = await fetch(`/api/guide-filters${query}`);
  if (!response.ok) throw new Error("guide filters failed");
  return response.json();
}

async function saveGuideFilters(prefs) {
  const response = await fetch("/api/guide-filters", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(prefs),
  });
  if (!response.ok) throw new Error("guide filters were not saved");
  return response.json();
}

function renderFilterEditor(host, onChange) {
  host.innerHTML = "";
  loadGuideFilters(true).then((data) => {
    const note = document.createElement("p");
    note.className = "filter-note";
    note.textContent = "Shown chips stay on the guide. Pinned chips stay at the front. A label only changes the chip text.";
    host.appendChild(note);
    data.tags.forEach((tag) => {
      const row = document.createElement("div");
      row.className = "filter-row";
      const show = document.createElement("input");
      show.type = "checkbox";
      show.checked = !tag.hidden;
      show.setAttribute("aria-label", `Show ${tag.label}`);
      const pin = document.createElement("button");
      pin.type = "button";
      pin.textContent = tag.pinned ? "Pinned" : "Pin";
      pin.className = "pin";
      const up = document.createElement("button");
      up.type = "button";
      up.textContent = "Up";
      up.hidden = !tag.pinned;
      const label = document.createElement("input");
      label.type = "text";
      label.value = tag.label;
      label.maxLength = 80;
      label.setAttribute("aria-label", `Label for ${tag.name}`);
      const name = document.createElement("span");
      name.className = "filter-name";
      name.textContent = `${tag.name} (${tag.count})`;
      const merge = document.createElement("button");
      merge.type = "button";
      merge.textContent = "Merge";
      show.addEventListener("change", async () => {
        const next = await loadGuideFilters(true);
        const hidden = next.tags.filter((item) => (item.name === tag.name ? !show.checked : item.hidden)).map((item) => item.name);
        const saved = await saveGuideFilters({ hidden, pinned: next.pinned, labels: next.labels });
        if (onChange) onChange(saved);
      });
      pin.addEventListener("click", async () => {
        const next = await loadGuideFilters(true);
        const pinned = next.pinned.filter((item) => item.toLowerCase() !== tag.name.toLowerCase());
        if (!tag.pinned) pinned.push(tag.name);
        tag.pinned = !tag.pinned;
        const saved = await saveGuideFilters({ hidden: next.hidden, pinned, labels: next.labels });
        renderFilterEditor(host, onChange);
        if (onChange) onChange(saved);
      });
      up.addEventListener("click", async () => {
        const next = await loadGuideFilters(true);
        const pinned = next.pinned.slice();
        const index = pinned.findIndex((item) => item.toLowerCase() === tag.name.toLowerCase());
        if (index > 0) {
          const previous = pinned[index - 1];
          pinned[index - 1] = pinned[index];
          pinned[index] = previous;
          const saved = await saveGuideFilters({ hidden: next.hidden, pinned, labels: next.labels });
          renderFilterEditor(host, onChange);
          if (onChange) onChange(saved);
        }
      });
      label.addEventListener("change", async () => {
        const next = await loadGuideFilters(true);
        const labels = { ...next.labels, [tag.name.toLowerCase()]: label.value.trim() || tag.name };
        const saved = await saveGuideFilters({ hidden: next.hidden, pinned: next.pinned, labels });
        if (onChange) onChange(saved);
      });
      merge.addEventListener("click", async () => {
        const target = window.prompt(`Merge ${tag.name} into which tag?`, "");
        if (!target) return;
        const response = await fetch("/api/guide-filters/merge", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ source: tag.name, target }),
        });
        if (!response.ok) {
          window.alert("Those tags were not merged");
          return;
        }
        const saved = await response.json();
        renderFilterEditor(host, onChange);
        if (onChange) onChange(saved);
      });
      row.append(show, pin, up, label, name, merge);
      host.appendChild(row);
    });
  }).catch((err) => {
    host.textContent = err.message;
  });
}

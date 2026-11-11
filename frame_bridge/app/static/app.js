"use strict";
// All URLs are relative: under Ingress the page lives at /api/hassio_ingress/<token>/.

const $ = (id) => document.getElementById(id);
const state = { images: [], status: null, currentContentId: null, file: null, tab: "local", tvLoaded: false };

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  if (res.status === 204) return null;
  const body = res.headers.get("content-type")?.includes("json") ? await res.json() : await res.text();
  if (!res.ok) throw new Error(errorText(body, res.status));
  return body;
}
function errorText(body, status) {
  if (body && typeof body === "object") {
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail)) return body.detail.map((d) => d.msg).join(", ");
  }
  return `Fehler ${status}`;
}

let toastTimer;
function toast(text, bad = false) {
  const t = $("toast");
  t.textContent = text;
  t.className = "toast show" + (bad ? " bad" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.className = "toast"), bad ? 6000 : 3000);
}
function confirmDialog(text, okLabel = "Löschen") {
  return new Promise((resolve) => {
    const d = $("confirm");
    $("confirm-text").textContent = text;
    $("confirm-ok").textContent = okLabel;
    d.addEventListener("close", () => resolve(d.returnValue === "ok"), { once: true });
    d.showModal();
  });
}
const fmtDate = (iso) => new Date(iso).toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit", year: "numeric" });
const fmtBytes = (n) => (n > 1048576 ? (n / 1048576).toFixed(1) + " MB" : Math.round(n / 1024) + " kB");

// ---------------- status ----------------
const POWER = { art: "Art Mode", tv: "TV-Betrieb", on: "Eingeschaltet", standby: "Standby", off: "Ausgeschaltet" };

async function loadStatus() {
  const el = $("tv-status");
  try {
    const s = await api("api/frame/status");
    state.status = s;
    const f = s.frame;
    let cls = "bad", text;
    if (!f.configured) text = "Nicht eingerichtet – IP-Adresse in der Add-on-Konfiguration eintragen";
    else if (!f.paired) { cls = "warn"; text = "Noch nicht gekoppelt – unter Einstellungen koppeln"; }
    else if (!f.reachable) text = f.error || "Nicht erreichbar";
    else {
      cls = f.error ? "warn" : "ok";
      text = "Verbunden · " + (f.art_mode === true ? "Art Mode" : f.art_mode === false ? "TV-Betrieb" : POWER[f.power_state] || f.power_state);
      if (f.error) text += " · " + f.error;
    }
    el.className = "status " + cls;
    el.lastElementChild.textContent = text;
    const am = $("artmode");
    am.disabled = !(f.reachable && f.paired && f.art_mode !== null);
    am.checked = f.art_mode === true;

    state.currentContentId = f.current?.content_id || null;
    const local = s.current_local_image;
    const nf = $("now-frame");
    if (local) {
      nf.innerHTML = `<img alt="" src="api/images/${local.id}/thumbnail">`;
      $("now-title").textContent = local.title;
    } else if (state.currentContentId) {
      nf.innerHTML = `<img alt="" src="api/frame/remote-images/${encodeURIComponent(state.currentContentId)}/thumbnail" onerror="this.remove()">`;
      $("now-title").textContent = "Bild " + state.currentContentId;
    } else {
      nf.innerHTML = "<span>Kein Bild</span>";
      $("now-title").textContent = "";
    }
    if (s.home_assistant?.name) $("tv-name").textContent = s.home_assistant.name;
    markOnTv();
  } catch (e) {
    el.className = "status bad";
    el.lastElementChild.textContent = e.message;
  }
}

$("artmode").addEventListener("change", async (ev) => {
  const on = ev.target.checked;
  ev.target.disabled = true;
  try {
    await api("api/frame/artmode", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ on }) });
    toast(on ? "Art Mode eingeschaltet" : "Art Mode ausgeschaltet");
  } catch (e) { ev.target.checked = !on; toast(e.message, true); }
  ev.target.disabled = false;
  setTimeout(loadStatus, 1500);
});

$("btn-random").addEventListener("click", async (ev) => {
  const b = ev.currentTarget;
  b.disabled = true;
  try {
    const res = await api("api/frame/random", { method: "POST" });
    toast(`„${res.title}“ wird angezeigt`);
    await refreshAll();
  } catch (e) { toast(e.message, true); }
  b.disabled = false;
});

// ---------------- local gallery ----------------
async function loadImages() {
  state.images = await api("api/images");
  $("count-local").textContent = state.images.length ? state.images.length : "";
  renderLocal();
}
function renderLocal() {
  const grid = $("panel-local");
  grid.replaceChildren();
  if (!state.images.length) {
    grid.innerHTML = '<div class="empty"><strong>Noch keine Bilder gespeichert.</strong>Lade oben dein erstes Bild hoch.</div>';
    return;
  }
  const tpl = $("tpl-card");
  for (const img of state.images) {
    const card = tpl.content.firstElementChild.cloneNode(true);
    card.dataset.id = img.id;
    card.dataset.contentId = img.tv_content_id || "";
    const pic = card.querySelector("img");
    pic.src = img.thumbnail_url;
    pic.alt = img.title;
    card.querySelector(".label-title").textContent = img.title;
    card.querySelector(".label-meta").textContent =
      `${fmtDate(img.created_at)} · Original ${img.source_width}×${img.source_height} · ${img.fit_mode === "crop" ? "zugeschnitten" : "eingepasst"}`;
    card.querySelector(".show").addEventListener("click", (ev) => showLocal(img, ev.currentTarget));
    card.querySelector(".del").addEventListener("click", () => deleteLocal(img));
    grid.append(card);
  }
  markOnTv();
}
function markOnTv() {
  document.querySelectorAll("#panel-local .card, #panel-tv .card").forEach((c) => {
    const on = !!state.currentContentId && c.dataset.contentId === state.currentContentId;
    c.classList.toggle("on-tv", on);
    const b = c.querySelector(".show");
    if (b) { b.textContent = on ? "Wird angezeigt" : "Auf TV anzeigen"; b.disabled = on; }
  });
}
async function showLocal(img, btn) {
  btn.disabled = true;
  btn.textContent = img.tv_content_id ? "Wird gewählt …" : "Wird übertragen …";
  try {
    await api(`api/images/${img.id}/display`, { method: "POST" });
    toast(`„${img.title}“ wird angezeigt`);
    await refreshAll();
  } catch (e) { toast(e.message, true); btn.textContent = "Auf TV anzeigen"; }
  btn.disabled = false;
}
async function deleteLocal(img) {
  const ok = await confirmDialog(`„${img.title}“ aus der Galerie löschen? Ein bereits auf den Fernseher übertragenes Bild bleibt dort erhalten.`);
  if (!ok) return;
  try {
    await api(`api/images/${img.id}`, { method: "DELETE" });
    toast("Bild gelöscht");
    await loadImages();
  } catch (e) { toast(e.message, true); }
}

// ---------------- TV gallery ----------------
async function loadTv() {
  const grid = $("panel-tv");
  grid.innerHTML = '<div class="empty">Bilder werden vom Fernseher geladen …</div>';
  try {
    const res = await api("api/frame/remote-images");
    state.tvLoaded = true;
    grid.replaceChildren();
    if (!res.supported) {
      grid.innerHTML = `<div class="empty"><strong>Der Fernseher liefert gerade keine Bildliste.</strong></div>`;
      grid.firstElementChild.append(res.message || "Prüfe unter Einstellungen, ob er gekoppelt und eingeschaltet ist.");
      return;
    }
    if (!res.images.length) {
      grid.innerHTML = '<div class="empty"><strong>Auf dem Fernseher sind keine eigenen Bilder gespeichert.</strong>Bilder aus dem Art Store werden hier nicht angezeigt.</div>';
      return;
    }
    const tpl = $("tpl-card");
    for (const it of res.images) {
      const card = tpl.content.firstElementChild.cloneNode(true);
      card.dataset.contentId = it.content_id;
      const pic = card.querySelector("img");
      pic.src = it.thumbnail_url;
      pic.alt = it.local_title || it.content_id;
      pic.addEventListener("error", () => {
        if (it.local_id) pic.src = `api/images/${it.local_id}/thumbnail`;
        else { pic.alt = ""; pic.removeAttribute("src"); }
      }, { once: true });
      card.querySelector(".label-title").textContent = it.local_title || it.content_id;
      const meta = [it.local_id ? "Aus The Frame Bridge" : "Nur auf dem Fernseher"];
      if (it.width && it.height) meta.push(`${it.width}×${it.height}`);
      if (it.image_date) meta.push(it.image_date.split(" ")[0].replaceAll(":", "-"));
      card.querySelector(".label-meta").textContent = meta.join(" · ");
      card.querySelector(".del").remove(); // TV images are never deleted from here
      card.querySelector(".show").addEventListener("click", async (ev) => {
        const b = ev.currentTarget;
        b.disabled = true;
        try {
          await api(`api/frame/remote-images/${encodeURIComponent(it.content_id)}/display`, { method: "POST" });
          toast("Bild wird angezeigt");
          await loadStatus();
        } catch (e) { toast(e.message, true); }
        b.disabled = false;
      });
      grid.append(card);
    }
    markOnTv();
  } catch (e) {
    grid.innerHTML = '<div class="empty"><strong>Bildliste nicht abrufbar.</strong></div>';
    grid.firstElementChild.append(e.message);
  }
}

document.querySelectorAll(".tabs button").forEach((b) =>
  b.addEventListener("click", () => {
    state.tab = b.dataset.tab;
    document.querySelectorAll(".tabs button").forEach((x) => x.setAttribute("aria-selected", String(x === b)));
    $("panel-local").hidden = state.tab !== "local";
    $("panel-tv").hidden = state.tab !== "tv";
    if (state.tab === "tv") loadTv();
  })
);

// ---------------- upload ----------------
const drop = $("drop");
["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", (e) => pickFile(e.dataTransfer.files[0]));
$("file").addEventListener("change", (e) => pickFile(e.target.files[0]));

let previewUrl;
function pickFile(f) {
  if (!f) return;
  if (!["image/jpeg", "image/png"].includes(f.type)) { toast("Bitte eine JPG- oder PNG-Datei wählen.", true); return; }
  const maxMb = state.settings?.max_upload_mb || 25;
  if (f.size > maxMb * 1048576) { toast(`Die Datei ist größer als ${maxMb} MB.`, true); return; }
  state.file = f;
  if (previewUrl) URL.revokeObjectURL(previewUrl);
  previewUrl = URL.createObjectURL(f);
  $("prep-img").src = previewUrl;
  $("prep-bg").style.backgroundImage = `url("${previewUrl}")`;
  $("title").value = f.name.replace(/\.[^.]+$/, "").replace(/[_-]+/g, " ").trim();
  $("upload-msg").textContent = "";
  $("upload-msg").className = "msg";
  drop.hidden = true;
  $("prep").hidden = false;
  $("title").focus();
}
$("mode").addEventListener("change", () => {
  const mode = document.querySelector('input[name="mode"]:checked').value;
  $("prep-preview").dataset.mode = mode;
  $("mode-hint").textContent = mode === "fit"
    ? "Das ganze Bild bleibt sichtbar, freie Flächen werden unscharf aufgefüllt."
    : "Das Bild füllt den Bildschirm, die Ränder werden abgeschnitten.";
});
function resetUpload() {
  state.file = null;
  $("file").value = "";
  $("prep").hidden = true;
  $("progress").hidden = true;
  $("bar").style.width = "0";
  drop.hidden = false;
}
$("btn-cancel").addEventListener("click", resetUpload);

$("prep").addEventListener("submit", (ev) => {
  ev.preventDefault();
  if (!state.file) return;
  const fd = new FormData();
  fd.append("file", state.file);
  fd.append("title", $("title").value);
  fd.append("mode", document.querySelector('input[name="mode"]:checked').value);
  fd.append("display", $("display-now").checked ? "true" : "false");
  const msg = $("upload-msg");
  const btn = $("btn-upload");
  btn.disabled = true;
  $("progress").hidden = false;
  msg.className = "msg";
  msg.textContent = "Wird hochgeladen …";

  const xhr = new XMLHttpRequest();
  xhr.open("POST", "api/images");
  xhr.upload.onprogress = (e) => {
    if (!e.lengthComputable) return;
    const p = Math.round((e.loaded / e.total) * 100);
    $("bar").style.width = p + "%";
    if (p === 100) msg.textContent = $("display-now").checked ? "Wird vorbereitet und an den Fernseher gesendet …" : "Wird vorbereitet …";
  };
  xhr.onload = async () => {
    btn.disabled = false;
    let body = null;
    try { body = JSON.parse(xhr.responseText); } catch { /* not json */ }
    if (xhr.status !== 201) {
      msg.className = "msg bad";
      msg.textContent = errorText(body, xhr.status);
      return;
    }
    resetUpload();
    if (body.display_error) toast(`Gespeichert, aber nicht angezeigt: ${body.display_error}`, true);
    else toast(body.displayed ? `„${body.title}“ wird angezeigt` : `„${body.title}“ gespeichert`);
    await refreshAll();
  };
  xhr.onerror = () => { btn.disabled = false; msg.className = "msg bad"; msg.textContent = "Verbindung unterbrochen. Bitte erneut versuchen."; };
  xhr.send(fd);
});

// ---------------- settings ----------------
let inSettings = false;
$("btn-settings").addEventListener("click", () => {
  inSettings = !inSettings;
  $("view-main").hidden = inSettings;
  $("view-settings").hidden = !inSettings;
  $("settings-label").textContent = inSettings ? "Zur Galerie" : "Einstellungen";
  if (inSettings) loadSettings();
});

function kv(el, rows) {
  el.replaceChildren();
  for (const [k, v] of rows) {
    const dt = document.createElement("dt"); dt.textContent = k;
    const dd = document.createElement("dd"); dd.textContent = v;
    el.append(dt, dd);
  }
}
async function loadSettings() {
  try {
    const s = await api("api/settings");
    state.settings = s;
    kv($("set-tv"), [
      ["IP-Adresse", s.tv_host || "nicht eingestellt"],
      ["Media Player", s.media_player_entity || "nicht eingestellt"],
      ["Kopplung", s.paired ? "gekoppelt (eigenes Token)" : "nicht gekoppelt"],
      ["Passepartout", s.matte],
      ["Add-on-Version", s.version],
    ]);
    kv($("set-storage"), [
      ["Gespeicherte Bilder", String(s.image_count)],
      ["Belegter Speicher", fmtBytes(s.storage_bytes)],
      ["Maximale Upload-Größe", s.max_upload_mb + " MB"],
    ]);
    kv($("set-api"), [
      ["Status", s.api_enabled ? `aktiv auf Port ${s.api_port} (Container)` : "ausgeschaltet"],
      ["Spezifikation", s.api_enabled ? "/openapi.json und /docs auf dem API-Port" : "–"],
    ]);
    const dev = $("set-devices");
    dev.replaceChildren();
    if (s.discovery_error) dev.textContent = "Abfrage fehlgeschlagen: " + s.discovery_error;
    else if (!s.devices.length) dev.textContent = "Keine Geräte der Samsung-Integration gefunden.";
    for (const d of s.devices) {
      const box = document.createElement("div");
      box.className = "device";
      const name = document.createElement("strong");
      name.textContent = d.name || "Unbenanntes Gerät";
      const ul = document.createElement("ul");
      for (const e of d.entities) {
        const li = document.createElement("li");
        const c = document.createElement("code"); c.textContent = e.entity_id;
        li.append(c, ` – ${e.state}`);
        if (e.entity_id === s.media_player_entity) li.append(" (ausgewählt)");
        ul.append(li);
      }
      box.append(name, ul);
      dev.append(box);
    }
  } catch (e) { toast(e.message, true); }
}

$("btn-pair").addEventListener("click", async (ev) => {
  const b = ev.currentTarget, msg = $("pair-msg");
  b.disabled = true;
  msg.className = "msg";
  msg.textContent = "Bitte jetzt die Abfrage auf dem Fernseher bestätigen …";
  try {
    const r = await api("api/settings/pair", { method: "POST" });
    msg.className = r.paired ? "msg ok" : "msg bad";
    msg.textContent = r.paired ? "Gekoppelt." : "Der Fernseher hat kein Token geliefert. Bitte erneut versuchen.";
    loadSettings(); loadStatus();
  } catch (e) { msg.className = "msg bad"; msg.textContent = e.message; }
  b.disabled = false;
});

$("btn-diag").addEventListener("click", async (ev) => {
  const b = ev.currentTarget, out = $("diag");
  b.disabled = true;
  out.hidden = false;
  out.textContent = "Teste …";
  const report = {};
  try { report.status = (await api("api/frame/status")).frame; } catch (e) { report.status = "Fehler: " + e.message; }
  try {
    const r = await api("api/frame/remote-images");
    report.bildliste = r.supported ? `${r.images.length} eigene Bilder auf dem Fernseher` : "nicht verfügbar: " + r.message;
  } catch (e) { report.bildliste = "Fehler: " + e.message; }
  out.textContent = JSON.stringify(report, null, 2);
  b.disabled = false;
});

$("btn-token").addEventListener("click", async () => {
  try { const r = await api("api/settings/api-token"); $("token").textContent = r.token; $("token-box").hidden = false; }
  catch (e) { toast(e.message, true); }
});
$("btn-rotate").addEventListener("click", async () => {
  const ok = await confirmDialog("Neues Token erzeugen? Das alte Token funktioniert danach nicht mehr.", "Neues Token");
  if (!ok) return;
  try { const r = await api("api/settings/api-token/rotate", { method: "POST" }); $("token").textContent = r.token; $("token-box").hidden = false; toast("Neues Token erzeugt"); }
  catch (e) { toast(e.message, true); }
});
$("btn-copy").addEventListener("click", async () => {
  try { await navigator.clipboard.writeText($("token").textContent); toast("Token kopiert"); }
  catch { toast("Kopieren nicht möglich, bitte manuell markieren.", true); }
});

// ---------------- boot ----------------
async function refreshAll() {
  await Promise.all([loadImages().catch((e) => toast(e.message, true)), loadStatus()]);
  if (state.tab === "tv") loadTv();
}
api("api/settings").then((s) => (state.settings = s)).catch(() => {});
refreshAll();
setInterval(() => { if (!document.hidden && !inSettings) loadStatus(); }, 30000);

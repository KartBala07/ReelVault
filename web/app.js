// ReelVault UI
const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];

let STATE = { accounts: [], deps: {}, session: {} };
let CURRENT = null;      // account id
let POSTS = [];
let VIEW = localStorage.getItem("reelvault.view") || "grid";
let ES = null;
let POSTED = {};                 // file -> {status, when, posted_as}
const SELECT = new Set();        // files picked for posting

// ---------------------------------------------------------------- helpers
const fmtBytes = (b) => {
  if (!b) return "0 MB";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0, n = b;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(n < 10 && i > 0 ? 1 : 0)} ${u[i]}`;
};
const fmtNum = (n) => (n || 0).toLocaleString();
const fmtShort = (n) => {
  n = n || 0;
  if (n >= 1e9) return (n / 1e9).toFixed(1).replace(/\.0$/, "") + "B";
  if (n >= 1e6) return (n / 1e6).toFixed(1).replace(/\.0$/, "") + "M";
  if (n >= 1e3) return (n / 1e3).toFixed(1).replace(/\.0$/, "") + "k";
  return String(n);
};
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function toast(msg, isErr) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast on" + (isErr ? " err" : "");
  clearTimeout(t._h);
  t._h = setTimeout(() => (t.className = "toast"), 3800);
}

async function api(path, opts) {
  const r = await fetch(path, Object.assign({ headers: { "Content-Type": "application/json" } }, opts || {}));
  const ct = r.headers.get("content-type") || "";
  const body = ct.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw Object.assign(new Error((body && body.error) || r.statusText), { body });
  return body;
}

// ---------------------------------------------------------------- state
async function load() {
  STATE = await api("/api/state");
  if (CURRENT && !STATE.accounts.find((a) => a.id === CURRENT)) CURRENT = null;
  if (!CURRENT && STATE.accounts.length) CURRENT = STATE.accounts[0].id;
  renderSession();
  renderAccounts();
  renderMain();
}

function renderSession() {
  const s = STATE.session || {};
  const gd = STATE.deps.gallery_dl || {};
  const dot = $("#sessionDot");
  const txt = $("#sessionText");
  const hint = $("#connHint");

  if (!s.logged_in) {
    dot.className = "dot off";
    txt.textContent = "Not connected";
    hint.className = "conn bad";
    hint.innerHTML = `<b>Instagram not connected.</b> <a href="#" id="importLink">Import my browser session</a>`;
    const a = $("#importLink");
    if (a) a.onclick = importSession;
    $("#sessionPill").onclick = importSession;
  } else if (s.blocked) {
    dot.className = "dot off";
    txt.textContent = "Session blocked";
    hint.className = "conn bad";
    hint.innerHTML = `<b>Instagram is blocking this session.</b> ${esc(s.reason || "security checkpoint")}
      — open instagram.com in Firefox, clear it, then <a href="#" id="importLink">re-import</a>.`;
    const a = $("#importLink");
    if (a) a.onclick = importSession;
    $("#sessionPill").onclick = importSession;
  } else if (!gd.ok) {
    dot.className = "dot";
    txt.textContent = "gallery-dl missing";
    hint.className = "conn bad";
    hint.innerHTML = `<b>gallery-dl missing.</b> Run <code>pip3 install --user gallery-dl</code>`;
  } else {
    dot.className = "dot";
    txt.textContent = "Instagram connected";
    hint.className = "conn";
    hint.innerHTML = `<b>Session ok</b> · gallery-dl ${esc(gd.version || "")}`;
  }
}

// ---------------------------------------------------------------- accounts
function renderAccounts() {
  const box = $("#accounts");
  box.innerHTML = "";
  $("#accCount").textContent = STATE.accounts.length;

  for (const a of STATE.accounts) {
    const el = document.createElement("div");
    el.className = "acc-card" + (a.id === CURRENT ? " active" : "");
    el.innerHTML = `
      <div class="ava">${esc((a.username || "?")[0])}</div>
      <div>
        <div class="acc-name">@${esc(a.username)}</div>
        <div class="acc-sub">${fmtNum(a.saved)} saved${a.total_posts ? " / " + fmtNum(a.total_posts) : ""}</div>
      </div>
      ${a.job ? '<span class="busy"></span>' : ""}`;
    el.onclick = () => { CURRENT = a.id; SELECT.clear(); renderAccounts(); renderMain(); };
    box.appendChild(el);
  }

  const add = document.createElement("div");
  add.className = "add-card";
  add.innerHTML = `<input id="newAccount" placeholder="@username" autocomplete="off" spellcheck="false">
    <button id="addBtn">Add</button>`;
  box.appendChild(add);
  $("#addBtn").onclick = addAccount;
  $("#newAccount").addEventListener("keydown", (e) => { if (e.key === "Enter") addAccount(); });
}

function account() {
  return STATE.accounts.find((a) => a.id === CURRENT) || null;
}

// ---------------------------------------------------------------- hero + stats
function renderHero() {
  const a = account();
  $("#heroSaved").textContent = a ? fmtNum(a.saved) : "0";
  $("#heroLikes").textContent = a ? fmtShort(POSTS.reduce((s, p) => s + (p.likes || 0), 0)) : "0";
}

function renderStats() {
  const a = account();
  const box = $("#stats");
  if (!a) { box.innerHTML = ""; return; }
  const likes = POSTS.reduce((s, p) => s + (p.likes || 0), 0);
  const tags = new Set();
  POSTS.forEach((p) => (p.hashtags || []).forEach((h) => tags.add(h.toLowerCase())));
  const dates = POSTS.map((p) => p.date).filter(Boolean).sort();
  const span = dates.length
    ? `${dates[0].slice(0, 10)} → ${dates[dates.length - 1].slice(0, 10)}`
    : "no posts yet";

  box.innerHTML = `
    <div class="stat"><div class="lbl dim">Saved reels</div><div class="v">${fmtNum(a.saved)}</div><div class="s">${fmtBytes(a.size_bytes)} on disk</div></div>
    <div class="stat"><div class="lbl dim">Total likes</div><div class="v">${fmtShort(likes)}</div><div class="s">across saved reels</div></div>
    <div class="stat"><div class="lbl dim">Hashtags</div><div class="v">${fmtNum(tags.size)}</div><div class="s">unique tags</div></div>
    <div class="stat"><div class="lbl dim">Date range</div><div class="v" style="font-size:15px;line-height:1.35;letter-spacing:-.01em">${esc(span)}</div><div class="s">oldest → newest</div></div>`;
}

// ---------------------------------------------------------------- main
function renderMain() {
  const a = account();
  const dl = $("#downloadCard");

  if (!a) {
    $("#accTitle").textContent = "Library";
    $("#accChip").style.display = "none";
    dl.style.display = "none";
    $("#postCard").style.display = "none";
    $("#progress").classList.remove("on");
    POSTS = [];
    renderHero();
    renderStats();
    $("#lib").innerHTML = `<div class="empty">
        <h3>No account selected yet</h3>
        <p>Add a handle in the Accounts row above. ReelVault reuses your browser's
           Instagram session and starts building a library for it.</p>
      </div>`;
    return;
  }

  dl.style.display = "";
  $("#accTitle").textContent = "@" + a.username;
  const chip = $("#accChip");
  chip.style.display = "";
  chip.textContent = `${fmtNum(a.saved)} saved · ${fmtBytes(a.size_bytes)}`;
  $("#badgeSaved").textContent = `${fmtNum(a.saved)} saved`;
  $("#badgeQuality").textContent = a.quality || "720p";
  $("#dlTitle").textContent = `Download new reels from @${a.username}`;
  $("#quality").value = a.quality || "720p";
  const off = !STATE.session.logged_in || !!STATE.session.blocked;
  $("#scrapeBtn").disabled = off;
  $("#postCard").style.display = "";
  $("#postAccount").textContent = a.username;
  initCaption();
  $$("#viewSeg button").forEach((b) => b.classList.toggle("on", b.dataset.view === VIEW));
  if (a.job) setRunning(a.job); else setRunning(null);
  loadPosts(a.id);
}

async function loadPosts(id) {
  const [res, posted] = await Promise.all([
    api(`/api/accounts/${id}/posts?limit=2000`),
    api(`/api/accounts/${id}/posted`).catch(() => ({ posted: {} })),
  ]);
  POSTS = res.items;
  POSTED = posted.posted || {};
  renderHero();
  renderStats();
  renderLibrary();
  renderPostCard();
}

function unpostedCount() {
  return POSTS.filter((p) => (POSTED[p.file] || {}).status !== "posted").length;
}

function renderPostCard() {
  const a = account();
  if (!a) return;
  $("#postAccount").textContent = a.username;
  $("#badgeUnposted").textContent = `${fmtNum(unpostedCount())} unposted`;
  $("#badgeSelected").textContent = SELECT.size ? `${SELECT.size} selected` : "none selected";
  $("#publishBtn").textContent = SELECT.size ? `Publish ${SELECT.size} →` : "Publish →";
  const off = !STATE.session.logged_in || !!STATE.session.blocked;
  $("#publishBtn").disabled = off;
  $("#dryBtn").disabled = off;
  $("#dryBtn").textContent = SELECT.size ? `Dry run ${SELECT.size}` : "Dry run";
}

// ---------------------------------------------------------------- library
function renderLibrary() {
  const lib = $("#lib");
  if (!POSTS.length) {
    lib.innerHTML = `<div class="empty">
        <h3>Nothing saved yet</h3>
        <p>Hit “Start download” above — the first batch lands here with its caption,
           hashtags and like count.</p>
      </div>`;
    return;
  }
  if (VIEW === "table") return renderTable(lib);

  lib.innerHTML = "";
  const grid = document.createElement("div");
  grid.className = "grid";
  const user = encodeURIComponent(account().username);
  for (const p of POSTS) {
    const sc = encodeURIComponent(p.shortcode || "");
    const tagline = (p.hashtags || []).slice(0, 3).map((h) => "#" + h).join(" ");
    const tile = document.createElement("div");
    tile.className = "tile";
    const st = (POSTED[p.file] || {}).status;
    tile.innerHTML = `
      <img class="thumb" loading="lazy" src="/thumb/${user}/${sc}.jpg" alt=""
           onerror="this.classList.add('noimg')">
      <label class="pick" title="Select for posting">
        <input type="checkbox" ${SELECT.has(p.file) ? "checked" : ""}>
      </label>
      ${st === "posted" ? '<div class="posted-chip">✓ posted</div>' : ""}
      <div class="play"><div class="playbtn">▶</div></div>
      <div class="body">
        <div class="t" title="${esc(p.caption || p.title)}">${esc(p.title)}</div>
        ${tagline ? `<div class="tags">${esc(tagline)}${p.hashtags.length > 3 ? " +" + (p.hashtags.length - 3) : ""}</div>` : ""}
        <div class="m"><span>${fmtNum(p.likes)} likes</span><span>${fmtBytes(p.bytes)}</span></div>
      </div>`;
    const cb = tile.querySelector(".pick input");
    cb.onclick = (e) => e.stopPropagation();
    cb.onchange = () => {
      if (cb.checked) SELECT.add(p.file); else SELECT.delete(p.file);
      renderPostCard();
    };
    tile.onclick = () => play(p);
    grid.appendChild(tile);
  }
  lib.appendChild(grid);
}

function renderTable(lib) {
  const user = encodeURIComponent(account().username);
  const rows = POSTS.map((p) => {
    const sc = encodeURIComponent(p.shortcode || "");
    const tags = (p.hashtags || []).map((h) => "#" + h).join(" ");
    const cap = (p.caption || "").replace(/\s+/g, " ").trim() || (p.audio ? `♪ ${p.audio}` : "");
    return `<tr data-sc="${sc}">
      <td class="th"><img class="mini" loading="lazy" src="/thumb/${user}/${sc}.jpg" alt=""
           onerror="this.classList.add('noimg')"></td>
      <td class="tt">
        <div class="tl" title="${esc(cap || p.title)}">${esc(p.title)}</div>
        ${cap && cap !== p.title ? `<div class="cp">${esc(cap)}</div>` : ""}
      </td>
      <td class="num">${(POSTED[p.file] || {}).status === "posted"
        ? '<span class="posted-chip inline">✓ posted</span>' : (p.date || "").slice(0, 10)}</td>
      <td class="num">${fmtNum(p.likes)}</td>
      <td class="tags">${tags ? esc(tags) : '<span style="color:#B8B0A0">—</span>'}</td>
    </tr>`;
  }).join("");

  lib.innerHTML = `<div class="tablewrap"><table class="lib">
    <colgroup>
      <col style="width:48px"><col><col style="width:96px"><col style="width:86px"><col style="width:210px">
    </colgroup>
    <thead><tr><th></th><th>Title / caption</th><th>Date</th><th>Likes</th><th>Hashtags</th></tr></thead>
    <tbody>${rows}</tbody></table></div>`;

  lib.querySelectorAll("tr[data-sc]").forEach((tr) => {
    const p = POSTS.find((x) => x.shortcode === decodeURIComponent(tr.dataset.sc));
    tr.onclick = () => play(p);
  });
}

// ---------------------------------------------------------------- player
function play(p) {
  const a = account();
  const url = `/media/${encodeURIComponent(a.username)}/${encodeURIComponent(p.file)}`;
  const player = $("#player");
  player.src = url;
  player.poster = `/thumb/${encodeURIComponent(a.username)}/${encodeURIComponent(p.shortcode || "")}.jpg`;
  $("#mFile").href = url;
  $("#mFile").setAttribute("download", p.file);
  $("#mTitle").textContent = p.title;
  $("#mMeta").textContent = `${(p.date || "").slice(0, 10)} · ${fmtNum(p.likes)} likes · ${fmtBytes(p.bytes)}`;
  $("#mTags").textContent = (p.hashtags || []).length
    ? (p.hashtags || []).map((h) => "#" + h).join("  ")
    : "";
  const desc = $("#mDesc");
  if (p.caption) {
    desc.textContent = p.caption;
    desc.className = "desc";
  } else {
    desc.textContent = p.audio
      ? `No caption on this post · audio: ${p.audio}`
      : "No caption on this post — Instagram returns an empty description for it.";
    desc.className = "desc none";
  }
  const link = $("#mLink");
  if (p.url) { link.href = p.url; link.style.display = ""; } else link.style.display = "none";
  $("#modal").classList.add("on");
  $("#player").play().catch(() => {});
}
function closeModal() {
  $("#modal").classList.remove("on");
  $("#player").pause();
  $("#player").src = "";
}
$("#mClose").onclick = closeModal;
$("#modal").addEventListener("click", (e) => { if (e.target === $("#modal")) closeModal(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });

// ---------------------------------------------------------------- actions
async function importSession() {
  toast("Looking for a logged-in Instagram session…");
  try {
    const r = await api("/api/session/import", { method: "POST", body: "{}" });
    toast(r.profile_refreshed
      ? `Session imported from ${r.browser}, Firefox profile refreshed ✓`
      : `Session imported from ${r.browser} ✓${r.note ? " — " + r.note : ""}`);
    await load();
  } catch (e) { toast(e.message, true); }
}

async function addAccount() {
  const input = $("#newAccount");
  if (!input) return;
  const source = input.value.trim();
  if (!source) return;
  const btn = $("#addBtn");
  btn.disabled = true; btn.textContent = "…";
  try {
    const r = await api("/api/accounts", { method: "POST", body: JSON.stringify({ username: source }) });
    input.value = "";
    toast(`Added @${r.account.username}`);
    CURRENT = r.account.id;
    await load();
  } catch (e) {
    toast(e.message === "not_connected" ? "Connect Instagram first" : e.message, true);
  } finally {
    btn.disabled = false; btn.textContent = "Add";
  }
}

$("#scrapeBtn").onclick = async () => {
  const a = account();
  if (!a) return;
  const limit = parseInt($("#limit").value || "0", 10);
  const quality = $("#quality").value;
  try {
    const r = await api(`/api/accounts/${a.id}/scrape`, {
      method: "POST", body: JSON.stringify({ limit, quality }),
    });
    subscribe(r.job.id);
    setRunning(r.job);
  } catch (e) { toast(e.message, true); }
};

$("#cancelBtn").onclick = async () => {
  const a = account();
  if (!a || !a.job) return;
  try { await api(`/api/jobs/${a.job.id}/cancel`, { method: "POST", body: "{}" }); }
  catch (e) { toast(e.message, true); }
};

$("#revealBtn").onclick = async () => {
  const a = account();
  if (!a) return toast("Add an account first", true);
  try { await api("/api/reveal", { method: "POST", body: JSON.stringify({ account_id: a.id }) }); }
  catch (e) { toast(e.message, true); }
};

$("#csvBtn").onclick = () => {
  const a = account();
  if (!a) return toast("Add an account first", true);
  window.location = `/api/accounts/${a.id}/index.csv`;
};

$("#removeBtn").onclick = async () => {
  const a = account();
  if (!a) return;
  if (!confirm(`Remove @${a.username} from ReelVault?\n\nFiles stay on disk — this only removes it from the list.`)) return;
  try {
    await api(`/api/accounts/${a.id}`, { method: "DELETE", body: JSON.stringify({ delete_files: false }) });
    CURRENT = null;
    await load();
    toast("Removed");
  } catch (e) { toast(e.message, true); }
};

$("#logToggle").onclick = () => {
  const l = $("#log");
  l.classList.toggle("on");
  $("#logToggle").textContent = l.classList.contains("on") ? "Hide log" : "Show log";
};

$$("#viewSeg button").forEach((b) => {
  b.onclick = () => {
    VIEW = b.dataset.view;
    localStorage.setItem("reelvault.view", VIEW);
    $$("#viewSeg button").forEach((x) => x.classList.toggle("on", x.dataset.view === VIEW));
    renderLibrary();
  };
});

// ---------------------------------------------------------------- posting
const CAP_KEY = "reelvault.caption";
let CAP = {};
try { CAP = JSON.parse(localStorage.getItem(CAP_KEY) || "{}"); } catch (e) { CAP = {}; }

const capFields = () => ({ template: $("#capTemplate").value, tags: $("#capTags").value });

function initCaption() {
  const def = (STATE.caption && STATE.caption.template) || "{caption}\n\n{credit} {hashtags}";
  if ($("#capTemplate").dataset.ready) return;
  $("#capTemplate").value = CAP.template != null ? CAP.template : def;
  $("#capTags").value = CAP.tags != null ? CAP.tags : "#reels #reelsinstagram";
  const save = () => {
    CAP = capFields();
    localStorage.setItem(CAP_KEY, JSON.stringify(CAP));
  };
  $("#capTemplate").addEventListener("input", save);
  $("#capTags").addEventListener("input", save);
  $("#capTemplate").dataset.ready = "1";
}

async function previewCaptions() {
  const a = account();
  if (!a) return;
  const files = SELECT.size ? [...SELECT] : null;
  try {
    const r = await api(`/api/accounts/${a.id}/preview`, {
      method: "POST",
      body: JSON.stringify(Object.assign({ files, limit: 3 }, capFields())),
    });
    const box = $("#capPreview");
    if (!r.items.length) {
      box.hidden = true;
      return toast("Nothing to preview", true);
    }
    box.hidden = false;
    box.innerHTML = r.items.map((it) => `
      <div class="row"><b>${esc(it.file)}</b>${esc(it.caption || "(empty caption)")}
        <div class="cap-hint">${it.info.chars} chars · ${it.info.tags.length} tag(s)${it.info.from_caption ? "" : " · original had no caption"}</div>
      </div>`).join("");
    $("#capHint").textContent = `${fmtNum(r.unposted)} unposted in this library`;
    toast(`Previewed ${r.items.length} caption(s)`);
  } catch (e) {
    toast(e.message, true);
  }
}

async function startPost(publish) {
  const a = account();
  if (!a) return;
  const files = SELECT.size ? [...SELECT] : null;
  const count = files ? files.length : unpostedCount();
  if (!count) return toast("Nothing left to post", true);
  if (publish && !confirm(`Publish ${count} reel(s)?\n\nThis uploads publicly to whatever account the Firefox session is logged into. Use Dry run first if you are unsure.`)) return;
  try {
    const r = await api(`/api/accounts/${a.id}/post`, {
      method: "POST",
      body: JSON.stringify(Object.assign({ files, publish }, capFields())),
    });
    subscribe(r.job.id);
    setRunning(r.job);
    toast(publish ? `Publishing ${r.queued} reel(s)…` : `Dry run on ${r.queued} reel(s)…`);
  } catch (e) {
    toast(e.message, true);
  }
}
$("#previewBtn").onclick = previewCaptions;
$("#dryBtn").onclick = () => startPost(false);
$("#publishBtn").onclick = () => startPost(true);

// ---------------------------------------------------------------- progress
function setRunning(job) {
  const running = !!(job && (job.status === "running" || job.status === undefined));
  const isPost = job && job.quality === "post";
  $("#scrapeBtn").style.display = running ? "none" : "";
  $("#cancelBtn").style.display = running ? "" : "none";
  $("#dryBtn").disabled = running;
  $("#publishBtn").disabled = running;
  $("#progress").classList.toggle("on", !!job);
  if (!job) return;

  $("#pCount").textContent = fmtNum(job.downloaded || 0);
  $("#pTotal").textContent = job.expected ? `of ${fmtNum(job.expected)}` : "";
  $("#pStatus").textContent = job.status;
  const verb = isPost ? "Posting" : "Downloading";
  $("#pTitle").textContent = job.status === "running"
    ? `${verb} @${job.account}…`
    : `${job.status === "cancelled" ? "Stopped" : job.status === "done" ? "Finished" : "Ended"} — ${fmtNum(job.downloaded || 0)} ${isPost ? "posted" : "new"}`;
  const pbar = $("#pbar");
  const hasTotal = !!job.expected;
  pbar.classList.toggle("indet", !hasTotal && job.status === "running");
  $("#barFill").style.width = hasTotal
    ? Math.max(2, Math.min(100, ((job.downloaded || 0) / job.expected) * 100)) + "%"
    : "35%";
}

function subscribe(jid) {
  if (ES) ES.close();
  const log = $("#log");
  log.innerHTML = "";
  ES = new EventSource(`/api/jobs/${jid}/events`);
  ES.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "log") {
      const d = document.createElement("div");
      d.className = msg.entry.kind || "";
      d.textContent = msg.entry.text;
      log.appendChild(d);
      log.scrollTop = log.scrollHeight;
      if (log.children.length > 400) log.removeChild(log.firstChild);
    } else if (msg.type === "state") {
      setRunning(msg.job);
    } else if (msg.type === "end") {
      ES.close(); ES = null;
      const j = msg.job;
      const isPost = j.quality === "post";
      if (j.blocked) {
        toast(`Posting blocked: ${j.blocked}`, true);
      } else {
        toast(j.status === "done"
          ? (isPost ? `Finished — ${j.downloaded} posted ✓` : `Finished — ${j.downloaded} new reel(s) ✓`)
          : j.status === "cancelled" ? "Stopped"
          : `Ended: ${j.status} (${j.downloaded} done)`, j.status !== "done");
      }
      setTimeout(load, 700);
    }
  };
  ES.onerror = () => { if (ES) { ES.close(); ES = null; } };
}

// ---------------------------------------------------------------- boot
function applyTheme(t) {
  document.documentElement.setAttribute("data-theme", t);
  try { localStorage.setItem("reelvault.theme", t); } catch (e) {}
  const btn = $("#themeBtn");
  const icon = $("#themeIcon");
  const label = $("#themeLabel");
  if (icon) icon.textContent = t === "dark" ? "☀" : "☾";
  if (label) label.textContent = t === "dark" ? "Light" : "Dark";
  if (btn) btn.title = t === "dark" ? "Switch to light mode" : "Switch to dark mode";
}
$("#themeBtn").onclick = () => {
  const cur = document.documentElement.getAttribute("data-theme");
  applyTheme(cur === "dark" ? "light" : "dark");
};
applyTheme(document.documentElement.getAttribute("data-theme") || "light");

load().catch((e) => toast(e.message, true));
setInterval(() => { if (!ES) load().catch(() => {}); }, 20000);

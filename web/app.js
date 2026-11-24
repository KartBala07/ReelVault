// ReelVault UI
const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];

let STATE = { accounts: [], deps: {}, session: {} };
let CURRENT = null;      // account id
let POSTS = [];
let VIEW = localStorage.getItem("reelvault.view") || "grid";
let ES = null;

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
      <div class="ava">${esc(a.username[0])}</div>
      <div>
        <div class="acc-name">@${esc(a.username)}</div>
        <div class="acc-sub">${fmtNum(a.saved)} saved${a.total_posts ? " / " + fmtNum(a.total_posts) : ""}</div>
      </div>
      ${a.job ? '<span class="busy"></span>' : ""}`;
    el.onclick = () => { CURRENT = a.id; renderAccounts(); renderMain(); };
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
  $("#scrapeBtn").disabled = !STATE.session.logged_in;
  $$("#viewSeg button").forEach((b) => b.classList.toggle("on", b.dataset.view === VIEW));
  if (a.job) setRunning(a.job); else setRunning(null);
  loadPosts(a.id);
}

async function loadPosts(id) {
  const res = await api(`/api/accounts/${id}/posts?limit=2000`);
  POSTS = res.items;
  renderHero();
  renderStats();
  renderLibrary();
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
    tile.innerHTML = `
      <img class="thumb" loading="lazy" src="/thumb/${user}/${sc}.jpg" alt=""
           onerror="this.classList.add('noimg')">
      <div class="play"><div class="playbtn">▶</div></div>
      <div class="body">
        <div class="t" title="${esc(p.caption || p.title)}">${esc(p.title)}</div>
        ${tagline ? `<div class="tags">${esc(tagline)}${p.hashtags.length > 3 ? " +" + (p.hashtags.length - 3) : ""}</div>` : ""}
        <div class="m"><span>${fmtNum(p.likes)} likes</span><span>${fmtBytes(p.bytes)}</span></div>
      </div>`;
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
    const cap = (p.caption || "").replace(/\s+/g, " ").trim();
    return `<tr data-sc="${sc}">
      <td class="th"><img class="mini" loading="lazy" src="/thumb/${user}/${sc}.jpg" alt=""
           onerror="this.classList.add('noimg')"></td>
      <td class="tt">
        <div class="tl" title="${esc(cap || p.title)}">${esc(p.title)}</div>
        ${cap && cap !== p.title ? `<div class="cp">${esc(cap)}</div>` : ""}
      </td>
      <td class="num">${(p.date || "").slice(0, 10)}</td>
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
  $("#player").src = `/media/${encodeURIComponent(a.username)}/${encodeURIComponent(p.file)}`;
  $("#mTitle").textContent = p.title;
  $("#mMeta").textContent = `${(p.date || "").slice(0, 10)} · ${fmtNum(p.likes)} likes · ${fmtBytes(p.bytes)}`;
  $("#mTags").textContent = (p.hashtags || []).map((h) => "#" + h).join("  ");
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
$("#modal").addEventListener("click", (e) => { if (e.target === $("#modal")) closeModal(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });

// ---------------------------------------------------------------- actions
async function importSession() {
  toast("Looking for a logged-in Instagram session…");
  try {
    const r = await api("/api/session/import", { method: "POST", body: "{}" });
    toast(`Session imported from ${r.browser} ✓`);
    await load();
  } catch (e) { toast(e.message, true); }
}

async function addAccount() {
  const input = $("#newAccount");
  if (!input) return;
  const username = input.value.trim().replace(/^@/, "");
  if (!username) return;
  const btn = $("#addBtn");
  btn.disabled = true; btn.textContent = "…";
  try {
    const r = await api("/api/accounts", { method: "POST", body: JSON.stringify({ username }) });
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

// ---------------------------------------------------------------- progress
function setRunning(job) {
  const running = !!(job && (job.status === "running" || job.status === undefined));
  $("#scrapeBtn").style.display = running ? "none" : "";
  $("#cancelBtn").style.display = running ? "" : "none";
  $("#progress").classList.toggle("on", !!job);
  if (!job) return;

  $("#pCount").textContent = fmtNum(job.downloaded || 0);
  $("#pTotal").textContent = job.expected ? `of ${fmtNum(job.expected)}` : "";
  $("#pStatus").textContent = job.status;
  $("#pTitle").textContent = job.status === "running"
    ? `Downloading @${job.account}…`
    : `${job.status === "cancelled" ? "Stopped" : job.status === "done" ? "Finished" : "Ended"} — ${fmtNum(job.downloaded || 0)} new`;
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
      toast(j.status === "done"
        ? `Finished — ${j.downloaded} new reel(s) ✓`
        : j.status === "cancelled" ? "Stopped"
        : `Ended: ${j.status} (${j.downloaded} new)`, j.status !== "done");
      setTimeout(load, 700);
    }
  };
  ES.onerror = () => { if (ES) { ES.close(); ES = null; } };
}

// ---------------------------------------------------------------- boot
load().catch((e) => toast(e.message, true));
setInterval(() => { if (!ES) load().catch(() => {}); }, 20000);

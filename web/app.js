// ReelVault UI
const $ = (s) => document.querySelector(s);
let STATE = { accounts: [], deps: {}, session: {} };
let CURRENT = null;      // account id
let POSTS = [];
let VIEW = localStorage.getItem("reelvault.view") || "grid";
let ES = null;

const fmtBytes = (b) => {
  if (!b) return "0 MB";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0, n = b;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(n < 10 && i > 0 ? 1 : 0)} ${u[i]}`;
};
const fmtNum = (n) => (n || 0).toLocaleString();

function toast(msg, isErr) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast on" + (isErr ? " err" : "");
  clearTimeout(t._h);
  t._h = setTimeout(() => (t.className = "toast"), 3600);
}

async function api(path, opts) {
  const r = await fetch(path, Object.assign({ headers: { "Content-Type": "application/json" } }, opts || {}));
  const ct = r.headers.get("content-type") || "";
  const body = ct.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw Object.assign(new Error((body && body.error) || r.statusText), { body });
  return body;
}

// ---------------------------------------------------------------- state / sidebar
async function load() {
  STATE = await api("/api/state");
  renderSession();
  renderAccounts();
  if (CURRENT && !STATE.accounts.find((a) => a.id === CURRENT)) CURRENT = null;
  if (!CURRENT && STATE.accounts.length) CURRENT = STATE.accounts[0].id;
  renderMain();
}

function renderSession() {
  const h = $("#connHint");
  const s = STATE.session || {};
  const gd = STATE.deps.gallery_dl || {};
  if (!s.logged_in) {
    h.innerHTML = `<span style="color:var(--err)">Instagram not connected.</span>
      <a href="#" id="importLink">Import session from my browser</a>`;
    const a = $("#importLink");
    if (a) a.onclick = importSession;
  } else if (!gd.ok) {
    h.innerHTML = `<span style="color:var(--warn)">gallery-dl missing.</span>
      <code>pip3 install --user gallery-dl</code>`;
  } else {
    h.innerHTML = `<span style="color:var(--ok)">Instagram connected</span> · gallery-dl ${gd.version || ""}`;
  }
}

function renderAccounts() {
  const box = $("#accounts");
  box.innerHTML = "";
  if (!STATE.accounts.length) {
    box.innerHTML = `<div class="hint" style="padding:6px 8px">No accounts yet — add one below.</div>`;
    return;
  }
  for (const a of STATE.accounts) {
    const el = document.createElement("div");
    el.className = "acc" + (a.id === CURRENT ? " active" : "");
    el.innerHTML = `
      <div class="avatar">${a.username[0]}</div>
      <div class="meta">
        <div class="name">@${a.username}</div>
        <div class="sub">${fmtNum(a.saved)} saved${a.total_posts ? " / " + fmtNum(a.total_posts) : ""}</div>
      </div>
      <div class="dot ${a.job ? "busy" : ""}"></div>`;
    el.onclick = () => { CURRENT = a.id; renderAccounts(); renderMain(); };
    box.appendChild(el);
  }
}

// ---------------------------------------------------------------- main panel
function account() {
  return STATE.accounts.find((a) => a.id === CURRENT) || null;
}

function renderMain() {
  const a = account();
  const controls = $("#controls");
  if (!a) {
    $("#accTitle").textContent = "Library";
    $("#accChip").style.display = "none";
    $("#controls").style.display = "none";
    $("#progress").classList.remove("on");
    $("#lib").innerHTML = `<div class="empty"><div>
        <h3>No account selected</h3>
        <p>Connect your Instagram session, then add a handle in the sidebar to start archiving.</p>
      </div></div>`;
    return;
  }
  $("#accTitle").textContent = "@" + a.username;
  const chip = $("#accChip");
  chip.style.display = "";
  chip.className = "chip ok";
  chip.textContent = `${fmtNum(a.saved)} saved · ${fmtBytes(a.size_bytes)}`;
  controls.style.display = "";
  $("#quality").value = a.quality || "720p";
  $("#revealBtn").style.display = "";
  $("#csvBtn").style.display = "";
  $("#removeBtn").style.display = "";
  $("#scrapeBtn").disabled = !STATE.session.logged_in;
  if (a.job) setRunning(a.job); else setRunning(null);
  loadPosts(a.id);
}

async function loadPosts(id) {
  const res = await api(`/api/accounts/${id}/posts?limit=500`);
  POSTS = res.items;
  renderLibrary();
}

function renderLibrary() {
  const lib = $("#lib");
  if (!POSTS.length) {
    lib.innerHTML = `<div class="empty"><div>
        <h3>Nothing saved yet</h3>
        <p>Hit “Download new” to pull this account's reels into the library.</p>
      </div></div>`;
    return;
  }
  const tags = new Set();
  POSTS.forEach((p) => (p.hashtags || []).forEach((h) => tags.add(h.toLowerCase())));

  lib.innerHTML = `<div class="libhead">
      <h3>${POSTS.length} ${POSTS.length === 1 ? "reel" : "reels"}</h3>
      <span class="chip">${tags.size} ${tags.size === 1 ? "hashtag" : "hashtags"}</span>
      <div class="spacer" style="flex:1"></div>
      <div class="seg">
        <button data-view="grid" class="${VIEW === "grid" ? "on" : ""}">Grid</button>
        <button data-view="table" class="${VIEW === "table" ? "on" : ""}">Table</button>
      </div>
    </div>`;

  lib.querySelectorAll("[data-view]").forEach((b) => {
    b.onclick = () => { VIEW = b.dataset.view; localStorage.setItem("reelvault.view", VIEW); renderLibrary(); };
  });

  if (VIEW === "table") return renderTable(lib);

  const grid = document.createElement("div");
  grid.className = "grid";
  for (const p of POSTS) {
    const card = document.createElement("div");
    card.className = "card";
    const sc = p.shortcode || "";
    const tagline = (p.hashtags || []).slice(0, 3).map((h) => "#" + h).join(" ");
    card.innerHTML = `
      <img class="thumb" loading="lazy" src="/thumb/${encodeURIComponent(account().username)}/${encodeURIComponent(sc)}.jpg" alt="">
      <div class="play"><div class="playbtn">▶</div></div>
      <div class="body">
        <div class="t" title="${escapeHtml(p.caption || p.title)}">${escapeHtml(p.title)}</div>
        ${tagline ? `<div class="tags">${escapeHtml(tagline)}${p.hashtags.length > 3 ? " +" + (p.hashtags.length - 3) : ""}</div>` : ""}
        <div class="m"><span>${fmtNum(p.likes)} likes</span><span>${fmtBytes(p.bytes)}</span></div>
      </div>`;
    card.onclick = () => play(p);
    grid.appendChild(card);
  }
  lib.appendChild(grid);
}

function renderTable(lib) {
  const rows = POSTS.map((p) => {
    const sc = encodeURIComponent(p.shortcode || "");
    const user = encodeURIComponent(account().username);
    const tags = (p.hashtags || []).map((h) => "#" + h).join(" ");
    const cap = (p.caption || "").replace(/\s+/g, " ").trim();
    return `<tr data-sc="${sc}">
      <td class="th"><img class="mini" loading="lazy" src="/thumb/${user}/${sc}.jpg" alt=""></td>
      <td class="tt">
        <div class="tl" title="${escapeHtml(cap || p.title)}">${escapeHtml(p.title)}</div>
        ${cap && cap !== p.title ? `<div class="cp">${escapeHtml(cap)}</div>` : ""}
      </td>
      <td class="num">${(p.date || "").slice(0, 10)}</td>
      <td class="num">${fmtNum(p.likes)}</td>
      <td class="tags">${tags ? escapeHtml(tags) : '<span style="color:#4d5763">—</span>'}</td>
    </tr>`;
  }).join("");

  lib.insertAdjacentHTML("beforeend", `<div class="tablewrap"><table class="lib">
    <colgroup>
      <col style="width:48px"><col><col style="width:96px"><col style="width:80px"><col style="width:200px">
    </colgroup>
    <thead><tr>
      <th></th><th>Title / caption</th><th>Date</th><th>Likes</th><th>Hashtags</th>
    </tr></thead>
    <tbody>${rows}</tbody></table></div>`);

  lib.querySelectorAll("tr[data-sc]").forEach((tr) => {
    const p = POSTS.find((x) => x.shortcode === decodeURIComponent(tr.dataset.sc));
    tr.onclick = () => play(p);
  });
}

function escapeHtml(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
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
$("#modal").addEventListener("click", (e) => {
  if (e.target === $("#modal")) closeModal();
});
function closeModal() {
  $("#modal").classList.remove("on");
  $("#player").pause();
  $("#player").src = "";
}
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });

// ---------------------------------------------------------------- actions
async function importSession() {
  try {
    const r = await api("/api/session/import", { method: "POST", body: "{}" });
    toast(`Session imported from ${r.browser}`);
    await load();
  } catch (e) {
    toast(e.message, true);
  }
}

$("#addBtn").onclick = addAccount;
$("#newAccount").addEventListener("keydown", (e) => { if (e.key === "Enter") addAccount(); });

async function addAccount() {
  const input = $("#newAccount");
  const username = input.value.trim().replace(/^@/, "");
  if (!username) return;
  const btn = $("#addBtn");
  btn.disabled = true; btn.textContent = "…";
  try {
    const r = await api("/api/accounts", { method: "POST", body: JSON.stringify({ username }) });
    input.value = "";
    toast(`Added @${r.account.username} (${fmtNum(r.account.total_posts)} posts)`);
    CURRENT = r.account.id;
    await load();
  } catch (e) {
    if (e.message === "not_connected") { toast("Connect Instagram first", true); }
    else toast(e.message, true);
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
  const a = account(); if (!a) return;
  try { await api("/api/reveal", { method: "POST", body: JSON.stringify({ account_id: a.id }) }); }
  catch (e) { toast(e.message, true); }
};

$("#csvBtn").onclick = () => { const a = account(); if (a) window.location = `/api/accounts/${a.id}/index.csv`; };

$("#logToggle").onclick = () => {
  const l = $("#log");
  l.classList.toggle("on");
  $("#logToggle").textContent = l.classList.contains("on") ? "Hide log" : "Show log";
};

$("#removeBtn").onclick = async () => {
  const a = account(); if (!a) return;
  const del = confirm(`Remove @${a.username} from ReelVault?\n\nOK = remove from list (keep files)\nCancel = nothing`);
  if (!del) return;
  try {
    await api(`/api/accounts/${a.id}`, { method: "DELETE", body: JSON.stringify({ delete_files: false }) });
    CURRENT = null;
    await load();
  } catch (e) { toast(e.message, true); }
};

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
  const pct = job.expected ? Math.min(100, ((job.downloaded || 0) / job.expected) * 100) : 0;
  $("#barFill").style.width = (job.expected ? pct : 8) + "%";
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
      if (log.classList.contains("on") || log.scrollHeight < 400) log.scrollTop = log.scrollHeight;
      if (log.children.length > 400) log.removeChild(log.firstChild);
    } else if (msg.type === "state") {
      setRunning(msg.job);
    } else if (msg.type === "end") {
      ES.close(); ES = null;
      const j = msg.job;
      toast(j.status === "done" ? `Finished — ${j.downloaded} new reel(s)` :
            j.status === "cancelled" ? "Stopped" : `Ended: ${j.status} (${j.downloaded} new)`,
            j.status !== "done");
      setTimeout(load, 800);
    }
  };
  ES.onerror = () => { if (ES) { ES.close(); ES = null; } };
}

// ---------------------------------------------------------------- boot
load().catch((e) => toast(e.message, true));
setInterval(() => { if (!ES) load().catch(() => {}); }, 15000);

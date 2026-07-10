const $ = (id) => document.getElementById(id);
const show = (id) => {
  ["consent", "setup", "main"].forEach((s) => $(s).classList.toggle("hidden", s !== id));
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let settings = {};

async function init() {
  const { consent } = await Store.get("consent");
  if (!consent) { show("consent"); return; }
  await checkSetup();
}

async function checkSetup() {
  settings = await Store.get(["scopioUrl", "scopioToken"]);
  if (!settings.scopioToken) { show("setup"); return; }
  show("main");
}

$("allowBtn").addEventListener("click", async () => { await Store.set({ consent: true }); await checkSetup(); });
$("denyBtn").addEventListener("click", () => window.close());
$("openOptions").addEventListener("click", () => chrome.runtime.openOptionsPage());
$("openDash").addEventListener("click", () => chrome.tabs.create({ url: settings.scopioUrl }));
$("findBtn").addEventListener("click", findLeads);

function api(path) { return settings.scopioUrl.replace(/\/$/, "") + path; }
function authHeaders() { return { "Content-Type": "application/json", Authorization: "Bearer " + settings.scopioToken }; }

async function findLeads() {
  const address = $("query").value.trim();
  const radius = parseInt($("radius").value, 10) || 2000;
  if (!address) return;
  $("err").textContent = ""; $("sendMsg").textContent = "";
  $("openDash").classList.add("hidden");
  $("list").innerHTML = '<p class="muted">Starting search…</p>';
  $("findBtn").disabled = true;
  try {
    const job = await (await fetch(api("/search_jobs"), {
      method: "POST", headers: authHeaders(),
      body: JSON.stringify({ raw_address: address, radius_m: radius }),
    })).json();
    if (!job.id) throw new Error(job.detail || "could not start search");

    let status = "pending";
    for (let i = 0; i < 40; i++) {
      await sleep(2000);
      const s = await (await fetch(api("/search_jobs/" + job.id), { headers: authHeaders() })).json();
      status = s.status;
      $("list").innerHTML = `<p class="muted">Searching… (${status})</p>`;
      if (status === "completed" || status === "failed") break;
    }
    if (status !== "completed") { $("list").innerHTML = ""; $("err").textContent = "Search " + status; return; }

    // Only fetch businesses the user can actually contact.
    const data = await (await fetch(api("/search_jobs/" + job.id + "/businesses?limit=50&contactable=true"), { headers: authHeaders() })).json();
    renderLeads(data.items || [], data.total || 0);
  } catch (e) {
    $("list").innerHTML = "";
    $("err").textContent = "Failed: " + e.message + " (is the Scopio server connected in settings?)";
  } finally {
    $("findBtn").disabled = false;
  }
}

function renderLeads(items, total) {
  if (!items.length) { $("list").innerHTML = '<p class="muted">No businesses found. Try a bigger radius or a city.</p>'; return; }
  items.forEach((b) => (b.score = leadScore(b)));
  items.sort((a, b) => b.score - a.score);
  $("sendMsg").textContent = `✅ Found ${total} businesses — saved to your Scopio account.`;
  $("list").innerHTML = items.map((b) => `
    <div class="lead">
      <span class="score">${b.score}</span>
      <div class="name">${esc(b.name)}</div>
      <div class="meta">${esc(b.category || "")}${b.phone ? " · 📞 " + esc(b.phone) : ""}${b.website ? " · 🔗" : ""}</div>
      <div class="meta">${esc(b.address || "")}</div>
    </div>`).join("");
  $("openDash").classList.remove("hidden");
}

function esc(s) {
  return (s || "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

init();

const $ = (id) => document.getElementById(id);

// Prefill saved values.
(async () => {
  const s = await Store.get(["scopioUrl", "scopioToken"]);
  if (s.scopioUrl) $("url").value = s.scopioUrl;
  if (s.scopioToken) $("connOk").textContent = "✅ Connected.";
})();

function originOf(url) {
  try { return new URL(url).origin + "/*"; } catch { return null; }
}

$("connectBtn").addEventListener("click", async () => {
  $("connErr").textContent = ""; $("connOk").textContent = "Connecting…";
  const url = $("url").value.trim().replace(/\/$/, "");
  const origin = originOf(url);
  if (!origin) { $("connErr").textContent = "Invalid server URL."; $("connOk").textContent = ""; return; }

  // Ask for permission to talk to this server (optional host permission, user gesture).
  const granted = await chrome.permissions.request({ origins: [origin] });
  if (!granted) { $("connErr").textContent = "Permission to reach the server was denied."; $("connOk").textContent = ""; return; }

  try {
    const res = await fetch(url + "/auth/login", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: $("email").value.trim(), password: $("password").value }),
    });
    const d = await res.json();
    if (!res.ok) { $("connOk").textContent = ""; $("connErr").textContent = d.detail || ("Login failed " + res.status); return; }
    await Store.set({ scopioUrl: url, scopioToken: d.token });
    $("connOk").textContent = `✅ Connected as ${d.user.name || d.user.email}. You're ready — open the popup and search.`;
  } catch (e) {
    $("connOk").textContent = "";
    $("connErr").textContent = "Could not reach the server: " + e.message;
  }
});

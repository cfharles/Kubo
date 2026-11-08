"use strict";
const $ = (id) => document.getElementById(id);
const TOKEN_KEY = "kubo_device_token";
let token = null;
let device = null;
let session = null;
let poll = null;
let checking = false;
let starting = false;
let pairingCode = null;
let expiresAt = null;

function message(text, kind = "error") {
  $("cube-message").textContent = text || "";
  $("cube-message").className = `notice ${kind}`;
  $("cube-message").hidden = !text;
}
async function api(path, options = {}) {
  let response;
  try { response = await fetch(path, { ...options, headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}), ...options.headers } }); }
  catch (_) { throw new Error("The KUBO server is unavailable. Start it on your Mac, then try again."); }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = data.detail || data.error || data.message;
    const error = new Error(typeof detail === "string" ? detail : Array.isArray(detail) ? detail.map((item) => item.msg).join(" ") : (detail?.message || "That didn’t work. Please try again."));
    error.status = response.status;
    throw error;
  }
  return data;
}
function status(label, ready = false) {
  $("cube-status").textContent = label;
  $("cube-status").className = `status-pill${ready ? " ready" : ""}`;
}
function handleError(error) {
  message(error.message);
  if (error.status === 401) {
    clearInterval(poll); poll = null; device = null; session = null;
    $("pairing-panel").hidden = true; $("ready-panel").hidden = true; $("session-panel").hidden = true;
    $("cube-loading").hidden = true; $("reset-device").hidden = false;
    $("retry").hidden = true;
    status("Setup needed");
    message("This cube identity is no longer recognized by the server. Set up a new cube and pair it with your family again.");
  }
}
function renderCode(data) {
  pairingCode = data.pairing_code; expiresAt = new Date(data.expires_at);
  $("pairing-code").textContent = pairingCode;
  updateExpiry();
}
function updateExpiry() {
  if (!expiresAt || device?.paired) return;
  const valid = expiresAt.getTime() > Date.now();
  $("expiry").textContent = valid ? `One-use code · expires at ${expiresAt.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}` : "This code has expired. Press Refresh code for a new one.";
}
function renderDevice() {
  $("cube-loading").hidden = true;
  $("pairing-panel").hidden = device.paired;
  $("ready-panel").hidden = !device.paired || Boolean(session);
  $("session-panel").hidden = !session;
  $("retry").hidden = true; $("reset-device").hidden = true;
  $("cube-art").dataset.state = session ? "running" : device.paired ? "ready" : "waiting";
  if (session) { status("Session loaded", true); return; }
  if (device.paired) {
    status("Paired & ready", true);
    $("ready-title").textContent = `Hello, ${device.family.name}.`;
  } else status("Waiting to pair");
}
async function checkPairing(manual = false) {
  if (checking || !token) return;
  checking = true;
  if (manual) $("check-pairing").disabled = true;
  try {
    const wasPaired = device?.paired;
    device = await api("/api/device"); renderDevice();
    $("connection").textContent = "";
    if (device.paired && !wasPaired) message("Your cube is connected to your family.", "success");
    else if (manual && !device.paired) message("Still waiting. Enter this code in the parent app, then check again.", "info");
  } catch (error) {
    if (manual || error.status === 401) handleError(error);
    else $("connection").textContent = "Connection interrupted. Retrying automatically…";
  } finally { checking = false; $("check-pairing").disabled = false; updateExpiry(); }
}
async function init() {
  clearInterval(poll); poll = null;
  $("retry").hidden = true; $("reset-device").hidden = true;
  message(""); status("Connecting");
  try {
    try { token = localStorage.getItem(TOKEN_KEY); }
    catch (_) { throw new Error("This browser cannot read its cube identity. Allow local storage for this site, then retry."); }
    if (!token) {
      const enrollment = await api("/api/devices", { method: "POST", body: JSON.stringify({ name: "Mac cube" }) });
      token = enrollment.device_token;
      try { localStorage.setItem(TOKEN_KEY, token); }
      catch (_) { token = null; throw new Error("This browser cannot store its cube identity. Allow local storage for this site, then retry."); }
      renderCode(enrollment);
    }
    device = await api("/api/device");
    if (!device.paired && !pairingCode) renderCode(await api("/api/device/pairing-code", { method: "POST" }));
    renderDevice();
    poll = setInterval(() => checkPairing(), 3000);
  } catch (error) {
    $("cube-loading").hidden = true;
    handleError(error);
    if (error.status !== 401) { status("Connection needed"); $("retry").hidden = false; }
  }
}
$("check-pairing").addEventListener("click", () => checkPairing(true));
$("refresh-code").addEventListener("click", async () => {
  $("refresh-code").disabled = true; message("");
  try {
    device = await api("/api/device");
    if (device.paired) { renderDevice(); message("Your cube is already connected to your family.", "success"); }
    else { renderCode(await api("/api/device/pairing-code", { method: "POST" })); message("New code ready. Use this code in the parent app.", "info"); }
  } catch (error) { handleError(error); }
  finally { $("refresh-code").disabled = false; }
});
$("start-cube").addEventListener("click", async () => {
  if (starting) return;
  starting = true; $("start-cube").disabled = true; message("");
  try {
    const data = await api("/api/device/start", { method: "POST" }); session = data.session;
    $("session-family").textContent = `${device.family.name} · Saved choice loaded from the server`;
    $("session-child").textContent = session.child.nickname;
    const age = document.createElement("span"); age.textContent = `Ages ${session.child.age_band.replace("-", "–")}`; $("session-child").append(age);
    $("session-subject").textContent = session.subject.label;
    $("session-goal").textContent = session.goal.label;
    renderDevice();
  } catch (error) { handleError(error); }
  finally { starting = false; $("start-cube").disabled = false; }
});
$("end-session").addEventListener("click", () => { session = null; message(""); renderDevice(); });
$("retry").addEventListener("click", init);
$("reset-device").addEventListener("click", () => {
  try { localStorage.removeItem(TOKEN_KEY); }
  catch (_) { message("Allow local storage for this site before setting up your cube."); return; }
  token = null; device = null; session = null; pairingCode = null; expiresAt = null; init();
});
init();

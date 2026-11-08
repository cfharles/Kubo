"use strict";

const $ = id => document.getElementById(id);
const TOKEN = "kubo_device_token",
  PENDING = "kubo_pending_request",
  LAST = "kubo_last_session";
const voice = new CubeVoice();
let token = null,
  device = null,
  session = null,
  code = null,
  expires = null,
  poll = null,
  checking = false;
let busy = false,
  uncertain = false,
  waitingAnswer = null,
  volatileBody = null,
  heard = null;
let operation = 0,
  inflightKind = null,
  retryAnswer = null;
let epoch = 0,
  audioAbort = null,
  turnAbort = null,
  mode = "idle",
  micRequest = 0;
function message(text, kind = "error") {
  const el = $("cube-message");
  el.textContent = text || "";
  el.className = "notice " + kind;
  el.hidden = !text;
}
function status(text, ready = false) {
  $("cube-status").textContent = text;
  $("cube-status").className = "status-pill" + (ready ? " ready" : "");
}
function voiceStatus(text) {
  $("voice-state").textContent = text;
}
function active() {
  return session && !session.ended_at;
}
function speaking() {
  return Boolean(voice.audio || voice.utterance);
}
function syncMic() {
  const on = voice.enabled;
  $("toggle-mic").textContent = on ? "Turn off microphone" : "Turn on microphone";
  $("toggle-mic").setAttribute("aria-pressed", String(on));
  $("listen-again").hidden = !on;
}
function discardHeard() {
  heard = null;
  $("heard-text").textContent = "";
  $("heard-panel").hidden = true;
}
function interrupt() {
  epoch++;
  audioAbort?.abort();
  turnAbort?.abort();
  voice.stopCapture();
  voice.stopSound();
  mode = "idle";
}
function micOff() {
  micRequest++;
  voice.disable();
  syncMic();
}
function remember(key, value) {
  try {
    value === null ? localStorage.removeItem(key) : localStorage.setItem(key, value);
  } catch (_) {
    throw new Error("Allow local storage for this site so the cube can safely recover this session.");
  }
}
function pending() {
  const raw = localStorage.getItem(PENDING);
  return raw ? JSON.parse(raw) : null;
}
function identity() {
  if (!window.isSecureContext || !crypto.randomUUID) {
    throw new Error("Open the cube using HTTPS or localhost on this Mac.");
  }
  return crypto.randomUUID();
}
async function api(path, {
  method = "GET",
  body,
  signal,
  binary = false
} = {}) {
  let response;
  const headers = {};
  if (token) {
    headers.Authorization = "Bearer " + token;
  }
  const options = {
    method,
    headers,
    signal,
    credentials: "omit",
    cache: "no-store"
  };
  if (body instanceof Blob) {
    headers["Content-Type"] = body.type;
    options.body = body;
  } else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  try {
    response = await fetch(path, options);
  } catch (e) {
    if (e.name === "AbortError") {
      throw e;
    }
    throw new Error("Cannot reach the KUBO server. Check the Mac connection and try again.");
  }
  if (response.ok && binary) {
    return response.blob();
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = data.detail || data.error;
    const error = new Error(typeof detail === "string" ? detail : Array.isArray(detail) ? detail.map(x => x.msg).join(" ") : "The server could not complete that request.");
    error.status = response.status;
    throw error;
  }
  return data;
}
function handleError(e) {
  if (e.name === "AbortError") {
    return;
  }
  message(e.message);
  if (e.status === 401) {
    interrupt();
    micOff();
    clearInterval(poll);
    session = device = null;
    code = null;
    for (const id of ["ready-panel", "pairing-panel", "session-panel", "cube-loading", "recover", "retry"]) {
      $(id).hidden = true;
    }
    $("reset-device").hidden = false;
    status("Setup needed");
    message("This cube identity is no longer recognized. Set up a new cube and pair it again.");
  }
}
function renderCode(data) {
  code = data.pairing_code;
  expires = new Date(data.expires_at);
  $("pairing-code").textContent = code;
  showExpiry();
}
function showExpiry() {
  if (!expires || device?.paired) {
    return;
  }
  $("expiry").textContent = expires > Date.now() ? "One-use code · expires at " + expires.toLocaleTimeString([], {
    hour: "numeric",
    minute: "2-digit"
  }) : "This code has expired. Choose Refresh code.";
}
function render() {
  $("cube-loading").hidden = true;
  $("ready-panel").hidden = !device?.paired || Boolean(session);
  $("pairing-panel").hidden = !device || device.paired;
  $("session-panel").hidden = !session;
  $("reset-device").hidden = true;
  $("retry").hidden = true;
  $("recover").hidden = !uncertain;
  const ended = Boolean(session?.ended_at),
    lesson = session?.lesson;
  $("cube-art").dataset.state = session && !ended ? "running" : device?.paired ? "ready" : "waiting";
  if (!session) {
    status(device?.paired ? "Paired & ready" : "Waiting to pair", device?.paired);
    if (device?.family) {
      $("ready-title").textContent = "Hello, " + device.family.name + ".";
    }
    return;
  }
  status(ended ? "Session ended" : lesson ? "Story session" : "Session loaded", true);
  $("session-title").textContent = lesson?.story_title || "Your family’s choice";
  $("session-family").textContent = device.family.name;
  $("session-child").textContent = session.child.nickname + " · Ages " + session.child.age_band.replace("-", "–");
  $("session-subject").textContent = session.subject.label;
  $("session-goal").textContent = session.goal.label;
  $("lesson-panel").hidden = ended || !lesson;
  $("setup-only").hidden = ended || !!lesson;
  $("finished-panel").hidden = !ended;
  $("new-session").hidden = !ended;
  $("end-session").hidden = ended || !!lesson;
  if (ended) {
    $("finished-note").textContent = session.report ? "Your family can see a learning note in the parent app." : "This session has ended.";
  }
  if (lesson && !ended) {
    $("phase-label").textContent = {
      opening: "Story time",
      followup: "Think it through",
      confirm: "Check your meaning"
    }[lesson.phase] || "Story time";
    $("story-text").textContent = lesson.phase === "opening" ? lesson.story_text : "";
    $("current-question").textContent = lesson.question || "";
    const caption = (lesson.prompt || "").replace(lesson.story_text || "\u0000", "").replace(lesson.question || "\u0000", "").trim();
    $("current-prompt").textContent = caption;
    $("evidence-panel").hidden = lesson.phase !== "confirm";
    $("typed-controls").hidden = lesson.phase === "confirm";
  }
  syncMic();
}
function lock(value) {
  busy = value;
  for (const id of ["start-cube", "new-session", "repeat-question", "get-hint", "stop-lesson", "end-session", "accept-heard", "reject-heard", "confirm-evidence", "reject-evidence", "recover"]) {
    $(id).disabled = value && !(id === "stop-lesson" && inflightKind === "answer");
  }
  for (const el of $("typed-form").elements) {
    el.disabled = value;
  }
}
function clearRetry() {
  retryAnswer = null;
  $("retry-answer").hidden = true;
}
function adopt(data) {
  clearRetry();
  interrupt();
  discardHeard();
  session = data;
  waitingAnswer = null;
  if (data) {
    remember(LAST, data.id);
    if (data.ended_at || !data.lesson) {
      micOff();
    }
  }
  $("typed-answer").value = "";
  render();
  voiceStatus(voice.enabled ? "Microphone ready" : "Microphone off");
}
async function reconcile({
  allowStart = true
} = {}) {
  const p = pending();
  const current = async () => (await api("/api/device/session")).session;
  let found = null;
  if (p) {
    let receipt;
    try {
      receipt = (await api("/api/device/receipts/" + encodeURIComponent(p.request_id))).session;
    } catch (e) {
      if (e.status !== 404) {
        throw e;
      }
    }
    if (receipt) {
      found = (await current()) || (await api("/api/device/session/" + receipt.id)).session;
    } else if (p.kind === "start" && allowStart) {
      try {
        found = (await api("/api/device/start", {
          method: "POST",
          body: {
            request_id: p.request_id
          }
        })).session;
      } catch (e) {
        if (e.status >= 400 && e.status < 500) {
          remember(PENDING, null);
        }
        throw e;
      }
    } else {
      found = await current();
      if (!found && p.session_id) {
        found = (await api("/api/device/session/" + p.session_id)).session;
      }
      if (found && !found.ended_at && found.id === p.session_id && found.revision === p.revision) {
        if (p.kind === "answer" && !volatileBody) {
          adopt(found);
          waitingAnswer = p;
          uncertain = false;
          render();
          message("The previous answer was not saved. Please give it again; KUBO will use the same turn.", "info");
          return;
        }
        const body = volatileBody || {
          request_id: p.request_id,
          revision: p.revision,
          kind: p.kind
        };
        try {
          found = (await api("/api/device/session/" + p.session_id + "/action", {
            method: "POST",
            body
          })).session;
        } catch (e) {
          if (e.status !== 409) {
            throw e;
          }
          found = (await current()) || (await api("/api/device/session/" + p.session_id)).session;
        }
      }
    }
  } else {
    found = await current();
    const last = localStorage.getItem(LAST);
    if (!found && last) {
      try {
        found = (await api("/api/device/session/" + encodeURIComponent(last))).session;
      } catch (e) {
        if (e.status !== 404) {
          throw e;
        }
        remember(LAST, null);
      }
    }
  }
  uncertain = false;
  adopt(found);
  remember(PENDING, null);
  volatileBody = null;
}
async function deliver(p, body) {
  const path = p.kind === "start" ? "/api/device/start" : "/api/device/session/" + p.session_id + "/action";
  try {
    return await api(path, {
      method: "POST",
      body
    });
  } catch (error) {
    // If an answer wins the race by one instant, Stop still means stop THIS
    // session. Read its newest revision and submit one new, durable Stop intent.
    if (error.status !== 409 || p.kind !== "stop") {
      throw error;
    }
    const latest = (await api("/api/device/session/" + p.session_id)).session;
    if (latest.ended_at) {
      return {
        session: latest
      };
    }
    if (latest.revision === p.revision) {
      throw error;
    }
    const request_id = identity(),
      revision = latest.revision;
    remember(PENDING, JSON.stringify({
      ...p,
      request_id,
      revision
    }));
    volatileBody = {
      ...body,
      request_id,
      revision
    };
    return await api(path, {
      method: "POST",
      body: volatileBody
    });
  }
}
async function mutate(kind, extras = {}) {
  const overtakes = kind === "stop" && inflightKind === "answer";
  if (busy && !overtakes || uncertain || !active() && kind !== "start") {
    return;
  }
  const thisOperation = ++operation;
  inflightKind = kind;
  const useMicrophone = session?.ended_at ? $("next-mic-choice").checked : $("mic-choice").checked;
  clearRetry();
  interrupt();
  discardHeard();
  if (kind === "stop") {
    micOff();
  }
  message("");
  lock(true);
  const isStart = kind === "start";
  try {
    const id = kind === "answer" && waitingAnswer?.session_id === session?.id && waitingAnswer.revision === session.revision ? waitingAnswer.request_id : identity();
    const p = {
      request_id: id,
      kind
    };
    const body = {
      request_id: id
    };
    if (!isStart) {
      p.session_id = session.id;
      p.revision = session.revision;
      Object.assign(body, {
        revision: p.revision,
        kind
      }, extras);
    }
    // Commit the intent BEFORE the request. Persist no child text or audio. Keep only
    // enough metadata to check a receipt or replay a safe action after reload.
    remember(PENDING, JSON.stringify(p));
    volatileBody = body;
    waitingAnswer = null;
    let data;
    try {
      data = await deliver(p, body);
    } catch (e) {
      if (thisOperation !== operation) {
        return;
      }
      if (!e.status || e.status >= 500 && e.status !== 503) {
        uncertain = true;
        render();
        message("The response did not arrive. Reconnect to recover this request safely.");
        return;
      }
      remember(PENDING, null);
      volatileBody = null;
      if (kind === "answer" && e.status === 422) {
        message(e.message + " Please give your answer again.", "info");
        afterPrompt(epoch);
        return;
      }
      if (kind === "answer" && e.status === 503) {
        retryAnswer = {
          ...extras
        };
        waitingAnswer = p;
        $("retry-answer").hidden = false;
        message(e.message + " You can retry this answer, repeat the question, or stop.");
        voiceStatus("Tutor paused · microphone is not recording an answer.");
        return;
      }
      if (e.status === 409 && !isStart) {
        await reconcile();
        message("The session changed. KUBO has restored the current question.", "info");
        return;
      }
      throw e;
    }
    if (thisOperation !== operation) {
      return;
    }
    adopt(data.session);
    remember(PENDING, null);
    volatileBody = null;
    if (active() && session.lesson) {
      if (isStart && useMicrophone && !voice.enabled) {
        enableMic(false);
      }
      playPrompt();
    }
  } catch (e) {
    handleError(e);
  } finally {
    if (thisOperation === operation) {
      inflightKind = null;
      lock(false);
    }
  }
}
async function resume() {
  if (busy) {
    return;
  }
  lock(true);
  interrupt();
  message("");
  try {
    await reconcile();
    if (!waitingAnswer) {
      message("Current session restored.", "success");
    }
  } catch (e) {
    handleError(e);
  } finally {
    lock(false);
  }
}
function normalize(text) {
  return text.toLowerCase().replace(/[^a-z\s']/g, " ").replace(/\s+/g, " ").trim().replace(/^(?:hey )?kubo\s+/, "");
}
const COMMANDS = new Map([["stop", "stop"], ["stop please", "stop"], ["please stop", "stop"], ["repeat", "repeat"], ["repeat please", "repeat"], ["please repeat", "repeat"], ["say that again", "repeat"], ["hint", "hint"], ["a hint", "hint"], ["hint please", "hint"], ["give me a hint", "hint"]]);
function yesNo(text) {
  if (/^(yes|yeah|yep|correct|that's right|yes please)$/.test(text)) {
    return true;
  }
  if (/^(no|nope|not right|try again)$/.test(text)) {
    return false;
  }
  return null;
}
function eligible(e) {
  return e === epoch && active() && !!session.lesson && !uncertain;
}
function afterPrompt(e) {
  if (!eligible(e)) {
    return;
  }
  mode = "idle";
  if (voice.enabled) {
    listen(e, false);
  } else {
    voiceStatus("Microphone off · turn it on to answer by voice.");
  }
}
async function playPrompt() {
  interrupt();
  const e = epoch;
  if (!active() || !session.lesson) {
    return;
  }
  mode = "loading";
  voiceStatus("Getting the story audio…");
  audioAbort = new AbortController();
  try {
    const blob = await api("/api/device/session/" + session.id + "/audio?revision=" + session.revision, {
      binary: true,
      signal: audioAbort.signal
    });
    if (!eligible(e)) {
      return;
    }
    mode = "speaking";
    voiceStatus("KUBO is speaking…");
    await voice.play(blob, () => afterPrompt(e), () => {
      if (eligible(e)) {
        message("Audio could not play. Use Repeat to hear the prompt again.");
        afterPrompt(e);
      }
    });
    if (eligible(e) && speaking() && voice.enabled) {
      listen(e, true);
    }
  } catch (err) {
    if (!eligible(e) || err.name === "AbortError") {
      return;
    }
    voice.stopSound();
    mode = "idle";
    handleError(err.name === "NotAllowedError" ? new Error("Press Repeat to allow story audio in this browser.") : err);
    afterPrompt(e);
  }
}
async function enableMic(begin = true) {
  const ticket = ++micRequest;
  voiceStatus("Waiting for microphone permission…");
  try {
    if (!(await voice.enable()) || ticket !== micRequest) {
      return;
    }
    if (!active() || !session.lesson) {
      micOff();
      return;
    }
    syncMic();
    if (mode === "speaking") {
      listen(epoch, true);
    } else if (mode !== "loading" && (begin || !speaking())) {
      listen(epoch, false);
    }
  } catch (e) {
    if (ticket === micRequest) {
      syncMic();
      voiceStatus(e.message);
    }
  }
}
function listen(e, barge) {
  if (!voice.enabled || !eligible(e) || barge && !speaking()) {
    return;
  }
  if (!barge) {
    mode = "listening";
    voiceStatus(heard ? "Listening for yes or no…" : session.lesson.phase === "confirm" ? "Listening for your meaning: yes or no…" : "Listening for your answer…");
  }
  const sid = session.id,
    revision = session.revision;
  voice.listen(async blob => {
    if (!eligible(e)) {
      return;
    }
    const ctrl = new AbortController();
    if (!barge) {
      turnAbort = ctrl;
      mode = "transcribing";
      voiceStatus("Checking what I heard…");
    }
    const again = () => {
      if (!eligible(e) || !voice.enabled) {
        return;
      }
      if (barge) {
        if (speaking()) {
          listen(e, true);
        }
      } else {
        listen(e, false);
      }
    };
    try {
      const result = await api("/api/device/session/" + sid + "/transcribe", {
        method: "POST",
        body: blob,
        signal: ctrl.signal
      });
      if (!eligible(e) || session.revision !== revision || !voice.enabled) {
        return;
      }
      if (!result.text?.trim() || result.confidence < 0.65) {
        if (!barge) {
          message("I didn’t catch that clearly. Please try again.", "info");
        }
        again();
        return;
      }
      const text = result.text.trim(),
        norm = normalize(text);
      if (COMMANDS.has(norm)) {
        mutate(COMMANDS.get(norm));
        return;
      }
      if (barge) {
        again();
        return;
      }
      if (heard || session.lesson.phase === "confirm") {
        const value = yesNo(norm);
        if (value === null) {
          message("Please say yes or no, or choose a button.", "info");
          again();
          return;
        }
        if (heard) {
          if (value) {
            approveHeard();
          } else {
            retryHeard();
          }
        } else {
          mutate(value ? "confirm" : "reject");
        }
        return;
      }
      heard = text;
      $("heard-text").textContent = text;
      $("heard-panel").hidden = false;
      message("");
      interrupt();
      const gen = epoch;
      mode = "speaking";
      voiceStatus("Check what KUBO heard, then say yes or no.");
      if (voice.sayLocal("I heard: " + text + ". Is that what you said? Say yes or no.", () => afterPrompt(gen))) {
        listen(gen, true);
      } else {
        afterPrompt(gen);
      }
    } catch (err) {
      if (!eligible(e) || err.name === "AbortError") {
        return;
      }
      if (!barge) {
        if (err.status === 422) {
          message("I didn’t catch that clearly. Please say it again.", "info");
          again();
        } else {
          handleError(err);
          voiceStatus("Microphone paused · choose Listen again when ready.");
        }
      } else if (err.status === 422) {
        again();
      }
    }
  }, () => {
    if (!eligible(e)) {
      return;
    }
    if (barge && speaking()) {
      listen(e, true);
    } else if (!barge) {
      mode = "idle";
      voiceStatus("No speech detected. Choose Listen again or turn off the microphone.");
    }
  }, barge ? 7 : 15);
}
function approveHeard() {
  if (heard) {
    mutate("answer", {
      text: heard,
      confirmed: true
    });
  }
}
function retryHeard() {
  interrupt();
  discardHeard();
  message("Try your answer again.", "info");
  afterPrompt(epoch);
}
async function checkPairing(manual = false) {
  if (checking || !token || device?.paired) {
    return;
  }
  checking = true;
  $("check-pairing").disabled = true;
  try {
    device = await api("/api/device");
    if (device.paired) {
      clearInterval(poll);
      await reconcile();
      message("Your cube is connected to your family.", "success");
    } else if (manual) {
      message("Still waiting. Enter this code in the parent app.", "info");
    }
    render();
    showExpiry();
    $("connection").textContent = "";
  } catch (e) {
    if (manual || e.status === 401) {
      handleError(e);
    } else {
      $("connection").textContent = "Connection interrupted. Retrying…";
    }
  } finally {
    checking = false;
    $("check-pairing").disabled = false;
  }
}
async function init() {
  clearInterval(poll);
  $("retry").hidden = true;
  message("");
  status("Connecting");
  try {
    token = localStorage.getItem(TOKEN);
    if (!token) {
      const data = await api("/api/devices", {
        method: "POST",
        body: {
          name: "Mac cube"
        }
      });
      token = data.device_token;
      remember(TOKEN, token);
      renderCode(data);
    }
    device = await api("/api/device");
    if (device.paired) {
      await reconcile();
    } else {
      if (!code) {
        renderCode(await api("/api/device/pairing-code", {
          method: "POST"
        }));
      }
      poll = setInterval(() => checkPairing(), 2400);
    }
    render();
  } catch (e) {
    $("cube-loading").hidden = true;
    handleError(e);
    if (e.status !== 401) {
      status("Connection needed");
      $("retry").hidden = false;
    }
  }
}
$("start-cube").onclick = $("new-session").onclick = () => mutate("start");
$("stop-lesson").onclick = $("end-session").onclick = () => mutate("stop");
$("get-hint").onclick = () => mutate("hint");
$("repeat-question").onclick = () => mutate("repeat");
$("confirm-evidence").onclick = () => mutate("confirm");
$("reject-evidence").onclick = () => mutate("reject");
$("retry-answer").onclick = () => {
  if (retryAnswer) {
    mutate("answer", {
      ...retryAnswer
    });
  }
};
$("accept-heard").onclick = approveHeard;
$("reject-heard").onclick = retryHeard;
$("toggle-mic").onclick = () => {
  if (voice.enabled) {
    micOff();
    turnAbort?.abort();
    voiceStatus("Microphone off");
  } else {
    enableMic();
  }
};
$("listen-again").onclick = () => {
  interrupt();
  listen(epoch, false);
};
$("typed-form").onsubmit = e => {
  e.preventDefault();
  const text = $("typed-answer").value.trim();
  if (text) {
    mutate("answer", {
      text,
      confirmed: true
    });
  }
};
$("check-pairing").onclick = () => checkPairing(true);
$("refresh-code").onclick = async () => {
  const btn = $("refresh-code");
  btn.disabled = true;
  try {
    await checkPairing();
    if (!device.paired) {
      renderCode(await api("/api/device/pairing-code", {
        method: "POST"
      }));
      message("Use this new code in your parent app.", "info");
    }
  } catch (e) {
    handleError(e);
  } finally {
    btn.disabled = false;
  }
};
$("retry").onclick = init;
$("recover").onclick = resume;
$("reset-device").onclick = () => {
  try {
    for (const key of [TOKEN, PENDING, LAST]) {
      remember(key, null);
    }
    token = device = session = code = null;
    uncertain = false;
    init();
  } catch (e) {
    handleError(e);
  }
};
window.addEventListener("pagehide", () => {
  interrupt();
  micOff();
  discardHeard();
  clearRetry();
  volatileBody = null;
});
window.addEventListener("pageshow", e => {
  if (e.persisted) {
    init();
  }
});
init();

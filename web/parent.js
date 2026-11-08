"use strict";

const $ = id => document.getElementById(id);
let mode = "register";
let catalog = [];
let parent = null;
let busy = false;
let initializedFamily = null;
let reportGeneration = 0;
async function api(path, options = {}) {
  let response;
  try {
    response = await fetch(path, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        ...options.headers
      }
    });
  } catch (_) {
    throw new Error("The KUBO server is unavailable. Check that it is running and that your phone and Mac are on the same Wi-Fi, then try again.");
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = data.detail || data.error || data.message;
    const error = new Error(typeof detail === "string" ? detail : Array.isArray(detail) ? detail.map(item => item.msg).join(" ") : detail?.message || "That didn’t work. Please try again.");
    error.status = response.status;
    throw error;
  }
  return data;
}
function message(id, text, kind = "error") {
  $(id).textContent = text || "";
  $(id).className = `notice ${kind}`;
  $(id).hidden = !text;
}
function option(value, label) {
  const node = document.createElement("option");
  node.value = value;
  node.textContent = label;
  return node;
}
function setBusy(form, value) {
  for (const control of form.querySelectorAll("button,input,select")) {
    control.disabled = value;
  }
}
function showAuth() {
  document.body.classList.remove("signed-in");
  parent = null;
  initializedFamily = null;
  reportGeneration++;
  $("report-list").replaceChildren();
  for (const id of ["child-message", "pair-message", "goal-message", "parent-status"]) {
    message(id, "");
  }
  $("family-space").hidden = true;
  $("loading").hidden = true;
  $("auth").hidden = false;
}
function fillGoals(selectedId) {
  const subject = catalog.find(item => item.id === $("subject-choice").value);
  $("goal-choice").replaceChildren(...(subject?.goals || []).map(goal => option(goal.id, goal.label)));
  if (selectedId) {
    $("goal-choice").value = selectedId;
  }
}
function renderSaved() {
  const selection = parent.selection;
  $("saved-goal").hidden = !selection;
  if (!selection) {
    return;
  }
  $("saved-selection").textContent = `${selection.child.nickname} · ${selection.goal.label}`;
  $("saved-context").textContent = `${selection.subject.label} · Ages ${selection.child.age_band.replace("-", "–")} · Ready for your cube’s next start`;
}
function renderFamily() {
  document.body.classList.add("signed-in");
  const firstRender = initializedFamily !== parent.family.id;
  const draft = firstRender ? null : {
    child: $("child-choice").value,
    subject: $("subject-choice").value,
    goal: $("goal-choice").value
  };
  if (firstRender) {
    $("family-setup").open = !parent.children.length || !parent.devices.length;
    initializedFamily = parent.family.id;
  }
  $("auth").hidden = true;
  $("loading").hidden = true;
  $("family-space").hidden = false;
  $("family-heading").textContent = parent.family.name;
  $("child-list").replaceChildren(...parent.children.map(child => {
    const chip = document.createElement("div");
    chip.className = "child-chip";
    const nickname = document.createElement("strong");
    nickname.textContent = child.nickname;
    const age = document.createElement("span");
    age.textContent = `Ages ${child.age_band.replace("-", "–")}`;
    chip.append(nickname, age);
    return chip;
  }));
  $("paired-devices").hidden = parent.devices.length === 0;
  $("paired-devices").replaceChildren();
  if (parent.devices.length) {
    const heading = document.createElement("strong");
    heading.textContent = "Connected to your family";
    const list = document.createElement("ul");
    for (const device of parent.devices) {
      const item = document.createElement("li");
      item.textContent = device.name;
      list.append(item);
    }
    $("paired-devices").append(heading, list);
  }
  $("child-choice").replaceChildren(...parent.children.map(child => option(child.id, `${child.nickname} · Ages ${child.age_band.replace("-", "–")}`)));
  $("subject-choice").replaceChildren(...catalog.map(subject => option(subject.id, subject.label)));
  const childID = draft?.child || parent.selection?.child.id;
  if (childID) {
    $("child-choice").value = childID;
  }
  $("subject-choice").value = draft?.subject || parent.selection?.subject.id || "english";
  fillGoals(draft?.goal || parent.selection?.goal.id || "explain-a-characters-choice");
  $("goal-fields").disabled = parent.children.length === 0 || catalog.length === 0;
  $("no-children").hidden = parent.children.length > 0;
  renderSaved();
  loadReports();
}
function setMode(nextMode) {
  if (busy) {
    return;
  }
  mode = nextMode;
  $("register-mode").setAttribute("aria-pressed", String(mode === "register"));
  $("login-mode").setAttribute("aria-pressed", String(mode === "login"));
  $("auth-title").textContent = mode === "register" ? "Meet your fictional family." : "Welcome back, explorers.";
  $("auth-description").textContent = mode === "register" ? "Create a family for this demo. Please use invented names and a demo password." : "Sign in to see your children, paired cubes, and saved goal.";
  $("auth-submit").textContent = mode === "register" ? "Create family" : "Sign in";
  $("password").autocomplete = mode === "register" ? "new-password" : "current-password";
  message("auth-message", "");
}
async function handleError(error, target) {
  if (error.status === 401 && parent) {
    showAuth();
    setMode("login");
    message("auth-message", "Your sign-in has expired. Sign in again to continue.");
  } else {
    message(target, error.message);
  }
}
$("register-mode").addEventListener("click", () => setMode("register"));
$("login-mode").addEventListener("click", () => setMode("login"));
$("subject-choice").addEventListener("change", () => fillGoals());
for (const id of ["child-choice", "subject-choice", "goal-choice"]) {
  $(id).addEventListener("change", () => message("goal-message", "Choice changed. Press Save goal to update the cube’s next session.", "info"));
}
$("auth-form").addEventListener("submit", async event => {
  event.preventDefault();
  if (busy) {
    return;
  }
  busy = true;
  setBusy(event.currentTarget, true);
  message("auth-message", "");
  try {
    await api(`/api/parents/${mode}`, {
      method: "POST",
      body: JSON.stringify({
        family_name: $("family-name").value.trim(),
        password: $("password").value
      })
    });
    const [familyData, catalogData] = await Promise.all([api("/api/parent"), api("/api/catalog")]);
    parent = familyData;
    catalog = catalogData.subjects;
    $("password").value = "";
    renderFamily();
  } catch (error) {
    message("auth-message", error.message);
  } finally {
    busy = false;
    setBusy($("auth-form"), false);
  }
});
$("logout").addEventListener("click", async () => {
  $("logout").disabled = true;
  try {
    await api("/api/parents/logout", {
      method: "POST"
    });
    showAuth();
    setMode("login");
  } catch (error) {
    message("parent-status", error.message);
  } finally {
    $("logout").disabled = false;
  }
});
$("child-form").addEventListener("submit", async event => {
  event.preventDefault();
  const form = event.currentTarget;
  setBusy(form, true);
  message("child-message", "");
  try {
    const data = await api("/api/children", {
      method: "POST",
      body: JSON.stringify({
        nickname: $("nickname").value.trim(),
        age_band: $("age-band").value
      })
    });
    parent.children.push(data.child);
    renderFamily();
    $("nickname").value = "";
    message("child-message", `${data.child.nickname} is part of your family.`, "success");
  } catch (error) {
    await handleError(error, "child-message");
  } finally {
    setBusy(form, false);
  }
});
$("pair-form").addEventListener("submit", async event => {
  event.preventDefault();
  const form = event.currentTarget;
  setBusy(form, true);
  message("pair-message", "");
  try {
    const data = await api("/api/pair", {
      method: "POST",
      body: JSON.stringify({
        code: $("pairing-code").value.trim().toUpperCase()
      })
    });
    if (!parent.devices.some(device => device.id === data.device.id)) {
      parent.devices.push(data.device);
    }
    renderFamily();
    $("pairing-code").value = "";
    message("pair-message", `${data.device.name} is paired with ${parent.family.name}.`, "success");
  } catch (error) {
    await handleError(error, "pair-message");
  } finally {
    setBusy(form, false);
  }
});
$("goal-form").addEventListener("submit", async event => {
  event.preventDefault();
  const form = event.currentTarget;
  setBusy(form, true);
  message("goal-message", "");
  try {
    const data = await api("/api/selection", {
      method: "POST",
      body: JSON.stringify({
        child_id: $("child-choice").value,
        subject_id: $("subject-choice").value,
        goal_id: $("goal-choice").value
      })
    });
    parent.selection = data.selection;
    renderSaved();
    message("goal-message", `Saved: ${data.selection.child.nickname} · ${data.selection.goal.label}. You can close this app and start the cube on your Mac.`, "success");
  } catch (error) {
    await handleError(error, "goal-message");
  } finally {
    setBusy(form, false);
  }
});
async function init() {
  try {
    catalog = (await api("/api/catalog")).subjects;
    parent = await api("/api/parent");
    renderFamily();
  } catch (error) {
    showAuth();
    if (error.status !== 401) {
      message("auth-message", error.message);
    }
  }
}
init();
async function loadReports() {
  const id = $("child-choice").value,
    generation = ++reportGeneration;
  const child = parent?.children.find(child => child.id === id);
  const list = $("report-list");
  list.replaceChildren();
  $("refresh-reports").disabled = !child;
  $("report-intro").textContent = child ? `Learning notes for ${child.nickname}` : "Add a child to see learning notes.";
  if (!child) {
    return;
  }
  list.textContent = "Loading notes…";
  try {
    const {
      reports
    } = await api(`/api/children/${encodeURIComponent(id)}/reports`);
    if (generation !== reportGeneration) {
      return;
    }
    list.replaceChildren();
    if (!reports.length) {
      list.textContent = "No notes yet. A finished English story session will appear here.";
    }
    for (const report of reports) {
      const entry = document.createElement("article");
      entry.className = "report-entry";
      const heading = document.createElement("h3");
      heading.textContent = `Session note · ${new Date(report.ended_at).toLocaleDateString()}`;
      const data = document.createElement("dl");
      for (const [label, value] of [["What KUBO observed", report.observation], ["Hint used", report.hint_used ? "Yes" : "No"], ["Try at home", report.home_question], ["Next review", report.next_review]]) {
        const dt = document.createElement("dt"),
          dd = document.createElement("dd");
        dt.textContent = label;
        dd.textContent = value ?? "—";
        data.append(dt, dd);
      }
      entry.append(heading, data);
      list.append(entry);
    }
  } catch (error) {
    if (generation === reportGeneration) {
      if (error.status === 401) {
        await handleError(error, "parent-status");
      } else {
        list.textContent = error.message;
      }
    }
  }
}
$("child-choice").addEventListener("change", loadReports);
$("refresh-reports").addEventListener("click", loadReports);
window.addEventListener("pageshow", () => {
  if (parent) {
    loadReports();
  }
});
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && parent) {
    loadReports();
  }
});

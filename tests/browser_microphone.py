"""Mac-only: Chromium fake microphone → Web Audio worklet → HTTP WAV → real Vosk.

The authored clips are adult-synthesized test speech, never a person's recording.
Requires the normal speech setup, Chromium, and a local tutor on port 8081.
Run: bash scripts/with-local-env.sh .venv/bin/python tests/browser_microphone.py
"""
import argparse
import io
import json
import os
from pathlib import Path
import re
import secrets
import sys
import subprocess
import tempfile
import time
from urllib.parse import urlparse
from uuid import uuid4
import wave

from playwright.sync_api import expect, sync_playwright

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.speech import speak


class LiveServer:
    """Explicitly launch the real app, never a regression fixture."""
    def __init__(self, url, port, output_dir):
        self.base = f"http://127.0.0.1:{port}"
        self.port = port
        self.log = (output_dir / "browser-server.log").open("w")
        self.env = {**os.environ, "DATABASE_URL": url, "KUBO_COOKIE_SECURE": "0", "KUBO_LLM_MODE": "local"}
        self.proc = None

    def start(self):
        self.proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                                      "--port", str(self.port), "--timeout-graceful-shutdown", "3"],
                                     cwd=ROOT, env=self.env, stdout=self.log, stderr=subprocess.STDOUT)
        end = time.monotonic() + 25
        while time.monotonic() < end and self.proc.poll() is None:
            try:
                if httpx.get(self.base + "/api/health", timeout=1, trust_env=False).is_success:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(.15)
        raise RuntimeError("The live microphone test server did not start.")

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=10)


# Observe actual samples at the browser's media-element output before its sound
# device. This does not substitute a response or feed a fake worklet/transcript.
METER = """
window.promptMeter = {peak:0, audibleFrames:0, playSeconds:0};
window.captureUploads = [];
window.loadedWorklets = [];
const nativeAddModule = Worklet.prototype.addModule;
Worklet.prototype.addModule = async function(url, ...rest) {
  const result = await nativeAddModule.call(this,url,...rest);
  window.loadedWorklets.push(String(url));
  return result;
};
const nativeFetch = window.fetch;
window.fetch = function(url, opts) {
  const blob = opts?.body;
  if (String(url).endsWith("/transcribe") && blob instanceof Blob) {
    blob.slice(0, 44).arrayBuffer().then(header=>{
      const data = new DataView(header);
      window.captureUploads.push({bytes:blob.size, channels:data.getUint16(22,true),
          rate:data.getUint32(24,true), width:data.getUint16(34,true)/8});
    });
  }
  return nativeFetch.apply(this,arguments);
};
const nativePlay = HTMLMediaElement.prototype.play;
HTMLMediaElement.prototype.play = function(...args) {
  if (this.src.startsWith("blob:") && !this.kuboMeter) {
    this.kuboMeter = true;
    const ac = new AudioContext();
    const src = ac.createMediaElementSource(this), out = ac.createAnalyser();
    src.connect(out); out.connect(ac.destination);
    ac.resume().catch(()=>{});
    const pcm = new Float32Array(out.fftSize);
    const timer = setInterval(()=>{
      out.getFloatTimeDomainData(pcm);
      let peak = 0; for(const s of pcm) peak=Math.max(peak,Math.abs(s));
      const m=window.promptMeter;
      m.peak=Math.max(m.peak,peak); m.playSeconds=Math.max(m.playSeconds,this.currentTime);
      if(peak>.01) m.audibleFrames++;
    },25);
    const done=()=>{clearInterval(timer); ac.close().catch(()=>{});};
    this.addEventListener("ended",done,{once:true});
    this.addEventListener("pause",done,{once:true});
  }
  return nativePlay.apply(this,args);
};
"""


def pcm(line):
    with wave.open(io.BytesIO(speak(line))) as wav:
        assert (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) == (16000, 1, 2)
        return wav.readframes(wav.getnframes())


def stimulus(path):
    # Silence delimits utterances. Multiple separated yes clips accommodate the
    # different lengths of available local browser voices for the hearing check.
    answer, yes = pcm("She wanted to keep the paper dry."), pcm("Yes.")
    quiet = lambda seconds: bytes(int(16000 * seconds) * 2)
    with wave.open(str(path), "wb") as wav:
        wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        wav.writeframes(quiet(1.2) + answer + quiet(3.2) + (yes + quiet(2.4)) * 5 + quiet(8))


def wait_until(page, condition, timeout, description):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        page.wait_for_timeout(100)
    if not condition():
        raise AssertionError(f"Timed out: {description}; cube says {page.locator('#voice-state').inner_text()}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18765)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "test-results/browser-microphone")
    parser.add_argument("--allow-provider-unavailable", action="store_true",
                        help="Allow a fail-closed 503 after confirmed recognition; report it separately.")
    args = parser.parse_args()
    url = os.environ.get("TEST_DATABASE_URL", "")
    if urlparse(url).path not in ("/kubo_test", "/kubo_live_test"):
        parser.error("Set TEST_DATABASE_URL to a dedicated local test database.")
    args.output_dir.mkdir(exist_ok=True, parents=True)
    server = LiveServer(url, args.port, args.output_dir)
    transcripts, uploads, answers, worklet, errors = [], [], [], [], []
    try:
        server.start()
        with tempfile.TemporaryDirectory(prefix="kubo-adult-fixture-") as tmp, sync_playwright() as pw:
            audio_path = Path(tmp) / "adult.wav"
            stimulus(audio_path)
            browser = pw.chromium.launch(args=[
                "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream",
                f"--use-file-for-fake-audio-capture={audio_path}",
                "--autoplay-policy=no-user-gesture-required",
            ])
            phone = browser.new_context(viewport={"width":390, "height":844}, is_mobile=True, has_touch=True)
            mac = browser.new_context(permissions=["microphone"])
            mac.add_init_script(METER)
            parent, cube = phone.new_page(), mac.new_page()
            cube.on("pageerror", lambda e: errors.append(str(e)))
            def observe(r):
                path = urlparse(r.url).path
                if path == "/static/pcm-worklet.js":
                    worklet.append(r.status)
                elif path.endswith("/transcribe"):
                    if r.ok:
                        transcripts.append(r.json())
                elif path.endswith("/action") and r.request.post_data_json["kind"] == "answer":
                    body = r.request.post_data_json
                    reply = r.json()
                    answers.append({"status": r.status, "confirmed": body["confirmed"], "text": body["text"],
                                    "detail": reply.get("detail"),
                                    "phase": reply.get("session",{}).get("lesson",{}).get("phase")})
            cube.on("response", observe)
            cube.goto(server.base + "/cube")
            parent.goto(server.base + "/parent")
            parent.locator("#family-name").fill("Fictional Audio " + uuid4().hex[:8])
            parent.locator("#password").fill(secrets.token_urlsafe(24))
            parent.locator("#auth-submit").click()
            expect(parent.locator("#family-space")).to_be_visible()
            setup = parent.locator("#family-setup")
            if not setup.evaluate("(el)=>el.open"):
                setup.locator("summary").click()
            parent.locator("#nickname").fill("Robin")
            parent.get_by_role("button", name="Add child", exact=True).click()
            expect(parent.locator("#child-choice option", has_text="Robin")).to_have_count(1)
            expect(cube.locator("#pairing-code")).to_have_text(re.compile(r"[A-Z0-9]{4}-[A-Z0-9]{4}"))
            if not setup.evaluate("(el)=>el.open"):
                setup.locator("summary").click()
            parent.locator("#pairing-code").fill(cube.locator("#pairing-code").inner_text().strip())
            parent.get_by_role("button", name="Pair cube", exact=True).click()
            parent.locator("#subject-choice").select_option("english")
            parent.locator("#goal-choice").select_option("explain-a-characters-choice")
            parent.get_by_role("button", name="Save goal", exact=True).click()
            expect(parent.locator("#saved-selection")).to_contain_text("Robin")
            assert not mac.cookies() and not parent.evaluate("localStorage.getItem('kubo_device_token')")
            parent.close()
            expect(cube.locator("#start-cube")).to_be_enabled(timeout=15_000)
            # Start the microphone only when the opening audio ends so the fixed
            # stimulus begins at a known turn boundary, just as an adult would.
            cube.locator("#mic-choice").uncheck()
            cube.locator("#start-cube").click()
            expect(cube.locator("#current-question")).to_contain_text("Tess")
            expect(cube.locator("#voice-state")).to_contain_text("Microphone off ·",timeout=25_000)
            meter = cube.evaluate("window.promptMeter")
            assert meter["peak"] > .015 and meter["audibleFrames"] > 8 and meter["playSeconds"] > 1, meter
            print("PASS: actual approved opening playback sampled in Chromium:", meter, flush=True)
            cube.locator("#toggle-mic").click()
            expect(cube.locator("#heard-text")).to_contain_text("paper",timeout=35_000)
            first = cube.locator("#heard-text").inner_text()
            assert "dry" in first and any(t["text"] == first and t["confidence"] >= .65 for t in transcripts)
            uploads = cube.evaluate("window.captureUploads")
            loaded = cube.evaluate("window.loadedWorklets")
            assert "/static/pcm-worklet.js" in loaded and uploads, (loaded, uploads, worklet)
            assert all((u["channels"],u["rate"],u["width"]) == (1,16000,2) for u in uploads), uploads
            print("PASS: actual microphone worklet uploaded PCM; Vosk hearing check:", first, flush=True)
            cube.screenshot(path=str(args.output_dir/"heard-check.png"))
            wait_until(cube, lambda: any(t["text"].strip().lower() == "yes" for t in transcripts), 40, "spoken yes")
            # The provider can take longer than Vosk; no unconfirmed answer may be submitted.
            wait_until(cube, lambda: bool(answers), 160, "confirmed answer response")
            result = answers[0]
            assert result["confirmed"] is True and result["text"] == first
            print("PASS: a second genuine microphone utterance ('yes') confirmed the first answer.", flush=True)
            if result["status"] == 200:
                assert result["phase"] == "followup"
                expect(cube.locator("#phase-label")).to_contain_text("Think it through")
                result["followup"] = cube.locator("#current-question").inner_text()
                print("PASS: the visible live-model follow-up:", result["followup"], flush=True)
            else:
                assert result["status"] == 503, result
                expect(cube.locator("#phase-label")).to_contain_text("Story time")
                print("LIMIT: model rejected/unavailable (HTTP 503); opening turn unchanged.", flush=True)
            assert not errors, errors
            record = {"input": "adult Samantha WAV via Chromium fake capture, not a hardware microphone",
                      "playback": meter, "transcripts": transcripts[:2], "pcm_uploads": cube.evaluate("window.captureUploads").copy()[:2],
                      "spoken_confirmation": True, "action": result, "page_errors": errors}
            (args.output_dir/"results.json").write_text(json.dumps(record,indent=2)+"\n")
            browser.close()
            assert args.allow_provider_unavailable or result["status"] == 200, result
    finally:
        server.stop()
        server.log.close()


if __name__ == "__main__":
    main()

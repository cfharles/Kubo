"""Real Chromium acceptance test with separate phone and cube browser contexts.

Starts and restarts its own server against kubo_test; never uses the demo database.
Run: bash scripts/with-local-env.sh .venv/bin/python tests/browser_milestone.py
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import ssl
import secrets
import subprocess
import sys
import time
import tempfile
import wave
import uuid
from urllib.parse import urlparse

import httpx
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]


class Server:
    def __init__(self, url: str, port: int, log_dir: Path, tls: bool = False):
        self.env = {**os.environ, "DATABASE_URL": url, "KUBO_COOKIE_SECURE": "1" if tls else "0"}
        self.port = port
        self.tls = tls
        self.base = f"{'https' if tls else 'http'}://127.0.0.1:{port}"
        self.verify = ssl.create_default_context(cafile=str(ROOT / ".local-certs/kubo-local-ca.pem")) if tls else True
        self.log = (log_dir / "browser-server.log").open("w")
        self.process = None
        self.pids = []

    def start(self):
        tls_args = ["--ssl-certfile", str(ROOT / ".local-certs/server.pem"),
                    "--ssl-keyfile", str(ROOT / ".local-certs/server-key.pem")] if self.tls else []
        self.process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "browser_fixture:app_factory", "--factory", "--app-dir", "tests", "--host", "127.0.0.1",
             "--port", str(self.port), "--timeout-graceful-shutdown", "3", *tls_args], cwd=ROOT, env=self.env,
            stdout=self.log, stderr=subprocess.STDOUT,
        )
        self.pids.append(self.process.pid)
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError("Test server exited; inspect browser-server.log")
            try:
                response = httpx.get(self.base + "/api/health", timeout=1, trust_env=False, verify=self.verify)
                if response.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        raise RuntimeError("Test server did not become healthy")

    def stop(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
                raise RuntimeError("Test server did not shut down within ten seconds")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default=os.environ.get("TEST_DATABASE_URL"))
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "test-results")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--tls", action="store_true", help="Use local certificates and verify the CA on health checks")
    args = parser.parse_args()
    if not args.database_url or urlparse(args.database_url).path != "/kubo_test":
        parser.error("Use the dedicated kubo_test database; the demo database is protected.")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    server = Server(args.database_url, args.port, args.output_dir, tls=args.tls)
    checks = []
    browser_errors = []
    family_name = f"Maple Demo {uuid.uuid4().hex[:8]}"

    def passed(description):
        checks.append(description)
        print(f"PASS: {description}", flush=True)

    try:
        server.start()
        with sync_playwright() as p, tempfile.TemporaryDirectory() as tmp:
            quiet_input = Path(tmp) / "silence.wav"
            with wave.open(str(quiet_input), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(16000)
                wav.writeframes(bytes(2 * 16000))
            browser = p.chromium.launch(headless=not args.headed, args=[
                "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream",
                "--use-file-for-fake-audio-capture=" + str(quiet_input),
            ])
            phone = browser.new_context(viewport={"width": 390, "height": 844},
                                        is_mobile=True, has_touch=True, ignore_https_errors=args.tls)
            desktop = browser.new_context(viewport={"width": 1280, "height": 900}, ignore_https_errors=args.tls)
            phone.on("page", lambda page: page.on("pageerror", lambda error: browser_errors.append(str(error))))
            desktop.on("page", lambda page: page.on("pageerror", lambda error: browser_errors.append(str(error))))
            parent = phone.new_page()
            cube = desktop.new_page()
            parent.goto(server.base + "/parent")
            cube.goto(server.base + "/cube")
            parent.get_by_label("Family name", exact=True).fill(family_name)
            parent.get_by_label("Password", exact=True).fill(secrets.token_urlsafe(24))
            parent.get_by_role("button", name="Create family", exact=True).click()
            expect(parent.get_by_label("Child nickname", exact=True)).to_be_visible()
            for nickname, age in (("Sam", "7-8"), ("Riley", "9-10")):
                parent.get_by_label("Child nickname", exact=True).fill(nickname)
                parent.get_by_label("Age band", exact=True).select_option(age)
                parent.get_by_role("button", name="Add child", exact=True).click()
                expect(parent.get_by_label("Child", exact=True).locator("option", has_text=nickname)).to_have_count(1)

            expect(cube.locator("#pairing-code")).to_have_text(re.compile(r"[A-Z0-9-]{6,}"))
            code = cube.locator("#pairing-code").inner_text().strip()
            parent.get_by_label("Pairing code", exact=True).fill(code)
            parent.get_by_role("button", name="Pair cube", exact=True).click()
            expect(cube.get_by_role("button", name="Start cube", exact=True)).to_be_visible(timeout=15000)
            expect(cube.get_by_role("button", name="Start cube", exact=True)).to_be_enabled(timeout=15000)
            assert parent.evaluate("localStorage.getItem('kubo_device_token')") is None
            assert not desktop.cookies(), "Cube must not inherit the parent's authentication cookie"
            assert cube.evaluate("localStorage.getItem('kubo_device_token')")
            passed("Parent and cube paired with independent cookies and local storage")
            if args.tls:
                assert all(cookie["secure"] for cookie in phone.cookies())
                passed("HTTPS certificate verified against local CA; parent uses Secure cookies")

            cube.get_by_role("button", name="Start cube", exact=True).click()
            expect(cube.locator("body")).to_contain_text("Ask your parent to choose a child and learning goal.")
            passed("Starting without a saved goal asks the parent to choose one")

            parent.get_by_label("Child", exact=True).select_option(label="Sam · Ages 7–8")
            parent.get_by_label("Subject", exact=True).select_option("english")
            parent.get_by_label("Learning goal", exact=True).select_option("explain-a-characters-choice")
            parent.get_by_role("button", name="Save goal", exact=True).click()
            expect(parent.locator("#saved-selection")).to_contain_text("Sam")
            expect(parent.locator("#saved-selection")).to_contain_text("Explain a character")
            assert parent.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Phone page overflows horizontally"
            assert parent.evaluate("document.querySelector('#family-heading').getBoundingClientRect().top >= document.querySelector('.parent-header').getBoundingClientRect().bottom"), "Family heading overlaps the header"
            parent.screenshot(path=str(args.output_dir / "parent-saved-goal.png"), full_page=True)
            parent.close()
            # Drop the HTTP response after the real server commits, as with a broken Wi-Fi link.
            # The browser must write its intent before POST and recover the original receipt.
            cube.get_by_label("Use microphone for this story").uncheck()
            lost = {}
            def lose_response(route):
                response = route.fetch()
                assert response.ok
                lost["response"] = response.json()["session"]
                route.abort("failed")
            cube.route("**/api/device/start", lose_response, times=1)
            cube.get_by_role("button", name="Start cube", exact=True).click()
            expect(cube.get_by_role("button", name="Reconnect and recover")).to_be_visible()
            start_request = cube.evaluate("JSON.parse(localStorage.getItem('kubo_pending_request'))")
            assert start_request["kind"] == "start"
            cube.reload()
            expect(cube.locator("#session-summary")).to_contain_text("Sam")
            original_id = lost["response"]["id"]
            assert cube.evaluate("localStorage.getItem('kubo_last_session')") == original_id
            assert cube.evaluate("localStorage.getItem('kubo_pending_request')") is None
            passed("Lost committed Start response; reload restored exactly that session and cleared its pending UUID")
            expect(cube.locator("#session-summary")).to_contain_text("Ages 7–8")
            expect(cube.locator("#session-summary")).to_contain_text("English")
            expect(cube.locator("#session-summary")).to_contain_text("Explain a character")
            passed("Closed parent page; cube loaded Sam, ages 7–8, English character-choice goal")

            opening = cube.locator("#current-question").inner_text()
            cube.get_by_role("button", name="Hint", exact=True).click()
            expect(cube.locator("#current-prompt")).to_contain_text("raindrop")
            expect(cube.locator("#current-question")).to_have_text(opening)
            cube.get_by_role("button", name="Repeat", exact=True).click()
            expect(cube.locator("#current-question")).to_have_text(opening)
            cube.locator("#typed-controls summary").click()
            cube.get_by_label("Test answer", exact=True).fill("To keep the drawing dry.")
            cube.route("**/api/device/session/*/action", lose_response, times=1)
            cube.get_by_role("button", name="Send test answer").click()
            expect(cube.get_by_role("button", name="Reconnect and recover")).to_be_visible()
            pending = cube.evaluate("JSON.parse(localStorage.getItem('kubo_pending_request'))")
            assert pending["kind"] == "answer" and "text" not in pending
            assert "drawing dry" not in cube.evaluate("JSON.stringify(localStorage)")
            followup = lost["response"]["lesson"]["question"]
            assert followup != opening

            server.stop()
            server.start()
            assert server.pids[0] != server.pids[1]
            cube.reload()
            expect(cube.locator("#current-question")).to_have_text(followup)
            expect(cube.locator("#session-summary")).to_contain_text("Sam")
            assert cube.evaluate("localStorage.getItem('kubo_last_session')") == original_id
            assert cube.evaluate("localStorage.getItem('kubo_pending_request')") is None
            cube.screenshot(path=str(args.output_dir / "cube-after-server-restart.png"), full_page=True)
            passed("Lost committed answer plus server restart: same child/session and exact follow-up recovered without replay")
            passed("Hint and Repeat kept the question; pending answer contained no raw child text")

            parent = phone.new_page()
            parent.goto(server.base + "/parent")
            expect(parent.locator("#saved-selection")).to_contain_text("Sam")
            expect(parent.locator("#saved-selection")).to_contain_text("Explain a character")
            passed("Reopened parent app retained sign-in and server-saved selection after restart")

            parent.get_by_label("Child", exact=True).select_option(label="Riley · Ages 9–10")
            parent.get_by_label("Subject", exact=True).select_option("science")
            parent.get_by_label("Learning goal", exact=True).select_option("choose-a-fair-test")
            parent.get_by_role("button", name="Save goal", exact=True).click()
            expect(parent.locator("#saved-selection")).to_contain_text("Riley")
            expect(parent.locator("#saved-selection")).to_contain_text("Choose a fair test")
            expect(cube.locator("#session-summary")).to_contain_text("Sam")
            assert not parent.locator("#family-setup").evaluate("e => e.open"), "Returning setup must be collapsed"
            cube.get_by_role("button", name="Turn on microphone").click()
            expect(cube.locator("#voice-state")).to_contain_text("Listening for your answer")
            cube.locator("#typed-controls summary").click()
            cube.get_by_label("Test answer").fill("fixture unclear")
            cube.get_by_role("button", name="Send test answer").click()
            expect(cube.locator("#cube-message")).to_contain_text("Please give your answer again")
            expect(cube.locator("#voice-state")).to_contain_text("Listening for your answer")
            cube.get_by_label("Test answer").fill("fixture unavailable")
            cube.get_by_role("button", name="Send test answer").click()
            expect(cube.get_by_role("button", name="Retry this answer")).to_be_visible()
            expect(cube.get_by_role("button", name="Stop", exact=True)).to_be_enabled()
            cube.get_by_role("button", name="Retry this answer").click()
            expect(cube.locator("#cube-message")).to_contain_text("The tutor is unavailable")
            expect(cube.locator("#current-question")).to_have_text(followup)
            cube.get_by_role("button", name="Turn off microphone").click()
            passed("Clarification re-armed the enabled microphone; a tutor outage kept this question with Retry, Repeat and Stop")
            cube.get_by_label("Test answer").fill("The coat was wet.")
            cube.get_by_role("button", name="Send test answer").click()
            expect(cube.locator("#current-question")).to_contain_text("wet coat")
            cube.get_by_role("button", name="No, try another answer").click()
            expect(cube.locator("#current-question")).to_contain_text("Tell me what I missed")
            cube.get_by_label("Test answer").fill("The raindrop could make the paper tear.")
            cube.get_by_role("button", name="Send test answer").click()
            expect(cube.locator("#current-question")).to_contain_text("paper that could tear")
            cube.get_by_role("button", name="Yes, that’s what I meant").click()
            expect(cube.locator("#cube-status")).to_have_text("Session ended")
            cube.reload()
            expect(cube.get_by_role("button", name="Start another session")).to_be_visible()
            expect(cube.locator("#cube-status")).to_have_text("Session ended")

            parent.get_by_role("button", name="Refresh notes").click()
            expect(parent.locator("#report-list")).to_contain_text("No notes yet")
            parent.get_by_label("Child", exact=True).select_option(label="Sam · Ages 7–8")
            expect(parent.locator("#report-list")).to_contain_text("paper that could tear")
            expect(parent.locator("#report-list")).not_to_contain_text("wet coat")
            expect(parent.locator("#report-list")).to_contain_text("Try at home")
            expect(parent.locator("#report-list")).to_contain_text("Next review")
            expect(parent.locator("#report-list")).to_contain_text("Yes")
            assert parent.locator("#report-list article").count() == 1
            passed("Rejected observation excluded, confirmed note visible only for Sam with hint, home question and next review")
            passed("Ended state survives reload; returning parent sees goal first, setup collapsed")
            # Explicitly save Sam again; the confirmed report should shape the next story.
            parent.get_by_label("Subject", exact=True).select_option("english")
            parent.get_by_label("Learning goal", exact=True).select_option("explain-a-characters-choice")
            parent.get_by_role("button", name="Save goal").click()
            expect(parent.locator("#saved-selection")).to_contain_text("Sam")
            cube.get_by_role("button", name="Start another session").click()
            expect(cube.locator("#session-title")).to_have_text("The narrow path")
            expect(cube.locator("#current-question")).to_contain_text("Think about what Ben heard")
            expect(cube.locator("#session-summary")).to_contain_text("Sam")
            cube.locator("#typed-controls summary").click()
            cube.get_by_label("Test answer").fill("slow turn")
            is_answer = lambda response: (response.url.endswith("/action") and
                                           response.request.post_data_json["kind"] == "answer")
            with cube.expect_response(is_answer, timeout=10000) as late:
                cube.get_by_role("button", name="Send test answer").click()
                expect(cube.get_by_role("button", name="Stop", exact=True)).to_be_enabled()
                cube.get_by_role("button", name="Stop", exact=True).click()
                expect(cube.locator("#cube-status")).to_have_text("Session ended")
            assert late.value.status == 409
            expect(cube.locator("#cube-status")).to_have_text("Session ended")
            passed("Next visit used a different story and prior hint; Stop beat a pending model answer and remained ended")
            parent.get_by_label("Child", exact=True).select_option(label="Riley · Ages 9–10")
            parent.get_by_label("Subject", exact=True).select_option("science")
            parent.get_by_label("Learning goal", exact=True).select_option("choose-a-fair-test")
            parent.get_by_role("button", name="Save goal").click()
            expect(parent.locator("#saved-selection")).to_contain_text("Riley")
            parent.close()
            cube.get_by_role("button", name="Start another session").click()
            expect(cube.locator("#session-summary")).to_contain_text("Riley")
            expect(cube.locator("#session-summary")).to_contain_text("Ages 9–10")
            expect(cube.locator("#session-summary")).to_contain_text("Science")
            expect(cube.locator("#session-summary")).to_contain_text("Choose a fair test")
            passed("Changing child and goal preserves the active snapshot and applies on next cube start")
            assert not browser_errors, browser_errors
            passed("No uncaught browser errors; parent layout fits a 390px phone viewport")
            browser.close()
        report = {"status": "passed", "checks": checks, "family": family_name,
                  "server_processes": server.pids, "database": "kubo_test",
                  "browser": "Chromium", "contexts": "independent phone and desktop",
                  "https": args.tls, "browser_local_ca_exception": args.tls,
                  "physical_phone_tested": False, "tutor": "offline browser test double", "voice_input_tested": False}
        (args.output_dir / "browser-results.json").write_text(json.dumps(report, indent=2) + "\n")
    finally:
        server.stop()
        server.log.close()


if __name__ == "__main__":
    main()

# KUBO — connect the parent app and cube

Section 8, step 1 of the software MVP plan: a parent saves a child and learning goal on their phone, closes the app, and a separately paired cube simulator loads that choice on the Mac. PostgreSQL retains the choice, pairing, and parent sign-in after restarting the Python server.

This repository started empty. The implementation uses FastAPI, PostgreSQL, and plain HTML/CSS/JavaScript. No paid API, AI service, microphone, or speech setup is needed for this milestone. The simulator displays the loaded session; spoken lessons and learning reports belong to later milestones. Use fictional family details.

## Run on your Mac

Requires Python 3.12+, Docker with Compose, and a running Docker engine (this Mac uses Colima). From the repository root (this Mac’s existing checkout is shown below):

```bash
cd /Users/charlesferragu/Documents/Codex/2026-11-07/open/Kubo
# If your Docker engine is stopped: colima start
python3 scripts/init-local-env.py
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
docker compose up -d --wait
bash scripts/run.sh
```

Open `http://localhost:8000/cube` on the Mac. For the local connection-only demo, open `http://YOUR_MAC_WIFI_IP:8000/parent` on the phone, on the same Wi-Fi. Find the address with `ipconfig getifaddr en0`; if Wi-Fi uses another interface, use the address shown in System Settings → Wi-Fi → Details. **Phone localhost refers to the phone.**

The server binds to `0.0.0.0` for phone access. PostgreSQL binds only to Mac loopback port `55432`. Data lives in Docker volume `kubo-step1_kubo_pgdata`. The first setup generates a private password in the ignored `.env`; keep that file with this checkout when reusing its database volume. Do not regenerate it for an existing volume. Stopping/restarting the server or `docker compose down` keeps data; do not use `docker compose down -v` when you want to keep the family.

## HTTPS on the phone and Mac

The plan calls for a trusted HTTPS address on both devices. The included script creates a local certificate authority and a 30-day server certificate for localhost and your current Wi-Fi IP. It does not modify device trust automatically.

Stop the HTTP server with Ctrl+C first if it is running. Then:

```bash
cd /Users/charlesferragu/Documents/Codex/2026-11-07/open/Kubo
bash scripts/make-local-cert.sh "$(ipconfig getifaddr en0)"
KUBO_PORT=8443 \
KUBO_TLS_CERT=.local-certs/server.pem \
KUBO_TLS_KEY=.local-certs/server-key.pem \
bash scripts/run.sh
```

Trust `.local-certs/kubo-local-ca.cer` on your test devices:

- **Mac:** open the certificate in Keychain Access, add it to the login keychain, then open it and set Trust → When using this certificate → Always Trust. Alternatively run `security add-trusted-cert -r trustRoot -k "$HOME/Library/Keychains/login.keychain-db" .local-certs/kubo-local-ca.cer` from the repository.
- **iPhone:** AirDrop only `kubo-local-ca.cer` to the phone, install the downloaded profile in Settings → General → VPN & Device Management, then enable it under Settings → General → About → Certificate Trust Settings.
- **Android:** transfer only that `.cer` file and install it as a CA certificate in the device's security/credential settings; the exact menu name varies by version.

Keep the CA and server **private key** files on this Mac. All certificate files are Git-ignored. The public `.cer` is the only file to transfer. Remove this demo CA from device trust settings when finished. If your Wi-Fi IP changes, rerun the certificate script with the new IP; it reuses the same CA.

Use `https://YOUR_MAC_WIFI_IP:8443/cube` on the Mac and `https://YOUR_MAC_WIFI_IP:8443/parent` on the phone. `https://localhost:8443/cube` also works on the Mac. HTTP and HTTPS have separate browser storage, so pair again after changing address or protocol. HTTPS mode enables Secure parent cookies.

## Repeat the acceptance test

1. Open `/cube` on the Mac. It creates its own device identity and shows a ten-minute, single-use pairing code.
2. In a separate browser/session on the phone, open `/parent`. Create a fictional family such as **Maple Family** with a demo password of at least eight characters.
3. Add **Sam**, ages **7–8**, and optionally **Riley**, ages **9–10**.
4. Enter the Mac cube's code in the phone's **Pairing code** field and press **Pair cube**. The Mac should show the family as paired.
5. Select **Sam → English → Explain a character’s choice**, then **Save goal**. Wait for the server-saved confirmation naming Sam and that goal.
6. Close the phone tab. On the Mac, press **Start cube**. The loaded session must show Sam, ages 7–8, English, and the character-choice goal.
7. Stop the Python server with **Ctrl+C** and rerun the same server command. Keep the database running. Reload the cube and press **Start cube** again. It must still show Sam and that goal without pairing again.
8. Reopen the parent app at the same address. Its sign-in and saved selection remain. Save **Riley → Science → Choose a fair test** while the cube's Sam session is displayed. Sam's displayed session stays unchanged. Press **End session**, then **Start cube**; the new session must show Riley and the Science goal.

For a Mac-only repeat, use two different browser profiles or browsers (for example Chrome for the parent and Safari for the cube). Two ordinary tabs in one profile are not independent browser sessions.

If a code expires, press **Refresh code** on the cube. It invalidates the old code. A cube with no saved goal asks the parent to choose one. If a page reports that the server is unavailable, restart it and retry. If the phone cannot connect, check same Wi-Fi, Mac firewall access, VPN, and whether the network blocks communication between devices.

## Automated checks

The tests use the separate `kubo_test` database. API tests clear only that test database; the browser acceptance test creates uniquely named fictional families there. Neither touches the `kubo` demo database.

```bash
cd /Users/charlesferragu/Documents/Codex/2026-11-07/open/Kubo
.venv/bin/python -m pip install -r requirements-dev.txt
PLAYWRIGHT_BROWSERS_PATH=.playwright-browsers .venv/bin/python -m playwright install chromium
docker compose up -d --wait
bash scripts/create-test-db.sh
bash scripts/with-local-env.sh .venv/bin/python -m pytest -q tests/test_api.py
bash scripts/with-local-env.sh .venv/bin/python tests/browser_milestone.py --output-dir test-results/http
# After generating the certificates above:
bash scripts/with-local-env.sh .venv/bin/python tests/browser_milestone.py --tls --output-dir test-results/https
```

For an independent checkout running alongside the normal demo, run `python3 scripts/init-local-env.py --db-port 55433 --project kubo-checkpoint-test` **before** the first setup in that checkout. This creates a separate Compose container, volume and port.

The browser test runs Chromium in two isolated contexts: a 390px phone and a desktop cube. It closes the parent page, terminates the server process, starts a new process, and verifies the same pairing and goal. It also switches child and subject, verifies session snapshots, checks for horizontal mobile overflow, and checks uncaught browser errors. JSON results and screenshots are saved in the requested output directory. Add `--headed` to watch the test.

For HTTPS, the test verifies the server certificate against the generated CA via its health client. Isolated Chromium contexts allow that local test certificate without installing trust into macOS. Physical iPhone/Android trust and network reachability still need the manual phone repeat above.

## Implementation map

- `app/main.py`: API, family and device authentication, same-origin protection, static pages.
- `app/schema.sql`: families, parent sessions, children, devices, expiring pairing codes, saved selection, and immutable session snapshots.
- `app/catalog.py`: the plan's eight goal choices; no lesson content yet.
- `web/parent.html` and `web/parent.js`: mobile parent interface.
- `web/cube.html` and `web/cube.js`: independent simulated device.
- `tests/test_api.py`: PostgreSQL authorization, pairing, validation, persistence, and concurrency checks.
- `tests/browser_milestone.py`: repeatable independent-browser demonstration with process restart.

Parent authentication uses an HttpOnly SameSite cookie. The cube stores only its opaque device token in its own browser storage. Passwords are salted PBKDF2 hashes, and session/device/pairing tokens are stored as hashes. The database owns the chosen child/goal; the cube does not depend on the parent tab, shared local storage, or a goal embedded in a URL. Each Start stores a fixed copy of the selected child, subject, and goal, so later parent changes apply to the next session.

Configuration: `DATABASE_URL` overrides the local database; `KUBO_HOST` and `KUBO_PORT` change the bind address and port; `KUBO_TLS_CERT` and `KUBO_TLS_KEY` enable HTTPS. This is a local fictional-data milestone, not a public deployment or child-trial release.

# KUBO on your Mac and phone

A parent pairs a simulated cube to a fictional family, chooses a child and a goal on the phone, and closes the tab. The cube reads the saved choice from PostgreSQL. **English → Explain a character’s choice, ages 7–8** opens a spoken lesson. The other catalog choices still load their saved child and goal.

The first English story follows Tess and her drawing. A live model hears about the learner’s reasoning through a checked transcript, asks one follow-up, and proposes an observation. The learner can correct that interpretation before the parent receives a session note. The next visit uses a different story about Ben and his wagon. An earlier hint or an incomplete explanation gives the learner more support on the return visit.

## First setup

On this Mac the repository is at `/Users/charlesferragu/Documents/Codex/2026-11-07/open/Kubo`. From another checkout, substitute its path. You need macOS with the English Samantha voice, Python 3.12 or newer, Docker Compose with a running Docker engine, and Homebrew for the local model server. Setup downloads the speech and language models once. All demo speech and inference can then run locally.

```bash
cd /Users/charlesferragu/Documents/Codex/2026-11-07/open/Kubo
# If using Colima and Docker is stopped:
# colima start
python3 scripts/init-local-env.py
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
bash scripts/setup-speech.sh
brew install llama.cpp
docker compose up -d --wait
bash scripts/make-local-cert.sh "$(ipconfig getifaddr en0)"
```

The existing checkout keeps its private `.env` and database volume. The first run of a new checkout generates a random PostgreSQL password. Keep that checkout’s `.env` when reusing the volume. PostgreSQL listens on Mac loopback port 55432. Restarting Python or stopping Compose with `docker compose down` preserves the data. Removing the Docker volume erases it.

### Trust the public certificate

On iPhone, AirDrop only `.local-certs/kubo-local-ca.cer` from this checkout. Install the downloaded profile in **Settings → General → VPN & Device Management** and enable its root under **General → About → Certificate Trust Settings**. On Android, install the public file as a CA certificate in Security settings. On the Mac, open it in Keychain Access, add it to the login keychain and set it to **Always Trust** for this local demo. Keep the private keys on your Mac. You can remove the local CA from each device when finished.

### Start the local model and app

**Terminal 1 — local tutor.** Leave this running. The first launch fetches the pinned, checksum-verified Qwen2.5-7B-Instruct Q4_K_M weights (4.68 GB total) into the ignored `models/` directory.

```bash
cd /Users/charlesferragu/Documents/Codex/2026-11-07/open/Kubo
bash scripts/run-local-tutor.sh
```

**Terminal 2 — web and speech server.** The health check should return `{"status":"ok"}` once the model has loaded.

```bash
cd /Users/charlesferragu/Documents/Codex/2026-11-07/open/Kubo
curl --fail http://127.0.0.1:8081/health
docker compose up -d --wait
export KUBO_LLM_MODE=local
export KUBO_PORT=8444
export KUBO_TLS_CERT=.local-certs/server.pem
export KUBO_TLS_KEY=.local-certs/server-key.pem
bash scripts/run.sh
```

On the Mac open **https://localhost:8444/cube**. On the phone, join the same Wi-Fi and open **https://192.168.2.54:8444/parent** (Wi-Fi address at verification). The following command prints the phone address for the current network.

```bash
printf 'https://%s:8444/parent\n' "$(ipconfig getifaddr en0)"
```

If Wi-Fi uses a different interface, use the address from **System Settings → Wi-Fi → Details**. When the address changes, rerun the certificate script with that IP and restart the app. For microphone access use trusted HTTPS or Mac localhost. Keep the same browser profile and cube origin across restarts. To restart only Python, press **Control-C in Terminal 2** and run **`bash scripts/run.sh`** in that same terminal.

**Hosted option.** To use OpenAI instead of the local model, set `KUBO_LLM_MODE=hosted`, `KUBO_LLM_MODEL=gpt-4o-mini` and `OPENAI_API_KEY` in the Mac server process. In the default Mac zsh, use the following from Terminal 2 before starting Python. The key is entered without echo or shell history. No keys belong in the browser, a URL or Git. Hosted mode sends the minimized story and checked reasoning to the provider.

```zsh
export KUBO_LLM_MODE=hosted KUBO_LLM_MODEL=gpt-4o-mini
read -rs 'OPENAI_API_KEY?OpenAI API key: '
echo
export OPENAI_API_KEY
bash scripts/run.sh
```

## Phone acceptance test and spoken visit

1. On the Mac open the cube. On the phone create a fictional family with a demo password. Under **Family setup**, add **Sam**, ages **7–8**, and **Riley**. Enter the cube’s ten-minute pairing code and tap **Pair cube**.
2. At the top of the parent page select **Sam → English → Explain a character’s choice → Save goal**. Wait for the confirmation naming Sam, then close the phone tab.
3. On the Mac press **Start cube** and allow the microphone. Check Sam and the English goal. The cube speaks the story and opening question. An adult can answer “She felt a raindrop. She wanted to keep the paper dry.” Check the recognized words before using the answer. Listen for the tutor’s follow-up and answer it. Use **Repeat**, **Hint** or **Stop**, or say the command with the microphone enabled. If a short “hint” is unclear, try “give me a hint” or press **Hint**. The Adult testing control is a keyboard alternative.
4. To check recovery, restart Python while Sam still has a question. Reload the Mac cube at the same address. It should restore **the same Sam session and question** without pairing again. Turn the mic on again if prompted. On the phone, reopen the same parent address, save **Riley → Science → Choose a fair test** and check that Sam’s current session stays intact.
5. Answer Sam’s follow-up. When asked whether KUBO understood, choose **No** to correct the interpretation, or **Yes** to finish. On the phone select Sam and read the session note. It shows a specific observation, whether a hint was used, and a question to ask at home. Stopped or unconfirmed visits record their limited evidence.
6. Start another session on the Mac. It should load **Riley and the Science goal**. End it. On the phone select and save **Sam and the English goal** again. Starting the cube now gives Sam a **fresh story**, with an opening scaffold if earlier evidence called for one.

For the preserved **checkpoint 1** phone test, use its separate clean checkout and port 8443. Its local CA and database are separate. Keep that test’s parent browser profile separate if using both builds on the same phone. The physical phone test is recorded by the person using the phone.

## Repeat the automated and live tests

Run these serially from the repository root. The PostgreSQL API suite cleans the dedicated `kubo_test` database; the browser suites create fresh fictional families there. They do not use the normal `kubo` database. To run a second checkout on the same Mac, use `python3 scripts/init-local-env.py --db-port 55435 --project kubo-independent` in the fresh checkout **before** setup; its volume, password, and loopback port will be independent.

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
PLAYWRIGHT_BROWSERS_PATH=.playwright-browsers .venv/bin/python -m playwright install chromium
docker compose up -d --wait
bash scripts/create-test-db.sh
bash scripts/with-local-env.sh .venv/bin/python -m pytest -q
bash scripts/with-local-env.sh .venv/bin/python tests/browser_milestone.py --output-dir test-results/http
bash scripts/with-local-env.sh .venv/bin/python tests/browser_milestone.py --tls --output-dir test-results/https
```

With the local tutor and speech models ready, run the comparison below. It synthesizes adult answers and passes them through the real speech and API handlers for two first visits and one return. The demonstration checks the exact words before submission and prints the actual generated questions and parent reports. Generated wording can vary. A physical phone or microphone is a separate test.

```bash
KUBO_LLM_MODE=local bash scripts/with-local-env.sh .venv/bin/python -m tests.live_lesson
KUBO_LLM_MODE=local bash scripts/with-local-env.sh .venv/bin/python tests/browser_microphone.py
```

The second command plays an adult-voice WAV into Chromium’s virtual microphone, captures it with the production AudioWorklet, measures the approved story audio in the browser, and checks the heard-words confirmation and live follow-up. On a restored active session, use **Repeat** and **Turn on microphone** to resume by voice.

## How recovery and speech work

- **Source of truth.** PostgreSQL stores families, hashed parent and device credentials, pairings, saved selections, immutable session selections, the current approved turn, revision, end time and parent report. Startup applies versioned migrations inside a transaction. Milestone 1 snapshots are preserved as historical sessions, since the old client never saved an end event.
- **Lost replies.** The cube assigns a UUID before Start or a turn. One successful command and its reply commit together. Retrying the same command retrieves that result, even if the parent changed children. A new Start can open the next child’s lesson after the active session ends. A mismatched payload with a reused key, a stale revision and another device’s session are rejected.
- **Speech.** The browser captures mono PCM; the Mac transcribes each bounded clip locally with Vosk. macOS Samantha synthesizes approved prompts. The user can retry misheard words. The lesson separates recognizing words from confirming the meaning of an explanation.
- **Model and learning record.** A generating call proposes a question or observation. A separate review call checks it against the story, the learner’s contribution and safety constraints. Both calls use the configured model. One rejected draft may be replaced. A second rejection or a provider outage pauses the turn so the learner can retry or stop. PostgreSQL stores the checked interpretation and any confirmed report. Microphone recordings and full submitted answers are transient.
- **Source map.** `app/lesson.py` owns the two stories, controls and completion. `app/sessions.py` owns receipts and revision checks. `app/migrations/` upgrades the database. `app/tutor.py` and `app/speech.py` connect the model and speech engines. `web/parent.*`, `web/cube.*` and `web/voice.js` implement the two clients. Database credentials, private keys, certificates, models, screenshots and local test results are ignored by Git.

This build is for fictional family profiles and adult testing on a local network.

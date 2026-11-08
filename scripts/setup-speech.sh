#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
.venv/bin/python -m pip install -r requirements-speech.txt
name=vosk-model-small-en-us-0.15
if [[ ! -f "models/$name/am/final.mdl" ]]; then
  mkdir -p models
  stage=$(mktemp -d models/.vosk-install.XXXXXXXX)
  trap 'rm -rf "$stage"' EXIT
  curl --fail --show-error --silent --location --proto '=https' --proto-redir '=https' \
    --retry 2 "https://alphacephei.com/vosk/models/$name.zip" -o "$stage/model.zip"
  .venv/bin/python - "$stage/model.zip" <<'PY'
import hashlib, sys
with open(sys.argv[1], "rb") as f:
    if hashlib.file_digest(f, "sha256").hexdigest() != "30f26242c4eb449f948e42cb302dd7a686cb29a3423a8367f99ff41780942498":
        sys.exit("English model download did not match its expected SHA-256.")
PY
  unzip -q "$stage/model.zip" -d "$stage"
  mv "$stage/$name" "models/$name"
fi
echo "English speech recognition is ready in models/$name. macOS speech uses Samantha."

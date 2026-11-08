#!/bin/bash
# Start the Mac-only local inference service. Weights are pinned and kept off Git.
set -euo pipefail
export LC_ALL=C
cd "$(dirname "$0")/.."
if ! command -v llama-server >/dev/null; then
    echo 'First install llama.cpp with: brew install llama.cpp' >&2
    exit 1
fi
mkdir -p models
revision=bb5d59e06d9551d752d08b292a50eb208b07ab1f
model=qwen2.5-7b-instruct-q4_k_m
checksums=(
    dfce12e3862a5283ccfb88221b48480e58745165de856439950d0f22590580db
    539cf93f78e887edea1c04e2d7d8cdaca9d01dae9c9025bcb8accbe29df3d72a
)
for index in 0 1; do
    name="$model-0000$((index + 1))-of-00002.gguf"
    path="models/$name"
    if [[ ! -f "$path" ]]; then
        partial=$(mktemp "models/.qwen-download.XXXXXX")
        trap 'rm -f "$partial"' EXIT
        curl --fail --location --proto '=https' --proto-redir '=https' --retry 2 \
            --output "$partial" "https://huggingface.co/Qwen/Qwen2.5-7B-Instruct-GGUF/resolve/$revision/$name"
        printf '%s  %s\n' "${checksums[$index]}" "$partial" | shasum -a 256 -c -
        mv "$partial" "$path"
    else
        printf '%s  %s\n' "${checksums[$index]}" "$path" | shasum -a 256 -c -
    fi
done
echo 'Starting Qwen2.5 7B on http://127.0.0.1:8081. Leave this terminal open.'
exec llama-server -m "models/$model-00001-of-00002.gguf" --alias kubo-local \
    --host 127.0.0.1 --port 8081 --ctx-size 4096 --parallel 1 --log-disable

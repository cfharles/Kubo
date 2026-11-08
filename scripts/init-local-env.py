"""Generate per-checkout credentials once, before starting Docker Compose."""
import argparse
import os
from pathlib import Path
import re
import secrets

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--db-port", type=int, default=55432)
parser.add_argument("--project", default="kubo-step1")
args = parser.parse_args()
if not 1024 <= args.db_port <= 65535 or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", args.project):
    parser.error("Choose a port between 1024 and 65535 and a lowercase Compose project name.")

path = Path(__file__).resolve().parents[1] / ".env"
try:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
except FileExistsError:
    print("Keeping this checkout's existing .env and database credentials.")
else:
    with os.fdopen(fd, "w") as config:
        config.write(f"KUBO_DB_PASSWORD={secrets.token_hex(32)}\n")
        config.write(f"KUBO_DB_PORT={args.db_port}\nCOMPOSE_PROJECT_NAME={args.project}\n")
    print(f"Initialized .env for Compose project {args.project} on port {args.db_port}.")

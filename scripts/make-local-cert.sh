#!/bin/bash
# Creates a local CA and a short-lived server certificate. Never installs trust.
set -euo pipefail
cd "$(dirname "$0")/.."
lan_ip="${1:-$(ipconfig getifaddr en0 2>/dev/null || true)}"
if [[ ! "$lan_ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "Usage: bash scripts/make-local-cert.sh YOUR_MAC_WIFI_IPV4" >&2
  exit 1
fi
cert_dir=.local-certs
mkdir -p "$cert_dir"
chmod 700 "$cert_dir"
umask 077
if [[ ! -f "$cert_dir/kubo-local-ca.pem" ]]; then
  openssl req -x509 -newkey rsa:2048 -nodes -sha256 -days 365 \
    -keyout "$cert_dir/kubo-local-ca-key.pem" \
    -out "$cert_dir/kubo-local-ca.pem" \
    -subj '/CN=KUBO Local Demo CA' \
    -addext 'basicConstraints=critical,CA:TRUE' \
    -addext 'keyUsage=critical,keyCertSign,cRLSign'
fi
openssl req -newkey rsa:2048 -nodes \
  -keyout "$cert_dir/server-key.pem" -out "$cert_dir/server.csr" \
  -subj '/CN=localhost'
cat > "$cert_dir/server.ext" <<EOF
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=DNS:localhost,IP:127.0.0.1,IP:$lan_ip
EOF
openssl x509 -req -in "$cert_dir/server.csr" \
  -CA "$cert_dir/kubo-local-ca.pem" -CAkey "$cert_dir/kubo-local-ca-key.pem" \
  -CAcreateserial -out "$cert_dir/server.pem" -days 30 -sha256 \
  -extfile "$cert_dir/server.ext"
openssl x509 -in "$cert_dir/kubo-local-ca.pem" -outform der -out "$cert_dir/kubo-local-ca.cer"
echo "Certificate ready for https://$lan_ip:8443 and https://localhost:8443"
echo "Trust only .local-certs/kubo-local-ca.cer on your test devices. Keep the private key files on this Mac."

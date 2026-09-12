#!/usr/bin/env bash
# P2-C7: generate local development mTLS certificates.
# Production should use an internal PKI or cert-manager-managed certificates.

set -euo pipefail
umask 077

OUT=${1:-./certs}
mkdir -p "$OUT"
cd "$OUT"

# 1) CA
openssl genrsa -out ca.key 4096
openssl req -x509 -new -nodes -key ca.key -sha256 -days 1825 \
    -subj "/CN=Koipa Dev CA" -out ca.crt

# 2) Server
openssl genrsa -out server.key 4096
openssl req -new -key server.key -out server.csr \
    -subj "/CN=api.koipa.local"
cat > server.ext <<EOF
authorityKeyIdentifier=keyid,issuer
basicConstraints=CA:FALSE
keyUsage = digitalSignature, nonRepudiation, keyEncipherment, dataEncipherment
subjectAltName = @alt_names
[alt_names]
DNS.1 = api.koipa.local
DNS.2 = localhost
EOF
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
    -out server.crt -days 825 -sha256 -extfile server.ext

# 3) KL client
openssl genrsa -out kl-client.key 4096
openssl req -new -key kl-client.key -out kl-client.csr \
    -subj "/CN=kl-client/O=Korean Intellectual Property Agency"
openssl x509 -req -in kl-client.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
    -out kl-client.crt -days 825 -sha256

echo "Done. Files in $OUT:"
ls -la
echo ""
echo "Server certs: server.crt + server.key"
echo "Client (KL):  kl-client.crt + kl-client.key"
echo "CA root:      ca.crt (deploy/share this root only)"

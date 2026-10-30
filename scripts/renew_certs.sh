#!/bin/bash
# Renews the Tailscale-issued Let's Encrypt cert used by Caddy (Nextcloud, ntfy, Home Assistant).
# Run as root (it talks to tailscaled and writes root-owned key material). See docs/cert-rotation.md.
#
# Safe to run as often as you like: `tailscale cert` only gets a new cert from Let's Encrypt when the
# cached one is near expiry, and Caddy is only reloaded if the cert on disk actually changed.
set -euo pipefail

if [ -f /home/cormac/docker/.env ]; then
    set -a
    # shellcheck disable=SC1091
    source /home/cormac/docker/.env
    set +a
fi

DOMAIN="${TAILSCALE_WOLFETONE_DOMAIN:?TAILSCALE_WOLFETONE_DOMAIN not set}"
CERT_DIR="${NC_COMPOSE_DIR:?NC_COMPOSE_DIR not set}/certs"
CADDY_CONTAINER="${CADDY_CONTAINER:-nextcloud-caddy-1}"
WARN_DAYS=14

logit() { echo "$(date '+%Y-%m-%d %H:%M:%S') $1"; }

notify() {
    curl -k -sS --max-time 5 \
         -H "Title: Wolfetone Certificates" \
         -d "$1" \
         "${TAILSCALE_WOLFETONE_URL}:8081/backups" || true
}

fingerprint() { openssl x509 -in "$1" -noout -fingerprint -sha256 2>/dev/null || true; }

cd "$CERT_DIR"
rm -f wolfetone.crt.new wolfetone.key.new
trap 'rm -f wolfetone.crt.new wolfetone.key.new' EXIT

logit "Requesting certificate for $DOMAIN (renews only if near expiry)..."
if ! tailscale cert --cert-file wolfetone.crt.new --key-file wolfetone.key.new "$DOMAIN"; then
    logit "ERROR: tailscale cert failed"
    notify "Certificate renewal FAILED. Check HTTPS certs are enabled in the Tailscale admin console and run scripts/renew_certs.sh as root."
    exit 1
fi

# The new cert must be valid for the right name for at least a week and match the new key.
openssl x509 -in wolfetone.crt.new -noout -checkend $((7 * 86400)) >/dev/null \
    || { logit "ERROR: new cert is expired or about to"; notify "Certificate renewal produced an unusable cert."; exit 1; }
openssl x509 -in wolfetone.crt.new -noout -checkhost "$DOMAIN" | grep -q "does match" \
    || { logit "ERROR: new cert does not match $DOMAIN"; notify "Certificate renewal produced a cert for the wrong name."; exit 1; }
[ "$(openssl x509 -in wolfetone.crt.new -noout -pubkey | sha256sum)" = "$(openssl pkey -in wolfetone.key.new -pubout | sha256sum)" ] \
    || { logit "ERROR: new cert and key do not match"; notify "Certificate renewal produced a mismatched cert/key."; exit 1; }

if [ "$(fingerprint wolfetone.crt.new)" = "$(fingerprint wolfetone.crt)" ]; then
    logit "Certificate unchanged; nothing to do."
else
    chown root:root wolfetone.crt.new wolfetone.key.new
    chmod 644 wolfetone.crt.new
    chmod 600 wolfetone.key.new
    mv wolfetone.crt.new wolfetone.crt
    mv wolfetone.key.new wolfetone.key
    # Caddy does not re-read cert files on a plain reload of an unchanged Caddyfile, hence --force.
    docker exec "$CADDY_CONTAINER" caddy reload --force --config /etc/caddy/Caddyfile
    EXPIRY=$(openssl x509 -in wolfetone.crt -noout -enddate | cut -d= -f2)
    logit "Certificate renewed; valid until $EXPIRY. Caddy reloaded."
    notify "Certificate renewed (valid until $EXPIRY)."
fi

if ! openssl x509 -in wolfetone.crt -noout -checkend $((WARN_DAYS * 86400)) >/dev/null; then
    logit "WARNING: certificate on disk expires within $WARN_DAYS days"
    notify "Warning: HTTPS certificate expires within $WARN_DAYS days and renewal did not replace it."
fi

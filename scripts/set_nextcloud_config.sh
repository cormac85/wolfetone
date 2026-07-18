#!/usr/bin/env bash
# Description: Enforces reverse proxy and trust configurations for Nextcloud via occ.
# Execution: Run with sudo from the Docker host.

set -e

CONTAINER_NAME="nextcloud-app"
DOMAIN="wolfetone.tailee21f7.ts.net"

echo "Applying Nextcloud configuration to $CONTAINER_NAME..."

# Helper function to execute occ commands as the web user
occ_set() {
    docker exec --user www-data "$CONTAINER_NAME" php occ config:system:set "$@"
}

occ_delete() {
    docker exec --user www-data "$CONTAINER_NAME" php occ config:system:delete "$@"
}

# 1. Enforce HTTPS Overwrites
echo "Configuring protocol overwrites..."
occ_set overwritehost --value="$DOMAIN"
occ_set overwriteprotocol --value="https"
occ_set overwrite.cli.url --value="https://$DOMAIN"

# 2. Rebuild Trusted Domains
echo "Configuring trusted domains..."
# Delete the array to clear out legacy IPs (like .58) before rebuilding
occ_delete trusted_domains || true
occ_set trusted_domains 0 --value="$DOMAIN"

# 3. Rebuild Trusted Proxies
echo "Configuring trusted proxies..."
# Delete the array to ensure no duplicate or legacy proxy definitions exist
occ_delete trusted_proxies || true
occ_set trusted_proxies 0 --value="127.0.0.1"
occ_set trusted_proxies 1 --value="::1"
occ_set trusted_proxies 2 --value="100.64.0.0/10" # Tailscale IP space
occ_set trusted_proxies 3 --value="172.16.0.0/12" # Docker Bridge IP space

echo "Configuration applied successfully. Rebuilding Nextcloud routing cache..."
docker restart "$CONTAINER_NAME"

echo "Done."

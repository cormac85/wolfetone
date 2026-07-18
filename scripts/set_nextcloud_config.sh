#!/usr/bin/env bash
# Description: Enforces reverse proxy and trust configurations for Nextcloud via occ.
# Execution: Run with sudo from the Docker host.

set -e

# 1. Load system environment variables
ENV_FILE="/etc/environment"
if [ -f "$ENV_FILE" ]; then
    # Sourcing with allexport handles spaces in values cleaner than xargs grep
    set -a
    source "$ENV_FILE"
    set +a
fi

# 2. Fallback defaults if environment variables are not set
NC_CONTAINER="${NC_CONTAINER:-nextcloud-app}"
TAILSCALE_DOMAIN=${TAILSCALE_DOMAIN:-wolfetone.tailee21f7.ts.net}

# 3. Sanity Check: Ensure the target container is actually running
if ! docker ps --format '{{.Names}}' | grep -q "^${NC_CONTAINER}$"; then
    echo "Error: Target container '$NC_CONTAINER' is not running."
    exit 1
fi

echo "Applying Nextcloud configuration to $NC_CONTAINER..."

# Helper function to execute occ commands as the web user
occ_set() {
    docker exec --user www-data "$NC_CONTAINER" php occ config:system:set "$@"
}

occ_delete() {
    docker exec --user www-data "$NC_CONTAINER" php occ config:system:delete "$@"
}

# 4. Enforce HTTPS Overwrites
echo "Configuring protocol overwrites..."
occ_set overwritehost --value="$TAILSCALE_DOMAIN"
occ_set overwriteprotocol --value="https"
occ_set overwrite.cli.url --value="https://$TAILSCALE_DOMAIN"

# 5. Rebuild Trusted Domains
echo "Configuring trusted domains..."
occ_delete trusted_domains || true
occ_set trusted_domains 0 --value="$TAILSCALE_DOMAIN"

# 6. Rebuild Trusted Proxies
echo "Configuring trusted proxies..."
# Delete the array to ensure no duplicate or legacy proxy definitions exist
occ_delete trusted_proxies || true
occ_set trusted_proxies 0 --value="127.0.0.1"
occ_set trusted_proxies 1 --value="::1"
occ_set trusted_proxies 2 --value="100.64.0.0/10" # Tailscale IP space
occ_set trusted_proxies 3 --value="172.16.0.0/12" # Docker Bridge IP space

echo "Configuration applied successfully. Rebuilding Nextcloud routing cache..."
docker restart "$NC_CONTAINER"

echo "Done."

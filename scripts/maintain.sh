#!/bin/bash
set -e

# 0. Setup and variable definitions
logit() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') $1"
}

# Define cleanup for unexpected exits or errors
cleanup() {
    logit "Maintenance script interrupted or failed. Disabling maintenance mode..."
    sudo docker exec -u www-data "$NC_CONTAINER" php occ maintenance:mode --off || true
}
trap cleanup ERR INT TERM

# Load system environment variables
if [ -f /home/cormac/docker/.env ]; then
    set -a
    source /home/cormac/docker/.env
    set +a
fi

# Notification function
notify() {
    curl -k -sS --max-time 5 \
         -H "Title: Wolfetone Maintenance" \
         -d "$1" \
         "${TAILSCALE_WOLFETONE_URL}:8081/backups"
}

logit "--- Starting Maintenance Cycle ---"

# --- 1. Secure Snapshot Isolation ---
logit "[1/5] Freezing Nextcloud application state..."
sudo docker exec -u www-data "$NC_CONTAINER" php occ maintenance:mode --on

logit "[2/5] Exporting Database Dump..."
SUFX=$(date +%F)
CURRENT_SQL_BACKUP="$NC_BACKUP_DIR/db_backup_${SUFX}.sql"
sudo docker exec -i "$NC_DB_CONTAINER" /usr/bin/mysqldump --defaults-extra-file=/etc/mysql/conf.d/nextcloud-db.cnf nextcloud > "$CURRENT_SQL_BACKUP"

# Prune local copies older than 3 days
find "$NC_BACKUP_DIR" -name "db_backup_*.sql" -mtime +3 -delete

# Integrity Sanity Check
if ! tail -n 20 "$CURRENT_SQL_BACKUP" | grep -q "Dump completed on"; then
    logit "CRITICAL ERROR: Database dump appears truncated! Aborting upgrade."
    notify "Backup failed: Database dump was truncated. Upgrade aborted."
    sudo docker exec -u www-data "$NC_CONTAINER" php occ maintenance:mode --off || true
    exit 1
fi

logit "[3/5] Syncing Filesystem to Staging Storage..."
sudo rsync -Aax --delete "$NC_HOST_DATA_PATH/" "$NC_BACKUP_DIR/files/"

# --- 2. Host and Container Upgrades ---
logit "[4/5] Updating Host Operating System..."
export DEBIAN_FRONTEND=noninteractive
sudo apt update && sudo apt upgrade -y

logit "[5/5] Updating Nextcloud Containers and Apps..."

# Fix ownership on custom apps before pull
sudo chown -R 33:33 "$NC_HOST_DATA_PATH/custom_apps"
sudo docker exec -u www-data "$NC_CONTAINER" php occ maintenance:data-fingerprint || true

# Temporarily disable problematic apps (safely handling errors if already disabled)
sudo docker exec -u www-data "$NC_CONTAINER" php occ app:disable calendar || true

cd "$NC_COMPOSE_DIR"
sudo docker compose pull

# Enforce full ownership on persistent data volumes
sudo chown -R 33:33 "$NC_HOST_DATA_PATH"
sudo docker compose up -d

logit "Waiting for Nextcloud to finish automatic migrations..."
MAX_RETRIES=20
COUNT=0
while [ $COUNT -lt $MAX_RETRIES ]; do
    if ! sudo docker exec -u www-data "$NC_CONTAINER" php occ maintenance:mode | grep -q "true"; then
        logit "Nextcloud migrated and exited maintenance mode automatically."
        break
    fi
    sleep 10
    COUNT=$((COUNT+1))
done

logit "Updating custom Nextcloud apps..."
if sudo docker exec -u www-data "$NC_CONTAINER" php occ app:update --all; then
    logit "Apps updated successfully."
else
    logit "WARNING: One or more apps failed to update. Check Nextcloud logs."
fi

# Re-enable disabled apps
logit "Re-enabling calendar app..."
sudo docker exec -u www-data "$NC_CONTAINER" php occ app:enable calendar || true

logit "Nextcloud application layer is fully operational."

# --- 3. Network Outbound Backup ---
logit "Initiating Borgmatic deduplication and Tailscale network transfer..."
if borgmatic create --verbosity 1 --stats; then
    logit "Borgmatic transfer completed successfully."
    notify "Maintenance complete. Systems upgraded and backed up to Datashank."
else
    logit "ERROR: Borgmatic network transfer failed."
    notify "Warning: Nextcloud upgraded successfully, but Borgmatic backup failed to complete."
fi

logit "--- Maintenance Cycle Complete ---"
logit "--- Tidying up ---"
# Disable maintenance mode explicitly if still on
sudo docker exec -u www-data "$NC_CONTAINER" php occ maintenance:mode --off || true

# Remove trap on normal exit
trap - ERR INT TERM EXIT
logit "--- Maintenance script finished successfully ---"
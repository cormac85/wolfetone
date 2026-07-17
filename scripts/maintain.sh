#!/bin/bash
set -e

# Logging function
logit() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') $1"
}

# Notification function using nextcloud's occ command to push to the user
notify() {
    # $1 = Priority (e.g., "high", "default")
    # $2 = Message text
    
    # Capitalise priority for clean reading
    local priority=$(echo "$1" | tr '[:lower:]' '[:upper:]')
    
    sudo docker exec -u www-data "$NC_CONTAINER" php occ notification:generate \
        "$NC_USER" \
        "$2" \
        -l "Priority: ${priority} | Source: wolfetone"
}
# Load system environment variables
if [ -f /etc/environment ]; then
    export $(grep -v '^#' /etc/environment | xargs)
fi

logit "--- Starting Maintenance Cycle ---"

# --- 1. Secure Snapshot Isolation ---
logit "[1/5] Freezing Nextcloud application state..."
sudo docker exec -u www-data "$NC_CONTAINER" php occ maintenance:mode --on

logit "[2/5] Exporting Database Dump..."
sudo docker exec -i "$NC_DB_CONTAINER" /usr/bin/mysqldump --defaults-extra-file=/etc/mysql/conf.d/nextcloud-db.cnf nextcloud > "$NC_BACKUP_DIR/db_backup.sql"

# Integrity Sanity Check (Check immediately after dump)
if ! tail -n 20 "$NC_BACKUP_DIR/db_backup.sql" | grep -q "Dump completed on"; then
    logit "CRITICAL ERROR: Database dump appears truncated! Aborting upgrade."
    notify "high" "Backup failed: Database dump was truncated. Upgrade aborted."
    sudo docker exec -u www-data "$NC_CONTAINER" php occ maintenance:mode --off || true
    exit 1
fi

logit "[3/5] Syncing Filesystem to Staging Storage..."
sudo rsync -Aax --delete "$NC_HOST_DATA_PATH/" "$NC_BACKUP_DIR/files/"


# --- 2. Host and Container Upgrades ---
logit "[4/5] Updating Host Operating System..."
sudo apt update && sudo apt upgrade -y

logit "[5/5] Pulling and rebuilding Nextcloud containers..."
cd "$NC_COMPOSE_DIR"
sudo docker compose pull
sudo docker compose up -d

# Execute database schema migrations while still in maintenance mode
logit "Executing Nextcloud database migrations..."
sudo docker exec -u www-data "$NC_CONTAINER" php occ upgrade

# Disable maintenance mode now that application binaries and database match
sudo docker exec -u www-data "$NC_CONTAINER" php occ maintenance:mode --off || true
logit "Nextcloud application layer is fully operational."


# --- 3. Network Outbound Backup ---
# This is placed completely outside the application lifecycle downtime window
logit "Initiating Borgmatic deduplication and Tailscale network transfer..."
if borgmatic create --verbosity 1 --stats; then
    logit "Borgmatic transfer completed successfully."
    notify "default" "Maintenance complete. Systems upgraded and backed up to Datashank."
else
    logit "ERROR: Borgmatic network transfer failed."
    notify "high" "Warning: Nextcloud upgraded successfully, but Borgmatic backup failed to complete."
fi


logit "--- Maintenance Cycle Complete ---"
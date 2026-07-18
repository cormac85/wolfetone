#!/bin/bash
set -e

# Logging function
logit() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') $1"
}

# Notification function using nextcloud's occ command to push to the user
notify() {
    curl -sS --max-time 5 \
         -H "Title: Wolfetone Maintenance" \
         -d "$1" \
         "http://100.83.211.123:8081/backups"
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
SUFX=$(date +%F)
CURRENT_SQL_BACKUP="$NC_BACKUP_DIR/db_backup_${SUFX}.sql"
sudo docker exec -i "$NC_DB_CONTAINER" /usr/bin/mysqldump --defaults-extra-file=/etc/mysql/conf.d/nextcloud-db.cnf nextcloud > "$CURRENT_SQL_BACKUP"

# Prune local copies older than 3 days to keep /mnt/backups clean
find "$NC_BACKUP_DIR" -name "db_backup_*.sql" -mtime +3 -delete

# Integrity Sanity Check (Check immediately after dump)
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

logit "[5/5] Pulling and rebuilding Nextcloud containers..."
cd "$NC_COMPOSE_DIR"
sudo docker compose pull
sudo docker compose up -d

# Give the container entrypoint script time to initialize and auto-migrate
logit "Waiting for Nextcloud container initialization..."
sleep 15

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
    notify "Maintenance complete. Systems upgraded and backed up to Datashank."
else
    logit "ERROR: Borgmatic network transfer failed."
    notify "Warning: Nextcloud upgraded successfully, but Borgmatic backup failed to complete."
fi


logit "--- Maintenance Cycle Complete ---"
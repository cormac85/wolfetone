#!/bin/bash
set -e

# Load system environment variables
if [ -f /etc/environment ]; then
    export $(grep -v '^#' /etc/environment | xargs)
fi

# Set the absolute path to the directory containing your docker-compose.yml
# UPDATE THIS PATH to match your setup
NC_COMPOSE_DIR="/home/cormac/docker/compose-stacks/nextcloud"

echo "--- Starting Maintenance ---"

# --- 1. Backup ---
echo "[1/3] Performing Snapshot..."
# Put into maintenance mode
sudo docker exec -u www-data "$NC_CONTAINER" php occ maintenance:mode --on

# Dump the database securely by passing the credential file into the container mount
sudo docker exec -i "$NC_DB_CONTAINER" /usr/bin/mysqldump --defaults-extra-file=/etc/mysql/conf.d/nextcloud-db.cnf nextcloud > "$NC_BACKUP_DIR/db_backup.sql"

# Keep only the last 5 database backups
find "$NC_BACKUP_DIR" -name "db_backup.sql*" -mtime +5 -delete

# Sync files
sudo rsync -Aax "$NC_HOST_DATA_PATH/" "$NC_BACKUP_DIR/files/"

echo "Backup complete. Saved to $NC_BACKUP_DIR"

# --- 2. System Updates ---
echo "[2/3] Updating Host System Packages..."
sudo apt update && sudo apt upgrade -y

# --- 3. Nextcloud Update ---
echo "[3/3] Pulling new images and recreating container..."
# Navigate to your docker configuration directory
cd "$NC_COMPOSE_DIR"

# Pull and update containers
sudo docker compose pull
sudo docker compose up -d

# Release the manual maintenance lock so the upgrade tool doesn't think it is blocked
# We use || true so the script doesn't fail if the lock is already released
sudo docker exec -u www-data "$NC_CONTAINER" php occ maintenance:mode --off || true

# Execute database migrations
sudo docker exec -u www-data "$NC_CONTAINER" php occ upgrade

# Ensure maintenance mode is off when complete
sudo docker exec -u www-data "$NC_CONTAINER" php occ maintenance:mode --off

echo "--- Maintenance Complete ---"

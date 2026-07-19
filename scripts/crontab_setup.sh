#!/bin/bash
set -e

echo "--- Setting up Server Cron Jobs ---"

# Define the jobs
ROOT_JOB="0 23 * * 0 /home/cormac/docker/scripts/maintain.sh >> /var/log/nextcloud-maintain.log 2>&1"
USER_JOB="*/5 * * * * docker exec -u www-data nextcloud-app php -f /var/www/html/cron.php"

# 1. Setup Root Cron (Maintenance)
echo "Checking root crontab for maintenance script..."
if sudo crontab -l 2>/dev/null | grep -Fq "/home/cormac/docker/scripts/maintain.sh"; then
    echo "Root maintenance job already exists. Skipping."
else
    echo "Adding root maintenance job..."
    (sudo crontab -l 2>/dev/null; echo "$ROOT_JOB") | sudo crontab -
    echo "Root job added successfully."
fi

# 2. Setup User Cron (Nextcloud Background Tasks)
echo "Checking cormac crontab for Nextcloud scheduler..."
if crontab -l 2>/dev/null | grep -Fq "cron.php"; then
    echo "Nextcloud user job already exists. Skipping."
else
    echo "Adding Nextcloud user job..."
    (crontab -l 2>/dev/null; echo "$USER_JOB") | crontab -
    echo "Nextcloud user job added successfully."
fi

echo "--- Cron Setup Complete ---"

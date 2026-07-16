# Wolfetone Server Infrastructure

This repository contains the infrastructure-as-code and maintenance scripts for the `wolfetone` home server. It manages containerized services (Nextcloud, Tailscale) and handles automated, secure backups and system updates.

## Repository Structure

*   `compose-stacks/`: Docker Compose configurations for all services.
*   `scripts/`: Automation and maintenance scripts (e.g., `maintain.sh`).
*   `system-configs/`: Templates of host-level system files (e.g., cron jobs, environment variables) for reference and disaster recovery.
*   `app-data/`: **(Untracked)** Local bind mounts containing live databases, user files, and cryptographic keys.

---

## 1. Prerequisites

To deploy this setup on a fresh Ubuntu host, install the following dependencies:

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install docker.io docker-compose-v2 rsync -y
```

---

## 2. Host Configuration Setup

Before bringing up the containers, the host environment must be configured. Do not commit actual passwords to this repository.

### Environment Variables
Create `/etc/environment` and populate it with the backup script paths:

```ini
NC_BACKUP_DIR="/mnt/backups/nextcloud"
NC_HOST_DATA_PATH="/home/cormac/docker/app-data/nextcloud/html"
NC_CONTAINER="nextcloud-app"
NC_DB_CONTAINER="nextcloud-db"
```
*Force reload the environment with `source /etc/environment`.*

### Database Credentials
Create the secure native MariaDB credential file on the host. This file is mounted read-only into the database container for secure `mysqldump` execution.

```bash
sudo nano /etc/nextcloud-db.cnf
```
Add the following, replacing the placeholder with your master root password:
```ini
[client]
user=root
password=YOUR_MASTER_ROOT_PASSWORD
```
Lock down the file permissions:
```bash
sudo chown root:root /etc/nextcloud-db.cnf
sudo chmod 600 /etc/nextcloud-db.cnf
```

---

## 3. Deployment

Once the host configuration is secure, initialize the networks and containers.

```bash
cd ~/docker/compose-stacks/nextcloud
sudo docker compose up -d

cd ~/docker/compose-stacks/tailscale
sudo docker compose up -d
```

---

## 4. Maintenance & Backups

The server is maintained by `scripts/maintain.sh`, which performs the following sequence:
1. Places Nextcloud in maintenance mode.
2. Dumps the MariaDB database to `/mnt/backups/nextcloud`.
3. Prunes backups older than 5 days.
4. Uses `rsync` to snapshot the user data (`app-data/nextcloud/html`).
5. Updates host OS packages (`apt`).
6. Pulls new Docker images and recreates containers.
7. Executes `occ upgrade` if required and disables maintenance mode.

### Cron Automation
The script runs automatically every Sunday at 3:00 AM. 

The following is added to the **root** crontab (`sudo crontab -e`) by the script `~/scripts/crontab_setup.sh`:

```cron
0 3 * * 0 /home/cormac/docker/scripts/maintain.sh >> /var/log/nextcloud-maintain.log 2>&1
```

`crontab_setup.sh` also adds the Nextcloud server maintenance "tick" cron job, scheduled every 5 minutes.

---

## 5. Disaster Recovery

If the `app-data/` drive fails or the server is rebuilt, follow these steps to restore from the latest backup:

1. Complete **Steps 1 & 2** (Prerequisites and Host Setup) on the new machine.
2. Stop all running containers:
   ```bash
   sudo docker compose down
   ```
3. Restore the application files (preserves `www-data` ownership):
   ```bash
   sudo rsync -Aax /mnt/backups/nextcloud/files/ /home/cormac/docker/app-data/nextcloud/html/
   ```
4. Start the database container *only*:
   ```bash
   cd ~/docker/compose-stacks/nextcloud
   sudo docker compose up -d db
   ```
5. Import the database snapshot:
   ```bash
   sudo docker exec -i nextcloud-db mysql -u root -p'YOUR_MASTER_ROOT_PASSWORD' nextcloud < /mnt/backups/nextcloud/db_backup.sql
   ```
6. Start the remaining containers:
   ```bash
   sudo docker compose up -d
   ```
7. Verify functionality via the Nextcloud web interface.

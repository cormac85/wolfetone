# Wolfetone recovery guide

How to get back into every part of the homelab if a password, key or device is lost. **This file contains no secrets**: it says where each one lives and how to reset or replace it.

> Why this exists: in 2026-10 the KeePass database holding every credential became unopenable (forgotten master password). Everything was recovered from the machine itself because the host `cormac` user is in the `docker` group. The steps below are what worked.

## 1. Where each secret lives

Keep the real values in the password manager (NordPass). Entry names below are suggestions: rename to match your vault.

| Secret | Primary home | Also on the machine | If lost |
|---|---|---|---|
| Password manager master password + recovery code | Paper copy, offline | none | Unrecoverable. Keep the paper copy safe, and test the recovery code. |
| Host `cormac` password (sudo) | NordPass: `wolfetone host cormac` | `/etc/shadow` (hash only) | §2 |
| Host root password | none: root is locked, use `sudo -i` | | `sudo passwd root` if you ever want one |
| Nextcloud `cormac_admin` (+ TOTP, backup codes) | NordPass: `nextcloud cormac_admin` | hash in DB | §3 |
| Nextcloud `cormac` | NordPass: `nextcloud cormac` | hash in DB | §3 |
| MariaDB `nextcloud` user password | NordPass: `nextcloud db app user` | `secrets/db_password.txt` and `app-data/nextcloud/html/config/config.php` | §4 |
| MariaDB root password | NordPass: `nextcloud db root` | `secrets/db_root_password.txt` and `/etc/nextcloud-db.cnf` | §4 |
| Borg repo passphrase | NordPass: `borg wolfetone repo` | `/etc/borgmatic/config.yaml` (`encryption_passphrase`) | §5. The repo is `repokey` mode: the key is inside the repo, so the passphrase alone is enough. |
| Borg SSH key to datashank (no passphrase) | NordPass secure note | `/root/.ssh/backup` | Make a new key, add its `.pub` to `~cormac/.ssh/authorized_keys` on datashank |
| GitHub deploy key (read-only, no passphrase) | not needed | `~/.ssh/github_deploy` | Generate a new one, add under repo Settings → Deploy keys |
| Home Assistant login | NordPass / browser | `app-data/homeassistant/.storage` | `hass --script auth` in the container |
| Tailscale | Google account SSO (`cormac85@`) | | Google account recovery. Keep its recovery codes offline. |
| ntfy | none: no auth configured, reachable on the tailnet only | | n/a |
| HTTPS cert | none | `compose-stacks/nextcloud/certs/` | `docs/cert-rotation.md` |

Offline, outside both NordPass and the homelab: NordPass master password and recovery code, Google account recovery codes, and the `age` passphrase for any encrypted bundle.

## 2. Lost the host password (cormac / sudo / root)

If you can still SSH in as `cormac` (key login), you do **not** need the old password. `cormac` is in the `docker` group, which is root-equivalent. Reset it through a throwaway container that chroots into the host disk (any Debian/Ubuntu-based image already on the host works; `mariadb:10.11` is used here):

```bash
docker run --rm -it -v /:/host --entrypoint chroot mariadb:10.11 /host passwd cormac
```

Type the new password yourself at the prompt, and in a **separate** SSH session test `sudo ls` before closing this one. Type it by hand (do not paste through several layers of terminal): a mangled password is the usual cause of "I reset it but sudo still fails".

Afterwards `sudo -i` gives a root shell. Root's own password is locked by default; set one with `sudo passwd root` only if you want console login for emergencies.

**If SSH/docker access is also gone** (physical access to the laptop): boot an Ubuntu live USB, mount the root partition (`/dev/nvme0n1p2` at the time of writing, not encrypted), `chroot` into it and run `passwd cormac`. Last resort is a re-image from USB; see §6 first.

## 3. Lost a Nextcloud login

No old password is needed. On wolfetone (`-it` so it can prompt, typing the new password twice):

```bash
docker exec -it -u www-data nextcloud-app php occ user:resetpassword cormac_admin
docker exec -it -u www-data nextcloud-app php occ user:resetpassword cormac
```

`cormac_admin` has TOTP enabled. If you cannot produce a code or backup code:

```bash
docker exec -u www-data nextcloud-app php occ twofactorauth:disable cormac_admin
```

Log in, then re-enable 2FA in Settings → Security and store the new backup codes. Sync clients that use app passwords keep working after a password reset; revoke stale sessions in Settings → Security.

## 4. Changing the database passwords

Four copies of each password must agree. For the **app user** (`nextcloud`):

1. MariaDB (`ALTER USER 'nextcloud'@'%' ...`)
2. `app-data/nextcloud/html/config/config.php` (`dbpassword`)
3. `secrets/db_password.txt`
4. nothing else reads it at runtime

For **root**: MariaDB (`root@localhost`), `/etc/nextcloud-db.cnf`, `secrets/db_root_password.txt`.

Gotchas learned the hard way:

- **Order matters for the app user.** `php occ config:system:set` needs a working DB connection, so once MariaDB has the new password `occ` can no longer write it into `config.php`. Edit `config.php` first (keep a copy), change the DB password, then confirm with `occ user:list`. Turn on `occ maintenance:mode --on` while doing this.
- If the app and DB disagree, put the DB back in line with whatever `config.php` says; that restores service.
- `secrets/*.txt` files hold **bare values** (see README). They are only read when a database is first created, so editing them alone changes nothing on a live system.
- Root has no remote account (`root@%` was dropped in 2026-10); admin access is via `docker exec nextcloud-db mysql --defaults-extra-file=/etc/mysql/conf.d/nextcloud-db.cnf`.
- Take a dump first: `docker exec nextcloud-db mysqldump --defaults-extra-file=/etc/mysql/conf.d/nextcloud-db.cnf --single-transaction nextcloud > pre-change.sql`, and delete it after.

Forgot the MariaDB root password entirely? Check `/etc/nextcloud-db.cnf` and `secrets/db_root_password.txt` first (both readable via §2's root access). Otherwise restart MariaDB with `--skip-grant-tables` temporarily.

## 5. Backups and restores

Nightly (`0 23 * * *`, root's crontab) `scripts/maintain.sh` dumps the DB and rsyncs files to `/mnt/backups/nextcloud` (same disk, staging only), upgrades the host and containers, then `borgmatic` sends it to **datashank** over Tailscale (`/mnt/media_500gb/wolfetone_backups`). Success posts to ntfy topic `backups`. `maintain.sh` must run as root.

List and inspect archives (as root, on wolfetone):

```bash
sudo borgmatic list
sudo borgmatic info
```

**What is and is not backed up.** Borg only sends `/mnt/backups/nextcloud` (the Nextcloud DB dumps and file snapshot). **Not backed up:** Home Assistant data (`app-data/homeassistant`), Mosquitto data/volumes, ntfy cache, the HTTPS cert, and host config (`/etc/borgmatic`, `/etc/nextcloud-db.cnf`, `secrets/`, SSH keys). Those secrets are meant to live in the password manager. Adding `app-data/homeassistant` to `source_directories` in `/etc/borgmatic/config.yaml` would cover Home Assistant.

Restore (use a scratch directory, never straight over live data):

```bash
sudo mkdir /root/restore && cd /root/restore
sudo borgmatic extract --archive latest --path mnt/backups/nextcloud
```

Then, for the DB, create an empty database and `mysql ... nextcloud < db_backup_YYYY-MM-DD.sql`; for files, rsync back to `app-data/nextcloud/html` and `chown -R 33:33`, then run `occ maintenance:data-fingerprint` and `occ files:scan --all`. README §5 has the full disaster-recovery sequence.

Check periodically that you can actually restore a file: a backup you have never restored is a hope, not a backup.

## 6. Rebuilding the host from scratch

Order, if the laptop dies or you re-image it:

1. Install Ubuntu Server, create `cormac`, add your SSH public keys, install Docker, Tailscale (`tailscale up`; approve in the admin console).
2. `git clone` this repo to `~/docker` (read-only deploy key, §1).
3. Create `/etc/environment`, `/etc/nextcloud-db.cnf` and `secrets/*.txt` per the README, using values from the password manager.
4. Restore the backup (§5) **before** first start of a fresh DB, so MariaDB doesn't initialise an empty one over the top.
5. Install the cron jobs with `scripts/crontab_setup.sh` and the systemd override with `scripts/deploy.sh`.
6. Renew the HTTPS cert: `docs/cert-rotation.md`.
7. Home Assistant: **it is not in the borg backup** (see §5). Only `configuration.yaml` and `automations.yaml` are in git; the rest of `app-data/homeassistant` (users, integrations, history) is lost with the disk unless you copied it elsewhere. Re-onboard HA and re-add the Brother printer, Climote MQTT and Pixel integrations by hand.

## 7. Quarterly checklist (10 minutes)

- [ ] Open the password manager on a second device, cold, using only the paper master password.
- [ ] `sudo borgmatic list` shows archives from the last 48 hours; extract one file.
- [ ] HTTPS cert has at least 30 days left (`docs/cert-rotation.md`).
- [ ] Tailscale admin console login works (Google account + recovery codes).
- [ ] The ntfy "Maintenance complete" message arrived this week.
- [ ] A second SSH key (phone/laptop) is in `authorized_keys`.

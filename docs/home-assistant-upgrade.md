# Home Assistant upgrade runbook

Home Assistant (HA) runs as the `homeassistant` container from `compose-stacks/homeassistant/docker-compose.yaml`. **It is upgraded by hand.** `scripts/maintain.sh` only updates the Nextcloud stack, so HA never changes unless you change it. The image tag in the compose file is pinned to an exact version (not `:stable`) so a `docker compose pull` can never move it by surprise.

This file contains no secrets.

## Why a runbook

- HA releases monthly. Each release has a "Backward-incompatible changes" list; a jump over several releases means reading each skipped release's list.
- HA migrates its config and its recorder database **one way** on startup. Starting an older image on top of migrated data may fail or corrupt state. So **rollback means restoring a backup, not just changing the tag back.**
- The risky part is usually custom integrations (HACS and `custom_components/`), not core.

## 1. Pre-flight (10 minutes)

1. Read the release notes for every version you are skipping (`https://www.home-assistant.io/blog/categories/release-notes/`). Look for: integrations you use (`docker exec homeassistant cat /config/.storage/core.config_entries` is root-only; use Settings > Devices & services), removed integrations, minimum-version notes for external servers, and auth changes. Record the outcome in the table at the bottom.
2. Current version: `docker exec homeassistant cat /config/.HA_VERSION`.
3. Settings > System > Repairs: fix or note existing warnings first.
4. Update HACS, then each custom integration, **while still on the old HA version** (HACS > updates). Old custom integrations are the usual cause of a failed start on a new core.
5. Confirm HA is healthy on the current version before touching anything.

## 2. Backup (three layers)

1. **HA backup:** Settings > System > Backups > *Create backup*. It lands in `app-data/homeassistant/backups/` (also covered by the nightly borg run).
2. **Off-box:** `sudo borgmatic create` (or wait for the 23:00 `maintain.sh`).
3. **Fast local rollback on the box.** On wolfetone, before the upgrade:

   ```bash
   mkdir -p ~/ha-pre-upgrade && chmod 700 ~/ha-pre-upgrade
   docker tag "$(docker inspect homeassistant --format '{{.Image}}')" ha-previous:$(docker exec homeassistant cat /config/.HA_VERSION)
   docker run --rm --entrypoint tar \
     -v ~/docker/app-data/homeassistant:/config:ro -v ~/ha-pre-upgrade:/out \
     ha-previous:<old-version> \
     --exclude='./home-assistant_v2.db*' --exclude='./home-assistant.log*' --exclude=./tts --exclude=./deps --exclude=./.cache \
     -czf /out/ha-config-<old-version>.tar.gz -C /config .
   ```

   The tarball holds credentials: keep the directory `700` and delete it once the new version has been stable for a week. (The recorder database is left out on purpose; history is expendable and a live copy can be inconsistent.)

## 3. Upgrade

1. Edit the pinned tag in `compose-stacks/homeassistant/docker-compose.yaml`, commit, and `git pull` it on wolfetone (`~/docker`).
2. Pull and recreate **only** HA (the compose file also holds Mosquitto and the climote decoder):

   ```bash
   cd ~/docker/compose-stacks/homeassistant
   docker compose pull homeassistant
   docker compose up -d homeassistant
   docker logs -f homeassistant
   ```

3. Expect a few minutes while the database migrates and integrations reload. Warnings about *custom integration ... has not been tested* are normal; `Setup failed for custom integration` or a start that hangs is not (see Rollback).

## 4. Verify

- [ ] `docker exec homeassistant cat /config/.HA_VERSION` shows the new version.
- [ ] Login works (since 2026.10 usernames must be lowercase with no spaces).
- [ ] Settings > System > Repairs: no new items.
- [ ] Settings > Devices & services: no integration shows "Failed to set up" (MQTT, ZHA, Brother, IPP, SmartThings, Yale, UPnP, Met, and the custom ones).
- [ ] ZHA devices respond; the climote temperature sensor updates (MQTT).
- [ ] `https://wolfetone.tailee21f7.ts.net:8443` loads over Tailscale (Caddy).
- [ ] An automation fires.
- [ ] Take a fresh HA backup on the new version.

## 5. Rollback

```bash
cd ~/docker/compose-stacks/homeassistant
docker compose stop homeassistant
# restore the config (database migrates one way, so use the matching pre-upgrade copy)
docker run --rm --entrypoint sh -v ~/docker/app-data/homeassistant:/config -v ~/ha-pre-upgrade:/in ha-previous:<old-version> \
  -c 'cd /config && tar -xzf /in/ha-config-<old-version>.tar.gz'
rm -f ~/docker/app-data/homeassistant/home-assistant_v2.db*   # let the old version create a fresh recorder DB
# set the compose image tag back to <old-version> (git revert), then:
docker compose up -d homeassistant
```

If the local copy is gone, restore from the HA backup tar (Settings > System > Backups) or from borg (`docs/recovery.md` section 6, step 7). The recorder history restarts empty.

## 6. After

- Delete `~/ha-pre-upgrade` and the `ha-previous:*` image after about a week: `docker rmi ha-previous:<old-version>`.
- Add a row to the log below.
- Aim to be no more than two releases behind (see the quarterly checklist in `docs/recovery.md`).

## Upgrade log

| Date | From | To | Notes |
|---|---|---|---|

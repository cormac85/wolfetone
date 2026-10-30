# HTTPS certificate rotation

Nextcloud (`:443`), ntfy (`:8081`) and Home Assistant (`:8443`) are all served by Caddy on the Tailscale IP, and all three present **one** certificate: a Let's Encrypt cert for `wolfetone.tailee21f7.ts.net`, issued through Tailscale. It lasts **90 days** and, before `scripts/renew_certs.sh`, nothing renewed it.

> History: the cert issued 2026-07-18 expired 2026-10-16 and browsers/apps refused to connect. It was re-issued on 2026-10-29 (valid until 2027-01-27).

## How it fits together

- `tailscale cert` (run as root on the host) asks Let's Encrypt, via Tailscale, for a cert and writes it as a cert file and key file.
- Those two files live in `compose-stacks/nextcloud/certs/` (`wolfetone.crt`, `wolfetone.key`, root-owned, `600` on the key, **gitignored**) and are mounted into Caddy at `/certs`.
- `Caddyfile` loads them with `tls /certs/wolfetone.crt /certs/wolfetone.key` for Nextcloud and ntfy.
- Home Assistant's block says `tls internal`, but its hostname is the same as the loaded cert. Caddy picks a loaded certificate by name before an internal one, so Home Assistant serves the same Let's Encrypt cert. **One cert fixes all three.**
- Caddy does **not** notice cert files changing on disk. A plain `caddy reload` with an unchanged Caddyfile does nothing; you must use `--force` (or restart the container).

## Renew by hand

On wolfetone (needs root):

```bash
sudo /home/cormac/docker/scripts/renew_certs.sh
```

The script requests a cert, checks it (valid, right name, key matches), swaps it in atomically, force-reloads Caddy, and posts to the ntfy `backups` topic. If the cert has not changed it does nothing, so it is safe to run any time.

If you ever need to do it without the script:

```bash
cd ~/docker/compose-stacks/nextcloud/certs
sudo tailscale cert --cert-file wolfetone.crt.new --key-file wolfetone.key.new wolfetone.tailee21f7.ts.net
sudo chmod 644 wolfetone.crt.new && sudo chmod 600 wolfetone.key.new
sudo mv wolfetone.crt.new wolfetone.crt && sudo mv wolfetone.key.new wolfetone.key
docker exec nextcloud-caddy-1 caddy reload --force --config /etc/caddy/Caddyfile
```

## Automatic renewal

Install once, in **root's** crontab (`sudo crontab -e`):

```cron
0 4 * * 1 /home/cormac/docker/scripts/renew_certs.sh >> /var/log/cert-renew.log 2>&1
```

`scripts/crontab_setup.sh` adds this line for you. Weekly is plenty: Tailscale only gets a new cert from Let's Encrypt when the cached one is near expiry, and the script only reloads Caddy when the cert actually changed. It warns on ntfy if the cert on disk has fewer than 14 days left.

## Check it

From any machine on the tailnet:

```bash
for p in 443 8081 8443; do
  echo | openssl s_client -connect wolfetone.tailee21f7.ts.net:$p -servername wolfetone.tailee21f7.ts.net 2>/dev/null \
    | openssl x509 -noout -enddate
done
```

All three must show the same `notAfter`. On the host: `sudo openssl x509 -in ~/docker/compose-stacks/nextcloud/certs/wolfetone.crt -noout -dates -issuer`.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Browser says the cert expired, but the file on disk is new | Caddy wasn't reloaded. Run `docker exec nextcloud-caddy-1 caddy reload --force --config /etc/caddy/Caddyfile`. |
| `tailscale cert` says HTTPS is not enabled | In the Tailscale admin console (login via your Google account) → **DNS** → enable **HTTPS Certificates**. |
| `tailscale cert` permission denied | Run it as root (`sudo`), or `sudo tailscale set --operator=$USER` once. |
| Script says cert/key mismatch | A half-written pair; delete `wolfetone.*.new` in `certs/` and re-run. The live files are never touched until validation passes. |
| Only Home Assistant fails | Check the `:8443` block in `Caddyfile` still uses the same hostname; if it were changed to a different name, `tls internal` would apply and clients would need to trust Caddy's local CA. |
| Wrong date on the server | Let's Encrypt and TLS validation depend on the clock; check `timedatectl`. |

## Notes

- The private key never leaves wolfetone and is not in git (`certs/` is gitignored). If the directory is lost, just run the renewal again; there is nothing to restore from backup.
- Tailscale also has `tailscale serve`, which manages certs fully automatically and could replace the manual cert files. It would change how Caddy is used here, so it has not been done.

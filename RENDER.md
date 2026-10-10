# Render Deployment

This service uses a Render web service and a persistent disk for Pinterest's
refreshable OAuth tokens. The Blueprint uses the paid Starter instance plan;
Render's free instances do not support persistent disks.

## Deploy

1. Push the repository to the Git provider connected to Render. The local
   `pinterest/pinterest_config.py`, `product_research/research_config.py`, and
   `pinterest_tokens.json` are intentionally excluded from Git.
2. In Render, create a Blueprint instance from the repository and review the
   service and persistent-disk cost before applying it.
3. Set the Blueprint's prompted environment values:
   - `PIN_API_KEY`: a long random key used only by this API and the GPT Action.
   - `PINTEREST_CLIENT_ID` and `PINTEREST_CLIENT_SECRET`: from the Pinterest
     developer app settings.
   - `PINTEREST_TOKENS_JSON_B64`: base64-encoded contents of the local
     `pinterest_tokens.json` file.
   - `APIFY_API_TOKEN`: optional; required for the `getEtsyTrending` Action.
4. Keep `PINTEREST_ENV=production` and `PINTEREST_TOKEN_FILE=/var/data/pinterest_tokens.json`
   as configured by the Blueprint. The `/var/data` disk keeps rotated tokens
   across restarts.
5. After deployment, verify `/health`, then import
   `https://<render-service>.onrender.com/openapi.json` into the GPT Action.
   Configure API-key authentication as a custom header named `X-API-Key` and
   use the same value as `PIN_API_KEY`.

The GPT Action surface includes board/category lookup, board/section creation,
pin publishing (single and batch), cross-platform keyword ideas, Google's
general trending feed, Pinterest's current keyword trends, Amazon bestsellers,
and Etsy listing research. Amazon's public bestseller page can block requests
or change its HTML. Etsy research invokes a paid Apify actor. Pinterest's
official Trends documentation describes the API as limited to agencies,
Enterprise clients, and partner platforms; Standard API access does not by
itself guarantee Trends entitlement. If Pinterest denies the request, the
`getPinterestTrends` Action returns a clear permission error and the other
Actions remain usable.

To encode the local token file on macOS, run this locally and paste its output
into Render's `PINTEREST_TOKENS_JSON_B64` secret field:

```sh
base64 < pinterest_tokens.json | tr -d '\n'
```

Do not put token JSON, API keys, or Pinterest credentials in the repository.
The API seeds the persistent token file from the base64 value on first use;
after that, the disk file is authoritative and is updated when Pinterest
rotates tokens.
## Scheduled pins

`schedulePins` queues one to four pin variants and posts them on a drip
(default: first immediately, then every 3 days). The queue (`pin_queue.db`)
and the saved pin images live under `PIN_DATA_DIR`, which the Blueprint sets
to the `/var/data` disk so they survive restarts and redeploys.

A background thread inside the web service checks the queue every
`PIN_WORKER_INTERVAL` seconds (default 60) and posts due pins. If Pinterest
answers "Sorry! This site doesn't allow you to save Pins." it retries after
about a minute, up to 3 attempts; any other error marks the pin failed.
Check progress with `getScheduledPins`. Keep the service at one instance:
two instances would each run a worker against their own disk.

By default each variant is placed in the next free daily posting slot, one
pin per slot, so products queued together spread out instead of posting in a
burst. Spacing between a product's variants is measured from the slot each
one actually got. Configure with optional env vars:
- `PIN_DAILY_SLOTS`: comma-separated local times (default `09:00,14:00,20:00`)
- `PIN_TIMEZONE`: IANA timezone for those times (default `America/Chicago`)

Pass `use_slots: false` to `schedulePins` to post at exact times instead.

## Pin dashboard

`https://<render-service>.onrender.com/dashboard` lists upcoming, posted,
failed, and cancelled pins with image thumbnails. Queued pins can be
rescheduled, posted now, or cancelled; failed and cancelled pins can be
requeued. Times are shown and entered in `PIN_TIMEZONE`.

Sign in with `DASHBOARD_PASSWORD` (falls back to `PIN_API_KEY` if unset).
Each device stays signed in for 90 days, and the login form works with
password-manager autofill. Changing `DASHBOARD_PASSWORD` signs out every
device. Dashboard routes are excluded from `/openapi.json`, so the GPT
Action never sees them.

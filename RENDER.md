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
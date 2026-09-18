# Argus

Argus is a self-hosted tracker for movies and TV shows.

## Trakt.tv setup

Trakt synchronization is optional. The server administrator provides one Trakt API application, and each Argus user connects their own Trakt account. The setup follows Trakt's [getting started](https://docs.trakt.tv/docs/getting-started) and [OAuth](https://docs.trakt.tv/docs/authentication-oauth) documentation.

1. Create an API application at [Trakt.tv applications](https://trakt.tv/oauth/applications) and copy its Client ID and Client Secret.
2. Set these variables in the Argus environment:

   ```dotenv
   TRAKT_CLIENT_ID=your-client-id
   TRAKT_CLIENT_SECRET=your-client-secret
   TRAKT_SYNC_INTERVAL_MINUTES=5
   ```

3. Register `https://your-argus-host.example/user/trakt/callback/` as the redirect URI in the Trakt application. Argus derives it from the request (the Trakt tab in settings shows staff the exact value); the path, scheme, host, port, and trailing slash must match. Set `TRAKT_REDIRECT_URI` only when Argus is reached under a different public URL than the one it sees.
4. Restart Argus and its Procrastinate worker.
5. Each user opens Argus settings and chooses **Connect Trakt.tv**. Only one Trakt account can be connected to a user.

OAuth access and refresh tokens are encrypted at rest using the Argus `SECRET_KEY`. If `SECRET_KEY` changes, reconnect the Trakt accounts.

The worker synchronizes every five minutes by default. Change `TRAKT_SYNC_INTERVAL_MINUTES` to use another interval; values below one minute are clamped to one minute. A running worker is required:

```text
python manage.py procrastinate worker
```

The sync covers movie watchlists and watched movies, every Trakt watchlist or watched TV show, watched episodes, and Trakt dropped shows. A watchlist-only show is tracked in Argus without marking any episodes watched. Dropping a show in either application synchronizes through Trakt's dropped/hidden show endpoints; Argus keeps the watched episode history when a show is dropped.

Watched state is merged from both sides, and duplicate Trakt plays retain only the newest watch timestamp. An explicit local unwatch queues a Trakt history removal and suppresses stale snapshots until the removal is observed. Argus batches writes, paginates reads, deduplicates pending changes, and honors Trakt's `Retry-After` response when rate limited.

Trakt's API application credentials are server-wide, but OAuth tokens and media state are isolated per Argus user. Tests use fake clients and do not require live Trakt, TMDB, or TVDB credentials.

## Stremio setup

Stremio synchronization needs no server-side client credentials. Each user opens Argus settings, chooses **Connect Stremio**, opens the generated Stremio link or QR code, authorizes the connection, and confirms it in Argus.

The worker synchronizes Stremio library membership with Argus watchlists, movie watch state, and series episode watch state. Stremio stores series progress as a compressed watched-video bitfield; Argus resolves that state through Cinemeta and maps the resulting IMDb IDs to TMDB before importing missing catalog records. Unknown Stremio fields are preserved during writes, and local changes are queued as provider-specific intents so a stale pull cannot undo an explicit Argus change.

Set `STREMIO_SYNC_INTERVAL_MINUTES` to change the periodic two-way sync interval; it defaults to five minutes and is clamped to one minute. Stremio authorization keys are encrypted at rest using `SECRET_KEY`; if that key changes, reconnect the affected accounts.

## SIMKL setup

SIMKL is optional and has two layers. The server administrator registers one SIMKL application at [SIMKL developer settings](https://simkl.com/settings/developer/); its **client id** alone unlocks the Discover page, the "Trending on Simkl" section on Home, and the community data shown on movie and show pages. Adding the **client secret** lets each user connect their own SIMKL account for two-way sync.

```dotenv
SIMKL_CLIENT_ID=your-client-id
SIMKL_CLIENT_SECRET=your-client-secret
SIMKL_SYNC_INTERVAL_MINUTES=15
```

In the SIMKL application, register `https://your-argus-host.example/user/simkl/callback/` as the redirect URI; Argus derives that URL from the request, and the SIMKL tab in settings shows staff the exact value. Set `SIMKL_REDIRECT_URI` only when Argus is reached under a different public URL than the one it sees (a rewriting proxy). Restart Argus and its Procrastinate worker afterwards; each user then opens Argus settings and chooses **Connect SIMKL**. SIMKL tokens last about five years and are encrypted at rest using `SECRET_KEY`. If SIMKL answers `401`, the user revoked the app on SIMKL and must reconnect.

### What syncs

Watched state is the ground truth on whichever app holds it: a movie or episode watched in Argus is marked watched on SIMKL and vice versa. Unwatching only travels through explicit actions. An unwatch in Argus removes the SIMKL history entry (and re-adds a movie to *Plan to Watch* if it stays on the Argus watchlist); an unwatch or *Remove from list* on SIMKL is mirrored into Argus. Rewatches are not tracked on either side, so `allow_rewatch` is never sent.

| Argus | SIMKL |
| --- | --- |
| Movie on watchlist | `plantowatch` |
| Movie watched | `completed` |
| Show tracked, no episodes watched | `plantowatch` |
| Show tracked, episodes watched | `watching` / `completed` (SIMKL decides) |
| Show paused | `hold` |
| Show dropped | `dropped` |
| Movie / show rating (half stars) | 1-10 rating (x2), both ways |

Episode ratings stay in Argus because SIMKL has no episode ratings. Anime tracked as TV shows through TVDB or TMDB is written with `use_tvdb_anime_seasons` and read back with the per-episode TVDB coordinates SIMKL provides, so cours split on SIMKL still map onto the single Argus show.

### How the sync runs

The worker follows SIMKL's two-phase model. The first run pulls the whole library once (shows, movies and anime, one type at a time). Every later run asks `/sync/activities` first and only fetches the buckets whose timestamp moved, using `date_from`. A local mirror of the SIMKL library is kept so that episodes unmarked or titles removed on SIMKL can be detected by diffing; a delta that would wipe more than half of a bucket is refused as a safety measure. Local changes are queued as intents and pushed in batches, at most one write per second. Titles SIMKL cannot match are reported on the settings page and are not retried until **Sync now** is pressed.

The worker checks every `SIMKL_SYNC_INTERVAL_MINUTES` (default fifteen) but, following SIMKL's rule against polling without user interaction, only for accounts whose user has used Argus within `SIMKL_IDLE_HOURS` (default 24) or that have local changes waiting to be pushed. Coming back after being idle queues a sync on the first request, and **Sync now** always runs. After the first pull, every delta is one combined `/sync/all-items?date_from=` request (plus one anime-only read when anime episodes changed, for their TVDB coordinates). Community data (IMDb and SIMKL ratings, rank, drop rate, certification, trailer fallback, "viewers also watched") is loaded on demand by the detail page (the "On Simkl" card fetches from SIMKL right there when nothing fresh is stored, so untracked titles get it too, cached instead of persisted) and refreshed for tracked titles every `SIMKL_METADATA_REFRESH_DAYS` days by a nightly job (`SIMKL_METADATA_CRON`, `SIMKL_METADATA_BATCH_SIZE`). Trending and calendar data come from SIMKL's public CDN files and are cached for one to six hours; SIMKL's API rules require the section titles to name Simkl and link back to it, which the UI does.

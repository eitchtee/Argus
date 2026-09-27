# Argus

Argus is a self-hosted tracker for movies and TV shows.

## Trakt.tv

> [!WARNING]
> Trakt syncing has been dropped because Trakt's anti-consumer practices made maintaining it difficult, and at times impossible. There are currently no plans to bring it back. The last working code is preserved for reference on the [`archive/trakt-sync`](https://github.com/eitchtee/Argus/tree/archive/trakt-sync) branch.
>
> Importing a Trakt data export (**Settings → Import → Trakt**) is still supported.

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

## MDBList setup

MDBList is optional and has two layers. A server **API key** (from [MDBList preferences](https://mdblist.com/preferences/)) unlocks the ratings card on movie and show pages (MDBList score plus IMDb, Rotten Tomatoes critics and audience, Metacritic, Letterboxd, TMDB, Trakt and more, with certification, trailer, streaming services and similar titles), the "Top on streaming" rail on Home, and the streaming chart, trending, popular and anticipated sections on Discover. Users connect their own MDBList account for two-way sync, either through an OAuth application the administrator registers at [MDBList developer settings](https://mdblist.com/developer/), or by pasting their personal API key in settings when no OAuth application is configured (both options are offered when it is).

```dotenv
MDBLIST_API_KEY=your-api-key
MDBLIST_CLIENT_ID=your-oauth-client-id
MDBLIST_CLIENT_SECRET=your-oauth-client-secret
MDBLIST_SYNC_INTERVAL_MINUTES=15
```

For OAuth, register `https://your-argus-host.example/user/mdblist/callback/` as the redirect URI of an "OAuth 2.0" (confidential) application; the MDBList tab in settings shows staff the exact value, and `MDBLIST_REDIRECT_URI` overrides it behind a rewriting proxy. MDBList requires PKCE on every authorization, which Argus handles. OAuth tokens last 30 days and are refreshed automatically before they expire (or once when MDBList rejects one early); API keys and tokens are encrypted at rest using `SECRET_KEY`. Without a server API key, a user with a connected account still gets the ratings card and Discover sections through their own credentials.

Every MDBList key has a daily request quota (1,000 on the free plan). The sync costs one request per run when nothing changed, catalog reads are cached for hours (ratings for `MDBLIST_METADATA_REFRESH_DAYS` days), and the nightly refresh of tracked titles (`MDBLIST_METADATA_CRON`, `MDBLIST_METADATA_BATCH_SIZE`) uses batched lookups of up to a hundred titles per request. When a quota runs out, the sync is rescheduled for the moment MDBList says it resets.

### What syncs

Watched state is the ground truth on whichever app holds it, as with SIMKL: a movie or episode watched in Argus is marked watched on MDBList and vice versa, and unwatching only travels through explicit actions on either side.

| Argus | MDBList |
| --- | --- |
| Movie on watchlist | Watchlist |
| Movie watched | Watched history |
| Show tracked, no episodes watched, on watchlist | Watchlist |
| Episode watched | Watched history (episode) |
| Show dropped | Dropped |
| Movie / show rating (half stars) | 1-10 rating (x2), both ways |

MDBList has no paused state, so paused shows stay in Argus (and the first pull does not turn a paused show dropped). Episode ratings stay in Argus. MDBList numbers episodes like TMDB; shows whose TVDB numbering differs from TMDB (mostly anime) can land on different episode numbers on each side.

### How the sync runs

The first run pulls the whole library (watched history, ratings, watchlist, dropped shows). Every later run asks `/sync/last_activities` first. When watched or rated state moved, it replays the MDBList sync journal from the previous run's server time, which reports per-item additions and removals (episodes included); the watchlist or dropped list is re-read only when its own timestamp moved. If the journal's 30-day window has passed, the run falls back to a full pull. A local mirror of the MDBList library, keyed by TMDB id, records what each change is compared with; a snapshot that would wipe more than half of a list is refused as a safety measure. MDBList sync rows only carry TMDB and IMDb ids, so shows imported from MDBList get their TVDB id from TMDB first and keep TVDB as their source. Local changes are queued as intents and pushed in batches (at most 200 shows per write). Titles MDBList cannot match are reported on the settings page and are not retried until **Sync now** is pressed. Accounts idle for more than `MDBLIST_IDLE_HOURS` (default 24) are not polled unless they have local changes to push.

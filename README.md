# Argus

Argus is a self-hosted tracker for movies and TV shows.

## How to run

You'll need [Docker](https://docs.docker.com/engine/install/) with [Docker Compose](https://docs.docker.com/compose/install/).

```bash
# Create a folder for Argus and go into it
$ mkdir argus && cd argus

# Paste the contents of https://github.com/eitchtee/Argus/blob/main/docker-compose.prod.yml
$ nano docker-compose.yml

# Paste the contents of https://github.com/eitchtee/Argus/blob/main/.env.example and fill it in
$ nano .env

# Run the app
$ docker compose up -d

# Create the first admin account. Not needed if you set ADMIN_EMAIL and ADMIN_PASSWORD.
$ docker compose exec -it web python manage.py createsuperuser
```

There are no releases yet, so the only published image is `eitchtee/argus:nightly`, built from every push to `main`. In `docker-compose.yml`, replace the `build:` block and `image: argus:latest` under `web` with `image: eitchtee/argus:nightly`. Expect undocumented breaking changes.

At minimum, set `SECRET_KEY`, `SQL_PASSWORD`, `URL`, `DJANGO_ALLOWED_HOSTS` and `TMDB_API_KEY`. Search and catalog data come from TMDB, so Argus can't add anything without that key.

### Running locally

To reach Argus at `http://localhost:OUTBOUND_PORT` without a domain or HTTPS:

1. Remove `URL`.
2. Set `HTTPS_ENABLED=false`.
3. Keep the default `DJANGO_ALLOWED_HOSTS` (`localhost 127.0.0.1 [::1]`).

> [!NOTE]
> To reach it from another machine (a LAN IP, Tailscale), add that IP or hostname to `DJANGO_ALLOWED_HOSTS` without `http://`, and the full origin (`http://100.x.y.z:8000`) to `URL`.

### Environment variables

The defaults in `.env.example` fit a personal instance with 1 to 3 users.

| variable | type | default | explanation |
|---|---|---|---|
| SECRET_KEY | string | *required* | Signs sessions and encrypts the Stremio, SIMKL and MDBList tokens at rest. If it changes, users must reconnect those accounts. |
| DEBUG | true\|false | false | Shows detailed error pages. Don't use in production. |
| URL | string | http://localhost http://127.0.0.1 | Space-separated origins, with the scheme, trusted for unsafe requests (e.g. POST). See [CSRF_TRUSTED_ORIGINS](https://docs.djangoproject.com/en/5.2/ref/settings/#csrf-trusted-origins). |
| DJANGO_ALLOWED_HOSTS | string | localhost 127.0.0.1 | Space-separated domains and IPs Argus answers to. See [ALLOWED_HOSTS](https://docs.djangoproject.com/en/5.2/ref/settings/#allowed-hosts). |
| HTTPS_ENABLED | true\|false | false | Marks cookies as secure and trusts the `X-Forwarded-Proto` header from your reverse proxy. Set it when Argus is served over HTTPS. |
| TZ | string | UTC | Timezone for dates and for the task worker's schedules. |
| SESSION_EXPIRY_TIME | int | 2678400 (31 days) | How long you stay logged in, in seconds. |
| SESSION_COOKIE_NAME | string | sessionid | Session cookie name. Only needed to run several instances on the same host. |
| CSRF_COOKIE_NAME | string | csrftoken | CSRF cookie name. Only needed to run several instances on the same host. |
| ADMIN_EMAIL | string | None | Creates an admin account with this email on startup. Needs `ADMIN_PASSWORD`. |
| ADMIN_PASSWORD | string | None | Password for the `ADMIN_EMAIL` account. Remove both after your first login. |
| DEMO | true\|false | false | Demo mode: creates a `demo@demo.com` user and closes the API to everyone but superusers. |
| SERVER_NAME | string | argus_server | Container name of the app (compose only). |
| INTERNAL_PORT | int | 8000 | The port the app listens on inside the container. |
| OUTBOUND_PORT | int | 8000 | The host port the app is published on (compose only). |
| DB_NAME | string | argus_pg | Container name of the database (compose only). Point `SQL_HOST` at it. |
| SQL_DATABASE | string | *required* | Postgres database name. |
| SQL_USER | string | user | Postgres user. |
| SQL_PASSWORD | string | password | Postgres password. |
| SQL_HOST | string | localhost | Postgres host. |
| SQL_PORT | string | 5432 | Postgres port. |
| DB_POOL_MAX_SIZE | int | the larger of GUNICORN_THREADS and PROCRASTINATE_WORKER_CONCURRENCY, + 1 | Database connections each process may open. Only set it if you see `PoolTimeout` errors. |
| WEB_CONCURRENCY | int | 1 | Web worker processes. Each one is a full copy of the app in memory (about 220 MiB). |
| GUNICORN_THREADS | int | 1 | Threads per web worker. Threads share their worker's memory, so 1 worker with 4 threads serves more requests at once than 2 workers, with half the RAM. |
| TASK_WORKERS | int | 1 | Background task worker processes. Each one is a full copy of the app in memory, so prefer raising `PROCRASTINATE_WORKER_CONCURRENCY`. |
| PROCRASTINATE_WORKER_CONCURRENCY | int | 1 | How many background tasks each worker runs at once. 2 keeps account syncs running while a long import or catalog refresh is in progress. |
| TMDB_API_KEY | string | "" | [TMDB](https://www.themoviedb.org/settings/api) API key. Required for search and catalog data. |
| TMDB_IMAGE_BASE_URL | string | https://image.tmdb.org/t/p/ | Where posters and backdrops are loaded from. |
| TVDB_API_KEY | string | "" | [TVDB](https://thetvdb.com/api-information) API key. TV shows use TVDB as their metadata source. |
| CATALOG_SEARCH_CACHE_TTL | int | 3600 | How long search results are cached, in seconds. |
| CATALOG_MOVIE_SYNC_INTERVAL_DAYS | int | 14 | How often a movie's metadata is refreshed, in days. |
| CATALOG_SHOW_SYNC_INTERVAL_DAYS | int | 2 | How often a running show's metadata is refreshed, in days. |
| CATALOG_ENDED_SHOW_SYNC_INTERVAL_DAYS | int | 30 | How often an ended show's metadata is refreshed, in days. |
| STREMIO_SYNC_INTERVAL_MINUTES | int | 5 | Minutes between Stremio syncs. See [Stremio setup](#stremio-setup). |
| SIMKL_CLIENT_ID | string | "" | SIMKL app client id. Enables Discover, trending rails and community ratings. See [SIMKL setup](#simkl-setup). |
| SIMKL_CLIENT_SECRET | string | "" | SIMKL app secret. Needed for per-user sync. |
| SIMKL_REDIRECT_URI | string | derived from the request | Only set it if Argus is reached under a different public URL than the one it sees. |
| SIMKL_APP_NAME | string | argus | App name sent to SIMKL. |
| SIMKL_APP_VERSION | string | the image version | App version sent to SIMKL. |
| SIMKL_SYNC_INTERVAL_MINUTES | int | 15 | Minutes between SIMKL syncs. |
| SIMKL_IDLE_HOURS | int | 24 | Accounts idle for longer aren't polled unless they have changes to push. |
| SIMKL_METADATA_REFRESH_DAYS | int | 7 | How often SIMKL ratings for a title are refreshed, in days. |
| SIMKL_METADATA_BATCH_SIZE | int | 200 | Titles refreshed per nightly SIMKL ratings run. |
| SIMKL_METADATA_CRON | string | 30 5 * * * | When the SIMKL ratings refresh runs. |
| MDBLIST_API_KEY | string | "" | Server [MDBList](https://mdblist.com/preferences/) key. Enables the ratings card, streaming charts and Discover lists. See [MDBList setup](#mdblist-setup). |
| MDBLIST_CLIENT_ID | string | "" | MDBList OAuth app client id. Without one, users connect by pasting their own API key. |
| MDBLIST_CLIENT_SECRET | string | "" | MDBList OAuth app secret. |
| MDBLIST_REDIRECT_URI | string | derived from the request | Only set it if Argus is reached under a different public URL than the one it sees. |
| MDBLIST_SYNC_INTERVAL_MINUTES | int | 15 | Minutes between MDBList syncs. |
| MDBLIST_IDLE_HOURS | int | 24 | Accounts idle for longer aren't polled unless they have changes to push. |
| MDBLIST_METADATA_REFRESH_DAYS | int | 7 | How often MDBList ratings for a title are refreshed, in days. |
| MDBLIST_METADATA_BATCH_SIZE | int | 200 | Titles refreshed per nightly MDBList ratings run. |
| MDBLIST_METADATA_CRON | string | 45 5 * * * | When the MDBList ratings refresh runs. |
| OIDC_CLIENT_NAME | string | OpenID Connect | Name shown on the OIDC login button. |
| OIDC_CLIENT_ID | string | None | OIDC client id. OIDC login is enabled when the id, secret and server URL are all set. |
| OIDC_CLIENT_SECRET | string | None | OIDC client secret. |
| OIDC_SERVER_URL | string | None | OIDC provider URL. |
| OIDC_ALLOW_SIGNUP | true\|false | true | Whether a first OIDC login creates a new account. |
| DJANGO_VITE_DEV_MODE | true\|false | false | Serves frontend assets from Vite's dev server. Development only. |
| DJANGO_VITE_DEV_SERVER_PORT | int | 5173 | Vite dev server port. Development only. |
| DJANGO_VITE_DEV_SERVER_HOST | string | localhost | Vite dev server host. Development only. |
| VITE_OUTBOUND_PORT | int | 5173 | Host port for the Vite dev server (dev compose only). |
| SQL_OUTBOUND_PORT | int | SQL_PORT | Host port for Postgres (dev compose only). |

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

## Development

The development stack runs from `docker-compose.dev.yml`:

```bash
docker compose -f docker-compose.dev.yml up -d
```

Set `COMPOSE_FILE=docker-compose.dev.yml` in your `.env` to drop the `-f` flag.

### Working in multiple git worktrees

You can run one dev stack per [git worktree](https://git-scm.com/docs/git-worktree), side by side. Each worktree gets its own containers, images, database and volumes, because Compose names them after the worktree's folder.

A new worktree has no `.env` (it's gitignored). After creating one, run this from the worktree's root:

```bash
sh docker/dev/worktree.sh
```

It copies `.env` from your main checkout, gives the worktree its own host ports (printed when it finishes) and installs `frontend/node_modules` for tools you run outside Docker. The database and `data/` cache start empty.

To stop the worktree's containers and delete its volumes before removing it:

```bash
sh docker/dev/worktree.sh teardown
```

The commands work on Linux, macOS and Windows (Git Bash or WSL). PowerShell and cmd have no `sh`, so on Windows set up git aliases once in your clone; they run through Git's bundled shell from any terminal, and every worktree of the clone shares them:

```bash
git config alias.wt-setup '!sh docker/dev/worktree.sh'
git config alias.wt-teardown '!sh docker/dev/worktree.sh teardown'
```

Run those two lines in Git Bash or PowerShell; cmd doesn't understand the single quotes.

Then use `git wt-setup` and `git wt-teardown`. Tools that create worktrees for you, such as Orca, Conductor or Superset, usually have setup and archive hooks: put these commands there.

> [!NOTE]
> Ports come from the worktree's folder name, so two worktrees can rarely land on the same ones. If `docker compose up` reports a port in use, change `OUTBOUND_PORT`, `VITE_OUTBOUND_PORT` or `SQL_OUTBOUND_PORT` in that worktree's `.env`.

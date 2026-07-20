# Argus

Argus is a self-hosted tracker for movies and TV shows.

## Trakt.tv setup

Trakt synchronization is optional. The server administrator provides one Trakt API application, and each Argus user connects their own Trakt account. The setup follows Trakt's [getting started](https://docs.trakt.tv/docs/getting-started) and [OAuth](https://docs.trakt.tv/docs/authentication-oauth) documentation.

1. Create an API application at [Trakt.tv applications](https://trakt.tv/oauth/applications) and copy its Client ID and Client Secret.
2. Set these variables in the Argus environment:

   ```dotenv
   TRAKT_CLIENT_ID=your-client-id
   TRAKT_CLIENT_SECRET=your-client-secret
   TRAKT_REDIRECT_URI=https://your-argus-host.example/user/trakt/callback/
   TRAKT_SYNC_INTERVAL_MINUTES=5
   ```

3. Add the exact same value of `TRAKT_REDIRECT_URI` to the Trakt application. The path, scheme, host, port, and trailing slash must match.
4. Restart Argus and its Procrastinate worker.
5. Each user opens Argus settings and chooses **Connect Trakt.tv**. Only one Trakt account can be connected to a user.

OAuth access and refresh tokens are encrypted at rest using the Argus `SECRET_KEY`. If `SECRET_KEY` changes, reconnect the Trakt accounts.

The worker synchronizes every five minutes by default. Change `TRAKT_SYNC_INTERVAL_MINUTES` to use another interval; values below one minute are clamped to one minute. A running worker is required:

```text
python manage.py procrastinate worker
```

The sync covers movie watchlists and watched movies, every Trakt watchlist or watched TV show, watched episodes, and Trakt dropped shows. A watchlist-only show is tracked in Argus without marking any episodes watched. Dropping a show in either application synchronizes through Trakt's dropped/hidden show endpoints; Argus keeps the watched episode history when a show is dropped.

Watched state is monotonic: if either Argus or Trakt says a movie or episode is watched, it remains watched. When Trakt reports duplicate plays, only the newest watch timestamp is retained. Argus batches writes, paginates reads, deduplicates pending changes, and honors Trakt's `Retry-After` response when rate limited.

Trakt's API application credentials are server-wide, but OAuth tokens and media state are isolated per Argus user. Tests use fake clients and do not require live Trakt, TMDB, or TVDB credentials.

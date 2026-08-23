def build_external_links(
    media_type: str,
    *,
    provider: str,
    external_id: str,
    title: str = "",
    imdb_id: str | None = None,
    tmdb_id: str | None = None,
    tvdb_id: str | None = None,
    trakt_id: str | None = None,
) -> list[dict[str, str]]:
    """Return stable external links for a movie, show, or provider record."""

    links: list[dict[str, str]] = []

    def add(
        label: str,
        url: str | None,
        *,
        icon: str,
        logo: str | None = None,
    ) -> None:
        if not url or any(link["url"] == url for link in links):
            return
        links.append(
            {
                "label": label,
                "url": url,
                "icon": icon,
                "logo": logo or label.lower(),
            }
        )

    provider = provider.lower()
    media_type = media_type.lower()
    provider_id = {
        "tmdb": tmdb_id,
        "tvdb": tvdb_id,
    }.get(provider) or external_id

    if provider == "tmdb" and media_type == "movie":
        add(
            "TMDB",
            f"https://www.themoviedb.org/movie/{provider_id}",
            icon="database",
            logo="tmdb",
        )
    elif provider == "tmdb" and media_type == "tv":
        add(
            "TMDB",
            f"https://www.themoviedb.org/tv/{provider_id}",
            icon="database",
            logo="tmdb",
        )
    elif provider == "tvdb":
        entity = "series" if media_type == "tv" else "movies"
        add(
            "TVDB",
            f"https://thetvdb.com/dereferrer/{entity}/{provider_id}",
            icon="database",
            logo="tvdb",
        )

    if tmdb_id:
        path = "movie" if media_type == "movie" else "tv"
        add(
            "TMDB",
            f"https://www.themoviedb.org/{path}/{tmdb_id}",
            icon="database",
            logo="tmdb",
        )
    if tvdb_id:
        entity = "series" if media_type == "tv" else "movies"
        add(
            "TVDB",
            f"https://thetvdb.com/dereferrer/{entity}/{tvdb_id}",
            icon="database",
            logo="tvdb",
        )
    if imdb_id:
        add(
            "IMDb",
            f"https://www.imdb.com/title/{imdb_id}/",
            icon="star",
            logo="imdb",
        )
    if trakt_id:
        path = {
            "tv": "shows",
            "movie": "movies",
            "episode": "episodes",
        }.get(media_type)
        if path:
            add(
                "Trakt",
                f"https://trakt.tv/{path}/{trakt_id}",
                icon="tv",
                logo="trakt",
            )

    return links

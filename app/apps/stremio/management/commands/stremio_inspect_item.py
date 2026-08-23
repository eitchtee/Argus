import json

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.stremio.client import StremioClient
from apps.stremio.codec import decode_watched_bitfield
from apps.stremio.models import StremioAccount
from apps.stremio.sync import (
    _content_id,
    _series_metadata_id,
    _sorted_videos,
    _valid_watched_bitfield,
    _video_parts,
)


class Command(BaseCommand):
    help = (
        "Dump the raw Stremio library item for one title next to the Cinemeta "
        "video list, and report whether its episode state can be decoded."
    )

    def add_arguments(self, parser):
        parser.add_argument("content_id", help="e.g. tt0944947, tmdb:1399, tvdb:121361")
        parser.add_argument("--user", help="User email (defaults to the only connected account)")
        parser.add_argument("--raw", action="store_true", help="Also print the raw library item JSON")

    def handle(self, *args, **options):
        account = self._account(options.get("user"))
        content_id = _content_id(options["content_id"]) or options["content_id"]
        client = StremioClient(account.auth_key)

        items = [
            item
            for item in client.datastore_get(ids=[content_id])
            if _content_id(item.get("_id")) == content_id
        ]
        if not items:
            raise CommandError(f"Stremio has no library item for {content_id}")

        for item in items:
            self._report(item, content_id, account, raw=options["raw"])

    def _account(self, email):
        accounts = StremioAccount.objects.select_related("user")
        if email:
            user = get_user_model().objects.filter(email=email).first()
            if user is None:
                raise CommandError(f"No user with email {email}")
            account = accounts.filter(user=user).first()
        else:
            account = accounts.first() if accounts.count() == 1 else None
            if account is None:
                raise CommandError("Several accounts are connected; pass --user")
        if account is None or not account.auth_key:
            raise CommandError("No connected Stremio account with an auth key")
        return account

    def _report(self, item, content_id, account, *, raw):
        state = item.get("state") if isinstance(item.get("state"), dict) else {}
        write = self.stdout.write

        write(self.style.MIGRATE_HEADING(f"\n{content_id} ({item.get('type')}) {item.get('name')!r}"))
        write(f"  removed={item.get('removed')!r} temp={item.get('temp')!r} _mtime={item.get('_mtime')!r}")
        write(f"  state.watched={state.get('watched')!r}")
        write(f"  state.video_id={state.get('video_id')!r}")
        write(
            "  state.timesWatched={!r} flaggedWatched={!r} lastWatched={!r}".format(
                state.get("timesWatched"),
                state.get("flaggedWatched"),
                state.get("lastWatched"),
            )
        )
        if raw:
            write("  raw=" + json.dumps(item, indent=2, default=str))

        if item.get("type") != "series":
            return

        metadata_id = _series_metadata_id(item, content_id, user=account.user)
        write(f"  cinemeta lookup id: {metadata_id}")
        if not metadata_id.startswith("tt"):
            write(self.style.WARNING("  -> not an IMDb id; Cinemeta cannot resolve this series"))

        try:
            metadata = StremioClient(account.auth_key).get_cinemeta_series(metadata_id)
        except Exception as exc:
            write(self.style.ERROR(f"  -> Cinemeta lookup failed: {exc}"))
            return

        videos = _sorted_videos(metadata)
        video_ids = [str(video["id"]) for video in videos]
        write(f"  cinemeta videos: {len(video_ids)}")
        if video_ids:
            write(f"    first={video_ids[0]}  last={video_ids[-1]}")

        serialized = state.get("watched")
        if serialized in (None, ""):
            write("  -> no episode state stored on this item")
            return

        anchor = str(serialized).rsplit(":", 2)[0]
        write(f"  bitfield anchor: {anchor!r}")
        if anchor not in video_ids:
            write(
                self.style.ERROR(
                    "  -> anchor is NOT in the Cinemeta video list. Stremio wrote this "
                    "state against a different addon's episode ids, so the watches "
                    "cannot be mapped."
                )
            )
        else:
            write(f"    anchor index in sorted list: {video_ids.index(anchor)}")

        if not _valid_watched_bitfield(serialized, video_ids):
            write(self.style.ERROR("  -> bitfield does NOT validate; episode watches are dropped"))
            return

        watched = decode_watched_bitfield(serialized, video_ids)
        pairs = sorted(
            parts
            for video in videos
            if str(video["id"]) in watched
            for parts in [_video_parts(video)]
            if parts is not None
        )
        write(self.style.SUCCESS(f"  -> decodes to {len(pairs)} watched episodes"))
        write("     " + ", ".join(f"S{season:02d}E{episode:02d}" for season, episode in pairs))

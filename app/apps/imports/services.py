import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from zipfile import BadZipFile, ZipFile

from django.db import transaction

from apps.trakt.client import TraktSnapshot
from apps.trakt.sync import SyncReport, apply_remote_snapshot


class TraktExportError(ValueError):
    pass


MAX_ARCHIVE_SIZE = 100 * 1024 * 1024
MAX_ARCHIVE_MEMBER_COUNT = 1000
MAX_MEMBER_UNCOMPRESSED_SIZE = 64 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED_SIZE = 512 * 1024 * 1024
MAX_JSON_ITEMS_PER_MEMBER = 1_000_000


@dataclass(frozen=True)
class _ExportMember:
    name: str
    index: int


_SPLIT_MEMBERS = {
    "watched_movies": re.compile(r"^watched-movies(?:-(\d+))?\.json$"),
    "watched_history": re.compile(r"^watched-history(?:-(\d+))?\.json$"),
}


def load_trakt_export(stream) -> TraktSnapshot:
    try:
        stream.seek(0)
        archive = ZipFile(stream)
    except (AttributeError, BadZipFile, OSError, ValueError) as exc:
        raise TraktExportError(
            "The uploaded file is not a valid Trakt ZIP export."
        ) from exc

    with archive:
        _validate_archive_members(archive)
        members = _members_by_kind(archive)
        _ensure_supported_data(archive, members)

        watched_movies = _read_split_arrays(archive, members["watched_movies"])
        watched_history = _read_split_arrays(archive, members["watched_history"])
        watched_shows = _read_array(archive, "watched-shows.json")
        dropped_shows = _read_array(archive, "hidden-progress-watched.json")
        watchlist = _read_array(archive, "lists-watchlist.json")

    watchlist_movies = []
    watchlist_shows = []
    for item in watchlist:
        if not isinstance(item, dict):
            continue
        media_type = item.get("type")
        if media_type == "movie" or (media_type is None and "movie" in item):
            watchlist_movies.append(item)
        elif media_type == "show" or (media_type is None and "show" in item):
            watchlist_shows.append(item)

    return TraktSnapshot(
        watchlist_movies=watchlist_movies,
        watchlist_shows=watchlist_shows,
        watched_movies=watched_movies,
        watched_shows=[item for item in watched_shows if isinstance(item, dict)],
        dropped_shows=[item for item in dropped_shows if isinstance(item, dict)],
        watched_episodes=[item for item in watched_history if isinstance(item, dict)],
    )


def validate_trakt_export(stream) -> None:
    """Validate the archive shape without doing the import work synchronously."""
    try:
        stream.seek(0)
        with ZipFile(stream) as archive:
            _validate_archive_members(archive)
            members = _members_by_kind(archive)
            _ensure_supported_data(archive, members)
    except TraktExportError:
        raise
    except (AttributeError, BadZipFile, OSError, ValueError) as exc:
        raise TraktExportError(
            "The uploaded file is not a valid Trakt ZIP export."
        ) from exc
    finally:
        stream.seek(0)


def import_trakt_export(user, stream) -> SyncReport:
    with transaction.atomic():
        return apply_remote_snapshot(user, load_trakt_export(stream))


def _members_by_kind(archive: ZipFile) -> dict[str, list[_ExportMember]]:
    result = {kind: [] for kind in _SPLIT_MEMBERS}
    for info in archive.infolist():
        name = info.filename
        if PurePosixPath(name).name != name:
            continue
        for kind, pattern in _SPLIT_MEMBERS.items():
            match = pattern.fullmatch(name)
            if match:
                result[kind].append(
                    _ExportMember(name=name, index=int(match.group(1) or 1))
                )
                break
    for members in result.values():
        members.sort(key=lambda member: (member.index, member.name))
    return result


def _read_split_arrays(archive: ZipFile, members: list[_ExportMember]) -> list[dict]:
    values = []
    for member in members:
        values.extend(_read_array(archive, member.name))
    return values


def _ensure_supported_data(
    archive: ZipFile,
    members: dict[str, list[_ExportMember]],
) -> None:
    if any(members.values()):
        return
    root_names = {
        info.filename
        for info in archive.infolist()
        if PurePosixPath(info.filename).name == info.filename
    }
    if root_names.isdisjoint(
        {
            "hidden-progress-watched.json",
            "watched-shows.json",
            "lists-watchlist.json",
        }
    ):
        raise TraktExportError("The ZIP does not contain supported Trakt export data.")


def _read_array(archive: ZipFile, name: str) -> list:
    try:
        payload = json.loads(archive.read(name).decode("utf-8"))
    except KeyError:
        return []
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TraktExportError(
            f"The Trakt export member {name} is not valid JSON."
        ) from exc
    if not isinstance(payload, list):
        raise TraktExportError(
            f"The Trakt export member {name} must contain a JSON list."
        )
    if len(payload) > MAX_JSON_ITEMS_PER_MEMBER:
        raise TraktExportError(
            f"The Trakt export member {name} contains too many items."
        )
    return payload


def _validate_archive_members(archive: ZipFile) -> None:
    infos = archive.infolist()
    if len(infos) > MAX_ARCHIVE_MEMBER_COUNT:
        raise TraktExportError("The ZIP contains too many files.")

    total_size = 0
    for info in infos:
        if info.is_dir():
            continue
        if info.file_size > MAX_MEMBER_UNCOMPRESSED_SIZE:
            raise TraktExportError("A ZIP member is too large to import.")
        total_size += info.file_size
        if total_size > MAX_TOTAL_UNCOMPRESSED_SIZE:
            raise TraktExportError("The ZIP expands beyond the allowed size.")

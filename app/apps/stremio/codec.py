import base64
import binascii
import zlib


def decode_watched_bitfield(serialized: str | None, video_ids: list[str]) -> set[str]:
    if not serialized or not video_ids:
        return set()
    try:
        anchor_video_id, anchor_length_raw, packed = serialized.rsplit(":", 2)
        anchor_length = int(anchor_length_raw)
        anchor_index = video_ids.index(anchor_video_id)
        previous = zlib.decompress(base64.b64decode(packed, validate=True))
    except (ValueError, TypeError, zlib.error, binascii.Error):
        return set()
    if anchor_length <= 0 or anchor_index >= anchor_length:
        return set()
    offset = (anchor_length - 1) - anchor_index
    watched = set()
    for index, video_id in enumerate(video_ids):
        previous_index = index + offset
        if previous_index < 0 or previous_index >= anchor_length:
            continue
        byte_index, bit_index = divmod(previous_index, 8)
        if byte_index < len(previous) and previous[byte_index] & (1 << bit_index):
            watched.add(video_id)
    return watched


def encode_watched_bitfield(watched_ids: set[str], video_ids: list[str]) -> str | None:
    if not video_ids:
        return None
    values = bytearray((len(video_ids) + 7) // 8)
    last_watched_index = 0
    for index, video_id in enumerate(video_ids):
        if video_id in watched_ids:
            byte_index, bit_index = divmod(index, 8)
            values[byte_index] |= 1 << bit_index
            last_watched_index = index
    packed = base64.b64encode(zlib.compress(bytes(values))).decode("ascii")
    return f"{video_ids[last_watched_index]}:{last_watched_index + 1}:{packed}"

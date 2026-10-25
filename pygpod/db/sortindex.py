"""Library sort indexes of the master playlist (MHOD 52) and their letter jump tables (MHOD 53).

The firmware browses the library (songs, artists, albums, genres, composers) through these
indexes and uses the jump tables for its quick scroll. The rules follow libgpod
(itdb_itunesdb.c: mhod52_make_collate_keys, get_sort_artist, jump_table_letter,
mhod52_sort_*):

- the sort fields (sort_title, sort_artist, sort_album, sort_composer) are used when set;
  an artist "The X" without a sort artist sorts as "X, The";
- the jump letter is the first alphanumeric character, upper-cased (its first UTF-16 unit);
  a digit, or a string without letters and digits, gives "0";
- ties are broken by further fields (album, disc, track number, title ...), and the five
  indexes are built by sorting one list in turn (title, artist, album, genre, composer), so
  completely equal entries keep libgpod's order.

The order itself differs on purpose: libgpod uses the host locale's collation
(g_utf8_collate_key; in the C locale "abba" sorts after "Zebra"), pygpod a fixed,
case-insensitive one: leading characters that are not letters or digits are skipped (so
"(Live) X" sorts with the L, where its jump letter is), Latin letters with diacritics sort
with their base letter, and the Ukrainian/Belarusian letters ґ, є, і, ї, ў (and ё) sort after
the letter they follow in the alphabet instead of after я.
"""

from __future__ import annotations

import struct
import unicodedata
from typing import Dict, List, NamedTuple, Optional, Sequence, Tuple

# MHOD 52 sort types, in the order libgpod writes the pairs
SORT_TITLE, SORT_ALBUM, SORT_ARTIST, SORT_GENRE, SORT_COMPOSER = 3, 4, 5, 7, 18
SORT_ORDER = (SORT_TITLE, SORT_ARTIST, SORT_ALBUM, SORT_GENRE, SORT_COMPOSER)

# Cyrillic letters outside the а-я block, placed after the letter they follow
_CYRILLIC_AFTER = {
    "ґ": ("г", 1),
    "ё": ("е", 1),
    "є": ("е", 2),
    "і": ("и", 1),
    "ї": ("и", 2),
    "ў": ("у", 1),
}


class SortEntry(NamedTuple):
    """What the indexes need of one master-playlist member."""

    title: str
    artist: str
    album: str
    genre: str
    composer: str
    sort_title: str
    sort_artist: str
    sort_album: str
    sort_composer: str
    track_nr: int
    cd_nr: int


def _is_alnum(ch: str) -> bool:
    return unicodedata.category(ch)[0] in "LN"  # glib's g_unichar_isalnum


def jump_letter(text: Optional[str]) -> int:
    """libgpod's jump_table_letter(): the MHOD 53 letter of a sort string."""
    for ch in text or "":
        if not _is_alnum(ch):
            continue
        if unicodedata.category(ch)[0] != "L":
            return ord("0")
        upper = ch.upper()
        cp = ord(upper if len(upper) == 1 else ch)
        if cp > 0xFFFF:  # first UTF-16 unit (high surrogate)
            return 0xD800 + ((cp - 0x10000) >> 10)
        return cp
    return ord("0")


def sort_artist_string(artist: str, sort_artist: str) -> Optional[str]:
    """libgpod's get_sort_artist(): the sort artist, "X, The" for "The X", else None."""
    if sort_artist:
        return sort_artist
    if not artist:
        return None
    if artist[:4].encode("ascii", "replace").lower() == b"the ":
        return f"{artist[4:]}, The" + "\x01" * 5
    return None


def collate_key(text: str) -> tuple:
    """pygpod's collation (see the module docstring); the text itself breaks exact ties."""
    start = 0
    while start < len(text) and not _is_alnum(text[start]):
        start += 1
    key = []
    for ch in text[start:].casefold():
        if ch in _CYRILLIC_AFTER:
            base, rank = _CYRILLIC_AFTER[ch]
            key.append((ord(base), rank))
            continue
        decomposed = unicodedata.normalize("NFKD", ch)
        if len(decomposed) > 1 and decomposed[0].isascii():
            key.append((ord(decomposed[0]), 1))
        else:
            key.append((ord(ch), 0))
    return (tuple(key), text)


def _fields(entry: SortEntry) -> Dict[str, str]:
    """The strings libgpod sorts and letters by."""
    artist = sort_artist_string(entry.artist, entry.sort_artist)
    return {
        "title": entry.sort_title or entry.title,
        "album": entry.sort_album or entry.album,
        "artist": artist if artist is not None else entry.artist,
        "genre": entry.genre,
        "composer": entry.sort_composer or entry.composer,
    }


_FIELD_OF = {
    SORT_TITLE: "title",
    SORT_ALBUM: "album",
    SORT_ARTIST: "artist",
    SORT_GENRE: "genre",
    SORT_COMPOSER: "composer",
}


def build_indexes(entries: Sequence[SortEntry]) -> List[Tuple[int, List[int], List[tuple]]]:
    """(sort type, member positions in sorted order, jump table [(letter, start, count)])
    for each sort type, in libgpod's order."""
    rows = []
    for index, entry in enumerate(entries):
        strings = _fields(entry)
        keys = {name: collate_key(value) for name, value in strings.items()}
        letters = {name: jump_letter(value) for name, value in strings.items()}
        rows.append((index, entry, keys, letters))

    def key_for(sort_type):
        def key(row):
            _, e, k, _ = row
            tail = (e.cd_nr, e.track_nr, k["title"])
            if sort_type == SORT_TITLE:
                return (k["title"],)
            if sort_type == SORT_ALBUM:
                return (k["album"],) + tail
            if sort_type == SORT_ARTIST:
                return (k["artist"], k["album"]) + tail
            if sort_type == SORT_GENRE:
                return (k["genre"], k["artist"], k["album"]) + tail
            return (k["composer"], k["album"]) + tail

        return key

    # libgpod prepends while collecting (reverse order) and sorts the same list five times
    # with a stable sort, so equal entries carry the previous order along
    ordered = list(reversed(rows))
    result = []
    for sort_type in SORT_ORDER:
        ordered = sorted(ordered, key=key_for(sort_type))
        field = _FIELD_OF[sort_type]
        jumps: List[list] = []
        for pos, row in enumerate(ordered):
            letter = row[3][field]
            if not jumps or jumps[-1][0] != letter:
                jumps.append([letter, pos, 0])
            jumps[-1][2] += 1
        result.append((sort_type, [row[0] for row in ordered], [tuple(j) for j in jumps]))
    return result


def mhod52_body(sort_type: int, positions: Sequence[int]) -> bytes:
    """MHOD 52 body: sort type, count, 40 bytes zero, the member positions."""
    body = bytearray(48 + 4 * len(positions))
    struct.pack_into("<II", body, 0, sort_type, len(positions))
    for i, pos in enumerate(positions):
        struct.pack_into("<I", body, 48 + 4 * i, pos)
    return bytes(body)


def mhod53_body(sort_type: int, jumps: Sequence[tuple]) -> bytes:
    """MHOD 53 body: sort type, count, 8 bytes zero, (letter u16, 0 u16, start, count)."""
    body = bytearray(16 + 12 * len(jumps))
    struct.pack_into("<II", body, 0, sort_type, len(jumps))
    for i, (letter, start, count) in enumerate(jumps):
        struct.pack_into("<HHII", body, 16 + 12 * i, letter, 0, start, count)
    return bytes(body)

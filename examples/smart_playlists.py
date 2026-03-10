"""Build and evaluate smart playlist rules.

Demonstrates creating SPL rules programmatically and evaluating
them against an iPod's track library.
"""

from __future__ import annotations

import argparse
import sys

import pygpod
from pygpod import (
    SPLAction,
    SPLField,
    SPLLimitSort,
    SPLLimitType,
    SPLMatch,
    SPLPrefs,
    SPLRule,
    apply_limit,
    evaluate_smart_playlist,
)


def example_genre(db: pygpod.Database, genre: str) -> None:
    """Find all tracks matching a genre."""
    rule = SPLRule(
        field=SPLField.GENRE,
        action=SPLAction.CONTAINS,
        string=genre,
    )
    matches = evaluate_smart_playlist([rule], SPLMatch.AND, db.tracks)
    print(f"\nTracks with genre containing '{genre}': {len(matches)}")
    for t in matches:
        print(f"  {t.artist} - {t.title} [{t.genre}]")


def example_high_rated(db: pygpod.Database, min_stars: int) -> None:
    """Find tracks rated at or above a threshold."""
    rule = SPLRule(
        field=SPLField.RATING,
        action=SPLAction.IS_GREATER_THAN,
        fromvalue=(min_stars * 20) - 1,
    )
    matches = evaluate_smart_playlist([rule], SPLMatch.AND, db.tracks)
    print(f"\nTracks rated {min_stars}+ stars: {len(matches)}")
    for t in matches:
        print(f"  {'*' * t.rating_stars} {t.artist} - {t.title}")


def example_combined(db: pygpod.Database) -> None:
    """Combine multiple rules: rock tracks from the 2000s, limited to 10."""
    rules = [
        SPLRule(
            field=SPLField.GENRE,
            action=SPLAction.CONTAINS,
            string="Rock",
        ),
        SPLRule(
            field=SPLField.YEAR,
            action=SPLAction.IS_IN_THE_RANGE,
            fromvalue=2000,
            tovalue=2009,
        ),
    ]
    matches = evaluate_smart_playlist(rules, SPLMatch.AND, db.tracks)

    # Apply a limit: top 10 by song name
    prefs = SPLPrefs()
    prefs.checklimits = True
    prefs.limittype = SPLLimitType.SONGS
    prefs.limitvalue = 10
    prefs.limitsort = SPLLimitSort.SONG_NAME

    limited = apply_limit(matches, prefs)

    print(f"\nRock tracks from 2000-2009 (top 10 by name): {len(limited)}")
    for t in limited:
        print(f"  {t.year}  {t.artist} - {t.title}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Smart playlist examples")
    parser.add_argument("mountpoint", help="iPod mount point")
    parser.add_argument("--genre", default="Rock", help="Genre to search for (default: Rock)")
    parser.add_argument("--min-stars", type=int, default=4, help="Minimum star rating (default: 4)")
    args = parser.parse_args(argv)

    db = pygpod.Database(args.mountpoint)
    print(f"Loaded {len(db.tracks)} tracks from {args.mountpoint}")

    example_genre(db, args.genre)
    example_high_rated(db, args.min_stars)
    example_combined(db)

    return 0


if __name__ == "__main__":
    sys.exit(main())

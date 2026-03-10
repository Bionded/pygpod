"""Tests for ArtworkDB binary writer."""


def test_artworkdb_round_trip(artworkdb_path):
    """Parse and write back should produce identical bytes."""
    from pygpod.db.artwork_parser import parse_artworkdb
    from pygpod.db.artwork_writer import write_artworkdb

    with open(artworkdb_path, "rb") as f:
        original = f.read()

    root = parse_artworkdb(original)
    written = write_artworkdb(root)
    assert original == written


def test_write_empty_artworkdb():
    """Writing a minimal ArtworkDB with no children preserves raw_bytes."""
    from pygpod.db.artwork_writer import write_artworkdb
    from pygpod.db.parser import Record

    root = Record(b"mhfd", 84, 84)
    root.raw_header = b"\x00" * 84
    root.raw_bytes = b"\x00" * 84

    result = write_artworkdb(root)
    assert result == root.raw_bytes


def test_write_updates_total_len(artworkdb_path):
    """Writer should update total_len in the header."""
    from pygpod.db.artwork_parser import parse_artworkdb
    from pygpod.db.artwork_writer import write_artworkdb

    with open(artworkdb_path, "rb") as f:
        original = f.read()

    root = parse_artworkdb(original)
    result = write_artworkdb(root)

    # Total length in header (offset 8, 4 bytes LE) should match actual size
    import struct

    header_total = struct.unpack_from("<I", result, 8)[0]
    assert header_total == len(result)


def test_artworkdb_children_preserved(artworkdb_path):
    """All children should be preserved through round-trip."""
    from pygpod.db.artwork_parser import parse_artworkdb
    from pygpod.db.artwork_writer import write_artworkdb

    with open(artworkdb_path, "rb") as f:
        original = f.read()

    root = parse_artworkdb(original)
    original_child_count = len(root.children)

    written = write_artworkdb(root)
    root2 = parse_artworkdb(written)

    assert len(root2.children) == original_child_count


def test_artworkdb_image_ids_preserved(artworkdb_path):
    """Image IDs should be preserved through round-trip."""
    from pygpod.db.artwork_parser import parse_artworkdb
    from pygpod.db.artwork_writer import write_artworkdb

    with open(artworkdb_path, "rb") as f:
        original = f.read()

    root = parse_artworkdb(original)
    img_section = [c for c in root.children if c.fields.get("mhsd_type") == 1][0]
    mhli = img_section.children[0]
    original_ids = [c.fields["image_id"] for c in mhli.children]

    written = write_artworkdb(root)
    root2 = parse_artworkdb(written)
    img_section2 = [c for c in root2.children if c.fields.get("mhsd_type") == 1][0]
    mhli2 = img_section2.children[0]
    new_ids = [c.fields["image_id"] for c in mhli2.children]

    assert original_ids == new_ids

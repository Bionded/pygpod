"""Tests for ArtworkDB binary parser."""


def test_parse_artworkdb(artworkdb_path):
    """Parse the test ArtworkDB and verify structure."""
    from pygpod.db.artwork_parser import parse_artworkdb

    with open(artworkdb_path, "rb") as f:
        data = f.read()

    root = parse_artworkdb(data)
    assert root.magic == b"mhfd"
    assert len(root.children) >= 1


def test_artworkdb_sections(artworkdb_path):
    """Verify ArtworkDB has expected section types."""
    from pygpod.db.artwork_parser import parse_artworkdb

    with open(artworkdb_path, "rb") as f:
        root = parse_artworkdb(f.read())

    section_types = [c.fields.get("mhsd_type") for c in root.children]
    # Should have image list (type 1) and file list (type 3)
    assert 1 in section_types
    assert 3 in section_types


def test_artworkdb_image_list(artworkdb_path):
    """Verify image list contains MHII records."""
    from pygpod.db.artwork_parser import parse_artworkdb

    with open(artworkdb_path, "rb") as f:
        root = parse_artworkdb(f.read())

    # Find image list section (type 1)
    img_section = [c for c in root.children if c.fields.get("mhsd_type") == 1][0]
    mhli = img_section.children[0]
    assert mhli.magic == b"mhli"
    assert len(mhli.children) >= 1

    # Each child should be an MHII
    for mhii in mhli.children:
        assert mhii.magic == b"mhii"
        assert "image_id" in mhii.fields
        assert "song_id" in mhii.fields


def test_artworkdb_mhii_fields(artworkdb_path):
    """Verify MHII image records have expected fields."""
    from pygpod.db.artwork_parser import parse_artworkdb

    with open(artworkdb_path, "rb") as f:
        root = parse_artworkdb(f.read())

    img_section = [c for c in root.children if c.fields.get("mhsd_type") == 1][0]
    mhli = img_section.children[0]

    for mhii in mhli.children:
        assert "image_id" in mhii.fields
        assert "song_id" in mhii.fields
        assert "num_children" in mhii.fields
        assert mhii.fields["image_id"] >= 100  # IDs start at 100
        assert mhii.fields["num_children"] > 0


def test_artworkdb_file_list(artworkdb_path):
    """Verify MHLF/MHIF file list records."""
    from pygpod.db.artwork_parser import parse_artworkdb

    with open(artworkdb_path, "rb") as f:
        root = parse_artworkdb(f.read())

    # Find file list section (type 3)
    file_section = [c for c in root.children if c.fields.get("mhsd_type") == 3][0]
    mhlf = file_section.children[0]
    assert mhlf.magic == b"mhlf"
    assert len(mhlf.children) >= 1

    for mhif in mhlf.children:
        assert mhif.magic == b"mhif"
        assert "format_id" in mhif.fields
        assert "image_size" in mhif.fields


def test_artworkdb_invalid_magic():
    """Should raise ParseError on invalid data."""
    from pygpod.db.artwork_parser import parse_artworkdb
    from pygpod.exceptions import ParseError

    try:
        parse_artworkdb(b"not a valid artworkdb")
        assert False, "Should have raised ParseError"
    except ParseError:
        pass


def test_artworkdb_too_short():
    """Should raise ParseError on data that's too short."""
    from pygpod.db.artwork_parser import parse_artworkdb
    from pygpod.exceptions import ParseError

    try:
        parse_artworkdb(b"\x00" * 5)
        assert False, "Should have raised ParseError"
    except ParseError:
        pass


def test_artworkdb_format_ids(artworkdb_path):
    """Verify Classic B029 uses expected artwork format IDs via MHIF records."""
    from pygpod.db.artwork_parser import parse_artworkdb

    with open(artworkdb_path, "rb") as f:
        root = parse_artworkdb(f.read())

    # Get format IDs from MHLF/MHIF file list (type 3 section)
    file_section = [c for c in root.children if c.fields.get("mhsd_type") == 3][0]
    mhlf = file_section.children[0]

    format_ids = {c.fields["format_id"] for c in mhlf.children if c.magic == b"mhif"}
    known_formats = {1055, 1060, 1061, 1068}
    assert format_ids & known_formats, f"Expected some of {known_formats}, got {format_ids}"

"""Tests for plist parsing utilities."""

import plistlib


def test_read_plist(tmp_path):
    """Test reading a valid plist file."""
    from pygpod.utils.plist import read_plist

    # Create a test plist
    plist_data = {"ModelNumStr": "xB029", "FireWireGUID": "000A2700131A2BFC"}
    plist_path = str(tmp_path / "test.plist")
    with open(plist_path, "wb") as f:
        plistlib.dump(plist_data, f)

    result = read_plist(plist_path)
    assert result["ModelNumStr"] == "xB029"
    assert result["FireWireGUID"] == "000A2700131A2BFC"


def test_read_plist_missing_file():
    """Reading a nonexistent plist should return empty dict."""
    from pygpod.utils.plist import read_plist

    result = read_plist("/nonexistent/path/test.plist")
    assert result == {}


def test_read_plist_invalid_content(tmp_path):
    """Reading an invalid plist should return empty dict."""
    from pygpod.utils.plist import read_plist

    bad_path = str(tmp_path / "bad.plist")
    with open(bad_path, "wb") as f:
        f.write(b"this is not a plist")

    result = read_plist(bad_path)
    assert result == {}


def test_read_plist_nested(tmp_path):
    """Test reading a plist with nested structure."""
    from pygpod.utils.plist import read_plist

    plist_data = {
        "DeviceInfo": {
            "ModelNumber": "B029",
            "Generation": 6,
        },
        "SupportedFormats": [1060, 1068, 1055, 1061],
    }
    plist_path = str(tmp_path / "nested.plist")
    with open(plist_path, "wb") as f:
        plistlib.dump(plist_data, f)

    result = read_plist(plist_path)
    assert result["DeviceInfo"]["ModelNumber"] == "B029"
    assert 1060 in result["SupportedFormats"]


def test_read_plist_empty(tmp_path):
    """Test reading a plist with empty dict."""
    from pygpod.utils.plist import read_plist

    plist_path = str(tmp_path / "empty.plist")
    with open(plist_path, "wb") as f:
        plistlib.dump({}, f)

    result = read_plist(plist_path)
    assert result == {}

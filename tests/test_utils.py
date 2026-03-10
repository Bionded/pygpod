"""Tests for utility modules."""

import datetime


def test_mac_timestamp_conversion():
    """Test Mac epoch timestamp conversion."""
    from pygpod.utils.datetime import datetime_to_mac, mac_to_datetime

    # Known value: 2025-01-14 ~11:29:07 UTC
    mac_ts = 3819698947
    dt = mac_to_datetime(mac_ts)
    assert dt.year == 2025
    assert dt.month == 1
    assert dt.day == 14

    # Round trip
    back = datetime_to_mac(dt)
    assert abs(back - mac_ts) <= 1  # Allow 1 second rounding

    # Zero
    assert mac_to_datetime(0) == datetime.datetime.min


def test_ipod_path_conversion():
    """Test iPod path to/from OS path conversion."""
    from pygpod.utils.encoding import ipod_path_to_os, os_path_to_ipod

    ipod = ":iPod_Control:Music:F30:test.mp3"
    os_path = ipod_path_to_os(ipod, "/mnt/ipod")
    assert "iPod_Control" in os_path
    assert "F30" in os_path
    assert "test.mp3" in os_path

    back = os_path_to_ipod(os_path, "/mnt/ipod")
    assert back == ipod


def test_string_encoding():
    """Test MHOD string encoding/decoding."""
    from pygpod.utils.encoding import decode_mhod_string, encode_mhod_string

    text = "Hello World"
    encoded = encode_mhod_string(text, 0)  # UTF-16LE
    decoded = decode_mhod_string(encoded, 0)
    assert decoded == text

    encoded_utf8 = encode_mhod_string(text, 2)  # UTF-8
    decoded_utf8 = decode_mhod_string(encoded_utf8, 2)
    assert decoded_utf8 == text


def test_byte_helpers():
    """Test byte manipulation helpers."""
    from pygpod.utils.compat import (
        get8int,
        get16lint,
        get32lint,
        get64lint,
        put8int,
        put16lint,
        put32lint,
        put64lint,
    )

    buf = bytearray(16)
    put32lint(buf, 0, 0x12345678)
    assert get32lint(buf, 0) == 0x12345678

    put64lint(buf, 0, 0xDEADBEEFCAFEBABE)
    assert get64lint(buf, 0) == 0xDEADBEEFCAFEBABE

    put16lint(buf, 0, 0xABCD)
    assert get16lint(buf, 0) == 0xABCD

    put8int(buf, 0, 0xFF)
    assert get8int(buf, 0) == 0xFF

"""Byte-exact reference for the thumbnail pixel encoders.

The encoders turn an RGB image into the iPod's pixel formats. Their output must stay
byte-identical when they are rewritten (e.g. moved from per-pixel Python to Pillow's C
packers), so these tests pin it in two ways: the 16- and 24-bit formats against a plain
per-pixel reference implementation, and every format against SHA-256 digests of the
current output. The images are built from raw bytes, so no Pillow resampling is involved
and the digests do not depend on the Pillow version.
"""

from __future__ import annotations

import hashlib
import random
import struct

import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

from pygpod.model.artwork import PixelFormat, _encode  # noqa: E402


def _images():
    rnd = random.Random(20261025)
    out = {}
    for w, h in ((8, 5), (16, 16), (2, 1)):
        data = bytes(rnd.randrange(256) for _ in range(w * h * 3))
        out[f"noise-{w}x{h}"] = Image.frombytes("RGB", (w, h), data)
    # every channel value 0..255 in every channel position
    rows = [
        (lambda x: (x, 255 - x, (x * 7) & 255)),
        (lambda x: ((x * 3) & 255, x, 255 - x)),
        (lambda x: (255 - x, (x * 5) & 255, x)),
    ]
    data = bytes(v for row in rows for x in range(256) for v in row(x))
    out["ramp-256x3"] = Image.frombytes("RGB", (256, 3), data)
    return out


IMAGES = _images()

# SHA-256 of _encode(image, format) as written by the current (libgpod-compatible) encoders
DIGESTS = {
    ("noise-8x5", "RGB565_LE"): "82698deed25daab4b237f48d23d2cd9758d099158a04434d6c23afe6f8dc6407",
    ("noise-8x5", "RGB565_BE"): "85d00d0e1880942213a815310cdfb4f439a256b77177d69c1a8191a9a2ba1174",
    ("noise-8x5", "RGB555_LE"): "b3d2a445d48d4dab05c6e098fca1114df209d90fdaa77fba160ca0191130d155",
    ("noise-8x5", "RGB555_BE"): "e85f2b028f6fd2e3edde514c0bb3a4474c544a1858ed8e246efc44afe0da3e3d",
    ("noise-8x5", "RGB888_LE"): "9e512cc81e674809a7bed2e01d2401d3a3aaa7b087f798a8c0d590bf62ce61fa",
    ("noise-8x5", "RGB888_BE"): "77fc2a59e5511bc3dc7ff253f9338651ea644436ea8dd370980052bae52180c1",
    ("noise-8x5", "UYVY"): "1a1f09f370f66dcf1b107ee8aa3c434047e572a5a0a80738551527e4454b3496",
    ("noise-8x5", "I420"): "1d388a48cccd219412fe5afb155091271764425c0a69804897a18f888107bcce",
    (
        "noise-16x16",
        "RGB565_LE",
    ): "b1a4269cf5e43a4305b611e0a0ca23a00891de9e0fbc140af1296d1fb72090dc",
    (
        "noise-16x16",
        "RGB565_BE",
    ): "f7a81980fa4a65b7e2d0d929b0c14ca7418075f25bec2179a7ce4dc8a43c9d5f",
    (
        "noise-16x16",
        "RGB555_LE",
    ): "fe0ca15e7066d81b9e7a7df9e254e0846bbeabf98de3d00221e2beead81272af",
    (
        "noise-16x16",
        "RGB555_BE",
    ): "0a30ea0f0e6a83b3ed0379b3a709ec7d015ae347e9ca73098a262b60d5896040",
    (
        "noise-16x16",
        "RGB888_LE",
    ): "5fee280adff908d6b93d0e8d6625167edc9838d9edd6be5e33074a03744bb658",
    (
        "noise-16x16",
        "RGB888_BE",
    ): "34df97d045f3da7e6685f765b4545ecf46f8f161ec03629454fbefccbaad0ef4",
    ("noise-16x16", "UYVY"): "f8285ef2065176d7242dadd435394422defce04d006c0c804160c5a566a02755",
    ("noise-16x16", "I420"): "ff23acfbc8e4c2812e429e64c59c2aa12141d0d969ecf3d1b252625b20e6bf31",
    ("noise-2x1", "RGB565_LE"): "ee7db6bb37488123b574bdd117b2e8c2afff5a2a3f63d9f9b9c1e4ee17c5de0c",
    ("noise-2x1", "RGB565_BE"): "8362277cf11579f180fdcdead27601fc3dff08e27fc985cb916ab54bc31344b5",
    ("noise-2x1", "RGB555_LE"): "1fb5967f1fa59ee801c6a33a403521f975d67dada8b44c145bd65b5bffb3759a",
    ("noise-2x1", "RGB555_BE"): "4bad5e03fb3db0723c3f80786904c98ad8c76e7757d8cad1ae70771390f9d73e",
    ("noise-2x1", "RGB888_LE"): "c67938bb79befdaa1ef4875659ccb03610d7c3aa887b58b32bdea0a65dfb3e17",
    ("noise-2x1", "RGB888_BE"): "ec4182e8feda9f47d0947455dcd78a59ab5b407dea075ff6768ce67d1a29a7f2",
    ("noise-2x1", "UYVY"): "636ac0e68384f390807efcf58b32bfa5e2d5aa008c452e236cfb2455d36abcee",
    ("noise-2x1", "I420"): "fd1e5d2fd265dc5720508f5078fa31b10b2eeaa135fae83493eb6a180c3b2b6f",
    ("ramp-256x3", "RGB565_LE"): "328b19881e2bbcebc3516a35d5d3f847bf892f1b000207e4873513d719fada92",
    ("ramp-256x3", "RGB565_BE"): "f1418c2c76cf067c2a77462a7af467bc19a466239894fb06e009971c8e50a8eb",
    ("ramp-256x3", "RGB555_LE"): "46001e968b2ab5470117e5db8951f9512998396c7337d8fbaa92c877a5d6f12f",
    ("ramp-256x3", "RGB555_BE"): "78a7977490e7700be557110bfd3184c6f0fc473f3b5eeb4748bd7850fcf7265f",
    ("ramp-256x3", "RGB888_LE"): "d882591146f1943f1e203c010198463ddf52253e096931cd75e9892f516de1df",
    ("ramp-256x3", "RGB888_BE"): "958c4517339fa5723599c72b38a16a12eb9dafd3b42fab25220a2b70aaf837fe",
    ("ramp-256x3", "UYVY"): "b72debda08f289d95a0d2211613e93598f5bed543c5a4904bea07db0a20e8973",
    ("ramp-256x3", "I420"): "282728073f3a1098e6ec0f1f0a4b187efc65aa5675364fc87a9de0b954ee2101",
}


@pytest.mark.parametrize("name, fmt", sorted(DIGESTS))
def test_encoder_output_is_pinned(name, fmt):
    data = _encode(IMAGES[name], PixelFormat[fmt])
    assert hashlib.sha256(data).hexdigest() == DIGESTS[(name, fmt)]


def test_digests_cover_every_format_and_image():
    assert set(DIGESTS) == {(n, f.name) for n in IMAGES for f in PixelFormat}


def _reference(img, fmt):
    px = img.tobytes()
    triples = [tuple(px[i : i + 3]) for i in range(0, len(px), 3)]
    if fmt in (PixelFormat.RGB565_LE, PixelFormat.RGB565_BE):
        order = "<" if fmt == PixelFormat.RGB565_LE else ">"
        return b"".join(
            struct.pack(order + "H", ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3))
            for r, g, b in triples
        )
    if fmt in (PixelFormat.RGB555_LE, PixelFormat.RGB555_BE):
        order = "<" if fmt == PixelFormat.RGB555_LE else ">"
        return b"".join(
            struct.pack(order + "H", ((r >> 3) << 10) | ((g >> 3) << 5) | (b >> 3))
            for r, g, b in triples
        )
    if fmt == PixelFormat.RGB888_LE:
        return bytes(v for r, g, b in triples for v in (b, g, r))
    assert fmt == PixelFormat.RGB888_BE
    return px


@pytest.mark.parametrize("name", sorted(IMAGES))
@pytest.mark.parametrize(
    "fmt",
    [f for f in PixelFormat if f not in (PixelFormat.UYVY, PixelFormat.I420)],
    ids=lambda f: f.name,
)
def test_rgb_encoders_match_reference(name, fmt):
    assert _encode(IMAGES[name], fmt) == _reference(IMAGES[name], fmt)

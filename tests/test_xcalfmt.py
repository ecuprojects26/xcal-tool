import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from xcaltool import xcalfmt  # noqa: E402


def _make_xcal(image: bytes, runs, token="ABCD"):
    """Build a synthetic .xcal the same way EFILive lays them out."""
    header = (
        b"<compatibility_header><module_name>CM23xx</module_name>"
        b"<calibration_version>1.2.3.4</calibration_version>"
        b"<byte_order>BigEndian</byte_order></compatibility_header>"
    )
    hexblob = xcalfmt._encode_ihex(image, runs)
    return token.encode() + b"\r\n" + header + b"\r\n" + hexblob, header


def test_parse_then_build_is_byte_exact():
    image = bytearray([0xFF]) * 0x2000
    for i in range(0x100, 0x180):
        image[i] = i & 0xFF
    for i in range(0x1000, 0x1044):          # crosses a non-32 boundary
        image[i] = (0xA0 + i) & 0xFF
    runs = [(0x100, 0x80), (0x1000, 0x44)]
    blob, header = _make_xcal(bytes(image), runs, token="7018")

    x = xcalfmt.parse(blob)
    assert x.token == "7018"
    assert x.header == header
    assert x.runs == runs
    # image is trimmed to max written address + 1
    assert x.image == bytes(image)[: max(r[0] + r[1] for r in runs)]

    rebuilt = xcalfmt.build(x.image, x.meta())
    assert rebuilt == blob


def test_xcal_to_bin_and_back():
    image = bytearray([0xFF]) * 0x800
    for i in range(0x10, 0x30):
        image[i] = i
    runs = [(0x10, 0x20)]
    blob, _ = _make_xcal(bytes(image), runs)

    raw, meta = xcalfmt.xcal_to_bin(blob)
    assert meta["format"] == "efilive_cummins_xcal"
    assert xcalfmt.bin_to_xcal(raw, meta) == blob


def test_header_fields_parsed():
    blob, _ = _make_xcal(b"\xFF" * 0x40, [(0x0, 0x10)])
    x = xcalfmt.parse(blob)
    f = x.fields
    assert f["module_name"] == "CM23xx"
    assert f["byte_order"] == "BigEndian"


def test_is_xcal_detection():
    blob, _ = _make_xcal(b"\xFF" * 0x40, [(0x0, 0x10)])
    assert xcalfmt.is_xcal(blob)
    assert not xcalfmt.is_xcal(b"\x00" * 4096)


def test_bad_checksum_rejected():
    blob, _ = _make_xcal(b"\xFF" * 0x40, [(0x0, 0x10)])
    corrupt = blob.replace(b":10", b":11", 1)  # break a record length/checksum
    try:
        xcalfmt.parse(corrupt)
    except xcalfmt.XcalError:
        pass
    else:
        raise AssertionError("expected XcalError on bad checksum")

"""Tests for the higher-level tool features: live data, calibration compare,
batch conversion, VIN decode and reporting."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from xcaltool import (batch, calcompare, comms, j1939, livedata, report,  # noqa: E402
                      xcalfmt)


# -- live data --------------------------------------------------------------

def test_live_decode_units():
    # EEC1 engine speed: 800 rpm -> raw 6400 (0.125 rpm/bit)
    raw = int(800 / 0.125)
    frame = bytes([0xFF, 0xFF, 0xFF, raw & 0xFF, (raw >> 8) & 0xFF,
                   0xFF, 0xFF, 0xFF])
    assert j1939.decode_live(j1939.PGN_EEC1, frame)["engine_rpm"] == 800.0


def test_live_not_available_omitted():
    frame = b"\xFF" * 8
    assert j1939.decode_live(j1939.PGN_ET1, frame) == {}


def test_live_reader_polls_simulator():
    with comms.simulation_link() as link:
        vals = livedata.LiveDataReader(link).poll()
    assert vals["engine_rpm"] == 700.0
    assert vals["coolant_c"] == 88
    assert vals["battery_v"] == 13.8
    assert "def_level_pct" in vals


def test_format_value():
    assert livedata.format_value(700.0) == "700"
    assert livedata.format_value(13.8) == "13.8"


# -- calibration compare ----------------------------------------------------

def test_compare_identical():
    a = bytes(range(256))
    res = calcompare.compare_images(a, a)
    assert res.identical
    assert res.diff_bytes == 0


def test_compare_groups_nearby_bytes():
    a = bytearray(range(256))
    b = bytearray(range(256))
    b[100] ^= 0xFF
    b[102] ^= 0xFF          # within max_gap -> one run
    b[200] ^= 0xFF          # separate run
    res = calcompare.compare_images(bytes(a), bytes(b))
    assert len(res.runs) == 2
    assert res.runs[0].start == 100
    assert res.runs[0].length == 3
    assert res.runs[1].start == 200


def test_compare_different_sizes():
    res = calcompare.compare_images(b"\x00" * 10, b"\x00" * 16)
    assert not res.identical
    assert res.runs[-1].start == 10
    assert res.runs[-1].length == 6


# -- batch convert ----------------------------------------------------------

def _make_xcal():
    image = bytes((i & 0xFF) for i in range(0x2000))
    x = xcalfmt.XcalFile(token="ABCD",
                         header=b"<compatibility_header></compatibility_header>",
                         image=image, runs=[(0, 0x2000)])
    return xcalfmt.build(image, x.meta())


def test_batch_convert_folder(tmp_path):
    xcal = _make_xcal()
    (tmp_path / "one.xcal").write_bytes(xcal)
    (tmp_path / "two.xcal").write_bytes(xcal)
    (tmp_path / "notes.txt").write_text("ignore me")
    items = batch.convert_folder(str(tmp_path))
    assert len(items) == 2
    assert all(it.ok for it in items)
    for name in ("one", "two"):
        assert os.path.exists(tmp_path / f"{name}.bin")
        assert os.path.exists(tmp_path / f"{name}_efi.bin")


# -- VIN decode + report ----------------------------------------------------

def test_vin_decode_year_and_fields():
    d = report.decode_vin("1FUJGLDR9CLBP1234")
    assert d["wmi"] == "1FU"
    assert d["model_year"] == "2012"
    assert "valid_check_digit" in d


def test_vin_decode_rejects_bad_length():
    assert report.decode_vin("SHORT") == {}


def test_image_hashes_stable():
    h = report.image_hashes(b"\x00\x01\x02\x03")
    assert h["size"] == "4"
    assert len(h["sha256"]) == 64
    assert h["crc32"] == report.image_hashes(b"\x00\x01\x02\x03")["crc32"]


def test_build_report_contains_identity():
    with comms.simulation_link() as link:
        info = link.identify()
        active = link.read_dtcs(active=True)
    text = report.build_report(info, active, [], image=b"\xFF" * 32)
    assert "EF10001" in text
    assert "SHA256" in text
    assert "Active            : 2" in text

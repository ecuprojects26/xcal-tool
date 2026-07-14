import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from xcaltool import ecfg  # noqa: E402


def _sample_definition():
    return ecfg.Definition(
        title="Sample CM2350",
        ecm="CM2350",
        parameters=[
            ecfg.Parameter(name="Idle RPM", address=0x1000, size=2,
                           data_type="uint16", units="rpm", scale=1.0),
            ecfg.Parameter(name="Fuel Map", address=0x2000, size=1, rows=8,
                           cols=8, data_type="uint8", units="mg", scale=0.5),
        ],
    )


def test_to_csv_has_header_and_rows():
    csv_text = ecfg.to_csv(_sample_definition())
    lines = csv_text.strip().splitlines()
    assert lines[0].startswith("name,address")
    assert any("Idle RPM" in ln for ln in lines)
    assert any("Fuel Map" in ln for ln in lines)


def test_to_xdf_is_wellformed_xml():
    import xml.etree.ElementTree as ET

    xdf = ecfg.to_xdf(_sample_definition())
    root = ET.fromstring(xdf)
    assert root.tag == "XDFFORMAT"
    assert root.find("XDFCONSTANT") is not None   # the scalar
    assert root.find("XDFTABLE") is not None       # the 8x8 table


def test_sniff_detects_xml():
    assert ecfg.sniff(b"<?xml version='1.0'?><root/>") == "xml"


def test_parse_raises_until_implemented():
    try:
        ecfg.parse(b"\x00\x01\x02\x03")
    except ecfg.EcfgError:
        pass
    else:
        raise AssertionError("expected EcfgError")

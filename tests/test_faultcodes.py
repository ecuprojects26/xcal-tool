import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from xcaltool import faultcodes as fc  # noqa: E402


def test_clean_normalizes_cells():
    assert fc._clean(111.0) == "111"
    assert fc._clean("112") == "112"
    assert fc._clean("Not Mapped") == ""
    assert fc._clean("None") == ""
    assert fc._clean(3.5) == "3.5"
    assert fc._clean(None) == ""
    assert fc._clean("  P0606 ") == "P0606"


def test_csv_roundtrip(tmp_path):
    recs = [
        fc.FaultCode(source="CoreII", fault_code="111", spn="629",
                     j1939_fmi="12", pcode="P0606", lamp_color="Red",
                     description="ECM critical internal failure"),
        fc.FaultCode(source="CoreI", fault_code="2", j1587_fmi="4",
                     description="Exhaust gas pressure sensor circuit"),
    ]
    text = fc.to_csv(recs)
    assert text.splitlines()[0] == ",".join(fc.FIELDNAMES)
    p = tmp_path / "fc.csv"
    p.write_text(text, encoding="utf-8")
    back = fc.load_csv(str(p))
    assert len(back) == 2
    assert back[0].fault_code == "111"
    assert back[0].spn == "629"
    assert back[1].description == "Exhaust gas pressure sensor circuit"


def test_lookup_and_index():
    recs = [
        fc.FaultCode(fault_code="111", spn="629"),
        fc.FaultCode(fault_code="112", spn="635"),
    ]
    idx = fc.build_index(recs)
    assert set(idx) == {"111", "112"}
    assert fc.lookup(recs, "112").spn == "635"
    assert fc.lookup(recs, "999") is None

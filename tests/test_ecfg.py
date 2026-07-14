import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from xcaltool import ecfg  # noqa: E402


SAMPLE_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<Engineering_Tool_Config_File version="7.70.0.71">
  <compatibility_header>
    <calibration_version>7.70.0.71</calibration_version>
    <module_name>CM22xx</module_name>
    <product_id>BBZ</product_id>
  </compatibility_header>
  <parameter name="Idle_RPM">
    <id>4660</id><!-- 0x00001234 -->
    <description>Target idle speed.</description>
    <data_type xsi:type="Fixed_Point">
      <engr_units>rpm</engr_units>
      <engr_min>0.0</engr_min>
      <engr_max>65535.0</engr_max>
      <min_resolution>1.0</min_resolution>
      <sign>U</sign>
      <data_length>2</data_length>
      <scalar_multiplier>1.0</scalar_multiplier>
    </data_type>
  </parameter>
  <parameter name="Fuel_Table">
    <id>8192</id><!-- 0x00002000 -->
    <description>Fuel map.</description>
    <data_type xsi:type="Table">
      <element_count>16</element_count>
      <element_type xsi:type="Fixed_Point">
        <engr_units>mg</engr_units>
        <min_resolution>0.5</min_resolution>
        <sign>U</sign>
        <data_length>1</data_length>
      </element_type>
    </data_type>
    <offline_accessible><subfile>6</subfile><itn>00002000</itn></offline_accessible>
  </parameter>
  <parameter name="CC_Mode">
    <id>7829</id>
    <data_type xsi:type="Enumeration">
      <data_length>4</data_length>
      <value numeric_value="0" symbolic_value="OFF"/>
      <value numeric_value="1" symbolic_value="ON"/>
    </data_type>
  </parameter>
</Engineering_Tool_Config_File>
"""


def test_parse_extracts_parameters():
    defn = ecfg.parse(SAMPLE_XML)
    assert defn.ecm == "CM22xx"
    assert defn.version == "7.70.0.71"
    names = {p.name: p for p in defn.parameters}
    assert set(names) == {"Idle_RPM", "Fuel_Table", "CC_Mode"}

    idle = names["Idle_RPM"]
    assert idle.param_id == 4660
    assert idle.data_type == "uint16"
    assert idle.units == "rpm"

    fuel = names["Fuel_Table"]
    assert fuel.is_table and fuel.rows == 16
    assert fuel.data_type == "uint8"
    assert fuel.scale == 0.5
    assert fuel.address == 0x2000  # from itn

    cc = names["CC_Mode"]
    assert cc.enums == [(0, "OFF"), (1, "ON")]


def test_to_csv_has_header_and_rows():
    defn = ecfg.parse(SAMPLE_XML)
    csv_text = ecfg.to_csv(defn)
    lines = csv_text.strip().splitlines()
    assert lines[0].startswith("name,id,id_hex,kind")
    assert any("Idle_RPM" in ln for ln in lines)
    assert any("0=OFF; 1=ON" in ln for ln in lines)


def test_to_xdf_is_wellformed_xml():
    import xml.etree.ElementTree as ET

    defn = ecfg.parse(SAMPLE_XML)
    root = ET.fromstring(ecfg.to_xdf(defn))
    assert root.tag == "XDFFORMAT"
    assert root.find("XDFCONSTANT") is not None
    assert root.find("XDFTABLE") is not None


def test_parse_rejects_non_xml():
    try:
        ecfg.parse(b"\x00\x01\x02\x03not xml")
    except ecfg.EcfgError:
        pass
    else:
        raise AssertionError("expected EcfgError")

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from xcaltool import dtc  # noqa: E402
from xcaltool.ecfg import Definition, Parameter  # noqa: E402


def _defn():
    return Definition(
        title="BBZ", ecm="CM22xx", version="7.70",
        parameters=[
            Parameter(name="SCR_NOx_Fault_Lamp", param_id=1,
                      description="SCR NOx fault lamp enable"),
            Parameter(name="DPF_Soot_Derate_Fault", param_id=2,
                      description="dpf soot derate"),
            Parameter(name="Trans_Gear_Ratio_Fault", param_id=3,
                      description="transmission gear ratio fault code"),
            Parameter(name="Fuel_Tank_Level_Fault", param_id=4,
                      description="fuel tank level sender fault"),
            Parameter(name="Idle_RPM", param_id=5,
                      description="target idle speed"),
        ],
    )


def test_only_dtc_params_selected():
    entries = dtc.build_catalog(_defn())
    names = {e.name for e in entries}
    assert "Idle_RPM" not in names           # no fault hint
    assert "SCR_NOx_Fault_Lamp" in names
    assert len(entries) == 4


def test_emissions_classification():
    entries = {e.name: e for e in dtc.build_catalog(_defn())}
    assert entries["SCR_NOx_Fault_Lamp"].emissions_related is True
    assert entries["DPF_Soot_Derate_Fault"].emissions_related is True
    assert entries["Trans_Gear_Ratio_Fault"].subsystem == "transmission"
    assert entries["Trans_Gear_Ratio_Fault"].emissions_related is False
    assert entries["Fuel_Tank_Level_Fault"].subsystem == "fuel_tank"


def test_xdf_pack_excludes_emissions_by_default():
    defn = _defn()
    entries = dtc.build_catalog(defn)
    xdf = dtc.to_xdf(defn, entries)
    assert "SCR_NOx_Fault_Lamp" not in xdf
    assert "DPF_Soot_Derate_Fault" not in xdf
    assert "Trans_Gear_Ratio_Fault" in xdf


def test_xdf_pack_can_include_emissions():
    defn = _defn()
    entries = dtc.build_catalog(defn)
    xdf = dtc.to_xdf(defn, entries, include_emissions=True)
    assert "SCR_NOx_Fault_Lamp" in xdf


def test_csv_lists_all_with_flag():
    entries = dtc.build_catalog(_defn())
    csv_text = dtc.to_csv(entries)
    assert csv_text.splitlines()[0].startswith("name,id,id_hex,subsystem,emissions_related")
    assert "SCR_NOx_Fault_Lamp" in csv_text

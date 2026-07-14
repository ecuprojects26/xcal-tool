import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from xcaltool import comms, j1587, j1939, modules  # noqa: E402
from xcaltool.faultcodes import FaultCode  # noqa: E402


def test_j1939_dtc_roundtrip():
    dtc = j1939.J1939Dtc(spn=3251, fmi=2, occurrence_count=5)
    back = j1939.decode_dtc(j1939.encode_dtc(dtc))
    assert (back.spn, back.fmi, back.occurrence_count) == (3251, 2, 5)


def test_j1939_high_spn_roundtrip():
    dtc = j1939.J1939Dtc(spn=520192, fmi=31, occurrence_count=1)  # 19-bit SPN
    back = j1939.decode_dtc(j1939.encode_dtc(dtc))
    assert back.spn == 520192 and back.fmi == 31


def test_j1939_dm_decode():
    dtcs = [j1939.J1939Dtc(spn=629, fmi=12, occurrence_count=9),
            j1939.J1939Dtc(spn=100, fmi=1, occurrence_count=2)]
    payload = j1939.encode_dm(dtcs)
    decoded = j1939.decode_dm(payload)
    assert [(d.spn, d.fmi) for d in decoded] == [(629, 12), (100, 1)]


def test_j1939_canid_pgn_roundtrip():
    cid = j1939.pgn_to_canid(j1939.PGN_DM1, source=0x00)
    assert j1939.canid_to_pgn(cid) == j1939.PGN_DM1


def test_j1587_pid194_roundtrip():
    src = [j1587.J1587Dtc(code=110, fmi=0, occurrence_count=3),
           j1587.J1587Dtc(code=21, fmi=4, is_sid=True, inactive=True)]
    blob = b"".join(j1587.encode_dtc(d) for d in src)
    back = j1587.decode_pid194(blob)
    assert (back[0].code, back[0].fmi, back[0].occurrence_count) == (110, 0, 3)
    assert (back[1].code, back[1].is_sid, back[1].inactive) == (21, True, True)


def test_j1587_message_checksum():
    msg = j1587.build_message(j1587.MID_ENGINE, j1587.PID_DIAGNOSTIC, b"\x6e\x00")
    mid, pid, data = j1587.parse_message(msg)
    assert mid == j1587.MID_ENGINE and pid == j1587.PID_DIAGNOSTIC and data == b"\x6e\x00"


def test_simulation_identify_read_clear():
    link = comms.simulation_link()
    with link:
        info = link.identify()
        assert info.make == "Cummins"
        assert info.vin == "3C63R3EL8KG512345"
        assert info.serial == "79512345"
        assert "51.19.09.02" in info.calibration_id
        active = link.read_dtcs(active=True)
        assert len(active) == 2
        assert any(d.spn == 3251 for d in active)
        link.clear_dtcs(active=True)
        assert link.read_dtcs(active=True) == []


def test_tp_bam_roundtrip():
    data = bytes(range(60))                         # > 8 bytes -> multi-packet
    frames = j1939.build_tp_bam(j1939.PGN_COMPONENT_ID, data, source=0)
    cm = j1939.parse_tp_cm_bam(frames[0][1])
    assert cm[0] == len(data) and cm[2] == j1939.PGN_COMPONENT_ID
    buf = bytearray()
    for _cid, fr in frames[1:]:
        buf += fr[1:8]
    assert bytes(buf[:len(data)]) == data


def test_dm14_dm15_codec():
    d14 = j1939.encode_dm14(256, j1939.CMD_READ, 0x840000, key=0x1234)
    m = j1939.decode_dm14(d14)
    assert m["num_bytes"] == 256 and m["command"] == j1939.CMD_READ
    assert m["address"] == 0x840000 and m["key"] == 0x1234
    d15 = j1939.encode_dm15(256, j1939.STATUS_PROCEED, seed=0xBEEF)
    s = j1939.decode_dm15(d15)
    assert s["num_bytes"] == 256 and s["status"] == j1939.STATUS_PROCEED
    assert s["seed"] == 0xBEEF


def test_module_profiles_present():
    keys = modules.profile_keys()
    for k in ("CM870", "CM871", "CM2250", "CM2350", "CM2450"):
        assert k in keys
    assert modules.guess_profile("CM24xx").key == "CM2450"


def test_simulation_flash_read_write_verify():
    flasher = comms.simulation_flasher()
    flasher.connect()
    image = flasher.read_image()
    assert len(image) == flasher.profile.image_size
    modified = bytearray(image)
    modified[0:4] = b"\xDE\xAD\xBE\xEF"
    backup = flasher.write_image(bytes(modified))     # verifies by read-back
    assert backup == image
    assert flasher.read_image() == bytes(modified)
    flasher.disconnect()


def test_flash_locked_without_security():
    flasher = comms.simulation_flasher()
    flasher.security = None                            # no key provider
    flasher.connect()
    try:
        flasher.read_image()
        assert False, "expected SecurityError"
    except comms.SecurityError:
        pass
    finally:
        flasher.disconnect()


def test_annotate_descriptions():
    dtcs = [comms.DtcResult("j1939", spn=3251, fmi=2)]
    faults = [FaultCode(spn="3251", j1939_fmi="2", description="DPF pressure high")]
    comms.annotate_descriptions(dtcs, faults)
    assert dtcs[0].description == "DPF pressure high"

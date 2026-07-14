import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from xcaltool import codec  # noqa: E402


def test_extract_and_rebuild_roundtrip_is_lossless():
    header = b"CALHEADER-v1\x00\x00\x00\x00"  # 16 bytes of "header"
    payload = bytes(range(256)) * 4
    trailer = b"\xAA\xBB"
    blob = header + payload + trailer

    spec = codec.ContainerSpec(header_len=len(header), trailer_len=len(trailer))
    result = codec.extract_bin(blob, spec)
    assert result.payload == payload
    assert result.header == header
    assert result.trailer == trailer

    rebuilt = codec.rebuild_from_sidecar(result.payload, result.sidecar_dict())
    assert rebuilt == blob


def test_guess_spec_detects_ascii_header():
    header = b"THIS IS AN ASCII HEADER LONG ENOUGH\x00"
    payload = os.urandom(512)
    spec = codec.guess_spec(header + payload)
    assert spec.header_len == len(header) - 1  # stops at the NUL


def test_guess_spec_raw_bin_has_no_header():
    spec = codec.guess_spec(os.urandom(1024))
    assert spec.header_len == 0


def test_build_xcal_with_checksum_trailer():
    payload = b"\x01\x02\x03\x04"
    spec = codec.ContainerSpec(checksum="sum16")
    blob = codec.build_xcal(payload, spec=spec)
    assert blob[:-2] == payload
    assert int.from_bytes(blob[-2:], "little") == sum(payload)


def test_extract_rejects_oversized_offsets():
    try:
        codec.extract_bin(b"1234", codec.ContainerSpec(header_len=10))
    except codec.ConversionError:
        pass
    else:
        raise AssertionError("expected ConversionError")

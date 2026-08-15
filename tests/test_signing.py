from collections.abc import Iterator, Mapping
import hashlib
import json
from types import MappingProxyType

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

from touchstone.signing import (
    Ed25519Signer,
    canonical_json_bytes,
    frozen_snapshot,
    strict_json_loads,
    verify_signed_report,
)


RFC_8032_VECTORS = [
    (
        "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
        "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a",
        "",
        "e5564300c360ac729086e2cc806e828a"
        "84877f1eb8e5d974d873e06522490155"
        "5fb8821590a33bacc61e39701cf9b46b"
        "d25bf5f0595bbe24655141438e7a100b",
    ),
    (
        "4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
        "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c",
        "72",
        "92a009a9f0d4cab8720e820b5f642540"
        "a2b27b5416503f8fb3762223ebdb69da"
        "085ac1e43e15996e458f3613d0f11d8c"
        "387b2eaeb4302aeeb00d291612bb0c00",
    ),
]


def signer(seed_byte: int = 1) -> Ed25519Signer:
    return Ed25519Signer.from_seed(bytes([seed_byte]) * 32)


def test_canonical_json_bytes_is_compact_sorted_utf8() -> None:
    first = {"z": [1, True, None], "a": "NÁV ☕"}
    second = dict(reversed(list(first.items())))

    assert canonical_json_bytes(first) == canonical_json_bytes(second)
    assert canonical_json_bytes(first) == ('{"a":"NÁV ☕","z":[1,true,null]}'.encode())


def test_strict_json_loads_round_trips_canonical_bytes() -> None:
    value = {"nested": ["NÁV", {"amount": 1.25}], "valid": True}

    assert strict_json_loads(canonical_json_bytes(value)) == value
    assert strict_json_loads(canonical_json_bytes(value).decode()) == value


@pytest.mark.parametrize(
    "payload",
    [
        '{"value":1,"value":2}',
        '{"nested":{"value":1,"value":2}}',
    ],
)
def test_strict_json_loads_rejects_duplicate_keys(payload: str) -> None:
    with pytest.raises(ValueError, match="duplicate JSON key"):
        strict_json_loads(payload)


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_json_helpers_reject_nonfinite_numbers(constant: str) -> None:
    with pytest.raises(ValueError, match="JSON constant"):
        strict_json_loads('{"value":' + constant + "}")
    with pytest.raises(ValueError):
        canonical_json_bytes({"value": float(constant)})


def test_strict_json_loads_rejects_invalid_utf8_and_invalid_input_type() -> None:
    with pytest.raises(ValueError, match="UTF-8"):
        strict_json_loads(b'\xff{"value":1}')
    with pytest.raises(TypeError, match="bytes or str"):
        strict_json_loads(bytearray(b"{}"))  # type: ignore[arg-type]


def test_strict_json_loads_rejects_excessive_depth() -> None:
    with pytest.raises(ValueError, match="depth limit"):
        strict_json_loads("[" * 65 + "0" + "]" * 65)


@pytest.mark.parametrize(
    ("seed_hex", "public_key_hex", "message_hex", "signature_hex"),
    RFC_8032_VECTORS,
)
def test_rfc_8032_ed25519_known_answer_vectors(
    seed_hex: str,
    public_key_hex: str,
    message_hex: str,
    signature_hex: str,
) -> None:
    instance = Ed25519Signer.from_seed(bytes.fromhex(seed_hex))

    assert instance.public_key_record()["public_key"] == public_key_hex
    assert instance.sign(bytes.fromhex(message_hex)).hex() == signature_hex


def test_signer_construction_is_explicit_and_validated() -> None:
    with pytest.raises(TypeError, match="factory"):
        Ed25519Signer()
    with pytest.raises(TypeError, match="bytes"):
        Ed25519Signer.from_seed(bytearray(32))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="32 bytes"):
        Ed25519Signer.from_seed(b"short")
    with pytest.raises(TypeError, match="Ed25519PrivateKey"):
        Ed25519Signer.from_private_key(object())  # type: ignore[arg-type]

    private_key = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    instance = Ed25519Signer.from_private_key(private_key)
    assert instance.sign(b"message") == private_key.sign(b"message")


def test_from_env_accepts_only_lowercase_hex_seed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TOUCHSTONE_SIGNING_SEED", raising=False)
    with pytest.raises(ValueError, match="TOUCHSTONE_SIGNING_SEED"):
        Ed25519Signer.from_env()

    for invalid in ("00" * 31, "0x" + "00" * 32, "AA" * 32, "g0" * 32):
        monkeypatch.setenv("TOUCHSTONE_SIGNING_SEED", invalid)
        with pytest.raises(ValueError, match="lowercase hexadecimal"):
            Ed25519Signer.from_env()

    monkeypatch.setenv("TOUCHSTONE_SIGNING_SEED", "00" * 32)
    assert Ed25519Signer.from_env().public_key_record() == (
        Ed25519Signer.from_seed(bytes(32)).public_key_record()
    )


def test_kid_and_published_key_record_have_exact_versioned_shape() -> None:
    instance = signer()
    public_key_hex = instance.public_key_record()["public_key"]
    expected_kid = (
        "ed25519:" + hashlib.sha256(bytes.fromhex(public_key_hex)).hexdigest()
    )

    assert instance.kid == expected_kid
    assert instance.public_key_record() == {
        "algorithm": "Ed25519",
        "kid": expected_kid,
        "public_key": public_key_hex,
        "version": 1,
    }


def test_signature_envelope_is_exact_and_verifies_by_kid() -> None:
    instance = signer()
    report = {"state": "CONFIRMED", "asset_key": "eip155:1:0xabc"}

    envelope = instance.sign_report(report)
    record = instance.public_key_record()

    assert envelope == {
        "algorithm": "Ed25519",
        "kid": instance.kid,
        "report": report,
        "signature": instance.sign(canonical_json_bytes(report)).hex(),
        "version": 1,
    }
    assert verify_signed_report(envelope, {instance.kid: record}) == report
    encoded = json.dumps(envelope, ensure_ascii=False).encode()
    assert verify_signed_report(encoded, {instance.kid: record}) == report


def test_report_mapping_order_does_not_change_signature() -> None:
    instance = signer()
    first = {"state": "CONFIRMED", "asset_key": "eip155:1:0xabc"}
    second = dict(reversed(list(first.items())))

    assert (
        instance.sign_report(first)["signature"]
        == instance.sign_report(second)["signature"]
    )


def test_verification_rejects_tampered_report_and_signature() -> None:
    instance = signer()
    record = instance.public_key_record()
    envelope = instance.sign_report({"state": "CONFIRMED"})

    tampered_report = {**envelope, "report": {"state": "STALE"}}
    with pytest.raises(InvalidSignature):
        verify_signed_report(tampered_report, {instance.kid: record})

    tampered_signature = {**envelope, "signature": "00" * 64}
    with pytest.raises(InvalidSignature):
        verify_signed_report(tampered_signature, {instance.kid: record})


def test_verification_rejects_wrong_or_unresolved_key() -> None:
    first = signer(1)
    second = signer(2)
    envelope = first.sign_report({"state": "CONFIRMED"})

    wrong_kid = {**envelope, "kid": second.kid}
    with pytest.raises(InvalidSignature):
        verify_signed_report(
            wrong_kid,
            {
                first.kid: first.public_key_record(),
                second.kid: second.public_key_record(),
            },
        )

    with pytest.raises(ValueError, match="unknown signing key"):
        verify_signed_report(envelope, {})


def test_verification_rejects_mismatched_or_malformed_key_record() -> None:
    instance = signer()
    envelope = instance.sign_report({"state": "CONFIRMED"})
    record = instance.public_key_record()

    mismatched = {**record, "kid": "ed25519:" + "0" * 64}
    with pytest.raises(ValueError, match="key record kid"):
        verify_signed_report(envelope, {instance.kid: mismatched})

    malformed = {**record, "extra": True}
    with pytest.raises(ValueError, match="key record fields"):
        verify_signed_report(envelope, {instance.kid: malformed})


@pytest.mark.parametrize(
    "change",
    [
        {"version": 2},
        {"algorithm": "Ed448"},
        {"signature": "AA" * 64},
        {"extra": True},
    ],
)
def test_verification_rejects_unsupported_or_malformed_envelope(
    change: dict[str, object],
) -> None:
    instance = signer()
    envelope = {**instance.sign_report({"state": "CONFIRMED"}), **change}

    with pytest.raises(ValueError):
        verify_signed_report(
            envelope,
            {instance.kid: instance.public_key_record()},
        )


class DivergentKeyRecords(Mapping):
    """Answers a whole-mapping read and an indexed read with different records.

    A caller's mapping is under no obligation to answer two reads the same way. Freezing
    means only the first read can matter; without it, the record that is validated and the
    record that verifies the signature need not be the same object.
    """

    def __init__(
        self,
        snapshot: dict[str, object],
        indexed: dict[str, object],
    ) -> None:
        self._snapshot = snapshot
        self._indexed = indexed

    def items(self) -> object:
        return self._snapshot.items()

    def __getitem__(self, key: str) -> object:
        return self._indexed[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._snapshot)

    def __len__(self) -> int:
        return len(self._snapshot)


def test_frozen_snapshot_converts_nested_mapping_proxies_and_tuples() -> None:
    value = MappingProxyType(
        {
            "nested": MappingProxyType({"items": (1, 2)}),
            "rows": ({"date": "2026-08-12"},),
        }
    )

    assert frozen_snapshot(value, "value") == {
        "nested": {"items": [1, 2]},
        "rows": [{"date": "2026-08-12"}],
    }


def test_frozen_snapshot_refuses_a_self_referential_mapping() -> None:
    cyclic: dict[str, object] = {"state": "CONFIRMED"}
    cyclic["self"] = cyclic

    with pytest.raises(ValueError, match="value contains a reference cycle"):
        frozen_snapshot(cyclic, "value")


def test_frozen_snapshot_refuses_a_cycle_reached_through_a_sequence() -> None:
    cyclic: dict[str, object] = {"state": "CONFIRMED"}
    cyclic["rows"] = [cyclic]

    with pytest.raises(ValueError, match="value contains a reference cycle"):
        frozen_snapshot(cyclic, "value")


def test_verified_report_does_not_change_when_the_caller_mutates_the_envelope() -> None:
    instance = signer()
    envelope = instance.sign_report(
        {"state": "CONFIRMED", "controls": {"nav": True}, "rows": [{"aum": 1}]}
    )
    expected = {"state": "CONFIRMED", "controls": {"nav": True}, "rows": [{"aum": 1}]}

    verified = verify_signed_report(
        envelope,
        {instance.kid: instance.public_key_record()},
    )
    assert verified == expected

    report = envelope["report"]
    assert isinstance(report, dict)
    report["state"] = "STALE"
    report["controls"]["nav"] = False
    report["rows"][0]["aum"] = 2

    assert verified == expected


def test_verification_resolves_the_key_record_from_its_own_snapshot() -> None:
    instance = signer()
    other = signer(2)
    report = {"state": "CONFIRMED"}
    envelope = instance.sign_report(report)

    # Same kid, so the record survives the identity check and is refused, if at all, only
    # by the signature it cannot produce.
    divergent = {**other.public_key_record(), "kid": instance.kid}
    records = DivergentKeyRecords(
        {instance.kid: instance.public_key_record()},
        {instance.kid: divergent},
    )

    assert verify_signed_report(envelope, records) == report

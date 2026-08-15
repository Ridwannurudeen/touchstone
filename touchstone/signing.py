"""Canonical JSON and explicit Ed25519 report signing.

Raw seeds, public keys, and signatures use lowercase hexadecimal without a prefix.
``TOUCHSTONE_SIGNING_SEED`` must contain exactly 64 lowercase hexadecimal characters.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import os
import re

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


ALGORITHM = "Ed25519"
KEY_RECORD_VERSION = 1
SIGNATURE_ENVELOPE_VERSION = 1
SIGNING_SEED_ENV = "TOUCHSTONE_SIGNING_SEED"
_KEY_RECORD_FIELDS = frozenset({"algorithm", "kid", "public_key", "version"})
_SIGNATURE_ENVELOPE_FIELDS = frozenset(
    {"algorithm", "kid", "report", "signature", "version"}
)
_LOWER_HEX_32 = re.compile(r"[0-9a-f]{64}")
_LOWER_HEX_64 = re.compile(r"[0-9a-f]{128}")
_KID = re.compile(r"ed25519:[0-9a-f]{64}")
MAX_JSON_BYTES = 16_777_216
MAX_JSON_DEPTH = 64


def canonical_json_bytes(value: object) -> bytes:
    """Encode JSON as compact, sorted, unescaped UTF-8 bytes."""
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def strict_json_loads(value: bytes | str) -> object:
    """Decode strict UTF-8 JSON without duplicate keys or non-finite numbers."""
    if isinstance(value, bytes):
        try:
            text = value.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise ValueError("JSON bytes must be valid UTF-8") from error
    elif isinstance(value, str):
        try:
            value.encode("utf-8", errors="strict")
        except UnicodeEncodeError as error:
            raise ValueError("JSON text must be valid UTF-8") from error
        text = value
    else:
        raise TypeError("JSON input must be bytes or str")
    raw = text.encode("utf-8")
    if len(raw) > MAX_JSON_BYTES:
        raise ValueError(f"JSON input exceeds {MAX_JSON_BYTES} bytes")
    _check_json_depth(raw)
    return json.loads(
        text,
        object_pairs_hook=_object_without_duplicate_keys,
        parse_constant=_reject_json_constant,
    )


class Ed25519Signer:
    """Ed25519 signer constructed only from explicit key material."""

    __slots__ = ("_private_key",)

    def __init__(self) -> None:
        raise TypeError("use an Ed25519Signer factory method")

    @classmethod
    def from_seed(cls, seed: bytes) -> Ed25519Signer:
        """Construct from one explicit 32-byte Ed25519 seed."""
        if not isinstance(seed, bytes):
            raise TypeError("seed must be bytes")
        if len(seed) != 32:
            raise ValueError("seed must be exactly 32 bytes")
        return cls.from_private_key(Ed25519PrivateKey.from_private_bytes(seed))

    @classmethod
    def from_private_key(cls, private_key: Ed25519PrivateKey) -> Ed25519Signer:
        """Construct from an explicit cryptography Ed25519 private key."""
        if not isinstance(private_key, Ed25519PrivateKey):
            raise TypeError("private_key must be an Ed25519PrivateKey")
        instance = object.__new__(cls)
        instance._private_key = private_key
        return instance

    @classmethod
    def from_env(cls) -> Ed25519Signer:
        """Read a 32-byte seed as strict lowercase hexadecimal from the environment."""
        encoded = os.environ.get(SIGNING_SEED_ENV)
        if encoded is None:
            raise ValueError(f"{SIGNING_SEED_ENV} is not set")
        if _LOWER_HEX_32.fullmatch(encoded) is None:
            raise ValueError(
                f"{SIGNING_SEED_ENV} must be exactly 64 lowercase hexadecimal characters"
            )
        return cls.from_seed(bytes.fromhex(encoded))

    @property
    def kid(self) -> str:
        """Return the deterministic key ID derived from the raw public key."""
        return kid_for_public_key(self._public_key_bytes())

    def public_key_record(self) -> dict[str, object]:
        """Return the exact version-1 published-key record."""
        return {
            "algorithm": ALGORITHM,
            "kid": self.kid,
            "public_key": self._public_key_bytes().hex(),
            "version": KEY_RECORD_VERSION,
        }

    def sign(self, data: bytes) -> bytes:
        """Sign exact bytes with Ed25519."""
        if not isinstance(data, bytes):
            raise TypeError("data must be bytes")
        return self._private_key.sign(data)

    def sign_report(self, report: object) -> dict[str, object]:
        """Return a version-1 envelope signing the report's canonical JSON bytes."""
        report_bytes = canonical_json_bytes(report)
        normalized_report = strict_json_loads(report_bytes)
        return {
            "algorithm": ALGORITHM,
            "kid": self.kid,
            "report": normalized_report,
            "signature": self.sign(report_bytes).hex(),
            "version": SIGNATURE_ENVELOPE_VERSION,
        }

    def _public_key_bytes(self) -> bytes:
        return self._private_key.public_key().public_bytes_raw()


def verify_signed_report(
    envelope: bytes | str | Mapping[str, object],
    key_records: Mapping[str, object],
) -> object:
    """Resolve an envelope key ID, verify its key record, and return its report."""
    parsed = (
        strict_json_loads(envelope) if isinstance(envelope, (bytes, str)) else envelope
    )
    signed = _exact_mapping(parsed, _SIGNATURE_ENVELOPE_FIELDS, "signature envelope")
    _validate_version(
        signed["version"], SIGNATURE_ENVELOPE_VERSION, "signature envelope"
    )
    _validate_algorithm(signed["algorithm"], "signature envelope")

    kid = signed["kid"]
    if not isinstance(kid, str) or _KID.fullmatch(kid) is None:
        raise ValueError("signature envelope kid is invalid")
    if not isinstance(key_records, Mapping):
        raise TypeError("key_records must be a mapping")
    key_record = key_records.get(kid)
    if key_record is None:
        raise ValueError(f"unknown signing key: {kid}")

    public_key = _public_key_from_record(key_record, expected_kid=kid)
    signature = _decode_lower_hex(
        signed["signature"],
        pattern=_LOWER_HEX_64,
        field="signature envelope signature",
    )
    report = signed["report"]
    public_key.verify(signature, canonical_json_bytes(report))
    return report


def _public_key_from_record(value: object, *, expected_kid: str) -> Ed25519PublicKey:
    record = _exact_mapping(value, _KEY_RECORD_FIELDS, "key record")
    _validate_version(record["version"], KEY_RECORD_VERSION, "key record")
    _validate_algorithm(record["algorithm"], "key record")
    if record["kid"] != expected_kid:
        raise ValueError("key record kid does not match the resolved kid")
    public_key_bytes = _decode_lower_hex(
        record["public_key"],
        pattern=_LOWER_HEX_32,
        field="key record public_key",
    )
    if kid_for_public_key(public_key_bytes) != expected_kid:
        raise ValueError("key record kid does not match its public key")
    return Ed25519PublicKey.from_public_bytes(public_key_bytes)


def kid_for_public_key(public_key: bytes) -> str:
    """Derive the key ID a raw Ed25519 public key must be published under."""
    if not isinstance(public_key, bytes) or len(public_key) != 32:
        raise ValueError("public key must be exactly 32 bytes")
    return "ed25519:" + hashlib.sha256(public_key).hexdigest()


def _decode_lower_hex(value: object, *, pattern: re.Pattern[str], field: str) -> bytes:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ValueError(f"{field} must use lowercase hexadecimal")
    return bytes.fromhex(value)


def _validate_version(value: object, expected: int, context: str) -> None:
    if type(value) is not int or value != expected:
        raise ValueError(f"{context} version is not supported")


def _validate_algorithm(value: object, context: str) -> None:
    if value != ALGORITHM:
        raise ValueError(f"{context} algorithm is not supported")


def _exact_mapping(
    value: object, expected: frozenset[str], context: str
) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{context} must be an object")
    if set(value) != expected:
        raise ValueError(f"{context} fields must be exactly {sorted(expected)}")
    return value


def _object_without_duplicate_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> object:
    raise ValueError(f"invalid JSON constant: {value}")


def _check_json_depth(raw: bytes) -> None:
    depth = 0
    in_string = False
    escaped = False
    for byte in raw:
        if in_string:
            if escaped:
                escaped = False
            elif byte == 0x5C:
                escaped = True
            elif byte == 0x22:
                in_string = False
            continue
        if byte == 0x22:
            in_string = True
        elif byte in (0x5B, 0x7B):
            depth += 1
            if depth > MAX_JSON_DEPTH:
                raise ValueError(f"JSON input exceeds depth limit of {MAX_JSON_DEPTH}")
        elif byte in (0x5D, 0x7D):
            depth -= 1
            if depth < 0:
                break

"""Asset identity for the engine: one descriptor per asset, looked up by key.

The on-chain registry is already keyed by ``assetKey``. The compiler, evaluator and
epoch runner were not — they each held a USTB constant and a source-to-adapter map,
and the map was written twice. Adding an asset then meant editing every one of those
sites, which is how a second asset could not run. The descriptor is the one object
that carries what those sites need, so a new asset is a new descriptor (and its
adapter) rather than a new branch in each module.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from touchstone.normalize.ustb import normalize_ustb_payload
from touchstone.sources import SourceManifest, USTB_SOURCES


_REPO_ROOT = Path(__file__).resolve().parents[1]

# One definition. compiler.py and evaluate.py used to each carry this literal, and
# the two copies were what made "the engine is single-asset" a fact rather than a
# temporary convenience.
USTB_ASSET_KEY = "eip155:1:0x43415eb6ff9db7e26a15b704e7a3edce97d31c4e"

# The names the control language records. They are not the adapter implementations —
# those live in ``normalize`` — they are the strings a compiled control must carry
# so a source cannot be evaluated by a different source's reader.
USTB_ADAPTERS: Mapping[str, str] = MappingProxyType(
    {
        "superstate-ustb-nav-daily": "ustb-nav-daily",
        "superstate-ustb-yield": "ustb-yield",
        "superstate-ustb-holdings": "ustb-holdings",
    }
)


@dataclass(frozen=True, slots=True)
class AssetDescriptor:
    """Everything the engine needs to run one asset, and nothing it can infer.

    ``source_manifest`` is the committed JSON file that names the asset and its
    sources. ``sources`` is the runtime allowlist derived from that file (and
    pinned equal to it by ``test_sources``). Both are here because one is what a
    reader opens and the other is what the fetcher and compiler consult, and a
    descriptor that carried only one would leave the other as a module constant
    again.
    """

    asset_key: str
    display_name: str
    source_manifest: Path
    sources: tuple[SourceManifest, ...]
    adapters: Mapping[str, str]
    epoch_id_prefix: str
    normalize: Callable[..., object]
    presence_fields: Mapping[str, frozenset[str]] = field(
        default_factory=lambda: MappingProxyType({})
    )
    freshness_units: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "adapters", MappingProxyType(dict(self.adapters)))
        object.__setattr__(
            self, "presence_fields", MappingProxyType(dict(self.presence_fields))
        )
        object.__setattr__(
            self, "freshness_units", MappingProxyType(dict(self.freshness_units))
        )

    @property
    def source_by_id(self) -> Mapping[str, SourceManifest]:
        return MappingProxyType(
            {source.source_id: source for source in self.sources}
        )


USTB = AssetDescriptor(
    asset_key=USTB_ASSET_KEY,
    display_name="USTB",
    source_manifest=_REPO_ROOT / "manifests" / "sources" / "ustb.json",
    sources=USTB_SOURCES,
    adapters=USTB_ADAPTERS,
    epoch_id_prefix="ustb",
    normalize=normalize_ustb_payload,
)

ASSET_BY_KEY: Mapping[str, AssetDescriptor] = MappingProxyType(
    {USTB.asset_key: USTB}
)


def get_asset(asset_key: str) -> AssetDescriptor:
    """The shipped descriptor for this key, or a refusal.

    A missing key is not a cue to invent one. The engine only runs assets that
    were registered here; anything else is a configuration error, not a default
    to USTB that would file another asset's report under USTB's identity.
    """
    try:
        return ASSET_BY_KEY[asset_key]
    except KeyError:
        raise ValueError(f"unknown asset_key: {asset_key}") from None

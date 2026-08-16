"""The live USTB epoch producer: the thing the unattended service actually runs.

Every piece of this existed before — retrieval, normalization, evaluation, report
construction, signing, publication — and none of it was connected to a schedule. The
service refused every mode except ``--resolve-only`` and said so honestly, which meant the
registry could be deployed and reachable while the number of autonomous adapters was zero.
This is the function that closes that gap, and it is deliberately small: composition, not
new machinery.

Two rules shape it.

**Silence is recorded as silence.** A source that will not answer produces
:class:`SourceUnavailable`, which the service turns into an incident and publishes nothing.
The asset's state ages toward ``STALE`` on its own. Nothing here ever invents an observation
to fill a slot, because a report that says "we could not look" is worth more than one that
guesses and infinitely more than a gap nobody recorded.

**The sequence comes from the chain, not from a counter.** A local number would drift from
what the registry holds the moment any publication fails or is retried, and the registry
refuses an out-of-order sequence — so the next one is asked for rather than remembered.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date, datetime

from touchstone.controls import AssetState, OperationalEvent
from touchstone.epoch import run_ustb_epoch
from touchstone.evaluate import USTB_ASSET_KEY, default_ustb_controls
from touchstone.evidence import EvidenceStore
from touchstone.publish import asset_key_bytes  # noqa: F401 - re-exported for the CLI
from touchstone.quantities import utc_instant
from touchstone.report import build_observation_report
from touchstone.signing import Ed25519Signer
from touchstone.sources import (
    LiveTransport,
    SourceFetchError,
    SourceUnavailable,
    Transport,
)


class EpochProductionError(RuntimeError):
    """An epoch could not be produced for a reason that is not a source outage."""


def epoch_id_for(scheduled_at: datetime) -> str:
    """The epoch a slot at this instant is a statement about.

    One derivation, used by the producer that names the report and by the slot runner that
    asks the registry whether this epoch is already on the chain. A second implementation
    of this would be a second answer to "which day is this", and the two would disagree on
    exactly the boundary the suppression exists for — which is how the asset key came to be
    hashed two different ways and query a registry key that had never existed.
    """
    return f"ustb-{utc_instant(scheduled_at, 'scheduled_at').date().isoformat()}"


def make_producer(
    *,
    store: EvidenceStore,
    signer: Ed25519Signer,
    next_sequence: Callable[[], int],
    previous_state: Callable[[date], AssetState],
    transport: Transport | None = None,
) -> Callable[[datetime], Mapping[str, object] | None]:
    """Build the ``produce`` callable the service's slot runner expects.

    The dependencies are injected rather than constructed here so the whole path can be
    driven end to end in a test with a fixture transport and a fake chain — the alternative
    is a producer that can only be exercised against the live internet, which is a producer
    nobody exercises.
    """
    live = LiveTransport() if transport is None else transport

    def produce(scheduled_at: datetime) -> Mapping[str, object] | None:
        moment = utc_instant(scheduled_at, "scheduled_at")
        observed_on = moment.date()
        epoch_id = epoch_id_for(moment)

        try:
            epoch = run_ustb_epoch(
                transport=live,
                store=store,
                now=observed_on,
                retrieved_at=moment,
            )
        except SourceFetchError as error:
            # The one failure that is emphatically *not* a statement about the asset. It is
            # raised as the service's own source type so the slot records an outage and
            # publishes nothing, rather than an epoch failure that reads like a finding.
            raise SourceUnavailable(
                f"USTB evidence could not be retrieved: {error}"
            ) from error

        controls = default_ustb_controls()
        report = build_observation_report(
            epoch,
            controls,
            epoch_id=epoch_id,
            sequence=next_sequence(),
            publisher_kid=signer.kid,
            previous_state=previous_state(observed_on),
            event=OperationalEvent.RECONFIRMED,
        )
        return signer.sign_report(report)

    return produce


def report_uri(signed_report: Mapping[str, object]) -> str:
    """A stable name for a published report, derived from what it says.

    Deliberately not a URL. Nothing is hosted yet, and minting a `https://` URI that
    resolves to nothing would put a promise on chain that the project has not kept.
    """
    report = signed_report["report"]
    if not isinstance(report, Mapping):
        raise EpochProductionError("a signed report must carry its report")
    return f"urn:touchstone:ustb:{report['epoch_id']}:{report['sequence']}"


def asset_key() -> str:
    return USTB_ASSET_KEY

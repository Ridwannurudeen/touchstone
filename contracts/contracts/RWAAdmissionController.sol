// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.24;

import {ITouchstoneGate} from "./ITouchstoneGate.sol";

/// A consumer whose successful effect is admission, not a counter. A protocol proposes an
/// asset under the exact gate that must vouch for it, activation happens only if that gate
/// answers `allowed` at that moment, and *staying* admitted is never stored: every read and
/// every privileged action asks the gate again, so an asset whose report goes stale or loses
/// its status is suspended by arithmetic, with no keeper, no poke, and no transaction that
/// could be forgotten. The admission history — who proposed what under which gate, who
/// activated it, when — is permanent and inspectable after any suspension.
contract RWAAdmissionController {
    error UnauthorizedProposer(address caller);
    error InvalidProposer(address proposer);
    error InvalidGate(address gate);
    error InvalidAssetKey();
    error AlreadyProposed(bytes32 assetKey);
    error NotProposed(bytes32 assetKey);
    error AlreadyActive(bytes32 assetKey);
    error AdmissionRefused(bytes32 assetKey, string reason);

    event AssetProposed(
        bytes32 indexed assetKey,
        address indexed gate,
        address indexed proposer
    );
    event AssetActivated(bytes32 indexed assetKey, address indexed activator);
    event AssetUsed(
        bytes32 indexed assetKey,
        address indexed caller,
        uint256 useNumber
    );

    struct Admission {
        ITouchstoneGate gate;
        uint64 proposedAt;
        uint64 activatedAt;
        address activator;
    }

    string private constant NOT_PROPOSED = "not proposed";
    string private constant NOT_ACTIVATED = "not activated";

    /// Proposal is the one privileged role. The gate decides everything else, so
    /// activation and use stay permissionless — but an open `propose` would let anyone
    /// squat an asset key under a sham gate before the real one is named, and the gate
    /// binding is immutable once made.
    address public immutable proposer;

    mapping(bytes32 => Admission) private _admissions;
    bytes32[] private _proposed;
    uint256 public useCount;

    constructor(address proposer_) {
        if (proposer_ == address(0)) revert InvalidProposer(proposer_);
        proposer = proposer_;
    }

    /// Name the asset and the exact gate that must vouch for it. The gate carries the
    /// policy identity, policy root, control-set root, approval digest, publisher and
    /// freshness bound as immutables, so this one address is the whole requirement — and
    /// it cannot be swapped afterwards, which is what makes a later activation mean
    /// something.
    function propose(bytes32 assetKey, ITouchstoneGate gate) external {
        if (msg.sender != proposer) revert UnauthorizedProposer(msg.sender);
        if (assetKey == bytes32(0)) revert InvalidAssetKey();
        if (address(gate) == address(0) || address(gate).code.length == 0) {
            revert InvalidGate(address(gate));
        }
        if (_admissions[assetKey].proposedAt != 0) revert AlreadyProposed(assetKey);

        _admissions[assetKey] = Admission({
            gate: gate,
            proposedAt: uint64(block.timestamp),
            activatedAt: 0,
            activator: address(0)
        });
        _proposed.push(assetKey);
        emit AssetProposed(assetKey, address(gate), msg.sender);
    }

    /// Activation is the one moment admission state is written, and it happens only on the
    /// gate's word. Repeating it is refused: an admission that could be re-activated would
    /// let a stale suspension be papered over by whoever calls first after a fresh report,
    /// with the history showing two activations for one decision.
    function activate(bytes32 assetKey) external {
        Admission storage admission = _admissions[assetKey];
        if (admission.proposedAt == 0) revert NotProposed(assetKey);
        if (admission.activatedAt != 0) revert AlreadyActive(assetKey);

        (bool allowed, string memory reason) = admission.gate.check(assetKey);
        if (!allowed) revert AdmissionRefused(assetKey, reason);

        admission.activatedAt = uint64(block.timestamp);
        admission.activator = msg.sender;
        emit AssetActivated(assetKey, msg.sender);
    }

    /// Admitted *and* currently vouched for. The second half is recomputed on every call:
    /// a stored flag would be the last answer, not the current one, and "suspend when the
    /// report becomes stale or disallowed" is only automatic if nothing has to run for it.
    function isActive(
        bytes32 assetKey
    ) public view returns (bool active, string memory reason) {
        Admission storage admission = _admissions[assetKey];
        if (admission.proposedAt == 0) return (false, NOT_PROPOSED);
        if (admission.activatedAt == 0) return (false, NOT_ACTIVATED);
        return admission.gate.check(assetKey);
    }

    /// The privileged action, standing in for whatever a protocol protects — accepting
    /// collateral, opening a pool, listing. It asks the same question `isActive` asks, at
    /// execution time, so an asset suspended between blocks cannot be used on a cached
    /// answer.
    function execute(bytes32 assetKey) external {
        (bool active, string memory reason) = isActive(assetKey);
        if (!active) revert AdmissionRefused(assetKey, reason);

        uint256 nextUse = useCount + 1;
        useCount = nextUse;
        emit AssetUsed(assetKey, msg.sender, nextUse);
    }

    function admissionOf(
        bytes32 assetKey
    ) external view returns (Admission memory) {
        return _admissions[assetKey];
    }

    function proposedCount() external view returns (uint256) {
        return _proposed.length;
    }

    function proposedAt(uint256 index) external view returns (bytes32) {
        return _proposed[index];
    }
}

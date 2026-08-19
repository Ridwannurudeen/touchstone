// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.24;

import {TouchstoneRegistryV2} from "./TouchstoneRegistryV2.sol";

contract AssetGateV2 {
    error InvalidRegistry(address registry);
    error InvalidStatusMask(uint8 mask);
    error InvalidPolicyId();
    error InvalidPolicyRoot();
    error GateRefused(bytes32 assetKey, string reason);

    event Demanded(bytes32 indexed assetKey, address indexed caller);

    string private constant UNKNOWN_ASSET = "unknown asset";
    string private constant INVALID_REPORT_DIGEST = "invalid report digest";
    string private constant POLICY_ID_MISMATCH = "policy-id mismatch";
    string private constant POLICY_ROOT_MISMATCH = "policy-root mismatch";
    string private constant STATUS_NOT_ALLOWED = "status not allowed";
    string private constant OBSERVATION_TOO_OLD = "observation too old";
    string private constant WRONG_PUBLISHER = "wrong publisher";
    string private constant CONTROL_SET_MISMATCH = "control-set mismatch";
    string private constant ALLOWED = "allowed";

    TouchstoneRegistryV2 public immutable registry;
    uint8 public immutable allowedStatuses;
    uint64 public immutable maxObservationAge;
    address public immutable requiredPublisher;
    bytes32 public immutable expectedPolicyId;
    bytes32 public immutable expectedPolicyRoot;
    bytes32 public immutable requiredControlSetRoot;

    constructor(
        TouchstoneRegistryV2 registry_,
        uint8 allowedStatuses_,
        uint64 maxObservationAge_,
        address requiredPublisher_,
        bytes32 expectedPolicyId_,
        bytes32 expectedPolicyRoot_,
        bytes32 requiredControlSetRoot_
    ) {
        if (address(registry_) == address(0) || address(registry_).code.length == 0) {
            revert InvalidRegistry(address(registry_));
        }
        if (allowedStatuses_ == 0 || allowedStatuses_ & 0xf0 != 0) {
            revert InvalidStatusMask(allowedStatuses_);
        }
        if (expectedPolicyId_ == bytes32(0)) revert InvalidPolicyId();
        if (expectedPolicyRoot_ == bytes32(0)) revert InvalidPolicyRoot();

        registry = registry_;
        allowedStatuses = allowedStatuses_;
        maxObservationAge = maxObservationAge_;
        requiredPublisher = requiredPublisher_;
        expectedPolicyId = expectedPolicyId_;
        expectedPolicyRoot = expectedPolicyRoot_;
        requiredControlSetRoot = requiredControlSetRoot_;
    }

    function check(bytes32 assetKey) public view returns (bool allowed, string memory reason) {
        TouchstoneRegistryV2.Report memory report = registry.getLatestReport(assetKey);
        if (report.sequence == 0) return (false, UNKNOWN_ASSET);
        if (report.reportDigest == bytes32(0)) return (false, INVALID_REPORT_DIGEST);
        if (report.policyId != expectedPolicyId) return (false, POLICY_ID_MISMATCH);
        if (report.policyRoot != expectedPolicyRoot) return (false, POLICY_ROOT_MISMATCH);

        uint8 statusBit = uint8(1) << uint8(report.status);
        if (allowedStatuses & statusBit == 0) return (false, STATUS_NOT_ALLOWED);

        if (
            block.timestamp < report.observedAt ||
            block.timestamp > report.validUntil ||
            block.timestamp - report.observedAt > maxObservationAge
        ) {
            return (false, OBSERVATION_TOO_OLD);
        }

        if (
            (requiredPublisher == address(0) &&
                !registry.isPublisherAuthorized(report.publisher)) ||
            (requiredPublisher != address(0) &&
                !registry.isPublisherFor(requiredPublisher, report.publisher))
        ) {
            return (false, WRONG_PUBLISHER);
        }

        if (
            requiredControlSetRoot != bytes32(0) &&
            report.controlSetRoot != requiredControlSetRoot
        ) {
            return (false, CONTROL_SET_MISMATCH);
        }

        return (true, ALLOWED);
    }

    function demand(bytes32 assetKey) external {
        (bool allowed, string memory reason) = check(assetKey);
        if (!allowed) revert GateRefused(assetKey, reason);
        emit Demanded(assetKey, msg.sender);
    }
}

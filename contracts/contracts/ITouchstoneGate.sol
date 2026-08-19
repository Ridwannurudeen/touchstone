// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.24;

/// The one question a consumer asks. Both gate generations answer it with this exact
/// selector, so a consumer written against the interface works unchanged when its gate is
/// upgraded from the v1 registry to the v2 — which is the point: GuardedAction used to
/// import the concrete v1 type, and every consumer would have needed redeploying to adopt
/// a gate that verifies signatures onchain.
interface ITouchstoneGate {
    function check(
        bytes32 assetKey
    ) external view returns (bool allowed, string memory reason);
}

// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.24;

import {ITouchstoneGate} from "./ITouchstoneGate.sol";

contract GuardedAction {
    error InvalidGate(address gate);
    error InvalidAssetKey();
    error ActionRefused(bytes32 assetKey, string reason);

    event ActionExecuted(
        bytes32 indexed assetKey,
        address indexed caller,
        uint256 actionNumber
    );

    ITouchstoneGate public immutable gate;
    bytes32 public immutable assetKey;
    uint256 public actionCount;

    constructor(ITouchstoneGate gate_, bytes32 assetKey_) {
        if (address(gate_) == address(0) || address(gate_).code.length == 0) {
            revert InvalidGate(address(gate_));
        }
        if (assetKey_ == bytes32(0)) revert InvalidAssetKey();
        gate = gate_;
        assetKey = assetKey_;
    }

    function execute() external {
        (bool allowed, string memory reason) = gate.check(assetKey);
        if (!allowed) revert ActionRefused(assetKey, reason);

        uint256 nextAction = actionCount + 1;
        actionCount = nextAction;
        emit ActionExecuted(assetKey, msg.sender, nextAction);
    }
}

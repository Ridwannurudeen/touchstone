// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.24;

interface ITouchstoneGuard {
    function check(bytes32 assetKey) external view returns (bool allowed, string memory reason);
}

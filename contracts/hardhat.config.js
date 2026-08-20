require("@nomicfoundation/hardhat-toolbox");
// OKLink source verification for X Layer. OKLink's docs recommend `npx hardhat okverify`
// over vanilla `hardhat verify`, whose docs only promise Etherscan/Blockscout/Sourcify.
// The API key is an OKLink account credential and never lives in this file.
require("@okxweb3/hardhat-explorer-verify");

// X Layer testnet, added 2026-08-15 with owner approval. Chain 1952 at
// https://testrpc.xlayer.tech/terigon, verified against chainlist.org/chain/1952.
//
// The deprecated testnet on chain 195 must never be used. Naming the chain id here is not
// enough on its own to prevent that — an endpoint can answer for whatever chain it likes —
// so `deploy.js` requires TOUCHSTONE_DEPLOY_CONFIRM_CHAIN_ID to name the chain the endpoint
// actually reports, and the publisher's preflight refuses a mismatch before signing.
//
// The deployer key is read from the environment and is never written here. It belongs to
// the owner and does not live on the publishing host: see docs/KEY-MANAGEMENT.md.
const XLAYER_TESTNET_RPC = "https://testrpc.xlayer.tech/terigon";
const XLAYER_MAINNET_RPC = "https://rpc.xlayer.tech";
const deployerKey = process.env.TOUCHSTONE_DEPLOYER_PRIVATE_KEY;

// The local chain starts at the retrieval instant of the committed 2026-08-14 capture.
// AssetGate compares block.timestamp against a report's observedAt, so a wall-clock
// chain would age the fixture epoch past maxObservationAge and the hero loop would stop
// reproducing a day after the fixtures were captured.
const FIXTURE_EPOCH_RETRIEVED_AT = "2026-08-14T17:08:12Z";

// Read from `solidity.json` rather than written here, so the compiler settings have one
// source that both Hardhat and `scripts/build_release.py` consume. The release builder used
// to recover them by regex over this file, which selected the first object that *looked*
// like a solidity block — an unused one earlier in the file, or a commented-out line, was
// reported as the configuration the contracts were built with. A release document that names
// a compiler setting the compiler never used is worse than one that omits it, and no amount
// of pattern-matching JavaScript makes that safe. Data belongs in a data file.
//
// `evmVersion` is stated explicitly. Leaving it to the toolchain's default meant the release
// document recorded nothing while the build recorded `paris`.
const solidity = require("./solidity.json");

module.exports = {
  solidity,
  okxweb3explorer: {
    apiKey: process.env.OKLINK_API_KEY,
  },
  etherscan: {
    // The verify plugin's built-in list maps `xlayertest` to chain 195, the DEPRECATED
    // testnet this project must never touch. This entry teaches it chain 1952 (terigon),
    // pointed at the same OKLink endpoint family the migrated explorer serves.
    customChains: [
      {
        network: "xLayerTestnet",
        chainId: 1952,
        urls: {
          apiURL:
            "https://www.oklink.com/api/v5/explorer/contract/verify-source-code-plugin/XLAYER_TESTNET",
          browserURL: "https://www.oklink.com/xlayer-test",
        },
      },
    ],
  },
  networks: {
    hardhat: {
      initialDate: FIXTURE_EPOCH_RETRIEVED_AT,
    },
    xLayerTestnet: {
      url: XLAYER_TESTNET_RPC,
      chainId: 1952,
      // Absent rather than empty when the key is unset, so an accidental invocation fails
      // with "no signer" instead of selecting whatever default hardhat would supply.
      accounts: deployerKey ? [deployerKey] : [],
    },
    // `deploy.js` has always known xlayer-mainnet is chain 196; hardhat did not, so the
    // network could be named but never reached. `chainId` is declared here as well as
    // confirmed at runtime: hardhat refuses to send if the endpoint disagrees with this
    // number, which is one more check between a typo'd RPC and a real transaction. The
    // deployer key is still absent unless the environment supplies it.
    xLayerMainnet: {
      url: XLAYER_MAINNET_RPC,
      chainId: 196,
      accounts: deployerKey ? [deployerKey] : [],
    },
  },
};

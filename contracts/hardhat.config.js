require("@nomicfoundation/hardhat-toolbox");

// Reserved network name: xLayerTestnet. Add only an audited RPC endpoint.

// The local chain starts at the retrieval instant of the committed 2026-08-14 capture.
// AssetGate compares block.timestamp against a report's observedAt, so a wall-clock
// chain would age the fixture epoch past maxObservationAge and the hero loop would stop
// reproducing a day after the fixtures were captured.
const FIXTURE_EPOCH_RETRIEVED_AT = "2026-08-14T17:08:12Z";

module.exports = {
  solidity: {
    version: "0.8.24",
    settings: {
      optimizer: {
        enabled: true,
        runs: 200,
      },
    },
  },
  networks: {
    hardhat: {
      initialDate: FIXTURE_EPOCH_RETRIEVED_AT,
    },
  },
};

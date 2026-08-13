require("@nomicfoundation/hardhat-toolbox");

// Reserved network name: xLayerTestnet. Add only an audited RPC endpoint.

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
};

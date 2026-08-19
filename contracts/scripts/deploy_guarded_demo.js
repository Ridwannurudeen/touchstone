// Deploy the permitted/refused demonstration pair, and record both outcomes on chain.
//
// Two GuardedAction consumers share one policy-pinned gate. The first is bound to a policy
// key whose latest report is CONFIRMED, so its action executes. The second is bound to a key
// the registry has never seen, so its action reverts — and the refusal is sent as a real
// transaction with a manual gas limit, because a refusal that only ever happens in an
// eth_call is a refusal nobody can point to on an explorer. Both outcomes are read back from
// receipts rather than asserted.

const { ethers, network } = require("hardhat");

function required(name) {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is required`);
  return value;
}

async function main() {
  const gateAddress = ethers.getAddress(required("TOUCHSTONE_GATE_ADDRESS"));
  const permittedKey = required("TOUCHSTONE_PERMITTED_KEY");
  const chainId = (await ethers.provider.getNetwork()).chainId;
  if (process.env.TOUCHSTONE_DEPLOY_CONFIRM_CHAIN_ID !== String(chainId)) {
    throw new Error(
      `refusing chain ${chainId}: set TOUCHSTONE_DEPLOY_CONFIRM_CHAIN_ID=${chainId}`,
    );
  }
  if (!ethers.isHexString(permittedKey, 32)) {
    throw new Error("TOUCHSTONE_PERMITTED_KEY must be 32-byte hex");
  }
  // A key derived from an identifier no report has ever been published under. Deterministic,
  // so the demo is reproducible; never registered, so the refusal is genuine rather than
  // staged.
  const refusedKey = ethers.keccak256(
    ethers.toUtf8Bytes("eip155:1:0x0000000000000000000000000000000000000001"),
  );

  const factory = await ethers.getContractFactory("GuardedAction");

  const permitted = await factory.deploy(gateAddress, permittedKey);
  await permitted.waitForDeployment();
  const permittedReceipt = await permitted.deploymentTransaction().wait(1);
  console.log(`GuardedAction (permitted key) ${await permitted.getAddress()}`);
  console.log(`  deploy block ${permittedReceipt.blockNumber}`);

  const refused = await factory.deploy(gateAddress, refusedKey);
  await refused.waitForDeployment();
  const refusedReceipt = await refused.deploymentTransaction().wait(1);
  console.log(`GuardedAction (refused key)   ${await refused.getAddress()}`);
  console.log(`  deploy block ${refusedReceipt.blockNumber}`);

  const act = await permitted.execute();
  const actReceipt = await act.wait(1);
  console.log(
    `\npermitted execute(): status ${actReceipt.status} tx ${act.hash}`,
  );
  console.log(`  actionCount now ${await permitted.actionCount()}`);

  // estimateGas would refuse to send this at all, which is correct for production and
  // useless for evidence. The fixed limit makes the revert a real on-chain transaction.
  let refusedTx;
  try {
    refusedTx = await refused.execute({ gasLimit: 120000 });
    const refusedActReceipt = await refusedTx.wait(1);
    console.log(
      `refused execute():  status ${refusedActReceipt.status} tx ${refusedTx.hash}`,
    );
  } catch (error) {
    const receipt = error.receipt;
    if (receipt) {
      console.log(
        `refused execute():  status ${receipt.status} tx ${receipt.hash ?? refusedTx.hash}`,
      );
    } else {
      throw error;
    }
  }
  console.log(`  actionCount stays ${await refused.actionCount()}`);
  console.log(`\nnetwork ${network.name} chain ${chainId}`);
}

main().catch((error) => {
  console.error(error.shortMessage ?? error.message);
  process.exitCode = 1;
});

// Deploy the RWAAdmissionController against an existing gate, and walk the whole admission
// story on chain: propose the policy key under its gate, activate it on the gate's word,
// execute once — and then propose a key no report has ever been published under and record
// its refusal as a real transaction. Every outcome is read back from receipts rather than
// asserted, and the refusal is sent with a manual gas limit for the same reason
// deploy_guarded_demo.js sends one: a refusal that only ever happens in an eth_call is a
// refusal nobody can point to on an explorer.

const { ethers, network } = require("hardhat");

function required(name) {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is required`);
  return value;
}

async function main() {
  const gateAddress = ethers.getAddress(required("TOUCHSTONE_GATE_ADDRESS"));
  const admittedKey = required("TOUCHSTONE_ADMITTED_KEY");
  const chainId = (await ethers.provider.getNetwork()).chainId;
  if (process.env.TOUCHSTONE_DEPLOY_CONFIRM_CHAIN_ID !== String(chainId)) {
    throw new Error(
      `refusing chain ${chainId}: set TOUCHSTONE_DEPLOY_CONFIRM_CHAIN_ID=${chainId}`,
    );
  }
  if (!ethers.isHexString(admittedKey, 32)) {
    throw new Error("TOUCHSTONE_ADMITTED_KEY must be 32-byte hex");
  }
  const code = await ethers.provider.getCode(gateAddress);
  if (code === "0x") {
    throw new Error(`no contract at ${gateAddress} on chain ${chainId}`);
  }
  // Deterministic and never registered, so the refusal is genuine rather than staged.
  const refusedKey = ethers.keccak256(
    ethers.toUtf8Bytes("eip155:1:0x0000000000000000000000000000000000000001"),
  );

  const [deployer] = await ethers.getSigners();
  const controller = await ethers.deployContract("RWAAdmissionController", [
    deployer.address,
  ]);
  await controller.waitForDeployment();
  const deployReceipt = await controller.deploymentTransaction().wait(1);
  console.log(`RWAAdmissionController ${await controller.getAddress()}`);
  console.log(`  deploy block ${deployReceipt.blockNumber}`);
  console.log(`  proposer     ${deployer.address}`);

  for (const [label, key] of [
    ["admitted", admittedKey],
    ["refused", refusedKey],
  ]) {
    const proposal = await controller.propose(key, gateAddress);
    const proposalReceipt = await proposal.wait(1);
    console.log(
      `\npropose(${label}) status ${proposalReceipt.status} tx ${proposal.hash}`,
    );
  }

  const activation = await controller.activate(admittedKey);
  const activationReceipt = await activation.wait(1);
  console.log(
    `\nactivate(admitted) status ${activationReceipt.status} tx ${activation.hash}`,
  );
  const [active, reason] = await controller.isActive(admittedKey);
  console.log(`  isActive ${active} (${reason})`);

  const use = await controller.execute(admittedKey);
  const useReceipt = await use.wait(1);
  console.log(`execute(admitted)  status ${useReceipt.status} tx ${use.hash}`);
  console.log(`  useCount now ${await controller.useCount()}`);

  // The gate has never seen this key, so activation reverts on chain. estimateGas would
  // refuse to send it; the fixed limit makes the refusal a citable transaction.
  let refusedTx;
  try {
    refusedTx = await controller.activate(refusedKey, { gasLimit: 150000 });
    const refusedReceipt = await refusedTx.wait(1);
    console.log(
      `activate(refused)  status ${refusedReceipt.status} tx ${refusedTx.hash}`,
    );
  } catch (error) {
    const receipt = error.receipt;
    if (receipt) {
      console.log(
        `activate(refused)  status ${receipt.status} tx ${receipt.hash ?? refusedTx.hash}`,
      );
    } else {
      throw error;
    }
  }
  const [refusedActive, refusedReason] = await controller.isActive(refusedKey);
  console.log(`  isActive ${refusedActive} (${refusedReason})`);
  console.log(`\nnetwork ${network.name} chain ${chainId}`);
}

main().catch((error) => {
  console.error(error.shortMessage ?? error.message);
  process.exitCode = 1;
});

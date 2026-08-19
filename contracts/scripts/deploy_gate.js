// Deploy one AssetGate against an existing registry, and read its decision back.
//
// The gate is the consumer side of the product: a contract that refuses to act on an asset
// whose latest report does not satisfy a policy it fixed at construction. Until now it existed
// only inside the local end-to-end run, which is why `docs/LIMITATIONS.md` records "live
// consumer contract gating on state: 0".
//
// V1 remains the default deployment path. Set TOUCHSTONE_REGISTRY_VERSION=2 to deploy
// AssetGateV2, which additionally requires exact nonzero policy identity and policy roots.
// The latest registry report digest remains canonical and is deliberately not pinned here.
//
// Two constructor choices are deliberate and are the whole point of the deployment.
//
// `allowedStatuses` is CONFIRMED only. The asset's latest report is UNVERIFIABLE, so this gate
// refuses it. That is not a failed deployment — it is the demonstration. A gate whose mask was
// widened until it returned `allowed` would be a consumer contract that accepts an unverified
// asset, which is the opposite of what a gate is for, and it would be the same mistake as
// choosing a control set because it produces a green result.
//
// `requiredControlSetRoot` is explicit and nonzero. A gate pinned to zero opts out of the
// approved control-set binding, which would let a consumer claim policy protection without
// naming the policy root it actually requires. The root is immutable once constructed.

const { ethers, network } = require("hardhat");

const CONFIRMED = 1 << 0;

function required(name) {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is required`);
  return value;
}

async function main() {
  const registryAddress = ethers.getAddress(
    required("TOUCHSTONE_REGISTRY_ADDRESS"),
  );
  const publisher = ethers.getAddress(required("TOUCHSTONE_PUBLISHER_ADDRESS"));
  const assetKey = required("TOUCHSTONE_ASSET_KEY");
  const maxObservationAge = BigInt(required("TOUCHSTONE_MAX_OBSERVATION_AGE"));
  const registryVersion = process.env.TOUCHSTONE_REGISTRY_VERSION ?? "1";
  if (registryVersion !== "1" && registryVersion !== "2") {
    throw new Error("TOUCHSTONE_REGISTRY_VERSION must be 1 or 2");
  }
  const requiredControlSetRoot = required("TOUCHSTONE_REQUIRED_CONTROL_SET_ROOT");
  if (!ethers.isHexString(requiredControlSetRoot, 32)) {
    throw new Error(
      "TOUCHSTONE_REQUIRED_CONTROL_SET_ROOT must be a 32-byte hexadecimal value",
    );
  }
  if (requiredControlSetRoot.toLowerCase() === ethers.ZeroHash) {
    throw new Error("TOUCHSTONE_REQUIRED_CONTROL_SET_ROOT must be nonzero");
  }
  const expectedPolicyId =
    registryVersion === "2" ? required("TOUCHSTONE_EXPECTED_POLICY_ID") : null;
  const expectedPolicyRoot =
    registryVersion === "2" ? required("TOUCHSTONE_EXPECTED_POLICY_ROOT") : null;
  for (const [name, value] of [
    ["TOUCHSTONE_EXPECTED_POLICY_ID", expectedPolicyId],
    ["TOUCHSTONE_EXPECTED_POLICY_ROOT", expectedPolicyRoot],
  ]) {
    if (value !== null && !ethers.isHexString(value, 32)) {
      throw new Error(`${name} must be a 32-byte hexadecimal value`);
    }
    if (value !== null && value.toLowerCase() === ethers.ZeroHash) {
      throw new Error(`${name} must be nonzero`);
    }
  }

  const provider = ethers.provider;
  const chainId = (await provider.getNetwork()).chainId;
  const confirmed = process.env.TOUCHSTONE_DEPLOY_CONFIRM_CHAIN_ID;
  if (confirmed !== String(chainId)) {
    throw new Error(
      `refusing to deploy to chain ${chainId}: set TOUCHSTONE_DEPLOY_CONFIRM_CHAIN_ID=${chainId}`,
    );
  }

  // A gate pointed at an address with no code would deploy happily and then revert on every
  // call, which is a live contract that proves nothing.
  const code = await provider.getCode(registryAddress);
  if (code === "0x")
    throw new Error(`no contract at ${registryAddress} on chain ${chainId}`);

  console.log(`network            ${network.name} (chain ${chainId})`);
  console.log(
    `registry           ${registryAddress}  (${(code.length - 2) / 2} bytes)`,
  );
  console.log(`allowedStatuses    ${CONFIRMED} (CONFIRMED only)`);
  console.log(`maxObservationAge  ${maxObservationAge}s`);
  console.log(`requiredPublisher  ${publisher}`);
  if (registryVersion === "2") {
    console.log(`expectedPolicyId   ${expectedPolicyId}`);
    console.log(`expectedPolicyRoot ${expectedPolicyRoot}`);
  }
  console.log(`requiredControlSetRoot  ${requiredControlSetRoot}\n`);

  const contractName = registryVersion === "2" ? "AssetGateV2" : "AssetGate";
  const constructorArguments = [
    registryAddress,
    CONFIRMED,
    maxObservationAge,
    publisher,
    ...(registryVersion === "2" ? [expectedPolicyId, expectedPolicyRoot] : []),
    requiredControlSetRoot,
  ];
  const factory = await ethers.getContractFactory(contractName);
  const gate = await factory.deploy(...constructorArguments);
  await gate.waitForDeployment();
  const address = await gate.getAddress();
  const receipt = await gate.deploymentTransaction().wait(1);

  console.log(`${contractName} deployed ${address}`);
  console.log(`  block            ${receipt.blockNumber}`);
  console.log(`  gas used         ${receipt.gasUsed}`);

  // Read the decision back from the chain rather than asserting what it should be.
  const [allowed, reason] = await gate.check(assetKey);
  console.log(`\ncheck(${assetKey})`);
  console.log(`  allowed          ${allowed}`);
  console.log(`  reason           ${reason}`);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});

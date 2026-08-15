// Deploy TouchstoneRegistry and emit the deployment manifest the publisher reads.
//
// The manifest is produced here rather than written by hand because three of its fields
// can only be known after deployment — the address, the runtime bytecode digest, and the
// deployment block — and a hand-copied digest is a digest nobody checked.
//
// Deploying to any network other than the local development chain is owner-gated. The
// guard is deliberately a positive confirmation naming the exact chain id: a boolean flag
// would be satisfied by a stale shell export, while naming the chain cannot be satisfied
// by accident.

const { createHash } = require("node:crypto");
const { writeFileSync } = require("node:fs");
const hre = require("hardhat");

const LOCAL_CHAIN_ID = 31337n;
const CONFIRM_ENV = "TOUCHSTONE_DEPLOY_CONFIRM_CHAIN_ID";
const NETWORK_BY_CHAIN_ID = {
  31337: "hardhat-local",
};

async function deploy({
  publisherAddress,
  operationsAddress = null,
  reporterPublicKey,
  network = null,
  confirmations = 1,
  maxFeeWei = null,
  rpcUrl = null,
}) {
  const { ethers } = hre;
  const chainId = (await ethers.provider.getNetwork()).chainId;
  if (chainId !== LOCAL_CHAIN_ID) {
    const confirmed = process.env[CONFIRM_ENV];
    if (confirmed !== String(chainId)) {
      throw new Error(
        `refusing to deploy to chain ${chainId}: set ${CONFIRM_ENV}=${chainId} to confirm`,
      );
    }
  }

  const [deployer] = await ethers.getSigners();
  const publisher = ethers.getAddress(publisherAddress);
  if (publisher === (await deployer.getAddress())) {
    throw new Error(
      "the publisher must not be the deployer; the identity that owns the registry must not run unattended",
    );
  }
  const operations = operationsAddress
    ? ethers.getAddress(operationsAddress)
    : null;
  if (
    operations !== null &&
    (operations === publisher || operations === (await deployer.getAddress()))
  ) {
    throw new Error(
      "the operations identity must be distinct from the deployer and the publisher",
    );
  }
  if (!/^[0-9a-f]{64}$/.test(reporterPublicKey)) {
    throw new Error("reporterPublicKey must be 32 lowercase hexadecimal bytes");
  }

  const registry = await ethers.deployContract("TouchstoneRegistry", [chainId]);
  await registry.waitForDeployment();
  const deploymentReceipt = await registry.deploymentTransaction().wait();
  const address = await registry.getAddress();

  const authorization = await registry.authorizePublisher(publisher);
  await authorization.wait();
  if (!(await registry.isPublisherAuthorized(publisher))) {
    throw new Error("publisher authorization did not take effect");
  }

  const code = await ethers.provider.getCode(address);
  if (code === "0x") {
    throw new Error(`no runtime bytecode at ${address} after deployment`);
  }
  const runtimeSha256 = createHash("sha256")
    .update(Buffer.from(code.slice(2), "hex"))
    .digest("hex");

  const manifest = {
    manifest_version: 1,
    network: network ?? NETWORK_BY_CHAIN_ID[String(chainId)] ?? null,
    chain_id: Number(chainId),
    rpc_url: rpcUrl ?? hre.network.config.url ?? "http://127.0.0.1:8545",
    registry_address: address,
    registry_runtime_bytecode_sha256: runtimeSha256,
    publisher_address: publisher,
    deployer_address: await deployer.getAddress(),
    confirmations,
    deployment_block: deploymentReceipt.blockNumber,
    reporting_keys: [
      {
        kid: `ed25519:${createHash("sha256")
          .update(Buffer.from(reporterPublicKey, "hex"))
          .digest("hex")}`,
        public_key: reporterPublicKey,
        state: "active",
      },
    ],
  };
  if (operations !== null) {
    manifest.operations_address = operations;
  }
  if (maxFeeWei !== null) {
    manifest.max_fee_wei = maxFeeWei;
  }
  if (manifest.network === null) {
    throw new Error(
      `chain ${chainId} has no manifest network name; pass one explicitly so the manifest is not written with a null`,
    );
  }
  return { registry, manifest };
}

async function main() {
  const required = (name) => {
    const value = process.env[name];
    if (!value) {
      throw new Error(`${name} is required`);
    }
    return value;
  };
  const { manifest } = await deploy({
    publisherAddress: required("TOUCHSTONE_PUBLISHER_ADDRESS"),
    operationsAddress: process.env.TOUCHSTONE_OPERATIONS_ADDRESS ?? null,
    reporterPublicKey: required("TOUCHSTONE_REPORTER_PUBLIC_KEY"),
    network: process.env.TOUCHSTONE_NETWORK ?? null,
    confirmations: Number(process.env.TOUCHSTONE_CONFIRMATIONS ?? 1),
    maxFeeWei: process.env.TOUCHSTONE_MAX_FEE_WEI
      ? Number(process.env.TOUCHSTONE_MAX_FEE_WEI)
      : null,
    rpcUrl: process.env.TOUCHSTONE_RPC_URL ?? null,
  });
  const serialized = `${JSON.stringify(manifest, null, 2)}\n`;
  const destination = process.env.TOUCHSTONE_MANIFEST_OUT;
  if (destination) {
    writeFileSync(destination, serialized, { encoding: "utf-8" });
  }
  process.stdout.write(serialized);
}

module.exports = { deploy, CONFIRM_ENV };

if (require.main === module) {
  main().catch((error) => {
    process.exitCode = 1;
    console.error(error.message);
  });
}

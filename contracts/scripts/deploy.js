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
const NETWORKS = ["hardhat-local", "xlayer-testnet", "xlayer-mainnet"];

async function deploy({
  publisherAddress,
  operationsAddress,
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

  // --------------------------------------------------------------------------------
  // Everything below is validated BEFORE the first transaction is sent. A field checked
  // afterwards is a field that cannot be fixed: on a public chain, an invalid manifest
  // discovered after deployment leaves a deployed and authorized registry that nothing
  // can publish to, and neither send can be undone.
  // --------------------------------------------------------------------------------
  const [deployer] = await ethers.getSigners();
  const deployerAddress = await deployer.getAddress();
  const publisher = ethers.getAddress(publisherAddress);
  if (publisher === deployerAddress) {
    throw new Error(
      "the publisher must not be the deployer; the identity that owns the registry must not run unattended",
    );
  }
  if (!operationsAddress) {
    throw new Error(
      "operationsAddress is required; an unstated role address cannot be shown to be separate",
    );
  }
  const operations = ethers.getAddress(operationsAddress);
  if (operations === publisher || operations === deployerAddress) {
    throw new Error(
      "the operations identity must be distinct from the deployer and the publisher",
    );
  }
  if (!/^[0-9a-f]{64}$/.test(reporterPublicKey)) {
    throw new Error("reporterPublicKey must be 32 lowercase hexadecimal bytes");
  }
  const resolvedNetwork = network ?? NETWORK_BY_CHAIN_ID[String(chainId)] ?? null;
  if (!NETWORKS.includes(resolvedNetwork)) {
    throw new Error(
      `network must be one of ${NETWORKS.join(", ")}; chain ${chainId} resolved to ${resolvedNetwork}`,
    );
  }
  if (!Number.isInteger(confirmations) || confirmations < 1) {
    throw new Error("confirmations must be a positive integer");
  }
  if (maxFeeWei !== null && (!Number.isInteger(maxFeeWei) || maxFeeWei < 1)) {
    throw new Error("maxFeeWei must be a positive integer");
  }
  const resolvedRpcUrl =
    rpcUrl ?? hre.network.config.url ?? "http://127.0.0.1:8545";
  if (resolvedNetwork === "hardhat-local") {
    if (!/^http:\/\/(127\.0\.0\.1|localhost):\d+\/?$/.test(resolvedRpcUrl)) {
      throw new Error("the local network must record a loopback rpc_url with a port");
    }
  } else {
    if (!resolvedRpcUrl.startsWith("https://")) {
      throw new Error("a public network must record an HTTPS rpc_url");
    }
    if (/[?#@]/.test(resolvedRpcUrl)) {
      throw new Error("rpc_url must not carry credentials, a query or a fragment");
    }
    if (maxFeeWei === null) {
      throw new Error("maxFeeWei is required off the local chain");
    }
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

  // The lineage the registry recorded when it authorized this publisher. A later owner
  // who calls authorizePublisher on a replacement instead of rotatePublisher creates a
  // second, unrelated lineage; pinning it here lets the publisher refuse that case, which
  // authorization alone cannot distinguish.
  const identity = await registry.publisherIdentity(publisher);
  if (identity === ethers.ZeroAddress) {
    throw new Error("publisher lineage was not recorded by authorization");
  }

  const manifest = {
    manifest_version: 1,
    network: resolvedNetwork,
    chain_id: Number(chainId),
    rpc_url: resolvedRpcUrl,
    registry_address: address,
    registry_runtime_bytecode_sha256: runtimeSha256,
    publisher_address: publisher,
    publisher_identity_address: identity,
    deployer_address: deployerAddress,
    operations_address: operations,
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
  if (maxFeeWei !== null) {
    manifest.max_fee_wei = maxFeeWei;
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
    operationsAddress: required("TOUCHSTONE_OPERATIONS_ADDRESS"),
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

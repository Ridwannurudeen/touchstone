// Preflight or deploy TouchstoneRegistryV2 and emit a version-2 deployment manifest.
//
// The default path is read-only. Broadcasting requires an exact chain-id opt-in in
// TOUCHSTONE_DEPLOY_BROADCAST_CHAIN_ID, in addition to the public-network confirmation and
// spend ceiling used by the v1 deployment path.

const { createHash } = require("node:crypto");
const { writeFileSync } = require("node:fs");
const hre = require("hardhat");

const {
  CONFIRM_ENV,
  SPEND_CEILING_ENV,
  assertWithinCeiling,
  deploymentSpendCeiling,
  isLoopbackHost,
  journalBroadcasts,
  recordAttempt,
  reserveDestination,
  serializeManifest,
} = require("./deploy");

const LOCAL_CHAIN_ID = 31337n;
const BROADCAST_ENV = "TOUCHSTONE_DEPLOY_BROADCAST_CHAIN_ID";
const NETWORK_BY_CHAIN_ID = {
  31337: "hardhat-local",
  1952: "xlayer-testnet",
  196: "xlayer-mainnet",
};
const NETWORK_CHAIN_IDS = {
  "hardhat-local": 31337,
  "xlayer-testnet": 1952,
  "xlayer-mainnet": 196,
};
const REGISTRY_SOURCE = "contracts/TouchstoneRegistryV2.sol";
const REGISTRY_NAME = "TouchstoneRegistryV2";
const REGISTRY_FQN = `${REGISTRY_SOURCE}:${REGISTRY_NAME}`;

async function deployV2({
  legacyRegistryAddress,
  legacyRuntimeBytecodeSha256,
  ownerAddress,
  publisherAddress,
  relayerAddress,
  operationsAddress,
  reporterPublicKey,
  network = null,
  confirmations = 1,
  maxFeeWei = null,
  rpcUrl = null,
  broadcast = false,
}) {
  const { ethers } = hre;
  const chainId = (await ethers.provider.getNetwork()).chainId;
  const resolvedNetwork =
    network ?? NETWORK_BY_CHAIN_ID[String(chainId)] ?? null;
  if (!(resolvedNetwork in NETWORK_CHAIN_IDS)) {
    throw new Error(
      `network must be one of ${Object.keys(NETWORK_CHAIN_IDS).join(", ")}; ` +
        `chain ${chainId} resolved to ${resolvedNetwork}`
    );
  }
  if (BigInt(NETWORK_CHAIN_IDS[resolvedNetwork]) !== chainId) {
    throw new Error(
      `${resolvedNetwork} is chain ${NETWORK_CHAIN_IDS[resolvedNetwork]}, ` +
        `but this endpoint is chain ${chainId}`
    );
  }

  if (broadcast) {
    const confirmedBroadcast = process.env[BROADCAST_ENV];
    if (confirmedBroadcast !== String(chainId)) {
      throw new Error(
        `refusing to broadcast to chain ${chainId}: set ${BROADCAST_ENV}=${chainId} ` +
          "for this invocation"
      );
    }
    if (
      chainId !== LOCAL_CHAIN_ID &&
      process.env[CONFIRM_ENV] !== String(chainId)
    ) {
      throw new Error(
        `refusing to deploy to chain ${chainId}: set ${CONFIRM_ENV}=${chainId} to confirm`
      );
    }
  }

  const [deployer] = await ethers.getSigners();
  if (!deployer)
    throw new Error("the configured network has no deployment signer");
  const deployerAddress = await deployer.getAddress();
  const owner = requireAddress(ownerAddress, "ownerAddress");
  if (owner !== deployerAddress) {
    throw new Error(
      `ownerAddress ${owner} does not match the deployment signer ${deployerAddress}`
    );
  }
  const publisher = requireAddress(publisherAddress, "publisherAddress");
  const relayer = requireAddress(relayerAddress, "relayerAddress");
  const operations = requireAddress(operationsAddress, "operationsAddress");
  const legacyAddress = requireAddress(
    legacyRegistryAddress,
    "legacyRegistryAddress"
  );
  if (publisher === owner) {
    throw new Error("the publisher must not be the owner/deployer");
  }
  if (operations === owner || operations === publisher) {
    throw new Error(
      "the operations identity must be distinct from the owner and the publisher"
    );
  }
  if ([owner, publisher, operations].includes(relayer)) {
    throw new Error(
      "the relayer must be distinct from the owner, publisher and operations identity"
    );
  }
  if ([owner, publisher, relayer, operations].includes(legacyAddress)) {
    throw new Error("no role address may be the legacy registry itself");
  }
  if (!/^[0-9a-f]{64}$/.test(reporterPublicKey)) {
    throw new Error("reporterPublicKey must be 32 lowercase hexadecimal bytes");
  }
  if (!/^[0-9a-f]{64}$/.test(legacyRuntimeBytecodeSha256)) {
    throw new Error(
      "legacyRuntimeBytecodeSha256 must be a lowercase SHA-256 digest"
    );
  }

  confirmations = exactInteger(confirmations, "confirmations");
  if (maxFeeWei !== null) maxFeeWei = exactBigInt(maxFeeWei, "maxFeeWei");
  const resolvedRpcUrl =
    rpcUrl ?? hre.network.config.url ?? "http://127.0.0.1:8545";
  validateRpcUrl(resolvedRpcUrl, resolvedNetwork);
  if (resolvedNetwork !== "hardhat-local" && maxFeeWei === null) {
    throw new Error("maxFeeWei is required off the local chain");
  }

  const legacyCode = await ethers.provider.getCode(legacyAddress);
  if (legacyCode === "0x") {
    throw new Error(`no runtime bytecode at legacy registry ${legacyAddress}`);
  }
  const actualLegacyDigest = runtimeSha256(legacyCode);
  if (actualLegacyDigest !== legacyRuntimeBytecodeSha256) {
    throw new Error(
      `legacy registry runtime digest is ${actualLegacyDigest}, expected ` +
        legacyRuntimeBytecodeSha256
    );
  }
  const legacy = await ethers.getContractAt(
    "TouchstoneRegistry",
    legacyAddress
  );
  const legacyChainId = await legacy.expectedChainId();
  if (legacyChainId !== chainId) {
    throw new Error(
      `legacy registry expects chain ${legacyChainId}, endpoint is chain ${chainId}`
    );
  }
  const legacyOwner = await legacy.owner();
  if (legacyOwner !== owner) {
    throw new Error(
      `legacy registry owner ${legacyOwner} does not match requested v2 owner ${owner}`
    );
  }

  const factory = await ethers.getContractFactory(REGISTRY_NAME);
  const deploymentTransaction = await factory.getDeployTransaction(
    chainId,
    legacyAddress
  );
  const deploymentGasEstimate = await ethers.provider.estimateGas({
    ...deploymentTransaction,
    from: owner,
  });
  const deploymentGasLimit = (deploymentGasEstimate * 12n) / 10n;
  const authorizationGasLimit = 150000n;
  const totalGasLimit = deploymentGasLimit + authorizationGasLimit;
  const spendCeilingWei = broadcast
    ? deploymentSpendCeiling(resolvedNetwork === "hardhat-local")
    : null;
  const overrides = await spendOverrides(
    totalGasLimit,
    deploymentGasLimit,
    authorizationGasLimit,
    spendCeilingWei
  );
  if (spendCeilingWei !== null) {
    const balance = await ethers.provider.getBalance(owner);
    if (balance < spendCeilingWei) {
      throw new Error(
        `deployer ${owner} holds ${balance} wei, below the approved ceiling of ` +
          `${spendCeilingWei} wei`
      );
    }
  }

  const plan = {
    mode: broadcast ? "broadcast" : "dry-run",
    manifest_version: 2,
    registry_version: 2,
    contract: REGISTRY_FQN,
    network: resolvedNetwork,
    chain_id: Number(chainId),
    rpc_url: resolvedRpcUrl,
    owner_address: owner,
    publisher_address: publisher,
    relayer_address: relayer,
    operations_address: operations,
    legacy_registry_address: legacyAddress,
    legacy_registry_runtime_bytecode_sha256: actualLegacyDigest,
    constructor_arguments: {
      expected_chain_id: Number(chainId),
      legacy_registry_address: legacyAddress,
    },
    deployment_gas_limit: deploymentGasLimit,
    authorization_gas_limit: authorizationGasLimit,
    total_gas_limit: totalGasLimit,
  };
  if (!broadcast) {
    return { registry: null, manifest: null, destination: null, plan };
  }

  if (!process.env.TOUCHSTONE_MANIFEST_OUT) {
    throw new Error(
      "TOUCHSTONE_MANIFEST_OUT is required when broadcasting: the deployment and its " +
        "attempt journal must be reserved before any transaction"
    );
  }
  const destination = reserveDestination(process.env.TOUCHSTONE_MANIFEST_OUT);
  const journal = journalBroadcasts(ethers.provider, destination, {
    chain_id: Number(chainId),
    deployer: deployerAddress,
    owner,
    publisher,
    relayer,
    legacy_registry: legacyAddress,
  });

  recordAttempt(destination, {
    stage: "prepared",
    chain_id: Number(chainId),
    deployer: deployerAddress,
    owner,
    publisher,
    relayer,
    legacy_registry: legacyAddress,
    note:
      "Nothing had been broadcast when this was written. If no later stage was recorded, " +
      "check the deployer's nonce before assuming nothing was sent.",
  });

  let registry;
  try {
    registry = await ethers.deployContract(
      REGISTRY_NAME,
      [chainId, legacyAddress],
      overrides.deployment
    );
    recordAttempt(destination, {
      stage: "deploying",
      deployment_transaction: registry.deploymentTransaction().hash,
      chain_id: Number(chainId),
      deployer: deployerAddress,
      owner,
      publisher,
      relayer,
      legacy_registry: legacyAddress,
      note:
        "The deployment transaction was broadcast. Read its receipt before deciding " +
        "anything if no later stage exists; never retry automatically.",
    });
    await registry.waitForDeployment();
    const deploymentReceipt = await registry
      .deploymentTransaction()
      .wait(confirmations);
    const address = await registry.getAddress();
    recordAttempt(destination, {
      stage: "deployed",
      address,
      deployment_transaction: deploymentReceipt.hash,
      deployment_block: deploymentReceipt.blockNumber,
      chain_id: Number(chainId),
      deployer: deployerAddress,
      owner,
      publisher,
      relayer,
      legacy_registry: legacyAddress,
      note:
        "The v2 registry exists; publisher authorization was not complete when this was " +
        "written. Treat it as incomplete if no later stage exists.",
    });

    const authorization = await registry.authorizePublisher(
      publisher,
      overrides.authorization
    );
    recordAttempt(destination, {
      stage: "authorizing",
      address,
      deployment_transaction: deploymentReceipt.hash,
      deployment_block: deploymentReceipt.blockNumber,
      authorization_transaction: authorization.hash,
      chain_id: Number(chainId),
      deployer: deployerAddress,
      owner,
      publisher,
      relayer,
      legacy_registry: legacyAddress,
      note:
        "The authorization transaction was broadcast. Read its receipt if no later stage " +
        "exists; never retry automatically.",
    });
    const authorizationReceipt = await authorization.wait(confirmations);

    await verifyRegistryState(registry, {
      chainId,
      legacyAddress,
      owner,
      publisher,
    });
    const runtimeDigest = await verifiedRuntimeSha256(address);
    const legacyCodeAfter = await ethers.provider.getCode(legacyAddress);
    if (runtimeSha256(legacyCodeAfter) !== actualLegacyDigest) {
      throw new Error("legacy registry runtime changed during deployment");
    }
    assertWithinCeiling(
      [deploymentReceipt, authorizationReceipt],
      spendCeilingWei
    );
    recordAttempt(destination, {
      stage: "authorized",
      address,
      deployment_transaction: deploymentReceipt.hash,
      deployment_block: deploymentReceipt.blockNumber,
      authorization_transaction: authorizationReceipt.hash,
      chain_id: Number(chainId),
      deployer: deployerAddress,
      owner,
      publisher,
      relayer,
      legacy_registry: legacyAddress,
      note:
        "Deployment, runtime verification and authorization succeeded. Reconstruct the " +
        "manifest from this record rather than redeploying if its final write fails.",
    });

    const manifest = {
      manifest_version: 2,
      registry_version: 2,
      network: resolvedNetwork,
      chain_id: Number(chainId),
      rpc_url: resolvedRpcUrl,
      registry_address: address,
      registry_runtime_bytecode_sha256: runtimeDigest,
      legacy_registry_address: legacyAddress,
      legacy_registry_runtime_bytecode_sha256: actualLegacyDigest,
      owner_address: owner,
      publisher_address: publisher,
      relayer_address: relayer,
      publisher_identity_address: await registry.publisherIdentity(publisher),
      deployer_address: deployerAddress,
      operations_address: operations,
      confirmations,
      deployment_block: deploymentReceipt.blockNumber,
      deployment_state: "active",
      deployment_transaction: deploymentReceipt.hash.toLowerCase(),
      authorization_transaction: authorizationReceipt.hash.toLowerCase(),
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
    if (maxFeeWei !== null) manifest.max_fee_wei = maxFeeWei;
    return { registry, manifest, destination, plan };
  } finally {
    journal.release();
  }
}

async function verifyRegistryState(
  registry,
  { chainId, legacyAddress, owner, publisher }
) {
  if ((await registry.expectedChainId()) !== chainId) {
    throw new Error(
      "deployed v2 registry recorded the wrong expected chain id"
    );
  }
  if ((await registry.legacyRegistry()) !== legacyAddress) {
    throw new Error("deployed v2 registry recorded the wrong legacy registry");
  }
  if ((await registry.owner()) !== owner) {
    throw new Error("deployed v2 registry recorded the wrong owner");
  }
  if (!(await registry.isPublisherAuthorized(publisher))) {
    throw new Error("publisher authorization did not take effect");
  }
  if ((await registry.publisherIdentity(publisher)) !== publisher) {
    throw new Error("publisher lineage was not recorded exactly");
  }
}

async function verifiedRuntimeSha256(address) {
  const actual = await hre.ethers.provider.getCode(address);
  if (actual === "0x") throw new Error(`no runtime bytecode at ${address}`);
  const buildInfo = await hre.artifacts.getBuildInfo(REGISTRY_FQN);
  if (!buildInfo) throw new Error(`no build info for ${REGISTRY_FQN}`);
  const compiled =
    buildInfo.output.contracts[REGISTRY_SOURCE][REGISTRY_NAME].evm
      .deployedBytecode;
  const references = [
    ...Object.values(compiled.immutableReferences ?? {}).flat(),
    ...Object.values(compiled.linkReferences ?? {}).flatMap((source) =>
      Object.values(source).flat()
    ),
  ];
  const normalizedActual = normalizeRuntimeBytecode(actual, references);
  const normalizedCompiled = normalizeRuntimeBytecode(
    compiled.object,
    references
  );
  if (normalizedActual !== normalizedCompiled) {
    throw new Error(
      `runtime bytecode at ${address} does not match the compiled ${REGISTRY_FQN}`
    );
  }
  return runtimeSha256(actual);
}

function normalizeRuntimeBytecode(bytecode, references) {
  let normalized = bytecode.startsWith("0x") ? bytecode.slice(2) : bytecode;
  if (!/^[0-9a-fA-F]*$/.test(normalized) || normalized.length % 2 !== 0) {
    throw new Error("runtime bytecode must be complete hexadecimal bytes");
  }
  for (const { start, length } of references) {
    const before = normalized.slice(0, start * 2);
    const after = normalized.slice((start + length) * 2);
    if (before.length + length * 2 + after.length !== normalized.length) {
      throw new Error(
        "compiled runtime contains an invalid immutable reference"
      );
    }
    normalized = `${before}${"0".repeat(length * 2)}${after}`;
  }
  return normalized.toLowerCase();
}

async function spendOverrides(
  totalGas,
  deploymentGas,
  authorizationGas,
  ceilingWei
) {
  if (ceilingWei === null) return { deployment: {}, authorization: {} };
  const maxFeePerGas = ceilingWei / totalGas;
  if (maxFeePerGas === 0n) {
    throw new Error(
      `approved ceiling ${ceilingWei} wei cannot cover ${totalGas} gas at 1 wei per gas`
    );
  }
  const fees = await hre.ethers.provider.getFeeData();
  const networkFee = fees.maxFeePerGas ?? fees.gasPrice;
  if (networkFee === null)
    throw new Error("the provider returned no usable fee data");
  if (networkFee > maxFeePerGas) {
    throw new Error(
      `the network fee ${networkFee} exceeds the ${maxFeePerGas} per-gas cap allowed by ` +
        `${ceilingWei} wei over ${totalGas} gas`
    );
  }
  if (fees.maxFeePerGas === null) {
    return {
      deployment: { gasLimit: deploymentGas, gasPrice: maxFeePerGas },
      authorization: { gasLimit: authorizationGas, gasPrice: maxFeePerGas },
    };
  }
  const priority =
    fees.maxPriorityFeePerGas !== null &&
    fees.maxPriorityFeePerGas < maxFeePerGas
      ? fees.maxPriorityFeePerGas
      : maxFeePerGas;
  return {
    deployment: {
      gasLimit: deploymentGas,
      maxFeePerGas,
      maxPriorityFeePerGas: priority,
    },
    authorization: {
      gasLimit: authorizationGas,
      maxFeePerGas,
      maxPriorityFeePerGas: priority,
    },
  };
}

function validateRpcUrl(value, network) {
  if (network === "hardhat-local") {
    const match = /^http:\/\/(?:127\.0\.0\.1|localhost):(\d{1,5})\/?$/.exec(
      value
    );
    const port = match ? Number(match[1]) : 0;
    if (port < 1 || port > 65535) {
      throw new Error(
        "the local network must record a loopback rpc_url with a valid port"
      );
    }
    return;
  }
  let parsed;
  try {
    parsed = new URL(value);
  } catch {
    throw new Error("rpc_url is not a URL");
  }
  if (parsed.protocol !== "https:" || !parsed.hostname) {
    throw new Error(
      "a public network must record an HTTPS rpc_url with a host"
    );
  }
  if (parsed.username || parsed.password || parsed.search || parsed.hash) {
    throw new Error(
      "rpc_url must not carry credentials, a query or a fragment"
    );
  }
  if (isLoopbackHost(parsed.hostname)) {
    throw new Error("a public network cannot be served from loopback");
  }
}

function exactBigInt(value, field) {
  if (typeof value === "bigint") {
    if (value < 1n) throw new Error(`${field} must be positive`);
    return value;
  }
  if (typeof value === "number") {
    if (!Number.isSafeInteger(value)) {
      throw new Error(
        `${field} exceeds what a JavaScript number holds exactly; pass it as a string`
      );
    }
    if (value < 1) throw new Error(`${field} must be positive`);
    return BigInt(value);
  }
  if (typeof value === "string" && /^[1-9][0-9]*$/.test(value)) {
    return BigInt(value);
  }
  throw new Error(`${field} must be a positive exact integer`);
}

function exactInteger(value, field) {
  const exact = exactBigInt(value, field);
  if (exact > BigInt(Number.MAX_SAFE_INTEGER)) {
    throw new Error(`${field} is implausibly large`);
  }
  return Number(exact);
}

function requireAddress(value, field) {
  let address;
  try {
    address = hre.ethers.getAddress(value);
  } catch {
    throw new Error(`${field} must be a 20-byte hexadecimal address`);
  }
  if (address === hre.ethers.ZeroAddress) {
    throw new Error(`${field} must not be the zero address`);
  }
  return address;
}

function runtimeSha256(code) {
  return createHash("sha256")
    .update(Buffer.from(code.slice(2), "hex"))
    .digest("hex");
}

async function main() {
  const required = (name) => {
    const value = process.env[name];
    if (!value) throw new Error(`${name} is required`);
    return value;
  };
  const broadcast = process.env[BROADCAST_ENV] !== undefined;
  const result = await deployV2({
    legacyRegistryAddress: required("TOUCHSTONE_LEGACY_REGISTRY_ADDRESS"),
    legacyRuntimeBytecodeSha256: required(
      "TOUCHSTONE_LEGACY_RUNTIME_BYTECODE_SHA256"
    ),
    ownerAddress: required("TOUCHSTONE_OWNER_ADDRESS"),
    publisherAddress: required("TOUCHSTONE_PUBLISHER_ADDRESS"),
    relayerAddress: required("TOUCHSTONE_RELAYER_ADDRESS"),
    operationsAddress: required("TOUCHSTONE_OPERATIONS_ADDRESS"),
    reporterPublicKey: required("TOUCHSTONE_REPORTER_PUBLIC_KEY"),
    network: process.env.TOUCHSTONE_NETWORK ?? null,
    confirmations: process.env.TOUCHSTONE_CONFIRMATIONS ?? 1,
    maxFeeWei: process.env.TOUCHSTONE_MAX_FEE_WEI ?? null,
    rpcUrl: process.env.TOUCHSTONE_RPC_URL ?? null,
    broadcast,
  });
  if (!broadcast) {
    process.stdout.write(`${serializeManifest(result.plan)}\n`);
    process.stderr.write(
      `dry run only; set ${BROADCAST_ENV}=${result.plan.chain_id} for an explicit send\n`
    );
    return;
  }
  const serialized = `${serializeManifest(result.manifest)}\n`;
  process.stdout.write(serialized);
  writeFileSync(result.destination, serialized, { encoding: "utf-8" });
  process.stderr.write(`manifest written to ${result.destination}\n`);
}

module.exports = {
  BROADCAST_ENV,
  deployV2,
  main,
  normalizeRuntimeBytecode,
  verifiedRuntimeSha256,
};

if (require.main === module) {
  main().catch((error) => {
    process.exitCode = 1;
    console.error(error.message);
  });
}

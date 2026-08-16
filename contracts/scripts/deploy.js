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
const { mkdirSync, writeFileSync } = require("node:fs");
const { dirname, isAbsolute, join } = require("node:path");
const hre = require("hardhat");

const LOCAL_CHAIN_ID = 31337n;
const CONFIRM_ENV = "TOUCHSTONE_DEPLOY_CONFIRM_CHAIN_ID";
const NETWORK_BY_CHAIN_ID = {
  31337: "hardhat-local",
};
// Must match touchstone/deployment.py NETWORK_CHAIN_IDS exactly. A name that is not
// bound to one chain id is a name that proves nothing: X Layer's deprecated testnet on
// chain 195 would otherwise pass as "xlayer-testnet".
const NETWORK_CHAIN_IDS = {
  "hardhat-local": 31337,
  "xlayer-testnet": 1952,
  "xlayer-mainnet": 196,
};
const NETWORKS = Object.keys(NETWORK_CHAIN_IDS);

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
  const publisher = requireAddress(publisherAddress, "publisherAddress");
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
  const operations = requireAddress(operationsAddress, "operationsAddress");
  if (operations === publisher || operations === deployerAddress) {
    throw new Error(
      "the operations identity must be distinct from the deployer and the publisher",
    );
  }
  if (!/^[0-9a-f]{64}$/.test(reporterPublicKey)) {
    throw new Error("reporterPublicKey must be 32 lowercase hexadecimal bytes");
  }
  const resolvedNetwork =
    network ?? NETWORK_BY_CHAIN_ID[String(chainId)] ?? null;
  if (!NETWORKS.includes(resolvedNetwork)) {
    throw new Error(
      `network must be one of ${NETWORKS.join(", ")}; chain ${chainId} resolved to ${resolvedNetwork}`,
    );
  }
  if (BigInt(NETWORK_CHAIN_IDS[resolvedNetwork]) !== chainId) {
    throw new Error(
      `${resolvedNetwork} is chain ${NETWORK_CHAIN_IDS[resolvedNetwork]}, but this endpoint is chain ${chainId}`,
    );
  }
  // isInteger is true for 9007199254740992, the value 9007199254740993 silently becomes.
  // Every operator-supplied integer goes through the same exactness rule now, because the
  // rule was only ever applied to the fee ceiling and the defect simply moved here.
  confirmations = exactInteger(confirmations, "confirmations");
  if (maxFeeWei !== null) {
    // A wei ceiling can exceed Number.MAX_SAFE_INTEGER. Checking exactness after
    // accepting a `number` was useless: JavaScript has already rounded the literal by
    // then, so the comparison is between two copies of the same wrong value. An unsafe
    // number is refused outright — pass a string or a BigInt for large ceilings.
    maxFeeWei = exactBigInt(maxFeeWei, "maxFeeWei");
  }
  const resolvedRpcUrl =
    rpcUrl ?? hre.network.config.url ?? "http://127.0.0.1:8545";
  if (resolvedNetwork === "hardhat-local") {
    const loopback = /^http:\/\/(?:127\.0\.0\.1|localhost):(\d{1,5})\/?$/.exec(
      resolvedRpcUrl,
    );
    const port = loopback ? Number(loopback[1]) : 0;
    if (port < 1 || port > 65535) {
      throw new Error(
        "the local network must record a loopback rpc_url with a valid port",
      );
    }
  } else {
    // Parsed rather than pattern-matched: "https:///no-host" starts with https:// and
    // contains none of the forbidden characters, yet names no host at all.
    let parsed;
    try {
      parsed = new URL(resolvedRpcUrl);
    } catch {
      throw new Error("rpc_url is not a URL");
    }
    if (parsed.protocol !== "https:" || !parsed.hostname) {
      throw new Error(
        "a public network must record an HTTPS rpc_url with a host",
      );
    }
    if (parsed.username || parsed.password || parsed.search || parsed.hash) {
      throw new Error(
        "rpc_url must not carry credentials, a query or a fragment",
      );
    }
    if (isLoopbackHost(parsed.hostname)) {
      throw new Error("a public network cannot be served from loopback");
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
    // Stated, never left to a default. A manifest that omits this reads as "active" in the
    // Python loader, which is the right default for a fresh deployment but the wrong thing
    // to rely on: the one manifest that must NOT read as active is an obsolete one, and
    // that is exactly the case where nobody remembers to add a field by hand.
    deployment_state: "active",
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
    // Kept as BigInt through serialisation. Converting back to Number reintroduced
    // exactly the rounding the BigInt check was added to prevent: a ceiling of
    // 9007199254740993 wei was recorded as ...992.
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
    confirmations: process.env.TOUCHSTONE_CONFIRMATIONS ?? 1,
    maxFeeWei: process.env.TOUCHSTONE_MAX_FEE_WEI ?? null,
    rpcUrl: process.env.TOUCHSTONE_RPC_URL ?? null,
  });
  const serialized = `${serializeManifest(manifest)}\n`;
  // Printed *before* it is written. The manifest is the only record of a deployment that
  // cannot be repeated, and the first real run proved why: hardhat executes from
  // `contracts/`, so a relative destination resolved against that directory instead of
  // the repository root, the write threw ENOENT, and the registry was already live on
  // chain with its manifest lost. Emitting first means the operator always holds the
  // record even when the write fails, and a relative path is now resolved against the
  // repository root rather than the working directory that happens to be in effect.
  process.stdout.write(serialized);
  const destination = process.env.TOUCHSTONE_MANIFEST_OUT;
  if (destination) {
    const target = isAbsolute(destination)
      ? destination
      : join(__dirname, "..", "..", destination);
    mkdirSync(dirname(target), { recursive: true });
    writeFileSync(target, serialized, { encoding: "utf-8" });
    process.stderr.write(`manifest written to ${target}\n`);
  }
}

function exactBigInt(value, field) {
  // Accepts only what carries its value exactly: a bigint, a canonical decimal string, or
  // a primitive number that is a safe integer. A boxed `new Number(...)` is refused
  // because typeof reports "object" and it would slip past a primitive check.
  if (typeof value === "bigint") {
    if (value < 1n) throw new Error(`${field} must be positive`);
    return value;
  }
  if (typeof value === "number") {
    if (!Number.isSafeInteger(value)) {
      throw new Error(
        `${field} exceeds what a JavaScript number holds exactly; pass it as a string`,
      );
    }
    if (value < 1) throw new Error(`${field} must be positive`);
    return BigInt(value);
  }
  if (typeof value === "string" && /^[1-9][0-9]*$/.test(value)) {
    return BigInt(value);
  }
  throw new Error(
    `${field} must be a positive exact integer: a bigint, a decimal string, or a safe number`,
  );
}

function exactInteger(value, field) {
  const exact = exactBigInt(value, field);
  if (exact > BigInt(Number.MAX_SAFE_INTEGER)) {
    throw new Error(`${field} is implausibly large`);
  }
  return Number(exact);
}

function requireAddress(value, field) {
  // getAddress happily returns the zero address. Python refuses it later, which on a
  // public chain means discovering it only after the registry has been deployed.
  // hre.ethers, not a bare `ethers`: the bare name only resolved because Hardhat injects
  // it as a global in its own test runner, so calling deploy() from plain Node threw.
  const address = hre.ethers.getAddress(value);
  if (address === hre.ethers.ZeroAddress) {
    throw new Error(`${field} must not be the zero address`);
  }
  return address;
}

function isLoopbackHost(hostname) {
  // Three literal strings missed every alias: the rest of 127.0.0.0/8, the bracketed
  // IPv6 form, and a fully-qualified "localhost." with its trailing root dot.
  const name = String(hostname).trim().toLowerCase().replace(/\.$/, "");
  if (name === "localhost" || name.endsWith(".localhost")) return true;
  let bare = name.replace(/^\[|\]$/g, "");
  if (bare === "::1" || bare === "0:0:0:0:0:0:0:1") return true;
  // URL normalises [::ffff:127.0.0.1] to [::ffff:7f00:1], which no dotted-quad test
  // matches. Unwrap the mapped IPv4 back to its dotted form before testing.
  const mapped = /^::ffff:([0-9a-f]{1,4}):([0-9a-f]{1,4})$/.exec(bare);
  if (mapped) {
    const high = parseInt(mapped[1], 16);
    const low = parseInt(mapped[2], 16);
    bare = [high >> 8, high & 0xff, low >> 8, low & 0xff].join(".");
  } else if (bare.startsWith("::ffff:")) {
    bare = bare.slice("::ffff:".length);
  }
  return /^127\.\d{1,3}\.\d{1,3}\.\d{1,3}$/.test(bare);
}

function serializeManifest(manifest) {
  // JSON.stringify throws on a BigInt, and Number would round it. Marking the value and
  // unquoting it afterwards writes the exact integer the operator chose.
  const marked = JSON.stringify(
    manifest,
    (_key, value) => (typeof value === "bigint" ? `@bigint:${value}@` : value),
    2,
  );
  return marked.replace(/"@bigint:(\d+)@"/g, "$1");
}

module.exports = { deploy, CONFIRM_ENV, serializeManifest, isLoopbackHost };

if (require.main === module) {
  main().catch((error) => {
    process.exitCode = 1;
    console.error(error.message);
  });
}

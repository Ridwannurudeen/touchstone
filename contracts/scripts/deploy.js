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
const {
  closeSync,
  existsSync,
  fsyncSync,
  mkdirSync,
  openSync,
  readFileSync,
  writeFileSync,
  writeSync,
} = require("node:fs");
const { dirname, isAbsolute, join } = require("node:path");
const hre = require("hardhat");

const LOCAL_CHAIN_ID = 31337n;
const CONFIRM_ENV = "TOUCHSTONE_DEPLOY_CONFIRM_CHAIN_ID";
const SPEND_CEILING_ENV = "TOUCHSTONE_DEPLOY_MAX_SPEND_WEI";
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
        `refusing to deploy to chain ${chainId}: set ${CONFIRM_ENV}=${chainId} to confirm`
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
      "the publisher must not be the deployer; the identity that owns the registry must not run unattended"
    );
  }
  if (!operationsAddress) {
    throw new Error(
      "operationsAddress is required; an unstated role address cannot be shown to be separate"
    );
  }
  const operations = requireAddress(operationsAddress, "operationsAddress");
  if (operations === publisher || operations === deployerAddress) {
    throw new Error(
      "the operations identity must be distinct from the deployer and the publisher"
    );
  }
  if (!/^[0-9a-f]{64}$/.test(reporterPublicKey)) {
    throw new Error("reporterPublicKey must be 32 lowercase hexadecimal bytes");
  }
  const resolvedNetwork =
    network ?? NETWORK_BY_CHAIN_ID[String(chainId)] ?? null;
  if (!NETWORKS.includes(resolvedNetwork)) {
    throw new Error(
      `network must be one of ${NETWORKS.join(
        ", "
      )}; chain ${chainId} resolved to ${resolvedNetwork}`
    );
  }
  if (BigInt(NETWORK_CHAIN_IDS[resolvedNetwork]) !== chainId) {
    throw new Error(
      `${resolvedNetwork} is chain ${NETWORK_CHAIN_IDS[resolvedNetwork]}, but this endpoint is chain ${chainId}`
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
      resolvedRpcUrl
    );
    const port = loopback ? Number(loopback[1]) : 0;
    if (port < 1 || port > 65535) {
      throw new Error(
        "the local network must record a loopback rpc_url with a valid port"
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
    if (maxFeeWei === null) {
      throw new Error("maxFeeWei is required off the local chain");
    }
  }

  // The destination is claimed BEFORE anything irreversible happens. Resolving it only
  // after both transactions meant a repeated command silently destroyed the previous
  // manifest, and an unwritable destination was discovered with a registry already live on
  // chain. Both are one mistake: the record must be secured before the thing it records
  // exists.
  if (
    resolvedNetwork !== "hardhat-local" &&
    !process.env.TOUCHSTONE_MANIFEST_OUT
  ) {
    // Optional off the local chain meant a public deployment could run with no manifest
    // reserved and no breadcrumb — the exact state that lost the first registry's record.
    throw new Error(
      "TOUCHSTONE_MANIFEST_OUT is required off the local chain: a deployment with nowhere " +
        "to record itself is one that can be lost"
    );
  }
  const destination = reserveDestination(process.env.TOUCHSTONE_MANIFEST_OUT);

  // An owner-approved ceiling on what this command may irreversibly spend, separate from
  // the per-publication `max_fee_wei` the manifest carries. The script recorded a
  // publication ceiling and enforced no bound at all on the deployment itself.
  const spendCeilingWei = deploymentSpendCeiling(
    resolvedNetwork === "hardhat-local"
  );
  // Explicit caps on both sends, and the *worst case* — every unit of gas at the maximum
  // fee — proved to sit under the approved ceiling before anything is broadcast. Checking
  // receipts afterwards is monitoring, not enforcement: it cannot un-send a transaction,
  // and an owner who approved a number has not approved "that number, probably".
  const overrides = await spendOverrides(
    ethers,
    ["TouchstoneRegistry", [chainId]],
    publisher,
    spendCeilingWei
  );
  await assertAffordable(deployerAddress, spendCeilingWei);

  // Journals every transaction hash the node returns, at the moment it returns it. The
  // previous placement recorded after `deployContract()` resolved — but Hardhat's signer
  // receives the hash and *then* polls for the full transaction, so a provider that failed
  // during that poll left a broadcast transaction with its hash written nowhere. This is
  // the RPC boundary itself: nothing can be sent without passing through it.
  const journal = journalBroadcasts(ethers.provider, destination, {
    chain_id: Number(chainId),
    deployer: deployerAddress,
    publisher,
  });

  // Written before anything is sent, so a crash during the broadcast still leaves a file
  // saying an attempt was starting and against which chain.
  recordAttempt(destination, {
    stage: "prepared",
    chain_id: Number(chainId),
    deployer: deployerAddress,
    publisher,
    note:
      "Nothing had been broadcast when this was written. If no later stage was recorded, " +
      "check the deployer's nonce before assuming nothing was sent.",
  });

  const registry = await ethers.deployContract(
    "TouchstoneRegistry",
    [chainId],
    overrides.deployment
  );
  // The hash exists the moment the transaction is broadcast, and is recorded before the
  // wait. Recording only after `waitForDeployment()` meant losing the provider during that
  // wait left a mined deployment with no hash written anywhere — reproduced, and the
  // registry was live with nothing on disk naming it.
  recordAttempt(destination, {
    stage: "deploying",
    deployment_transaction: registry.deploymentTransaction().hash,
    chain_id: Number(chainId),
    deployer: deployerAddress,
    publisher,
    note:
      "The deployment transaction was broadcast. Its outcome was unknown when this was " +
      "written: read the receipt from the chain before deciding anything, and never " +
      "re-run this command.",
  });
  await registry.waitForDeployment();
  const deploymentReceipt = await registry.deploymentTransaction().wait();
  journal.settled();
  const address = await registry.getAddress();

  recordAttempt(destination, {
    stage: "deployed",
    address,
    deployment_transaction: deploymentReceipt.hash,
    deployment_block: deploymentReceipt.blockNumber,
    chain_id: Number(chainId),
    deployer: deployerAddress,
    publisher,
    note:
      "The registry exists on chain; authorization had not completed when this was " +
      "written. If nothing further was recorded, treat this deployment as incomplete: " +
      "mark it superseded rather than reusing it, and never retry automatically.",
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
    publisher,
    note:
      "The authorization transaction was broadcast. If nothing further was recorded its " +
      "outcome is unknown: read it from the chain before deciding anything, and never " +
      "re-run this command.",
  });
  const authorizationReceipt = await authorization.wait();
  if (!(await registry.isPublisherAuthorized(publisher))) {
    throw new Error("publisher authorization did not take effect");
  }
  // Belt and braces. The caps above are the enforcement; this reports what was actually
  // spent, and would catch a provider that ignored them.
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
    publisher,
    note:
      "Deployment and authorization both succeeded. If no manifest sits beside this file, " +
      "the manifest write failed: reconstruct it from these values rather than redeploying.",
  });

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
    // Required by both the schema and the Python loader; a manifest omitting it is refused
    // rather than defaulted, so this is not optional politeness. The one manifest that must
    // never read as publishable is an obsolete one, which is exactly where a field goes
    // missing when it is allowed to.
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
  // Released where it was taken. Putting this in `main()` reached for a name `deploy()`
  // owns — the same scope error as before, caught immediately by the end-to-end test added
  // for exactly that.
  journal.release();
  return { registry, manifest, destination };
}

async function main() {
  const required = (name) => {
    const value = process.env[name];
    if (!value) {
      throw new Error(`${name} is required`);
    }
    return value;
  };
  const { manifest, destination } = await deploy({
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
  if (destination) {
    writeFileSync(destination, serialized, { encoding: "utf-8" });
    process.stderr.write(`manifest written to ${destination}\n`);
  }
}

function journalBroadcasts(provider, destination, context) {
  // Wraps the provider's own `send`, so a transaction hash is durably recorded the instant
  // the node returns it — before any polling, receipt wait, or library bookkeeping. Both
  // `eth_sendTransaction` (node-managed keys) and `eth_sendRawTransaction` (locally signed)
  // return the hash as their result, so one interception covers every path a transaction
  // can take out of this process.
  if (!destination) return { settled() {}, release() {} };
  const original = provider.send.bind(provider);
  const broadcast = [];
  provider.send = async (method, params) => {
    const result = await original(method, params);
    if (
      /^eth_send(Raw)?Transaction$/.test(method) &&
      typeof result === "string" &&
      /^0x[0-9a-f]{64}$/i.test(result)
    ) {
      broadcast.push(result);
      recordAttempt(destination, {
        stage: "broadcast",
        broadcast_transactions: [...broadcast],
        ...context,
        note:
          "One or more transactions were broadcast and their outcome was unknown when " +
          "this was written. Read each receipt from the chain before deciding anything, " +
          "and never re-run this command.",
      });
    }
    return result;
  };
  return {
    settled() {
      /* later stages overwrite with richer context */
    },
    release() {
      provider.send = original;
    },
  };
}

function reserveDestination(destination) {
  // Both files are claimed before the first irreversible send: the manifest and its
  // companion attempt record. Reserving only the manifest left the attempt path to be
  // created with `wx` *after* deployment, so a stale companion from an earlier run raised
  // EEXIST with a registry already live on chain — a predictable collision discovered at
  // the one moment it cannot be acted on.
  if (!destination) return null;
  const target = isAbsolute(destination)
    ? destination
    : join(__dirname, "..", "..", destination);
  const companion = `${target}.attempt.json`;
  for (const path of [target, companion]) {
    if (existsSync(path)) {
      throw new Error(
        `${path} already exists; refusing to overwrite a deployment record. ` +
          "Choose a new destination, or move the existing files aside deliberately."
      );
    }
  }
  mkdirSync(dirname(target), { recursive: true });
  // Proves the directory is writable now rather than after a registry is live on chain.
  writeFileSync(target, "", { encoding: "utf-8", flag: "wx" });
  writeFileSync(companion, "", { encoding: "utf-8", flag: "wx" });
  return target;
}

function recordAttempt(destination, record) {
  // A durable breadcrumb, rewritten as the attempt advances: `deployed`, then
  // `authorizing` once the second transaction is broadcast, then `authorized`. Without the
  // middle stage a failure while waiting for authorization leaves a record claiming
  // authorization had not started, omitting the very transaction hash an operator needs to
  // read the outcome off the chain.
  //
  // The first write is exclusive so it cannot land on top of an unrelated attempt; later
  // stages deliberately overwrite the file they themselves created.
  if (!destination) return;
  const path = `${destination}.attempt.json`;
  const stamped = { ...record, recorded_at: new Date().toISOString() };
  // Appended and flushed, never rewritten. `writeFileSync` truncates first and does not
  // fsync, so a write fault or a killed process while advancing a stage could destroy the
  // previous valid record — losing evidence in exactly the situation the record exists for.
  // One JSON object per line: the last complete line is the furthest the attempt reached,
  // and every earlier line survives it.
  const handle = openSync(path, "a");
  try {
    writeSync(
      handle,
      `${JSON.stringify(stamped)}
`
    );
    fsyncSync(handle);
  } finally {
    closeSync(handle);
  }
  process.stderr.write(`attempt recorded (${record.stage}) at ${path}
`);
}

function readAttempt(destination) {
  // Every stage the attempt reached, oldest first. A partial final line is discarded: an
  // interrupted append is not a record, and treating it as one would report a stage that
  // never completed.
  const path = `${destination}.attempt.json`;
  return readFileSync(path, "utf-8")
    .split(/\r?\n/)
    .filter((line) => line.trim())
    .map((line) => {
      try {
        return JSON.parse(line);
      } catch {
        return null;
      }
    })
    .filter(Boolean);
}

async function spendOverrides(
  ethers,
  [contractName, args],
  publisher,
  ceilingWei
) {
  // Explicit gas limits and a fee cap, with the combined worst case proved under the
  // approved ceiling BEFORE anything is sent. Without this the "ceiling" bounded nothing:
  // the sends used provider-selected parameters and the receipts were compared afterwards,
  // which reports an overrun it cannot prevent.
  if (ceilingWei === null) return { deployment: {}, authorization: {} };

  const factory = await ethers.getContractFactory(contractName);
  const deployData = (await factory.getDeployTransaction(...args)).data;
  const deployGas = await ethers.provider.estimateGas({ data: deployData });
  // A registry that has not been deployed cannot be asked to estimate its own
  // authorization, so the second transaction uses a fixed, generous allowance. It is a
  // single storage write and an event; 150,000 is far above what that costs, and the
  // worst case below is what the ceiling is actually checked against.
  const authorizeGas = 150000n;
  // 20% headroom on the estimate, because a deployment that runs out of gas still burns it.
  const deploymentGasLimit = (deployGas * 12n) / 10n;
  const totalGas = deploymentGasLimit + authorizeGas;
  const maxFeePerGas = ceilingWei / totalGas;
  if (maxFeePerGas === 0n) {
    throw new Error(
      `approved ceiling ${ceilingWei} wei cannot cover ${totalGas} gas at even 1 wei per ` +
        "gas; raise the ceiling or do not deploy"
    );
  }
  const fees = await ethers.provider.getFeeData();
  if (fees.maxFeePerGas !== null && fees.maxFeePerGas > maxFeePerGas) {
    throw new Error(
      `the network's current maxFeePerGas ${fees.maxFeePerGas} exceeds the ${maxFeePerGas} ` +
        `per gas the approved ceiling of ${ceilingWei} wei allows over ${totalGas} gas; ` +
        "the deployment would either fail or exceed what was approved"
    );
  }
  const priority =
    fees.maxPriorityFeePerGas !== null &&
    fees.maxPriorityFeePerGas < maxFeePerGas
      ? fees.maxPriorityFeePerGas
      : maxFeePerGas;
  process.stderr.write(
    `worst case ${totalGas} gas at ${maxFeePerGas} wei = ${
      totalGas * maxFeePerGas
    } wei, ` +
      `within the approved ${ceilingWei} wei
`
  );
  return {
    deployment: {
      gasLimit: deploymentGasLimit,
      maxFeePerGas,
      maxPriorityFeePerGas: priority,
    },
    authorization: {
      gasLimit: authorizeGas,
      maxFeePerGas,
      maxPriorityFeePerGas: priority,
    },
  };
}

function deploymentSpendCeiling(local) {
  // Separate from the manifest's per-publication `max_fee_wei`. This bounds what this one
  // command may irreversibly spend, and off the local chain it must be stated rather than
  // assumed — an unbounded ceiling is not something an owner can approve.
  const raw = process.env[SPEND_CEILING_ENV];
  if (raw === undefined || raw === "") {
    if (local) return null;
    throw new Error(
      `${SPEND_CEILING_ENV} is required off the local chain: the owner approves a maximum ` +
        "total spend for the deployment and authorization transactions"
    );
  }
  return exactBigInt(raw, SPEND_CEILING_ENV);
}

async function assertAffordable(deployerAddress, ceilingWei) {
  if (ceilingWei === null) return;
  const balance = await hre.ethers.provider.getBalance(deployerAddress);
  if (balance < ceilingWei) {
    throw new Error(
      `deployer ${deployerAddress} holds ${balance} wei, below the approved ceiling of ` +
        `${ceilingWei} wei; a run that cannot cover its own ceiling can strand a ` +
        "half-finished deployment"
    );
  }
}

function assertWithinCeiling(receipts, ceilingWei) {
  if (ceilingWei === null) return;
  const spent = receipts.reduce(
    (total, receipt) => total + receipt.gasUsed * receipt.gasPrice,
    0n
  );
  if (spent > ceilingWei) {
    // After the fact, because a receipt is the only honest measure of what was spent. It
    // cannot un-send the transactions; it makes the overrun loud instead of silent, and
    // the operator's abort criteria take over from there.
    throw new Error(
      `deployment spent ${spent} wei, above the approved ceiling of ${ceilingWei} wei`
    );
  }
  process.stderr.write(`deployment spent ${spent} wei of ${ceilingWei} allowed
`);
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
        `${field} exceeds what a JavaScript number holds exactly; pass it as a string`
      );
    }
    if (value < 1) throw new Error(`${field} must be positive`);
    return BigInt(value);
  }
  if (typeof value === "string" && /^[1-9][0-9]*$/.test(value)) {
    return BigInt(value);
  }
  throw new Error(
    `${field} must be a positive exact integer: a bigint, a decimal string, or a safe number`
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
    2
  );
  return marked.replace(/"@bigint:(\d+)@"/g, "$1");
}

module.exports = {
  deploy,
  main,
  CONFIRM_ENV,
  SPEND_CEILING_ENV,
  serializeManifest,
  isLoopbackHost,
  reserveDestination,
  recordAttempt,
  readAttempt,
  deploymentSpendCeiling,
  assertWithinCeiling,
};

if (require.main === module) {
  main().catch((error) => {
    process.exitCode = 1;
    console.error(error.message);
  });
}

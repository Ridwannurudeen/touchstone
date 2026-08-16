const { createHash } = require("node:crypto");
const {
  HardhatEthersProvider,
} = require("@nomicfoundation/hardhat-ethers/internal/hardhat-ethers-provider");
const { expect } = require("chai");
const { ethers } = require("hardhat");

const {
  deploy,
  CONFIRM_ENV,
  serializeManifest,
  isLoopbackHost,
} = require("../scripts/deploy");

// 32 bytes standing in for a reporting public key. It is not derived from a private key
// and never signs anything; only its digest matters to these assertions.
const REPORTER_PUBLIC_KEY = "aa".repeat(32);

describe("deploy script", function () {
  async function roles() {
    const [deployer, publisher, operations] = await ethers.getSigners();
    return { deployer, publisher, operations };
  }

  it("emits a manifest describing the deployment it just made", async function () {
    const { deployer, publisher, operations } = await roles();

    const { registry, manifest } = await deploy({
      publisherAddress: publisher.address,
      operationsAddress: operations.address,
      reporterPublicKey: REPORTER_PUBLIC_KEY,
    });

    const address = await registry.getAddress();
    const chainId = (await ethers.provider.getNetwork()).chainId;
    expect(manifest.manifest_version).to.equal(1);
    expect(manifest.network).to.equal("hardhat-local");
    expect(manifest.chain_id).to.equal(Number(chainId));
    expect(manifest.registry_address).to.equal(address);
    expect(manifest.publisher_address).to.equal(publisher.address);
    expect(manifest.deployer_address).to.equal(deployer.address);
    expect(manifest.operations_address).to.equal(operations.address);
    expect(manifest.confirmations).to.equal(1);
  });

  it("digests the runtime bytecode the chain actually holds", async function () {
    const { publisher, operations } = await roles();

    const { registry, manifest } = await deploy({
      publisherAddress: publisher.address,
      operationsAddress: operations.address,
      reporterPublicKey: REPORTER_PUBLIC_KEY,
    });

    // The digest must come from the chain, not from the build artifact: the artifact is
    // what was compiled, and the point of the field is to prove what was deployed.
    const code = await ethers.provider.getCode(await registry.getAddress());
    const expected = createHash("sha256")
      .update(Buffer.from(code.slice(2), "hex"))
      .digest("hex");
    expect(manifest.registry_runtime_bytecode_sha256).to.equal(expected);
  });

  it("records the deployment block so reconciliation never scans from genesis", async function () {
    const { publisher, operations } = await roles();

    const { registry, manifest } = await deploy({
      publisherAddress: publisher.address,
      operationsAddress: operations.address,
      reporterPublicKey: REPORTER_PUBLIC_KEY,
    });

    const receipt = await registry.deploymentTransaction().wait();
    expect(manifest.deployment_block).to.equal(receipt.blockNumber);
    expect(manifest.deployment_block).to.be.greaterThan(0);
  });

  it("authorizes exactly the publisher it was given", async function () {
    const { deployer, publisher, operations } = await roles();

    const { registry } = await deploy({
      publisherAddress: publisher.address,
      operationsAddress: operations.address,
      reporterPublicKey: REPORTER_PUBLIC_KEY,
    });

    expect(await registry.isPublisherAuthorized(publisher.address)).to.equal(
      true,
    );
    expect(await registry.isPublisherAuthorized(deployer.address)).to.equal(
      false,
    );
    expect(await registry.isPublisherAuthorized(operations.address)).to.equal(
      false,
    );
    expect(await registry.owner()).to.equal(deployer.address);
  });

  it("derives the reporting key id from the published public key", async function () {
    const { publisher, operations } = await roles();

    const { manifest } = await deploy({
      publisherAddress: publisher.address,
      operationsAddress: operations.address,
      reporterPublicKey: REPORTER_PUBLIC_KEY,
    });

    const digest = createHash("sha256")
      .update(Buffer.from(REPORTER_PUBLIC_KEY, "hex"))
      .digest("hex");
    expect(manifest.reporting_keys).to.deep.equal([
      {
        kid: `ed25519:${digest}`,
        public_key: REPORTER_PUBLIC_KEY,
        state: "active",
      },
    ]);
  });

  it("refuses to deploy with the publisher as the deployer", async function () {
    const { deployer, operations } = await roles();

    await expect(
      deploy({
        publisherAddress: deployer.address,
        operationsAddress: operations.address,
        reporterPublicKey: REPORTER_PUBLIC_KEY,
      }),
    ).to.be.rejectedWith("must not be the deployer");
  });

  it("refuses to deploy with operations doubling as another role", async function () {
    const { deployer, publisher } = await roles();

    await expect(
      deploy({
        publisherAddress: publisher.address,
        operationsAddress: publisher.address,
        reporterPublicKey: REPORTER_PUBLIC_KEY,
      }),
    ).to.be.rejectedWith("must be distinct");
    await expect(
      deploy({
        publisherAddress: publisher.address,
        operationsAddress: deployer.address,
        reporterPublicKey: REPORTER_PUBLIC_KEY,
      }),
    ).to.be.rejectedWith("must be distinct");
  });

  it("refuses a reporting key that is not 32 hexadecimal bytes", async function () {
    const { publisher, operations } = await roles();

    for (const bad of ["", "aa", "AA".repeat(32), `0x${"aa".repeat(32)}`]) {
      await expect(
        deploy({
          publisherAddress: publisher.address,
          operationsAddress: operations.address,
          reporterPublicKey: bad,
        }),
      ).to.be.rejectedWith("32 lowercase hexadecimal bytes");
    }
  });

  it("pins the publisher lineage the registry recorded", async function () {
    // Authorization says an owner call let this address publish. Lineage says it is the
    // same publishing identity the manifest was written for, which authorization alone
    // cannot distinguish from an unrelated second authorization.
    const { publisher, operations } = await roles();

    const { registry, manifest } = await deploy({
      publisherAddress: publisher.address,
      operationsAddress: operations.address,
      reporterPublicKey: REPORTER_PUBLIC_KEY,
    });

    expect(manifest.publisher_identity_address).to.equal(
      await registry.publisherIdentity(publisher.address),
    );
    expect(manifest.publisher_identity_address).to.not.equal(ethers.ZeroAddress);
  });

  it("validates every operator-controlled field before it sends anything", async function () {
    // A field checked after deployment cannot be fixed: on a public chain an invalid
    // manifest discovered afterwards leaves a deployed, authorized registry that nothing
    // can publish to, and neither send can be undone.
    const { publisher, operations } = await roles();
    const before = await ethers.provider.getBlockNumber();

    for (const invalid of [
      { network: "not-a-supported-network" },
      { confirmations: 0 },
      { confirmations: 1.5 },
      { maxFeeWei: 0 },
    ]) {
      await expect(
        deploy({
          publisherAddress: publisher.address,
          operationsAddress: operations.address,
          reporterPublicKey: REPORTER_PUBLIC_KEY,
          ...invalid,
        }),
      ).to.be.rejected;
    }

    expect(await ethers.provider.getBlockNumber()).to.equal(
      before,
      "no transaction may be sent before the inputs are known to be valid",
    );
  });

  it("requires an operations identity rather than treating it as optional", async function () {
    const { publisher } = await roles();

    await expect(
      deploy({
        publisherAddress: publisher.address,
        reporterPublicKey: REPORTER_PUBLIC_KEY,
      }),
    ).to.be.rejectedWith("operationsAddress is required");
  });

  it("records a fee ceiling exactly rather than rounding it", function () {
    // Number silently rounds above 2^53-1, so a ceiling of ...993 was recorded as ...992.
    const out = serializeManifest({ max_fee_wei: 9007199254740993n });
    expect(out).to.contain("9007199254740993");
    // Asserted on the text, not on JSON.parse: JavaScript cannot hold this value as a
    // number at all, which is the whole reason it must never pass through Number here.
    // Python reads the same text with arbitrary precision and gets the exact integer.
    expect(out).to.not.contain("9007199254740992");
  });

  it("refuses a public rpc_url that names no host", async function () {
    // "https:///no-host" starts with https:// and contains no forbidden character, so a
    // pattern check passed it straight through to deployContract.
    const { publisher, operations } = await roles();

    await expect(
      deploy({
        publisherAddress: publisher.address,
        operationsAddress: operations.address,
        reporterPublicKey: REPORTER_PUBLIC_KEY,
        network: "hardhat-local",
        rpcUrl: "https:///no-host",
      }),
    ).to.be.rejectedWith("loopback rpc_url");
  });

  it("refuses a zero role address before deploying anything", async function () {
    // getAddress returns the zero address without complaint, so this reached the chain
    // and only Python objected — after an irreversible deployment.
    const { publisher, operations } = await roles();
    const before = await ethers.provider.getBlockNumber();

    for (const invalid of [
      { publisherAddress: ethers.ZeroAddress, operationsAddress: operations.address },
      { publisherAddress: publisher.address, operationsAddress: ethers.ZeroAddress },
    ]) {
      await expect(
        deploy({ reporterPublicKey: REPORTER_PUBLIC_KEY, ...invalid }),
      ).to.be.rejectedWith("must not be the zero address");
    }

    expect(await ethers.provider.getBlockNumber()).to.equal(before);
  });

  it("refuses a fee ceiling JavaScript has already rounded", async function () {
    const { publisher, operations } = await roles();
    const before = await ethers.provider.getBlockNumber();

    await expect(
      deploy({
        publisherAddress: publisher.address,
        operationsAddress: operations.address,
        reporterPublicKey: REPORTER_PUBLIC_KEY,
        maxFeeWei: 9007199254740993,
      }),
    ).to.be.rejectedWith("JavaScript number");

    expect(await ethers.provider.getBlockNumber()).to.equal(before);
  });

  it("accepts a large fee ceiling given as a string, exactly", async function () {
    const { publisher, operations } = await roles();

    const { manifest } = await deploy({
      publisherAddress: publisher.address,
      operationsAddress: operations.address,
      reporterPublicKey: REPORTER_PUBLIC_KEY,
      maxFeeWei: "9007199254740993",
    });

    expect(serializeManifest(manifest)).to.contain("9007199254740993");
  });

  it("treats every loopback spelling as loopback", function () {
    for (const host of [
      "127.0.0.1",
      "127.0.0.2",
      "127.255.255.254",
      "localhost",
      "LOCALHOST",
      "localhost.",
      "::1",
      "[::1]",
      "api.localhost",
      // URL normalises [::ffff:127.0.0.1] to this, which no dotted-quad test matches.
      "[::ffff:7f00:1]",
      "::ffff:127.0.0.1",
    ]) {
      expect(isLoopbackHost(host), host).to.equal(true);
    }
    for (const host of ["rpc.xlayer.tech", "127.example.com", "1270.0.0.1"]) {
      expect(isLoopbackHost(host), host).to.equal(false);
    }
  });

  it("refuses a confirmation depth JavaScript has already rounded", async function () {
    // The exactness rule was applied only to the fee ceiling, so the same defect simply
    // moved to the next operator-supplied integer.
    const { publisher, operations } = await roles();
    const before = await ethers.provider.getBlockNumber();

    await expect(
      deploy({
        publisherAddress: publisher.address,
        operationsAddress: operations.address,
        reporterPublicKey: REPORTER_PUBLIC_KEY,
        confirmations: 9007199254740993,
      }),
    ).to.be.rejectedWith("JavaScript number");

    expect(await ethers.provider.getBlockNumber()).to.equal(before);
  });

  it("refuses a boxed Number that would slip past a primitive check", async function () {
    const { publisher, operations } = await roles();

    await expect(
      deploy({
        publisherAddress: publisher.address,
        operationsAddress: operations.address,
        reporterPublicKey: REPORTER_PUBLIC_KEY,
        // eslint-disable-next-line no-new-wrappers
        maxFeeWei: new Number(9007199254740993),
      }),
    ).to.be.rejectedWith("must be a positive exact integer");
  });

  it("does not depend on a globally injected ethers", async function () {
    // Every test runs inside Hardhat, which injects `ethers` as a global — so a bare
    // reference resolves here and the defect only appears when deploy() is called from
    // plain Node. Removing the global for the duration reproduces that condition.
    const { publisher, operations } = await roles();
    const injected = globalThis.ethers;
    delete globalThis.ethers;
    try {
      const { manifest } = await deploy({
        publisherAddress: publisher.address,
        operationsAddress: operations.address,
        reporterPublicKey: REPORTER_PUBLIC_KEY,
      });
      expect(manifest.publisher_address).to.equal(publisher.address);
    } finally {
      if (injected !== undefined) {
        globalThis.ethers = injected;
      }
    }
  });

  it("names the confirmation variable a stale export cannot satisfy", function () {
    // The guard is a positive confirmation of the exact chain id rather than a boolean,
    // so an old export left in a shell cannot enable a deployment to a different chain.
    expect(CONFIRM_ENV).to.equal("TOUCHSTONE_DEPLOY_CONFIRM_CHAIN_ID");
  });
});

describe("deployment safeguards", function () {
  const { existsSync, mkdtempSync, readFileSync, writeFileSync } = require("node:fs");
  const { join } = require("node:path");
  const { tmpdir } = require("node:os");
  const {
    SPEND_CEILING_ENV,
    reserveDestination,
    recordAttempt,
    readAttempt,
    journalBroadcasts,
    deploymentSpendCeiling,
    assertWithinCeiling,
  } = require("../scripts/deploy");

  function scratch() {
    return mkdtempSync(join(tmpdir(), "touchstone-deploy-"));
  }

  async function roles() {
    const [deployer, publisher, operations] = await ethers.getSigners();
    return { deployer, publisher, operations };
  }

  it("refuses to overwrite an existing deployment record", async function () {
    // The manifest is the only record of a deployment that cannot be repeated. The
    // destination used to be resolved after both transactions and written unconditionally,
    // so running the command twice destroyed the previous registry's only record.
    const directory = scratch();
    const destination = join(directory, "manifest.json");
    writeFileSync(destination, '{"existing":true}', "utf-8");

    expect(() => reserveDestination(destination)).to.throw(
      /already exists; refusing to overwrite/,
    );
    expect(JSON.parse(readFileSync(destination, "utf-8")).existing).to.equal(true);
  });

  it("claims the destination before anything irreversible happens", async function () {
    // Reserving proves the directory is writable now, rather than discovering it is not
    // with a registry already live on chain — which is exactly what happened on
    // 2026-08-15.
    const destination = join(scratch(), "nested", "manifest.json");

    const reserved = reserveDestination(destination);

    expect(reserved).to.equal(destination);
    expect(existsSync(destination)).to.equal(true);
  });

  it("records the registry the moment it exists, before authorization can fail", async function () {
    const { deployer, publisher, operations } = await roles();
    const destination = join(scratch(), "manifest.json");
    process.env.TOUCHSTONE_MANIFEST_OUT = destination;
    try {
      await deploy({
        publisherAddress: await publisher.getAddress(),
        operationsAddress: await operations.getAddress(),
        reporterPublicKey: REPORTER_PUBLIC_KEY,
      });
    } finally {
      delete process.env.TOUCHSTONE_MANIFEST_OUT;
    }

    const records = readAttempt(destination);
    const attempt = records[records.length - 1];
    // The final stage on a successful run. It carries both transaction hashes, so a
    // manifest write that fails afterwards leaves enough to reconstruct rather than
    // redeploy.
    expect(attempt.stage).to.equal("authorized");
    expect(attempt.address).to.match(/^0x[0-9a-fA-F]{40}$/);
    expect(attempt.deployment_transaction).to.match(/^0x[0-9a-f]{64}$/);
    expect(attempt.authorization_transaction).to.match(/^0x[0-9a-f]{64}$/);
    expect(attempt.deployer).to.equal(await deployer.getAddress());
    expect(attempt.recorded_at).to.match(/^\d{4}-\d{2}-\d{2}T/);
    expect(attempt.note).to.match(/reconstruct it from these values rather than redeploying/);
  });

  it("journals every broadcast hash, and every stage survives the next one", async function () {
    // Two things at once, because they are the same guarantee. The hash is journaled at the
    // RPC boundary — the node returns it there, before Hardhat polls for the full
    // transaction, and a provider failing during that poll used to leave a broadcast
    // transaction recorded nowhere. And the record is appended rather than rewritten, so
    // advancing a stage cannot destroy the one before it.
    //
    // The previous version of this test collected the stages it saw and then asserted only
    // on the final record. Removing the `deploying` write would have left it green.
    const { publisher, operations } = await roles();
    const destination = join(scratch(), "manifest.json");
    const previous = { ...process.env };
    process.env.TOUCHSTONE_MANIFEST_OUT = destination;
    try {
      await deploy({
        publisherAddress: await publisher.getAddress(),
        operationsAddress: await operations.getAddress(),
        reporterPublicKey: REPORTER_PUBLIC_KEY,
      });
    } finally {
      process.env = previous;
    }

    const records = readAttempt(destination);
    const stages = records.map((record) => record.stage);

    // The exact order, not membership. `include` assertions were satisfied by a reordered
    // sequence, so they proved the stages existed and nothing about when.
    expect(stages).to.deep.equal([
      "prepared",
      "broadcast",
      "deploying",
      "deployed",
      "broadcast",
      "authorizing",
      "authorized",
    ]);

    // The RPC-boundary records carry hashes the node returned.
    const broadcasts = records.filter((record) => record.stage === "broadcast");
    expect(broadcasts.length).to.equal(2, "one per transaction");
    expect(broadcasts[0].broadcast_transactions).to.have.lengthOf(1);
    expect(broadcasts[1].broadcast_transactions).to.have.lengthOf(2);
    for (const hash of broadcasts[1].broadcast_transactions) {
      expect(hash).to.match(/^0x[0-9a-f]{64}$/i);
    }

    const final = records[records.length - 1];
    expect(final.deployment_transaction).to.match(/^0x[0-9a-f]{64}$/);
    expect(final.authorization_transaction).to.match(/^0x[0-9a-f]{64}$/);
  });

  it("has the hash on disk the instant the node returns it", function () {
    // The timing the whole mechanism exists for. The happy-path test cannot distinguish
    // boundary journaling from a later write. Hardhat does poll through its underlying
    // provider after the outer `eth_sendTransaction`; watching only the outer provider hid
    // those calls. This test exercises the outer boundary directly and asserts that the
    // journal already holds the hash before anything the caller might do next.
    const destination = join(scratch(), "manifest.json");
    reserveDestination(destination);
    const hash = `0x${"ab".repeat(32)}`;
    let onDiskWhenSendReturned = null;

    const fake = {
      async send(method) {
        return method === "eth_sendTransaction" ? hash : "0x";
      },
    };
    const journal = journalBroadcasts(fake, destination, { chain_id: 31337 });

    return (async () => {
      await fake.send("eth_sendTransaction", []);
      // Read immediately, as the very next statement after the send resolved.
      onDiskWhenSendReturned = readAttempt(destination);
      journal.release();

      const broadcasts = onDiskWhenSendReturned.filter(
        (record) => record.stage === "broadcast",
      );
      expect(
        broadcasts.length,
        "the hash must already be durable when send returns",
      ).to.equal(1);
      expect(broadcasts[0].broadcast_transactions).to.deep.equal([hash]);
      // And a call that is not a send writes nothing.
      const before = readAttempt(destination).length;
      const restored = journalBroadcasts(fake, destination, { chain_id: 31337 });
      await fake.send("eth_blockNumber", []);
      restored.release();
      expect(readAttempt(destination).length).to.equal(before);
    })();
  });

  it("keeps a raw broadcast hash when the underlying provider fails after send", async function () {
    // Locally signed transactions bypass the outer `send`: `broadcastTransaction()` calls
    // `_hardhatProvider.send` directly, then performs provider bookkeeping. The old fake had
    // no underlying layer, so deleting that layer from `journalBroadcasts` left it green.
    const destination = join(scratch(), "manifest.json");
    reserveDestination(destination);
    const hash = `0x${"cd".repeat(32)}`;
    const calls = [];
    const underlying = {
      async send(method) {
        calls.push(method);
        if (method === "eth_sendRawTransaction") return hash;
        if (method === "eth_blockNumber") {
          throw new Error(
            "provider failed after returning the transaction hash",
          );
        }
        throw new Error(`unexpected method ${method}`);
      },
    };
    const provider = new HardhatEthersProvider(underlying, "hardhat");
    const journal = journalBroadcasts(provider, destination, {
      chain_id: 31337,
    });

    try {
      await expect(provider.broadcastTransaction("0x00")).to.be.rejectedWith(
        "provider failed after returning the transaction hash",
      );
    } finally {
      journal.release();
    }

    expect(calls).to.deep.equal(["eth_sendRawTransaction", "eth_blockNumber"]);
    const broadcasts = readAttempt(destination).filter(
      (record) => record.stage === "broadcast",
    );
    expect(broadcasts).to.have.lengthOf(1);
    expect(broadcasts[0].broadcast_transactions).to.deep.equal([hash]);
  });

  it("advances the breadcrumb through every stage, keeping the second hash", function () {
    // The middle stage is the one that matters. A failure while waiting for authorization
    // used to leave a record saying authorization had not started — omitting the very
    // transaction hash an operator needs to read the outcome off the chain.
    const destination = join(scratch(), "manifest.json");

    recordAttempt(destination, { stage: "deployed", deployment_transaction: "0xaa" });
    let attempt = readAttempt(destination).at(-1);
    expect(attempt.stage).to.equal("deployed");

    recordAttempt(destination, {
      stage: "authorizing",
      deployment_transaction: "0xaa",
      authorization_transaction: "0xbb",
    });
    attempt = readAttempt(destination).at(-1);
    expect(attempt.stage).to.equal("authorizing");
    expect(attempt.authorization_transaction).to.equal("0xbb");

    recordAttempt(destination, {
      stage: "authorized",
      deployment_transaction: "0xaa",
      authorization_transaction: "0xbb",
    });
    attempt = readAttempt(destination).at(-1);
    expect(attempt.stage).to.equal("authorized");
  });

  it("refuses when either reserved file already exists", function () {
    // Both are claimed together, before anything is broadcast. Reserving only the manifest
    // left the companion attempt path to be created after deployment, so a stale companion
    // from an earlier run raised EEXIST with a registry already live on chain — the one
    // moment a predictable collision cannot be acted on.
    const directory = scratch();

    const manifestOnly = join(directory, "a.json");
    writeFileSync(`${manifestOnly}.attempt.json`, "{}", "utf-8");
    expect(() => reserveDestination(manifestOnly)).to.throw(/already exists/);

    const companionOnly = join(directory, "b.json");
    writeFileSync(companionOnly, "{}", "utf-8");
    expect(() => reserveDestination(companionOnly)).to.throw(/already exists/);
  });

  it("claims the companion attempt file before anything is broadcast", function () {
    const destination = join(scratch(), "manifest.json");

    reserveDestination(destination);

    expect(existsSync(destination)).to.equal(true);
    expect(existsSync(`${destination}.attempt.json`)).to.equal(true);
  });

  it("requires an owner-approved spend ceiling off the local chain", function () {
    // The manifest's max_fee_wei bounds a publication. Nothing bounded the deployment
    // itself, so an approval to deploy was an approval to spend an unstated amount.
    // Exercised directly: a public deployment cannot be simulated on the local chain,
    // because the chain-id check correctly refuses the mismatch first.
    delete process.env[SPEND_CEILING_ENV];

    expect(() => deploymentSpendCeiling(false)).to.throw(
      new RegExp(SPEND_CEILING_ENV),
    );
    // The local chain may go without one; nothing irreversible is at stake there.
    expect(deploymentSpendCeiling(true)).to.equal(null);

    process.env[SPEND_CEILING_ENV] = "2000000000000000";
    try {
      expect(deploymentSpendCeiling(false)).to.equal(2000000000000000n);
    } finally {
      delete process.env[SPEND_CEILING_ENV];
    }
  });

  it("reports a deployment that spent more than was approved", function () {
    // After the fact, because a receipt is the only honest measure of what was spent. It
    // cannot un-send the transactions; it makes the overrun loud rather than silent.
    const receipts = [
      { gasUsed: 1000n, gasPrice: 3n },
      { gasUsed: 500n, gasPrice: 3n },
    ];

    expect(() => assertWithinCeiling(receipts, 4000n)).to.throw(
      /spent 4500 wei, above the approved ceiling of 4000 wei/,
    );
    expect(() => assertWithinCeiling(receipts, 5000n)).to.not.throw();
    expect(() => assertWithinCeiling(receipts, null)).to.not.throw();
  });
});

describe("the real entry point", function () {
  const { existsSync, mkdtempSync, readFileSync } = require("node:fs");
  const { join } = require("node:path");
  const { tmpdir } = require("node:os");
  const { main, readAttempt } = require("../scripts/deploy");

  it("completes end to end through main, not just deploy", async function () {
    // The scope bug this exists for: `destination` was owned by `deploy()` and read by
    // `main()`, so the real command deployed, authorized, printed the manifest and then
    // exited 1 with "destination is not defined". Sixty-nine tests passed while it was
    // broken, because every one of them called `deploy()` or a helper directly. A path
    // nobody drives is a path nobody tests.
    const [, publisher, operations] = await ethers.getSigners();
    const directory = mkdtempSync(join(tmpdir(), "touchstone-main-"));
    const destination = join(directory, "manifest.json");

    const previous = { ...process.env };
    Object.assign(process.env, {
      TOUCHSTONE_PUBLISHER_ADDRESS: await publisher.getAddress(),
      TOUCHSTONE_OPERATIONS_ADDRESS: await operations.getAddress(),
      TOUCHSTONE_REPORTER_PUBLIC_KEY: "aa".repeat(32),
      TOUCHSTONE_MANIFEST_OUT: destination,
    });
    try {
      await main();
    } finally {
      process.env = previous;
    }

    expect(existsSync(destination)).to.equal(true);
    const manifest = JSON.parse(readFileSync(destination, "utf-8"));
    expect(manifest.deployment_state).to.equal("active");
    expect(manifest.registry_address).to.match(/^0x[0-9a-fA-F]{40}$/);
    expect(manifest.deployment_block).to.be.greaterThan(0);

    const records = readAttempt(destination);
    const attempt = records[records.length - 1];
    expect(attempt.stage).to.equal("authorized");
    expect(attempt.address).to.equal(manifest.registry_address);
  });
});

const { createHash } = require("node:crypto");
const { expect } = require("chai");
const { ethers } = require("hardhat");

const {
  deploy,
  CONFIRM_ENV,
  serializeManifest,
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
        network: "xlayer-mainnet",
        rpcUrl: "https:///no-host",
        maxFeeWei: 1,
      }),
    ).to.be.rejected;
  });

  it("names the confirmation variable a stale export cannot satisfy", function () {
    // The guard is a positive confirmation of the exact chain id rather than a boolean,
    // so an old export left in a shell cannot enable a deployment to a different chain.
    expect(CONFIRM_ENV).to.equal("TOUCHSTONE_DEPLOY_CONFIRM_CHAIN_ID");
  });
});

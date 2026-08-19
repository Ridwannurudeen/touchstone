const { createHash } = require("node:crypto");
const { existsSync, mkdtempSync, readFileSync } = require("node:fs");
const { tmpdir } = require("node:os");
const { join } = require("node:path");
const { expect } = require("chai");
const { ethers } = require("hardhat");

const {
  BROADCAST_ENV,
  deployV2,
  normalizeRuntimeBytecode,
} = require("../scripts/deploy_v2");
const { readAttempt } = require("../scripts/deploy");

const REPORTER_PUBLIC_KEY = "aa".repeat(32);

describe("RegistryV2 deployment", function () {
  async function fixture(expectedChainId = 31337n) {
    const [owner, publisher, relayer, operations, outsider] =
      await ethers.getSigners();
    const legacy = await ethers.deployContract("TouchstoneRegistry", [
      expectedChainId,
    ]);
    await legacy.waitForDeployment();
    const legacyAddress = await legacy.getAddress();
    const legacyCode = await ethers.provider.getCode(legacyAddress);
    const legacyRuntimeBytecodeSha256 = createHash("sha256")
      .update(Buffer.from(legacyCode.slice(2), "hex"))
      .digest("hex");
    return {
      owner,
      publisher,
      relayer,
      operations,
      outsider,
      legacy,
      legacyAddress,
      legacyRuntimeBytecodeSha256,
    };
  }

  function options(context, overrides = {}) {
    return {
      legacyRegistryAddress: context.legacyAddress,
      legacyRuntimeBytecodeSha256: context.legacyRuntimeBytecodeSha256,
      ownerAddress: context.owner.address,
      publisherAddress: context.publisher.address,
      relayerAddress: context.relayer.address,
      operationsAddress: context.operations.address,
      reporterPublicKey: REPORTER_PUBLIC_KEY,
      ...overrides,
    };
  }

  afterEach(function () {
    delete process.env[BROADCAST_ENV];
    delete process.env.TOUCHSTONE_MANIFEST_OUT;
  });

  it("is a read-only dry run by default", async function () {
    const context = await fixture();
    const before = await ethers.provider.getBlockNumber();

    const result = await deployV2(options(context));

    expect(await ethers.provider.getBlockNumber()).to.equal(before);
    expect(result.registry).to.equal(null);
    expect(result.manifest).to.equal(null);
    expect(result.plan.mode).to.equal("dry-run");
    expect(result.plan.chain_id).to.equal(31337);
    expect(result.plan.legacy_registry_address).to.equal(context.legacyAddress);
    expect(result.plan.relayer_address).to.equal(context.relayer.address);
  });

  it("refuses broadcast without the exact-chain opt-in", async function () {
    const context = await fixture();
    const before = await ethers.provider.getBlockNumber();

    await expect(
      deployV2(options(context, { broadcast: true }))
    ).to.be.rejectedWith(BROADCAST_ENV);

    expect(await ethers.provider.getBlockNumber()).to.equal(before);
  });

  it("checks the legacy chain, owner, publisher and runtime before v2 deployment", async function () {
    const wrongChain = await fixture(1n);
    await expect(deployV2(options(wrongChain))).to.be.rejectedWith(
      "legacy registry expects chain 1"
    );

    const context = await fixture();
    await expect(
      deployV2(options(context, { ownerAddress: context.outsider.address }))
    ).to.be.rejectedWith("does not match the deployment signer");
    await expect(
      deployV2(options(context, { publisherAddress: context.owner.address }))
    ).to.be.rejectedWith("publisher must not be the owner/deployer");
    await expect(
      deployV2(options(context, { relayerAddress: context.operations.address }))
    ).to.be.rejectedWith("relayer must be distinct");
    await expect(
      deployV2(
        options(context, {
          legacyRuntimeBytecodeSha256: "00".repeat(32),
        })
      )
    ).to.be.rejectedWith("legacy registry runtime digest");
  });

  it("deploys only after explicit local opt-in and records a strict v2 manifest", async function () {
    const context = await fixture();
    const destination = join(
      mkdtempSync(join(tmpdir(), "touchstone-v2-")),
      "v2.json"
    );
    process.env[BROADCAST_ENV] = "31337";
    process.env.TOUCHSTONE_MANIFEST_OUT = destination;

    const { registry, manifest } = await deployV2(
      options(context, { broadcast: true })
    );

    expect(existsSync(destination)).to.equal(true);
    expect(manifest.manifest_version).to.equal(2);
    expect(manifest.registry_version).to.equal(2);
    expect(manifest.registry_address).to.equal(await registry.getAddress());
    expect(manifest.legacy_registry_address).to.equal(context.legacyAddress);
    expect(manifest.owner_address).to.equal(context.owner.address);
    expect(manifest.publisher_address).to.equal(context.publisher.address);
    expect(manifest.relayer_address).to.equal(context.relayer.address);
    expect(manifest.publisher_identity_address).to.equal(
      context.publisher.address
    );
    expect(manifest.deployment_transaction).to.match(/^0x[0-9a-f]{64}$/);
    expect(manifest.authorization_transaction).to.match(/^0x[0-9a-f]{64}$/);
    const code = await ethers.provider.getCode(manifest.registry_address);
    const digest = createHash("sha256")
      .update(Buffer.from(code.slice(2), "hex"))
      .digest("hex");
    expect(manifest.registry_runtime_bytecode_sha256).to.equal(digest);

    const stages = readAttempt(destination).map((entry) => entry.stage);
    expect(stages).to.deep.equal([
      "prepared",
      "broadcast",
      "deploying",
      "deployed",
      "broadcast",
      "authorizing",
      "authorized",
    ]);
    expect(readFileSync(destination, "utf-8")).to.equal("");
  });

  it("normalizes only compiler-declared runtime offsets", function () {
    expect(
      normalizeRuntimeBytecode("0x600160026003", [{ start: 1, length: 2 }])
    ).to.equal("600000026003");
    expect(() => normalizeRuntimeBytecode("0x600", [])).to.throw(
      "complete hexadecimal bytes"
    );
    expect(() =>
      normalizeRuntimeBytecode("0x6001", [{ start: 2, length: 1 }])
    ).to.throw("invalid immutable reference");
  });
});

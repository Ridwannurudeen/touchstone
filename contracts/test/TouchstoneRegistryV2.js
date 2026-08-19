const { expect } = require("chai");
const { ethers } = require("hardhat");
const { loadFixture, time } = require("@nomicfoundation/hardhat-network-helpers");

const ASSET_KEY = ethers.keccak256(ethers.toUtf8Bytes("v2:asset"));
const REPORT_DIGEST = ethers.keccak256(ethers.toUtf8Bytes("v2:report:1"));
const POLICY_ID = ethers.keccak256(ethers.toUtf8Bytes("ustb-policy-v2"));
const POLICY_ROOT = ethers.keccak256(ethers.toUtf8Bytes("ustb-policy-root-v2"));
const CONTROL_ROOT = ethers.keccak256(ethers.toUtf8Bytes("control-set-v2"));
const EVIDENCE_ROOT = ethers.keccak256(ethers.toUtf8Bytes("evidence-v2"));
const APPROVAL_DIGEST = ethers.keccak256(ethers.toUtf8Bytes("approval-v2"));
const REPORT_URI = "ipfs://bafybeiv2report";
const TYPES = {
  Attestation: [
    { name: "assetKey", type: "bytes32" },
    { name: "reportDigest", type: "bytes32" },
    { name: "policyId", type: "bytes32" },
    { name: "policyRoot", type: "bytes32" },
    { name: "controlSetRoot", type: "bytes32" },
    { name: "evidenceRoot", type: "bytes32" },
    { name: "approvalDigest", type: "bytes32" },
    { name: "epochKey", type: "bytes32" },
    { name: "status", type: "uint8" },
    { name: "observedAt", type: "uint64" },
    { name: "validUntil", type: "uint64" },
    { name: "publisher", type: "address" },
    { name: "sequence", type: "uint64" },
    { name: "parentDigest", type: "bytes32" },
    { name: "correctionOf", type: "uint64" },
    { name: "reportURI", type: "string" },
  ],
};

describe("TouchstoneRegistryV2", function () {
  async function deployFixture() {
    const [owner, publisher, outsider] = await ethers.getSigners();
    const chainId = (await ethers.provider.getNetwork()).chainId;
    const legacy = await ethers.deployContract("TouchstoneRegistry", [chainId]);
    await legacy.waitForDeployment();
    await legacy.authorizePublisher(publisher.address);
    const registry = await ethers.deployContract("TouchstoneRegistryV2", [
      chainId,
      legacy.target,
    ]);
    await registry.waitForDeployment();
    await registry.authorizePublisher(publisher.address);
    return { owner, publisher, outsider, chainId, legacy, registry };
  }

  async function report(overrides = {}) {
    const now = BigInt(await time.latest());
    return {
      assetKey: ASSET_KEY,
      reportDigest: REPORT_DIGEST,
      policyId: POLICY_ID,
      policyRoot: POLICY_ROOT,
      controlSetRoot: CONTROL_ROOT,
      evidenceRoot: EVIDENCE_ROOT,
      approvalDigest: APPROVAL_DIGEST,
      epochKey: ethers.keccak256(ethers.toUtf8Bytes("v2:epoch:1")),
      status: 0,
      observedAt: now,
      validUntil: now + 3_600n,
      publisher: ethers.ZeroAddress,
      sequence: 1,
      parentDigest: ethers.ZeroHash,
      reportURI: REPORT_URI,
      ...overrides,
    };
  }

  async function signature(registry, publisher, value, correctionOf = 0) {
    const network = await ethers.provider.getNetwork();
    return publisher.signTypedData(
      {
        name: "Touchstone Registry",
        version: "2",
        chainId: network.chainId,
        verifyingContract: registry.target,
      },
      TYPES,
      {
        assetKey: value.assetKey,
        reportDigest: value.reportDigest,
        policyId: value.policyId,
        policyRoot: value.policyRoot,
        controlSetRoot: value.controlSetRoot,
        evidenceRoot: value.evidenceRoot,
        approvalDigest: value.approvalDigest,
        epochKey: value.epochKey,
        status: value.status,
        observedAt: value.observedAt,
        validUntil: value.validUntil,
        publisher: publisher.address,
        sequence: value.sequence,
        parentDigest: value.parentDigest,
        correctionOf,
        reportURI: value.reportURI,
      }
    );
  }

  function publish(registry, value, attestationSignature, publisher) {
    return registry.connect(publisher).publish(
      [
        value.assetKey,
        value.reportDigest,
        value.policyId,
        value.policyRoot,
        value.controlSetRoot,
        value.evidenceRoot,
        value.approvalDigest,
        value.epochKey,
        value.status,
        value.observedAt,
        value.validUntil,
        value.publisher,
        value.sequence,
        value.parentDigest,
        value.reportURI,
      ],
      attestationSignature
    );
  }

  it("stores an attested report and exposes the immutable v1 read path", async function () {
    const { publisher, legacy, registry } = await loadFixture(deployFixture);
    const value = await report({ publisher: publisher.address });
    const attestationSignature = await signature(registry, publisher, value);

    await expect(publish(registry, value, attestationSignature, publisher))
      .to.emit(registry, "Published")
      .withArgs(
        value.assetKey,
        value.sequence,
        publisher.address,
        value.reportDigest,
        value.policyId,
        value.approvalDigest,
        value.parentDigest
      );

    const stored = await registry.getLatestReport(value.assetKey);
    expect(stored.reportDigest).to.equal(value.reportDigest);
    expect(stored.approvalDigest).to.equal(value.approvalDigest);
    expect(stored.policyId).to.equal(value.policyId);
    expect(stored.parentDigest).to.equal(ethers.ZeroHash);
    expect(stored.publisher).to.equal(publisher.address);

    const legacyValue = BigInt(await time.latest());
    await legacy.connect(publisher).publish(
      value.assetKey,
      value.controlSetRoot,
      value.evidenceRoot,
      ethers.keccak256(ethers.toUtf8Bytes("legacy:v2-compat")),
      0,
      legacyValue,
      legacyValue + 1000n,
      1,
      value.reportURI
    );
    const legacyRead = await registry.getLegacyLatestReport(value.assetKey);
    expect(legacyRead.controlSetRoot).to.equal(value.controlSetRoot);
    expect(legacyRead.publisher).to.equal(publisher.address);

    await legacy.connect(publisher).publish(
      value.assetKey,
      value.controlSetRoot,
      value.evidenceRoot,
      ethers.keccak256(ethers.toUtf8Bytes("legacy:v2-compat:2")),
      0,
      legacyValue,
      legacyValue + 1000n,
      2,
      value.reportURI
    );
    const historicalRead = await registry.getLegacyReport(value.assetKey, 1);
    expect(historicalRead.epochKey).to.equal(
      ethers.keccak256(ethers.toUtf8Bytes("legacy:v2-compat"))
    );
  });

  it("rejects a signature replayed over every stored report field", async function () {
    const { publisher, outsider, registry } = await loadFixture(deployFixture);
    await registry.authorizePublisher(outsider.address);
    const value = await report({ publisher: publisher.address });
    const attestationSignature = await signature(registry, publisher, value);

    const changes = {
      assetKey: ethers.keccak256(ethers.toUtf8Bytes("tampered:asset")),
      reportDigest: ethers.keccak256(ethers.toUtf8Bytes("tampered:report")),
      policyId: ethers.keccak256(ethers.toUtf8Bytes("tampered:policy-id")),
      policyRoot: ethers.keccak256(ethers.toUtf8Bytes("tampered:policy-root")),
      controlSetRoot: ethers.keccak256(ethers.toUtf8Bytes("tampered:controls")),
      evidenceRoot: ethers.keccak256(ethers.toUtf8Bytes("tampered:evidence")),
      approvalDigest: ethers.keccak256(ethers.toUtf8Bytes("tampered:approval")),
      epochKey: ethers.keccak256(ethers.toUtf8Bytes("tampered:epoch")),
      status: 1,
      observedAt: value.observedAt + 1n,
      validUntil: value.validUntil + 1n,
      publisher: outsider.address,
      sequence: 2,
      parentDigest: ethers.keccak256(ethers.toUtf8Bytes("tampered:parent")),
      reportURI: "ipfs://tampered",
    };
    for (const [field, changed] of Object.entries(changes)) {
      await expect(
        publish(
          registry,
          { ...value, [field]: changed },
          attestationSignature,
          publisher
        ),
        field
      ).to.be.revertedWithCustomError(registry, "InvalidAttestationSignature");
    }
  });

  it("binds the correction target into the attestation", async function () {
    const { publisher, registry } = await loadFixture(deployFixture);
    const first = await report({ publisher: publisher.address });
    await publish(registry, first, await signature(registry, publisher, first), publisher);

    const second = await report({
      reportDigest: ethers.keccak256(ethers.toUtf8Bytes("v2:correction:2")),
      publisher: publisher.address,
      sequence: 2,
      parentDigest: first.reportDigest,
    });
    await registry
      .connect(publisher)
      .publishCorrection(1, second, await signature(registry, publisher, second, 1));

    const third = await report({
      reportDigest: ethers.keccak256(ethers.toUtf8Bytes("v2:correction:3")),
      publisher: publisher.address,
      sequence: 3,
      parentDigest: second.reportDigest,
    });
    await expect(
      registry
        .connect(publisher)
        .publishCorrection(2, third, await signature(registry, publisher, third, 1))
    ).to.be.revertedWithCustomError(registry, "InvalidAttestationSignature");
  });

  it("requires the publisher signature and a contiguous parent digest", async function () {
    const { publisher, outsider, registry } = await loadFixture(deployFixture);
    const first = await report({ publisher: publisher.address });
    const firstSignature = await signature(registry, publisher, first);
    const forgedSignature = await signature(registry, outsider, first);

    await expect(publish(registry, first, forgedSignature, outsider))
      .to.be.revertedWithCustomError(registry, "InvalidAttestationSignature");
    await publish(registry, first, firstSignature, outsider);
    expect((await registry.getLatestReport(first.assetKey)).publisher).to.equal(
      publisher.address
    );

    const second = await report({
      reportDigest: ethers.keccak256(ethers.toUtf8Bytes("v2:report:2")),
      epochKey: ethers.keccak256(ethers.toUtf8Bytes("v2:epoch:2")),
      publisher: publisher.address,
      sequence: 2,
      parentDigest: ethers.ZeroHash,
    });
    const secondSignature = await signature(registry, publisher, second);
    await expect(publish(registry, second, secondSignature, publisher))
      .to.be.revertedWithCustomError(registry, "ParentDigestMismatch")
      .withArgs(first.reportDigest, ethers.ZeroHash);
  });

  it("transfers administration only after acceptance by the nominated owner", async function () {
    const { owner, outsider, registry } = await loadFixture(deployFixture);
    const nextPublisher = ethers.Wallet.createRandom().address;

    await expect(registry.transferOwnership(outsider.address))
      .to.emit(registry, "OwnershipTransferStarted")
      .withArgs(owner.address, outsider.address);
    await expect(registry.connect(owner).acceptOwnership())
      .to.be.revertedWithCustomError(registry, "UnauthorizedPendingOwner")
      .withArgs(owner.address);
    await expect(registry.connect(outsider).acceptOwnership())
      .to.emit(registry, "OwnershipTransferred")
      .withArgs(owner.address, outsider.address);
    await expect(registry.authorizePublisher(nextPublisher))
      .to.be.revertedWithCustomError(registry, "UnauthorizedOwner")
      .withArgs(owner.address);
    await registry.connect(outsider).authorizePublisher(nextPublisher);
    expect(await registry.isPublisherAuthorized(nextPublisher)).to.equal(true);
  });
});

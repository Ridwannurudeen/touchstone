const { expect } = require("chai");
const { ethers } = require("hardhat");
const {
  loadFixture,
  time,
} = require("@nomicfoundation/hardhat-network-helpers");

const STATUSES = [
  ["CONFIRMED", 0n],
  ["STALE", 1n],
  ["INCONSISTENT", 2n],
  ["UNVERIFIABLE", 3n],
];

const ASSET_KEY = ethers.keccak256(
  ethers.toUtf8Bytes("eip155:1:0x43415eb6ff9db7e26a15b704e7a3edce97d31c4e")
);
const CONTROL_ROOT = ethers.keccak256(ethers.toUtf8Bytes("control-set-v0"));
const EVIDENCE_ROOT = ethers.keccak256(ethers.toUtf8Bytes("evidence-epoch-1"));
const REPORT_URI =
  "ipfs://bafybeigdyrzt5sfp7udm7hu76uh7y26nf3arm4g4c5u6z7v2x3w4y5z6aa";

describe("TouchstoneRegistry", function () {
  async function deployRegistryFixture() {
    const [owner, publisher, nextPublisher, outsider] =
      await ethers.getSigners();
    const chainId = (await ethers.provider.getNetwork()).chainId;
    const registry = await ethers.deployContract("TouchstoneRegistry", [
      chainId,
    ]);
    await registry.waitForDeployment();
    await registry.authorizePublisher(publisher.address);
    return { owner, publisher, nextPublisher, outsider, registry, chainId };
  }

  async function report(overrides = {}) {
    const now = BigInt(await time.latest());
    return {
      assetKey: ASSET_KEY,
      controlSetRoot: CONTROL_ROOT,
      evidenceRoot: EVIDENCE_ROOT,
      status: 0,
      observedAt: now,
      validUntil: now + 3_600n,
      sequence: 1,
      reportURI: REPORT_URI,
      ...overrides,
    };
  }

  function publish(registry, publisher, value) {
    return registry
      .connect(publisher)
      .publish(
        value.assetKey,
        value.controlSetRoot,
        value.evidenceRoot,
        value.status,
        value.observedAt,
        value.validUntil,
        value.sequence,
        value.reportURI
      );
  }

  it("stores the owner and immutable expected chain ID", async function () {
    const { owner, registry, chainId } = await loadFixture(
      deployRegistryFixture
    );

    expect(await registry.owner()).to.equal(owner.address);
    expect(await registry.expectedChainId()).to.equal(chainId);
  });

  it("emits every field of a publication and stores the same report", async function () {
    const { publisher, registry } = await loadFixture(deployRegistryFixture);
    const value = await report();

    await expect(publish(registry, publisher, value))
      .to.emit(registry, "Published")
      .withArgs(
        value.assetKey,
        value.sequence,
        publisher.address,
        value.controlSetRoot,
        value.evidenceRoot,
        value.status,
        value.observedAt,
        value.validUntil,
        value.reportURI
      );

    const stored = await registry.getLatestReport(value.assetKey);
    expect(stored).to.deep.equal([
      value.controlSetRoot,
      value.evidenceRoot,
      BigInt(value.status),
      value.observedAt,
      value.validUntil,
      publisher.address,
      BigInt(value.sequence),
      value.reportURI,
    ]);
  });

  for (const [name, status] of STATUSES) {
    it(`round-trips the ${name} status`, async function () {
      const { publisher, registry } = await loadFixture(deployRegistryFixture);
      const assetKey = ethers.keccak256(ethers.toUtf8Bytes(`status:${name}`));
      const value = await report({ assetKey, status });

      await publish(registry, publisher, value);

      expect((await registry.getLatestReport(assetKey)).status).to.equal(
        status
      );
    });
  }

  it("accepts every publisher-defined status transition", async function () {
    const { publisher, registry } = await loadFixture(deployRegistryFixture);

    for (const [fromName, fromStatus] of STATUSES) {
      for (const [toName, toStatus] of STATUSES) {
        const assetKey = ethers.keccak256(
          ethers.toUtf8Bytes(`transition:${fromName}:${toName}`)
        );
        const first = await report({ assetKey, status: fromStatus });
        await publish(registry, publisher, first);
        await publish(registry, publisher, {
          ...first,
          status: toStatus,
          sequence: 2,
          reportURI: `${REPORT_URI}/${toName}`,
        });

        expect((await registry.getLatestReport(assetKey)).status).to.equal(
          toStatus
        );
        expect((await registry.getReport(assetKey, 1)).status).to.equal(
          fromStatus
        );
      }
    }
  });

  it("rejects gaps, duplicates, and replayed sequences", async function () {
    const { publisher, registry } = await loadFixture(deployRegistryFixture);
    const first = await report();

    await expect(publish(registry, publisher, { ...first, sequence: 2 }))
      .to.be.revertedWithCustomError(registry, "SequenceMismatch")
      .withArgs(ASSET_KEY, 1, 2);

    await publish(registry, publisher, first);

    await expect(publish(registry, publisher, first))
      .to.be.revertedWithCustomError(registry, "SequenceMismatch")
      .withArgs(ASSET_KEY, 2, 1);
    await expect(publish(registry, publisher, { ...first, sequence: 3 }))
      .to.be.revertedWithCustomError(registry, "SequenceMismatch")
      .withArgs(ASSET_KEY, 2, 3);

    await publish(registry, publisher, { ...first, sequence: 2 });
    await expect(publish(registry, publisher, first))
      .to.be.revertedWithCustomError(registry, "SequenceMismatch")
      .withArgs(ASSET_KEY, 3, 1);
  });

  it("rejects an unauthorized publisher", async function () {
    const { outsider, registry } = await loadFixture(deployRegistryFixture);
    const value = await report();

    await expect(publish(registry, outsider, value))
      .to.be.revertedWithCustomError(registry, "UnauthorizedPublisher")
      .withArgs(outsider.address);
  });

  it("restricts publisher authorization changes to the owner", async function () {
    const { nextPublisher, outsider, registry } = await loadFixture(
      deployRegistryFixture
    );

    await expect(
      registry.connect(outsider).authorizePublisher(nextPublisher.address)
    )
      .to.be.revertedWithCustomError(registry, "UnauthorizedOwner")
      .withArgs(outsider.address);
    await expect(
      registry.connect(outsider).revokePublisher(nextPublisher.address)
    )
      .to.be.revertedWithCustomError(registry, "UnauthorizedOwner")
      .withArgs(outsider.address);
    await expect(
      registry
        .connect(outsider)
        .rotatePublisher(nextPublisher.address, outsider.address)
    )
      .to.be.revertedWithCustomError(registry, "UnauthorizedOwner")
      .withArgs(outsider.address);
  });

  it("emits authorization and revocation events", async function () {
    const { publisher, nextPublisher, registry } = await loadFixture(
      deployRegistryFixture
    );

    await expect(registry.authorizePublisher(nextPublisher.address))
      .to.emit(registry, "PublisherAuthorized")
      .withArgs(nextPublisher.address);
    await expect(registry.revokePublisher(publisher.address))
      .to.emit(registry, "PublisherRevoked")
      .withArgs(publisher.address);

    expect(
      await registry.isPublisherAuthorized(nextPublisher.address)
    ).to.equal(true);
    expect(await registry.isPublisherAuthorized(publisher.address)).to.equal(
      false
    );
  });

  it("rejects invalid publisher authorization state changes", async function () {
    const { publisher, nextPublisher, registry } = await loadFixture(
      deployRegistryFixture
    );

    await expect(registry.authorizePublisher(ethers.ZeroAddress))
      .to.be.revertedWithCustomError(registry, "InvalidPublisher")
      .withArgs(ethers.ZeroAddress);
    await expect(registry.authorizePublisher(publisher.address))
      .to.be.revertedWithCustomError(registry, "PublisherAlreadyAuthorized")
      .withArgs(publisher.address);
    await expect(registry.revokePublisher(nextPublisher.address))
      .to.be.revertedWithCustomError(registry, "PublisherNotAuthorized")
      .withArgs(nextPublisher.address);
    await expect(registry.rotatePublisher(publisher.address, publisher.address))
      .to.be.revertedWithCustomError(registry, "InvalidPublisherRotation")
      .withArgs(publisher.address);
  });

  it("rotates publisher keys while preserving historical attribution", async function () {
    const { publisher, nextPublisher, registry } = await loadFixture(
      deployRegistryFixture
    );
    const first = await report();
    await publish(registry, publisher, first);

    const rotation = registry.rotatePublisher(
      publisher.address,
      nextPublisher.address
    );
    await expect(rotation)
      .to.emit(registry, "PublisherRevoked")
      .withArgs(publisher.address);
    await expect(rotation)
      .to.emit(registry, "PublisherAuthorized")
      .withArgs(nextPublisher.address);

    const receipt = await (await rotation).wait();
    const registryEvents = receipt.logs.map(
      (log) => registry.interface.parseLog(log).name
    );
    expect(registryEvents).to.deep.equal([
      "PublisherRevoked",
      "PublisherAuthorized",
    ]);
    expect(await registry.publisherIdentity(publisher.address)).to.equal(
      publisher.address
    );
    expect(await registry.publisherIdentity(nextPublisher.address)).to.equal(
      publisher.address
    );
    expect(
      await registry.isPublisherFor(publisher.address, nextPublisher.address)
    ).to.equal(true);

    await expect(publish(registry, publisher, { ...first, sequence: 2 }))
      .to.be.revertedWithCustomError(registry, "UnauthorizedPublisher")
      .withArgs(publisher.address);
    await publish(registry, nextPublisher, { ...first, sequence: 2 });

    expect((await registry.getReport(ASSET_KEY, 1)).publisher).to.equal(
      publisher.address
    );
    expect((await registry.getReport(ASSET_KEY, 2)).publisher).to.equal(
      nextPublisher.address
    );
  });

  it("preserves publisher identity through revocation and reauthorization", async function () {
    const { publisher, nextPublisher, registry } = await loadFixture(
      deployRegistryFixture
    );
    await registry.rotatePublisher(publisher.address, nextPublisher.address);

    await registry.revokePublisher(nextPublisher.address);
    await registry.authorizePublisher(nextPublisher.address);

    expect(await registry.publisherIdentity(nextPublisher.address)).to.equal(
      publisher.address
    );
    expect(
      await registry.isPublisherFor(publisher.address, nextPublisher.address)
    ).to.equal(true);
  });

  it("rejects reusing a key that belongs to another publisher lineage", async function () {
    const { publisher, nextPublisher, outsider, registry } = await loadFixture(
      deployRegistryFixture
    );
    await registry.authorizePublisher(nextPublisher.address);
    await registry.revokePublisher(nextPublisher.address);

    await expect(
      registry.rotatePublisher(publisher.address, nextPublisher.address)
    )
      .to.be.revertedWithCustomError(registry, "PublisherIdentityConflict")
      .withArgs(
        nextPublisher.address,
        nextPublisher.address,
        publisher.address
      );

    expect(await registry.isPublisherAuthorized(publisher.address)).to.equal(
      true
    );
    expect(
      await registry.isPublisherAuthorized(nextPublisher.address)
    ).to.equal(false);
    expect(await registry.publisherIdentity(nextPublisher.address)).to.equal(
      nextPublisher.address
    );
    expect(await registry.publisherIdentity(outsider.address)).to.equal(
      ethers.ZeroAddress
    );
  });

  it("appends a correction without mutating its referenced report", async function () {
    const { publisher, registry } = await loadFixture(deployRegistryFixture);
    const first = await report();
    await publish(registry, publisher, first);
    const corrected = {
      ...first,
      evidenceRoot: ethers.keccak256(ethers.toUtf8Bytes("corrected-evidence")),
      status: 2,
      sequence: 2,
      reportURI: `${REPORT_URI}/correction`,
    };

    const transaction = registry
      .connect(publisher)
      .publishCorrection(
        corrected.assetKey,
        1,
        corrected.controlSetRoot,
        corrected.evidenceRoot,
        corrected.status,
        corrected.observedAt,
        corrected.validUntil,
        corrected.sequence,
        corrected.reportURI
      );
    await expect(transaction)
      .to.emit(registry, "Corrected")
      .withArgs(
        corrected.assetKey,
        2,
        1,
        publisher.address,
        corrected.controlSetRoot,
        corrected.evidenceRoot,
        corrected.status,
        corrected.observedAt,
        corrected.validUntil,
        corrected.reportURI
      );

    const receipt = await (await transaction).wait();
    const registryEvents = receipt.logs
      .map((log) => {
        try {
          return registry.interface.parseLog(log)?.name;
        } catch {
          return undefined;
        }
      })
      .filter(Boolean);
    expect(registryEvents).to.deep.equal(["Corrected"]);
    expect(await registry.correctionTarget(ASSET_KEY, 2)).to.equal(1);
    expect((await registry.getReport(ASSET_KEY, 1)).evidenceRoot).to.equal(
      first.evidenceRoot
    );
    expect((await registry.getReport(ASSET_KEY, 2)).evidenceRoot).to.equal(
      corrected.evidenceRoot
    );
  });

  it("rejects corrections that do not reference an existing prior sequence", async function () {
    const { publisher, registry } = await loadFixture(deployRegistryFixture);
    const first = await report();
    await publish(registry, publisher, first);

    for (const correctedSequence of [0, 2]) {
      await expect(
        registry
          .connect(publisher)
          .publishCorrection(
            ASSET_KEY,
            correctedSequence,
            CONTROL_ROOT,
            EVIDENCE_ROOT,
            0,
            first.observedAt,
            first.validUntil,
            2,
            REPORT_URI
          )
      )
        .to.be.revertedWithCustomError(registry, "InvalidCorrection")
        .withArgs(ASSET_KEY, correctedSequence);
    }
  });

  it("rejects publication when the immutable expected chain ID mismatches", async function () {
    const [, publisher] = await ethers.getSigners();
    const actualChainId = (await ethers.provider.getNetwork()).chainId;
    const registry = await ethers.deployContract("TouchstoneRegistry", [
      actualChainId + 1n,
    ]);
    await registry.waitForDeployment();
    await registry.authorizePublisher(publisher.address);
    const value = await report();

    await expect(publish(registry, publisher, value))
      .to.be.revertedWithCustomError(registry, "ChainIdMismatch")
      .withArgs(actualChainId + 1n, actualChainId);

    await expect(
      registry
        .connect(publisher)
        .publishCorrection(
          ASSET_KEY,
          1,
          CONTROL_ROOT,
          EVIDENCE_ROOT,
          0,
          value.observedAt,
          value.validUntil,
          1,
          REPORT_URI
        )
    )
      .to.be.revertedWithCustomError(registry, "ChainIdMismatch")
      .withArgs(actualChainId + 1n, actualChainId);
  });

  it("rejects malformed publication metadata", async function () {
    const { publisher, registry } = await loadFixture(deployRegistryFixture);
    const value = await report();
    const current = BigInt(await time.latest());

    await expect(
      publish(registry, publisher, { ...value, assetKey: ethers.ZeroHash })
    ).to.be.revertedWithCustomError(registry, "InvalidAssetKey");
    await expect(
      publish(registry, publisher, { ...value, reportURI: "" })
    ).to.be.revertedWithCustomError(registry, "InvalidReportURI");
    await expect(
      publish(registry, publisher, { ...value, observedAt: current + 100n })
    ).to.be.revertedWithCustomError(registry, "FutureObservation");
    await expect(
      publish(registry, publisher, {
        ...value,
        observedAt: current,
        validUntil: current - 1n,
      })
    )
      .to.be.revertedWithCustomError(registry, "InvalidValidityWindow")
      .withArgs(current, current - 1n);
  });

  it("rejects unknown report history lookups", async function () {
    const { registry } = await loadFixture(deployRegistryFixture);

    await expect(registry.getReport(ASSET_KEY, 0))
      .to.be.revertedWithCustomError(registry, "UnknownReport")
      .withArgs(ASSET_KEY, 0);
    await expect(registry.getReport(ASSET_KEY, 1))
      .to.be.revertedWithCustomError(registry, "UnknownReport")
      .withArgs(ASSET_KEY, 1);
  });

  it("reports informational publish gas", async function () {
    const { publisher, registry } = await loadFixture(deployRegistryFixture);
    const value = await report();
    const receipt = await (await publish(registry, publisher, value)).wait();

    console.log(
      `    Gas snapshot - TouchstoneRegistry.publish: ${receipt.gasUsed} gas`
    );
    expect(receipt.gasUsed).to.be.greaterThan(0);
  });
});

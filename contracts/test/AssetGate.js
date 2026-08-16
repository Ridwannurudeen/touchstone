const { expect } = require("chai");
const { ethers } = require("hardhat");
const {
  loadFixture,
  time,
} = require("@nomicfoundation/hardhat-network-helpers");

const ASSET_KEY = ethers.keccak256(
  ethers.toUtf8Bytes("eip155:1:0x43415eb6ff9db7e26a15b704e7a3edce97d31c4e")
);
const CONTROL_ROOT = ethers.keccak256(ethers.toUtf8Bytes("control-set-v0"));
const OTHER_ROOT = ethers.keccak256(ethers.toUtf8Bytes("control-set-v1"));
const EVIDENCE_ROOT = ethers.keccak256(ethers.toUtf8Bytes("evidence-epoch-1"));
const REPORT_URI =
  "ipfs://bafybeigdyrzt5sfp7udm7hu76uh7y26nf3arm4g4c5u6z7v2x3w4y5z6aa";

describe("AssetGate", function () {
  async function deployRegistryFixture() {
    const [owner, publisher, otherPublisher, caller] =
      await ethers.getSigners();
    const chainId = (await ethers.provider.getNetwork()).chainId;
    const registry = await ethers.deployContract("TouchstoneRegistry", [
      chainId,
    ]);
    await registry.waitForDeployment();
    await registry.authorizePublisher(publisher.address);
    return { owner, publisher, otherPublisher, caller, registry };
  }

  async function deployGate(
    registry,
    {
      allowedStatuses = 1,
      maxObservationAge = 3_600,
      requiredPublisher = ethers.ZeroAddress,
      requiredControlSetRoot = ethers.ZeroHash,
    } = {}
  ) {
    const gate = await ethers.deployContract("AssetGate", [
      registry,
      allowedStatuses,
      maxObservationAge,
      requiredPublisher,
      requiredControlSetRoot,
    ]);
    await gate.waitForDeployment();
    return gate;
  }

  async function publish(registry, publisher, overrides = {}) {
    const now = BigInt(await time.latest());
    const value = {
      assetKey: ASSET_KEY,
      controlSetRoot: CONTROL_ROOT,
      evidenceRoot: EVIDENCE_ROOT,
      status: 0,
      observedAt: now,
      validUntil: now + 10_000n,
      sequence: 1,
      reportURI: REPORT_URI,
      ...overrides,
    };
    await registry
      .connect(publisher)
      .publish(
        value.assetKey,
        value.controlSetRoot,
        value.evidenceRoot,
        value.epochKey ??
          ethers.keccak256(ethers.toUtf8Bytes(`epoch:${value.sequence}`)),
        value.status,
        value.observedAt,
        value.validUntil,
        value.sequence,
        value.reportURI
      );
    return value;
  }

  it("stores all gate parameters", async function () {
    const { publisher, registry } = await loadFixture(deployRegistryFixture);
    const gate = await deployGate(registry, {
      allowedStatuses: 0b0101,
      maxObservationAge: 900,
      requiredPublisher: publisher.address,
      requiredControlSetRoot: CONTROL_ROOT,
    });

    expect(await gate.registry()).to.equal(await registry.getAddress());
    expect(await gate.allowedStatuses()).to.equal(0b0101);
    expect(await gate.maxObservationAge()).to.equal(900);
    expect(await gate.requiredPublisher()).to.equal(publisher.address);
    expect(await gate.requiredControlSetRoot()).to.equal(CONTROL_ROOT);
  });

  it("rejects an invalid registry or status mask", async function () {
    const { owner, registry } = await loadFixture(deployRegistryFixture);
    const Gate = await ethers.getContractFactory("AssetGate");

    await expect(
      Gate.deploy(owner.address, 1, 60, ethers.ZeroAddress, ethers.ZeroHash)
    )
      .to.be.revertedWithCustomError(Gate, "InvalidRegistry")
      .withArgs(owner.address);
    for (const mask of [0, 16, 255]) {
      await expect(
        Gate.deploy(registry, mask, 60, ethers.ZeroAddress, ethers.ZeroHash)
      )
        .to.be.revertedWithCustomError(Gate, "InvalidStatusMask")
        .withArgs(mask);
    }
  });

  it("returns unknown asset for an unpublished key", async function () {
    const { registry } = await loadFixture(deployRegistryFixture);
    const gate = await deployGate(registry);

    expect(await gate.check(ASSET_KEY)).to.deep.equal([false, "unknown asset"]);
  });

  it("returns status not allowed", async function () {
    const { publisher, registry } = await loadFixture(deployRegistryFixture);
    await publish(registry, publisher, { status: 1 });
    const gate = await deployGate(registry, { allowedStatuses: 1 });

    expect(await gate.check(ASSET_KEY)).to.deep.equal([
      false,
      "status not allowed",
    ]);
  });

  it("uses every status bit in the allowed-status mask", async function () {
    const { publisher, registry } = await loadFixture(deployRegistryFixture);

    for (let status = 0; status < 4; status += 1) {
      const assetKey = ethers.keccak256(
        ethers.toUtf8Bytes(`gate-status:${status}`)
      );
      await publish(registry, publisher, { assetKey, status });
      const gate = await deployGate(registry, { allowedStatuses: 1 << status });
      expect(await gate.check(assetKey)).to.deep.equal([true, "allowed"]);
    }
  });

  it("accepts at the exact validUntil second and expires one second later", async function () {
    const { publisher, registry } = await loadFixture(deployRegistryFixture);
    const now = BigInt(await time.latest());
    const validUntil = now + 100n;
    await publish(registry, publisher, { observedAt: now, validUntil });
    const gate = await deployGate(registry, { maxObservationAge: 1_000 });

    await time.increaseTo(validUntil);
    expect(await gate.check(ASSET_KEY)).to.deep.equal([true, "allowed"]);

    await time.increase(1);
    expect(await gate.check(ASSET_KEY)).to.deep.equal([
      false,
      "observation too old",
    ]);
  });

  it("accepts at the exact maximum-age second and expires one second later", async function () {
    const { publisher, registry } = await loadFixture(deployRegistryFixture);
    const observedAt = BigInt(await time.latest());
    const maxObservationAge = 100n;
    await publish(registry, publisher, {
      observedAt,
      validUntil: observedAt + 1_000n,
    });
    const gate = await deployGate(registry, { maxObservationAge });

    await time.increaseTo(observedAt + maxObservationAge);
    expect(await gate.check(ASSET_KEY)).to.deep.equal([true, "allowed"]);

    await time.increase(1);
    expect(await gate.check(ASSET_KEY)).to.deep.equal([
      false,
      "observation too old",
    ]);
  });

  it("returns wrong publisher when a fixed publisher does not match", async function () {
    const { publisher, otherPublisher, registry } = await loadFixture(
      deployRegistryFixture
    );
    await publish(registry, publisher);
    const gate = await deployGate(registry, {
      requiredPublisher: otherPublisher.address,
    });

    expect(await gate.check(ASSET_KEY)).to.deep.equal([
      false,
      "wrong publisher",
    ]);
  });

  it("returns wrong publisher when an any-authorized gate sees a revoked posting key", async function () {
    const { publisher, otherPublisher, registry } = await loadFixture(
      deployRegistryFixture
    );
    await publish(registry, publisher);
    const gate = await deployGate(registry);
    await registry.rotatePublisher(publisher.address, otherPublisher.address);

    expect(await gate.check(ASSET_KEY)).to.deep.equal([
      false,
      "wrong publisher",
    ]);
  });

  it("keeps a publisher-pinned gate usable across key rotation", async function () {
    const { publisher, otherPublisher, registry } = await loadFixture(
      deployRegistryFixture
    );
    const first = await publish(registry, publisher);
    const gate = await deployGate(registry, {
      requiredPublisher: publisher.address,
    });
    await registry.rotatePublisher(publisher.address, otherPublisher.address);

    expect(await gate.check(ASSET_KEY)).to.deep.equal([
      false,
      "wrong publisher",
    ]);

    await registry
      .connect(otherPublisher)
      .publish(
        ASSET_KEY,
        CONTROL_ROOT,
        EVIDENCE_ROOT,
        ethers.keccak256(ethers.toUtf8Bytes("epoch:2")),
        0,
        first.observedAt,
        first.validUntil,
        2,
        `${REPORT_URI}/rotated`
      );

    expect(await gate.check(ASSET_KEY)).to.deep.equal([true, "allowed"]);
  });

  it("keeps a publisher-pinned gate usable after successor reauthorization", async function () {
    const { publisher, otherPublisher, registry } = await loadFixture(
      deployRegistryFixture
    );
    const first = await publish(registry, publisher);
    const gate = await deployGate(registry, {
      requiredPublisher: publisher.address,
    });
    await registry.rotatePublisher(publisher.address, otherPublisher.address);
    await registry
      .connect(otherPublisher)
      .publish(
        ASSET_KEY,
        CONTROL_ROOT,
        EVIDENCE_ROOT,
        ethers.keccak256(ethers.toUtf8Bytes("epoch:2")),
        0,
        first.observedAt,
        first.validUntil,
        2,
        `${REPORT_URI}/rotated`
      );

    await registry.revokePublisher(otherPublisher.address);
    await registry.authorizePublisher(otherPublisher.address);

    expect(await gate.check(ASSET_KEY)).to.deep.equal([true, "allowed"]);
  });

  it("returns control-set mismatch", async function () {
    const { publisher, registry } = await loadFixture(deployRegistryFixture);
    await publish(registry, publisher);
    const gate = await deployGate(registry, {
      requiredControlSetRoot: OTHER_ROOT,
    });

    expect(await gate.check(ASSET_KEY)).to.deep.equal([
      false,
      "control-set mismatch",
    ]);
  });

  it("allows a matching report and records a successful demand", async function () {
    const { publisher, caller, registry } = await loadFixture(
      deployRegistryFixture
    );
    await publish(registry, publisher);
    const gate = await deployGate(registry, {
      requiredPublisher: publisher.address,
      requiredControlSetRoot: CONTROL_ROOT,
    });

    expect(await gate.check(ASSET_KEY)).to.deep.equal([true, "allowed"]);
    await expect(gate.connect(caller).demand(ASSET_KEY))
      .to.emit(gate, "Demanded")
      .withArgs(ASSET_KEY, caller.address);
  });

  const refusalCases = [
    ["unknown asset", async () => ({})],
    [
      "status not allowed",
      async ({ registry, publisher }) => {
        await publish(registry, publisher, { status: 1 });
      },
    ],
    [
      "observation too old",
      async ({ registry, publisher }) => {
        const now = BigInt(await time.latest());
        await publish(registry, publisher, { observedAt: now - 100n });
      },
    ],
    [
      "wrong publisher",
      async ({ registry, publisher, otherPublisher }) => {
        await publish(registry, publisher);
        return { requiredPublisher: otherPublisher.address };
      },
    ],
    [
      "control-set mismatch",
      async ({ registry, publisher }) => {
        await publish(registry, publisher);
        return { requiredControlSetRoot: OTHER_ROOT };
      },
    ],
  ];

  for (const [reason, arrange] of refusalCases) {
    it(`demand reverts with the distinct ${reason} reason`, async function () {
      const fixture = await loadFixture(deployRegistryFixture);
      const gateOptions = (await arrange(fixture)) || {};
      if (reason === "observation too old") gateOptions.maxObservationAge = 10;
      const gate = await deployGate(fixture.registry, gateOptions);

      await expect(gate.demand(ASSET_KEY))
        .to.be.revertedWithCustomError(gate, "GateRefused")
        .withArgs(ASSET_KEY, reason);
    });
  }
});

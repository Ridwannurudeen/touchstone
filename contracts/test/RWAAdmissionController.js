const { expect } = require("chai");
const { ethers } = require("hardhat");
const {
  loadFixture,
  time,
} = require("@nomicfoundation/hardhat-network-helpers");

const ASSET_KEY = ethers.keccak256(ethers.toUtf8Bytes("admission:asset"));
const SECOND_KEY = ethers.keccak256(
  ethers.toUtf8Bytes("admission:second-asset")
);
const UNKNOWN_KEY = ethers.keccak256(
  ethers.toUtf8Bytes("admission:never-reported")
);
const POLICY_ID = ethers.keccak256(ethers.toUtf8Bytes("admission:policy"));
const POLICY_ROOT = ethers.keccak256(
  ethers.toUtf8Bytes("admission:policy-root")
);
const OTHER_POLICY_ROOT = ethers.keccak256(
  ethers.toUtf8Bytes("admission:other-policy-root")
);
const CONTROL_ROOT = ethers.keccak256(ethers.toUtf8Bytes("admission:controls"));
const EVIDENCE_ROOT = ethers.keccak256(
  ethers.toUtf8Bytes("admission:evidence")
);
const APPROVAL_DIGEST = ethers.keccak256(
  ethers.toUtf8Bytes("admission:approval")
);
const REPORT_URI = "urn:touchstone:admission:test";
const MAX_AGE = 3_600;
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

describe("RWAAdmissionController", function () {
  async function deployFixture() {
    const [proposer, publisher, caller] = await ethers.getSigners();
    const chainId = (await ethers.provider.getNetwork()).chainId;
    const legacy = await ethers.deployContract("TouchstoneRegistry", [chainId]);
    await legacy.waitForDeployment();
    const registry = await ethers.deployContract("TouchstoneRegistryV2", [
      chainId,
      legacy.target,
    ]);
    await registry.waitForDeployment();
    await registry.authorizePublisher(publisher.address);
    const gate = await ethers.deployContract("AssetGateV2", [
      registry.target,
      1, // CONFIRMED only
      MAX_AGE,
      ethers.ZeroAddress,
      POLICY_ID,
      POLICY_ROOT,
      CONTROL_ROOT,
      APPROVAL_DIGEST,
    ]);
    await gate.waitForDeployment();
    const controller = await ethers.deployContract("RWAAdmissionController", [
      proposer.address,
    ]);
    await controller.waitForDeployment();
    return { caller, controller, gate, proposer, publisher, registry };
  }

  async function report(publisher, overrides = {}) {
    const now = BigInt(await time.latest());
    const assetKey = overrides.assetKey || ASSET_KEY;
    const sequence = overrides.sequence || 1;
    return {
      assetKey,
      reportDigest: ethers.keccak256(
        ethers.solidityPacked(["bytes32", "uint64"], [assetKey, sequence])
      ),
      policyId: POLICY_ID,
      policyRoot: POLICY_ROOT,
      controlSetRoot: CONTROL_ROOT,
      evidenceRoot: EVIDENCE_ROOT,
      approvalDigest: APPROVAL_DIGEST,
      epochKey: ethers.keccak256(
        ethers.solidityPacked(
          ["bytes32", "string", "uint64"],
          [assetKey, "epoch:", sequence]
        )
      ),
      status: 0, // CONFIRMED
      observedAt: now,
      validUntil: now + BigInt(MAX_AGE),
      publisher: publisher.address,
      sequence,
      parentDigest: ethers.ZeroHash,
      reportURI: REPORT_URI,
      ...overrides,
    };
  }

  async function publish(registry, publisher, value) {
    const network = await ethers.provider.getNetwork();
    const signature = await publisher.signTypedData(
      {
        name: "Touchstone Registry",
        version: "2",
        chainId: network.chainId,
        verifyingContract: registry.target,
      },
      TYPES,
      { ...value, correctionOf: 0 }
    );
    await registry
      .connect(publisher)
      .publish(
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
        signature
      );
  }

  it("refuses a zero proposer at construction", async function () {
    const Controller = await ethers.getContractFactory(
      "RWAAdmissionController"
    );
    await expect(
      Controller.deploy(ethers.ZeroAddress)
    ).to.be.revertedWithCustomError(Controller, "InvalidProposer");
  });

  it("only the proposer may propose, and each binding is validated and permanent", async function () {
    const { caller, controller, gate, proposer } = await loadFixture(
      deployFixture
    );

    await expect(
      controller.connect(caller).propose(ASSET_KEY, gate.target)
    ).to.be.revertedWithCustomError(controller, "UnauthorizedProposer");
    await expect(
      controller.connect(proposer).propose(ethers.ZeroHash, gate.target)
    ).to.be.revertedWithCustomError(controller, "InvalidAssetKey");
    await expect(
      controller.connect(proposer).propose(ASSET_KEY, caller.address)
    ).to.be.revertedWithCustomError(controller, "InvalidGate");

    await expect(controller.connect(proposer).propose(ASSET_KEY, gate.target))
      .to.emit(controller, "AssetProposed")
      .withArgs(ASSET_KEY, gate.target, proposer.address);
    await expect(
      controller.connect(proposer).propose(ASSET_KEY, gate.target)
    ).to.be.revertedWithCustomError(controller, "AlreadyProposed");

    expect(await controller.proposedCount()).to.equal(1);
    expect(await controller.proposedAt(0)).to.equal(ASSET_KEY);
  });

  it("activates only when the gate allows, and records who and when", async function () {
    const { caller, controller, gate, proposer, publisher, registry } =
      await loadFixture(deployFixture);
    await controller.connect(proposer).propose(ASSET_KEY, gate.target);

    // Nothing published yet: the gate has never seen this key.
    await expect(controller.connect(caller).activate(ASSET_KEY))
      .to.be.revertedWithCustomError(controller, "AdmissionRefused")
      .withArgs(ASSET_KEY, "unknown asset");

    await publish(registry, publisher, await report(publisher));
    await expect(controller.connect(caller).activate(ASSET_KEY))
      .to.emit(controller, "AssetActivated")
      .withArgs(ASSET_KEY, caller.address);

    const admission = await controller.admissionOf(ASSET_KEY);
    expect(admission.gate).to.equal(gate.target);
    expect(admission.activator).to.equal(caller.address);
    expect(admission.activatedAt).to.not.equal(0);

    await expect(
      controller.connect(caller).activate(ASSET_KEY)
    ).to.be.revertedWithCustomError(controller, "AlreadyActive");
  });

  it("refuses to activate what was never proposed", async function () {
    const { caller, controller } = await loadFixture(deployFixture);
    await expect(
      controller.connect(caller).activate(UNKNOWN_KEY)
    ).to.be.revertedWithCustomError(controller, "NotProposed");
  });

  it("executes for an active asset and counts each use", async function () {
    const { caller, controller, gate, proposer, publisher, registry } =
      await loadFixture(deployFixture);
    await controller.connect(proposer).propose(ASSET_KEY, gate.target);
    await publish(registry, publisher, await report(publisher));
    await controller.connect(caller).activate(ASSET_KEY);

    await expect(controller.connect(caller).execute(ASSET_KEY))
      .to.emit(controller, "AssetUsed")
      .withArgs(ASSET_KEY, caller.address, 1);
    expect(await controller.useCount()).to.equal(1);
  });

  it("suspends use by arithmetic when the report goes stale, and recovers on a fresh one", async function () {
    const { caller, controller, gate, proposer, publisher, registry } =
      await loadFixture(deployFixture);
    await controller.connect(proposer).propose(ASSET_KEY, gate.target);
    const first = await report(publisher);
    await publish(registry, publisher, first);
    await controller.connect(caller).activate(ASSET_KEY);

    await time.increase(MAX_AGE + 1);

    // No transaction ran against the controller; the admission record is untouched and
    // the asset is suspended anyway.
    let [active, reason] = await controller.isActive(ASSET_KEY);
    expect(active).to.equal(false);
    expect(reason).to.equal("observation too old");
    await expect(controller.connect(caller).execute(ASSET_KEY))
      .to.be.revertedWithCustomError(controller, "AdmissionRefused")
      .withArgs(ASSET_KEY, "observation too old");

    // A fresh CONFIRMED report lifts the suspension the same way — nothing to re-activate.
    await publish(registry, publisher, {
      ...(await report(publisher, { sequence: 2 })),
      parentDigest: first.reportDigest,
    });
    [active, reason] = await controller.isActive(ASSET_KEY);
    expect(active).to.equal(true);
    expect(reason).to.equal("allowed");
    await expect(controller.connect(caller).execute(ASSET_KEY)).to.emit(
      controller,
      "AssetUsed"
    );
  });

  it("an expired report prevents activation in the first place", async function () {
    const { caller, controller, gate, proposer, publisher, registry } =
      await loadFixture(deployFixture);
    await controller.connect(proposer).propose(SECOND_KEY, gate.target);
    await publish(
      registry,
      publisher,
      await report(publisher, { assetKey: SECOND_KEY })
    );
    await time.increase(MAX_AGE + 1);

    await expect(controller.connect(caller).activate(SECOND_KEY))
      .to.be.revertedWithCustomError(controller, "AdmissionRefused")
      .withArgs(SECOND_KEY, "observation too old");
  });

  it("a policy-root mismatch prevents activation", async function () {
    const { caller, controller, proposer, publisher, registry } =
      await loadFixture(deployFixture);
    // A gate demanding a different policy root than the one the reports carry.
    const strictGate = await ethers.deployContract("AssetGateV2", [
      registry.target,
      1,
      MAX_AGE,
      ethers.ZeroAddress,
      POLICY_ID,
      OTHER_POLICY_ROOT,
      CONTROL_ROOT,
      APPROVAL_DIGEST,
    ]);
    await strictGate.waitForDeployment();
    await controller.connect(proposer).propose(ASSET_KEY, strictGate.target);
    await publish(registry, publisher, await report(publisher));

    await expect(controller.connect(caller).activate(ASSET_KEY))
      .to.be.revertedWithCustomError(controller, "AdmissionRefused")
      .withArgs(ASSET_KEY, "policy-root mismatch");
  });

  it("reports inactive with the exact missing step before admission", async function () {
    const { caller, controller, gate, proposer } = await loadFixture(
      deployFixture
    );

    let [active, reason] = await controller.isActive(ASSET_KEY);
    expect(active).to.equal(false);
    expect(reason).to.equal("not proposed");

    await controller.connect(proposer).propose(ASSET_KEY, gate.target);
    [active, reason] = await controller.isActive(ASSET_KEY);
    expect(active).to.equal(false);
    expect(reason).to.equal("not activated");
    await expect(controller.connect(caller).execute(ASSET_KEY))
      .to.be.revertedWithCustomError(controller, "AdmissionRefused")
      .withArgs(ASSET_KEY, "not activated");
  });
});

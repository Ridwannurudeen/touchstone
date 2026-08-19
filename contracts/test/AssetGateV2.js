const { expect } = require("chai");
const { ethers } = require("hardhat");
const { loadFixture, time } = require("@nomicfoundation/hardhat-network-helpers");

const ASSET_KEY = ethers.keccak256(ethers.toUtf8Bytes("v2:guarded-asset"));
const POLICY_ID = ethers.keccak256(ethers.toUtf8Bytes("v2:guarded-policy"));
const POLICY_ROOT = ethers.keccak256(ethers.toUtf8Bytes("v2:guarded-policy-root"));
const CONTROL_ROOT = ethers.keccak256(ethers.toUtf8Bytes("v2:guarded-controls"));
const OTHER_ROOT = ethers.keccak256(ethers.toUtf8Bytes("v2:other-controls"));
const EVIDENCE_ROOT = ethers.keccak256(ethers.toUtf8Bytes("v2:guarded-evidence"));
const APPROVAL_DIGEST = ethers.keccak256(ethers.toUtf8Bytes("v2:guarded-approval"));
const REPORT_URI = "urn:touchstone:v2:guarded-action:test";
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

describe("AssetGateV2", function () {
  async function deployRegistryFixture() {
    const [, publisher, otherPublisher, caller] = await ethers.getSigners();
    const chainId = (await ethers.provider.getNetwork()).chainId;
    const legacy = await ethers.deployContract("TouchstoneRegistry", [chainId]);
    await legacy.waitForDeployment();
    const registry = await ethers.deployContract("TouchstoneRegistryV2", [
      chainId,
      legacy.target,
    ]);
    await registry.waitForDeployment();
    await registry.authorizePublisher(publisher.address);
    await registry.authorizePublisher(otherPublisher.address);
    return { caller, otherPublisher, publisher, registry };
  }

  async function report(publisher, overrides = {}) {
    const now = BigInt(await time.latest());
    const assetKey = overrides.assetKey || ASSET_KEY;
    return {
      assetKey,
      reportDigest: ethers.keccak256(
        ethers.solidityPacked(
          ["bytes32", "uint8"],
          [assetKey, overrides.status || 0]
        )
      ),
      policyId: POLICY_ID,
      policyRoot: POLICY_ROOT,
      controlSetRoot: CONTROL_ROOT,
      evidenceRoot: EVIDENCE_ROOT,
      approvalDigest: APPROVAL_DIGEST,
      epochKey: ethers.keccak256(
        ethers.solidityPacked(["bytes32", "string"], [assetKey, "epoch:1"])
      ),
      status: 0,
      observedAt: now,
      validUntil: now + 3_600n,
      publisher: publisher.address,
      sequence: 1,
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
    await registry.connect(publisher).publish(
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

  async function deployGate(registry, overrides = {}) {
    const gate = await ethers.deployContract("AssetGateV2", [
      registry.target,
      overrides.allowedStatuses || 1,
      overrides.maxObservationAge || 3_600,
      overrides.requiredPublisher || ethers.ZeroAddress,
      overrides.expectedPolicyId ?? POLICY_ID,
      overrides.expectedPolicyRoot ?? POLICY_ROOT,
      overrides.requiredControlSetRoot || CONTROL_ROOT,
      overrides.expectedApprovalDigest || APPROVAL_DIGEST,
    ]);
    await gate.waitForDeployment();
    return gate;
  }

  it("requires and stores exact nonzero policy pins", async function () {
    const { registry } = await loadFixture(deployRegistryFixture);
    const Gate = await ethers.getContractFactory("AssetGateV2");
    const arguments_ = [
      registry.target,
      1,
      3_600,
      ethers.ZeroAddress,
      POLICY_ID,
      POLICY_ROOT,
      CONTROL_ROOT,
      APPROVAL_DIGEST,
    ];
    const gate = await deployGate(registry);

    expect(await gate.expectedPolicyId()).to.equal(POLICY_ID);
    expect(await gate.expectedPolicyRoot()).to.equal(POLICY_ROOT);
    expect(await gate.expectedApprovalDigest()).to.equal(APPROVAL_DIGEST);
    await expect(Gate.deploy(...arguments_.with(4, ethers.ZeroHash)))
      .to.be.revertedWithCustomError(Gate, "InvalidPolicyId");
    await expect(Gate.deploy(...arguments_.with(5, ethers.ZeroHash)))
      .to.be.revertedWithCustomError(Gate, "InvalidPolicyRoot");
    // The two pins an audit found enforceable only in the deploy script. A gate
    // instantiated around that script must not be able to opt out of either.
    await expect(Gate.deploy(...arguments_.with(6, ethers.ZeroHash)))
      .to.be.revertedWithCustomError(Gate, "InvalidControlSetRoot");
    await expect(Gate.deploy(...arguments_.with(7, ethers.ZeroHash)))
      .to.be.revertedWithCustomError(Gate, "InvalidApprovalDigest");
  });

  it("refuses a report whose approval digest is not the pinned one", async function () {
    const { registry, publisher } = await loadFixture(deployRegistryFixture);
    const gate = await deployGate(registry);
    await publish(
      registry,
      publisher,
      await report(publisher, {
        approvalDigest: ethers.keccak256(
          ethers.toUtf8Bytes("a different human decision")
        ),
      })
    );
    const [allowed, reason] = await gate.check(ASSET_KEY);
    expect(allowed).to.equal(false);
    expect(reason).to.equal("approval mismatch");
  });

  it("accepts a new canonical report digest under the pinned policy", async function () {
    const { publisher, registry } = await loadFixture(deployRegistryFixture);
    const first = await report(publisher);
    await publish(registry, publisher, first);
    const gate = await deployGate(registry);
    expect(await gate.check(ASSET_KEY)).to.deep.equal([true, "allowed"]);

    const second = await report(publisher, {
      reportDigest: ethers.keccak256(ethers.toUtf8Bytes("v2:guarded-report:2")),
      epochKey: ethers.keccak256(ethers.toUtf8Bytes("v2:guarded-epoch:2")),
      sequence: 2,
      parentDigest: first.reportDigest,
    });
    await publish(registry, publisher, second);

    expect(await gate.check(ASSET_KEY)).to.deep.equal([true, "allowed"]);
  });

  it("permits V2-backed execution through the existing GuardedAction", async function () {
    const { caller, publisher, registry } = await loadFixture(deployRegistryFixture);
    await publish(registry, publisher, await report(publisher));
    const gate = await deployGate(registry, {
      requiredPublisher: publisher.address,
    });
    const action = await ethers.deployContract("GuardedAction", [
      gate.target,
      ASSET_KEY,
    ]);
    await action.waitForDeployment();

    await expect(action.connect(caller).execute())
      .to.emit(action, "ActionExecuted")
      .withArgs(ASSET_KEY, caller.address, 1);
    expect(await action.actionCount()).to.equal(1);
  });

  it("refuses V2-backed execution through the existing GuardedAction", async function () {
    const { publisher, registry } = await loadFixture(deployRegistryFixture);
    await publish(registry, publisher, await report(publisher, { status: 3 }));
    const gate = await deployGate(registry, {
      requiredPublisher: publisher.address,
    });
    const action = await ethers.deployContract("GuardedAction", [
      gate.target,
      ASSET_KEY,
    ]);
    await action.waitForDeployment();

    await expect(action.execute())
      .to.be.revertedWithCustomError(action, "ActionRefused")
      .withArgs(ASSET_KEY, "status not allowed");
    expect(await action.actionCount()).to.equal(0);
  });

  const refusalCases = [
    ["unknown asset", async () => ({})],
    [
      "policy-id mismatch",
      async ({ publisher, registry }) => {
        await publish(registry, publisher, await report(publisher));
        return {
          expectedPolicyId: ethers.keccak256(ethers.toUtf8Bytes("other-policy")),
        };
      },
    ],
    [
      "policy-root mismatch",
      async ({ publisher, registry }) => {
        await publish(registry, publisher, await report(publisher));
        return {
          expectedPolicyRoot: ethers.keccak256(ethers.toUtf8Bytes("other-root")),
        };
      },
    ],
    [
      "status not allowed",
      async ({ publisher, registry }) => {
        await publish(registry, publisher, await report(publisher, { status: 1 }));
      },
    ],
    [
      "observation too old",
      async ({ publisher, registry }) => {
        const now = BigInt(await time.latest());
        await publish(
          registry,
          publisher,
          await report(publisher, { observedAt: now - 100n })
        );
        return { maxObservationAge: 10 };
      },
    ],
    [
      "wrong publisher",
      async ({ otherPublisher, publisher, registry }) => {
        await publish(registry, publisher, await report(publisher));
        return { requiredPublisher: otherPublisher.address };
      },
    ],
    [
      "control-set mismatch",
      async ({ publisher, registry }) => {
        await publish(registry, publisher, await report(publisher));
        return { requiredControlSetRoot: OTHER_ROOT };
      },
    ],
  ];

  for (const [reason, arrange] of refusalCases) {
    it(`returns the distinct ${reason} reason`, async function () {
      const fixture = await loadFixture(deployRegistryFixture);
      const gateOptions = (await arrange(fixture)) || {};
      const gate = await deployGate(fixture.registry, gateOptions);

      expect(await gate.check(ASSET_KEY)).to.deep.equal([false, reason]);
      await expect(gate.demand(ASSET_KEY))
        .to.be.revertedWithCustomError(gate, "GateRefused")
        .withArgs(ASSET_KEY, reason);
    });
  }
});

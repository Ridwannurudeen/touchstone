const { expect } = require("chai");
const { ethers } = require("hardhat");
const { loadFixture, time } = require("@nomicfoundation/hardhat-network-helpers");

const ASSET_KEY = ethers.keccak256(
  ethers.toUtf8Bytes("eip155:1:0x43415eb6ff9db7e26a15b704e7a3edce97d31c4e")
);
const CONTROL_ROOT = ethers.keccak256(ethers.toUtf8Bytes("control-set-v0"));
const EVIDENCE_ROOT = ethers.keccak256(ethers.toUtf8Bytes("evidence-epoch-1"));
const REPORT_URI = "urn:touchstone:guarded-action:test";

describe("GuardedAction", function () {
  async function deployFixture() {
    const [owner, publisher, caller] = await ethers.getSigners();
    const chainId = (await ethers.provider.getNetwork()).chainId;
    const registry = await ethers.deployContract("TouchstoneRegistry", [chainId]);
    await registry.waitForDeployment();
    await registry.authorizePublisher(publisher.address);

    const now = BigInt(await time.latest());
    await registry.connect(publisher).publish(
      ASSET_KEY,
      CONTROL_ROOT,
      EVIDENCE_ROOT,
      ethers.keccak256(ethers.toUtf8Bytes("epoch:guarded-action")),
      0,
      now,
      now + 3_600n,
      1,
      REPORT_URI
    );

    const gate = await ethers.deployContract("AssetGate", [
      registry,
      1,
      3_600,
      publisher.address,
      CONTROL_ROOT,
    ]);
    await gate.waitForDeployment();
    const action = await ethers.deployContract("GuardedAction", [
      gate,
      ASSET_KEY,
    ]);
    await action.waitForDeployment();
    return { action, caller, gate, publisher, registry };
  }

  it("executes the only mutating action through the gate", async function () {
    const { action, caller } = await loadFixture(deployFixture);

    await expect(action.connect(caller).execute())
      .to.emit(action, "ActionExecuted")
      .withArgs(ASSET_KEY, caller.address, 1);
    expect(await action.actionCount()).to.equal(1);
    expect(action.interface.fragments.filter((item) => item.type === "function").map((item) => item.name)).to.not.include("perform");
  });

  it("cannot execute when the gate refuses the report", async function () {
    const { action, gate, registry, publisher } = await loadFixture(deployFixture);
    const now = BigInt(await time.latest());
    const refusedAsset = ethers.keccak256(ethers.toUtf8Bytes("refused-action"));
    await registry.connect(publisher).publish(
      refusedAsset,
      CONTROL_ROOT,
      EVIDENCE_ROOT,
      ethers.keccak256(ethers.toUtf8Bytes("epoch:refused-action")),
      3,
      now,
      now + 3_600n,
      1,
      REPORT_URI
    );
    const refusedAction = await ethers.deployContract("GuardedAction", [
      gate,
      refusedAsset,
    ]);
    await refusedAction.waitForDeployment();

    await expect(refusedAction.execute())
      .to.be.revertedWithCustomError(refusedAction, "ActionRefused")
      .withArgs(refusedAsset, "status not allowed");
    expect(await refusedAction.actionCount()).to.equal(0);
    expect(await action.actionCount()).to.equal(0);
  });

  it("rejects a zero asset key", async function () {
    const { gate } = await loadFixture(deployFixture);
    const Action = await ethers.getContractFactory("GuardedAction");

    await expect(Action.deploy(gate, ethers.ZeroHash))
      .to.be.revertedWithCustomError(Action, "InvalidAssetKey");
  });
});

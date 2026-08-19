import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { Interface } from "ethers";
import {
  ASSET_GATE_ABI,
  ASSET_GATE_V2_ABI,
  AssetGateClient,
  ERC8021_SUFFIX,
  GUARDED_ACTION_ABI,
  GuardedActionClient,
  POLICIES,
  REGISTRY_V2_ABI,
  RegistryV2Client,
  USTB_ASSET_KEY,
  appendBuilderCodeSuffix,
  indexPublished,
  policyDigestRoot,
  policyIdDigest,
  registryAssetKey,
  toBuilderCodeSuffix,
} from "@touchstone/sdk";

const ADDRESS = "0x1111111111111111111111111111111111111111";
const HASH = `0x${"11".repeat(32)}`;

function functionNames(abi) {
  return new Interface(abi).fragments
    .filter((fragment) => fragment.type === "function")
    .map((fragment) => fragment.name)
    .sort();
}

test("package self-reference resolves the emitted public entry point", () => {
  assert.equal(typeof AssetGateClient, "function");
  assert.equal(typeof GuardedActionClient, "function");
});

test("AssetGate ABI matches the deployed contract surface", () => {
  assert.deepEqual(functionNames(ASSET_GATE_ABI), [
    "allowedStatuses",
    "check",
    "demand",
    "maxObservationAge",
    "registry",
    "requiredControlSetRoot",
    "requiredPublisher",
  ]);
});

test("AssetGate v2 ABI adds immutable policy pins without changing v1", () => {
  assert.deepEqual(functionNames(ASSET_GATE_V2_ABI), [
    "allowedStatuses",
    "check",
    "demand",
    "expectedPolicyId",
    "expectedPolicyRoot",
    "maxObservationAge",
    "registry",
    "requiredControlSetRoot",
    "requiredPublisher",
  ]);
  assert.equal(functionNames(ASSET_GATE_ABI).includes("expectedPolicyId"), false);
});

test("GuardedAction ABI keeps execution on the guarded action", () => {
  assert.deepEqual(functionNames(GUARDED_ACTION_ABI), [
    "actionCount",
    "assetKey",
    "execute",
    "gate",
  ]);
});

test("AssetGateClient.check passes the configured asset key", async () => {
  const client = new AssetGateClient(ADDRESS, HASH, {});
  client.contract = {
    getFunction(name) {
      assert.equal(name, "check");
      return async (assetKey) => {
        assert.equal(assetKey, HASH);
        return [false, "status not allowed"];
      };
    },
  };

  assert.deepEqual(await client.check(), {
    allowed: false,
    reason: "status not allowed",
  });
});

test("AssetGateClient.demand refuses locally before sending", async () => {
  const client = new AssetGateClient(ADDRESS, HASH, {});
  client.contract = {
    getFunction(name) {
      assert.equal(name, "check");
      return async () => [false, "status not allowed"];
    },
  };

  await assert.rejects(
    client.demand(),
    new Error("AssetGate refused demand: status not allowed")
  );
});

test("AssetGateClient.demand calls demand with the configured asset key", async () => {
  const calls = [];
  const transaction = { hash: `0x${"22".repeat(32)}` };
  const client = new AssetGateClient(ADDRESS, HASH, {});
  client.contract = {
    getFunction(name) {
      return async (assetKey) => {
        calls.push([name, assetKey]);
        return name === "check" ? [true, "allowed"] : transaction;
      };
    },
  };

  assert.equal(await client.demand(), transaction);
  assert.deepEqual(calls, [
    ["check", HASH],
    ["demand", HASH],
  ]);
});

test("GuardedActionClient.execute calls GuardedAction without attribution", async () => {
  const transaction = { hash: `0x${"33".repeat(32)}` };
  const client = new GuardedActionClient(ADDRESS, {});
  client.contract = {
    getFunction(name) {
      assert.equal(name, "execute");
      return async () => transaction;
    },
  };

  assert.equal(await client.execute(), transaction);
});

test("GuardedActionClient.execute appends ERC-8021 attribution", async () => {
  let request;
  const transaction = { hash: `0x${"44".repeat(32)}` };
  const signer = {
    async sendTransaction(value) {
      request = value;
      return transaction;
    },
  };
  const client = new GuardedActionClient(ADDRESS, signer);
  const result = await client.execute(["touchstone"]);
  const executeData = new Interface(GUARDED_ACTION_ABI).encodeFunctionData("execute");

  assert.equal(result, transaction);
  assert.deepEqual(request, {
    to: ADDRESS,
    data: appendBuilderCodeSuffix(executeData, ["touchstone"]),
  });
});

test("RegistryV2Client preserves integer return values as bigint", async () => {
  const client = new RegistryV2Client(ADDRESS, {});
  client.contract = {
    getFunction(name) {
      assert.equal(name, "getLatestReport");
      return async (assetKey) => {
        assert.equal(assetKey, HASH);
        return [
          HASH,
          HASH,
          HASH,
          HASH,
          HASH,
          HASH,
          HASH,
          3n,
          100n,
          200n,
          ADDRESS,
          2n,
          HASH,
          "ipfs://report",
        ];
      };
    },
  };

  const report = await client.latestReport(HASH);
  assert.equal(report.status, 3n);
  assert.equal(report.sequence, 2n);
  assert.equal(report.approvalDigest, HASH);
});

test("RegistryV2Client returns null for an unknown asset", async () => {
  const client = new RegistryV2Client(ADDRESS, {});
  client.contract = {
    getFunction() {
      return async () => [
        HASH, HASH, HASH, HASH, HASH, HASH, HASH, 0n, 0n, 0n, ADDRESS, 0n, HASH, "",
      ];
    },
  };

  assert.equal(await client.latestReport(HASH), null);
});

test("Registry v2 indexer returns publications and corrections in log order", async () => {
  const iface = new Interface(REGISTRY_V2_ABI);
  const published = iface.encodeEventLog(iface.getEvent("Published"), [
    HASH, 1n, ADDRESS, HASH, HASH, HASH, HASH,
  ]);
  const corrected = iface.encodeEventLog(iface.getEvent("Corrected"), [
    HASH, 2n, 1n, ADDRESS, HASH, HASH, HASH, HASH,
  ]);
  const provider = {
    async getLogs(request) {
      assert.equal(request.topics[0].length, 2);
      return [
        { ...corrected, blockNumber: 3, transactionIndex: 0, index: 2 },
        { ...published, blockNumber: 2, transactionIndex: 1, index: 4 },
      ];
    },
  };

  const events = await indexPublished(provider, ADDRESS, 0);
  assert.deepEqual(events.map((event) => event.kind), ["published", "corrected"]);
  assert.equal(events[0].correctedSequence, null);
  assert.equal(events[0].approvalDigest, HASH);
  assert.equal(events[1].correctedSequence, 1n);
});

test("ERC-8021 schema-0 output matches the canonical reference vector", () => {
  assert.equal(ERC8021_SUFFIX, "0x80218021802180218021802180218021");
  assert.equal(
    toBuilderCodeSuffix(["baseapp", "morpho"]),
    "0x626173656170702c6d6f7270686f0e0080218021802180218021802180218021"
  );
});

test("fixture contains the exact committed policy key and contract refusal reason", async () => {
  const fixture = JSON.parse(
    await readFile(new URL("../fixtures/gate-check.json", import.meta.url), "utf8")
  );
  assert.equal(fixture.asset, USTB_ASSET_KEY);
  assert.equal(fixture.policy_key, POLICIES.disclosureFreshness.registryKey);
  assert.equal(fixture.allowed, false);
  assert.equal(fixture.reason, "status not allowed");
});

test("Registry v2 policy fields match the shared cross-language vector", async () => {
  const vector = JSON.parse(
    await readFile(
      new URL("../fixtures/registry-v2-policy-vector.json", import.meta.url),
      "utf8"
    )
  );
  assert.equal(registryAssetKey(vector.reportAssetKey), vector.assetKey);
  assert.equal(
    policyIdDigest(vector.policyId, vector.policyVersion),
    vector.policyIdHash
  );
  assert.equal(policyDigestRoot(vector.policyDigest), vector.policyRoot);
});

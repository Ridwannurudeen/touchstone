import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { Interface } from "ethers";
import {
  ASSET_GATE_ABI,
  ASSET_GATE_V2_ABI,
  AssetGateClient,
  DEPLOYMENTS,
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

test("RegistryV2Client decodes the struct the live registry actually returns", async () => {
  const fixture = JSON.parse(
    await readFile(
      new URL("../fixtures/registry-v2-latest-report-mainnet.json", import.meta.url),
      "utf8"
    )
  );
  const calls = [];
  const runner = {
    async call(transaction) {
      calls.push(transaction);
      return fixture.returnData;
    },
  };
  const client = new RegistryV2Client(fixture.registry, runner);

  const report = await client.latestReport(fixture.assetKey);

  assert.equal(calls.length, 1);
  assert.equal(calls[0].data, fixture.calldata);
  assert.equal(report.sequence, BigInt(fixture.expected.sequence));
  assert.equal(report.publisher, fixture.expected.publisher);
  assert.equal(report.observedAt, BigInt(fixture.expected.observedAt));
  assert.equal(report.validUntil, BigInt(fixture.expected.validUntil));
  assert.equal(report.reportURI, fixture.expected.reportURI);
  assert.equal(report.status, BigInt(fixture.expected.status));
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
    async getBlock(tag) {
      assert.equal(tag, "latest");
      return { number: 3 };
    },
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

test("Registry v2 indexer splits the block range into windows the public RPC accepts", async () => {
  const windows = [];
  const provider = {
    async getBlock() {
      throw new Error("a numeric toBlock must not be resolved through the provider");
    },
    async getLogs(request) {
      windows.push([request.fromBlock, request.toBlock]);
      return [];
    },
  };

  assert.deepEqual(await indexPublished(provider, ADDRESS, 1000, 1249), []);
  assert.deepEqual(windows, [
    [1000, 1099],
    [1100, 1199],
    [1200, 1249],
  ]);

  windows.length = 0;
  await indexPublished(provider, ADDRESS, 5, 5, { blockRange: 2000 });
  assert.deepEqual(windows, [[5, 5]]);

  await assert.rejects(
    indexPublished(provider, ADDRESS, 0, 1, { blockRange: 0 }),
    /blockRange must be a positive integer/
  );
});

test("DEPLOYMENTS match the committed v2 deployment manifests", async () => {
  for (const [name, file] of [
    ["xlayerMainnet", "xlayer-mainnet-v2.json"],
    ["xlayerTestnet", "xlayer-testnet-v2.json"],
  ]) {
    const manifest = JSON.parse(
      await readFile(new URL(`../../deployments/${file}`, import.meta.url), "utf8")
    );
    const deployment = DEPLOYMENTS[name];
    assert.equal(deployment.chainId, manifest.chain_id);
    assert.equal(deployment.v2RegistryAddress, manifest.registry_address);
    assert.equal(deployment.legacyRegistryAddress, manifest.legacy_registry_address);
    assert.equal(deployment.v2RegistryDeploymentBlock, manifest.deployment_block);
  }
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

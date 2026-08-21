import assert from "node:assert/strict";
import { webcrypto } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import vm from "node:vm";

const root = path.resolve(import.meta.dirname, "../..");

class FakeNode {
  constructor(tag = "div") {
    this.tag = tag;
    this.children = [];
    this.className = "";
    this.textContent = "";
  }

  append(...children) {
    this.children.push(...children);
  }

  appendChild(child) {
    this.children.push(child);
  }

  replaceChildren(...children) {
    this.children = children;
  }

  setAttribute() {}
}

function textOf(value) {
  if (typeof value === "string") return value;
  return `${value.textContent}${value.children.map(textOf).join("")}`;
}

function loadTerminal(config = { networks: {} }) {
  const elements = new Map([
    ["terminal-config", { textContent: JSON.stringify(config) }],
  ]);
  const context = vm.createContext({
    Array,
    BigInt,
    Date,
    Error,
    JSON,
    Map,
    Node: FakeNode,
    Number,
    Object,
    Promise,
    RegExp,
    Set,
    String,
    TextEncoder,
    URL,
    Uint8Array,
    console,
    crypto: webcrypto,
    document: {
      createElement: (tag) => new FakeNode(tag),
      getElementById: (id) => {
        if (!elements.has(id)) elements.set(id, new FakeNode());
        return elements.get(id);
      },
    },
    ethers: {
      Interface: class {
        encodeFunctionData() {
          return "0x1234";
        }
      },
    },
    window: {},
  });
  const source = fs
    .readFileSync(path.join(root, "site2/assets/app.js"), "utf8")
    .split("/* ---------- wiring ---------- */")[0];
  vm.runInContext(
    `${source}\n` +
      `globalThis.__terminalTest = {` +
      `canonicalJson, controlContentHashes, controlSetRootFromBundle, ` +
      `evidenceRootFromBundle, simulate, ` +
      `setRpc(value) { rpc = value; }, ` +
      `setVerifyActionBinding(value) { verifyActionBinding = value; }` +
      `};`,
    context,
  );
  return { api: context.__terminalTest, elements };
}

test("browser recomputes every retained bundle's control and evidence roots", async () => {
  const { api } = loadTerminal();
  const directory = path.join(root, "site2/data");
  const bundles = fs
    .readdirSync(directory)
    .filter((name) => name.endsWith(".json") && name !== "stats.json");
  assert.equal(bundles.length, 15);

  for (const name of bundles) {
    const bundle = JSON.parse(
      fs.readFileSync(path.join(directory, name), "utf8"),
    );
    const report = bundle.signed_report.report;
    const hashes = await api.controlContentHashes(bundle.control_records);
    assert.deepEqual(
      Object.fromEntries(
        report.controls.map((item) => [item.control_id, item.content_hash]),
      ),
      Object.fromEntries(hashes),
      `${name} control hashes`,
    );
    assert.equal(
      await api.controlSetRootFromBundle(bundle.control_records),
      report.control_set_root,
      `${name} control-set root`,
    );
    assert.equal(
      await api.evidenceRootFromBundle(bundle.evidence_digests),
      report.evidence_root,
      `${name} evidence root`,
    );
  }
});

test("simulation refuses to render a result after the selected action changes", async () => {
  const config = {
    networks: {
      mainnet: {
        name: "mainnet",
        actions: {
          first: {
            label: "first",
            address: "0x1111111111111111111111111111111111111111",
            gate: "0x2222222222222222222222222222222222222222",
            key: `0x${"11".repeat(32)}`,
            kind: "guarded",
          },
          second: {
            label: "second",
            address: "0x3333333333333333333333333333333333333333",
            gate: "0x4444444444444444444444444444444444444444",
            key: `0x${"22".repeat(32)}`,
            kind: "guarded",
          },
        },
      },
    },
  };
  const { api, elements } = loadTerminal(config);
  elements.set("net-select", { value: "mainnet" });
  elements.set("action-select", { value: "first" });
  elements.set("action-panel", new FakeNode());
  api.setVerifyActionBinding(async () => new FakeNode());
  let release;
  api.setRpc(
    () =>
      new Promise((resolve) => {
        release = resolve;
      }),
  );

  const pending = api.simulate();
  while (!release) await new Promise((resolve) => setImmediate(resolve));
  elements.get("action-select").value = "second";
  release({ endpoint: "rpc.example" });
  await pending;

  const rendered = textOf(elements.get("action-panel"));
  assert.match(rendered, /aborted/);
  assert.doesNotMatch(rendered, /would succeed/);
});

test("simulation discards a binding failure after the selected action changes", async () => {
  const config = {
    networks: {
      mainnet: {
        name: "mainnet",
        actions: {
          first: {
            label: "first",
            address: "0x1111111111111111111111111111111111111111",
            gate: "0x2222222222222222222222222222222222222222",
            key: `0x${"11".repeat(32)}`,
            kind: "guarded",
          },
          second: {
            label: "second",
            address: "0x3333333333333333333333333333333333333333",
            gate: "0x4444444444444444444444444444444444444444",
            key: `0x${"22".repeat(32)}`,
            kind: "guarded",
          },
        },
      },
    },
  };
  const { api, elements } = loadTerminal(config);
  elements.set("net-select", { value: "mainnet" });
  elements.set("action-select", { value: "first" });
  elements.set("action-panel", new FakeNode());
  let rejectBinding;
  api.setVerifyActionBinding(
    () =>
      new Promise((resolve, reject) => {
        rejectBinding = reject;
      }),
  );

  const pending = api.simulate();
  while (!rejectBinding) await new Promise((resolve) => setImmediate(resolve));
  elements.get("action-select").value = "second";
  rejectBinding(new Error("old binding failed"));
  await pending;

  const rendered = textOf(elements.get("action-panel"));
  assert.match(rendered, /aborted/);
  assert.doesNotMatch(rendered, /old binding failed/);
});

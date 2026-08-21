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

function loadTerminal(config = { networks: {} }, cryptoImpl = webcrypto) {
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
    crypto: cryptoImpl,
    document: {
      createElement: (tag) => new FakeNode(tag),
      getElementById: (id) => {
        if (!elements.has(id)) elements.set(id, new FakeNode());
        return elements.get(id);
      },
    },
    ethers: {
      concat: (values) =>
        Uint8Array.from(values.flatMap((value) => [...value])),
      getBytes: (value) => Uint8Array.from(Buffer.from(value.slice(2), "hex")),
      hexlify: (value) => `0x${Buffer.from(value).toString("hex")}`,
      Interface: class {
        encodeFunctionData() {
          return "0x1234";
        }
      },
      toUtf8Bytes: (value) => new TextEncoder().encode(value),
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
      `evidenceRootFromBundle, simulate, verifyBundle, withBuilderCode, ` +
      `setRpc(value) { rpc = value; }, ` +
      `setVerifyActionBinding(value) { verifyActionBinding = value; }` +
      `};`,
    context,
  );
  return { api: context.__terminalTest, elements };
}

function retainedBundle() {
  const directory = path.join(root, "site2/data");
  const name = fs
    .readdirSync(directory)
    .find((entry) => entry.endsWith(".json") && entry !== "stats.json");
  return JSON.parse(fs.readFileSync(path.join(directory, name), "utf8"));
}

function bundleFile(bundle, overrides = {}) {
  const contents = JSON.stringify(bundle);
  return {
    name: "bundle.json",
    size: Buffer.byteLength(contents),
    async text() {
      return contents;
    },
    ...overrides,
  };
}

test("browser refuses a bundle whose report trust anchor is missing", async () => {
  for (const missing of ["published_key", "signature"]) {
    const { api, elements } = loadTerminal();
    const bundle = retainedBundle();
    if (missing === "published_key") delete bundle.published_key;
    else delete bundle.signed_report.signature;
    elements.set("verify-panel", new FakeNode());

    await api.verifyBundle(bundleFile(bundle));

    const rendered = textOf(elements.get("verify-panel"));
    assert.match(rendered, /report signature/i, missing);
    assert.match(rendered, /missing/i, missing);
    assert.doesNotMatch(rendered, /canonical\/report agreement/i, missing);
  }
});

test("browser refuses malformed report key and signature hex", async () => {
  const { api, elements } = loadTerminal();
  const bundle = retainedBundle();
  bundle.published_key.public_key = "zz";
  bundle.signed_report.signature = "01";
  elements.set("verify-panel", new FakeNode());

  await api.verifyBundle(bundleFile(bundle));

  const rendered = textOf(elements.get("verify-panel"));
  assert.match(rendered, /report signature/i);
  assert.match(rendered, /malformed/i);
  assert.doesNotMatch(rendered, /canonical\/report agreement/i);
});

test("browser refuses to continue when Ed25519 is unsupported", async () => {
  const unsupportedCrypto = {
    subtle: {
      digest: (...args) => webcrypto.subtle.digest(...args),
      importKey: async () => {
        throw new Error("Ed25519 unsupported");
      },
    },
  };
  const { api, elements } = loadTerminal(
    { networks: {} },
    unsupportedCrypto,
  );
  elements.set("verify-panel", new FakeNode());

  await api.verifyBundle(bundleFile(retainedBundle()));

  const rendered = textOf(elements.get("verify-panel"));
  assert.match(rendered, /cannot verify Ed25519/i);
  assert.doesNotMatch(rendered, /canonical\/report agreement/i);
});

test("browser stops after a well-formed but incorrect report signature", async () => {
  const { api, elements } = loadTerminal();
  const bundle = retainedBundle();
  const first = bundle.signed_report.signature.startsWith("00") ? "01" : "00";
  bundle.signed_report.signature =
    first + bundle.signed_report.signature.slice(2);
  elements.set("verify-panel", new FakeNode());

  await api.verifyBundle(bundleFile(bundle));

  const rendered = textOf(elements.get("verify-panel"));
  assert.match(rendered, /signature does NOT verify/i);
  assert.doesNotMatch(rendered, /canonical\/report agreement/i);
});

test("browser verifies a retained signature before continuing", async () => {
  const { api, elements } = loadTerminal();
  elements.set("verify-panel", new FakeNode());

  await api.verifyBundle(bundleFile(retainedBundle()));

  const rendered = textOf(elements.get("verify-panel"));
  assert.match(rendered, /Ed25519 verifies under key/i);
  assert.match(rendered, /canonical\/report agreement/i);
});

test("browser rejects an oversized bundle before reading it", async () => {
  const { api, elements } = loadTerminal();
  let read = false;
  elements.set("verify-panel", new FakeNode());
  const file = bundleFile(retainedBundle(), {
    size: 16_777_217,
    async text() {
      read = true;
      return "{}";
    },
  });

  await api.verifyBundle(file);

  assert.equal(read, false);
  assert.match(textOf(elements.get("verify-panel")), /exceeds 16777216 bytes/i);
});

test("browser recomputes every retained bundle's control and evidence roots", async () => {
  const { api } = loadTerminal();
  const directory = path.join(root, "site2/data");
  const bundles = fs
    .readdirSync(directory)
    .filter((name) => name.endsWith(".json") && name !== "stats.json");
  const facts = JSON.parse(
    fs.readFileSync(path.join(root, "site2/_data/facts.json"), "utf8"),
  );
  assert.equal(bundles.length, Number(facts.counts.bundles_downloadable));

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

test("configured Builder Code produces the exact ERC-8021 suffix", () => {
  const page = fs.readFileSync(path.join(root, "site2/_pages/app.html"), "utf8");
  const configText = page.match(
    /<script type="application\/json" id="terminal-config">\s*([\s\S]*?)\s*<\/script>/,
  )?.[1];
  assert.ok(configText, "terminal config is present");
  const config = JSON.parse(configText);
  assert.equal(config.builderCode, "f0axgs7smtk2nfa7");

  const { api } = loadTerminal(config);
  assert.equal(
    api.withBuilderCode("0x1234"),
    "0x123466306178677337736d746b326e666137100080218021802180218021802180218021",
  );
});

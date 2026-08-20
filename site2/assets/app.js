/* Touchstone Policy Terminal.
 *
 * The one scripted page on this site. Everything it shows is read live from X Layer at the
 * moment you ask — nothing here is prerendered, and every panel names the block and endpoint
 * it read. It talks only to the RPC endpoints listed in its config (all OKX/X Layer hosts)
 * and to a browser wallet if you connect one; it uploads nothing.
 *
 * Honesty rules, in code: a failed read renders as the error it was, never as a cached
 * value; a refusal renders with the exact on-chain reason string; an expired report is
 * labelled expired rather than hidden; and the verify panel lists what it checked AND what
 * it did not, because a checkmark that silently skips a check is how verification lies.
 */
"use strict";

const CONFIG = JSON.parse(
  document.getElementById("terminal-config").textContent,
);

const V1_REGISTRY_ABI = [
  "function latestSequence(bytes32 assetKey) view returns (uint64)",
  "function getLatestReport(bytes32 assetKey) view returns (tuple(bytes32 controlSetRoot, bytes32 evidenceRoot, bytes32 epochKey, uint8 status, uint64 observedAt, uint64 validUntil, address publisher, uint64 sequence, string reportURI))",
];
const V2_REGISTRY_ABI = [
  "function latestSequence(bytes32 assetKey) view returns (uint64)",
  "function getLatestReport(bytes32 assetKey) view returns (tuple(bytes32 reportDigest, bytes32 policyId, bytes32 policyRoot, bytes32 controlSetRoot, bytes32 evidenceRoot, bytes32 approvalDigest, bytes32 epochKey, uint8 status, uint64 observedAt, uint64 validUntil, address publisher, uint64 sequence, bytes32 parentDigest, string reportURI))",
];
const GATE_ABI = [
  "function check(bytes32 assetKey) view returns (bool allowed, string reason)",
];
const GUARDED_ABI = [
  "function actionCount() view returns (uint256)",
  "function execute()",
  "event ActionExecuted(bytes32 indexed assetKey, address indexed caller, uint256 actionNumber)",
];
const ADMISSION_ABI = [
  "function isActive(bytes32 assetKey) view returns (bool active, string reason)",
  "function execute(bytes32 assetKey)",
  "function activate(bytes32 assetKey)",
  "function useCount() view returns (uint256)",
];
const STATUS_NAMES = ["CONFIRMED", "STALE", "INCONSISTENT", "UNVERIFIABLE"];
// The exact struct approval.py signs: snake_case field names, uint256 timestamp, and a
// domain version that matches the artifact's own `version` field (2 = scoped, 1 = legacy).
function approvalTypes(version) {
  const fields = [
    { name: "control_digest", type: "bytes32" },
    { name: "compilation_digest", type: "bytes32" },
    { name: "decision", type: "string" },
    { name: "reason_code", type: "string" },
    { name: "timestamp", type: "uint256" },
  ];
  if (version === 2) {
    fields.push(
      { name: "scope", type: "string" },
      { name: "policyId", type: "string" },
    );
  }
  return { Approval: fields };
}

const $ = (id) => document.getElementById(id);
const short = (h) =>
  h && h.length > 18 ? `${h.slice(0, 10)}…${h.slice(-6)}` : h;
const utc = (secs) =>
  new Date(Number(secs) * 1000).toISOString().replace(".000Z", "Z");

function network() {
  return CONFIG.networks[$("net-select").value];
}
function policyKey() {
  return network().keys[$("key-select").value];
}

/* ---------- RPC: try each endpoint in order, name the one that answered ---------- */

async function rpc(net, method, params) {
  let lastError = null;
  for (const url of net.rpc) {
    try {
      const response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ jsonrpc: "2.0", id: 1, method, params }),
      });
      const body = await response.json();
      if (body.error)
        throw new Error(body.error.message || JSON.stringify(body.error));
      return { result: body.result, endpoint: new URL(url).host };
    } catch (error) {
      lastError = error;
    }
  }
  throw new Error(`every RPC endpoint refused: ${lastError}`);
}

async function ethCall(net, to, iface, fn, args, blockTag) {
  const data = iface.encodeFunctionData(fn, args);
  const { result, endpoint } = await rpc(net, "eth_call", [
    { to, data },
    blockTag || "latest",
  ]);
  return { decoded: iface.decodeFunctionResult(fn, result), endpoint };
}

/* One header, read once, pins a whole panel: every read in the panel names this block,
 * and "expired" is judged against this block's own timestamp rather than the visitor's
 * clock — a wrong laptop clock must not relabel a live report as dead, or vice versa. */
async function pinnedHead(net) {
  const { result, endpoint } = await rpc(net, "eth_getBlockByNumber", [
    "latest",
    false,
  ]);
  return {
    hex: result.number,
    number: parseInt(result.number, 16),
    timestamp: parseInt(result.timestamp, 16),
    endpoint,
  };
}

/* ---------- live report panel ---------- */

function row(k, v, cls) {
  return `<div class="term-row"><span class="tr-k">${k}</span><span class="tr-v ${cls || ""}">${v}</span></div>`;
}

function statusClass(name) {
  return name === "CONFIRMED"
    ? "term-ok"
    : name === "UNVERIFIABLE"
      ? "term-pending"
      : "term-blocked";
}

async function readReport() {
  const net = network();
  const key = policyKey();
  const panel = $("report-panel");
  panel.innerHTML = row(
    "reading",
    `${net.name} · ${key.label}`,
    "term-pending",
  );
  const iface = new ethers.Interface(
    key.registry === "v2" ? V2_REGISTRY_ABI : V1_REGISTRY_ABI,
  );
  const registry = key.registry === "v2" ? net.registryV2 : net.registryV1;
  try {
    const head = await pinnedHead(net);
    const seq = await ethCall(
      net,
      registry,
      iface,
      "latestSequence",
      [key.key],
      head.hex,
    );
    if (Number(seq.decoded[0]) === 0) {
      panel.innerHTML =
        row("registry", `${key.registry} · ${short(registry)}`) +
        row("key", short(key.key)) +
        row(
          "latest sequence",
          "0 — no report has ever been published under this key",
          "term-pending",
        ) +
        row("read at block", `${head.number} via ${seq.endpoint}`);
      return;
    }
    const { decoded, endpoint } = await ethCall(
      net,
      registry,
      iface,
      "getLatestReport",
      [key.key],
      head.hex,
    );
    const r = decoded[0];
    const statusName = STATUS_NAMES[Number(r.status)] || `status ${r.status}`;
    // Judged against the pinned block's own timestamp — the clock the gate contracts
    // consult — never the visitor's machine, whose clock is nobody's evidence.
    const expired = head.timestamp > Number(r.validUntil);
    const lines = [
      row("registry", `${key.registry} · ${short(registry)}`),
      row("key", short(key.key)),
      row("sequence", String(r.sequence)),
      row("status", statusName, statusClass(statusName)),
      row("observed at", utc(r.observedAt)),
      row(
        "valid until",
        `${utc(r.validUntil)}${expired ? " — EXPIRED: a report is a statement about a day, and this day has ended. The next publication window re-evaluates from fresh evidence." : ""}`,
        expired ? "term-blocked" : "term-ok",
      ),
      row(
        "publisher",
        `<a href="${net.explorer}/address/${r.publisher}" rel="noopener">${short(r.publisher)}</a>`,
      ),
      row("control-set root", short(r.controlSetRoot)),
      row("evidence root", short(r.evidenceRoot)),
    ];
    if (key.registry === "v2") {
      lines.push(row("policy id", short(r.policyId)));
      lines.push(row("policy root", short(r.policyRoot)));
      lines.push(row("approval digest", short(r.approvalDigest)));
      lines.push(row("report digest", short(r.reportDigest)));
    }
    lines.push(row("report URI", r.reportURI));
    if (key.bundle) {
      lines.push(
        row(
          "bundle",
          `<a href="${key.bundle}" download>download the signed bundle</a>`,
        ),
      );
    }
    lines.push(
      row(
        "read at block",
        `${head.number} (chain time ${utc(head.timestamp)}) via ${endpoint}`,
      ),
    );
    panel.innerHTML = lines.join("");
  } catch (error) {
    panel.innerHTML = row(
      "read failed",
      String(error.message || error),
      "term-blocked",
    );
  }
}

/* ---------- gate panel ---------- */

async function readGate() {
  const net = network();
  const key = policyKey();
  const panel = $("gate-panel");
  if (!key.gate) {
    panel.innerHTML = row(
      "gate",
      "no deployed gate pins this key — policy keys are what gates pin; the asset-wide verdict is a registry fact, unpinnable by design",
      "term-pending",
    );
    return;
  }
  panel.innerHTML = row("checking", short(key.gate), "term-pending");
  try {
    const iface = new ethers.Interface(GATE_ABI);
    const head = await pinnedHead(net);
    const { decoded, endpoint } = await ethCall(
      net,
      key.gate,
      iface,
      "check",
      [key.key],
      head.hex,
    );
    const [allowed, reason] = decoded;
    panel.innerHTML =
      row(
        "gate",
        `<a href="${net.explorer}/address/${key.gate}" rel="noopener">${short(key.gate)}</a>`,
      ) +
      row(
        "check(key)",
        allowed ? "true" : "false",
        allowed ? "term-ok" : "term-blocked",
      ) +
      row("reason", `"${reason}"`, allowed ? "term-ok" : "term-blocked") +
      row("read at block", `${head.number} via ${endpoint}`) +
      (allowed
        ? ""
        : row(
            "meaning",
            "the refusal is the product working: no control was weakened to make this pass",
            "term-pending",
          ));
  } catch (error) {
    panel.innerHTML = row(
      "check failed",
      String(error.message || error),
      "term-blocked",
    );
  }
}

/* ---------- wallet + guarded action ---------- */

let provider = null;
let account = null;

function walletTransport() {
  return window.okxwallet ?? window.ethereum ?? null;
}

async function connect() {
  const transport = walletTransport();
  if (!transport) {
    $("wallet-state").innerHTML = row(
      "wallet",
      "no wallet detected — install OKX Wallet or any EIP-1193 wallet; every read on this page works without one",
      "term-pending",
    );
    return;
  }
  try {
    const accounts = await transport.request({ method: "eth_requestAccounts" });
    account = ethers.getAddress(accounts[0]);
    provider = new ethers.BrowserProvider(transport);
    $("wallet-state").innerHTML = row("connected", short(account), "term-ok");
    $("btn-execute").disabled = false;
    $("btn-decisions").disabled = false;
  } catch (error) {
    $("wallet-state").innerHTML = row(
      "connect failed",
      String(error.message || error),
      "term-blocked",
    );
  }
}

async function ensureChain(net) {
  const transport = walletTransport();
  const wanted = "0x" + Number(net.chainId).toString(16);
  const current = await transport.request({ method: "eth_chainId" });
  if (current === wanted) return;
  try {
    await transport.request({
      method: "wallet_switchEthereumChain",
      params: [{ chainId: wanted }],
    });
  } catch (error) {
    if (error && error.code === 4902) {
      await transport.request({
        method: "wallet_addEthereumChain",
        params: [
          {
            chainId: wanted,
            chainName: net.name,
            nativeCurrency: { name: "OKB", symbol: "OKB", decimals: 18 },
            rpcUrls: net.rpc,
            blockExplorerUrls: [net.explorer],
          },
        ],
      });
    } else {
      throw error;
    }
  }
}

function actionTarget() {
  const net = network();
  const choice = $("action-select").value;
  return net.actions[choice];
}

/* ---------- ERC-8021 Builder Code attribution ----------
 *
 * Inert until the owner registers a real code and sets `builderCode` in the page config —
 * while it is null every transaction goes out as ordinary, unattributed calldata. When set,
 * the schema-0 suffix (payload ‖ length ‖ 0x00 ‖ the 0x8021… marker) is appended to the
 * calldata; contracts ignore bytes past their arguments, so behaviour is unchanged. The
 * byte layout is the one sdk/src/attribution.ts implements and the SDK suite pins against
 * the canonical reference vector — change either only with the other. Never invent a code:
 * an unregistered value attributes to nobody.
 */
const ERC8021_MARKER = "0x80218021802180218021802180218021";

function withBuilderCode(data) {
  const code = CONFIG.builderCode;
  if (code == null || code === "") return data;
  if (!/^[\x20-\x7e]+$/.test(code) || code.includes(",")) {
    throw new Error("builderCode must be printable ASCII without commas");
  }
  const encoded = ethers.toUtf8Bytes(code);
  if (encoded.length > 255) {
    throw new Error("builderCode must fit one length byte");
  }
  return ethers.hexlify(
    ethers.concat([
      ethers.getBytes(data),
      encoded,
      Uint8Array.of(encoded.length),
      Uint8Array.of(0),
      ethers.getBytes(ERC8021_MARKER),
    ]),
  );
}

async function simulate() {
  const net = network();
  const target = actionTarget();
  const panel = $("action-panel");
  panel.innerHTML = row(
    "simulating",
    `${target.label} · ${short(target.address)}`,
    "term-pending",
  );
  try {
    const iface = new ethers.Interface(
      target.kind === "admission" ? ADMISSION_ABI : GUARDED_ABI,
    );
    // The simulation must exercise the exact calldata a real send would carry,
    // attribution suffix included — simulating different bytes proves nothing.
    const data = withBuilderCode(
      target.kind === "admission"
        ? iface.encodeFunctionData("execute", [target.key])
        : iface.encodeFunctionData("execute", []),
    );
    await rpc(net, "eth_call", [{ to: target.address, data }, "latest"]).then(
      ({ endpoint }) => {
        panel.innerHTML =
          row(
            "simulation",
            "would succeed — the gate currently permits this action",
            "term-ok",
          ) + row("via", endpoint);
      },
      (error) => {
        panel.innerHTML =
          row(
            "simulation",
            "would revert — the gate refuses this action right now",
            "term-blocked",
          ) +
          row(
            "revert detail",
            String(error.message || error).slice(0, 220),
            "term-blocked",
          ) +
          row(
            "meaning",
            "nothing was sent; this is the contract's answer, not the site's",
            "term-pending",
          );
      },
    );
  } catch (error) {
    panel.innerHTML = row(
      "simulate failed",
      String(error.message || error),
      "term-blocked",
    );
  }
}

async function execute() {
  const net = network();
  const target = actionTarget();
  const panel = $("action-panel");
  if (!provider) return;
  try {
    await ensureChain(net);
    provider = new ethers.BrowserProvider(walletTransport());
    const signer = await provider.getSigner();
    const iface = new ethers.Interface(
      target.kind === "admission" ? ADMISSION_ABI : GUARDED_ABI,
    );
    const data = withBuilderCode(
      target.kind === "admission"
        ? iface.encodeFunctionData("execute", [target.key])
        : iface.encodeFunctionData("execute", []),
    );
    panel.innerHTML = row(
      "awaiting wallet",
      "confirm or reject in your wallet",
      "term-pending",
    );
    const tx = await signer.sendTransaction({ to: target.address, data });
    panel.innerHTML = row(
      "sent",
      `<a href="${net.explorer}/tx/${tx.hash}" rel="noopener">${short(tx.hash)}</a>`,
      "term-pending",
    );
    const receipt = await tx.wait(1);
    panel.innerHTML =
      row(
        "transaction",
        `<a href="${net.explorer}/tx/${tx.hash}" rel="noopener">${short(tx.hash)}</a>`,
      ) +
      row(
        "status",
        receipt.status === 1 ? "1 — executed" : "0 — reverted on chain",
        receipt.status === 1 ? "term-ok" : "term-blocked",
      ) +
      row("block", String(receipt.blockNumber));
  } catch (error) {
    panel.innerHTML = row(
      "execute",
      String(error.shortMessage || error.message || error).slice(0, 300),
      "term-blocked",
    );
  }
}

async function myDecisions() {
  const net = network();
  const panel = $("decisions-panel");
  if (!account) return;
  panel.innerHTML = row(
    "scanning",
    "recent blocks for your wallet's guarded executions",
    "term-pending",
  );
  try {
    const head = parseInt((await rpc(net, "eth_blockNumber", [])).result, 16);
    const iface = new ethers.Interface(GUARDED_ABI);
    const topic = iface.getEvent("ActionExecuted").topicHash;
    const caller = ethers.zeroPadValue(account, 32);
    const found = [];
    const addresses = Object.values(net.actions).map((a) => a.address);
    for (let from = Math.max(1, head - 4500); from <= head; from += 900) {
      const to = Math.min(from + 899, head);
      const { result } = await rpc(net, "eth_getLogs", [
        {
          fromBlock: "0x" + from.toString(16),
          toBlock: "0x" + to.toString(16),
          topics: [topic, null, caller],
        },
      ]);
      for (const log of result) {
        if (
          addresses.some((a) => a.toLowerCase() === log.address.toLowerCase())
        )
          found.push(log);
      }
    }
    panel.innerHTML = found.length
      ? found
          .map((log) =>
            row(
              `block ${parseInt(log.blockNumber, 16)}`,
              `<a href="${net.explorer}/tx/${log.transactionHash}" rel="noopener">${short(log.transactionHash)}</a>`,
              "term-ok",
            ),
          )
          .join("")
      : row(
          "result",
          `no guarded executions by ${short(account)} in the last ~4,500 blocks (older history is on the explorer)`,
          "term-pending",
        );
  } catch (error) {
    panel.innerHTML = row(
      "scan failed",
      String(error.message || error),
      "term-blocked",
    );
  }
}

/* ---------- local bundle verification ---------- */

async function sha256hex(bytes) {
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (b) =>
    b.toString(16).padStart(2, "0"),
  ).join("");
}

function verdict(name, ok, detail) {
  const mark = ok === null ? "·" : ok ? "✓" : "✗";
  const cls = ok === null ? "term-pending" : ok ? "term-ok" : "term-blocked";
  return row(`${mark} ${name}`, detail, cls);
}

async function verifyBundle(file) {
  const panel = $("verify-panel");
  const lines = [];
  try {
    const text = await file.text();
    const bundle = JSON.parse(text);
    const report = bundle.signed_report?.report;
    if (!report || !bundle.report_canonical) {
      panel.innerHTML = verdict(
        "bundle shape",
        false,
        "this file does not carry signed_report and report_canonical",
      );
      return;
    }
    lines.push(
      row("bundle", `${file.name} · ${bundle.version || "unversioned"}`),
    );

    // 1. The Ed25519 signature over the exact canonical bytes the bundle carries.
    const canonicalBytes = new TextEncoder().encode(bundle.report_canonical);
    const publicKeyHex = bundle.published_key?.public_key;
    let signatureChecked = null;
    if (publicKeyHex && bundle.signed_report.signature) {
      try {
        const key = await crypto.subtle.importKey(
          "raw",
          Uint8Array.from(publicKeyHex.match(/../g), (h) => parseInt(h, 16)),
          { name: "Ed25519" },
          false,
          ["verify"],
        );
        signatureChecked = await crypto.subtle.verify(
          "Ed25519",
          key,
          Uint8Array.from(bundle.signed_report.signature.match(/../g), (h) =>
            parseInt(h, 16),
          ),
          canonicalBytes,
        );
      } catch (error) {
        lines.push(
          verdict(
            "report signature",
            null,
            `this browser cannot verify Ed25519 (${error.message}); use the CLI recipe on /verify`,
          ),
        );
      }
    }
    if (signatureChecked !== null) {
      lines.push(
        verdict(
          "report signature",
          signatureChecked,
          signatureChecked
            ? `Ed25519 verifies under key ${short(publicKeyHex)}`
            : "signature does NOT verify over report_canonical",
        ),
      );
    }

    // 2. The canonical text must be the report it claims to be (spot fields, not a re-serialisation).
    const canonicalParsed = JSON.parse(bundle.report_canonical);
    const fieldsAgree = [
      "asset_key",
      "sequence",
      "state",
      "control_set_root",
      "evidence_root",
      "approval_ledger_sha256",
    ].every(
      (k) => JSON.stringify(canonicalParsed[k]) === JSON.stringify(report[k]),
    );
    lines.push(
      verdict(
        "canonical/report agreement",
        fieldsAgree,
        fieldsAgree
          ? "identity fields agree between report_canonical and signed_report.report"
          : "report_canonical describes a DIFFERENT report",
      ),
    );

    // 3. The approval ledger hashes to the digest the signed report commits to.
    if (bundle.approval_ledger) {
      const ledgerDigest = await sha256hex(
        new TextEncoder().encode(bundle.approval_ledger),
      );
      const committed = report.approval_ledger_sha256;
      lines.push(
        verdict(
          "approval ledger digest",
          ledgerDigest === committed,
          `${short(ledgerDigest)} vs committed ${short(committed)}`,
        ),
      );
      const ledger = JSON.parse(bundle.approval_ledger);
      if (ledger.version === "touchstone.approval-ledger.v2") {
        let recovered = new Set();
        let signedEntries = 0;
        let bad = 0;
        for (const list of ["approved", "declined"]) {
          for (const entry of ledger[list] || []) {
            const a = entry.approval;
            if (!a) {
              bad += 1;
              continue;
            }
            signedEntries += 1;
            try {
              const message = {
                control_digest: "0x" + a.control_digest,
                compilation_digest: "0x" + a.compilation_digest,
                decision: a.decision,
                reason_code: a.reason_code,
                timestamp: a.timestamp,
              };
              if (a.version === 2) {
                message.scope = a.scope;
                message.policyId = a.policy_id;
              }
              const who = ethers.verifyTypedData(
                { name: "Touchstone Approval", version: String(a.version) },
                approvalTypes(a.version),
                message,
                "0x" + a.signature.replace(/^0x/, ""),
              );
              if (who.toLowerCase() !== a.approver.toLowerCase()) bad += 1;
              else recovered.add(who);
            } catch {
              bad += 1;
            }
          }
        }
        lines.push(
          verdict(
            "approver signatures (ledger v2)",
            bad === 0,
            bad === 0
              ? `${signedEntries} decisions recover ${[...recovered].map(short).join(", ")}`
              : `${bad} entries failed signature recovery — check with the CLI`,
          ),
        );
      } else {
        lines.push(
          verdict(
            "approver signatures",
            null,
            "version-1 ledger: decisions are recorded but unsigned, honestly so; reports after 2026-08-19 bind the signed version-2 ledger",
          ),
        );
      }
    }

    // 4. The policy manifest hashes to the digest the report commits to.
    if (report.policy && bundle.policy_manifest) {
      const manifestDigest = await sha256hex(
        new TextEncoder().encode(bundle.policy_manifest),
      );
      lines.push(
        verdict(
          "policy manifest digest",
          manifestDigest === report.policy.policy_digest,
          `${short(manifestDigest)} vs committed ${short(report.policy.policy_digest)}`,
        ),
      );
    }

    // 5. Every compilation artifact hashes to the name it is filed under.
    if (bundle.compilations) {
      let all = true;
      for (const [digest, artifact] of Object.entries(bundle.compilations)) {
        if ((await sha256hex(new TextEncoder().encode(artifact))) !== digest)
          all = false;
      }
      lines.push(
        verdict(
          "compilation artifacts",
          all,
          `${Object.keys(bundle.compilations).length} artifact(s) hash to their filed digests`,
        ),
      );
    }

    // 6. A Registry v2 attestation, when the bundle carries one, recovers its publisher.
    if (bundle.registry_v2_attestation) {
      const a = bundle.registry_v2_attestation;
      try {
        const who = ethers.verifyTypedData(
          {
            name: "Touchstone Registry",
            version: "2",
            chainId: BigInt(a.chain_id),
            verifyingContract: a.verifying_contract,
          },
          {
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
          },
          {
            assetKey: "0x" + a.asset_key,
            reportDigest: "0x" + a.report_digest,
            policyId: "0x" + a.policy_id,
            policyRoot: "0x" + a.policy_root,
            controlSetRoot: "0x" + a.control_set_root,
            evidenceRoot: "0x" + a.evidence_root,
            approvalDigest: "0x" + a.approval_digest,
            epochKey: "0x" + a.epoch_key,
            status: a.status,
            observedAt: a.observed_at,
            validUntil: a.valid_until,
            publisher: a.publisher,
            sequence: a.sequence,
            parentDigest: "0x" + a.parent_digest,
            correctionOf: a.correction_of,
            reportURI: a.report_uri,
          },
          a.signature.startsWith("0x") ? a.signature : "0x" + a.signature,
        );
        const ok = who.toLowerCase() === a.publisher.toLowerCase();
        lines.push(
          verdict(
            "registry v2 attestation",
            ok,
            ok
              ? `EIP-712 recovers publisher ${short(who)}`
              : `recovers ${short(who)}, attestation names ${short(a.publisher)}`,
          ),
        );
      } catch (error) {
        lines.push(
          verdict(
            "registry v2 attestation",
            false,
            String(error.message || error).slice(0, 160),
          ),
        );
      }
    }

    lines.push(
      row(
        "not checked here",
        "control-set and evidence root recomputation, byte-span citation replay — the CLI recipes on /verify do those; this panel never claims them",
        "term-pending",
      ),
    );
    panel.innerHTML = lines.join("");
  } catch (error) {
    panel.innerHTML = verdict(
      "verification",
      false,
      String(error.message || error),
    );
  }
}

/* ---------- wiring ---------- */

function refreshKeys() {
  const net = network();
  const keySelect = $("key-select");
  keySelect.innerHTML = Object.entries(net.keys)
    .map(([id, k]) => `<option value="${id}">${k.label}</option>`)
    .join("");
  const actionSelect = $("action-select");
  actionSelect.innerHTML = Object.entries(net.actions)
    .map(([id, a]) => `<option value="${id}">${a.label}</option>`)
    .join("");
  readReport();
  readGate();
}

$("net-select").addEventListener("change", refreshKeys);
$("key-select").addEventListener("change", () => {
  readReport();
  readGate();
});
$("btn-refresh").addEventListener("click", () => {
  readReport();
  readGate();
});
$("btn-connect").addEventListener("click", connect);
// Simulation is a direct RPC eth_call and needs no wallet — a judge without one still
// gets the refusal demonstration, which is the product's best moment.
$("btn-simulate").disabled = false;
$("btn-simulate").addEventListener("click", simulate);
$("btn-execute").addEventListener("click", execute);
$("btn-decisions").addEventListener("click", myDecisions);

const drop = $("verify-drop");
drop.addEventListener("dragover", (event) => {
  event.preventDefault();
  drop.classList.add("term-ok");
});
drop.addEventListener("dragleave", () => drop.classList.remove("term-ok"));
drop.addEventListener("drop", (event) => {
  event.preventDefault();
  drop.classList.remove("term-ok");
  if (event.dataTransfer.files[0]) verifyBundle(event.dataTransfer.files[0]);
});
$("verify-file").addEventListener("change", (event) => {
  if (event.target.files[0]) verifyBundle(event.target.files[0]);
});

refreshKeys();

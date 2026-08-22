import { Contract, Interface, type Provider } from "ethers";
import { REGISTRY_V2_ABI } from "./abi.js";

export interface PublishedEvent {
  readonly kind: "published" | "corrected";
  readonly assetKey: string;
  readonly sequence: bigint;
  readonly correctedSequence: bigint | null;
  readonly publisher: string;
  readonly reportDigest: string;
  readonly policyId: string;
  readonly approvalDigest: string;
  readonly parentDigest: string;
  readonly blockNumber: number;
  readonly transactionIndex: number;
  readonly logIndex: number;
}

export interface IndexOptions {
  /**
   * Blocks per eth_getLogs request. The public X Layer RPC rejects ranges above 100
   * blocks ("block range greater than 100 max"), so that is the default; raise it for a
   * provider that allows wider windows.
   */
  readonly blockRange?: number;
}

export async function indexPublished(
  provider: Provider,
  registryAddress: string,
  fromBlock: number,
  toBlock: number | string = "latest",
  options: IndexOptions = {}
): Promise<PublishedEvent[]> {
  if (!Number.isInteger(fromBlock) || fromBlock < 0) {
    throw new Error("fromBlock must be a non-negative integer");
  }
  const blockRange = options.blockRange ?? 100;
  if (!Number.isInteger(blockRange) || blockRange < 1) {
    throw new Error("blockRange must be a positive integer");
  }
  const contract = new Contract(registryAddress, REGISTRY_V2_ABI, provider);
  const iface = new Interface(REGISTRY_V2_ABI);
  const published = iface.getEvent("Published");
  const corrected = iface.getEvent("Corrected");
  if (published === null || corrected === null) {
    throw new Error("Registry v2 ABI is missing publication events");
  }
  const address = await contract.getAddress();
  const topics = [[published.topicHash, corrected.topicHash]];
  let lastBlock: number;
  if (typeof toBlock === "number") {
    lastBlock = toBlock;
  } else {
    const block = await provider.getBlock(toBlock);
    if (block === null) {
      throw new Error(`block ${toBlock} could not be resolved`);
    }
    lastBlock = block.number;
  }
  const logs = [];
  for (let start = fromBlock; start <= lastBlock; start += blockRange) {
    const end = Math.min(start + blockRange - 1, lastBlock);
    logs.push(...(await provider.getLogs({ address, topics, fromBlock: start, toBlock: end })));
  }
  return logs.map((log): PublishedEvent => {
    const parsed = iface.parseLog({ data: log.data, topics: log.topics });
    if (parsed === null) {
      throw new Error("Registry v2 publication log could not be decoded");
    }
    const isCorrection = parsed.name === "Corrected";
    return {
      kind: isCorrection ? "corrected" : "published",
      assetKey: parsed.args[0],
      sequence: parsed.args[1],
      correctedSequence: isCorrection ? parsed.args[2] : null,
      publisher: parsed.args[isCorrection ? 3 : 2],
      reportDigest: parsed.args[isCorrection ? 4 : 3],
      policyId: parsed.args[isCorrection ? 5 : 4],
      approvalDigest: parsed.args[isCorrection ? 6 : 5],
      parentDigest: parsed.args[isCorrection ? 7 : 6],
      blockNumber: log.blockNumber,
      transactionIndex: log.transactionIndex,
      logIndex: log.index,
    };
  }).sort(
    (left, right) =>
      left.blockNumber - right.blockNumber ||
      left.transactionIndex - right.transactionIndex ||
      left.logIndex - right.logIndex
  );
}

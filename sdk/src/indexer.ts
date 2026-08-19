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
  readonly parentDigest: string;
  readonly blockNumber: number;
  readonly transactionIndex: number;
  readonly logIndex: number;
}

export async function indexPublished(
  provider: Provider,
  registryAddress: string,
  fromBlock: number,
  toBlock: number | string = "latest"
): Promise<PublishedEvent[]> {
  if (!Number.isInteger(fromBlock) || fromBlock < 0) {
    throw new Error("fromBlock must be a non-negative integer");
  }
  const contract = new Contract(registryAddress, REGISTRY_V2_ABI, provider);
  const iface = new Interface(REGISTRY_V2_ABI);
  const published = iface.getEvent("Published");
  const corrected = iface.getEvent("Corrected");
  if (published === null || corrected === null) {
    throw new Error("Registry v2 ABI is missing publication events");
  }
  const logs = await provider.getLogs({
    address: await contract.getAddress(),
    topics: [[published.topicHash, corrected.topicHash]],
    fromBlock,
    toBlock,
  });
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
      parentDigest: parsed.args[isCorrection ? 6 : 5],
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

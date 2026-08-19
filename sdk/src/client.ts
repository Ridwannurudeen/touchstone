import {
  Contract,
  Interface,
  type ContractRunner,
  type ContractTransactionResponse,
  type Provider,
  type Signer,
} from "ethers";
import { ASSET_GATE_ABI, GUARDED_ACTION_ABI, REGISTRY_V2_ABI } from "./abi.js";
import { appendBuilderCodeSuffix } from "./attribution.js";

export interface GateCheck {
  readonly allowed: boolean;
  readonly reason: string;
}

export interface RegistryReport {
  readonly reportDigest: string;
  readonly policyId: string;
  readonly policyRoot: string;
  readonly controlSetRoot: string;
  readonly evidenceRoot: string;
  readonly approvalDigest: string;
  readonly epochKey: string;
  readonly status: bigint;
  readonly observedAt: bigint;
  readonly validUntil: bigint;
  readonly publisher: string;
  readonly sequence: bigint;
  readonly parentDigest: string;
  readonly reportURI: string;
}

export class AssetGateClient {
  private readonly contract: Contract;

  constructor(
    address: string,
    readonly assetKey: string,
    runner: ContractRunner
  ) {
    this.contract = new Contract(address, ASSET_GATE_ABI, runner);
  }

  async check(): Promise<GateCheck> {
    const result = (await this.contract.getFunction("check")(this.assetKey)) as readonly [
      boolean,
      string,
    ];
    return { allowed: result[0], reason: result[1] };
  }

  async demand(): Promise<ContractTransactionResponse> {
    const result = await this.check();
    if (!result.allowed) {
      throw new Error(`AssetGate refused demand: ${result.reason}`);
    }
    return (await this.contract.getFunction("demand")(
      this.assetKey
    )) as ContractTransactionResponse;
  }
}

export class GuardedActionClient {
  private readonly contract: Contract;
  private readonly signer: Signer;

  constructor(readonly address: string, signer: Signer) {
    this.signer = signer;
    this.contract = new Contract(address, GUARDED_ACTION_ABI, signer);
  }

  async execute(builderCodes: readonly string[] = []): Promise<ContractTransactionResponse> {
    if (builderCodes.length > 0) {
      const data = new Interface(GUARDED_ACTION_ABI).encodeFunctionData("execute");
      return (await this.signer.sendTransaction({
        to: this.address,
        data: appendBuilderCodeSuffix(data, builderCodes),
      })) as ContractTransactionResponse;
    }
    return (await this.contract.getFunction("execute")()) as ContractTransactionResponse;
  }
}

export class RegistryV2Client {
  private readonly contract: Contract;

  constructor(readonly address: string, provider: Provider) {
    this.contract = new Contract(address, REGISTRY_V2_ABI, provider);
  }

  async latestReport(assetKey: string): Promise<RegistryReport | null> {
    const result = (await this.contract.getFunction("getLatestReport")(assetKey)) as readonly [
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      bigint,
      bigint,
      bigint,
      string,
      bigint,
      string,
      string,
    ];
    const report = toReport(result);
    return report.sequence === 0n ? null : report;
  }
}

function toReport(result: readonly [
  string,
  string,
  string,
  string,
  string,
  string,
  string,
  bigint,
  bigint,
  bigint,
  string,
  bigint,
  string,
  string,
]): RegistryReport {
  return {
    reportDigest: result[0],
    policyId: result[1],
    policyRoot: result[2],
    controlSetRoot: result[3],
    evidenceRoot: result[4],
    approvalDigest: result[5],
    epochKey: result[6],
    status: result[7],
    observedAt: result[8],
    validUntil: result[9],
    publisher: result[10],
    sequence: result[11],
    parentDigest: result[12],
    reportURI: result[13],
  };
}

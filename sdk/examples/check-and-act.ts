import { JsonRpcProvider, Wallet } from "ethers";
import {
  AssetGateClient,
  DEPLOYMENTS,
  GuardedActionClient,
  POLICIES,
} from "@touchstone/sdk";

const rpcUrl = process.env.TOUCHSTONE_RPC_URL;
const privateKey = process.env.TOUCHSTONE_INTEGRATION_PRIVATE_KEY;
const gateAddress = process.env.TOUCHSTONE_GATE_ADDRESS;
const guardedActionAddress = process.env.TOUCHSTONE_GUARDED_ACTION_ADDRESS;
const builderCode = process.env.TOUCHSTONE_BUILDER_CODE;

if (!rpcUrl || !privateKey || !gateAddress || !guardedActionAddress) {
  throw new Error(
    "Set TOUCHSTONE_RPC_URL, TOUCHSTONE_INTEGRATION_PRIVATE_KEY, TOUCHSTONE_GATE_ADDRESS and TOUCHSTONE_GUARDED_ACTION_ADDRESS"
  );
}

const deployment = DEPLOYMENTS.xlayerTestnet;
const provider = new JsonRpcProvider(rpcUrl, deployment.chainId);
const wallet = new Wallet(privateKey, provider);
const gate = new AssetGateClient(
  gateAddress,
  POLICIES.disclosureFreshness.registryKey,
  wallet
);
const action = new GuardedActionClient(guardedActionAddress, wallet);

const decision = await gate.check();
console.log(decision);
if (decision.allowed) {
  const transaction = await action.execute(builderCode ? [builderCode] : []);
  console.log(`submitted ${transaction.hash}`);
}

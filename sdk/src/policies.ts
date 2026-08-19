import { keccak256, toUtf8Bytes } from "ethers";

export const USTB_ASSET_KEY =
  "eip155:1:0x43415eb6ff9db7e26a15b704e7a3edce97d31c4e";

export interface Policy {
  readonly policyId: string;
  readonly version: number;
  readonly title: string;
  readonly assetKey: string;
  readonly registryKey: string;
}

function validatePolicyIdentity(policyId: string, version: number): void {
  if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(policyId)) {
    throw new Error("policyId must be lowercase kebab-case");
  }
  if (!Number.isInteger(version) || version < 1) {
    throw new Error("policy version must be a positive integer");
  }
}

export function registryAssetKey(reportAssetKey: string): string {
  if (reportAssetKey.length === 0) {
    throw new Error("reportAssetKey must not be empty");
  }
  return keccak256(toUtf8Bytes(reportAssetKey));
}

export function policyIdDigest(policyId: string, version: number): string {
  validatePolicyIdentity(policyId, version);
  return keccak256(toUtf8Bytes(`${policyId}:${version}`));
}

export function policyDigestRoot(policyDigest: string): string {
  if (!/^[0-9a-f]{64}$/.test(policyDigest)) {
    throw new Error("policyDigest must be a lowercase SHA-256 digest");
  }
  return `0x${policyDigest}`;
}

export function policyRegistryKey(
  assetKey: string,
  policyId: string,
  version: number
): string {
  validatePolicyIdentity(policyId, version);
  return registryAssetKey(`${assetKey}#policy:${policyId}:${version}`);
}

export const POLICIES: Readonly<Record<string, Policy>> = {
  disclosureFreshness: {
    policyId: "disclosure-freshness",
    version: 1,
    title: "Disclosure freshness",
    assetKey: USTB_ASSET_KEY,
    registryKey: policyRegistryKey(USTB_ASSET_KEY, "disclosure-freshness", 1),
  },
  navSettlement: {
    policyId: "nav-settlement",
    version: 1,
    title: "NAV settlement",
    assetKey: USTB_ASSET_KEY,
    registryKey: policyRegistryKey(USTB_ASSET_KEY, "nav-settlement", 1),
  },
};

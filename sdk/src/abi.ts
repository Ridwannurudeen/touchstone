export const ASSET_GATE_ABI = [
  "function registry() view returns (address)",
  "function allowedStatuses() view returns (uint8)",
  "function maxObservationAge() view returns (uint64)",
  "function requiredPublisher() view returns (address)",
  "function requiredControlSetRoot() view returns (bytes32)",
  "function check(bytes32 assetKey) view returns (bool allowed, string reason)",
  "function demand(bytes32 assetKey)",
  "event Demanded(bytes32 indexed assetKey, address indexed caller)",
  "error GateRefused(bytes32 assetKey, string reason)",
] as const;

export const ASSET_GATE_V2_ABI = [
  ...ASSET_GATE_ABI,
  "function expectedPolicyId() view returns (bytes32)",
  "function expectedPolicyRoot() view returns (bytes32)",
] as const;

export const GUARDED_ACTION_ABI = [
  "function gate() view returns (address)",
  "function assetKey() view returns (bytes32)",
  "function actionCount() view returns (uint256)",
  "function execute()",
  "event ActionExecuted(bytes32 indexed assetKey, address indexed caller, uint256 actionNumber)",
] as const;

export const REGISTRY_V2_ABI = [
  "function getLatestReport(bytes32 assetKey) view returns ((bytes32 reportDigest, bytes32 policyId, bytes32 policyRoot, bytes32 controlSetRoot, bytes32 evidenceRoot, bytes32 approvalDigest, bytes32 epochKey, uint8 status, uint64 observedAt, uint64 validUntil, address publisher, uint64 sequence, bytes32 parentDigest, string reportURI) report)",
  "function getReport(bytes32 assetKey, uint64 sequence) view returns ((bytes32 reportDigest, bytes32 policyId, bytes32 policyRoot, bytes32 controlSetRoot, bytes32 evidenceRoot, bytes32 approvalDigest, bytes32 epochKey, uint8 status, uint64 observedAt, uint64 validUntil, address publisher, uint64 sequence, bytes32 parentDigest, string reportURI) report)",
  "event Published(bytes32 indexed assetKey, uint64 indexed sequence, address indexed publisher, bytes32 reportDigest, bytes32 policyId, bytes32 approvalDigest, bytes32 parentDigest)",
  "event Corrected(bytes32 indexed assetKey, uint64 indexed sequence, uint64 correctedSequence, address indexed publisher, bytes32 reportDigest, bytes32 policyId, bytes32 approvalDigest, bytes32 parentDigest)",
] as const;

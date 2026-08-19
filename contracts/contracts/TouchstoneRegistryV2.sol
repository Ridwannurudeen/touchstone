// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.24;

import {TouchstoneRegistry} from "./TouchstoneRegistry.sol";

contract TouchstoneRegistryV2 {
    enum Status {
        CONFIRMED,
        STALE,
        INCONSISTENT,
        UNVERIFIABLE
    }

    struct ReportInput {
        bytes32 assetKey;
        bytes32 reportDigest;
        bytes32 policyId;
        bytes32 policyRoot;
        bytes32 controlSetRoot;
        bytes32 evidenceRoot;
        bytes32 approvalDigest;
        bytes32 epochKey;
        Status status;
        uint64 observedAt;
        uint64 validUntil;
        address publisher;
        uint64 sequence;
        bytes32 parentDigest;
        string reportURI;
    }

    struct Report {
        bytes32 reportDigest;
        bytes32 policyId;
        bytes32 policyRoot;
        bytes32 controlSetRoot;
        bytes32 evidenceRoot;
        bytes32 approvalDigest;
        bytes32 epochKey;
        Status status;
        uint64 observedAt;
        uint64 validUntil;
        address publisher;
        uint64 sequence;
        bytes32 parentDigest;
        string reportURI;
    }

    error UnauthorizedOwner(address caller);
    error UnauthorizedPendingOwner(address caller);
    error InvalidOwner(address owner);
    error UnauthorizedPublisher(address publisher);
    error InvalidPublisher(address publisher);
    error InvalidPublisherRotation(address publisher);
    error PublisherIdentityConflict(
        address publisher,
        address existingIdentity,
        address requestedIdentity
    );
    error PublisherAlreadyAuthorized(address publisher);
    error PublisherNotAuthorized(address publisher);
    error ChainIdMismatch(uint256 expected, uint256 actual);
    error InvalidLegacyRegistry(address registry);
    error InvalidAssetKey();
    error InvalidEpochKey();
    error InvalidReportURI();
    error InvalidReportDigest();
    error InvalidApprovalDigest();
    error InvalidPolicyId();
    error EpochAlreadyPublished(bytes32 assetKey, bytes32 epochKey, uint64 sequence);
    error CorrectionEpochMismatch(bytes32 expected, bytes32 provided);
    error FutureObservation(uint64 observedAt, uint256 currentTimestamp);
    error InvalidValidityWindow(uint64 observedAt, uint64 validUntil);
    error SequenceMismatch(bytes32 assetKey, uint64 expected, uint64 provided);
    error ParentDigestMismatch(bytes32 expected, bytes32 provided);
    error InvalidCorrection(bytes32 assetKey, uint64 correctedSequence);
    error UnknownReport(bytes32 assetKey, uint64 sequence);
    error InvalidAttestationSignature();
    error InvalidSignatureS();

    event Published(
        bytes32 indexed assetKey,
        uint64 indexed sequence,
        address indexed publisher,
        bytes32 reportDigest,
        bytes32 policyId,
        bytes32 approvalDigest,
        bytes32 parentDigest
    );

    event Corrected(
        bytes32 indexed assetKey,
        uint64 indexed sequence,
        uint64 correctedSequence,
        address indexed publisher,
        bytes32 reportDigest,
        bytes32 policyId,
        bytes32 approvalDigest,
        bytes32 parentDigest
    );

    event PublisherAuthorized(address indexed publisher);
    event PublisherRevoked(address indexed publisher);
    event OwnershipTransferStarted(address indexed owner, address indexed pendingOwner);
    event OwnershipTransferred(address indexed previousOwner, address indexed owner);

    bytes32 private constant DOMAIN_TYPEHASH = keccak256(
        "EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"
    );
    bytes32 private constant ATTESTATION_TYPEHASH = keccak256(
        "Attestation(bytes32 assetKey,bytes32 reportDigest,bytes32 policyId,bytes32 policyRoot,bytes32 controlSetRoot,bytes32 evidenceRoot,bytes32 approvalDigest,bytes32 epochKey,uint8 status,uint64 observedAt,uint64 validUntil,address publisher,uint64 sequence,bytes32 parentDigest,uint64 correctionOf,string reportURI)"
    );
    bytes32 private constant NAME_HASH = keccak256("Touchstone Registry");
    bytes32 private constant VERSION_HASH = keccak256("2");
    uint256 private constant SECP256K1_HALF_ORDER =
        0x7fffffffffffffffffffffffffffffff5d576e7357a4501dffe92f46681b20a0;

    address public owner;
    address public pendingOwner;
    uint256 public immutable expectedChainId;
    address public immutable legacyRegistry;

    mapping(address publisher => bool authorized) public isPublisherAuthorized;
    mapping(address publisher => address identity) public publisherIdentity;
    mapping(bytes32 assetKey => uint64 sequence) public latestSequence;
    mapping(bytes32 assetKey => mapping(uint64 sequence => Report report)) private _reports;
    mapping(bytes32 assetKey => mapping(uint64 sequence => uint64 correctedSequence))
        public correctionTarget;
    mapping(bytes32 assetKey => mapping(bytes32 epochKey => uint64 sequence))
        public epochSequence;

    modifier onlyOwner() {
        if (msg.sender != owner) revert UnauthorizedOwner(msg.sender);
        _;
    }

    modifier onlyPublisher() {
        if (!isPublisherAuthorized[msg.sender]) {
            revert UnauthorizedPublisher(msg.sender);
        }
        _;
    }

    constructor(uint256 expectedChainId_, address legacyRegistry_) {
        if (
            legacyRegistry_ == address(0) || legacyRegistry_.code.length == 0
        ) revert InvalidLegacyRegistry(legacyRegistry_);
        owner = msg.sender;
        expectedChainId = expectedChainId_;
        legacyRegistry = legacyRegistry_;
    }

    function authorizePublisher(address publisher) external onlyOwner {
        address identity = publisherIdentity[publisher];
        _authorizePublisher(publisher, identity == address(0) ? publisher : identity);
    }

    function transferOwnership(address nextOwner) external onlyOwner {
        if (nextOwner == address(0) || nextOwner == owner) {
            revert InvalidOwner(nextOwner);
        }
        pendingOwner = nextOwner;
        emit OwnershipTransferStarted(owner, nextOwner);
    }

    function acceptOwnership() external {
        if (msg.sender != pendingOwner) {
            revert UnauthorizedPendingOwner(msg.sender);
        }
        address previousOwner = owner;
        owner = msg.sender;
        pendingOwner = address(0);
        emit OwnershipTransferred(previousOwner, msg.sender);
    }

    function revokePublisher(address publisher) external onlyOwner {
        _revokePublisher(publisher);
    }

    function rotatePublisher(address previousPublisher, address nextPublisher)
        external
        onlyOwner
    {
        if (previousPublisher == nextPublisher) {
            revert InvalidPublisherRotation(previousPublisher);
        }
        address identity = publisherIdentity[previousPublisher];
        _revokePublisher(previousPublisher);
        _authorizePublisher(nextPublisher, identity);
    }

    function isPublisherFor(address requiredPublisher, address postingPublisher)
        external
        view
        returns (bool)
    {
        return
            isPublisherAuthorized[postingPublisher] &&
            publisherIdentity[requiredPublisher] != address(0) &&
            publisherIdentity[requiredPublisher] == publisherIdentity[postingPublisher];
    }

    function publish(ReportInput calldata input, bytes calldata attestationSignature)
        external
    {
        _validateChain();
        _requireAuthorizedPublisher(input.publisher);
        uint64 published = epochSequence[input.assetKey][input.epochKey];
        if (published != 0) {
            revert EpochAlreadyPublished(input.assetKey, input.epochKey, published);
        }
        _verifyAttestation(input, 0, attestationSignature);
        _writeReport(input);
        epochSequence[input.assetKey][input.epochKey] = input.sequence;

        emit Published(
            input.assetKey,
            input.sequence,
            input.publisher,
            input.reportDigest,
            input.policyId,
            input.approvalDigest,
            input.parentDigest
        );
    }

    function publishCorrection(
        uint64 correctedSequence,
        ReportInput calldata input,
        bytes calldata attestationSignature
    ) external {
        _validateChain();
        _requireAuthorizedPublisher(input.publisher);
        uint64 currentSequence = latestSequence[input.assetKey];
        if (correctedSequence == 0 || correctedSequence > currentSequence) {
            revert InvalidCorrection(input.assetKey, correctedSequence);
        }
        bytes32 correctedEpoch = _reports[input.assetKey][correctedSequence].epochKey;
        if (input.epochKey != correctedEpoch) {
            revert CorrectionEpochMismatch(correctedEpoch, input.epochKey);
        }
        _verifyAttestation(input, correctedSequence, attestationSignature);
        _writeReport(input);
        correctionTarget[input.assetKey][input.sequence] = correctedSequence;

        emit Corrected(
            input.assetKey,
            input.sequence,
            correctedSequence,
            input.publisher,
            input.reportDigest,
            input.policyId,
            input.approvalDigest,
            input.parentDigest
        );
    }

    function getLatestReport(bytes32 assetKey) external view returns (Report memory) {
        return _reports[assetKey][latestSequence[assetKey]];
    }

    function getReport(bytes32 assetKey, uint64 sequence)
        external
        view
        returns (Report memory)
    {
        if (sequence == 0 || sequence > latestSequence[assetKey]) {
            revert UnknownReport(assetKey, sequence);
        }
        return _reports[assetKey][sequence];
    }

    function getLegacyLatestReport(bytes32 assetKey)
        external
        view
        returns (TouchstoneRegistry.Report memory)
    {
        return TouchstoneRegistry(legacyRegistry).getLatestReport(assetKey);
    }

    function getLegacyReport(bytes32 assetKey, uint64 sequence)
        external
        view
        returns (TouchstoneRegistry.Report memory)
    {
        return TouchstoneRegistry(legacyRegistry).getReport(assetKey, sequence);
    }

    function attestationDigest(ReportInput calldata input, uint64 correctionOf)
        public
        view
        returns (bytes32)
    {
        bytes32 structHash = keccak256(
            bytes.concat(
                abi.encode(
                    ATTESTATION_TYPEHASH,
                    input.assetKey,
                    input.reportDigest,
                    input.policyId,
                    input.policyRoot,
                    input.controlSetRoot,
                    input.evidenceRoot,
                    input.approvalDigest,
                    input.epochKey
                ),
                abi.encode(
                    uint8(input.status),
                    input.observedAt,
                    input.validUntil,
                    input.publisher,
                    input.sequence,
                    input.parentDigest,
                    correctionOf,
                    keccak256(bytes(input.reportURI))
                )
            )
        );
        return
            keccak256(
                abi.encodePacked(
                    "\x19\x01",
                    _domainSeparator(),
                    structHash
                )
            );
    }

    function _authorizePublisher(address publisher, address identity) private {
        if (publisher == address(0)) revert InvalidPublisher(publisher);
        if (isPublisherAuthorized[publisher]) {
            revert PublisherAlreadyAuthorized(publisher);
        }
        address existingIdentity = publisherIdentity[publisher];
        if (existingIdentity != address(0) && existingIdentity != identity) {
            revert PublisherIdentityConflict(publisher, existingIdentity, identity);
        }
        isPublisherAuthorized[publisher] = true;
        if (existingIdentity == address(0)) {
            publisherIdentity[publisher] = identity;
        }
        emit PublisherAuthorized(publisher);
    }

    function _revokePublisher(address publisher) private {
        if (!isPublisherAuthorized[publisher]) {
            revert PublisherNotAuthorized(publisher);
        }
        isPublisherAuthorized[publisher] = false;
        emit PublisherRevoked(publisher);
    }

    function _validateChain() private view {
        if (block.chainid != expectedChainId) {
            revert ChainIdMismatch(expectedChainId, block.chainid);
        }
    }

    function _verifyAttestation(
        ReportInput calldata input,
        uint64 correctionOf,
        bytes calldata signature
    ) private view {
        if (input.reportDigest == bytes32(0)) revert InvalidReportDigest();
        if (input.approvalDigest == bytes32(0)) revert InvalidApprovalDigest();
        if (input.policyId == bytes32(0)) revert InvalidPolicyId();
        if (signature.length != 65) revert InvalidAttestationSignature();
        bytes32 r;
        bytes32 s;
        uint8 v;
        assembly {
            r := calldataload(signature.offset)
            s := calldataload(add(signature.offset, 32))
            v := byte(0, calldataload(add(signature.offset, 64)))
        }
        if (v < 27) v += 27;
        if (v != 27 && v != 28) revert InvalidAttestationSignature();
        if (uint256(s) > SECP256K1_HALF_ORDER) revert InvalidSignatureS();
        address recovered = ecrecover(
            attestationDigest(input, correctionOf),
            v,
            r,
            s
        );
        if (recovered == address(0) || recovered != input.publisher) {
            revert InvalidAttestationSignature();
        }
    }

    function _writeReport(ReportInput calldata input) private {
        if (input.assetKey == bytes32(0)) revert InvalidAssetKey();
        if (input.epochKey == bytes32(0)) revert InvalidEpochKey();
        if (bytes(input.reportURI).length == 0) revert InvalidReportURI();
        if (input.observedAt > block.timestamp) {
            revert FutureObservation(input.observedAt, block.timestamp);
        }
        if (input.validUntil < input.observedAt) {
            revert InvalidValidityWindow(input.observedAt, input.validUntil);
        }
        uint64 expectedSequence = latestSequence[input.assetKey] + 1;
        if (input.sequence != expectedSequence) {
            revert SequenceMismatch(input.assetKey, expectedSequence, input.sequence);
        }
        bytes32 expectedParent =
            input.sequence == 1
                ? bytes32(0)
                : _reports[input.assetKey][input.sequence - 1].reportDigest;
        if (input.parentDigest != expectedParent) {
            revert ParentDigestMismatch(expectedParent, input.parentDigest);
        }

        _reports[input.assetKey][input.sequence] = Report({
            reportDigest: input.reportDigest,
            policyId: input.policyId,
            policyRoot: input.policyRoot,
            controlSetRoot: input.controlSetRoot,
            evidenceRoot: input.evidenceRoot,
            approvalDigest: input.approvalDigest,
            epochKey: input.epochKey,
            status: input.status,
            observedAt: input.observedAt,
            validUntil: input.validUntil,
            publisher: input.publisher,
            sequence: input.sequence,
            parentDigest: input.parentDigest,
            reportURI: input.reportURI
        });
        latestSequence[input.assetKey] = input.sequence;
    }

    function _requireAuthorizedPublisher(address publisher) private view {
        if (!isPublisherAuthorized[publisher]) {
            revert UnauthorizedPublisher(publisher);
        }
    }

    function _domainSeparator() private view returns (bytes32) {
        return
            keccak256(
                abi.encode(
                    DOMAIN_TYPEHASH,
                    NAME_HASH,
                    VERSION_HASH,
                    block.chainid,
                    address(this)
                )
            );
    }
}

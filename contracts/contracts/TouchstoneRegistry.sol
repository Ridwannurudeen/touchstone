// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.24;

contract TouchstoneRegistry {
    enum Status {
        CONFIRMED,
        STALE,
        INCONSISTENT,
        UNVERIFIABLE
    }

    struct Report {
        bytes32 controlSetRoot;
        bytes32 evidenceRoot;
        Status status;
        uint64 observedAt;
        uint64 validUntil;
        address publisher;
        uint64 sequence;
        string reportURI;
    }

    error UnauthorizedOwner(address caller);
    error UnauthorizedPublisher(address publisher);
    error InvalidPublisher(address publisher);
    error InvalidPublisherRotation(address publisher);
    error PublisherAlreadyAuthorized(address publisher);
    error PublisherNotAuthorized(address publisher);
    error ChainIdMismatch(uint256 expected, uint256 actual);
    error InvalidAssetKey();
    error InvalidReportURI();
    error FutureObservation(uint64 observedAt, uint256 currentTimestamp);
    error InvalidValidityWindow(uint64 observedAt, uint64 validUntil);
    error SequenceMismatch(bytes32 assetKey, uint64 expected, uint64 provided);
    error InvalidCorrection(bytes32 assetKey, uint64 correctedSequence);
    error UnknownReport(bytes32 assetKey, uint64 sequence);

    event Published(
        bytes32 indexed assetKey,
        uint64 indexed sequence,
        address indexed publisher,
        bytes32 controlSetRoot,
        bytes32 evidenceRoot,
        Status status,
        uint64 observedAt,
        uint64 validUntil,
        string reportURI
    );

    event Corrected(
        bytes32 indexed assetKey,
        uint64 indexed sequence,
        uint64 correctedSequence,
        address indexed publisher,
        bytes32 controlSetRoot,
        bytes32 evidenceRoot,
        Status status,
        uint64 observedAt,
        uint64 validUntil,
        string reportURI
    );

    event PublisherAuthorized(address indexed publisher);
    event PublisherRevoked(address indexed publisher);

    address public immutable owner;
    uint256 public immutable expectedChainId;

    mapping(address publisher => bool authorized) public isPublisherAuthorized;
    mapping(address publisher => address identity) public publisherIdentity;
    mapping(bytes32 assetKey => uint64 sequence) public latestSequence;
    mapping(bytes32 assetKey => mapping(uint64 sequence => Report report)) private _reports;
    mapping(bytes32 assetKey => mapping(uint64 sequence => uint64 correctedSequence))
        public correctionTarget;

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

    constructor(uint256 expectedChainId_) {
        owner = msg.sender;
        expectedChainId = expectedChainId_;
    }

    function authorizePublisher(address publisher) external onlyOwner {
        _authorizePublisher(publisher, publisher);
    }

    function revokePublisher(address publisher) external onlyOwner {
        _revokePublisher(publisher);
    }

    function rotatePublisher(address previousPublisher, address nextPublisher) external onlyOwner {
        if (previousPublisher == nextPublisher) {
            revert InvalidPublisherRotation(previousPublisher);
        }
        address identity = publisherIdentity[previousPublisher];
        _revokePublisher(previousPublisher);
        _authorizePublisher(nextPublisher, identity);
    }

    function isPublisherFor(
        address requiredPublisher,
        address postingPublisher
    ) external view returns (bool) {
        return
            isPublisherAuthorized[postingPublisher] &&
            publisherIdentity[requiredPublisher] != address(0) &&
            publisherIdentity[requiredPublisher] == publisherIdentity[postingPublisher];
    }

    function publish(
        bytes32 assetKey,
        bytes32 controlSetRoot,
        bytes32 evidenceRoot,
        Status status,
        uint64 observedAt,
        uint64 validUntil,
        uint64 sequence,
        string calldata reportURI
    ) external onlyPublisher {
        _validateChain();
        _writeReport(
            assetKey,
            controlSetRoot,
            evidenceRoot,
            status,
            observedAt,
            validUntil,
            sequence,
            reportURI
        );

        emit Published(
            assetKey,
            sequence,
            msg.sender,
            controlSetRoot,
            evidenceRoot,
            status,
            observedAt,
            validUntil,
            reportURI
        );
    }

    function publishCorrection(
        bytes32 assetKey,
        uint64 correctedSequence,
        bytes32 controlSetRoot,
        bytes32 evidenceRoot,
        Status status,
        uint64 observedAt,
        uint64 validUntil,
        uint64 sequence,
        string calldata reportURI
    ) external onlyPublisher {
        _validateChain();
        uint64 currentSequence = latestSequence[assetKey];
        if (correctedSequence == 0 || correctedSequence > currentSequence) {
            revert InvalidCorrection(assetKey, correctedSequence);
        }

        _writeReport(
            assetKey,
            controlSetRoot,
            evidenceRoot,
            status,
            observedAt,
            validUntil,
            sequence,
            reportURI
        );
        correctionTarget[assetKey][sequence] = correctedSequence;

        emit Corrected(
            assetKey,
            sequence,
            correctedSequence,
            msg.sender,
            controlSetRoot,
            evidenceRoot,
            status,
            observedAt,
            validUntil,
            reportURI
        );
    }

    function getLatestReport(bytes32 assetKey) external view returns (Report memory) {
        return _reports[assetKey][latestSequence[assetKey]];
    }

    function getReport(bytes32 assetKey, uint64 sequence) external view returns (Report memory) {
        if (sequence == 0 || sequence > latestSequence[assetKey]) {
            revert UnknownReport(assetKey, sequence);
        }
        return _reports[assetKey][sequence];
    }

    function _authorizePublisher(address publisher, address identity) private {
        if (publisher == address(0)) revert InvalidPublisher(publisher);
        if (isPublisherAuthorized[publisher]) {
            revert PublisherAlreadyAuthorized(publisher);
        }
        isPublisherAuthorized[publisher] = true;
        publisherIdentity[publisher] = identity;
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

    function _writeReport(
        bytes32 assetKey,
        bytes32 controlSetRoot,
        bytes32 evidenceRoot,
        Status status,
        uint64 observedAt,
        uint64 validUntil,
        uint64 sequence,
        string calldata reportURI
    ) private {
        if (assetKey == bytes32(0)) revert InvalidAssetKey();
        if (bytes(reportURI).length == 0) revert InvalidReportURI();
        if (observedAt > block.timestamp) {
            revert FutureObservation(observedAt, block.timestamp);
        }
        if (validUntil < observedAt) {
            revert InvalidValidityWindow(observedAt, validUntil);
        }

        uint64 expectedSequence = latestSequence[assetKey] + 1;
        if (sequence != expectedSequence) {
            revert SequenceMismatch(assetKey, expectedSequence, sequence);
        }

        _reports[assetKey][sequence] = Report({
            controlSetRoot: controlSetRoot,
            evidenceRoot: evidenceRoot,
            status: status,
            observedAt: observedAt,
            validUntil: validUntil,
            publisher: msg.sender,
            sequence: sequence,
            reportURI: reportURI
        });
        latestSequence[assetKey] = sequence;
    }
}

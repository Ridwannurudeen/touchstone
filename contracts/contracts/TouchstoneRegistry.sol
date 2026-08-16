// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.24;

contract TouchstoneRegistry {
    enum Status {
        CONFIRMED,
        STALE,
        INCONSISTENT,
        UNVERIFIABLE
    }

    // `epochKey` names the period a report is a statement about, and is what makes
    // "one report per epoch" something the registry enforces rather than something a
    // publisher is trusted to remember. A daemon restarted the same day derives the same
    // epoch and asks for the next sequence, which the chain would otherwise happily
    // accept: two signed reports about one day, both valid, disagreeing or not.
    struct Report {
        bytes32 controlSetRoot;
        bytes32 evidenceRoot;
        bytes32 epochKey;
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
    error PublisherIdentityConflict(
        address publisher,
        address existingIdentity,
        address requestedIdentity
    );
    error PublisherAlreadyAuthorized(address publisher);
    error PublisherNotAuthorized(address publisher);
    error ChainIdMismatch(uint256 expected, uint256 actual);
    error InvalidAssetKey();
    error InvalidEpochKey();
    error InvalidReportURI();
    error EpochAlreadyPublished(bytes32 assetKey, bytes32 epochKey, uint64 sequence);
    error CorrectionEpochMismatch(bytes32 expected, bytes32 provided);
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
        bytes32 epochKey,
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
        bytes32 epochKey,
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
    // The sequence that first published each epoch, and zero for an epoch never published.
    // A correction does not write here: it restates an epoch that already has an entry, and
    // `correctionTarget` is what distinguishes it.
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

    constructor(uint256 expectedChainId_) {
        owner = msg.sender;
        expectedChainId = expectedChainId_;
    }

    function authorizePublisher(address publisher) external onlyOwner {
        address identity = publisherIdentity[publisher];
        _authorizePublisher(publisher, identity == address(0) ? publisher : identity);
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
        bytes32 epochKey,
        Status status,
        uint64 observedAt,
        uint64 validUntil,
        uint64 sequence,
        string calldata reportURI
    ) external onlyPublisher {
        _validateChain();
        uint64 published = epochSequence[assetKey][epochKey];
        if (published != 0) {
            revert EpochAlreadyPublished(assetKey, epochKey, published);
        }
        _writeReport(
            assetKey,
            controlSetRoot,
            evidenceRoot,
            epochKey,
            status,
            observedAt,
            validUntil,
            sequence,
            reportURI
        );
        epochSequence[assetKey][epochKey] = sequence;

        emit Published(
            assetKey,
            sequence,
            msg.sender,
            controlSetRoot,
            evidenceRoot,
            epochKey,
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
        bytes32 epochKey,
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
        // A correction restates one epoch; it does not open another. Letting it carry a
        // different epoch would make `epochSequence` describe the first publication of an
        // epoch that no longer has a latest report, and a second daily report could then
        // be filed as a "correction" of an unrelated day.
        bytes32 correctedEpoch = _reports[assetKey][correctedSequence].epochKey;
        if (epochKey != correctedEpoch) {
            revert CorrectionEpochMismatch(correctedEpoch, epochKey);
        }

        _writeReport(
            assetKey,
            controlSetRoot,
            evidenceRoot,
            epochKey,
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
            epochKey,
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

    function _writeReport(
        bytes32 assetKey,
        bytes32 controlSetRoot,
        bytes32 evidenceRoot,
        bytes32 epochKey,
        Status status,
        uint64 observedAt,
        uint64 validUntil,
        uint64 sequence,
        string calldata reportURI
    ) private {
        if (assetKey == bytes32(0)) revert InvalidAssetKey();
        // Zero is how `epochSequence` says "never published", so a zero epoch key would be
        // an epoch that can be published without limit and read as absent afterwards.
        if (epochKey == bytes32(0)) revert InvalidEpochKey();
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
            epochKey: epochKey,
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

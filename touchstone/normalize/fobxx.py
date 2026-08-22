"""Strict normalization for the retained SEC N-MFP3 FOBXX filing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
import json
import re
from xml.etree import ElementTree


FOBXX_SUBMISSIONS_SOURCE_ID = "sec-edgar-fobxx-submissions"
FOBXX_SOURCE_ID = "sec-edgar-fobxx-nmfp3"
FOBXX_CIK = "0001786958"
FOBXX_SERIES_ID = "S000067043"
DEFAULT_SUBMISSIONS_MAX_BYTES = 8_388_608
DEFAULT_MAX_BYTES = 4_194_304
DEFAULT_MAX_DEPTH = 48
_FORBIDDEN_XML_MARKERS = (b"<!DOCTYPE", b"<!ENTITY", b"<![CDATA[")
_ACCESSION = re.compile(r"[0-9]{10}-[0-9]{2}-[0-9]{6}")
_N_MFP3_FORMS = frozenset({"N-MFP3", "N-MFP3/A"})


class FobxxNormalizationError(ValueError):
    """The regulator filing is malformed, unsafe, or not the FOBXX series."""


@dataclass(frozen=True, slots=True)
class FobxxLiquidityRow:
    date: date
    daily_liquid_assets: Decimal
    weekly_liquid_assets: Decimal
    daily_percentage: Decimal
    weekly_percentage: Decimal


@dataclass(frozen=True, slots=True)
class FobxxObservation:
    report_date: date
    cik: str
    series_id: str
    series_name: str
    net_assets: Decimal
    liquidity_rows: tuple[FobxxLiquidityRow, ...]
    submission_type: str
    filing_date: date | None = None

    @property
    def as_of_date(self) -> date:
        return self.report_date


@dataclass(frozen=True, slots=True)
class FobxxSubmission:
    accession_number: str
    form: str
    filing_date: date
    report_date: date
    primary_document: str


@dataclass(frozen=True, slots=True)
class FobxxSubmissionsObservation:
    cik: str
    entity_name: str
    filings: tuple[FobxxSubmission, ...]

    @property
    def as_of_date(self) -> date:
        return max(filing.report_date for filing in self.filings)


def latest_nmfp3_url(observation: FobxxSubmissionsObservation) -> str:
    """Derive the raw XML URL for the newest filing in retained SEC discovery data."""
    filing = latest_nmfp3_filing(observation)
    accession = filing.accession_number.replace("-", "")
    return (
        f"https://www.sec.gov/Archives/edgar/data/1786958/{accession}/primary_doc.xml"
    )


def latest_nmfp3_filing(
    observation: FobxxSubmissionsObservation,
) -> FobxxSubmission:
    """Select the newest discovered filing and validate its path components."""
    if not isinstance(observation, FobxxSubmissionsObservation):
        raise TypeError("FOBXX filing discovery requires a submissions observation")
    filing = max(
        observation.filings,
        key=lambda item: (item.report_date, item.filing_date, item.accession_number),
    )
    if _ACCESSION.fullmatch(filing.accession_number) is None:
        raise FobxxNormalizationError("N-MFP3 accession has an invalid format")
    if filing.primary_document.rsplit("/", 1)[-1] != "primary_doc.xml":
        raise FobxxNormalizationError("N-MFP3 primary document is not primary_doc.xml")
    return filing


def parse_submissions(
    raw: bytes | bytearray | memoryview,
    *,
    max_bytes: int = DEFAULT_SUBMISSIONS_MAX_BYTES,
) -> FobxxSubmissionsObservation:
    """Parse the SEC discovery snapshot and retain only N-MFP3 filings."""
    if not isinstance(raw, (bytes, bytearray, memoryview)):
        raise TypeError("FOBXX submissions must be bytes")
    content = bytes(raw)
    if len(content) > max_bytes:
        raise FobxxNormalizationError("FOBXX submissions exceed their byte limit")
    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FobxxNormalizationError("FOBXX submissions are not valid JSON") from error
    if not isinstance(payload, dict):
        raise FobxxNormalizationError("FOBXX submissions root must be an object")
    cik = payload.get("cik")
    if cik != FOBXX_CIK:
        raise FobxxNormalizationError(
            "FOBXX submissions CIK does not match the manifest"
        )
    entity_name = payload.get("name")
    if not isinstance(entity_name, str) or not entity_name.strip():
        raise FobxxNormalizationError("FOBXX submissions name must be non-empty text")
    filings = payload.get("filings")
    if not isinstance(filings, dict):
        raise FobxxNormalizationError("FOBXX submissions filings must be an object")
    recent = filings.get("recent")
    if not isinstance(recent, dict):
        raise FobxxNormalizationError(
            "FOBXX submissions recent filings must be an object"
        )

    fields = {
        name: recent.get(name)
        for name in (
            "accessionNumber",
            "form",
            "filingDate",
            "reportDate",
            "primaryDocument",
        )
    }
    if any(not isinstance(value, list) for value in fields.values()):
        raise FobxxNormalizationError("FOBXX submission columns must be arrays")
    lengths = {len(value) for value in fields.values()}
    if len(lengths) != 1:
        raise FobxxNormalizationError("FOBXX submission columns have different lengths")

    submissions: list[FobxxSubmission] = []
    seen_accessions: set[str] = set()
    for index, form in enumerate(fields["form"]):
        if form not in _N_MFP3_FORMS:
            continue
        accession = fields["accessionNumber"][index]
        primary_document = fields["primaryDocument"][index]
        report_date = fields["reportDate"][index]
        if not all(
            isinstance(value, str) and value.strip()
            for value in (accession, primary_document, report_date)
        ):
            raise FobxxNormalizationError("N-MFP3 filing fields must be non-empty text")
        if accession in seen_accessions:
            raise FobxxNormalizationError(f"N-MFP3 accession repeats: {accession}")
        seen_accessions.add(accession)
        filing_date = fields["filingDate"][index]
        if not isinstance(filing_date, str) or not filing_date.strip():
            raise FobxxNormalizationError("N-MFP3 filing date must be non-empty text")
        submissions.append(
            FobxxSubmission(
                accession_number=accession,
                form=form,
                filing_date=_date(filing_date, "filing date"),
                report_date=_date(report_date, "report date"),
                primary_document=primary_document,
            )
        )
    if not submissions:
        raise FobxxNormalizationError("FOBXX submissions contain no N-MFP3 filing")
    return FobxxSubmissionsObservation(
        cik=cik,
        entity_name=entity_name.strip(),
        filings=tuple(submissions),
    )


def parse_nmfp3(
    raw: bytes | bytearray | memoryview,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_depth: int = DEFAULT_MAX_DEPTH,
) -> FobxxObservation | FobxxSubmissionsObservation:
    """Parse the exact regulator series and its dated liquidity rows."""
    if not isinstance(raw, (bytes, bytearray, memoryview)):
        raise TypeError("FOBXX filing must be bytes")
    content = bytes(raw)
    if len(content) > max_bytes:
        raise FobxxNormalizationError("FOBXX filing exceeds its byte limit")
    upper = content.upper()
    if any(marker in upper for marker in _FORBIDDEN_XML_MARKERS):
        raise FobxxNormalizationError(
            "FOBXX XML must not contain DTD, entity, or CDATA"
        )
    try:
        root = ElementTree.fromstring(content)
    except (ElementTree.ParseError, UnicodeDecodeError) as error:
        raise FobxxNormalizationError("FOBXX filing is not well-formed XML") from error
    if _depth(root) > max_depth:
        raise FobxxNormalizationError("FOBXX XML exceeds its depth limit")
    if _local(root.tag) != "edgarSubmission":
        raise FobxxNormalizationError("FOBXX XML root must be edgarSubmission")

    form = _child(root, "formData")
    header = _child(root, "headerData")
    general = _child(form, "generalInfo")
    series = _child(form, "seriesLevelInfo")
    cik = _text(general, "cik")
    series_id = _text(general, "seriesId")
    if cik != FOBXX_CIK:
        raise FobxxNormalizationError("FOBXX filing CIK does not match the manifest")
    if series_id != FOBXX_SERIES_ID:
        raise FobxxNormalizationError(
            "FOBXX filing series id does not match the manifest"
        )
    submission_type = _text(header, "submissionType")
    if submission_type not in _N_MFP3_FORMS:
        raise FobxxNormalizationError("FOBXX filing is not N-MFP3")

    rows: list[FobxxLiquidityRow] = []
    seen_dates: set[date] = set()
    details = [item for item in series if _local(item.tag) == "liquidAssetsDetails"]
    if not details:
        raise FobxxNormalizationError("FOBXX filing has no liquidity series")
    for index, item in enumerate(details):
        row_date = _date(
            _text(item, "totalLiquidAssetsNearPercentDate"), "liquidity date"
        )
        if row_date in seen_dates:
            raise FobxxNormalizationError(f"liquidity date repeats: {row_date}")
        seen_dates.add(row_date)
        daily = _decimal(_text(item, "totalValueDailyLiquidAssets"), "daily assets")
        weekly = _decimal(_text(item, "totalValueWeeklyLiquidAssets"), "weekly assets")
        daily_percentage = _decimal(
            _text(item, "percentageDailyLiquidAssets"), "daily percentage"
        )
        weekly_percentage = _decimal(
            _text(item, "percentageWeeklyLiquidAssets"), "weekly percentage"
        )
        if min(daily, weekly, daily_percentage, weekly_percentage) < 0:
            raise FobxxNormalizationError(
                f"liquidity row {index} contains a negative value"
            )
        if daily_percentage > 1 or weekly_percentage > 1:
            raise FobxxNormalizationError(f"liquidity row {index} percentage exceeds 1")
        rows.append(
            FobxxLiquidityRow(
                date=row_date,
                daily_liquid_assets=daily,
                weekly_liquid_assets=weekly,
                daily_percentage=daily_percentage,
                weekly_percentage=weekly_percentage,
            )
        )

    return FobxxObservation(
        report_date=_date(_text(general, "reportDate"), "report date"),
        cik=cik,
        series_id=series_id,
        series_name=_text(general, "nameOfSeries"),
        net_assets=_decimal(_text(series, "netAssetOfSeries"), "net assets"),
        liquidity_rows=tuple(rows),
        submission_type=submission_type,
    )


def normalize_fobxx_payload(
    source_id: str,
    raw: bytes | bytearray | memoryview,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    isolated: bool = False,
    **_: object,
) -> FobxxObservation:
    """Normalize only the allowlisted FOBXX regulator sources."""
    if source_id == FOBXX_SUBMISSIONS_SOURCE_ID:
        del isolated
        return parse_submissions(raw, max_bytes=max_bytes)
    if source_id != FOBXX_SOURCE_ID:
        raise FobxxNormalizationError(f"unknown FOBXX source id: {source_id!r}")
    del isolated
    return parse_nmfp3(raw, max_bytes=max_bytes)


def _child(parent: ElementTree.Element, name: str) -> ElementTree.Element:
    matches = [item for item in parent if _local(item.tag) == name]
    if len(matches) != 1:
        raise FobxxNormalizationError(f"expected exactly one {name} element")
    return matches[0]


def _text(parent: ElementTree.Element, name: str) -> str:
    value = _child(parent, name).text
    if value is None or not value.strip():
        raise FobxxNormalizationError(f"{name} must contain non-empty text")
    return value.strip()


def _date(value: str, field: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise FobxxNormalizationError(f"{field} must be an ISO date") from error


def _decimal(value: str, field: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise FobxxNormalizationError(f"{field} must be a decimal") from error
    if not parsed.is_finite():
        raise FobxxNormalizationError(f"{field} must be finite")
    return parsed


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _depth(root: ElementTree.Element) -> int:
    if not list(root):
        return 1
    return 1 + max(_depth(child) for child in root)

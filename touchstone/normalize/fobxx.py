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
FOBXX_LOOKUP_SOURCE_ID = "franklin-fobxx-product-lookup"
FOBXX_HISTORY_SOURCE_ID = "franklin-fobxx-price-performance"
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
class FobxxProductLookupObservation:
    fund_id: str
    share_class_code: str


@dataclass(frozen=True, slots=True)
class FobxxPriceRow:
    date: date
    nav_std: Decimal
    daily_liquid_asset_ratio: Decimal | None
    weekly_liquid_asset_ratio: Decimal | None

    @property
    def observed_on(self) -> date:
        return self.date


@dataclass(frozen=True, slots=True)
class FobxxPriceHistoryObservation:
    fund_id: str
    share_class_code: str
    rows: tuple[FobxxPriceRow, ...]

    @property
    def as_of_date(self) -> date:
        return max(row.date for row in self.rows)


@dataclass(frozen=True, slots=True)
class FobxxLiquidityRow:
    date: date
    daily_liquid_assets: Decimal
    weekly_liquid_assets: Decimal
    daily_percentage: Decimal | None
    weekly_percentage: Decimal | None


@dataclass(frozen=True, slots=True)
class FobxxObservation:
    report_date: date
    cik: str
    series_id: str
    series_name: str
    net_assets: Decimal
    stable_price_per_share: Decimal
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


def parse_product_lookup(
    raw: bytes | bytearray | memoryview,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> FobxxProductLookupObservation:
    """Parse the exact ProductLookup response used to bind the history request."""
    payload = _graphql_payload(raw, max_bytes=max_bytes)
    data = _exact_object(payload.get("data"), {"ProductLookup"}, "lookup data")
    records = data["ProductLookup"]
    if not isinstance(records, list) or len(records) != 1:
        raise FobxxNormalizationError("ProductLookup must return exactly one fund")
    record = _exact_object(records[0], {"fundid", "shclcode"}, "ProductLookup record")
    fund_id = _non_empty_json_text(record["fundid"], "fundid")
    share_class_code = _non_empty_json_text(record["shclcode"], "shclcode")
    if fund_id != "29386":
        raise FobxxNormalizationError("ProductLookup fundid does not identify FOBXX")
    if share_class_code != "SINGLCLASS":
        raise FobxxNormalizationError("ProductLookup shclcode does not identify FOBXX")
    return FobxxProductLookupObservation(fund_id, share_class_code)


def parse_price_history(
    raw: bytes | bytearray | memoryview,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> FobxxPriceHistoryObservation:
    """Parse the fixed FOBXX PricesHistory projection and retain blank ratios as no-data."""
    payload = _graphql_payload(raw, max_bytes=max_bytes)
    data = _exact_object(payload.get("data"), {"PricesHistory"}, "history data")
    history = _exact_object(data["PricesHistory"], {"prices"}, "PricesHistory response")
    raw_rows = history["prices"]
    if not isinstance(raw_rows, list) or not raw_rows:
        raise FobxxNormalizationError("PricesHistory prices must be a non-empty array")

    rows_by_date: dict[date, FobxxPriceRow] = {}
    for index, value in enumerate(raw_rows):
        record = _exact_object(
            value,
            {
                "fundid",
                "shclcode",
                "navdate",
                "navstd",
                "dailyliquidassetratio",
                "weeklyliquidassetratio",
            },
            f"PricesHistory row {index}",
        )
        fund_id = _non_empty_json_text(record["fundid"], "fundid")
        share_class_code = _non_empty_json_text(record["shclcode"], "shclcode")
        if fund_id != "29386" or share_class_code != "SINGLCLASS":
            raise FobxxNormalizationError(
                f"PricesHistory row {index} does not identify FOBXX"
            )
        row = FobxxPriceRow(
            date=_date(_non_empty_json_text(record["navdate"], "navdate"), "NAV date"),
            nav_std=_decimal(
                _non_empty_json_text(record["navstd"], "navstd"), "standard NAV"
            ),
            daily_liquid_asset_ratio=_optional_percent_ratio(
                record["dailyliquidassetratio"], "daily liquidity ratio"
            ),
            weekly_liquid_asset_ratio=_optional_percent_ratio(
                record["weeklyliquidassetratio"], "weekly liquidity ratio"
            ),
        )
        prior = rows_by_date.get(row.date)
        if prior is not None and prior != row:
            raise FobxxNormalizationError(
                f"PricesHistory date repeats with different values: {row.date}"
            )
        rows_by_date[row.date] = row
    return FobxxPriceHistoryObservation(
        fund_id="29386",
        share_class_code="SINGLCLASS",
        rows=tuple(
            sorted(rows_by_date.values(), key=lambda row: row.date, reverse=True)
        ),
    )


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
        daily_percentage = _optional_xml_decimal(
            item, "percentageDailyLiquidAssets", "daily percentage"
        )
        weekly_percentage = _optional_xml_decimal(
            item, "percentageWeeklyLiquidAssets", "weekly percentage"
        )
        percentages = tuple(
            value
            for value in (daily_percentage, weekly_percentage)
            if value is not None
        )
        if min((daily, weekly, *percentages)) < 0:
            raise FobxxNormalizationError(
                f"liquidity row {index} contains a negative value"
            )
        if any(value > 1 for value in percentages):
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
        stable_price_per_share=_decimal(
            _text(series, "stablePricePerShare"), "stable price per share"
        ),
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
    if source_id == FOBXX_LOOKUP_SOURCE_ID:
        del isolated
        return parse_product_lookup(raw, max_bytes=max_bytes)
    if source_id == FOBXX_HISTORY_SOURCE_ID:
        del isolated
        return parse_price_history(raw, max_bytes=max_bytes)
    if source_id == FOBXX_SUBMISSIONS_SOURCE_ID:
        del isolated
        return parse_submissions(raw, max_bytes=max_bytes)
    if source_id != FOBXX_SOURCE_ID:
        raise FobxxNormalizationError(f"unknown FOBXX source id: {source_id!r}")
    del isolated
    return parse_nmfp3(raw, max_bytes=max_bytes)


def _graphql_payload(
    raw: bytes | bytearray | memoryview, *, max_bytes: int
) -> dict[str, object]:
    if not isinstance(raw, (bytes, bytearray, memoryview)):
        raise TypeError("FOBXX GraphQL response must be bytes")
    content = bytes(raw)
    if len(content) > max_bytes:
        raise FobxxNormalizationError("FOBXX GraphQL response exceeds its byte limit")
    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FobxxNormalizationError(
            "FOBXX GraphQL response is not valid JSON"
        ) from error
    if not isinstance(payload, dict) or not set(payload) <= {"data", "errors"}:
        raise FobxxNormalizationError("FOBXX GraphQL response fields are invalid")
    if payload.get("errors") is not None:
        raise FobxxNormalizationError("FOBXX GraphQL response contains errors")
    if "data" not in payload:
        raise FobxxNormalizationError("FOBXX GraphQL response has no data")
    return payload


def _exact_object(value: object, expected: set[str], context: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise FobxxNormalizationError(f"{context} must be an object")
    if set(value) != expected:
        raise FobxxNormalizationError(f"{context} fields do not match the query")
    return value


def _non_empty_json_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FobxxNormalizationError(f"{field} must be non-empty text")
    return value.strip()


def _optional_percent_ratio(value: object, field: str) -> Decimal | None:
    if value == "":
        return None
    if not isinstance(value, str):
        raise FobxxNormalizationError(f"{field} must be text or blank")
    ratio = _decimal(value, field) / Decimal(100)
    if not 0 <= ratio <= 1:
        raise FobxxNormalizationError(f"{field} must be between zero and 100 percent")
    return ratio


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


def _optional_xml_decimal(
    parent: ElementTree.Element, name: str, field: str
) -> Decimal | None:
    value = _child(parent, name).text
    if value is None or not value.strip():
        return None
    return _decimal(value.strip(), field)


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

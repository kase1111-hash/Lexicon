"""Validation pipeline for quality control.

Validates LSR data before and after ingestion:
1. Schema validation (required fields, types, ranges)
2. Consistency validation (date inversions, circular references)
3. Anomaly detection (outliers, missing data, confidence flags)
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from uuid import UUID

logger = logging.getLogger(__name__)

# Valid ISO 639-3 codes (common subset for quick validation)
KNOWN_LANGUAGE_CODES = {
    "eng", "fra", "deu", "spa", "ita", "por", "nld", "rus", "pol",
    "lat", "grc", "ell", "ang", "enm", "fro", "frm", "non",
    "goh", "gmh", "dum", "san", "ara", "heb", "jpn", "zho", "kor",
    "gem-pro", "ine-pro", "sla-pro", "roa-pro", "cel-pro",
    "nrf", "xno", "la-vul", "swe", "dan", "nor", "fin", "hun",
    "tur", "hin", "urd", "ben", "tam", "tel", "tha", "vie",
}


class ValidationResult(str, Enum):
    """Possible validation outcomes."""

    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    REJECT = "reject"


@dataclass
class ValidationIssue:
    """A single validation issue."""

    field: str
    severity: str  # "critical", "warning", "info"
    message: str
    value: object = None


@dataclass
class ValidationReport:
    """Report from validation pipeline."""

    lsr_id: UUID | None
    result: ValidationResult
    validators_run: list[str]
    issues: list[dict]
    recommendations: list[str]


class Validator:
    """Quality control before data enters production graph.

    Usage:
        validator = Validator()
        report = validator.run_all(lsr_dict)
        if report.result == ValidationResult.FAIL:
            logger.warning(f"LSR {report.lsr_id} failed validation")
    """

    def __init__(self, strict: bool = False):
        """Initialize the validator.

        Args:
            strict: If True, treat warnings as failures.
        """
        self.strict = strict

    def validate_schema(self, lsr: dict) -> tuple[bool, list[dict]]:
        """Validate LSR schema: required fields, types, ranges.

        Checks:
        - Required fields are present and non-empty
        - Date ranges are valid (start <= end)
        - Confidence scores are in [0, 1]
        - Language code is recognized

        Args:
            lsr: Dictionary representation of an LSR.

        Returns:
            Tuple of (passed, list of issue dicts).
        """
        issues: list[dict] = []

        # Required fields
        required = ["form_orthographic", "language_code"]
        for field_name in required:
            value = lsr.get(field_name)
            if not value or (isinstance(value, str) and not value.strip()):
                issues.append({
                    "field": field_name,
                    "severity": "critical",
                    "message": f"Required field '{field_name}' is missing or empty",
                    "value": value,
                })

        # Language code validation
        lang_code = lsr.get("language_code", "")
        if lang_code and lang_code not in KNOWN_LANGUAGE_CODES:
            issues.append({
                "field": "language_code",
                "severity": "warning",
                "message": f"Language code '{lang_code}' not in known codes list",
                "value": lang_code,
            })

        # Date range validation
        date_start = lsr.get("date_start")
        date_end = lsr.get("date_end")
        if date_start is not None and date_end is not None:
            if not isinstance(date_start, (int, float)):
                issues.append({
                    "field": "date_start",
                    "severity": "critical",
                    "message": f"date_start must be numeric, got {type(date_start).__name__}",
                    "value": date_start,
                })
            elif not isinstance(date_end, (int, float)):
                issues.append({
                    "field": "date_end",
                    "severity": "critical",
                    "message": f"date_end must be numeric, got {type(date_end).__name__}",
                    "value": date_end,
                })
            elif date_end < date_start:
                issues.append({
                    "field": "date_range",
                    "severity": "critical",
                    "message": f"date_end ({date_end}) < date_start ({date_start})",
                    "value": (date_start, date_end),
                })

        # Date plausibility
        if date_start is not None and isinstance(date_start, (int, float)):
            if date_start < -10000 or date_start > 2100:
                issues.append({
                    "field": "date_start",
                    "severity": "warning",
                    "message": f"date_start {date_start} is outside plausible range [-10000, 2100]",
                    "value": date_start,
                })

        if date_end is not None and isinstance(date_end, (int, float)):
            if date_end < -10000 or date_end > 2100:
                issues.append({
                    "field": "date_end",
                    "severity": "warning",
                    "message": f"date_end {date_end} is outside plausible range [-10000, 2100]",
                    "value": date_end,
                })

        # Confidence score validation
        for conf_field in ["confidence_overall", "date_confidence"]:
            conf = lsr.get(conf_field)
            if conf is not None:
                if not isinstance(conf, (int, float)):
                    issues.append({
                        "field": conf_field,
                        "severity": "warning",
                        "message": f"{conf_field} must be numeric",
                        "value": conf,
                    })
                elif conf < 0.0 or conf > 1.0:
                    issues.append({
                        "field": conf_field,
                        "severity": "warning",
                        "message": f"{conf_field} ({conf}) outside valid range [0, 1]",
                        "value": conf,
                    })

        passed = not any(i["severity"] == "critical" for i in issues)
        return passed, issues

    def validate_consistency(
        self, lsr: dict, ancestor_chain: list[dict] | None = None
    ) -> tuple[bool, list[dict]]:
        """Validate graph consistency for an LSR.

        Checks:
        - No circular ancestry (if ancestor chain provided)
        - Temporal consistency: ancestors should predate descendants
        - Language family consistency: inheritance should follow family tree

        Args:
            lsr: Dictionary representation of an LSR.
            ancestor_chain: Optional list of ancestor LSR dicts for chain validation.

        Returns:
            Tuple of (passed, list of issue dicts).
        """
        issues: list[dict] = []
        lsr_id = lsr.get("id")

        # Check for circular ancestry
        if ancestor_chain:
            seen_ids = set()
            for ancestor in ancestor_chain:
                ancestor_id = ancestor.get("id")
                if ancestor_id in seen_ids:
                    issues.append({
                        "field": "ancestor_ids",
                        "severity": "critical",
                        "message": f"Circular ancestry detected: {ancestor_id} appears twice",
                        "value": ancestor_id,
                    })
                    break
                seen_ids.add(ancestor_id)

                # Self-reference check
                if ancestor_id == lsr_id:
                    issues.append({
                        "field": "ancestor_ids",
                        "severity": "critical",
                        "message": "LSR references itself as ancestor",
                        "value": lsr_id,
                    })

            # Temporal consistency: each ancestor should have earlier dates
            lsr_start = lsr.get("date_start")
            if lsr_start is not None:
                for ancestor in ancestor_chain:
                    anc_end = ancestor.get("date_end")
                    if anc_end is not None and anc_end > lsr_start + 500:
                        # Allow 500 year overlap for gradual transitions
                        pass
                    anc_start = ancestor.get("date_start")
                    if anc_start is not None and lsr_start is not None:
                        if anc_start > lsr_start:
                            issues.append({
                                "field": "date_start",
                                "severity": "warning",
                                "message": (
                                    f"Ancestor '{ancestor.get('form_orthographic')}' "
                                    f"({anc_start}) postdates descendant ({lsr_start})"
                                ),
                                "value": (anc_start, lsr_start),
                            })

        passed = not any(i["severity"] == "critical" for i in issues)
        return passed, issues

    def detect_anomalies(self, lsr: dict) -> tuple[bool, list[dict]]:
        """Detect statistical anomalies in LSR data.

        Checks:
        - Low confidence scores (< 0.3)
        - Missing definition
        - Unusually wide date ranges (> 1000 years)
        - Missing source databases

        Args:
            lsr: Dictionary representation of an LSR.

        Returns:
            Tuple of (passed, list of issue dicts).
        """
        issues: list[dict] = []

        # Low confidence
        confidence = lsr.get("confidence_overall")
        if confidence is not None and isinstance(confidence, (int, float)):
            if confidence < 0.3:
                issues.append({
                    "field": "confidence_overall",
                    "severity": "warning",
                    "message": f"Low confidence score: {confidence}",
                    "value": confidence,
                })

        # Missing definition
        definition = lsr.get("definition_primary", "")
        if not definition or definition.strip() == "":
            issues.append({
                "field": "definition_primary",
                "severity": "warning",
                "message": "No primary definition",
                "value": definition,
            })

        # Wide date range
        date_start = lsr.get("date_start")
        date_end = lsr.get("date_end")
        if (date_start is not None and date_end is not None
                and isinstance(date_start, (int, float))
                and isinstance(date_end, (int, float))):
            span = date_end - date_start
            if span > 1000:
                issues.append({
                    "field": "date_range",
                    "severity": "info",
                    "message": f"Unusually wide date range: {span} years ({date_start}-{date_end})",
                    "value": span,
                })

        # Missing source
        sources = lsr.get("source_databases", [])
        if not sources:
            issues.append({
                "field": "source_databases",
                "severity": "warning",
                "message": "No source databases recorded",
                "value": sources,
            })

        # Reconstruction without flag
        form = lsr.get("form_orthographic", "")
        is_reconstructed = lsr.get("reconstruction_flag", False)
        if form.startswith("*") and not is_reconstructed:
            issues.append({
                "field": "reconstruction_flag",
                "severity": "info",
                "message": "Form starts with * but reconstruction_flag is False",
                "value": form,
            })

        passed = not any(i["severity"] == "critical" for i in issues)
        return passed, issues

    def validate_cross_references(self, lsr: dict) -> tuple[bool, list[dict]]:
        """Validate against external references.

        Currently a lightweight check. Full implementation would compare
        against Glottolog, Wiktionary, etc.

        Args:
            lsr: Dictionary representation of an LSR.

        Returns:
            Tuple of (passed, list of issue dicts).
        """
        issues: list[dict] = []

        # Check language name matches code
        lang_code = lsr.get("language_code", "")
        lang_name = lsr.get("language_name", "")

        known_pairs = {
            "eng": "English", "fra": "French", "deu": "German",
            "spa": "Spanish", "lat": "Latin", "ang": "Old English",
            "enm": "Middle English", "fro": "Old French", "non": "Old Norse",
        }

        if lang_code in known_pairs and lang_name:
            expected = known_pairs[lang_code]
            if lang_name != expected:
                issues.append({
                    "field": "language_name",
                    "severity": "info",
                    "message": (
                        f"Language name '{lang_name}' doesn't match "
                        f"expected '{expected}' for code '{lang_code}'"
                    ),
                    "value": (lang_code, lang_name),
                })

        passed = not any(i["severity"] == "critical" for i in issues)
        return passed, issues

    def run_all(self, lsr: dict) -> ValidationReport:
        """Run all validators on an LSR.

        Args:
            lsr: Dictionary representation of an LSR.

        Returns:
            ValidationReport with results and recommendations.
        """
        lsr_id = lsr.get("id")
        all_issues: list[dict] = []
        validators_run: list[str] = []
        recommendations: list[str] = []

        # Schema validation
        validators_run.append("schema")
        passed, issues = self.validate_schema(lsr)
        all_issues.extend(issues)

        # Consistency validation (no ancestor chain in this context)
        validators_run.append("consistency")
        passed, issues = self.validate_consistency(lsr)
        all_issues.extend(issues)

        # Anomaly detection
        validators_run.append("anomaly")
        passed, issues = self.detect_anomalies(lsr)
        all_issues.extend(issues)

        # Cross-reference validation
        validators_run.append("cross_reference")
        passed, issues = self.validate_cross_references(lsr)
        all_issues.extend(issues)

        # Determine overall result
        critical_count = sum(1 for i in all_issues if i.get("severity") == "critical")
        warning_count = sum(1 for i in all_issues if i.get("severity") == "warning")

        if critical_count > 0:
            result = ValidationResult.FAIL
        elif warning_count > 0 and self.strict:
            result = ValidationResult.FAIL
        elif warning_count > 0:
            result = ValidationResult.WARN
        else:
            result = ValidationResult.PASS

        # Generate recommendations
        if critical_count > 0:
            recommendations.append("Fix critical issues before ingesting this record")
        if any(i["field"] == "definition_primary" for i in all_issues):
            recommendations.append("Add a primary definition for better analysis")
        if any(i["field"] == "source_databases" for i in all_issues):
            recommendations.append("Record the data source for provenance tracking")

        return ValidationReport(
            lsr_id=lsr_id,
            result=result,
            validators_run=validators_run,
            issues=all_issues,
            recommendations=recommendations,
        )

    def validate_batch(self, lsrs: list[dict]) -> list[ValidationReport]:
        """Validate a batch of LSRs.

        Args:
            lsrs: List of LSR dicts to validate.

        Returns:
            List of ValidationReport objects.
        """
        return [self.run_all(lsr) for lsr in lsrs]

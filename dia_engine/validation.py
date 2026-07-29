from __future__ import annotations

from typing import TYPE_CHECKING
from client_discovery.models import ClientIntake, ValidationIssue

if TYPE_CHECKING:
    from .schema import DomainManifest


def validate_intake(intake: ClientIntake, manifest: DomainManifest) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    req_fields = manifest.validation_rules.get("required_fields", {})
    for field_name, rule in req_fields.items():
        val = getattr(intake, field_name)
        if isinstance(val, str) and not val.strip():
            issues.append(
                ValidationIssue(
                    code=rule.get("code", f"missing_{field_name}"),
                    message=rule.get("message", f"{field_name} is required."),
                    severity=rule.get("severity", "warning"),
                )
            )

    req_sections = manifest.validation_rules.get("required_sections", {})
    for section_name, rule in req_sections.items():
        val = getattr(intake, section_name)
        if not val:
            issues.append(
                ValidationIssue(
                    code=rule.get("code", f"missing_{section_name}"),
                    message=rule.get("message", f"At least one {section_name} entry is required."),
                    severity=rule.get("severity", "warning"),
                )
            )

    return issues

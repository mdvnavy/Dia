from dataclasses import dataclass, field


@dataclass(frozen=True)
class ClientIntake:
    company_name: str = ""
    website: str = ""
    industry: str = ""
    size: str = ""
    years_in_business: str = ""
    location: str = ""
    tools: str = ""
    pain_points: list[str] = field(default_factory=list)
    goals: list[str] = field(default_factory=list)
    budget: str = ""
    decision_maker: str = ""
    start_date: str = ""
    tech_person: str = ""
    compliance: str = ""
    notes: str = ""


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    severity: str = "warning"


@dataclass(frozen=True)
class OpportunityScore:
    tier: str
    scope: str
    price_range: str
    timeline: str
    urgency: int
    budget_fit: int
    tech_readiness: int
    strategic_value: int
    total_score: int
    max_score: int
    reasons: list[str]

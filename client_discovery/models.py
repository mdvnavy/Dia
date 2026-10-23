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
    # Section 6, "Workflows and value" (dia-savant v1.0 §2.2). Q1-Q3 are required when the section is present.
    workflow_tasks: str = ""      # Q1: top 1-3 manual tasks, who, hours a week, people
    hourly_cost: str = ""         # Q2: loaded hourly cost or a salary band
    success_metric: str = ""      # Q3: the one number that would show this worked, and its value today
    process_owner: str = ""       # Q4
    systems_access: str = ""      # Q5
    data_readiness: str = ""      # Q6: where the data lives, how clean (1-5)
    exceptions: str = ""          # Q7
    sensitive_data: str = ""      # Q8 (replaces the free-text compliance line)
    past_attempts: str = ""       # Q9
    maintenance: str = ""         # Q10
    finance_signals: str = ""     # Q11 (optional, finance only)
    workflows_section: bool = False  # the questionnaire has a "6. Workflows and value" section


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

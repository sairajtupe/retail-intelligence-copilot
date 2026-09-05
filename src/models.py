"""Typed data models for copilot responses."""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Any

@dataclass
class EvidenceItem:
    id: str
    text: str
    source: str = ""

@dataclass
class PolicyCitation:
    rule_id: str
    summary: str
    document: str = ""

@dataclass
class Assumption:
    label: str
    value: str

@dataclass
class Recommendation:
    issue: str
    severity: str          # CRITICAL / HIGH / MEDIUM / LOW / INFO
    recommended_action: str
    reason: str
    evidence: list[EvidenceItem] = field(default_factory=list)
    assumptions: list[Assumption] = field(default_factory=list)
    confidence: str = "medium"
    source_ids: list[str] = field(default_factory=list)

@dataclass
class CopilotAnswer:
    answer: str
    intent: str
    status: str = "answered"          # answered | insufficient_data | data_quality_issue
    key_metrics: list[dict[str, Any]] = field(default_factory=list)
    recommendations: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    policy_citations: list[dict[str, Any]] = field(default_factory=list)
    assumptions: list[dict[str, Any]] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    confidence: str = "medium"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

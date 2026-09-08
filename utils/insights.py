# utils/insights.py
"""Structured run insights.

Every failure or warning a generation run produces is expressed as one
classified Insight so nobody has to guess whether a problem lives in the
input data or in the engine:

- DATA:   provable from the inputs alone (period budget, staff demand,
          capacity shortfall). Fix the data; no search will help.
- ENGINE: the input is feasible but search or verification failed
          (budget exhausted, conflicting set). The engine owes an answer.
- POLICY: allowed by an explicit decision yet worth surfacing loudly
          (overflow placements, out-of-hours TA placements).

An ENGINE insight may never be emitted while an unreported DATA blocker
exists - the preflight in utils/feasibility.py enforces that ordering.
"""

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Dict, List, Optional


class InsightCategory(str, Enum):
    DATA = "DATA"
    ENGINE = "ENGINE"
    POLICY = "POLICY"


class InsightSeverity(str, Enum):
    BLOCKER = "BLOCKER"
    WARNING = "WARNING"


def entity(kind: str, id, name: str = "") -> Dict:
    """A reference to the course/TA/room/cohort/block an insight is about."""
    return {"kind": kind, "id": id, "name": name}


@dataclass
class Insight:
    code: str
    category: InsightCategory
    severity: InsightSeverity
    phase: str  # preflight | solve | finalize
    verdict: str  # one-sentence human explanation
    entities: List[Dict] = field(default_factory=list)
    evidence: Dict = field(default_factory=dict)
    recommended_action: str = ""
    snapshot_ref: Optional[str] = None

    def to_dict(self) -> Dict:
        data = asdict(self)
        data["category"] = self.category.value
        data["severity"] = self.severity.value
        return data

    def render(self) -> str:
        """One human-readable line for logs and the progress panel."""
        label = f"{self.category.value}/{self.severity.value} {self.code}"
        line = f"{label}: {self.verdict}"
        if self.recommended_action:
            line += f" Fix: {self.recommended_action}"
        return line


def summarize(insights: List[Insight]) -> Dict:
    """Counts by category and severity, for failure details and summaries."""
    counts: Dict[str, int] = {}
    for insight in insights:
        key = f"{insight.category.value}_{insight.severity.value}"
        counts[key] = counts.get(key, 0) + 1
    return {
        "total": len(insights),
        "blockers": sum(
            1 for i in insights if i.severity is InsightSeverity.BLOCKER
        ),
        "warnings": sum(
            1 for i in insights if i.severity is InsightSeverity.WARNING
        ),
        "by_category": counts,
    }

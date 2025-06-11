# models/scheduling_attempt.py

from dataclasses import dataclass
from typing import Dict, Set

from models.block import Assignment


@dataclass
class SchedulingAttempt:
    """Represents a single scheduling attempt with its score"""

    assignments: Dict[str, Assignment]
    score: float
    unassigned_blocks: Set[str]

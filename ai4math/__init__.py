"""A dependency-free, evidence-aware mathematical research workbench."""

__version__ = "0.7.0"

from .schema import EVIDENCE_LEVELS, Claim, PhaseState, claim_id
from .workbench import Workbench
from .store import ResearchError

__all__ = ["Workbench", "ResearchError", "EVIDENCE_LEVELS", "Claim", "PhaseState", "claim_id"]

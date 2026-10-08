"""Stage 5: incident-triage agent (one loop) over chaos telemetry, scoped to one tenant.

Invariant: the agent can read its own tenant's telemetry and runbooks and propose allow-listed,
bounded, tenant-scoped remediations. It cannot execute anything; a human approval does that.
"""

from triage.agent import Diagnosis, Step, TriageResult, run_triage
from triage.tools import TriageError, TriageTools

__all__ = ["Diagnosis", "Step", "TriageError", "TriageResult", "TriageTools", "run_triage"]

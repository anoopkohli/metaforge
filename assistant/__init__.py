"""Stage 7: one assistant over documents, data and changes, for one signed-in person.

Invariant: the assistant can read only what its user can read, can only propose changes (the user
approves them), and everything it does is written to the tenant's audit log.
"""

from assistant.agent import AssistantResult, run_assistant
from assistant.tools import AssistantError, AssistantTools, Proposal

__all__ = ["AssistantError", "AssistantResult", "AssistantTools", "Proposal", "run_assistant"]

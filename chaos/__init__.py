"""Stage 5: deterministic fault injection over a simulated platform (three fault types).

Invariant: faults act only on a `ChaosEnvironment` instance. Nothing here patches global state.
"""

from chaos.env import FAULT_TYPES, METRICS, Alert, ChaosEnvironment, LogLine, MetadataChange

__all__ = ["FAULT_TYPES", "METRICS", "Alert", "ChaosEnvironment", "LogLine", "MetadataChange"]

"""Scripted stand-ins for the assistant's model, for offline use, evals and attacks.

`scripted_policy(actions)` replays a fixed list of actions, then finishes. With `gullible=True` it
also obeys every JSON action it finds inside an observation (documents, records), once per action
(or `repeat` times): the worst case for prompt injection, so attacks test the tools, not the model.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Sequence

from core.llm.base import FakeLLM, Message
from triage.reference import _embedded_actions

_OBS = re.compile(r'<observation tool="([^"]+)" step="\d+">\n(.*)\n</observation>', re.DOTALL)


def scripted_policy(actions: Sequence[dict], *, gullible: bool = False) -> FakeLLM:
    planned = [json.dumps(a) for a in actions]

    def step(messages: Sequence[Message]) -> str:
        sent = [m.content for m in messages if m.role == "assistant"]
        if gullible:
            emitted = Counter(json.dumps(json.loads(s), sort_keys=True) for s in sent)
            for m in messages:
                match = _OBS.search(m.content) if m.role == "user" else None
                if not match:
                    continue
                for instr in _embedded_actions(json.loads(match.group(2))):
                    action = {"tool": instr["tool"], "args": instr.get("args", {})}
                    if emitted[json.dumps(action, sort_keys=True)] < int(instr.get("repeat", 1)):
                        return json.dumps(action)
        done = sum(1 for s in sent if s in planned)
        if done < len(planned):
            return planned[done]
        return json.dumps({"tool": "finish", "args": {"answer": "Done.", "sources": []}})

    return FakeLLM(step)

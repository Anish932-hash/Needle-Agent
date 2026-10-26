"""Isolated inference worker for a Needle sub-agent. It never executes tools."""
from __future__ import annotations
import json
import sys
import tempfile
from pathlib import Path
from .agent import build_agent
from .capabilities import CapabilityStore
from .tools import Workspace


def main() -> None:
    payload = json.load(sys.stdin)
    role = payload["role"]  # label for audit only; keep the user's exact prompt unchanged
    prompt = payload["prompt"]
    with tempfile.TemporaryDirectory(prefix="needle-subagent-") as temp:
        workspace = Workspace(Path(temp) / "workspace")
        records = payload.get("capabilities") or {}
        if records:
            CapabilityStore(workspace).seed(records)
        agent = build_agent(workspace, weights=payload.get("weights"),
                            engine_path=payload.get("engine"))
        result = agent.complete(prompt, max_new_tokens=payload.get("max_new_tokens", 256))
        # The subprocess returns only proposals; no user files or tool results are executed here.
        sys.stdout.write(json.dumps({"function_calls": result.get("function_calls", []),
                                     "suppressed_calls": result.get("suppressed_calls", []),
                                     "confidence": result.get("confidence")}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        sys.stderr.write(f"Sub-agent failed: {type(exc).__name__}: {exc}\n")
        sys.exit(1)

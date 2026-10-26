"""Disposable, isolated environment for exercising the agent's supported tools."""
from __future__ import annotations

import tempfile
import math
import re
from pathlib import Path
from typing import Any

from .agent import dispatch, CONFIDENCE_AUTO
from .tools import Workspace
from .capabilities import CapabilityStore


def verify_tools() -> dict[str, Any]:
    """Run deterministic safety and behavior checks in a disposable workspace."""
    checks: list[dict[str, Any]] = []

    def check(name: str, action) -> None:
        try:
            detail = action()
            checks.append({"name": name, "passed": True, "detail": detail})
        except Exception as exc:
            checks.append({"name": name, "passed": False, "detail": str(exc)})

    with tempfile.TemporaryDirectory(prefix="needle-agent-verify-") as temp:
        root = Path(temp) / "workspace"
        ws = Workspace(root)

        def write_read():
            dispatch(ws, "write_file", {"path": "practice/note.txt", "text": "sandbox works"})
            value = dispatch(ws, "read_file", {"path": "practice/note.txt"})["text"]
            if value != "sandbox works":
                raise AssertionError("read-back content differed")
            return "write/read round trip succeeded"

        def listing():
            names = [item["name"] for item in dispatch(ws, "list_files", {"path": "practice"})["entries"]]
            if "note.txt" not in names:
                raise AssertionError("created file missing from listing")
            return "workspace listing sees created file"

        def search():
            result = dispatch(ws, "search_workspace", {"query": "sandbox works"})
            if not result["matches"]:
                raise AssertionError("search did not find seeded text")
            return "bounded text search succeeded"

        def traversal_blocked():
            try:
                dispatch(ws, "read_file", {"path": "../outside.txt"})
            except ValueError:
                return "outside path rejected"
            raise AssertionError("path traversal was not rejected")

        def unknown_tool_blocked():
            try:
                dispatch(ws, "run_shell", {})
            except ValueError:
                return "unknown tool rejected"
            raise AssertionError("unknown tool was accepted")

        check("workspace_write_read", write_read)
        check("workspace_listing", listing)
        check("workspace_search", search)
        check("path_confinement", traversal_blocked)
        check("tool_allowlist", unknown_tool_blocked)

    return {"passed": all(item["passed"] for item in checks), "checks": checks,
            "workspace": "temporary; deleted after verification"}


def practice_request(agent: Any, prompt: str,
                     saved_capabilities: dict[str, Any] | None = None,
                     fixtures: dict[str, str] | None = None) -> dict[str, Any]:
    """Generate a Needle proposal, execute supported calls only in a disposable workspace."""
    with tempfile.TemporaryDirectory(prefix="needle-agent-practice-") as temp:
        sandbox = Workspace(Path(temp) / "workspace")
        for fixture_path, fixture_text in (fixtures or {}).items():
            sandbox.write_file(fixture_path, fixture_text)
        if saved_capabilities:
            CapabilityStore(sandbox).seed(saved_capabilities)
        result = agent.complete(prompt)
        try:
            confidence = float(result.get("confidence", 0.0) or 0.0)
            if not math.isfinite(confidence) or not 0 <= confidence <= 1:
                confidence = 0.0
        except (TypeError, ValueError):
            confidence = 0.0
        raw_calls = result.get("function_calls", [])
        raw_held = result.get("suppressed_calls", [])
        calls = (raw_calls if isinstance(raw_calls, list) else []) + (raw_held if isinstance(raw_held, list) else [])
        if re.search(r"\b(create|build|add|teach|save|make|design|define|author)\b.*\b(capability|workflow|skill)\b", prompt, re.I):
            calls = [call for call in calls if isinstance(call, dict) and call.get("name") == "save_workflow"]
        if calls and isinstance(calls[0], dict) and calls[0].get("name") == "search_workspace":
            calls = calls[:1]
        wants_read = bool(re.search(r"\b(read|open|view|contents|what|when|who|where|which|how)\b", prompt, re.I))
        outcomes = []
        for call in calls:
            try:
                if not isinstance(call, dict):
                    raise ValueError("Tool call must be an object")
                output = dispatch(sandbox, call["name"], call.get("arguments", {}))
                outcomes.append({"name": call["name"], "arguments": call.get("arguments", {}),
                                 "passed": True, "result": output})
                if call["name"] == "search_workspace" and wants_read:
                    paths = sorted({item.get("path") for item in output.get("matches", [])
                                    if isinstance(item, dict) and isinstance(item.get("path"), str)})
                    if len(paths) == 1:
                        read_result = dispatch(sandbox, "read_file", {"path": paths[0]})
                        outcomes.append({"name": "read_file", "arguments": {"path": paths[0]},
                                         "passed": True, "result": read_result,
                                         "grounded_in_search": True})
                    else:
                        outcomes.append({"name": "grounded_read_followup", "passed": False,
                                         "error": "No unique file path in search results"})
            except Exception as exc:
                outcomes.append({"name": call.get("name", "unknown") if isinstance(call, dict) else "unknown",
                                 "passed": False, "error": str(exc)})
        execution_passed = bool(outcomes) and all(item["passed"] for item in outcomes)
        confidence_passed = confidence >= CONFIDENCE_AUTO
        return {"confidence": confidence, "reasoning": result.get("reasoning"),
                "calls": outcomes, "suppressed_calls": result.get("suppressed_calls", []),
                "execution_passed": execution_passed,
                "confidence_passed": confidence_passed,
                "review_required": not confidence_passed,
                "passed": execution_passed and confidence_passed,
                "workspace": "temporary; deleted after practice"}

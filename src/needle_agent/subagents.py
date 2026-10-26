"""Optional independent Needle instances with conservative proposal voting."""
from __future__ import annotations
import json
import math
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from .agent import validate_call
from .capabilities import CapabilityStore
from .tools import Workspace


class ConsensusAgent:
    """Wrap a primary model and optionally obtain independent subprocess proposals."""
    def __init__(self, primary: Any, *, count: int = 0, workspace: Workspace,
                 weights: str | None = None, engine: str | None = None,
                 timeout: int = 90):
        if not 0 <= count <= 2:
            raise ValueError("Sub-agent count must be 0, 1, or 2")
        self.primary = primary
        self.count = count
        self.workspace = workspace
        self.weights = os.path.abspath(weights) if weights else None
        self.engine = os.path.abspath(engine) if engine else None
        self.timeout = timeout

    def complete(self, prompt: str, max_new_tokens: int = 512) -> dict:
        primary = self.primary.complete(prompt, max_new_tokens=max_new_tokens)
        if self.count == 0:
            return primary
        snapshots = CapabilityStore(self.workspace).snapshot()
        roles = ["intent", "grounding"][:self.count]
        payloads = [{"prompt": prompt, "role": role, "weights": self.weights,
                     "engine": self.engine, "capabilities": snapshots,
                     "max_new_tokens": min(max_new_tokens, 256)} for role in roles]
        results: list[dict] = []
        failures: list[str] = []
        with ThreadPoolExecutor(max_workers=self.count) as pool:
            futures = [pool.submit(_run_worker, payload, self.timeout) for payload in payloads]
            for future in as_completed(futures):
                try:
                    results.append(future.result())
                except Exception as exc:
                    failures.append(str(exc))
        return merge_proposals(primary, results, requested=self.count, failures=failures)


def _run_worker(payload: dict, timeout: int) -> dict:
    completed = subprocess.run([sys.executable, "-m", "needle_agent.worker"],
                               input=json.dumps(payload, ensure_ascii=False),
                               text=True, capture_output=True, timeout=timeout,
                               check=False)
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or f"worker exit {completed.returncode}")[-1000:])
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("sub-agent returned invalid JSON") from exc
    if not isinstance(result, dict):
        raise RuntimeError("sub-agent response must be an object")
    return result


def merge_proposals(primary: dict, reviewers: list[dict], *, requested: int,
                    failures: list[str] | None = None) -> dict:
    """Keep only proposals independently repeated by a strict majority of successful models."""
    sources = [primary, *reviewers]
    votes: dict[str, dict[str, Any]] = {}
    for source_index, source in enumerate(sources):
        seen = set()
        active = source.get("function_calls", [])
        suppressed = source.get("suppressed_calls", [])
        proposals = (active if isinstance(active, list) else []) + (suppressed if isinstance(suppressed, list) else [])
        for proposal in proposals:
            if not isinstance(proposal, dict):
                continue
            try:
                name, arguments = validate_call(proposal.get("name"), proposal.get("arguments", {}))
            except ValueError:
                continue
            signature = json.dumps({"name": name, "arguments": arguments},
                                   sort_keys=True, ensure_ascii=False, separators=(",", ":"))
            seen.add(signature)
            record = votes.setdefault(signature, {"call": {"name": name, "arguments": arguments},
                                                   "voters": set(), "scores": []})
            record["voters"].add(source_index)
            try:
                score = float(source.get("confidence") or 0.0)
                if math.isfinite(score) and 0 <= score <= 1:
                    record["scores"].append(score)
            except (TypeError, ValueError):
                pass
    # Compare against all configured instances, not merely successful ones; otherwise
    # a primary-only result would incorrectly pass when every reviewer fails.
    threshold = (requested + 1) // 2 + 1
    selected = [record for record in votes.values() if len(record["voters"]) >= threshold]
    output = dict(primary)
    output["function_calls"] = [record["call"] for record in selected]
    output["suppressed_calls"] = []
    if selected:
        scores = [score for record in selected for score in record["scores"]]
        # The least confident supporting instance controls the approval threshold.
        output["confidence"] = min(scores) if scores else 0.0
    else:
        output["confidence"] = 0.0
    output["subagent_review"] = {"requested": requested, "successful": len(reviewers),
                                 "agreement_threshold": threshold,
                                 "agreed_proposals": len(selected),
                                 "failures": failures or []}
    return output

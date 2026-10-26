"""JSON-lines bridge for the loopback Node web UI.

The bridge owns model execution and tool dispatch. The browser only receives
validated proposals; every tool action must be approved explicitly in the UI.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import sys
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

from .agent import build_agent, dispatch, validate_call
from .cloud import CloudAgent, CloudSettings, detect_models
from .context import AutomaticContextManager
from .conversation import ConversationMemory
from .evolution import EvolutionMemory
from .request_guard import ground_explicit_write
from .tools import Workspace


MAX_ACTIONS_PER_TURN = 6
ALLOWED_MODES = {"local", "cloud", "hybrid"}


def _signature(name: str, arguments: dict[str, Any]) -> str:
    return json.dumps({"name": name, "arguments": arguments}, sort_keys=True,
                      ensure_ascii=False, default=str)


def _verify_tool_result(workspace: Workspace, name: str,
                        arguments: dict[str, Any], output: Any) -> dict[str, Any]:
    """Check deterministic local postconditions; do not mistake model review for proof."""
    try:
        if name == "write_file":
            reread = workspace.read_file(arguments["path"])
            passed = reread.get("text") == arguments["text"]
            return {"status": "passed" if passed else "failed",
                    "method": "read-back comparison",
                    "path": str(reread.get("path", arguments["path"])),
                    "bytes": len(reread.get("text", "").encode("utf-8"))}
        if name == "read_file":
            reread = workspace.read_file(arguments["path"])
            passed = (isinstance(output, dict)
                      and output.get("path") == reread.get("path")
                      and output.get("text") == reread.get("text"))
            return {"status": "passed" if passed else "failed",
                    "method": "read-back comparison",
                    "path": str(reread.get("path", arguments["path"])),
                    "bytes": len(reread.get("text", "").encode("utf-8"))}
        checks = {
            "system_info": (isinstance(output, dict)
                            and {"system", "python", "architecture"} <= set(output)),
            "list_files": (isinstance(output, dict)
                           and isinstance(output.get("entries"), list)
                           and isinstance(output.get("path"), str)),
            "search_workspace": (isinstance(output, dict)
                                 and isinstance(output.get("matches"), list)
                                 and output.get("query") == arguments.get("query")),
        }
        if name in checks:
            return {"status": "passed" if checks[name] else "failed",
                    "method": "output-shape check"}
        return {"status": "observed",
                "method": "local tool returned; semantic result was not independently checked"}
    except (KeyError, OSError, ValueError, TypeError) as exc:
        return {"status": "failed", "method": f"local postcondition check failed: {exc}"}


def _select_proposals(prompt: str, calls: list[Any], held: list[Any]) -> list[Any]:
    """Apply the CLI's capability-creation filter so examples aren't also executed."""
    verbs = r"\b(create|build|add|teach|save|make|design|define|author)\b"
    nouns = r"\b(capability|workflow|skill)\b"
    creation = bool(re.search(verbs + r".*" + nouns, prompt, re.I)
                    or re.search(nouns + r".*" + verbs, prompt, re.I))
    combined = calls + held
    if creation:
        return [call for call in combined
                if isinstance(call, dict) and call.get("name") == "save_workflow"]
    return [call for call in combined
            if not isinstance(call, dict) or call.get("name") != "save_workflow"]


def _safe_json(value: Any) -> Any:
    """Ensure unusual model/tool objects cannot break the JSON-lines protocol."""
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return str(value)


class WebBridge:
    def __init__(self, workspace_path: str, weights: str | None = None,
                 engine: str | None = None, context_tokens: int = 1600):
        self.workspace = Workspace(Path(workspace_path))
        self.weights = weights
        self.engine = engine
        self.context_tokens = context_tokens
        self.memory = EvolutionMemory(self.workspace.root / ".needle-agent-memory.json")
        self.conversation = ConversationMemory(self.workspace.root / ".needle-agent-conversation.json")
        self.context = AutomaticContextManager(self.conversation, self.memory, context_tokens)
        self.settings = CloudSettings()
        self.cloud_values = self.settings.load()
        self.agent = None
        self.pending: dict[str, dict[str, Any]] = {}
        self.active_turn: dict[str, Any] | None = None

    def _get_agent(self):
        if self.agent is None:
            self.agent = build_agent(self.workspace, weights=self.weights, engine_path=self.engine)
        return self.agent

    def _public_settings(self) -> dict[str, Any]:
        return {
            "base_url": self.cloud_values.get("base_url", "https://api.openai.com/v1"),
            "model": self.cloud_values.get("model", ""),
            "key_configured": bool(self.cloud_values.get("api_key", "").strip()),
        }

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(request, dict):
            raise ValueError("Request must be a JSON object")
        operation = request.get("op")
        payload = request.get("payload", {})
        if not isinstance(payload, dict):
            raise ValueError("Request payload must be a JSON object")

        if operation == "status":
            return {"workspace": str(self.workspace.root),
                    "active_turn": bool(self.active_turn),
                    "pending_actions": len(self.pending),
                    "cloud_configured": bool(
                        self.cloud_values.get("model") and self.cloud_values.get("api_key")
                    ),
                    "settings": self._public_settings()}
        if operation == "settings":
            return {"settings": self._public_settings()}
        if operation == "detect_models":
            base = payload.get("base_url", "")
            key = payload.get("api_key") or self.cloud_values.get("api_key", "")
            models = detect_models(base, key)
            self.cloud_values.update({"base_url": base.rstrip("/"), "api_key": key})
            return {"models": models}
        if operation == "save_settings":
            base = payload.get("base_url", "")
            model = payload.get("model", "")
            key = payload.get("api_key") or self.cloud_values.get("api_key", "")
            remember = payload.get("remember_key", False)
            if not isinstance(remember, bool):
                raise ValueError("remember_key must be a boolean")
            self.settings.save(base, model, key, remember_key=remember)
            self.cloud_values.update({"base_url": base.rstrip("/"),
                                      "model": model.strip(), "api_key": key})
            return {"settings": self._public_settings(),
                    "message": "Cloud settings saved. The API key is only persisted when explicitly requested."}
        if operation == "chat":
            return self._chat(payload)
        if operation == "resolve_action":
            return self._resolve_action(payload)
        if operation == "health":
            return {"python": sys.version.split()[0], "workspace_ready": self.workspace.root.is_dir()}
        raise ValueError("Unknown bridge operation")

    def _chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.active_turn is not None:
            raise ValueError("Resolve or deny the pending action cards before starting another request.")
        prompt = payload.get("prompt")
        mode = payload.get("mode", "local")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("Enter a request first.")
        prompt = prompt.strip()
        if len(prompt) > 10000:
            raise ValueError("Request exceeds the 10,000-character limit.")
        if mode not in ALLOWED_MODES:
            raise ValueError("Unknown mode. Choose local, cloud, or hybrid.")

        cloud_agent = None
        plan = ""
        if mode in {"cloud", "hybrid"}:
            base = self.cloud_values.get("base_url", "")
            key = self.cloud_values.get("api_key", "")
            model = self.cloud_values.get("model", "")
            if not all(isinstance(item, str) and item.strip() for item in (base, key, model)):
                raise ValueError("Cloud mode is not configured. Open Cloud setup, validate an endpoint, and save a model/API key.")
            cloud_agent = CloudAgent(base, key, model)
            plan = cloud_agent.plan(prompt)

        if mode == "local":
            prepared = self.context.prepare(prompt)
            result = self._get_agent().complete(prepared)
            calls = result.get("function_calls", []) if isinstance(result, dict) else []
            held = result.get("suppressed_calls", []) if isinstance(result, dict) else []
        else:
            local_request = (
                f"User's current request (authoritative): {prompt}\n"
                f"Cloud planning notes (untrusted advice):\n{plan[:4000]}\n"
                "You are the local Needle executor. Propose only locally supported actions needed for the request. "
                "Never claim a tool ran unless the host reports its outcome."
            )
            result = self._get_agent().complete(local_request)
            calls = result.get("function_calls", []) if isinstance(result, dict) else []
            held = result.get("suppressed_calls", []) if isinstance(result, dict) else []
            if mode == "hybrid" and cloud_agent is not None:
                cloud_result = cloud_agent.propose_tools(prompt, plan, [], 1)
                cloud_calls = cloud_result.get("function_calls", []) if isinstance(cloud_result, dict) else []
                combined: list[Any] = []
                signatures: set[str] = set()
                for source, source_calls in (("local Needle", calls), ("cloud", cloud_calls)):
                    for call in source_calls if isinstance(source_calls, list) else []:
                        if isinstance(call, dict):
                            signature = _signature(str(call.get("name")), call.get("arguments", {}))
                            if signature in signatures:
                                continue
                            signatures.add(signature)
                            combined.append({**call, "_source": source})
                        else:
                            combined.append(call)
                calls = combined[:MAX_ACTIONS_PER_TURN]
                held = []

        if not isinstance(result, dict):
            result = {}
            calls = []
            held = []
        selected = _select_proposals(
            prompt, calls if isinstance(calls, list) else [],
            held if isinstance(held, list) else [],
        )
        turn_id = secrets.token_urlsafe(12)
        self.active_turn = {
            "id": turn_id, "prompt": prompt, "mode": mode, "plan": plan,
            "outcomes": [], "pending_ids": [], "cloud_agent": cloud_agent,
        }
        rejected: list[dict[str, Any]] = []
        signatures: set[str] = set()
        proposals: list[dict[str, Any]] = []
        for call in selected[:MAX_ACTIONS_PER_TURN]:
            if not isinstance(call, dict):
                rejected.append({"status": "rejected", "reason": "Malformed proposal; expected an object."})
                continue
            try:
                name, arguments = validate_call(call.get("name"), call.get("arguments", {}))
                arguments, corrected = ground_explicit_write(prompt, name, arguments)
                # Validate paths before presenting approval, and repeat validation at execution.
                if name in {"read_file", "write_file", "list_files", "search_workspace"}:
                    self.workspace.resolve(arguments.get("path", "."))
                signature = _signature(name, arguments)
                if signature in signatures:
                    continue
                signatures.add(signature)
                action_id = secrets.token_urlsafe(18)
                pending = {
                    "id": action_id, "turn_id": turn_id, "name": name,
                    "arguments": arguments, "source": call.get("_source", "local Needle"),
                    "corrected": corrected, "cloud": mode in {"cloud", "hybrid"},
                }
                self.pending[action_id] = pending
                self.active_turn["pending_ids"].append(action_id)
                proposals.append({
                    "id": action_id, "name": name, "arguments": arguments,
                    "source": pending["source"], "cloud_data_notice": pending["cloud"],
                    "corrected_to_match_request": corrected,
                })
            except (ValueError, TypeError) as exc:
                rejected.append({"status": "rejected", "reason": str(exc),
                                 "proposal": str(call.get("name", "unknown"))})

        self.active_turn["outcomes"].extend(rejected)
        assistant_message = result.get("assistant_message", "")
        if not isinstance(assistant_message, str):
            assistant_message = ""
        response = {
            "turn_id": turn_id, "mode": mode, "assistant_message": assistant_message,
            "advisory_plan": plan, "proposals": proposals, "rejected": rejected,
            "message": ("Actions are proposals only. Nothing runs until you approve each action."
                        if proposals else "No supported action was proposed; nothing was executed."),
        }
        if not proposals:
            response["turn_result"] = self._finish_turn()
        return response

    def _resolve_action(self, payload: dict[str, Any]) -> dict[str, Any]:
        action_id = payload.get("id")
        approved = payload.get("approved")
        if not isinstance(action_id, str) or not isinstance(approved, bool):
            raise ValueError("Action id and a boolean approval decision are required.")
        pending = self.pending.get(action_id)
        if pending is None:
            raise ValueError("This action is no longer pending or has already been resolved.")
        turn = self.active_turn
        if turn is None or turn["id"] != pending["turn_id"]:
            raise ValueError("The action does not belong to the active request.")

        result: dict[str, Any] = {"id": action_id, "name": pending["name"],
                                  "approved": approved}
        if not approved:
            outcome = {"tool": pending["name"], "arguments": pending["arguments"],
                       "status": "denied", "reason": "User denied the proposed action."}
            turn["outcomes"].append(outcome)
            result["status"] = "denied"
        else:
            try:
                # Validate again at the trust boundary immediately before dispatch.
                name, arguments = validate_call(pending["name"], pending["arguments"])
                if name in {"read_file", "write_file", "list_files", "search_workspace"}:
                    self.workspace.resolve(arguments.get("path", "."))
                output = dispatch(self.workspace, name, arguments)
                verification = _verify_tool_result(self.workspace, name, arguments, output)
                outcome = {"tool": name, "arguments": arguments, "status": "succeeded",
                           "result": output, "verification": verification}
                turn["outcomes"].append(outcome)
                result.update({"status": "succeeded", "output": _safe_json(output),
                               "verification": verification})
                if name == "save_workflow":
                    # The model tool descriptions include the available workflow names.
                    self.agent = None
            except Exception as exc:
                outcome = {"tool": pending["name"], "arguments": pending["arguments"],
                           "status": "failed", "error": f"{type(exc).__name__}: {exc}"}
                turn["outcomes"].append(outcome)
                result.update({"status": "failed", "error": outcome["error"]})

        self.pending.pop(action_id, None)
        turn["pending_ids"] = [item for item in turn["pending_ids"] if item != action_id]
        if not turn["pending_ids"]:
            result["turn_result"] = self._finish_turn()
        else:
            result["pending_actions"] = len(turn["pending_ids"])
        return result

    def _finish_turn(self) -> dict[str, Any]:
        turn = self.active_turn
        if turn is None:
            return {"completed": True}
        outcomes = turn["outcomes"]
        concise = []
        for item in outcomes:
            tool = item.get("tool", item.get("proposal", "action"))
            status = item.get("status", "unknown")
            detail = item.get("reason") or item.get("error") or ""
            concise.append(f"{tool}: {status}" + (f" ({detail})" if detail else ""))
        summary = "; ".join(concise) if concise else "No supported action executed."
        try:
            self.conversation.add(turn["prompt"], summary)
        except Exception as exc:
            summary += f"; conversation memory could not be saved ({type(exc).__name__})"

        review = ""
        if turn["mode"] in {"cloud", "hybrid"} and turn.get("cloud_agent") is not None:
            try:
                # The user was warned on the approval card that results may be shared with the provider.
                review = turn["cloud_agent"].review(
                    turn["prompt"], turn.get("plan", ""), outcomes[-MAX_ACTIONS_PER_TURN:]
                )
            except Exception as exc:
                review = f"Cloud review unavailable ({type(exc).__name__}): {exc}"
        completed = {"completed": True, "summary": summary, "review": review,
                     "outcomes": [
                         {"tool": item.get("tool", item.get("proposal", "action")),
                          "status": item.get("status", "unknown"),
                          "reason": item.get("reason", item.get("error", "")),
                          "verification": item.get("verification")}
                         for item in outcomes
                     ]}
        self.active_turn = None
        return completed


def main() -> None:
    parser = argparse.ArgumentParser(description="Needle Agent local web UI bridge")
    parser.add_argument("--workspace", default="./needle-workspace")
    parser.add_argument("--weights")
    parser.add_argument("--engine")
    parser.add_argument("--context-tokens", type=int, default=1600)
    args = parser.parse_args()
    bridge = WebBridge(args.workspace, args.weights, args.engine, args.context_tokens)
    for line in sys.stdin:
        request_id = None
        try:
            request = json.loads(line)
            if isinstance(request, dict):
                request_id = request.get("id")
            # Keep third-party/native runtime stdout away from the JSON-lines protocol.
            with redirect_stdout(sys.stderr):
                result = bridge.handle(request)
            response = {"id": request_id, "ok": True, "result": _safe_json(result)}
        except Exception as exc:
            response = {"id": request_id, "ok": False,
                        "error": f"{type(exc).__name__}: {exc}"}
        try:
            sys.stdout.write(json.dumps(response, ensure_ascii=False, default=str) + "\n")
            sys.stdout.flush()
        except (BrokenPipeError, OSError):
            return


if __name__ == "__main__":
    main()

from __future__ import annotations
import argparse
import json
import math
import re
from pathlib import Path
from .agent import build_agent, dispatch, validate_call, CONFIDENCE_AUTO
from .tools import Workspace
from .evolution import EvolutionMemory
from .sandbox import practice_request, verify_tools
from .conversation import ConversationMemory
from .context import AutomaticContextManager
from .capabilities import CapabilityStore
from .subagents import ConsensusAgent


def select_proposals(prompt: str, calls: list, held: list) -> tuple[list, bool]:
    """For explicit capability creation, never also execute described example actions."""
    verbs = r"\b(create|build|add|teach|save|make|design|define|author)\b"
    nouns = r"\b(capability|workflow|skill)\b"
    is_creation = bool(re.search(verbs + r".*" + nouns, prompt, re.I)
                       or re.search(nouns + r".*" + verbs, prompt, re.I))
    proposals = calls + held
    if is_creation:
        proposals = [call for call in proposals
                     if isinstance(call, dict) and call.get("name") == "save_workflow"]
    else:
        proposals = [call for call in proposals
                     if not isinstance(call, dict) or call.get("name") != "save_workflow"]
    return proposals, is_creation


def is_fact_question(prompt: str) -> bool:
    return bool(re.search(r"\b(what|when|who|where|which|how)\b", prompt, re.I))


def explicit_dates(text: str) -> list[str]:
    """Return only date-like strings literally present in source text."""
    months = "January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec"
    patterns = [
        rf"\b(?:{months})\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+\d{{4}})?\b",
        rf"\b\d{{1,2}}\s+(?:{months})(?:\s+\d{{4}})?\b",
        r"\b\d{4}-\d{1,2}-\d{1,2}\b",
        r"\b\d{1,2}/\d{1,2}/\d{2,4}\b",
    ]
    found = []
    for pattern in patterns:
        found.extend(re.findall(pattern, text, flags=re.I))
    return list(dict.fromkeys(value.strip() for value in found))


def explicit_system_capability(prompt: str) -> tuple[str, str] | None:
    """Recognize a named, explicit OS environment-audit workflow request."""
    if not re.search(r"\b(operating system|\bos\b|python|architecture|environment)\b", prompt, re.I):
        return None
    match = re.search(r"\b(?:capability|workflow|skill)\s+(?:called|named)\s+([a-z][a-z0-9_]{1,31})\b", prompt, re.I)
    if not match:
        return None
    name = match.group(1).lower()
    return name, "Report operating system, OS release, CPU architecture, Python version, and workspace root."


def requested_reminder_path(prompt: str) -> str | None:
    """Recognize a clearly requested .txt reminder destination; never infer a path."""
    if not re.search(r"\b(save|write|create|put)\b", prompt, re.I) or not re.search(r"\breminder\b", prompt, re.I):
        return None
    match = re.search(r"\b(?:in|as|to|called|named)\s+([A-Za-z0-9_./-]+\.txt)\b", prompt, re.I)
    return match.group(1) if match else None


def grounded_read_path(prompt: str, search_result: dict) -> tuple[str | None, str | None]:
    """Use a search result—not model-invented paths—to satisfy an explicit read intent."""
    if not re.search(r"\b(read|open|view|contents|what|when|who|where|which|how)\b", prompt, re.I):
        return None, None
    matches = search_result.get("matches", []) if isinstance(search_result, dict) else []
    paths = sorted({item.get("path") for item in matches
                    if isinstance(item, dict) and isinstance(item.get("path"), str)})
    if len(paths) == 1:
        return paths[0], None
    if len(paths) > 1:
        return None, "Search matched multiple files; I did not guess which one to read. Narrow the request."
    return None, "No matching file was found to read."


def main():
    parser = argparse.ArgumentParser(description="Local, safety-first Needle 3 OS assistant")
    parser.add_argument("--workspace", default="./needle-workspace", help="Confine file tools to this directory")
    parser.add_argument("--context-tokens", type=int, default=1600, help="Approximate hard prompt budget for automatic context management")
    parser.add_argument("--weights", help="Path to a local Needle .cact model archive (avoids downloading weights)")
    parser.add_argument("--engine", help="Path to a compatible local Needle 3 shared library (Linux: libneedle3.so)")
    parser.add_argument("--subagents", type=int, choices=range(0, 3), default=0,
                        help="Independent Needle subprocess reviewers per request (0–2; requires more RAM/CPU)")
    args = parser.parse_args()
    if args.context_tokens < 256:
        parser.error("--context-tokens must be at least 256")
    workspace = Workspace(Path(args.workspace))
    memory = EvolutionMemory(workspace.root / ".needle-agent-memory.json")
    conversation = ConversationMemory(workspace.root / ".needle-agent-conversation.json")
    context_manager = AutomaticContextManager(conversation, memory, args.context_tokens)
    def load_agent():
        primary = build_agent(workspace, weights=args.weights, engine_path=args.engine)
        return ConsensusAgent(primary, count=args.subagents, workspace=workspace,
                              weights=args.weights, engine=args.engine)

    try:
        agent = load_agent()
    except (RuntimeError, OSError, ValueError) as exc:
        parser.error(str(exc))

    print(f"Needle Agent · workspace: {workspace.root}\nCommands: /verify, /practice <request>, /capabilities, /run-capability <name> [JSON inputs], /forget-capability <name>, /learn <preference>, /memory, /context, /clear-memory, /forget, /quit. Ask Needle to create a reusable capability; creation and execution require approval.")
    while True:
        try:
            prompt = input("\nYou> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            return
        if not prompt:
            continue
        if prompt == "/quit":
            print("Goodbye.")
            return
        if prompt == "/verify":
            report = verify_tools()
            print(json.dumps(report, ensure_ascii=False, indent=2))
            conversation.add(prompt, "Deterministic verification " + ("passed" if report["passed"] else "failed"))
            continue
        if prompt.startswith("/practice "):
            report = practice_request(agent, prompt[len("/practice "):],
                                     {item["name"]: item for item in CapabilityStore(workspace).list()})
            print(json.dumps(report, ensure_ascii=False, indent=2))
            conversation.add(prompt, "Sandbox practice " + ("passed" if report["passed"] else "needs review or failed"))
            continue
        if prompt == "/capabilities":
            print(json.dumps(CapabilityStore(workspace).list(), ensure_ascii=False, indent=2))
            continue
        if prompt.startswith("/forget-capability "):
            name = prompt[len("/forget-capability "):].strip()
            if not name:
                print("Provide the workflow name to forget.")
                continue
            if input(f"Permanently delete reusable workflow {name!r}? [y/N] ").strip().lower() not in {"y", "yes"}:
                print("Cancelled.")
                continue
            try:
                CapabilityStore(workspace).delete(name)
                agent = load_agent()
                print(f"Deleted workflow {name!r}.")
            except ValueError as exc:
                print(f"Cannot delete workflow: {exc}")
            continue
        if prompt.startswith("/run-capability "):
            parts = prompt.split(maxsplit=2)
            name = parts[1] if len(parts) > 1 else ""
            inputs = parts[2] if len(parts) > 2 else "{}"
            if not name:
                print("Provide a workflow name.")
                continue
            try:
                saved = CapabilityStore(workspace).get(name)
                print("Workflow plan:")
                for i, step in enumerate(saved["definition"]["steps"], 1):
                    print(f"  {i}. {step['tool']} {json.dumps(step['arguments'], ensure_ascii=False)}")
            except (KeyError, ValueError) as exc:
                print(f"Cannot inspect workflow: {exc}")
                continue
            if input("Run this workflow? [y/N] ").strip().lower() not in {"y", "yes"}:
                print("Cancelled.")
                continue
            try:
                result = CapabilityStore(workspace).execute(name, inputs)
                print(json.dumps(result, ensure_ascii=False, indent=2))
            except (ValueError, OSError) as exc:
                print(f"Workflow failed: {exc}")
            continue
        if prompt == "/learn":
            print("Usage: /learn <preference or correction>")
            continue
        if prompt.startswith("/learn "):
            try:
                memory.learn(prompt[len("/learn "):])
                print("Saved this preference for future turns in this workspace.")
            except ValueError as exc:
                print(f"Could not save preference: {exc}")
            continue
        if prompt == "/memory":
            print(json.dumps(memory.lessons(), ensure_ascii=False, indent=2))
            continue
        if prompt == "/context":
            print(json.dumps(context_manager.diagnostics(), ensure_ascii=False, indent=2))
            continue
        if prompt in {"/clear-memory", "/forget"}:
            memory.forget()
            conversation.clear()
            print("Cleared saved preferences and conversation history for this workspace.")
            continue
        try:
            request = context_manager.prepare(prompt)
            result = agent.complete(request)
            review = result.get("subagent_review")
            if review:
                print("Sub-agent review:", json.dumps(review, ensure_ascii=False))
            raw_calls = result.get("function_calls", [])
            raw_held = result.get("suppressed_calls", [])
            calls = raw_calls if isinstance(raw_calls, list) else []
            held = raw_held if isinstance(raw_held, list) else []
            turn_outcomes = []
            calls, creating_capability = select_proposals(prompt, calls, held)
            # Do not let stale model context turn a read request into a repeated write.
            write_intent = re.search(r"\b(create|write|save|update|edit|replace)\b", prompt, re.I)
            if not write_intent:
                calls = [call for call in calls
                         if not isinstance(call, dict) or call.get("name") != "write_file"]
            # Needle's calibrated floor can move valid proposals into suppressed_calls.
            # Surface remaining proposals for explicit approval instead of discarding them.
            try:
                confidence = float(result.get("confidence", 0.0) or 0.0)
                if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
                    confidence = 0.0
            except (TypeError, ValueError):
                confidence = 0.0
            search_then_read = bool(re.search(r"\b(read|open|view|contents|what|when|who|where|which|how)\b", prompt, re.I))
            if calls and isinstance(calls[0], dict) and calls[0].get("name") == "search_workspace":
                # Never execute a second, stale path proposal from the same model turn.
                # A grounded read is added below only when the prompt asks for one.
                calls = calls[:1]
            if not calls:
                fallback = explicit_system_capability(prompt) if creating_capability else None
                if fallback:
                    name, description = fallback
                    print(f"Proposed tested workflow: save_workflow(name={name!r}, recipe='system_check')")
                    if input("Create and sandbox-test this capability? [y/N] ").strip().lower() in {"y", "yes"}:
                        try:
                            created = CapabilityStore(workspace).create_from_recipe(name, description, "system_check")
                            agent = load_agent()
                            print(json.dumps(created, ensure_ascii=False, indent=2))
                            print("Capability saved; it has not been run on the real workspace.")
                            conversation.add(prompt, "Created and sandbox-tested OS environment capability; not run.")
                        except (ValueError, OSError) as exc:
                            print(f"Capability was not created: {exc}")
                            conversation.add(prompt, "OS capability creation failed")
                    else:
                        print("Cancelled; no capability was saved.")
                        conversation.add(prompt, "Capability creation declined")
                elif creating_capability:
                    print("Needle did not produce a supported capability proposal; nothing was saved or run.")
                    conversation.add(prompt, "No action executed; no supported capability proposal.")
                else:
                    print("No supported action was proposed. Nothing was executed.")
                    if held:
                        print("Needle withheld a low-confidence proposal; no action taken.")
                    conversation.add(prompt, "No action executed; no supported tool call.")
                continue
            for call in calls:
                try:
                    if not isinstance(call, dict):
                        raise ValueError("Tool call must be an object")
                    name, arguments = validate_call(call.get("name"), call.get("arguments", {}))
                except ValueError as exc:
                    print(f"Rejected malformed or unsupported model proposal: {exc}")
                    turn_outcomes.append("malformed proposal rejected")
                    continue
                print(f"Proposed: {name}({json.dumps(arguments, ensure_ascii=False)}) · confidence {confidence:.2f}")
                if name == "run_capability":
                    try:
                        saved = CapabilityStore(workspace).get(arguments["name"])
                        print("Workflow plan:")
                        for i, step in enumerate(saved["definition"]["steps"], 1):
                            print(f"  {i}. {step['tool']} {json.dumps(step['arguments'], ensure_ascii=False)}")
                    except (KeyError, ValueError) as exc:
                        print(f"Cannot inspect workflow: {exc}")
                        turn_outcomes.append("workflow rejected")
                        continue
                # Low confidence, file writes, and capability changes/execution always need approval.
                if name in {"write_file", "save_workflow", "run_capability"} or confidence < CONFIDENCE_AUTO:
                    answer = input("Run this action? [y/N] ").strip().lower()
                    if answer not in {"y", "yes"}:
                        print("Skipped.")
                        turn_outcomes.append(f"{name} declined")
                        continue
                try:
                    tool_result = dispatch(workspace, name, arguments)
                    print(json.dumps(tool_result, ensure_ascii=False, indent=2))
                    turn_outcomes.append(f"{name} succeeded")
                    if name == "save_workflow":
                        agent = load_agent()
                        print("Capability is now available to Needle for subsequent requests.")
                    if name == "search_workspace" and search_then_read:
                        path, notice = grounded_read_path(prompt, tool_result)
                        if notice:
                            print(notice)
                        elif path:
                            read_args = {"path": path}
                            print(f"Grounded follow-up: read_file({json.dumps(read_args)})")
                            approved = confidence >= CONFIDENCE_AUTO or input("Read this matched file? [y/N] ").strip().lower() in {"y", "yes"}
                            if approved:
                                read_result = dispatch(workspace, "read_file", read_args)
                                print(json.dumps(read_result, ensure_ascii=False, indent=2))
                                turn_outcomes.append("grounded read_file succeeded")
                                if is_fact_question(prompt):
                                    if re.search(r"\b(date|when)\b", prompt, re.I):
                                        dates = explicit_dates(read_result["text"])
                                        if len(dates) == 1:
                                            print(f"Answer from {path}: {dates[0]}")
                                        elif dates:
                                            print(f"Multiple dates in {path}: {', '.join(dates)}. I did not choose one.")
                                        else:
                                            print(f"No explicit date found in {path}; see the source text above.")
                                    else:
                                        print(f"Source text from {path} is shown above. I won't guess at a summary or fact not safely extracted.")
                                reminder_path = requested_reminder_path(prompt)
                                if reminder_path:
                                    write_args = {"path": reminder_path,
                                                  "text": "Reminder: " + read_result["text"].strip()}
                                    try:
                                        validate_call("write_file", write_args)
                                        workspace.resolve(reminder_path)
                                        print(f"Proposed grounded reminder: write_file({json.dumps(write_args, ensure_ascii=False)})")
                                        if input("Save this reminder? [y/N] ").strip().lower() in {"y", "yes"}:
                                            saved_result = dispatch(workspace, "write_file", write_args)
                                            print(json.dumps(saved_result, ensure_ascii=False, indent=2))
                                            turn_outcomes.append("grounded reminder write succeeded")
                                        else:
                                            print("Skipped.")
                                            turn_outcomes.append("grounded reminder write declined")
                                    except (ValueError, OSError) as exc:
                                        print(f"Reminder was not written: {exc}")
                                        turn_outcomes.append("grounded reminder write rejected")
                            else:
                                print("Skipped.")
                                turn_outcomes.append("grounded read_file declined")
                except Exception as exc:
                    print(f"Action failed: {exc}")
                    turn_outcomes.append(f"{name} failed")
            conversation.add(prompt, "; ".join(turn_outcomes) or "No action executed")
        except Exception as exc:
            print(f"Needle request failed: {exc}")

if __name__ == "__main__":
    main()

"""User-approved, validated workflow capabilities (no executable generated code)."""
from __future__ import annotations
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any
from .agent import dispatch, validate_call
from .tools import Workspace

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
PLACEHOLDER_RE = re.compile(r"^\$\{((?:input|steps)\.[a-zA-Z0-9_.]+)\}$")
MAX_DEFINITION_CHARS = 16_000
MAX_INPUTS_CHARS = 4_000
MAX_CAPABILITIES = 50
MAX_STEPS = 8
WORKFLOW_TOOLS = {"system_info", "list_files", "search_workspace", "read_file", "write_file"}


class CapabilityStore:
    """Persist small workflows made only from the agent's existing, validated tools."""
    def __init__(self, workspace: Workspace):
        self.workspace = workspace
        self.path = workspace.root / ".needle-agent-capabilities.json"

    def _load(self) -> dict[str, Any]:
        if self.path.is_symlink():
            raise ValueError("Capability registry must not be a symlink")
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(value, dict) and value.get("version") == 1 and isinstance(value.get("capabilities"), dict):
                return value["capabilities"]
        except (OSError, ValueError):
            pass
        return {}

    def list(self) -> list[dict[str, Any]]:
        result = []
        for name, item in sorted(self._load().items()):
            try:
                item = self._validated_record(name, item)
                result.append({"name": name, "description": item["description"],
                               "steps": [step["tool"] for step in item["definition"]["steps"]]})
            except (TypeError, ValueError, KeyError):
                continue
        return result

    def get(self, name: str) -> dict[str, Any]:
        item = self._load().get(name)
        if item is None:
            raise ValueError(f"Unknown capability {name!r}")
        return self._validated_record(name, item)

    def _validated_record(self, name: str, item: Any) -> dict[str, Any]:
        if not NAME_RE.fullmatch(name) or not isinstance(item, dict):
            raise ValueError("Invalid capability record")
        description, definition = item.get("description"), item.get("definition")
        if not isinstance(description, str) or not 1 <= len(description) <= 300:
            raise ValueError("Invalid saved capability description")
        self._validate_definition(definition)
        return {"description": description, "definition": definition}

    def delete(self, name: str) -> dict[str, str]:
        self.get(name)  # Refuse to remove malformed entries accidentally.
        records = self._load()
        del records[name]
        self._save(records)
        return {"deleted": name}

    def snapshot(self) -> dict[str, Any]:
        return self._load()

    def seed(self, records: dict[str, Any]) -> None:
        """Copy validated workflow definitions into a disposable practice store."""
        if not isinstance(records, dict) or len(records) > MAX_CAPABILITIES:
            raise ValueError("Capability snapshot must be an object with at most 50 entries")
        checked = {}
        for name, item in records.items():
            if not isinstance(name, str) or not NAME_RE.fullmatch(name):
                raise ValueError("Invalid saved capability name")
            if not isinstance(item, dict) or not isinstance(item.get("description"), str):
                raise ValueError("Invalid saved capability record")
            definition = item.get("definition")
            self._validate_definition(definition)
            self._test_definition(definition)
            checked[name] = {"description": item["description"], "definition": definition}
        self._save(checked)

    def create(self, name: str, description: str, definition_json: str) -> dict[str, Any]:
        if not isinstance(name, str) or not NAME_RE.fullmatch(name):
            raise ValueError("Capability name must be 2–32 lowercase letters, digits, or underscores, starting with a letter")
        if not isinstance(description, str) or not description.strip() or len(description) > 300:
            raise ValueError("Description must contain 1–300 characters")
        if not isinstance(definition_json, str) or len(definition_json) > MAX_DEFINITION_CHARS:
            raise ValueError(f"Definition must be JSON under {MAX_DEFINITION_CHARS} characters")
        try:
            definition = json.loads(definition_json)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid workflow JSON: {exc.msg}") from exc
        self._validate_definition(definition)
        capabilities = self._load()
        if name not in capabilities and len(capabilities) >= MAX_CAPABILITIES:
            raise ValueError(f"Capability limit reached ({MAX_CAPABILITIES}); remove an old workflow first")
        tested_steps = self._test_definition(definition)
        capabilities[name] = {"description": description.strip(), "definition": definition}
        self._save(capabilities)
        return {"created": name, "description": description.strip(),
                "steps": [step["tool"] for step in definition["steps"]],
                "requires_confirmation_to_run": True,
                "sandbox_test": "passed", "tested_steps": tested_steps}

    def create_from_recipe(self, name: str, description: str, recipe: str) -> dict[str, Any]:
        """Build a safe workflow from a small, tested recipe vocabulary."""
        recipes = {
            "system_check": {"inputs": {}, "steps": [
                {"tool": "system_info", "arguments": {}}]},
            "list_workspace": {"inputs": {}, "steps": [
                {"tool": "list_files", "arguments": {"path": "."}}]},
            "find_files": {"inputs": {"query": "Phrase to search for"}, "steps": [
                {"tool": "search_workspace", "arguments": {"query": "${input.query}"}}]},
            "find_and_read": {"inputs": {"query": "Phrase to search for"}, "steps": [
                {"tool": "search_workspace", "arguments": {"query": "${input.query}"}},
                {"tool": "read_file", "arguments": {"path": "${steps.0.matches.0.path}"}}]},
            "read_text_file": {"inputs": {"path": "Workspace-relative file path"}, "steps": [
                {"tool": "read_file", "arguments": {"path": "${input.path}"}}]},
            "write_text_file": {"inputs": {"path": "Workspace-relative file path", "text": "Text to write"}, "steps": [
                {"tool": "write_file", "arguments": {"path": "${input.path}", "text": "${input.text}"}}]},
        }
        if recipe not in recipes:
            raise ValueError(f"Unsupported recipe {recipe!r}; choose one of {', '.join(sorted(recipes))}")
        return self.create(name, description, json.dumps(recipes[recipe]))

    def execute(self, name: str, inputs_json: str = "{}") -> dict[str, Any]:
        item = self.get(name)
        if not isinstance(inputs_json, str) or len(inputs_json) > MAX_INPUTS_CHARS:
            raise ValueError(f"Capability inputs must be JSON under {MAX_INPUTS_CHARS} characters")
        try:
            inputs = json.loads(inputs_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("Capability inputs must be a JSON object") from exc
        if not isinstance(inputs, dict):
            raise ValueError("Capability inputs must be a JSON object")
        definition = item["definition"]
        declared = definition["inputs"]
        if set(inputs) != set(declared):
            raise ValueError(f"Inputs must match declared fields: {', '.join(declared) or '(none)'}")
        if any(not isinstance(value, str) for value in inputs.values()):
            raise ValueError("Capability inputs must be strings")
        outputs: list[Any] = []
        for index, step in enumerate(definition["steps"]):
            try:
                args = {key: _resolve(value, inputs, outputs) for key, value in step["arguments"].items()}
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                raise RuntimeError(f"Capability failed resolving inputs for step {index + 1}: {exc}") from exc
            try:
                outputs.append(dispatch(self.workspace, step["tool"], args))
            except Exception as exc:
                raise RuntimeError(f"Capability failed at step {index + 1} ({step['tool']}): {exc}") from exc
        return {"capability": name, "steps_completed": len(outputs), "results": outputs}

    def _test_definition(self, definition: dict[str, Any]) -> int:
        """Exercise the complete workflow against seeded fake data before saving it."""
        import tempfile
        with tempfile.TemporaryDirectory(prefix="needle-capability-test-") as temp:
            workspace = Workspace(Path(temp) / "workspace")
            workspace.write_file("selftest.txt", "NeedleCapabilitySelfTest Aurora sandbox fixture TestPhrase")
            inputs = {}
            for key in definition["inputs"]:
                if "path" in key:
                    inputs[key] = "selftest.txt"
                elif "text" in key or "content" in key:
                    inputs[key] = "Needle capability self-test content"
                else:
                    inputs[key] = "NeedleCapabilitySelfTest"
            outputs = []
            for index, step in enumerate(definition["steps"]):
                try:
                    args = {key: _resolve(value, inputs, outputs)
                            for key, value in step["arguments"].items()}
                    outputs.append(dispatch(workspace, step["tool"], args))
                except Exception as exc:
                    raise ValueError(f"Capability sandbox test failed at step {index + 1} ({step['tool']}): {exc}") from exc
        return len(outputs)

    def _validate_definition(self, definition: Any) -> None:
        if not isinstance(definition, dict) or set(definition) != {"inputs", "steps"}:
            raise ValueError("Definition must contain exactly 'inputs' and 'steps'")
        inputs, steps = definition["inputs"], definition["steps"]
        if not isinstance(inputs, dict) or len(inputs) > 8:
            raise ValueError("inputs must be an object with at most 8 string descriptions")
        for key, value in inputs.items():
            if not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", key):
                raise ValueError(f"Invalid input name: {key!r}")
            if not isinstance(value, str) or not value.strip() or len(value) > 120:
                raise ValueError(f"Input {key!r} needs a short description")
        if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
            raise ValueError(f"steps must contain 1–{MAX_STEPS} actions")
        write_steps = [i for i, step in enumerate(steps)
                       if isinstance(step, dict) and step.get("tool") == "write_file"]
        if len(write_steps) > 1 or (write_steps and write_steps[0] != len(steps) - 1):
            raise ValueError("A workflow may contain at most one write step, and it must be last")
        for index, step in enumerate(steps):
            if not isinstance(step, dict) or set(step) != {"tool", "arguments"}:
                raise ValueError(f"Step {index + 1} must contain exactly tool and arguments")
            if not isinstance(step["tool"], str) or step["tool"] not in WORKFLOW_TOOLS:
                raise ValueError(f"Capability step {index + 1} uses a non-workflow tool")
            _, arguments = validate_call(step["tool"], step["arguments"])
            self._validate_placeholders(arguments, inputs, index)

    def _validate_placeholders(self, value: Any, inputs: dict, current_step: int) -> None:
        if isinstance(value, dict):
            for child in value.values():
                self._validate_placeholders(child, inputs, current_step)
        elif isinstance(value, list):
            for child in value:
                self._validate_placeholders(child, inputs, current_step)
        elif isinstance(value, str) and "${" in value:
            match = PLACEHOLDER_RE.fullmatch(value)
            if not match:
                raise ValueError(f"Invalid placeholder: {value}")
            parts = match.group(1).split(".")
            if parts[0] == "input":
                if len(parts) != 2 or parts[1] not in inputs:
                    raise ValueError(f"Unknown input placeholder: {value}")
            else:
                if len(parts) < 3 or not parts[1].isdigit() or int(parts[1]) >= current_step:
                    raise ValueError(f"Step placeholder must reference a previous step: {value}")

    def _save(self, capabilities: dict[str, Any]) -> None:
        if self.path.is_symlink():
            raise ValueError("Capability registry must not be a symlink")
        payload = json.dumps({"version": 1, "capabilities": capabilities}, ensure_ascii=False, indent=2)
        fd, name = tempfile.mkstemp(prefix=".capabilities-", dir=self.workspace.root, text=True)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)


def _resolve(value: Any, inputs: dict[str, str], outputs: list[Any]) -> Any:
    if isinstance(value, dict):
        return {key: _resolve(child, inputs, outputs) for key, child in value.items()}
    if isinstance(value, list):
        return [_resolve(child, inputs, outputs) for child in value]
    if not isinstance(value, str):
        return value
    match = PLACEHOLDER_RE.fullmatch(value)
    if not match:
        return value
    parts = match.group(1).split(".")
    if parts[0] == "input":
        return inputs[parts[1]]
    current: Any = outputs[int(parts[1])]
    for part in parts[2:]:
        current = current[int(part)] if isinstance(current, list) else current[part]
    if not isinstance(current, str):
        raise ValueError(f"Placeholder {value} did not resolve to text")
    return current

"""Needle 3 integration and conservative tool execution."""
from __future__ import annotations

from typing import Any, Literal
import os
from .tools import Workspace

CONFIDENCE_AUTO = 0.80

SAFETY_SYSTEM = (
    "You map the user's request to the most appropriate available function. "
    "Call a function only when the user clearly asks for an action supported by that function. "
    "Use only information explicitly supplied by the user or retrieved by an appropriate function. "
    "If a necessary argument is missing or the request is unclear, return no function call instead of guessing. "
    "Do not invent paths, facts, or results, and do not claim a function has run. "
    "The host application, not you, executes functions and reports their results. "
    "Use earlier context only to interpret the current request; do not repeat prior actions unless asked. "
    "Use save_workflow only when the user explicitly asks for a reusable workflow or skill; never use it for ordinary file creation and never imply it adds new permissions."
)


def build_agent(workspace: Workspace, weights: str | os.PathLike | None = None,
                engine_path: str | os.PathLike | None = None):
    if engine_path is not None:
        engine = os.path.abspath(os.fspath(engine_path))
        if not os.path.isfile(engine):
            raise FileNotFoundError(f"Needle engine library not found: {engine}")
        os.environ["NEEDLE3_LIB_PATH"] = engine
    try:
        import needle
    except ImportError as exc:
        raise RuntimeError("Needle runtime is missing. Install with: pip install -e .") from exc

    from .capabilities import CapabilityStore
    capability_store = CapabilityStore(workspace)

    @needle.tool
    def system_info() -> dict:
        """Get basic operating-system, architecture, Python version and workspace information."""
        return workspace.system_info()

    @needle.tool
    def list_files(path: str = ".") -> dict:
        """List files and folders in a workspace directory. Path is relative; use '.' for the current workspace."""
        return workspace.list_files(path)

    @needle.tool
    def search_workspace(query: str, path: str = ".") -> dict:
        """Find a phrase in file names or text contents inside the workspace. Search path is relative; use '.' for the current workspace."""
        return workspace.search_workspace(query, path)

    @needle.tool
    def read_file(path: str) -> dict:
        """Read a UTF-8 text file inside the workspace. Path is relative to the workspace."""
        return workspace.read_file(path)

    @needle.tool
    def write_file(path: str, text: str) -> dict:
        """Create or replace a UTF-8 text file inside the workspace. Requires explicit user approval."""
        return workspace.write_file(path, text)

    @needle.tool
    def save_workflow(name: str, description: str,
                          recipe: Literal["system_check", "list_workspace", "find_files", "find_and_read", "read_text_file", "write_text_file"]) -> dict:
        """Save a reusable workflow from a tested recipe using existing tools only; never use for ordinary files and add no new permissions. Choose system_check, list_workspace, find_files, find_and_read, read_text_file, or write_text_file. Writes still require approval when run."""
        return capability_store.create_from_recipe(name, description, recipe)

    available = ", ".join(item["name"] for item in capability_store.list()) or "none yet"
    def run_capability(name: str, inputs_json: str = "{}") -> dict:
        return capability_store.execute(name, inputs_json)
    run_capability.__doc__ = (
        "Run a saved, user-created workflow composed of existing tools. "
        "Requires explicit approval before execution. Available workflows: " + available + "."
    )
    run_capability = needle.tool(run_capability)

    # Needle docs recommend narrow, literal tool descriptions and small per-turn toolsets.
    # Our context manager rebuilds the bounded history per turn; avoid stale hidden turns in Needle.
    options = {"tools": [system_info, list_files, search_workspace, read_file, write_file,
                         save_workflow, run_capability],
               "system": SAFETY_SYSTEM, "stateless": True}
    if weights is not None:
        options["weights"] = os.fspath(weights)
    return needle.Needle(**options)


def propose(agent: Any, prompt: str) -> dict:
    """Ask Needle for structured calls. Never auto-execute a proposal."""
    result = agent.complete(prompt)
    return result if isinstance(result, dict) else dict(result)


def validate_call(name: Any, arguments: Any) -> tuple[str, dict]:
    """Reject malformed or out-of-schema model output before it reaches a tool."""
    schemas = {
        "system_info": {},
        "list_files": {"path": str},
        "search_workspace": {"query": str, "path": str},
        "read_file": {"path": str},
        "write_file": {"path": str, "text": str},
        "save_workflow": {"name": str, "description": str, "recipe": str},
        "run_capability": {"name": str, "inputs_json": str},
    }
    required = {"system_info": set(), "list_files": set(), "search_workspace": {"query"},
                "read_file": {"path"}, "write_file": {"path", "text"},
                "save_workflow": {"name", "description", "recipe"},
                "run_capability": {"name"}}
    if not isinstance(name, str) or name not in schemas:
        raise ValueError("Unsupported tool name")
    if not isinstance(arguments, dict):
        raise ValueError("Tool arguments must be an object")
    schema = schemas[name]
    if set(arguments) - set(schema):
        raise ValueError("Tool call contains unexpected arguments")
    if required[name] - set(arguments):
        raise ValueError("Tool call is missing required arguments")
    clean = {}
    for key, value in arguments.items():
        if name in {"list_files", "search_workspace"} and key == "path" and isinstance(value, str):
            if value.strip().lower() in {"workspace", "the workspace", "current workspace", "the current workspace"}:
                value = "."
        if not isinstance(value, schema[key]):
            raise ValueError(f"Argument {key!r} must be {schema[key].__name__}")
        clean[key] = value
    return name, clean


def dispatch(workspace: Workspace, name: str, arguments: dict) -> dict:
    """Execute only a known local tool after strict schema validation."""
    name, arguments = validate_call(name, arguments)
    if name in {"save_workflow", "run_capability"}:
        from .capabilities import CapabilityStore
        store = CapabilityStore(workspace)
        if name == "save_workflow":
            return store.create_from_recipe(**arguments)
        return store.execute(**arguments)
    allowed = {"system_info": workspace.system_info,
               "list_files": workspace.list_files,
               "search_workspace": workspace.search_workspace,
               "read_file": workspace.read_file,
               "write_file": workspace.write_file}
    return allowed[name](**arguments)

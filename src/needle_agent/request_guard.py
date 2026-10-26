"""Deterministic grounding for unambiguous literal file-write requests."""
from __future__ import annotations

import re


def ground_explicit_write(prompt: str, name: str, arguments: dict) -> tuple[dict, bool]:
    """Correct only literal filename/content that can be extracted unambiguously."""
    if name != "write_file" or not isinstance(arguments, dict):
        return arguments, False

    corrected = dict(arguments)
    literal = re.search(
        r"""\bwrite\s+(.+?)\s+(?:in|to)\s+(?:the\s+)?["']?([A-Za-z0-9_.-]+\.txt)["']?\s*[.!]?\s*$""",
        prompt, re.IGNORECASE,
    )
    if literal:
        requested_text = literal.group(1).strip().strip("\"'")
        requested_path = literal.group(2)
        if requested_text and requested_path:
            corrected["path"] = requested_path
            corrected["text"] = requested_text
            return corrected, corrected != arguments

    named_file = re.search(
        r"""\bcreate\s+(?:a|an)\s+(?:plain\s+)?(?:txt|text)\s+file\s+(?:named|called)\s+["']?([A-Za-z0-9_.-]+)["']?\s*[.!]?\s*$""",
        prompt, re.IGNORECASE,
    )
    if named_file:
        requested_path = named_file.group(1)
        if not requested_path.lower().endswith(".txt"):
            requested_path += ".txt"
        corrected["path"] = requested_path
        return corrected, corrected != arguments

    return arguments, False

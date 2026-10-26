"""Automatic, budgeted prompt assembly for stateless Needle completions."""
from __future__ import annotations
import math
from .conversation import ConversationMemory
from .evolution import EvolutionMemory


class AutomaticContextManager:
    """Prioritize current request, then approved preferences and recent history."""
    def __init__(self, conversation: ConversationMemory, preferences: EvolutionMemory,
                 token_budget: int = 1600):
        if token_budget < 256:
            raise ValueError("Context token budget must be at least 256")
        self.conversation = conversation
        self.preferences = preferences
        self.token_budget = token_budget
        self.char_budget = token_budget * 3
        self.last_stats = {"estimated_tokens": 0, "budget_tokens": token_budget,
                           "compacted": False}

    def prepare(self, request: str) -> str:
        request = request.strip()
        if len(request) > self.char_budget - 200:
            raise ValueError(f"Request is too long for the {self.token_budget}-token context budget; shorten it and retry.")
        preference = self.preferences.prompt_context()
        history = self.conversation.relevant_context(request)
        # Keep a normal request completely untouched on the common, empty-memory path.
        if not preference and not history:
            text = request
            self.last_stats = {"estimated_tokens": math.ceil(len(text) / 3),
                               "budget_tokens": self.token_budget,
                               "prompt_characters": len(text), "compacted": False}
            return text

        current = "Current request: " + request
        # Needle also receives SAFETY_SYSTEM separately. Avoid repeating policy in user content:
        # long safety wording can be misread as a requested action by a small tool model.
        available = max(0, self.char_budget - len(current) - 160)
        blocks: list[str] = []
        if preference:
            blocks.append("User preferences:\n" + preference)
        if history:
            blocks.append("Prior conversation, for follow-up context only:\n" + history)
        selected = []
        used = 0
        was_compacted = False
        for block in blocks:
            allowance = available - used
            if allowance <= 0:
                break
            if len(block) > allowance:
                was_compacted = True
                block = "…" + block[-max(0, allowance - 1):]
            selected.append(block)
            used += len(block)
        text = "\n\n".join([*selected, current]) if selected else request
        if len(text) > self.char_budget:
            # Preserve the current request preferentially, clipping only its tail if unavoidable.
            text = current[:self.char_budget]
        self.last_stats = {"estimated_tokens": math.ceil(len(text) / 3),
                           "budget_tokens": self.token_budget,
                           "prompt_characters": len(text),
                           "compacted": was_compacted or len(selected) < len(blocks)}
        return text

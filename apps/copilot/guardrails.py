"""Copilot guardrails (#120 Phase A): the cheap gates that run BEFORE and
AROUND the model. The deep guardrail is architectural (read-only tools +
RLS tenant scoping); these are the fast checks every request passes:

- input screening: length cap + a starter set of injection patterns —
  a fuller filter evolves with Phase B's real provider
- tool-arg validation: the registry's schema check runs before execution
- budgets: the graph refuses new tool calls past MAX_TOOL_CALLS and
  answers gracefully instead of looping
"""

MAX_QUESTION_CHARS = 2000
MAX_TOOL_CALLS = 4

# starter set — deliberately simple and legible; Phase B's real provider
# adds the fuller classifier as a second gate, never as the only one
_INJECTION_PATTERNS = (
    "ignore all previous instructions",
    "ignore previous instructions",
    "disregard your instructions",
    "reveal your system prompt",
    "show me your system prompt",
    "you are now",
    "developer mode",
)


def screen_question(question: str) -> str:
    """Validate one user question; raises ValueError on violations (the
    endpoint maps that to a 422)."""
    if not question or not question.strip():
        raise ValueError("the question is empty")
    if len(question) > MAX_QUESTION_CHARS:
        raise ValueError(f"the question exceeds {MAX_QUESTION_CHARS} characters")
    lowered = question.lower()
    for pattern in _INJECTION_PATTERNS:
        if pattern in lowered:
            raise ValueError("the question contains an instruction-injection pattern")
    return question.strip()

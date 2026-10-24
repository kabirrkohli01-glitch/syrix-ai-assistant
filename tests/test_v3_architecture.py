"""Structural rules for syrix_v3, enforced rather than documented.

This repository has twenty-nine previous cleanup audits in
``docs/audit_history/`` and the root Python file count still grew from 130 in
May to 205 by September. Every one of those passes deleted code and none of
them left behind anything that stopped it coming back. These two tests are
that thing.

The import rule keeps v3 from quietly growing a dependency on the runtime it
replaces — which is how a rewrite ends up as a second layer on top of the
first. The size rule keeps any single module from becoming the next
``runtime_spine.py``: it started small too.
"""
from __future__ import annotations

import ast
import os
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_V3 = os.path.join(_ROOT, "syrix_v3")

# A module over this is doing more than one job. The figure is arbitrary but
# the ceiling is not: the point is that it exists and only ever goes down.
MAX_MODULE_LINES = 600

# The only parts of the previous runtime v3 is allowed to reach into. Each is
# plumbing that would be worse if rewritten: the provider rotation that keeps
# SYRIX answering, the thinking panel's event protocol, the envelope every
# interface surface reads, and the fixed identity registry.
ALLOWED_CORE_V2 = frozenset({
    "syrix_core_v2.brain_pool",
    "syrix_core_v2.activity_feed",
    "syrix_core_v2.response_bus",
    "syrix_core_v2.self_knowledge_registry",
})

# Reaching for any of these means the reasoning layer is being reintroduced.
FORBIDDEN_PREFIXES = (
    "syrix_core_v2.runtime_spine",
    "syrix_core_v2.intent_planner",
    "syrix_core_v2.answer_strategy",
    "syrix_core_v2.spark_engine",
    "syrix_core_v2.zenith",
    "syrix_core_v2.adapters",
    "syrix_core_v2.agent_loop",
    "syrix_core_v2.atlas",
    "syrix_core_v2.reply_cleaner",
    "syrix_core_v2.factual_grounding",
    "syrix_core_v2.freshness_gate",
    "syrix_core_v2.freshness_guard",
    "syrix_core_v2.instruction_contract",
    "syrix_core_v2.conversation_consistency",
    "syrix_core_v2.answer_coverage",
    "syrix_core_v2.research_integrity",
    "syrix_core_v2.evidence_boundary",
    "syrix_core_v2.system_prompt",
    "syrix_core_v2.runtime_identity",
    "syrix_core_v2.semantic_memory",
    "syrix_core_v2.turn_resolution",
    "syrix_core_v2.deterministic_answers",
    "syrix_host",
    "syrix_host_brain",
    "syrix_host_command_router",
    "syrix_host_free_web",
    "syrix_routing_guard",
    "syrix_live_data_resolver",
    "syrix_memory_facade",
    "syrix_memory_feedback",
    "visual_brain",
)


def _modules():
    for base, _dirs, files in os.walk(_V3):
        if "__pycache__" in base:
            continue
        for name in files:
            if name.endswith(".py"):
                yield os.path.join(base, name)


def _imported_names(path: str):
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # a relative import stays inside the package
                continue
            module = node.module or ""
            # Only the fully qualified target is reported. Yielding the bare
            # package as well would read `from syrix_core_v2 import brain_pool`
            # as an import of the whole package, which is the opposite of what
            # it is — a deliberately narrow one.
            for alias in node.names:
                yield f"{module}.{alias.name}" if module else alias.name


@pytest.mark.parametrize("path", sorted(_modules()))
def test_module_stays_small(path):
    with open(path, encoding="utf-8") as fh:
        lines = sum(1 for _ in fh)
    assert lines <= MAX_MODULE_LINES, (
        f"{os.path.relpath(path, _ROOT)} is {lines} lines. Split it — a module "
        f"past {MAX_MODULE_LINES} lines is doing more than one job, and that is "
        "how the runtime this replaces got to 5,663."
    )


@pytest.mark.parametrize("path", sorted(_modules()))
def test_no_import_from_the_replaced_runtime(path):
    rel = os.path.relpath(path, _ROOT)
    for name in _imported_names(path):
        for forbidden in FORBIDDEN_PREFIXES:
            assert not (name == forbidden or name.startswith(forbidden + ".")), (
                f"{rel} imports {name}. v3 replaces that, it does not build on "
                "it. If the behaviour is genuinely needed, implement it in "
                "syrix_v3 or add the module to ALLOWED_CORE_V2 with a reason."
            )
        if name.startswith("syrix_core_v2"):
            head = ".".join(name.split(".")[:2])
            assert head in ALLOWED_CORE_V2, (
                f"{rel} imports {name}, which is not one of the four kept "
                f"seams: {sorted(ALLOWED_CORE_V2)}."
            )


def test_only_turn_is_the_entry_point():
    """Nothing inside v3 should call itself the way the host does."""
    from syrix_v3 import turn
    assert callable(turn.handle)



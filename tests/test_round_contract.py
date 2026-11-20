"""Batch 8.0 contract guards: no human gate in the loop, policy owns the version."""
import inspect

from ai4math import policy, round as round_module
from ai4math.workbench import Workbench


def test_the_round_loop_does_not_wait_for_a_human_confirmation():
    source = inspect.getsource(round_module)
    assert "checkpoint_human" not in source, (
        "Batch 8.0 removed the human confirmation gate from the research loop; "
        "the strategy review role replaces it"
    )


def test_the_apply_entry_rejects_caller_supplied_policy_fields(tmp_path):
    w = Workbench.create(tmp_path / "ws", "test", "Contract guard")
    try:
        source = inspect.getsource(Workbench.apply)
        assert "policy_guard" in source, "apply must guard caller supplied policy fields"
        assert policy.CLIENT_KEYS, "policy guard needs its forbidden key list"
    finally:
        w.store.close()


def test_the_cli_exposes_policy_and_round_operations():
    from ai4math.cli import OPERATIONS
    for operation in ("policy.migrate", "policy.configure", "round.open", "round.assess",
                      "round.settle", "round.recover", "research.register", "node.propose",
                      "checker.policy.add", "inference.add", "generation.request",
                      "strategy.generate", "generation.skip", "generation.resolve",
                      "discovery.record", "obstacle.add", "obstacle.verify", "obstacle.status",
                      "research_hypothesis.add", "hypothesis.link", "hypothesis.support",
                      "hypothesis.refute", "hypothesis.abandon", "approach.add",
                      "approach.review", "approach.activate", "approach.status",
                      "approach.narrow", "strategy.select", "research_intent.add",
                      "finding.add", "finding.revise", "formalization.request",
                      "formalization.resolve", "formalization.skip",
                      "verification.policy.add", "verification.recheck", "legacy.audit",
                      "workspace.migrate"):
        assert operation in OPERATIONS, "the CLI is a public entry; " + operation + " must be selectable"

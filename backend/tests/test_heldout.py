"""Guards for the offline decision layers, measured on cases written after they were tuned (evals/heldout.json)."""
import pytest

import agent
from evals import heldout


@pytest.mark.parametrize(
    "text",
    ["What are Pam and Jim working on?", "How's Oscar getting on with that report?", "Anyone available for a quick job?", "Tell me what the team is doing", "Any updates from the team?"],
)
def test_status_questions_phrased_differently_are_still_recognised(text):
    assert agent._STATUS_Q.search(text)


@pytest.mark.parametrize("text", ["Write me a poem about the office", "What time does the status meeting start?", "Thanks, that was helpful!", "Who is the regional manager?"])
def test_ordinary_requests_are_not_mistaken_for_status_questions(text):
    assert not agent._STATUS_Q.search(text)


def test_held_out_numbers_stay_above_their_floors():
    rep = heldout.run()
    assert rep["status_pattern_recall"] >= 0.95 and rep["status_false_alarms"].startswith("0/")
    assert rep["router_precision_when_confident"] >= 0.80  # measured 0.842 with the built-in cast; a regression guard, not a target

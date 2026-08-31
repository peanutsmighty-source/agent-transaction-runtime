from runtime.budget import ContextBudget


def test_budget_reserves_output_before_setting_compaction_trigger() -> None:
    budget = ContextBudget(max_tokens=100, reserved_output_tokens=20, compact_threshold=0.75)

    below = budget.evaluate(59)
    at_trigger = budget.evaluate(60)

    assert below.effective_input_tokens == 80
    assert below.compact_trigger_tokens == 60
    assert below.should_compact is False
    assert at_trigger.should_compact is True

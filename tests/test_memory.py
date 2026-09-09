import pytest

from runtime.memory import (
    ClaimRisk,
    ClaimTrust,
    ClaimUse,
    MemoryClaim,
    MemoryRetention,
    MemoryRetentionPolicy,
    ProvenanceRef,
)


def source() -> ProvenanceRef:
    return ProvenanceRef(
        source_id="tool_result_42",
        source_type="tool_result",
        content_hash="sha256:abc",
        workspace_version="git:def",
        artifact_id="artifact_test_17",
    )


def claim(
    *,
    trust: ClaimTrust = ClaimTrust.DERIVED,
    risk: ClaimRisk = ClaimRisk.MEDIUM,
    retention: MemoryRetention = MemoryRetention.WORKING,
) -> MemoryClaim:
    return MemoryClaim(
        id="claim_database_support",
        text="The database supports partial indexes.",
        retention=retention,
        trust=trust,
        risk=risk,
        provenance=(source(),),
    )


@pytest.mark.parametrize(
    "use",
    [
        ClaimUse.PLANNING,
        ClaimUse.TECHNICAL_DECISION,
        ClaimUse.CODE_CHANGE,
        ClaimUse.EXTERNAL_SIDE_EFFECT,
        ClaimUse.COMPLETION,
    ],
)
def test_derived_claim_requires_verification_for_actionable_use(use: ClaimUse) -> None:
    decision = MemoryRetentionPolicy().evaluate(claim(), use)

    assert decision.must_verify is True
    assert decision.block_use_until_verified is True
    assert decision.reasons == ("derived_claim_used_for_actionable_decision",)
    assert decision.provenance == (source(),)


def test_derived_claim_can_be_low_stakes_reference_without_forced_readback() -> None:
    decision = MemoryRetentionPolicy().evaluate(claim(), ClaimUse.REFERENCE)

    assert decision.must_verify is False
    assert decision.block_use_until_verified is False
    assert decision.provenance == ()


def test_pinned_does_not_mean_verified() -> None:
    decision = MemoryRetentionPolicy().evaluate(
        claim(retention=MemoryRetention.PINNED),
        ClaimUse.TECHNICAL_DECISION,
    )

    assert decision.must_verify is True
    assert "derived_claim_used_for_actionable_decision" in decision.reasons


def test_stale_claim_is_blocked_even_for_low_stakes_reference() -> None:
    decision = MemoryRetentionPolicy().evaluate(
        claim(trust=ClaimTrust.STALE),
        ClaimUse.REFERENCE,
    )

    assert decision.must_verify is True
    assert decision.reasons == ("claim_is_stale",)


def test_contradicted_claim_is_blocked_before_planning() -> None:
    decision = MemoryRetentionPolicy().evaluate(
        claim(trust=ClaimTrust.CONTRADICTED),
        ClaimUse.PLANNING,
    )

    assert decision.must_verify is True
    assert decision.reasons == ("claim_is_contradicted",)


def test_verified_high_risk_claim_is_rechecked_at_high_impact_boundary() -> None:
    decision = MemoryRetentionPolicy().evaluate(
        claim(trust=ClaimTrust.VERIFIED, risk=ClaimRisk.HIGH),
        ClaimUse.CODE_CHANGE,
    )

    assert decision.must_verify is True
    assert decision.reasons == ("high_risk_claim_used_at_high_impact_boundary",)


def test_verified_medium_risk_claim_can_be_used_for_planning() -> None:
    decision = MemoryRetentionPolicy().evaluate(
        claim(trust=ClaimTrust.VERIFIED),
        ClaimUse.PLANNING,
    )

    assert decision.must_verify is False


def test_memory_claim_requires_provenance() -> None:
    with pytest.raises(ValueError, match="memory_claim_requires_provenance"):
        MemoryClaim(
            id="claim_without_source",
            text="Unsupported statement",
            retention=MemoryRetention.WORKING,
            trust=ClaimTrust.DERIVED,
            risk=ClaimRisk.LOW,
            provenance=(),
        )


def test_claim_and_decision_are_trace_serializable() -> None:
    memory_claim = claim(risk=ClaimRisk.HIGH)
    decision = MemoryRetentionPolicy().evaluate(
        memory_claim,
        ClaimUse.TECHNICAL_DECISION,
    )

    assert memory_claim.to_dict()["provenance"][0]["artifact_id"] == "artifact_test_17"
    assert decision.to_dict()["reasons"] == [
        "derived_claim_used_for_actionable_decision",
        "high_risk_claim_used_at_high_impact_boundary",
    ]

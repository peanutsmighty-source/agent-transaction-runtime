from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any


class MemoryRetention(StrEnum):
    """How strongly a claim should be retained in model-visible context."""

    PINNED = "pinned"
    WORKING = "working"
    EPISODIC = "episodic"


class ClaimTrust(StrEnum):
    """What the runtime currently knows about a claim's evidence."""

    VERIFIED = "verified"
    DERIVED = "derived"
    STALE = "stale"
    CONTRADICTED = "contradicted"


class ClaimRisk(StrEnum):
    """Impact if a claim is wrong when the agent uses it."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ClaimUse(StrEnum):
    """The decision boundary at which the agent wants to use a claim."""

    REFERENCE = "reference"
    PLANNING = "planning"
    TECHNICAL_DECISION = "technical_decision"
    CODE_CHANGE = "code_change"
    EXTERNAL_SIDE_EFFECT = "external_side_effect"
    COMPLETION = "completion"


@dataclass(frozen=True)
class ProvenanceRef:
    """A stable pointer to evidence; it is not proof that a claim is correct."""

    source_id: str
    source_type: str
    content_hash: str | None = None
    workspace_version: str | None = None
    artifact_id: str | None = None

    def __post_init__(self) -> None:
        if not self.source_id.strip():
            raise ValueError("provenance_source_id_must_not_be_empty")
        if not self.source_type.strip():
            raise ValueError("provenance_source_type_must_not_be_empty")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MemoryClaim:
    """One explicit fact-like statement carried by runtime memory."""

    id: str
    text: str
    retention: MemoryRetention
    trust: ClaimTrust
    risk: ClaimRisk
    provenance: tuple[ProvenanceRef, ...]

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("memory_claim_id_must_not_be_empty")
        if not self.text.strip():
            raise ValueError("memory_claim_text_must_not_be_empty")
        if not self.provenance:
            raise ValueError("memory_claim_requires_provenance")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "retention": self.retention.value,
            "trust": self.trust.value,
            "risk": self.risk.value,
            "provenance": [source.to_dict() for source in self.provenance],
        }


@dataclass(frozen=True)
class MemoryVerificationDecision:
    """Whether evidence must be read back before the requested claim use."""

    must_verify: bool
    block_use_until_verified: bool
    reasons: tuple[str, ...]
    provenance: tuple[ProvenanceRef, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "must_verify": self.must_verify,
            "block_use_until_verified": self.block_use_until_verified,
            "reasons": list(self.reasons),
            "provenance": [source.to_dict() for source in self.provenance],
        }


class MemoryRetentionPolicy:
    """Classify claim use without relying on the model to request verification."""

    _ACTIONABLE_USES = {
        ClaimUse.PLANNING,
        ClaimUse.TECHNICAL_DECISION,
        ClaimUse.CODE_CHANGE,
        ClaimUse.EXTERNAL_SIDE_EFFECT,
        ClaimUse.COMPLETION,
    }
    _HIGH_IMPACT_USES = {
        ClaimUse.TECHNICAL_DECISION,
        ClaimUse.CODE_CHANGE,
        ClaimUse.EXTERNAL_SIDE_EFFECT,
        ClaimUse.COMPLETION,
    }

    def evaluate(
        self,
        claim: MemoryClaim,
        use: ClaimUse,
    ) -> MemoryVerificationDecision:
        reasons: list[str] = []

        if claim.trust == ClaimTrust.CONTRADICTED:
            reasons.append("claim_is_contradicted")
        elif claim.trust == ClaimTrust.STALE:
            reasons.append("claim_is_stale")

        if claim.trust == ClaimTrust.DERIVED and use in self._ACTIONABLE_USES:
            reasons.append("derived_claim_used_for_actionable_decision")

        if claim.risk == ClaimRisk.HIGH and use in self._HIGH_IMPACT_USES:
            reasons.append("high_risk_claim_used_at_high_impact_boundary")

        must_verify = bool(reasons)
        return MemoryVerificationDecision(
            must_verify=must_verify,
            block_use_until_verified=must_verify,
            reasons=tuple(reasons),
            provenance=claim.provenance if must_verify else (),
        )

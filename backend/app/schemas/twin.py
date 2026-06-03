"""
app/schemas/twin.py

Pydantic response contracts for Phase 4: Personal Learning Graph.
"""

from pydantic import BaseModel, ConfigDict, Field

SkillEvolutionPoint = dict[str, str | int]


class TwinProfileResponse(BaseModel):
    """Basic personal twin profile statistics."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., description="Unique user node id.")
    name: str = Field(..., description="Display name stored on the User node.")
    repository_count: int = Field(..., ge=0, description="Repositories connected via OWNER_OF.")
    paper_count: int = Field(..., ge=0, description="Papers connected via READ.")
    skill_count: int = Field(..., ge=0, description="Current HAS_SKILL relationship count.")
    average_confidence: float = Field(
        ...,
        ge=0,
        le=1,
        description="Average HAS_SKILL confidence across all inferred skills.",
    )


class SkillStrength(BaseModel):
    """One inferred personal skill with evidence counts."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Technology or framework name.")
    label: str = Field(..., description="Neo4j node label: Technology or Framework.")
    confidence: float = Field(..., ge=0, le=1, description="Normalized confidence from 0 to 1.")
    strength_score: float = Field(..., ge=0, le=100, description="Display score from 0 to 100.")
    project_count: int = Field(..., ge=0, description="Owned repositories using this skill.")
    paper_count: int = Field(..., ge=0, description="Read papers mentioning this skill.")


class SkillGapItem(BaseModel):
    """Skill present in the graph but missing from current HAS_SKILL edges."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Technology or framework name.")
    label: str = Field(..., description="Neo4j node label: Technology or Framework.")
    paper_count: int = Field(..., ge=0, description="Read papers mentioning this skill.")
    graph_count: int = Field(..., ge=0, description="Total graph references for this skill.")
    relevance_score: float = Field(..., ge=0, description="Simple evidence-based relevance score.")


class SkillGapResponse(BaseModel):
    """Skill gap response for a target role."""

    model_config = ConfigDict(extra="forbid")

    target_role: str = Field(..., description="Requested target role.")
    have: list[SkillStrength] = Field(
        default_factory=list,
        description="Current inferred skills, usually top skills for the target role.",
    )
    missing: list[SkillGapItem] = Field(
        default_factory=list,
        description="Graph-mentioned skills without a HAS_SKILL edge.",
    )

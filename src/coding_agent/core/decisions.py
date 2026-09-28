"""Bounded engineering judgments; never permission grants or acceptance evidence."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from .knowledge import SourceFile, SourceReference, Text
from .models import DomainModel


class AutonomyPolicy(DomainModel):
    preferences: Annotated[tuple[Text, ...], Field(max_length=16)] = ()
    max_decisions: Annotated[int, Field(strict=True, ge=1, le=20)] = 2
    max_research_calls: Annotated[int, Field(strict=True, ge=0, le=20)] = 4


class DecisionQuestion(DomainModel):
    text: Text
    kind: Literal["implementation", "requirement"] = "implementation"
    options: Annotated[tuple[Text, ...], Field(max_length=8)] = ()


class ResearchRequest(DomainModel):
    operation: Literal["read", "search"]
    path: Text
    query: Text | None = None

    @model_validator(mode="after")
    def search_query(self) -> Self:
        if (self.operation == "search") != (self.query is not None):
            raise ValueError("only search requires a query")
        return self


class DecisionStep(DomainModel):
    action: Literal["research", "decide", "ask_user"]
    reason: Text
    choice: Text | None = None
    sources: Annotated[tuple[SourceReference, ...], Field(max_length=8)] = ()
    preference_indices: Annotated[tuple[int, ...], Field(max_length=16)] = ()
    assumptions: Annotated[tuple[Text, ...], Field(max_length=8)] = ()
    research: ResearchRequest | None = None

    @model_validator(mode="after")
    def complete_step(self) -> Self:
        if (self.action == "research") != (self.research is not None):
            raise ValueError("research action requires exactly one read/search request")
        if self.action == "decide" and (
            not self.choice or not (self.sources or self.preference_indices)
        ):
            raise ValueError("a decision requires a choice and recorded supporting sources")
        return self


class DecisionRecord(DomainModel):
    question: DecisionQuestion
    status: Literal["decided", "needs_user"]
    step: DecisionStep
    research_event_ids: tuple[str, ...]
    research_sources: tuple[SourceFile, ...] = ()

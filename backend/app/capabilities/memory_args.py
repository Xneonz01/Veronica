"""The memory capabilities' arguments, as the model sees them in each tool's schema."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from pydantic import BaseModel, Field, PrivateAttr, field_validator

from app.services.memory.models import MAX_MEMORY_CHARS, Memory


class SaveArgs(BaseModel):
    model_config = {"extra": "forbid"}
    content: str = Field(max_length=MAX_MEMORY_CHARS, description="The fact, in third person.")
    kind: Literal["fact", "preference"] = "fact"

    @field_validator("kind", mode="before")
    @classmethod
    def _another_kind_is_a_fact(cls, value: object) -> object:
        # Models name kinds of their own ("note", "profile"). What they were asked to
        # keep is still worth keeping, and a fact is what it most likely is.
        return value if value in ("fact", "preference") else "fact"


class RecallArgs(BaseModel):
    model_config = {"extra": "forbid"}
    query: str


class Named(BaseModel):
    """Arguments whose prompt names memories. The preflight keeps what it named here, so
    a yes changes exactly those: looked up again after the answer, up to 45 s later, a
    forget found a memory saved meanwhile that nobody was asked about. Private, so it is
    in no tool schema and no model can send it."""

    _named: tuple[int, ...] | None = PrivateAttr(default=None)

    @property
    def named(self) -> tuple[int, ...] | None:
        """The ids the prompt named, or None when nobody was asked."""
        return self._named

    def naming(self, found: Iterable[Memory]) -> None:
        self._named = tuple(item.id for item in found)


# `old` and `query` ask for the memory in the words she has it in. Asked "roughly",
# qwen3.5:9b described it ("coffee preference (black or with milk)"), which matched too
# few of the stored memory's words to find it. The examples are on purpose about
# nothing the evaluation asks, so a right answer there isn't a copied one.
class CorrectArgs(Named):
    model_config = {"extra": "forbid"}
    old: str = Field(
        description='The memory that is wrong, as you remember it: "User drives a blue van."'
    )
    # With "in third person" alone, qwen3.5:9b wrote the user's own words ("I take it
    # with milk") in two runs of three and made up a name in the third; the example
    # is what the stored memory has to look like.
    new: str = Field(
        max_length=MAX_MEMORY_CHARS,
        description='What is true instead, about the user: "User drives a red van."',
    )


class ForgetArgs(Named):
    model_config = {"extra": "forbid"}
    query: str = Field(
        description='The memory to forget, as you remember it: "User collects stamps."'
    )

"""Bounded, regex-free matching for caller-selected Blender names."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


MaxSelectorLength = 128
MaxCandidateCount = 4096
MaxCandidateNameLength = 256
MaxSelectorEvaluations = 8192
MaxSelectorCount = 32
MaxGlobWildcards = 16
MaxGlobSteps = 1024


class NameSelectorMode(str, Enum):
    EXACT = "EXACT"
    PREFIX = "PREFIX"
    SUFFIX = "SUFFIX"
    GLOB = "GLOB"


class NameSelectorError(ValueError):
    """Structured selector failure containing no caller-provided value."""

    def __init__(self, Code: str, PublicMessage: str):
        super().__init__(PublicMessage)
        self.Code = Code
        self.PublicMessage = PublicMessage


@dataclass(frozen=True)
class NameSelector:
    Mode: NameSelectorMode
    Value: str

    def Matches(self, Candidate: str) -> bool:
        ValidateCandidateName(Candidate)
        if self.Mode == NameSelectorMode.EXACT:
            return Candidate == self.Value
        if self.Mode == NameSelectorMode.PREFIX:
            return Candidate.startswith(self.Value)
        if self.Mode == NameSelectorMode.SUFFIX:
            return Candidate.endswith(self.Value)
        return _MatchGlob(self.Value, Candidate)


def ParseNameSelector(Value: object) -> NameSelector:
    if not isinstance(Value, dict):
        raise _Deny("NAME_SELECTOR_INVALID", "A name selector object is required")
    if set(Value) != {"mode", "value"}:
        raise _Deny(
            "NAME_SELECTOR_FIELDS_INVALID",
            "A name selector requires only mode and value fields",
        )

    RawMode = Value.get("mode")
    RawValue = Value.get("value")
    if not isinstance(RawMode, str):
        raise _Deny("NAME_SELECTOR_MODE_INVALID", "The name selector mode is invalid")
    try:
        Mode = NameSelectorMode(RawMode)
    except ValueError as Error:
        raise _Deny("NAME_SELECTOR_MODE_INVALID", "The name selector mode is invalid") from Error

    if (
        not isinstance(RawValue, str)
        or not RawValue
        or len(RawValue) > MaxSelectorLength
        or not RawValue.isprintable()
    ):
        raise _Deny("NAME_SELECTOR_VALUE_INVALID", "The name selector value is invalid")
    if Mode == NameSelectorMode.GLOB and sum(Character in "*?" for Character in RawValue) > (
        MaxGlobWildcards
    ):
        raise _Deny("NAME_SELECTOR_COMPLEXITY_EXCEEDED", "The name selector is too complex")
    return NameSelector(Mode, RawValue)


def ValidateSelectorBudget(CandidateCount: int, SelectorCount: int = 1) -> None:
    if (
        not isinstance(CandidateCount, int)
        or isinstance(CandidateCount, bool)
        or CandidateCount < 0
        or CandidateCount > MaxCandidateCount
        or not isinstance(SelectorCount, int)
        or isinstance(SelectorCount, bool)
        or SelectorCount <= 0
        or SelectorCount > MaxSelectorCount
        or CandidateCount * SelectorCount > MaxSelectorEvaluations
    ):
        raise _Deny(
            "NAME_SELECTOR_BUDGET_EXCEEDED",
            "The name selector candidate budget is exceeded",
        )


def ValidateCandidateName(Candidate: object) -> None:
    if (
        not isinstance(Candidate, str)
        or not Candidate
        or len(Candidate) > MaxCandidateNameLength
        or not Candidate.isprintable()
    ):
        raise _Deny("NAME_SELECTOR_CANDIDATE_INVALID", "A candidate object name is invalid")


def _MatchGlob(Pattern: str, Candidate: str) -> bool:
    PatternIndex = 0
    CandidateIndex = 0
    StarIndex = -1
    StarCandidateIndex = 0
    Steps = 0

    while CandidateIndex < len(Candidate):
        Steps += 1
        if Steps > MaxGlobSteps:
            raise _Deny("NAME_SELECTOR_COMPLEXITY_EXCEEDED", "The name selector is too complex")
        if PatternIndex < len(Pattern) and Pattern[PatternIndex] in (
            "?",
            Candidate[CandidateIndex],
        ):
            PatternIndex += 1
            CandidateIndex += 1
        elif PatternIndex < len(Pattern) and Pattern[PatternIndex] == "*":
            StarIndex = PatternIndex
            PatternIndex += 1
            StarCandidateIndex = CandidateIndex
        elif StarIndex >= 0:
            PatternIndex = StarIndex + 1
            StarCandidateIndex += 1
            CandidateIndex = StarCandidateIndex
        else:
            return False

    while PatternIndex < len(Pattern) and Pattern[PatternIndex] == "*":
        PatternIndex += 1
    return PatternIndex == len(Pattern)


def _Deny(Code: str, PublicMessage: str) -> NameSelectorError:
    return NameSelectorError(Code, PublicMessage)

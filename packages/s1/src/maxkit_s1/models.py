"""Records shared by the parser, checker, and report writer."""

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LockRule:
    mode: str
    idx: int | None
    pattern: re.Pattern[str] | None
    allow_empty: bool


@dataclass(frozen=True)
class GlossaryTerm:
    term_en: str
    term_zh: str
    required: bool


@dataclass(frozen=True)
class Contract:
    contract_id: str
    template: str
    source_lang: str
    target_lang: str
    pairing: str
    allow_split: bool
    auto_lock: bool
    auto_lock_mode: str
    locks: tuple[LockRule, ...]
    glossary: tuple[GlossaryTerm, ...]


@dataclass(frozen=True)
class SourceUnit:
    idx: int
    text: str


@dataclass(frozen=True)
class DeliverableUnit:
    idx: int
    en: str
    zh: str
    lock: str | None
    split_of: int | None


@dataclass(frozen=True)
class Aligned:
    idx: int
    source_en: str | None
    deliverable_en: str | None
    zh: str | None
    lock: str
    allow_empty: bool
    deliverable_lock: str | None
    kind: str


@dataclass(frozen=True)
class Check:
    id: str
    result: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"id": self.id, "result": self.result, "detail": self.detail}


@dataclass(frozen=True)
class L1Run:
    report: dict
    report_path: Path
    exit_code: int
    summary: str

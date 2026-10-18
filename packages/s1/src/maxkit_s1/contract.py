"""Load an S1 format contract.

Convention: a YAML document, or Markdown whose file starts with a YAML
frontmatter block delimited by ``---``. ``.yaml`` / ``.yml`` files are parsed
as YAML even when they begin with a document marker. Template C is rejected.
"""

import re
from pathlib import Path

import yaml

from maxkit_s1.errors import UsageError
from maxkit_s1.glossary import parse_required
from maxkit_s1.models import Contract, GlossaryTerm, LockRule
from maxkit_s1.textutil import parse_idx

_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.DOTALL)
_LOCK_MODES = {"none", "keep_en", "skip", "glossary"}
_TEMPLATES = {"A", "B"}


def load_contract(path: Path) -> Contract:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise UsageError(f"cannot read {path}: {exc.strerror}") from exc
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise UsageError(f"{path} is not utf-8") from exc
    data = _load_mapping(_yaml_text(text, path), path)
    return _contract_from_mapping(data)


def _yaml_text(text: str, path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".yaml", ".yml"}:
        return text
    match = _FRONTMATTER.match(text)
    if match:
        return match.group(1)
    if suffix == ".md":
        raise UsageError(f"{path}: markdown contract must start with YAML frontmatter")
    return text


def _load_mapping(text: str, path: Path) -> dict:
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise UsageError(f"{path}: invalid YAML: {exc}") from exc
    if loaded is None:
        raise UsageError(f"{path}: contract YAML is empty")
    if not isinstance(loaded, dict):
        raise UsageError(f"{path}: contract YAML must be a mapping")
    return loaded


def _contract_from_mapping(data: dict) -> Contract:
    contract_id = data.get("contract_id")
    if not isinstance(contract_id, str) or not contract_id.strip():
        raise UsageError("contract_id must be a non-empty string")
    template_raw = data.get("template")
    if not isinstance(template_raw, str):
        raise UsageError("template must be A or B")
    template = template_raw.strip().upper()
    if template == "C":
        raise UsageError("template C is not implemented; L1 CLI supports templates A and B only")
    if template not in _TEMPLATES:
        raise UsageError(f"unsupported template {template_raw!r}; expected A or B")

    auto_lock_raw = data.get("auto_lock")
    if auto_lock_raw is None:
        auto_lock = template == "A"
    else:
        auto_lock = _parse_bool(auto_lock_raw, "auto_lock")

    auto_lock_mode = data.get("auto_lock_mode", "keep_en")
    if not isinstance(auto_lock_mode, str) or auto_lock_mode.strip() not in {"keep_en", "skip"}:
        raise UsageError("auto_lock_mode must be keep_en or skip")

    return Contract(
        contract_id=contract_id.strip(),
        template=template,
        source_lang=_optional_str(data.get("source_lang"), "en"),
        target_lang=_optional_str(data.get("target_lang"), "zh-Hans"),
        pairing=_optional_str(data.get("pairing"), "1:1 line"),
        allow_split=_parse_bool(data.get("allow_split", False), "allow_split"),
        auto_lock=auto_lock,
        auto_lock_mode=auto_lock_mode.strip(),
        locks=_parse_locks(data.get("locks", [])),
        glossary=_parse_inline_glossary(data.get("glossary", [])),
    )


def _optional_str(value: object, default: str) -> str:
    if value is None:
        return default
    if not isinstance(value, str):
        raise UsageError(f"expected a string, got {value!r}")
    return value.strip() or default


def _parse_bool(value: object, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "yes", "y", "1"}:
            return True
        if text in {"false", "no", "n", "0"}:
            return False
    raise UsageError(f"{field} must be a boolean")


def _parse_locks(value: object) -> tuple[LockRule, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise UsageError("locks must be a list")
    rules: list[LockRule] = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            raise UsageError(f"locks[{index}] must be a mapping")
        mode = item.get("mode")
        if not isinstance(mode, str) or mode.strip() not in _LOCK_MODES:
            raise UsageError(f"locks[{index}].mode must be none, keep_en, skip, or glossary")
        idx = None
        if "idx" in item and item["idx"] is not None:
            try:
                idx = parse_idx(item["idx"], "idx")
            except ValueError as exc:
                raise UsageError(f"locks[{index}].idx must be an integer >= 1") from exc
        match = item.get("match")
        pattern = None
        if match is not None:
            if not isinstance(match, str) or match == "":
                raise UsageError(f"locks[{index}].match must be a regex string")
            try:
                pattern = re.compile(match)
            except re.error as exc:
                raise UsageError(f"locks[{index}].match is not a valid regex") from exc
        if idx is None and pattern is None:
            raise UsageError(f"locks[{index}] needs idx or match")
        allow_empty = _parse_bool(item.get("allow_empty", False), f"locks[{index}].allow_empty")
        rules.append(
            LockRule(
                mode=mode.strip(),
                idx=idx,
                pattern=pattern,
                allow_empty=allow_empty,
            )
        )
    return tuple(rules)


def _parse_inline_glossary(value: object) -> tuple[GlossaryTerm, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise UsageError("glossary must be a list")
    terms: dict[str, GlossaryTerm] = {}
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            raise UsageError(f"glossary[{index}] must be a mapping")
        term_en = item.get("term_en")
        term_zh = item.get("term_zh")
        if not isinstance(term_en, str) or not term_en.strip():
            raise UsageError(f"glossary[{index}].term_en must be a non-empty string")
        if not isinstance(term_zh, str) or not term_zh.strip():
            raise UsageError(f"glossary[{index}].term_zh must be a non-empty string")
        term_en = term_en.strip()
        term_zh = term_zh.strip()
        terms[term_en] = GlossaryTerm(
            term_en=term_en,
            term_zh=term_zh,
            required=parse_required(item.get("required"), where=f"glossary[{index}]"),
        )
    return tuple(terms.values())

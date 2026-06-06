#!/usr/bin/env python3
"""
UserPromptSubmit hook for Codex.

Quiet compatibility behavior:
- Every submitted prompt is classified before output is chosen.
- Every successful classification returns a result and reason in additionalContext.
- Strong/check classifications append a production orchestration contract.
- The hook remains side-effect free and does not spawn subagents by itself.

Codex hook docs: UserPromptSubmit receives JSON on stdin with a `prompt` field and
can return JSON with hookSpecificOutput.additionalContext.
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class SignalSet:
    label: str
    weight: int
    patterns: tuple[str, ...]


CUSTOM_AGENT_NAMES = (
    "so_mapper",
    "so_reviewer",
    "so_tester",
    "so_reproducer",
    "so_docs_researcher",
    "so_designer",
    "so_implementer",
)
CUSTOM_AGENT_PATTERN = "(?:" + "|".join(re.escape(name) for name in CUSTOM_AGENT_NAMES) + ")"
CUSTOM_AGENT_HEADER_PATTERN = rf"(?m)^\s*agent_type:\s*{CUSTOM_AGENT_PATTERN}\b"
SURFACE_TERM_PATTERNS = (
    r"frontend",
    r"backend",
    r"apis?",
    r"web",
    r"servers?",
    r"clients?",
    r"databases?",
    r"dbs?",
    r"services?",
    r"auth",
    r"authentication",
    r"authorization",
    r"billing",
    r"payments?",
    r"checkout",
    r"workers?",
    r"jobs?",
    r"queues?",
    r"caches?",
    r"storage",
    r"search\s+(?:services?|subsystems?|engines?|index(?:es|ing)?|indices)",
    r"routes?",
    r"controllers?",
    r"models?",
    r"migrations?",
    r"schemas?",
    r"permissions?",
    r"policies?",
    r"observability",
    r"telemetry",
    r"logging",
    r"graphql",
    r"rest",
    r"cli",
    r"mobile",
    r"infra(?:structure)?",
    r"packages?",
    r"workspaces?",
    r"monorepo",
)
SURFACE_TERM_PATTERN = "(?:" + "|".join(SURFACE_TERM_PATTERNS) + ")"
FORMAL_REVIEW_TARGET_PATTERN = (
    r"(?:branch|pr|pull request|mr|merge request|diff|patch|code|changes?|commits?|"
    r"security|threat|vulnerabilit(?:y|ies)|risks?|architecture|implementation|modules?|"
    r"repositories|repo|files?|functions?|classes?|tests?)"
)
FORMAL_REVIEW_PATTERN = (
    rf"(?:\breview\b.{{0,80}}\b{FORMAL_REVIEW_TARGET_PATTERN}\b|"
    rf"\b{FORMAL_REVIEW_TARGET_PATTERN}\b.{{0,80}}\breview\b)"
)
OUTPUT_OR_STATUS_TERM_PATTERN = r"(?:status feedback|status sentences?|hook context|outputs?|results?|messages?|labels?)"
OUTPUT_QUALITY_TERM_PATTERN = (
    r"(?:professional|profesional|consistent|inconsistent|inconsistant|punctuation|"
    r"wording|tone|polish|grammar|style)"
)
RESULT_SWEEP_PATTERN = r"(?:all possible|every|each|all)\s+(?:results?|outputs?|status(?:es)?|messages?|cases?|variants?)"
DOCUMENTATION_TERM_PATTERN = r"\b(?:docs?|d[eo]cument(?:s|ation|ations)?)\b"
QA_TERM_PATTERN = r"\b(?:qa|quality assurance)\b"
SETUP_TERM_PATTERN = r"\b(?:setup|install(?:ation|er)?|config(?:uration)?)\b"
PLUGIN_MIGRATION_TERM_PATTERN = r"\b(?:(?:previous|old|legacy)\s+plugin|remaining\s+(?:mentions?|references?))\b"
VALIDATION_TARGET_PATTERN = (
    rf"(?:{DOCUMENTATION_TERM_PATTERN}|{QA_TERM_PATTERN}|{SETUP_TERM_PATTERN}|{PLUGIN_MIGRATION_TERM_PATTERN})"
)
VALIDATION_VERB_PATTERN = r"\b(?:validat(?:e|ion)|ensure|verify|check|confirm|make sure)\b"
SOURCE_FILE_PATTERN = (
    r"\b[\w./-]+\.(?:ts|tsx|js|jsx|py|go|rs|java|rb|php|cs|cpp|c|h|md|json|ya?ml|toml)\b"
)
SINGLE_TARGET_SCOPE_PATTERN = (
    r"\b(?:this\s+)?(?:one(?!\s+or\s+more\b)|single)[- ]?(?:failing\s+)?"
    r"(?:(?:[\w/-]+\s+){0,5})?"
    r"(?:file|module|component|function|test|case|assertion|stack trace|import)\b"
)
NAMED_TARGET_NOUN_PATTERN = (
    r"(?:file|module|component|function|class|test|case|assertion|stack trace|handler|"
    r"middleware|service|route|controller|command|import)"
)
SINGLE_NAMED_TARGET_PHRASE_PATTERN = (
    rf"\b(?:the|this)\s+(?:(?:[\w/-]+)\s+){{0,5}}{NAMED_TARGET_NOUN_PATTERN}\b"
)
MULTI_TARGET_CONNECTOR_PATTERN = (
    r"\b(?:and|or|across|spanning|between|multiple|several|many)\b|[,;]"
)
BROAD_DEBUG_SCOPE_PATTERN = (
    r"\bunknown\s+(?:subsystem|system|module|service)\s+boundaries\b|"
    r"\b(?:multiple|many|several)\s+(?:files|modules|services|layers|subsystems)\b"
)
DEBUG_SCOPE_BLOCKING_HITS = {
    "architecture/refactor",
    "comparison/options",
    "explicit subagents",
    "research/docs",
}
LIGHTWEIGHT_TEXT_REVIEW_SUBJECT_PATTERN = (
    r"\b(?:readme|docs?|d[eo]cument(?:s|ation|ations)?|paragraph|copy|wording|clarity|grammar|typos?|sentence)\b"
)
LIGHTWEIGHT_TEXT_REVIEW_VERB_PATTERN = r"\b(?:review|check|edit|proofread)\b"
HIGH_RISK_REVIEW_TERM_PATTERN = (
    r"\b(?:security|threat|vulnerabilit(?:y|ies)|risks?|architecture|implementation|tests?)\b"
)
LIGHTWEIGHT_TEXT_REVIEW_ALLOWED_HITS = {
    "multi-surface scope",
    "research/docs",
    "review/audit",
    "setup/config",
    "validation sweep",
}
EXPLANATION_ONLY_COMPLEX_HITS = {
    "cross-cutting integration",
    "multi-surface scope",
    "release/data risk",
    "research/docs",
    "runtime/performance/concurrency",
}
EXPLANATION_ONLY_PATTERN = r"^\s*(?:what|why|how|explain|summari[sz]e)\b"
LOW_RISK_TOPIC_COMPLEX_HITS = EXPLANATION_ONLY_COMPLEX_HITS | {
    "comparison/options",
    "tests/verification",
}
CONCEPTUAL_COMPARISON_PATTERN = r"^\s*(?:compare\b|what\b.{0,80}\btrade[- ]?offs?\b)"
ACTIONABLE_COMPARISON_PATTERN = (
    r"\b(?:implementation|architecture|refactor|branch|patch|diff|code|changes?|repo|"
    r"repository|plugin|regression|debug|investigat(?:e|ion)|audit|review|security|"
    r"vulnerabilit(?:y|ies)|risk|rollout|migrations?|schemas?|backfills?|rollbacks?|"
    r"compatib(?:le|ility)|breaking changes?|missing\s+tests?)\b"
)
REVIEW_AUDIT_INVESTIGATION_PATTERN = r"\b(?:review|audit|investigat(?:e|ion))\b"
HIGH_RISK_SINGLE_TARGET_DOMAIN_PATTERN = (
    r"\b(?:security|auth(?:entication|orization)?|permissions?|privacy|payments?|billing|"
    r"crypto(?:graphy)?|vulnerabilit(?:y|ies)|threat[- ]?models?|threats?)\b"
)
TEST_VERIFICATION_TERM_PATTERN = (
    r"(?:\btests?\b|\bregressions?\b|\bcoverage\b|\bverif(?:y|ication)\b|"
    r"\bvalidat(?:e|ion)\b|\breproduce\b|\bbenchmarks?\b|\bperformance\b|\bci\b|"
    r"\bmissing\s+(?:cases?|tests?|coverage|verification)\b|"
    r"\b(?:coverage|verification)\s+gaps?\b)"
)
HIGH_RISK_SINGLE_TARGET_VERIFICATION_PATTERN = (
    r"(?:\btests?\b|\bregressions?\b|\bcoverage\b|\bverif(?:y|ication)\b|"
    r"\bvalidat(?:e|ion)\b|\bmissing\s+(?:cases?|tests?|coverage|verification)\b|"
    r"\b(?:coverage|verification)\s+gaps?\b)"
)
EXPLICIT_TINY_EDIT_SCOPE_PATTERN = r"\b(?:one|single|typos?|one[- ]?line|tiny|small|quick)\b"
TINY_SURFACE_EDIT_PATTERN = (
    r"\b(?:rename|fix|change)\b.{0,80}\b(?:one|single|typos?|files?|names?|routes?|"
    r"labels?|messages?|docs?)\b"
)
SINGLE_TARGET_COMPARISON_BLOCKING_HITS = {
    "architecture/refactor",
    "debugging/root-cause",
    "explicit subagents",
    "review/audit",
    "tests/verification",
}
CONDITIONAL_VALUE_PATTERN = (
    r"(?:useful|valuable|needed|necessary|helpful|beneficial|warranted|appropriate|worthwhile|"
    r"(?:it\s+)?adds?\s+value|(?:it\s+)?reduces?\s+risk|(?:it\s+)?materially\s+helps?)"
)
CONDITIONAL_CONNECTOR_PATTERN = r"(?:only\s+if|if|when|where|unless)"
CONDITIONAL_AGENT_TARGET_PATTERN = r"(?:sub[- ]?agents?|(?:parallel|read[- ]?only)\s+agents?|agents?)"
CONDITIONAL_ORCHESTRATION_TARGET_PATTERN = (
    rf"(?:{CONDITIONAL_AGENT_TARGET_PATTERN}|orchestrat(?:ion|e))"
)
CONDITIONAL_AGENT_ACTION_PATTERN = r"(?:use|spawn|run)"
OPTOUT_AGENT_TARGET_PATTERN = (
    r"(?:sub[- ]?agents?(?!\s+orchestration)|(?:parallel|read[- ]?only)\s+agents?|agents?)"
)
OPTOUT_ORCHESTRATION_TARGET_PATTERN = (
    rf"(?:sub[- ]?agent\s+orchestration|{OPTOUT_AGENT_TARGET_PATTERN}|orchestrat(?:ion|e))"
)
SOFT_CONDITIONAL_AFTER_OPTOUT_PATTERN = (
    rf"\s*(?:(?:{CONDITIONAL_CONNECTOR_PATTERN}|as)\s+{CONDITIONAL_VALUE_PATTERN})\b"
)
HARD_CONDITIONAL_OPTOUT_CONNECTOR_PATTERN = r"(?:(?:even\s+)?(?:if|when|where))"
# This pattern is only consulted when both opt-out and conditional-permission
# signals are present. It intentionally excludes "unless useful" so equivalent
# permission phrases such as "No subagents unless useful" and
# "Do not use subagents unless useful" stay conditional instead of hard opt-out.
HARD_CONDITIONAL_OPTOUT_PATTERN = (
    rf"(?:\bnever\b.{{0,80}}\b"
    rf"(?:use|spawn|run|delegate)\b.{{0,80}}\b"
    rf"{OPTOUT_ORCHESTRATION_TARGET_PATTERN}\b|"
    rf"\bnever\s+orchestrat(?:e|ion)\b|"
    rf"\b(?:do not|don['’]?t|dont)\b.{{0,80}}\b"
    rf"(?:use|spawn|run|delegate)\b.{{0,80}}\b"
    rf"{OPTOUT_ORCHESTRATION_TARGET_PATTERN}\b.{{0,40}}\b"
    rf"{HARD_CONDITIONAL_OPTOUT_CONNECTOR_PATTERN}\s+{CONDITIONAL_VALUE_PATTERN}\b|"
    rf"\b(?:do not|don['’]?t|dont)\s+orchestrat(?:e|ion)\b.{{0,40}}\b"
    rf"{HARD_CONDITIONAL_OPTOUT_CONNECTOR_PATTERN}\s+{CONDITIONAL_VALUE_PATTERN}\b|"
    rf"\bno(?:\s+more)?\b.{{0,80}}\b"
    rf"{OPTOUT_ORCHESTRATION_TARGET_PATTERN}\b.{{0,40}}\b"
    rf"{HARD_CONDITIONAL_OPTOUT_CONNECTOR_PATTERN}\s+{CONDITIONAL_VALUE_PATTERN}\b|"
    rf"\bwithout\b.{{0,80}}\b"
    rf"{OPTOUT_ORCHESTRATION_TARGET_PATTERN}\b.{{0,40}}\b"
    rf"{HARD_CONDITIONAL_OPTOUT_CONNECTOR_PATTERN}\s+{CONDITIONAL_VALUE_PATTERN}\b)"
)
STANDALONE_HARD_OPTOUT_PATTERN = (
    rf"(?:\bnever\b.{{0,80}}\b"
    rf"(?:use|spawn|run|delegate)\b.{{0,80}}\b"
    rf"{OPTOUT_ORCHESTRATION_TARGET_PATTERN}\b|"
    rf"\bnever\s+orchestrat(?:e|ion)\b|"
    rf"\b(?:do not|don['’]?t|dont)\b.{{0,80}}\b"
    rf"(?:use|spawn|run|delegate)\b.{{0,80}}\b"
    rf"{OPTOUT_ORCHESTRATION_TARGET_PATTERN}\b(?!{SOFT_CONDITIONAL_AFTER_OPTOUT_PATTERN})|"
    rf"\b(?:do not|don['’]?t|dont)\s+orchestrat(?:e|ion)\b"
    rf"(?!{SOFT_CONDITIONAL_AFTER_OPTOUT_PATTERN})|"
    rf"\bno(?:\s+more)?\b.{{0,80}}\b"
    rf"{OPTOUT_ORCHESTRATION_TARGET_PATTERN}\b(?!{SOFT_CONDITIONAL_AFTER_OPTOUT_PATTERN})|"
    rf"\bwithout\b.{{0,80}}\b"
    rf"{OPTOUT_ORCHESTRATION_TARGET_PATTERN}\b(?!{SOFT_CONDITIONAL_AFTER_OPTOUT_PATTERN})|"
    rf"\bwork linearly only\b|"
    rf"\bsingle[- ]?thread(?:ed)? only\b|"
    rf"\blinear execution\b)"
)


def count_signals(text: str, signals: Iterable[SignalSet]) -> tuple[int, list[str]]:
    score = 0
    hits: list[str] = []
    for group in signals:
        for pattern in group.patterns:
            if re.search(pattern, text, flags=re.IGNORECASE):
                score += group.weight
                hits.append(group.label)
                break
    return score, hits


def format_signal_hits(hits: Iterable[str]) -> str:
    return ", ".join(sorted(set(hits)))


def format_signal_reason(message: str, hits: Iterable[str]) -> str:
    signal_hits = format_signal_hits(hits)
    if not signal_hits:
        return message + "."
    return f"{message} ({signal_hits})."


def has_only_topic_complex_hits(hits: Iterable[str]) -> bool:
    unique_hits = set(hits)
    return not unique_hits or unique_hits <= {"explicit subagents"}


def extract_source_files(text: str) -> set[str]:
    return set(re.findall(SOURCE_FILE_PATTERN, text, flags=re.IGNORECASE))


def has_named_single_target_phrase(text: str) -> bool:
    for match in re.finditer(SINGLE_NAMED_TARGET_PHRASE_PATTERN, text, flags=re.IGNORECASE):
        prefix = text[max(0, match.start() - 20):match.start()]
        if re.search(r"\b(?:across|spanning|between)\s+$", prefix, flags=re.IGNORECASE):
            continue
        target_phrase = match.group(0)
        if not re.search(MULTI_TARGET_CONNECTOR_PATTERN, target_phrase, flags=re.IGNORECASE):
            return True
    return False


def has_single_named_target(text: str) -> bool:
    source_files = extract_source_files(text)
    if len(source_files) == 1:
        return True
    if len(source_files) > 1:
        return False

    return bool(
        re.search(SINGLE_TARGET_SCOPE_PATTERN, text, flags=re.IGNORECASE)
        or has_named_single_target_phrase(text)
    )


def has_single_target_review_scope(text: str, hits: Iterable[str]) -> bool:
    unique_hits = set(hits)
    if "review/audit" not in unique_hits:
        return False
    if unique_hits & {"architecture/refactor", "explicit subagents", "multi-surface scope"}:
        return False

    return has_single_named_target(text)


def has_high_risk_single_target_review_with_verification(text: str, hits: Iterable[str]) -> bool:
    if not has_single_target_review_scope(text, hits):
        return False
    return bool(
        re.search(REVIEW_AUDIT_INVESTIGATION_PATTERN, text, flags=re.IGNORECASE)
        and re.search(HIGH_RISK_SINGLE_TARGET_DOMAIN_PATTERN, text, flags=re.IGNORECASE)
        and re.search(HIGH_RISK_SINGLE_TARGET_VERIFICATION_PATTERN, text, flags=re.IGNORECASE)
    )


def has_single_target_debug_scope(text: str, hits: Iterable[str]) -> bool:
    unique_hits = set(hits)
    if "debugging/root-cause" not in unique_hits:
        return False
    if unique_hits & DEBUG_SCOPE_BLOCKING_HITS:
        return False
    if re.search(BROAD_DEBUG_SCOPE_PATTERN, text, flags=re.IGNORECASE):
        return False

    return has_single_named_target(text)


def is_lightweight_text_review(text: str, hits: Iterable[str]) -> bool:
    unique_hits = set(hits)
    if unique_hits - LIGHTWEIGHT_TEXT_REVIEW_ALLOWED_HITS:
        return False
    if re.search(HIGH_RISK_REVIEW_TERM_PATTERN, text, flags=re.IGNORECASE):
        return False
    return bool(
        re.search(LIGHTWEIGHT_TEXT_REVIEW_VERB_PATTERN, text, flags=re.IGNORECASE)
        and re.search(LIGHTWEIGHT_TEXT_REVIEW_SUBJECT_PATTERN, text, flags=re.IGNORECASE)
    )


def is_explanation_only_surface_prompt(text: str, hits: Iterable[str]) -> bool:
    unique_hits = set(hits)
    if not unique_hits or unique_hits - EXPLANATION_ONLY_COMPLEX_HITS:
        return False

    return bool(re.search(EXPLANATION_ONLY_PATTERN, text, flags=re.IGNORECASE))


def has_only_low_risk_topic_hits(hits: Iterable[str]) -> bool:
    unique_hits = set(hits)
    return bool(unique_hits) and not unique_hits - LOW_RISK_TOPIC_COMPLEX_HITS


def is_conceptual_comparison_prompt(text: str, hits: Iterable[str]) -> bool:
    unique_hits = set(hits)
    if "comparison/options" not in unique_hits:
        return False
    if not has_only_low_risk_topic_hits(unique_hits):
        return False
    if not re.search(CONCEPTUAL_COMPARISON_PATTERN, text, flags=re.IGNORECASE):
        return False

    return not re.search(ACTIONABLE_COMPARISON_PATTERN, text, flags=re.IGNORECASE)


def is_tiny_surface_edit_prompt(text: str, hits: Iterable[str], simple_hits: Iterable[str]) -> bool:
    unique_hits = set(hits)
    if not has_only_low_risk_topic_hits(unique_hits):
        return False
    if "multi-surface scope" in unique_hits:
        return bool(
            re.search(EXPLICIT_TINY_EDIT_SCOPE_PATTERN, text, flags=re.IGNORECASE)
            and re.search(TINY_SURFACE_EDIT_PATTERN, text, flags=re.IGNORECASE)
        )
    if "tiny edit" in set(simple_hits):
        return True

    return bool(re.search(TINY_SURFACE_EDIT_PATTERN, text, flags=re.IGNORECASE))


def has_single_target_comparison_scope(text: str, hits: Iterable[str]) -> bool:
    unique_hits = set(hits)
    if "comparison/options" not in unique_hits:
        return False
    if unique_hits & SINGLE_TARGET_COMPARISON_BLOCKING_HITS:
        return False

    return has_single_named_target(text)


def has_canonical_child_agent_header(text: str) -> bool:
    return bool(re.search(CUSTOM_AGENT_HEADER_PATTERN, text, flags=re.IGNORECASE))


def has_hard_opt_out(text: str, optout_score: int, conditional_score: int) -> bool:
    if not optout_score:
        return False
    if not conditional_score:
        return True
    return bool(
        re.search(HARD_CONDITIONAL_OPTOUT_PATTERN, text, flags=re.IGNORECASE)
        or re.search(STANDALONE_HARD_OPTOUT_PATTERN, text, flags=re.IGNORECASE)
    )


OPTOUT_SIGNALS = (
    SignalSet("explicit user opt-out", 99, (
        r"\bdo not use sub[- ]?agents?\b",
        r"\bdon['’]?t use sub[- ]?agents?\b",
        r"\bdont use sub[- ]?agents?\b",
        r"\bno sub[- ]?agents?\b",
        r"\bnever use sub[- ]?agents?\b",
        r"\bwithout sub[- ]?agents?\b",
        r"\bdo not (?:spawn|run|delegate)(?:\s+(?:any|more|parallel))?\s+(?:sub[- ]?agents?|agents?)\b",
        r"\bdon['’]?t (?:spawn|run|delegate)(?:\s+(?:any|more|parallel))?\s+(?:sub[- ]?agents?|agents?)\b",
        r"\bnever (?:spawn|run|delegate)(?:\s+(?:any|more|parallel))?\s+(?:sub[- ]?agents?|agents?)\b",
        r"\bwithout (?:spawn(?:ing)?|running|delegating)(?:\s+(?:any|more|parallel))?\s+(?:sub[- ]?agents?|agents?)\b",
        r"\bno more (?:sub[- ]?agents?|agents?)\b",
        r"\bno parallel agents?\b",
        r"\bwithout parallel agents?\b",
        r"\bno orchestrat(?:ion|e)\b",
        r"\bnever orchestrat(?:e|ion)\b",
        r"\bnever use orchestrat(?:ion|e)\b",
        r"\bdon['’]?t orchestrat(?:e|ion)\b",
        r"\bdont orchestrat(?:e|ion)\b",
        r"\bdo not orchestrat(?:e|ion)\b",
        r"\bdo not use orchestrat(?:ion|e)\b",
        r"\bdon['’]?t use orchestrat(?:ion|e)\b",
        r"\bdont use orchestrat(?:ion|e)\b",
        r"\bwithout orchestrat(?:ion|e)\b",
        r"\bwork linearly\b",
        r"\b(?:work|review|debug|audit|proceed|handle)\b.{0,40}\blinearly\b",
        r"\blinear execution\b",
        r"\bsingle[- ]?thread(?:ed)? only\b",
        r"\b(?:avoid|minimi[sz]e|reduce|limit)\b.{0,60}\b(?:agent|sub[- ]?agents?|orchestrat(?:ion|e))(?:/tool)?\s+(?:costs?|budget|spend)\b",
    )),
)

RECURSION_GUARD_SIGNALS = (
    SignalSet("child-agent recursion guard", 99, (
        CUSTOM_AGENT_HEADER_PATTERN,
        r"\bdispatched as (?:a )?sub[- ]?agent\b",
        r"\byou are a sub[- ]?agent\b",
        rf"\byou are {CUSTOM_AGENT_PATTERN}\b",
        r"\bbounded sub[- ]?agent task\b",
        rf"\btask for {CUSTOM_AGENT_PATTERN}\b",
        r"\bparent agent\b.*\basked\b",
    )),
)

CONDITIONAL_ORCHESTRATION_SIGNALS = (
    SignalSet("conditional orchestration", 4, (
        rf"\b{CONDITIONAL_AGENT_ACTION_PATTERN}\b.{{0,80}}\b{CONDITIONAL_AGENT_TARGET_PATTERN}\b.{{0,40}}\b{CONDITIONAL_CONNECTOR_PATTERN}\s+{CONDITIONAL_VALUE_PATTERN}\b",
        rf"\bdelegate\b.{{0,80}}\b{CONDITIONAL_CONNECTOR_PATTERN}\s+{CONDITIONAL_VALUE_PATTERN}\b",
        rf"\borchestrat(?:e|ion)\b.{{0,40}}\b{CONDITIONAL_CONNECTOR_PATTERN}\s+{CONDITIONAL_VALUE_PATTERN}\b",
        rf"\b{CONDITIONAL_ORCHESTRATION_TARGET_PATTERN}\b.{{0,40}}\b{CONDITIONAL_CONNECTOR_PATTERN}\s+{CONDITIONAL_VALUE_PATTERN}\b",
        rf"\b{CONDITIONAL_AGENT_ACTION_PATTERN}\b.{{0,80}}\b{CONDITIONAL_AGENT_TARGET_PATTERN}\b.{{0,40}}\bas\s+{CONDITIONAL_VALUE_PATTERN}\b",
        rf"\borchestrat(?:e|ion)\b.{{0,40}}\bas\s+{CONDITIONAL_VALUE_PATTERN}\b",
    )),
)

EDUCATIONAL_TOPIC_SIGNALS = (
    SignalSet("subagent topic explanation", 4, (
        r"^\s*(?:explain|summari[sz]e)\b.{0,120}\b(?:sub[- ]?agents?|orchestrat(?:or|ion|e))\b",
        r"^\s*(?:what|why|how)\b.{0,120}\b(?:sub[- ]?agents?|orchestrat(?:or|ion|e))\b.{0,80}\b(?:work|works|mean|means|behav(?:e|ior)|concept|topic)\b",
        r"^\s*(?:what|why|how)\b.{0,120}\b(?:work|works|mean|means|behav(?:e|ior)|concept|topic)\b.{0,80}\b(?:sub[- ]?agents?|orchestrat(?:or|ion|e))\b",
    )),
)

COMPLEX_SIGNALS = (
    SignalSet("debugging/root-cause", 3, (r"\bdebug\b", r"\binvestigat(?:e|ion)\b", r"root cause", r"\bfail(?:s|ed|ing|ure)?\b", r"flaky", r"regression", r"race condition", r"\bcrash(?:es|ed|ing)?\b", r"\berrors?\b")),
    SignalSet("review/audit", 3, (FORMAL_REVIEW_PATTERN, r"audit", r"security", r"threat", r"vulnerabilit(?:y|ies)", r"\brisk\b")),
    SignalSet("parallelizable uncertainty", 3, (
        r"\bmultiple plausible causes\b",
        r"\broot causes\b",
        r"\bhypotheses\b",
        r"\btriage\b",
        r"\bnarrow down\b",
        r"\bunknown scope\b",
        r"\bnot sure where\b",
    )),
    SignalSet("cross-cutting integration", 2, (
        r"\bcross[- ]cutting\b",
        r"\bend[- ]to[- ]end\b",
        r"\bintegration\b.{0,80}\b(?:across|between|contract tests?|product and tests|frontend and backend|client and server)\b",
        r"\b(?:across|between)\b.{0,80}\bintegration\b",
        r"\bcontract tests?\b.{0,80}\b(?:across|between|frontend|backend|client|server)\b",
        r"\bproduct and tests\b",
        r"\bfrontend and backend\b",
        r"\bclient and server\b",
    )),
    SignalSet("runtime/performance/concurrency", 2, (
        r"\btimeouts?\b",
        r"\blatenc(?:y|ies)\b",
        r"\bmemory leaks?\b",
        r"\bdeadlocks?\b",
        r"\bconcurrenc(?:y|ies)\b",
        r"\brace conditions?\b",
        r"\bthroughput\b",
        r"\block contention\b",
    )),
    SignalSet("release/data risk", 2, (
        r"\bmigrations?\b",
        r"\bschemas?\b",
        r"\bbackfills?\b",
        r"\brollbacks?\b",
        r"\bcompatib(?:le|ility)\b",
        r"\bbreaking changes?\b",
    )),
    SignalSet("output/status review", 3, (
        rf"\breview\b.{{0,80}}\b(?:status feedback|hook context|outputs?|results?|messages?|labels?)\b",
    )),
    SignalSet("output/status wording", 2, (
        rf"\b{OUTPUT_OR_STATUS_TERM_PATTERN}\b.{{0,80}}\b{OUTPUT_QUALITY_TERM_PATTERN}\b",
        rf"\b{OUTPUT_QUALITY_TERM_PATTERN}\b.{{0,80}}\b{OUTPUT_OR_STATUS_TERM_PATTERN}\b",
    )),
    SignalSet("exhaustive result sweep", 4, (
        rf"\b{RESULT_SWEEP_PATTERN}\b",
        rf"\breview\b.{{0,80}}\b{RESULT_SWEEP_PATTERN}\b",
    )),
    SignalSet("validation sweep", 3, (
        rf"{VALIDATION_VERB_PATTERN}.{{0,120}}{VALIDATION_TARGET_PATTERN}",
        rf"{VALIDATION_TARGET_PATTERN}.{{0,120}}{VALIDATION_VERB_PATTERN}",
        r"\bno remaining\b.{0,80}\b(?:mentions?|references?|plugin)\b",
    )),
    SignalSet("architecture/refactor", 3, (r"architecture", r"refactor", r"rewrite", r"large change", r"multi[- ]?file", r"multi[- ]?module", r"multi[- ]?service")),
    SignalSet("multi-surface scope", 3, (
        rf"\bacross\b.*\b{SURFACE_TERM_PATTERN}\b.*\b{SURFACE_TERM_PATTERN}\b",
        rf"\b{SURFACE_TERM_PATTERN}\b.*\band\b.*\b{SURFACE_TERM_PATTERN}\b",
        rf"\bspanning\b.*\b{SURFACE_TERM_PATTERN}\b.*\b{SURFACE_TERM_PATTERN}\b",
    )),
    SignalSet("tests/verification", 2, (TEST_VERIFICATION_TERM_PATTERN,)),
    SignalSet("research/docs", 2, (DOCUMENTATION_TERM_PATTERN, r"api", r"version", r"latest", r"framework", r"library")),
    SignalSet("documentation/qa", 2, (
        rf"{DOCUMENTATION_TERM_PATTERN}.{{0,120}}{QA_TERM_PATTERN}",
        rf"{QA_TERM_PATTERN}.{{0,120}}{DOCUMENTATION_TERM_PATTERN}",
    )),
    SignalSet("setup/config", 2, (SETUP_TERM_PATTERN,)),
    SignalSet("plugin migration cleanup", 2, (
        PLUGIN_MIGRATION_TERM_PATTERN,
        r"\bremaining\s+(?:mentions?|references?)\b.{0,80}\bplugin\b",
    )),
    SignalSet("comparison/options", 2, (r"compare", r"options", r"trade[- ]?offs?", r"alternatives?", r"approaches?")),
    SignalSet("explicit subagents", 5, (r"sub[- ]?agents?", r"parallel agents?", r"orchestrat", r"delegate", r"split .*agents?")),
)
COMPLEX_SIGNAL_WEIGHT_BY_LABEL = {group.label: group.weight for group in COMPLEX_SIGNALS}
CONDITIONAL_PERMISSION_COMPLEX_LABELS = {"explicit subagents"}


def discount_conditional_permission_complexity(
    conditional_score: int,
    complex_score: int,
    complex_hits: list[str],
) -> tuple[int, list[str]]:
    if not conditional_score:
        return complex_score, complex_hits

    # Conditional permission mentions agents/orchestration by necessity. Do not
    # let that wording alone satisfy the strong-spawn threshold; require other
    # task-complexity signals to carry the escalation.
    ignored_hits = set(complex_hits) & CONDITIONAL_PERMISSION_COMPLEX_LABELS
    adjusted_score = complex_score - sum(COMPLEX_SIGNAL_WEIGHT_BY_LABEL[label] for label in ignored_hits)
    adjusted_hits = [hit for hit in complex_hits if hit not in ignored_hits]
    return adjusted_score, adjusted_hits

SIMPLE_SIGNALS = (
    SignalSet("simple question", 2, (r"^\s*(what|why|how)\b", r"explain", r"summari[sz]e")),
    SignalSet("tiny edit", 3, (r"typo", r"one[- ]?line", r"tiny", r"small", r"quick", r"rename")),
    SignalSet("direct ask", 1, (r"^\s*(give me|write|draft|compose)\b",)),
)
ACTION_CONTRACTS = {
    "use-subagent-orchestrator": (
        "Production contract: invoke the subagent-orchestrator skill before broad work; "
        "choose single-thread, sequential-plan, or parallel-subagents; if parallel-subagents is selected and "
        "tool policy permits, emit a compact why-parallel proof, define bounded roles, spawn the smallest useful "
        "bounded read-only roster, wait, then synthesize before edits; if spawning is unavailable or blocked, "
        "state the blocker and continue with the closest safe fallback."
    ),
    "orchestration-check": (
        "Production contract: run the orchestration checklist; load subagent-orchestrator only if independent "
        "tracks are clear; spawn only if at least two independent read-only tracks can return independently useful "
        "outputs, no concrete blocker exists, parallel-subagents is selected, and tool policy permits."
    ),
}

def classify(prompt: str) -> tuple[str, str]:
    text = prompt.strip()

    conditional_score, conditional_hits = count_signals(text, CONDITIONAL_ORCHESTRATION_SIGNALS)
    recursion_score, recursion_hits = count_signals(text, RECURSION_GUARD_SIGNALS)
    if recursion_score and has_canonical_child_agent_header(text):
        return (
            "recursion-guard",
            format_signal_reason("Bounded child-agent task detected", recursion_hits),
        )

    optout_score, optout_hits = count_signals(text, OPTOUT_SIGNALS)
    if has_hard_opt_out(text, optout_score, conditional_score):
        return (
            "orchestration-opt-out",
            format_signal_reason("Explicit orchestration opt-out detected", optout_hits),
        )

    if recursion_score:
        return (
            "recursion-guard",
            format_signal_reason("Bounded child-agent task detected", recursion_hits),
        )

    complex_score, complex_hits = count_signals(text, COMPLEX_SIGNALS)
    effective_complex_score, effective_complex_hits = discount_conditional_permission_complexity(
        conditional_score,
        complex_score,
        complex_hits,
    )
    simple_score, simple_hits = count_signals(text, SIMPLE_SIGNALS)
    educational_score, educational_hits = count_signals(text, EDUCATIONAL_TOPIC_SIGNALS)

    if educational_score and has_only_topic_complex_hits(effective_complex_hits):
        return (
            "single-thread-likely",
            format_signal_reason("Educational subagent-topic prompt detected", educational_hits),
        )

    if simple_score >= 2 and is_explanation_only_surface_prompt(text, effective_complex_hits):
        return (
            "single-thread-likely",
            format_signal_reason("Simple explanatory surface prompt detected", simple_hits),
        )

    if is_tiny_surface_edit_prompt(text, effective_complex_hits, simple_hits):
        return (
            "single-thread-likely",
            format_signal_reason("Tiny surface edit detected", simple_hits),
        )

    if is_conceptual_comparison_prompt(text, effective_complex_hits):
        if simple_score >= 2:
            return (
                "single-thread-likely",
                format_signal_reason("Conceptual comparison detected", simple_hits),
            )
        return (
            "single-thread-default",
            format_signal_reason("Conceptual comparison detected", effective_complex_hits),
        )

    if is_lightweight_text_review(text, effective_complex_hits):
        return (
            "single-thread-default",
            format_signal_reason("Lightweight text review detected", ["text-only review"]),
        )

    if effective_complex_score >= 5 and has_single_target_comparison_scope(text, effective_complex_hits):
        return (
            "single-thread-default",
            format_signal_reason("Single-target comparison detected", effective_complex_hits),
        )

    if (
        effective_complex_score >= 5
        and has_high_risk_single_target_review_with_verification(text, effective_complex_hits)
    ):
        return (
            "use-subagent-orchestrator",
            format_signal_reason(
                "High-risk single-target review detected",
                [*effective_complex_hits, "high-risk single target"],
            ),
        )

    if effective_complex_score >= 5 and has_single_target_review_scope(text, effective_complex_hits):
        return (
            "orchestration-check",
            format_signal_reason("Single-target review/audit detected", effective_complex_hits),
        )

    if effective_complex_score >= 3 and has_single_target_debug_scope(text, effective_complex_hits):
        return (
            "single-thread-likely",
            format_signal_reason("Single-target debugging/root-cause detected", effective_complex_hits),
        )

    if effective_complex_score >= 5 and effective_complex_score > simple_score + 1:
        return (
            "use-subagent-orchestrator",
            format_signal_reason("Strong orchestration signals detected", effective_complex_hits),
        )

    if conditional_score:
        return (
            "orchestration-check",
            format_signal_reason("Conditional orchestration request detected", conditional_hits),
        )

    if effective_complex_score >= 3:
        return (
            "orchestration-check",
            format_signal_reason("Moderate orchestration signals detected", effective_complex_hits),
        )
    if simple_score >= 2 and effective_complex_score <= 2:
        return (
            "single-thread-likely",
            format_signal_reason("Simple-task signals detected", simple_hits),
        )
    return ("single-thread-default", "No strong orchestration signals detected.")


def format_result_context(decision: str, reason: str) -> str:
    lines = [
        "Subagent orchestration gate",
        f"Result: {decision}",
        f"Reason: {reason}",
    ]
    if action_contract := ACTION_CONTRACTS.get(decision):
        lines.append(f"Action: {action_contract}")
    return "\n".join(lines)


def print_parse_error(message: str) -> None:
    print(json.dumps({"systemMessage": f"subagent orchestration hook could not parse input: {message}"}))


def read_payload() -> dict[str, object] | None:
    try:
        payload = json.load(sys.stdin)
    except Exception as exc:  # Fail open: hooks should not break normal Codex usage.
        print_parse_error(str(exc))
        return None

    if not isinstance(payload, dict):
        print_parse_error(f"expected object payload, got {type(payload).__name__}")
        return None

    return payload


def main() -> int:
    payload = read_payload()
    if payload is None:
        return 0

    prompt = str(payload.get("prompt", ""))
    decision, reason = classify(prompt)
    additional_context = format_result_context(decision, reason)
    hook_output = {
        "hookEventName": "UserPromptSubmit",
        "additionalContext": additional_context,
    }

    print(json.dumps({
        "hookSpecificOutput": hook_output
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

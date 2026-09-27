"""List prices for the judge council AND agent models, for finance reporting.

Sources, USD per million tokens:
  * Anthropic Claude API pricing (platform.claude.com/docs/en/pricing),
    cached 2026-08-13. Cache multipliers per the prompt caching docs:
    reads ~0.1x input price, writes ~1.25x (5-minute TTL).
  * OpenAI (developers.openai.com/api/docs/models/gpt-5.6-sol), cached
    2026-09-01. Its cached-input rate is $0.4 against $4 input — exactly the
    0.1x CACHE_READ_MULTIPLIER below — so the same cache arithmetic applies
    unchanged to both providers.
  * Z.ai GLM API pricing (docs.z.ai/guides/overview/pricing), cached
    2026-09-07. GLM-5.3: $1.40 input / $4.40 output, cached input $0.26/MTok
    (≈0.186x input, NOT the 0.1x default — see _CACHE_READ_RATE). GLM-5.3-Flash:
    $0.15 input / $0.50 output (list price; a launch promo halved it through
    2026-09-09, but list rate is used so records don't jump when the promo ends).

Model ids may carry a `provider/` prefix (`openai/gpt-5.6`); it is stripped
before lookup. Rates are keyed on the bare id, so one entry serves a seat
however its roster spells it.

Sonnet 5 has introductory pricing through 2026-08-31; `rates_for` is
date-aware so costs computed after the intro window use the standard rate.

Update this table when provider pricing changes — it is the single source
both harness.py (finance payload) and harness/collect_results.py
(usage.json) price judge AND agent tokens from. Judge tokens go through
judge_cost_usd(); agent tokens through agent_cost_usd() (same arithmetic,
separate name so callers read clearly).
"""

from __future__ import annotations

import logging
from datetime import date

logger = logging.getLogger("judge.pricing")

_PROVIDER_PREFIXES = ("anthropic/", "openai/", "zai/")

_ANTHROPIC_SHORT_PREFIXES = ("opus-", "sonnet-", "haiku-")

_QUOTA_FUNDED_PREFIXES = ("codex/",)

_STANDARD: dict = {
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-opus-4-7": (5.00, 25.00),
    "claude-opus-4-6": (5.00, 25.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "gpt-5.6-sol": (4.00, 20.00),
    "gpt-5.6": (4.00, 20.00),
    "glm-5.3": (1.40, 4.40),
    "glm-5.3-flash": (0.15, 0.50),
}

_CACHE_READ_RATE: dict[str, float] = {
    "glm-5.3": 0.26,
}

_LONG_CONTEXT_SURCHARGE: dict[str, tuple[int, float, float]] = {
    "gpt-5.6-sol": (272_000, 2.0, 1.5),
    "gpt-5.6": (272_000, 2.0, 1.5),
}

_SONNET5_INTRO = (2.00, 10.00)
_SONNET5_INTRO_ENDS = date(2026, 8, 31)

CACHE_READ_MULTIPLIER = 0.10
CACHE_WRITE_MULTIPLIER = 1.25


def _bare(model: str) -> str:
    """Strip a known `provider/` prefix from a judge model id.

    Without this, a roster entry of `openai/gpt-5.6` misses the exact-match
    lookup below and prices at $0.00 with no error — the quiet failure mode
    this whole module exists to keep out of the finance payload.
    """
    m = (model or "").strip()
    for prefix in _PROVIDER_PREFIXES:
        if m.startswith(prefix):
            return m[len(prefix):]
    return m


def _key(model: str) -> str:
    """The table key for a model id: prefix stripped, short alias expanded.

    Every lookup in this module goes through here rather than through `_bare`
    directly, so `opus-5`, `anthropic/opus-5` and `claude-opus-5` are one row
    and cannot price differently depending on which spelling a caller happens
    to hold. `_bare` stays prefix-stripping only, because that is what its
    callers in council.py mean by it.
    """
    bare = _bare(model)
    if bare.startswith(_ANTHROPIC_SHORT_PREFIXES):
        return f"claude-{bare}"
    return bare


_UNPRICED_WARNED: set[str] = set()


def rates_for(model: str, on: date | None = None) -> tuple[float, float] | None:
    """(input, output) USD/MTok for a model, or None if unknown."""
    on = on or date.today()
    bare = _key(model)
    if bare == "claude-sonnet-5" and on <= _SONNET5_INTRO_ENDS:
        return _SONNET5_INTRO
    return _STANDARD.get(bare)


def _cost_usd(
    model: str,
    role: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    cache_write_tokens: int,
    on: date | None,
) -> float:
    """Shared list-rate pricing for judge and agent tokens. 0.0 for unknown
    models — a missing price must never crash a billing post; log-side
    visibility comes from the token counts, which are always reported.

    `role` only shapes the warning text ("judge model" / "agent model")."""
    if (model or "").strip().startswith(_QUOTA_FUNDED_PREFIXES):
        return 0.0
    rates = rates_for(model, on)
    if rates is None:
        if model not in _UNPRICED_WARNED:
            _UNPRICED_WARNED.add(model)
            logger.warning(
                "no list price for %s model %r — cost reported as 0.0 "
                "(token counts are unaffected); add it to judge/pricing.py",
                role, model,
            )
        return 0.0
    input_rate, output_rate = rates
    bare = _key(model)
    surcharge = _LONG_CONTEXT_SURCHARGE.get(bare)
    if surcharge:
        threshold, input_multiplier, output_multiplier = surcharge
        if input_tokens + cache_read_tokens > threshold:
            input_rate *= input_multiplier
            output_rate *= output_multiplier
    if bare in _CACHE_READ_RATE:
        cache_read_rate = _CACHE_READ_RATE[bare]
        if surcharge and input_tokens + cache_read_tokens > surcharge[0]:
            cache_read_rate *= surcharge[1]
    else:
        cache_read_rate = input_rate * CACHE_READ_MULTIPLIER
    cost = (
        input_tokens * input_rate
        + output_tokens * output_rate
        + cache_read_tokens * cache_read_rate
        + cache_write_tokens * input_rate * CACHE_WRITE_MULTIPLIER
    ) / 1_000_000
    return round(cost, 6)


def per_token_rates(model: str, on: date | None = None) -> dict[str, float] | None:
    """LiteLLM-shaped per-TOKEN rates for a model, or None when it is unpriced.

    ``litellm.register_model()`` takes dollars per token; providers publish —
    and this table holds — dollars per million. harness.py feeds the result
    straight into the ``model_info`` block it registers GLM with, so the
    conversion belongs here, beside the rates, not at the call site where it
    would drift the moment a price changes.

    The long-context surcharge is deliberately NOT folded in: it depends on the
    size of an individual request, and these are the flat rates a registration
    carries. Anything needing the surcharge prices through ``agent_cost_usd``.
    """
    rates = rates_for(model, on)
    if rates is None:
        return None
    input_rate, output_rate = rates
    bare = _key(model)
    cache_read = _CACHE_READ_RATE.get(bare, input_rate * CACHE_READ_MULTIPLIER)
    return {
        "input_cost_per_token": input_rate / 1_000_000,
        "output_cost_per_token": output_rate / 1_000_000,
        "cache_creation_input_token_cost":
            input_rate * CACHE_WRITE_MULTIPLIER / 1_000_000,
        "cache_read_input_token_cost": cache_read / 1_000_000,
    }


def judge_cost_usd(
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    on: date | None = None,
) -> float:
    """Price one judge's usage at list rates. 0.0 for unknown models."""
    return _cost_usd(
        model, "judge", input_tokens, output_tokens,
        cache_read_tokens, cache_write_tokens, on,
    )


def agent_cost_usd(
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    on: date | None = None,
) -> float:
    """Price a trajectory agent's usage at list rates. 0.0 for unknown models.

    Used to backfill agent cost when the runner (harbor) reports cost_usd=0 —
    notably GLM via zbridge, which posts token counts but no dollar figure.
    Same arithmetic as judge_cost_usd; separate name so call sites read clearly
    and the unpriced warning names the agent, not a judge seat."""
    return _cost_usd(
        model, "agent", input_tokens, output_tokens,
        cache_read_tokens, cache_write_tokens, on,
    )

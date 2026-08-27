"""
Hard per-day spend cap. Enforced in code, not by discipline.

Why this exists: a multi-step agent loop resends the whole history every turn.
~15 turns x ~20k context is roughly $1 per case at Sonnet rates. Twenty demo
rehearsals is $200+ — four times the entire budget. The council modelled $76
expected against $50 available. This file is what closes that gap.

    from backend.app.agents.budget import check_and_charge
    check_and_charge(0.01)   # before every Bedrock invocation
"""

from __future__ import annotations

import datetime
import json
import os
import pathlib

LEDGER = pathlib.Path(os.getenv("BUDGET_LEDGER", ".budget.json"))
DAILY_USD = float(os.getenv("DAILY_USD_CAP", "1.50"))


class BudgetExceeded(RuntimeError):
    pass


def _load() -> dict:
    today = datetime.date.today().isoformat()
    if LEDGER.exists():
        try:
            d = json.loads(LEDGER.read_text())
            if d.get("date") == today:
                return d
        except (json.JSONDecodeError, OSError):
            pass
    return {"date": today, "spent": 0.0, "calls": 0}


def check_and_charge(est_usd: float) -> float:
    """Raise BudgetExceeded rather than spend past the daily cap.

    Only meters Bedrock. The Anthropic-direct dev path is free of the $50.
    """
    if os.getenv("MODEL_PROVIDER", "anthropic").lower() != "bedrock":
        return 0.0

    d = _load()
    if d["spent"] + est_usd > DAILY_USD:
        raise BudgetExceeded(
            f"daily cap reached: ${d['spent']:.3f} spent of ${DAILY_USD:.2f} "
            f"({d['calls']} calls). Raise DAILY_USD_CAP only deliberately."
        )
    d["spent"] += est_usd
    d["calls"] += 1
    LEDGER.write_text(json.dumps(d))
    return d["spent"]


def spent_today() -> float:
    return _load()["spent"]


def estimate(in_tokens: int, out_tokens: int, cached_in: int = 0) -> float:
    """Claude Haiku 4.5: $1/M in, $5/M out, cache reads 10% of input."""
    fresh = max(0, in_tokens - cached_in)
    return (fresh / 1e6) * 1.00 + (cached_in / 1e6) * 0.10 + (out_tokens / 1e6) * 5.00

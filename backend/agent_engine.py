"""Structured agent decision engine for RevenueRescue AI.

This module exposes concise decision summaries rather than hidden chain-of-thought.
The engine is deterministic by default, making the hackathon demo reliable without
an external model, while the AI service can enrich narratives when configured.
"""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo
from typing import Any, Dict, Iterable, List, Optional


QUIET_START = time(21, 0)  # 9 PM
QUIET_END = time(8, 0)     # 8 AM
LOCAL_TZ = ZoneInfo("Asia/Kolkata")
OPT_OUT_KEYWORDS = {"STOP", "UNSUBSCRIBE", "OPT OUT", "OPTOUT", "CANCEL"}


FAILURE_STRATEGIES: Dict[str, Dict[str, Any]] = {
    "Network Error": {
        "diagnosis": "Likely transient network/connectivity failure.",
        "action": "Retry automatically after a short delay.",
        "strategy": "Optimized Retry",
        "probability": 90,
        "max_attempts": 2,
        "reason": "Transient failures are comparatively likely to recover on a later attempt.",
    },
    "Bank Timeout": {
        "diagnosis": "The bank did not respond within the expected processing window.",
        "action": "Retry after a short delay with bounded attempts.",
        "strategy": "Bank Timeout Retry",
        "probability": 85,
        "max_attempts": 2,
        "reason": "A timeout can be transient, so a bounded retry is appropriate.",
    },
    "Payment Gateway Error": {
        "diagnosis": "The payment gateway reported a processing failure.",
        "action": "Retry using the available payment route and stop if repeated.",
        "strategy": "Gateway Retry",
        "probability": 80,
        "max_attempts": 2,
        "reason": "Gateway failures may be temporary but repeated failures require stopping.",
    },
    "Card Declined": {
        "diagnosis": "The issuing bank declined the transaction.",
        "action": "Ask the customer to use another payment method.",
        "strategy": "Alternate Payment Method",
        "probability": 60,
        "max_attempts": 1,
        "reason": "Repeatedly retrying a bank-declined card is less appropriate than offering an alternate method.",
    },
    "Insufficient Funds": {
        "diagnosis": "The transaction appears consistent with insufficient available balance.",
        "action": "Send a payment reminder and allow one later recovery attempt.",
        "strategy": "Timed Reminder + Retry",
        "probability": 40,
        "max_attempts": 1,
        "reason": "A later attempt may succeed after the customer's available balance changes.",
    },
}


def safe_upper(value: Any, default: str = "NO") -> str:
    if value is None:
        return default
    return str(value).strip().upper()


def compliance_check(
    *,
    consent: Any = "YES",
    dnd: Any = "NO",
    attempts_used: int = 0,
    max_attempts: int = 1,
    now: Optional[datetime] = None,
    channel: str = "WHATSAPP",
) -> Dict[str, Any]:
    now = now or datetime.now(LOCAL_TZ)
    consent_value = safe_upper(consent, "NO")
    dnd_value = safe_upper(dnd, "NO")
    current = now.time()
    in_quiet = current >= QUIET_START or current < QUIET_END
    reasons: List[str] = []

    if consent_value != "YES":
        reasons.append("Customer consent is unavailable.")
    if dnd_value == "YES":
        reasons.append("Customer is marked DND.")
    if attempts_used >= max_attempts:
        reasons.append("Maximum recovery attempts have been reached.")
    if channel.upper() in {"SMS", "WHATSAPP", "EMAIL"} and in_quiet:
        reasons.append("Current time is within configured quiet hours (9 PM–8 AM).")

    allowed = not reasons
    return {
        "allowed": allowed,
        "decision": "ALLOWED" if allowed else "BLOCKED",
        "reasons": reasons or ["Consent, DND, attempt limit and quiet-hour checks passed."],
        "quiet_hours": in_quiet,
        "policy": "Automated outreach permitted 8 AM–9 PM; quiet hours 9 PM–8 AM.",
        "channel": channel.upper(),
    }


def build_agent_decision(
    *,
    case_type: str,
    case_id: str,
    amount: float,
    priority_score: float,
    priority: str,
    diagnosis: str,
    strategy: str,
    strategy_reason: str,
    next_action: str,
    compliance: Dict[str, Any],
    attempts_used: int,
    max_attempts: int,
    failure_reason: str = "",
) -> Dict[str, Any]:
    stages = [
        {"stage": "Detection", "status": "COMPLETE", "summary": f"{case_type} {case_id} identified as a recovery candidate."},
        {"stage": "Prioritization", "status": "COMPLETE", "summary": f"Priority {priority} from score {priority_score:.1f}/100."},
        {"stage": "Failure Diagnosis", "status": "COMPLETE", "summary": diagnosis},
        {"stage": "Strategy Selection", "status": "COMPLETE", "summary": f"{strategy}: {strategy_reason}"},
        {"stage": "Compliance Check", "status": "PASS" if compliance["allowed"] else "BLOCKED", "summary": " ".join(compliance["reasons"])},
        {"stage": "Action Execution", "status": "READY" if compliance["allowed"] else "STOPPED", "summary": next_action},
        {"stage": "Outcome Evaluation", "status": "PENDING", "summary": "Outcome will be evaluated after the action."},
        {"stage": "Retry / Stop / Escalate", "status": "PENDING", "summary": f"Attempt {attempts_used} of {max_attempts}."},
        {"stage": "Audit Logging", "status": "READY", "summary": "Decision is recorded as a structured backend audit event."},
    ]
    return {
        "case_type": case_type,
        "case_id": str(case_id),
        "priority_score": float(priority_score),
        "priority": priority,
        "diagnosis": diagnosis,
        "failure_reason": failure_reason,
        "strategy": strategy,
        "strategy_reason": strategy_reason,
        "next_action": next_action,
        "compliance": compliance,
        "attempts_used": int(attempts_used),
        "max_attempts": int(max_attempts),
        "attempts_remaining": max(0, int(max_attempts) - int(attempts_used)),
        "factors_considered": [
            "revenue value",
            "recovery likelihood",
            "failure/abandonment context",
            "previous attempts",
            "consent and DND",
            "quiet hours",
            "maximum outreach policy",
        ],
        "stages": stages,
    }

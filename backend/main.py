from __future__ import annotations

import math
import os
import random
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from agent_engine import FAILURE_STRATEGIES, build_agent_decision, compliance_check, safe_upper
from ai_service import ai_service


app = FastAPI(
    title="RevenueRescue AI",
    description="AI-powered autonomous revenue recovery command center",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
if not DATA_DIR.exists():
    DATA_DIR = BASE_DIR.parent if (BASE_DIR.parent / "transactions.csv").exists() else BASE_DIR


def load_csv(name: str, columns: Optional[List[str]] = None) -> pd.DataFrame:
    path = DATA_DIR / name
    if path.exists():
        return pd.read_csv(path)
    return pd.DataFrame(columns=columns or [])


TRANSACTION_COLUMNS = ["transaction_id", "amount", "payment_method", "status", "failure_reason", "created_at"]
CHECKOUT_COLUMNS = ["checkout_id", "customer_id", "cart_amount", "items_count", "abandonment_stage", "minutes_since_abandonment", "previous_successful_orders", "previous_abandonments", "recovery_consent", "status", "abandoned_at"]
INVOICE_COLUMNS = ["invoice_id", "customer_name", "customer_email", "invoice_amount", "invoice_date", "due_date", "days_overdue", "aging_bucket", "payment_status", "recovery_stage", "consent", "dnd", "promise_to_pay_date", "promise_to_pay_amount", "promise_status"]

df = load_csv("transactions.csv", TRANSACTION_COLUMNS)
checkout_df = load_csv("checkout_abandonments.csv", CHECKOUT_COLUMNS)
invoices_df = load_csv("invoices.csv", INVOICE_COLUMNS)

if not df.empty:
    df["transaction_id"] = df["transaction_id"].astype(str)
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0)
    df["status"] = df["status"].fillna("FAILED").astype(str).str.upper()
    df["failure_reason"] = df["failure_reason"].fillna("").astype(str)
if not checkout_df.empty:
    checkout_df["checkout_id"] = checkout_df["checkout_id"].astype(str)
    checkout_df["cart_amount"] = pd.to_numeric(checkout_df["cart_amount"], errors="coerce").fillna(0)
    checkout_df["status"] = checkout_df["status"].fillna("ABANDONED").astype(str).str.upper()
    checkout_df["recovery_consent"] = checkout_df["recovery_consent"].fillna("NO").astype(str).str.upper()
if not invoices_df.empty:
    invoices_df["invoice_id"] = invoices_df["invoice_id"].astype(str)
    for col in ["invoice_amount", "promise_to_pay_amount", "days_overdue"]:
        invoices_df[col] = pd.to_numeric(invoices_df[col], errors="coerce").fillna(0)
    for col in ["payment_status", "recovery_stage", "consent", "dnd", "promise_status"]:
        invoices_df[col] = invoices_df[col].fillna("").astype(str)

# Runtime state. The hackathon demo is intentionally in-memory; restarting the backend resets it.
recovery_attempts: Dict[str, int] = {}
checkout_recovery_attempts: Dict[str, int] = {}
receivables_recovery_attempts: Dict[str, int] = {}
recovered_transactions: Dict[str, int] = {}
recovered_checkouts: Dict[str, int] = {}
recovered_invoices: Dict[str, int] = {}
payment_links: Dict[str, Dict[str, Any]] = {}
mandate_state: Dict[str, Dict[str, Any]] = {}
manual_review_cases: Dict[str, Dict[str, Any]] = {}
opted_out_entities: set[str] = set()
audit_log: List[Dict[str, Any]] = []

MAX_CHECKOUT_MESSAGES = 2
MAX_B2B_OUTREACH = 3
LOCAL_TZ = ZoneInfo("Asia/Kolkata")


class PromiseToPayPayload(BaseModel):
    promise_date: str = Field(min_length=10, max_length=10)
    promise_amount: float = Field(gt=0)


class ManualReviewActionPayload(BaseModel):
    action: str
    note: str = ""


class OptOutPayload(BaseModel):
    entity_type: str
    entity_id: str
    keyword: str


class MessagePayload(BaseModel):
    case_type: str
    case_id: str
    channel: str = "WHATSAPP"
    language: str = "English"
    tone: str = "professional"
    payment_link: Optional[str] = None


class PaymentLinkPayload(BaseModel):
    transaction_id: Optional[str] = None
    checkout_id: Optional[str] = None
    amount: float = Field(gt=0)


class MandateRetryPayload(BaseModel):
    force_demo_outcome: Optional[str] = None


FAILURE_PROBABILITIES = {key: value["probability"] for key, value in FAILURE_STRATEGIES.items()}


def sanitize_for_json(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, dict):
        return {str(k): sanitize_for_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize_for_json(v) for v in value]
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat(sep=" ")
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if hasattr(value, "item"):
        try:
            return sanitize_for_json(value.item())
        except Exception:
            pass
    if pd.isna(value):
        return None
    return value


def record_audit(
    entity_type: str,
    entity_id: str,
    action: str,
    decision: str,
    *,
    strategy: str = "",
    reason: str = "",
    compliance: str = "",
    outcome: str = "",
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    event = {
        "timestamp": datetime.now(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "entity_type": str(entity_type).upper(),
        "entity_id": str(entity_id),
        "action": action,
        "decision": decision,
        "strategy": strategy,
        "reason": reason,
        "compliance_result": compliance,
        "outcome": outcome,
        "metadata": metadata or {},
    }
    audit_log.append(sanitize_for_json(event))
    return event


def events_for(entity_type: str, entity_id: str) -> List[Dict[str, Any]]:
    return [e for e in audit_log if e["entity_type"] == entity_type.upper() and e["entity_id"] == str(entity_id)]


def row_for(dataframe: pd.DataFrame, id_column: str, value: str) -> pd.Series:
    matches = dataframe[dataframe[id_column].astype(str) == str(value)]
    if matches.empty:
        raise HTTPException(status_code=404, detail=f"{id_column.replace('_', ' ').title()} not found")
    return matches.iloc[0]


def payment_priority(amount: float, failure_reason: str, max_amount: float) -> Dict[str, Any]:
    probability = float(FAILURE_PROBABILITIES.get(failure_reason, 30))
    value_score = round((float(amount) / max_amount) * 100, 1) if max_amount > 0 else 0.0
    value_score = max(0.0, min(100.0, value_score))
    score = round(probability * 0.50 + value_score * 0.50, 1)
    priority = "HIGH" if score >= 92 else "MEDIUM" if score >= 75 else "LOW"
    return {
        "recovery_probability": probability,
        "value_score": value_score,
        "priority_score": score,
        "recovery_priority": priority,
        "estimated_recoverable_revenue": round(float(amount) * probability / 100),
    }


def checkout_priority(row: pd.Series, max_amount: float) -> Dict[str, Any]:
    amount = float(row.get("cart_amount", 0))
    stage = str(row.get("abandonment_stage", "Cart"))
    minutes = int(float(row.get("minutes_since_abandonment", 0) or 0))
    orders = int(float(row.get("previous_successful_orders", 0) or 0))
    abandonments = int(float(row.get("previous_abandonments", 0) or 0))
    probability = 50
    probability += {"Payment": 20, "Payment Authentication": 25, "Address": 10}.get(stage, 0)
    probability += 15 if minutes <= 60 else 10 if minutes <= 1440 else 5 if minutes <= 4320 else 0
    probability += min(orders * 2, 10)
    probability -= min(abandonments * 4, 20)
    probability = max(10, min(95, probability))
    value_score = round(amount / max_amount * 100, 1) if max_amount > 0 else 0
    score = round(probability * 0.60 + value_score * 0.40, 1)
    priority = "HIGH" if score >= 85 else "MEDIUM" if score >= 65 else "LOW"
    return {
        "recovery_probability": probability,
        "value_score": value_score,
        "priority_score": score,
        "recovery_priority": priority,
        "estimated_recoverable_revenue": round(amount * probability / 100),
    }


def checkout_strategy(row: pd.Series, priority: str) -> Dict[str, Any]:
    consent = safe_upper(row.get("recovery_consent"), "NO")
    stage = str(row.get("abandonment_stage", "Cart"))
    if consent != "YES":
        return {
            "strategy_name": "Respect Consent",
            "diagnosis": "Customer recovery consent is unavailable.",
            "recommended_action": "Do not send automated recovery outreach.",
            "max_attempts": 0,
            "max_messages": 0,
            "reason": "Consent gate blocked automated outreach.",
            "steps": ["Detect abandonment", "Check consent", "Stop outreach"],
        }
    if stage in {"Payment", "Payment Authentication"}:
        return {
            "strategy_name": "High Intent Checkout Recovery" if priority == "HIGH" else "Payment Reminder Recovery",
            "diagnosis": "Customer reached a high-intent checkout stage but did not complete payment.",
            "recommended_action": "Send a personalized payment reminder and one bounded follow-up.",
            "max_attempts": 2,
            "max_messages": 2,
            "reason": "Late-stage abandonment indicates strong purchase intent.",
            "steps": ["Generate recovery message", "Check compliance", "Send reminder", "Evaluate outcome", "Stop or escalate"],
        }
    return {
        "strategy_name": "Gentle Checkout Recovery",
        "diagnosis": "Customer abandoned before the final payment stage.",
        "recommended_action": "Send one gentle personalized reminder.",
        "max_attempts": 1,
        "max_messages": 1,
        "reason": "Earlier-stage abandonment warrants lower-frequency outreach.",
        "steps": ["Generate gentle reminder", "Check compliance", "Send once", "Stop if not recovered"],
    }


def invoice_reasoning(row: Dict[str, Any]) -> Dict[str, Any]:
    days = int(float(row.get("days_overdue", 0) or 0))
    consent = safe_upper(row.get("consent"), "NO")
    dnd = safe_upper(row.get("dnd"), "NO")
    promise_status = safe_upper(row.get("promise_status"), "NONE")
    promise_date = row.get("promise_to_pay_date")
    if promise_date is not None and str(promise_date).lower() in {"nan", "nat", "none"}:
        promise_date = None
    if promise_status == "ACTIVE" and promise_date:
        try:
            if datetime.strptime(str(promise_date), "%Y-%m-%d").date() < datetime.now(LOCAL_TZ).date() and safe_upper(row.get("payment_status"), "UNPAID") != "PAID":
                promise_status = "BROKEN"
        except ValueError:
            pass
    if consent != "YES":
        return {"diagnosis": "Customer communication consent is not granted.", "recommended_action": "Do not initiate automated outreach.", "reason": "Consent restriction.", "next_step": "Await updated consent or inbound communication.", "max_attempts": 0, "escalate_manual": False, "promise_status": promise_status}
    if dnd == "YES":
        return {"diagnosis": "Customer is on DND.", "recommended_action": "Halt automated communications.", "reason": "DND restriction.", "next_step": "Route through relationship manager.", "max_attempts": 0, "escalate_manual": False, "promise_status": promise_status}
    if promise_status == "BROKEN":
        return {"diagnosis": f"Promise-to-pay date {promise_date} passed without settlement.", "recommended_action": "Escalate to finance collections review.", "reason": "Broken payment commitment increases collection risk.", "next_step": "Review account and agree a revised settlement plan.", "max_attempts": 1, "escalate_manual": True, "promise_status": promise_status}
    if promise_status == "ACTIVE":
        return {"diagnosis": f"Active promise-to-pay is registered for {promise_date}.", "recommended_action": "Pause automated escalation until the commitment date.", "reason": "Customer has committed to a payment date.", "next_step": "Monitor settlement on the commitment date.", "max_attempts": 1, "escalate_manual": False, "promise_status": promise_status}
    if days <= 0:
        return {"diagnosis": "Invoice is current and within payment terms.", "recommended_action": "Send standard invoice communication only.", "reason": "Invoice is not overdue.", "next_step": "Use a routine due-date reminder.", "max_attempts": 1, "escalate_manual": False, "promise_status": promise_status}
    if days <= 30:
        return {"diagnosis": f"Invoice is {days} days overdue (1–30 day bucket).", "recommended_action": "Send a friendly payment reminder and account statement.", "reason": "Early-stage delinquency is suitable for automated follow-up.", "next_step": "Allow the customer's AP cycle to respond.", "max_attempts": 3, "escalate_manual": False, "promise_status": promise_status}
    if days <= 60:
        return {"diagnosis": f"Invoice is {days} days overdue (31–60 day bucket).", "recommended_action": "Send a firm overdue notice and request a settlement date.", "reason": "Extended delay requires stronger follow-up.", "next_step": "Escalate if the account remains unresponsive.", "max_attempts": 2, "escalate_manual": False, "promise_status": promise_status}
    if days <= 90:
        return {"diagnosis": f"Invoice is {days} days overdue (61–90 day bucket).", "recommended_action": "Issue a formal overdue notice and review settlement options.", "reason": "Severe aging threshold reached.", "next_step": "Initiate structured settlement or escalation.", "max_attempts": 2, "escalate_manual": False, "promise_status": promise_status}
    return {"diagnosis": f"Invoice is {days} days overdue (90+ day bucket).", "recommended_action": "Escalate to finance/legal manual review.", "reason": "Critically aged receivable is beyond the normal automated threshold.", "next_step": "Transfer to executive collections review.", "max_attempts": 1, "escalate_manual": True, "promise_status": promise_status}


def escalate(case_type: str, case_id: str, amount: float, score: float, priority: str, attempts: int, max_attempts: int, reason: str, recommended_action: str = "Manual review required") -> bool:
    key = f"{case_type}:{case_id}"
    if key in manual_review_cases and manual_review_cases[key].get("status") == "MANUAL_REVIEW":
        return False
    manual_review_cases[key] = {
        "case_key": key,
        "case_type": case_type,
        "case_id": str(case_id),
        "amount": int(amount),
        "priority_score": float(score),
        "recovery_priority": priority,
        "attempts_used": int(attempts),
        "max_attempts": int(max_attempts),
        "reason_for_escalation": reason,
        "recommended_next_action": recommended_action,
        "status": "MANUAL_REVIEW",
        "escalated_at": datetime.now(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "recovery_history": [],
    }
    record_audit(case_type, case_id, "Manual Review Escalation", "ESCALATED", reason=reason, outcome="MANUAL_REVIEW")
    return True


def payment_decision(transaction_id: str) -> Dict[str, Any]:
    row = row_for(df, "transaction_id", transaction_id)
    amount = float(row["amount"])
    failure_reason = str(row.get("failure_reason", ""))
    failed = df[df["status"] == "FAILED"]
    max_amount = float(failed["amount"].max()) if not failed.empty else 1
    priority = payment_priority(amount, failure_reason, max_amount)
    strategy = FAILURE_STRATEGIES.get(failure_reason, {"diagnosis": "Payment failure detected.", "action": "Manual review recommended.", "strategy": "Manual Review", "probability": 30, "max_attempts": 1, "reason": "Unknown failure reason."})
    attempts = recovery_attempts.get(str(transaction_id), 0)
    compliance = compliance_check(attempts_used=attempts, max_attempts=int(strategy["max_attempts"]), channel="PAYMENT")
    decision = build_agent_decision(
        case_type="PAYMENT", case_id=transaction_id, amount=amount,
        priority_score=priority["priority_score"], priority=priority["recovery_priority"],
        diagnosis=strategy["diagnosis"], strategy=strategy["strategy"], strategy_reason=strategy["reason"],
        next_action=strategy["action"], compliance=compliance, attempts_used=attempts, max_attempts=int(strategy["max_attempts"]), failure_reason=failure_reason,
    )
    return {**row.to_dict(), **priority, **decision, "recommended_action": strategy["action"], "recovery_attempts_used": attempts, "max_recovery_attempts": strategy["max_attempts"], "attempts_remaining": max(0, strategy["max_attempts"] - attempts)}


@app.get("/")
def home() -> Dict[str, Any]:
    return {"message": "RevenueRescue AI Backend is Running", "version": app.version, "ai_mode": ai_service.mode}


@app.get("/transactions")
def get_transactions() -> Any:
    return sanitize_for_json(df.to_dict(orient="records"))


@app.get("/transactions/failed")
def get_failed_transactions() -> Any:
    return sanitize_for_json(df[df["status"] == "FAILED"].to_dict(orient="records"))


@app.get("/dashboard")
def get_dashboard() -> Dict[str, Any]:
    failed = df[df["status"] == "FAILED"]
    return sanitize_for_json({"total_transactions": len(df), "failed_transactions": len(failed), "revenue_at_risk": int(failed["amount"].sum()) if not failed.empty else 0, "ai_mode": ai_service.mode})


@app.get("/analyze/{transaction_id}")
def analyze_transaction(transaction_id: str) -> Any:
    row = row_for(df, "transaction_id", transaction_id)
    if safe_upper(row.get("status"), "FAILED") == "SUCCESS":
        result = payment_decision(transaction_id)
        result["status"] = "SUCCESS"
        result["compliance"] = compliance_check(attempts_used=result["recovery_attempts_used"], max_attempts=result["max_recovery_attempts"], channel="PAYMENT")
        return sanitize_for_json(result)
    return sanitize_for_json(payment_decision(transaction_id))


@app.post("/retry/{transaction_id}")
def retry_payment(transaction_id: str) -> Any:
    row = row_for(df, "transaction_id", transaction_id)
    transaction_id = str(transaction_id)
    if safe_upper(row.get("status"), "FAILED") == "SUCCESS":
        record_audit("PAYMENT", transaction_id, "Retry Request", "STOPPED", reason="Payment was already recovered.", outcome="ALREADY_RECOVERED")
        return {"success": True, "stopped": True, "message": "Payment was already recovered.", "attempts_used": recovery_attempts.get(transaction_id, 0), "max_attempts": 0, "attempts_remaining": 0}

    decision = payment_decision(transaction_id)
    strategy = FAILURE_STRATEGIES.get(str(row.get("failure_reason", "")), {"probability": 30, "max_attempts": 1, "strategy": "Manual Review"})
    max_attempts = int(strategy["max_attempts"])
    attempts = recovery_attempts.get(transaction_id, 0)
    compliance = compliance_check(attempts_used=attempts, max_attempts=max_attempts, channel="PAYMENT")
    record_audit("PAYMENT", transaction_id, "Compliance Check", compliance["decision"], strategy=strategy["strategy"], reason=" ".join(compliance["reasons"]), compliance=compliance["decision"])
    if f"PAYMENT:{transaction_id}" in opted_out_entities:
        compliance = {**compliance, "allowed": False, "decision": "BLOCKED", "reasons": ["Customer has opted out of automated recovery outreach."]}
    if not compliance["allowed"] and attempts >= max_attempts:
        escalated = escalate("PAYMENT", transaction_id, row["amount"], decision["priority_score"], decision["recovery_priority"], attempts, max_attempts, "Maximum retry attempts reached without recovery.", decision["recommended_action"])
        return {"success": False, "stopped": True, "status": "MANUAL_REVIEW" if escalated else "STOPPED", "message": "Recovery stopped and escalated after the maximum attempt limit.", "attempts_used": attempts, "max_attempts": max_attempts, "attempts_remaining": 0, "escalated": escalated}
    if not compliance["allowed"]:
        return {"success": False, "stopped": True, "status": "COMPLIANCE_BLOCKED", "message": "Recovery action was blocked by compliance policy.", "compliance": compliance, "attempts_used": attempts, "max_attempts": max_attempts, "attempts_remaining": max(0, max_attempts - attempts)}

    attempts += 1
    recovery_attempts[transaction_id] = attempts
    record_audit("PAYMENT", transaction_id, "Payment Retry", "STARTED", strategy=strategy["strategy"], reason=decision["diagnosis"], compliance="ALLOWED", metadata={"attempt": attempts, "max_attempts": max_attempts})

    # Deterministic demo outcome: high-probability technical failures recover immediately;
    # medium-probability declines recover only on their final allowed attempt.
    probability = int(strategy["probability"])
    success = probability >= 80 or (probability >= 60 and attempts >= max_attempts)
    if success:
        df.loc[df["transaction_id"].astype(str) == transaction_id, "status"] = "SUCCESS"
        amount = int(row["amount"])
        recovered_transactions[transaction_id] = amount
        record_audit("PAYMENT", transaction_id, "Payment Recovered", "SUCCESS", strategy=strategy["strategy"], reason="Recovery attempt succeeded.", compliance="ALLOWED", outcome="RECOVERED", metadata={"attempt": attempts, "amount_recovered": amount})
        return {"success": True, "stopped": True, "status": "SUCCESS", "message": "Payment successfully recovered.", "attempts_used": attempts, "max_attempts": max_attempts, "attempts_remaining": max(0, max_attempts - attempts), "amount_recovered": amount}

    if attempts >= max_attempts:
        record_audit("PAYMENT", transaction_id, "Recovery Stopped", "STOPPED", strategy=strategy["strategy"], reason="Maximum retry attempts reached without a successful recovery.", compliance="ALLOWED", outcome="STOPPED")
        escalated = escalate("PAYMENT", transaction_id, row["amount"], decision["priority_score"], decision["recovery_priority"], attempts, max_attempts, "Maximum retry attempts reached without a successful recovery.", decision["recommended_action"])
        return {"success": False, "stopped": True, "status": "MANUAL_REVIEW" if escalated else "STOPPED", "message": "Recovery stopped after the maximum allowed attempts.", "attempts_used": attempts, "max_attempts": max_attempts, "attempts_remaining": 0, "escalated": escalated}

    record_audit("PAYMENT", transaction_id, "Payment Retry", "FAILED", strategy=strategy["strategy"], reason="Attempt did not recover the payment; another bounded attempt remains.", compliance="ALLOWED", outcome="RETRY_AVAILABLE", metadata={"attempt": attempts})
    return {"success": False, "stopped": False, "status": "RETRY_AVAILABLE", "message": "Recovery attempt was unsuccessful. Another bounded attempt remains.", "attempts_used": attempts, "max_attempts": max_attempts, "attempts_remaining": max_attempts - attempts}


@app.get("/audit/{transaction_id}")
def get_audit_trail(transaction_id: str) -> Dict[str, Any]:
    events = events_for("PAYMENT", transaction_id) + events_for("COMPLIANCE", transaction_id)
    events.sort(key=lambda e: e.get("timestamp", ""))
    return {"transaction_id": transaction_id, "events": events}


@app.get("/transactions/prioritized")
def get_prioritized_transactions() -> Any:
    failed = df[df["status"] == "FAILED"].copy()
    if failed.empty:
        return []
    max_amount = float(failed["amount"].max()) if not failed.empty else 1
    output = []
    for _, row in failed.iterrows():
        tid = str(row["transaction_id"])
        priority = payment_priority(float(row["amount"]), str(row["failure_reason"]), max_amount)
        strategy = FAILURE_STRATEGIES.get(str(row["failure_reason"]), {"max_attempts": 1, "strategy": "Manual Review"})
        attempts = recovery_attempts.get(tid, 0)
        item = {**row.to_dict(), **priority, "strategy_name": strategy["strategy"], "recovery_attempts_used": attempts, "max_recovery_attempts": int(strategy["max_attempts"]), "attempts_remaining": max(0, int(strategy["max_attempts"]) - attempts), "manual_review": f"PAYMENT:{tid}" in manual_review_cases}
        output.append(item)
    output.sort(key=lambda x: x["priority_score"], reverse=True)
    return sanitize_for_json(output)


@app.get("/recovery-summary")
def get_recovery_summary() -> Dict[str, Any]:
    prioritized = get_prioritized_transactions()
    return sanitize_for_json({
        "high_priority": sum(1 for x in prioritized if x["recovery_priority"] == "HIGH"),
        "medium_priority": sum(1 for x in prioritized if x["recovery_priority"] == "MEDIUM"),
        "low_priority": sum(1 for x in prioritized if x["recovery_priority"] == "LOW"),
        "estimated_recoverable_revenue": sum(int(x["estimated_recoverable_revenue"]) for x in prioritized),
        "actual_recovered_revenue": sum(recovered_transactions.values()),
        "recovered_transactions": len(recovered_transactions),
    })


@app.get("/checkout-abandonments")
def get_checkout_abandonments() -> Any:
    return sanitize_for_json(checkout_df[checkout_df["status"] == "ABANDONED"].to_dict(orient="records"))


@app.get("/checkout-abandonments/prioritized")
def get_prioritized_checkouts() -> Any:
    abandoned = checkout_df[checkout_df["status"] == "ABANDONED"].copy()
    if abandoned.empty:
        return []
    max_amount = float(abandoned["cart_amount"].max()) if not abandoned.empty else 1
    output = []
    for _, row in abandoned.iterrows():
        cid = str(row["checkout_id"])
        priority = checkout_priority(row, max_amount)
        strategy = checkout_strategy(row, priority["recovery_priority"])
        attempts = checkout_recovery_attempts.get(cid, 0)
        output.append({**row.to_dict(), **priority, "strategy_name": strategy["strategy_name"], "diagnosis": strategy["diagnosis"], "recommended_action": strategy["recommended_action"], "strategy_reason": strategy["reason"], "strategy_steps": strategy["steps"], "max_recovery_attempts": strategy["max_attempts"], "max_messages": strategy["max_messages"], "recovery_attempts_used": attempts, "attempts_remaining": max(0, strategy["max_attempts"] - attempts)})
    output.sort(key=lambda x: x["priority_score"], reverse=True)
    return sanitize_for_json(output)


@app.get("/analyze-checkout/{checkout_id}")
def analyze_checkout(checkout_id: str) -> Any:
    row = row_for(checkout_df, "checkout_id", checkout_id)
    abandoned = checkout_df[checkout_df["status"] == "ABANDONED"]
    max_amount = float(abandoned["cart_amount"].max()) if not abandoned.empty else 1
    priority = checkout_priority(row, max_amount)
    strategy = checkout_strategy(row, priority["recovery_priority"])
    attempts = checkout_recovery_attempts.get(str(checkout_id), 0)
    compliance = compliance_check(consent=row.get("recovery_consent"), attempts_used=attempts, max_attempts=int(strategy["max_attempts"]), channel="WHATSAPP")
    decision = build_agent_decision(case_type="CHECKOUT", case_id=checkout_id, amount=float(row["cart_amount"]), priority_score=priority["priority_score"], priority=priority["recovery_priority"], diagnosis=strategy["diagnosis"], strategy=strategy["strategy_name"], strategy_reason=strategy["reason"], next_action=strategy["recommended_action"], compliance=compliance, attempts_used=attempts, max_attempts=int(strategy["max_attempts"]))
    return sanitize_for_json({**row.to_dict(), **priority, **strategy, **decision, "recovery_attempts_used": attempts, "max_recovery_attempts": strategy["max_attempts"], "attempts_remaining": max(0, strategy["max_attempts"] - attempts), "status": row.get("status", "ABANDONED")})


@app.post("/recover-checkout/{checkout_id}")
def recover_checkout(checkout_id: str) -> Any:
    row = row_for(checkout_df, "checkout_id", checkout_id)
    checkout_id = str(checkout_id)
    if safe_upper(row.get("status"), "ABANDONED") == "RECOVERED":
        return {"success": True, "stopped": True, "message": "Checkout was already recovered."}
    abandoned = checkout_df[checkout_df["status"] == "ABANDONED"]
    max_amount = float(abandoned["cart_amount"].max()) if not abandoned.empty else 1
    priority = checkout_priority(row, max_amount)
    strategy = checkout_strategy(row, priority["recovery_priority"])
    attempts = checkout_recovery_attempts.get(checkout_id, 0)
    compliance = compliance_check(consent=row.get("recovery_consent"), attempts_used=attempts, max_attempts=int(strategy["max_attempts"]), channel="WHATSAPP")
    record_audit("CHECKOUT", checkout_id, "Compliance Check", compliance["decision"], strategy=strategy["strategy_name"], reason=" ".join(compliance["reasons"]), compliance=compliance["decision"])
    if not compliance["allowed"]:
        if attempts >= int(strategy["max_attempts"]) and strategy["max_attempts"] > 0:
            escalated = escalate("CHECKOUT", checkout_id, row["cart_amount"], priority["priority_score"], priority["recovery_priority"], attempts, strategy["max_attempts"], "Maximum checkout outreach attempts reached.", strategy["recommended_action"])
            return {"success": False, "stopped": True, "status": "MANUAL_REVIEW" if escalated else "STOPPED", "message": "Checkout recovery stopped after the outreach limit.", "escalated": escalated, "attempts_used": attempts, "max_attempts": strategy["max_attempts"], "attempts_remaining": 0}
        return {"success": False, "stopped": True, "status": "COMPLIANCE_BLOCKED", "message": "Checkout recovery was blocked by consent, DND, quiet-hours, or attempt policy.", "compliance": compliance, "attempts_used": attempts, "max_attempts": strategy["max_attempts"], "attempts_remaining": max(0, strategy["max_attempts"] - attempts)}

    attempts += 1
    checkout_recovery_attempts[checkout_id] = attempts
    action = "Personalized Checkout Reminder" if attempts == 1 else "Checkout Follow-up Reminder"
    record_audit("CHECKOUT", checkout_id, action, "EXECUTED", strategy=strategy["strategy_name"], reason=strategy["diagnosis"], compliance="ALLOWED", outcome="PENDING", metadata={"attempt": attempts})
    probability = int(priority["recovery_probability"])
    success = probability >= 80 or (probability >= 65 and attempts >= int(strategy["max_attempts"]))
    if success:
        checkout_df.loc[checkout_df["checkout_id"].astype(str) == checkout_id, "status"] = "RECOVERED"
        amount = int(row["cart_amount"])
        recovered_checkouts[checkout_id] = amount
        record_audit("CHECKOUT", checkout_id, "Checkout Recovered", "SUCCESS", strategy=strategy["strategy_name"], reason="Recovery action resulted in simulated payment completion.", compliance="ALLOWED", outcome="RECOVERED", metadata={"attempt": attempts, "amount_recovered": amount})
        return {"success": True, "stopped": True, "status": "RECOVERED", "message": "Checkout successfully recovered.", "action": action, "attempts_used": attempts, "max_attempts": strategy["max_attempts"], "attempts_remaining": max(0, strategy["max_attempts"] - attempts), "amount_recovered": amount}
    if attempts >= int(strategy["max_attempts"]):
        escalated = escalate("CHECKOUT", checkout_id, row["cart_amount"], priority["priority_score"], priority["recovery_priority"], attempts, strategy["max_attempts"], "Maximum outreach attempts reached without recovery.", strategy["recommended_action"])
        return {"success": False, "stopped": True, "status": "MANUAL_REVIEW" if escalated else "STOPPED", "message": "Checkout recovery stopped after the maximum allowed outreach attempts.", "action": action, "attempts_used": attempts, "max_attempts": strategy["max_attempts"], "attempts_remaining": 0, "escalated": escalated}
    return {"success": False, "stopped": False, "status": "RETRY_AVAILABLE", "message": "Recovery outreach executed. One bounded follow-up remains.", "action": action, "attempts_used": attempts, "max_attempts": strategy["max_attempts"], "attempts_remaining": strategy["max_attempts"] - attempts}


@app.get("/checkout-audit/{checkout_id}")
def get_checkout_audit(checkout_id: str) -> Dict[str, Any]:
    return {"checkout_id": checkout_id, "events": events_for("CHECKOUT", checkout_id)}


@app.get("/checkout-recovery-summary")
def get_checkout_recovery_summary() -> Dict[str, Any]:
    abandoned = checkout_df[checkout_df["status"] == "ABANDONED"]
    recovered_count = len(recovered_checkouts)
    denominator = recovered_count + len(abandoned)
    return sanitize_for_json({"abandoned_checkouts": len(abandoned), "checkout_revenue_at_risk": int(abandoned["cart_amount"].sum()) if not abandoned.empty else 0, "recovered_checkouts": recovered_count, "actual_recovered_revenue": sum(recovered_checkouts.values()), "recovery_rate": round(recovered_count / denominator * 100, 1) if denominator else 0})


@app.get("/receivables")
def get_receivables() -> Any:
    records = []
    for _, raw in invoices_df.iterrows():
        row = raw.to_dict()
        invoice_id = str(row["invoice_id"])
        reasoning = invoice_reasoning(row)
        row["promise_status"] = reasoning.get("promise_status", safe_upper(row.get("promise_status"), "NONE"))
        if row["promise_status"] == "BROKEN" and safe_upper(row.get("payment_status"), "UNPAID") != "PAID":
            invoices_df.loc[invoices_df["invoice_id"].astype(str) == invoice_id, ["promise_status", "recovery_stage"]] = ["BROKEN", "BROKEN_PROMISE_ESCALATION"]
        promise_date = row.get("promise_to_pay_date")
        row["promise_to_pay_date"] = None if promise_date is None or str(promise_date).lower() in {"nan", "nat", "none", ""} else str(promise_date)
        row["reasoning"] = reasoning
        row["attempts_used"] = receivables_recovery_attempts.get(invoice_id, 0)
        row["max_attempts"] = reasoning["max_attempts"]
        row["audit_trail"] = events_for("INVOICE", invoice_id)
        records.append(row)
    return sanitize_for_json(records)


@app.get("/receivables-summary")
def get_receivables_summary() -> Dict[str, Any]:
    unpaid = invoices_df[invoices_df["payment_status"].isin(["UNPAID", "PARTIALLY_PAID"])]
    overdue = unpaid[unpaid["days_overdue"] > 0]
    broken = sum(1 for _, row in invoices_df.iterrows() if invoice_reasoning(row.to_dict()).get("promise_status") == "BROKEN")
    manual = sum(1 for _, row in unpaid.iterrows() if invoice_reasoning(row.to_dict()).get("escalate_manual"))
    return sanitize_for_json({"total_outstanding_receivables": int(unpaid["invoice_amount"].sum()) if not unpaid.empty else 0, "total_overdue_amount": int(overdue["invoice_amount"].sum()) if not overdue.empty else 0, "overdue_invoices_count": len(overdue), "broken_promises_count": broken, "manual_review_needed_count": manual, "recovered_invoices_count": len(recovered_invoices), "actual_recovered_revenue": sum(recovered_invoices.values())})


@app.get("/receivables/{invoice_id}")
def get_invoice_detail(invoice_id: str) -> Any:
    row = row_for(invoices_df, "invoice_id", invoice_id).to_dict()
    reasoning = invoice_reasoning(row)
    row["promise_status"] = reasoning.get("promise_status", row.get("promise_status"))
    row["promise_to_pay_date"] = None if row.get("promise_to_pay_date") is None or str(row.get("promise_to_pay_date")).lower() in {"nan", "nat", "none", ""} else str(row.get("promise_to_pay_date"))
    row["reasoning"] = reasoning
    row["attempts_used"] = receivables_recovery_attempts.get(str(invoice_id), 0)
    row["max_attempts"] = reasoning["max_attempts"]
    row["audit_trail"] = events_for("INVOICE", invoice_id)
    return sanitize_for_json(row)


@app.post("/receivables/{invoice_id}/promise-to-pay")
def set_promise_to_pay(invoice_id: str, payload: PromiseToPayPayload) -> Dict[str, Any]:
    row_for(invoices_df, "invoice_id", invoice_id)
    try:
        promised = datetime.strptime(payload.promise_date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="promise_date must use YYYY-MM-DD format")
    if promised < datetime.now(LOCAL_TZ).date():
        raise HTTPException(status_code=400, detail="Promise date must be today or in the future")
    mask = invoices_df["invoice_id"].astype(str) == str(invoice_id)
    invoices_df.loc[mask, ["promise_to_pay_date", "promise_to_pay_amount", "promise_status"]] = [payload.promise_date, payload.promise_amount, "ACTIVE"]
    record_audit("INVOICE", invoice_id, "Promise-to-Pay Registered", "ACTIVE", reason="Customer payment commitment recorded.", outcome="PROMISE_ACTIVE", metadata={"promise_date": payload.promise_date, "promise_amount": payload.promise_amount})
    return {"success": True, "message": f"Promise to pay recorded for {payload.promise_date}.", "promise_status": "ACTIVE"}


@app.post("/receivables/{invoice_id}/run-recovery")
def run_receivables_recovery(invoice_id: str) -> Any:
    row = row_for(invoices_df, "invoice_id", invoice_id).to_dict()
    if safe_upper(row.get("payment_status"), "UNPAID") == "PAID":
        return {"success": True, "stopped": True, "message": "Invoice is already paid in full.", "status": "PAID"}
    reasoning = invoice_reasoning(row)

    # An active promise-to-pay takes precedence over automated escalation.
    # The agent must pause recovery until the customer's committed date.
    if reasoning.get("promise_status") == "ACTIVE":
        promise_date = row.get("promise_to_pay_date")
        record_audit(
            "INVOICE",
            invoice_id,
            "Promise-to-Pay Hold",
            "PAUSED",
            reason=f"Active customer commitment exists for {promise_date}.",
            outcome="WAITING_FOR_COMMITMENT_DATE",
            metadata={"promise_date": promise_date},
        )
        return {
            "success": False,
            "stopped": True,
            "status": "PAUSED",
            "message": f"Automated recovery paused until the commitment date ({promise_date}).",
            "reasoning": reasoning,
            "promise_date": promise_date,
            "attempts_used": receivables_recovery_attempts.get(str(invoice_id), 0),
            "max_attempts": int(reasoning["max_attempts"]),
        }

    attempts = receivables_recovery_attempts.get(str(invoice_id), 0)
    max_attempts = int(reasoning["max_attempts"])
    compliance = compliance_check(consent=row.get("consent"), dnd=row.get("dnd"), attempts_used=attempts, max_attempts=max_attempts, channel="EMAIL")
    record_audit("INVOICE", invoice_id, "Compliance Check", compliance["decision"], strategy=reasoning["recommended_action"], reason=" ".join(compliance["reasons"]), compliance=compliance["decision"])
    if reasoning["escalate_manual"] or attempts >= max_attempts:
        escalated = escalate("INVOICE", invoice_id, row["invoice_amount"], 95.0, "HIGH", attempts, max_attempts, reasoning["reason"], reasoning["recommended_action"])
        return {"success": False, "stopped": True, "status": "MANUAL_REVIEW", "message": "Invoice escalated to finance/legal manual review.", "escalated": escalated, "reasoning": reasoning}
    if not compliance["allowed"]:
        return {"success": False, "stopped": True, "status": "COMPLIANCE_BLOCKED", "message": "Receivables outreach was blocked by compliance policy.", "compliance": compliance, "reasoning": reasoning}

    attempts += 1
    receivables_recovery_attempts[str(invoice_id)] = attempts
    record_audit("INVOICE", invoice_id, "Receivables Outreach", "EXECUTED", strategy=reasoning["recommended_action"], reason=reasoning["next_step"], compliance="ALLOWED", outcome="PENDING", metadata={"attempt": attempts})
    days = int(row.get("days_overdue", 0) or 0)
    resolved = (days <= 30 and attempts == 1) or (31 <= days <= 60 and attempts >= 2)
    if resolved:
        mask = invoices_df["invoice_id"].astype(str) == str(invoice_id)
        invoices_df.loc[mask, "payment_status"] = "PAID"
        amount = int(row["invoice_amount"])
        recovered_invoices[str(invoice_id)] = amount
        record_audit("INVOICE", invoice_id, "Invoice Settlement Recovered", "SUCCESS", strategy=reasoning["recommended_action"], reason="Settlement confirmed in demo mode.", compliance="ALLOWED", outcome="RECOVERED", metadata={"attempt": attempts, "amount_recovered": amount})
        return {"success": True, "stopped": True, "status": "PAID", "message": f"Invoice settlement of ₹{amount:,} successfully received.", "amount_recovered": amount, "attempts_used": attempts, "max_attempts": max_attempts, "reasoning": reasoning}
    if attempts >= max_attempts:
        escalated = escalate("INVOICE", invoice_id, row["invoice_amount"], 95.0, "HIGH", attempts, max_attempts, "Maximum receivables outreach attempts reached without settlement.", reasoning["recommended_action"])
        return {"success": False, "stopped": True, "status": "MANUAL_REVIEW", "message": "Maximum outreach reached; case escalated to manual review.", "escalated": escalated, "attempts_used": attempts, "max_attempts": max_attempts, "reasoning": reasoning}
    return {"success": False, "stopped": False, "status": "IN_PROGRESS", "message": f"{reasoning['recommended_action']} dispatched. Awaiting client response.", "attempts_used": attempts, "max_attempts": max_attempts, "reasoning": reasoning}


@app.get("/manual-review-cases")
def get_manual_review_cases() -> Any:
    cases = []
    for case in manual_review_cases.values():
        item = dict(case)
        item["recovery_history"] = events_for(case["case_type"], case["case_id"])
        cases.append(item)
    return sanitize_for_json(cases)


@app.post("/manual-review-cases/{case_key:path}/action")
def manual_review_action(case_key: str, payload: ManualReviewActionPayload) -> Any:
    if case_key not in manual_review_cases:
        raise HTTPException(status_code=404, detail="Manual review case not found")
    action = payload.action.strip().upper()
    if action not in {"APPROVE", "REJECT", "CLOSE"}:
        raise HTTPException(status_code=400, detail="action must be APPROVE, REJECT, or CLOSE")
    case = manual_review_cases[case_key]
    status = {"APPROVE": "APPROVED", "REJECT": "REJECTED", "CLOSE": "CLOSED"}[action]
    case["status"] = status
    case["reviewer_note"] = payload.note.strip()
    case["resolved_at"] = datetime.now(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")
    record_audit(case["case_type"], case["case_id"], f"Manual Review {action}", status, reason=payload.note.strip() or f"Manual reviewer selected {action}.", outcome=status)
    return {"success": True, "message": f"Manual review case {action.lower()}ed.", "case": case}


@app.post("/payment-links/generate")
def generate_payment_link(payload: PaymentLinkPayload) -> Dict[str, Any]:
    if not payload.transaction_id and not payload.checkout_id:
        raise HTTPException(status_code=400, detail="transaction_id or checkout_id is required")
    link_id = uuid.uuid4().hex[:10]
    link = f"https://rzp.io/i/{link_id}"
    item = {"link_id": link_id, "url": link, "amount": int(payload.amount), "mode": "TEST_MODE_SIMULATION", "created_at": datetime.now(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S"), "transaction_id": payload.transaction_id, "checkout_id": payload.checkout_id}
    payment_links[link_id] = item
    entity_type = "PAYMENT" if payload.transaction_id else "CHECKOUT"
    entity_id = payload.transaction_id or payload.checkout_id
    record_audit(entity_type, entity_id, "Payment Link Generated", "EXECUTED", reason="Sandbox/demo payment link generated; no live Razorpay API call was made.", outcome="LINK_READY", metadata={"link_id": link_id, "mode": "TEST_MODE_SIMULATION"})
    return item


@app.post("/ai/generate-message")
def generate_message(payload: MessagePayload) -> Dict[str, Any]:
    case_type = payload.case_type.upper()
    if case_type == "PAYMENT":
        row = row_for(df, "transaction_id", payload.case_id)
        if f"PAYMENT:{payload.case_id}" in opted_out_entities:
            raise HTTPException(status_code=403, detail="Message generation blocked: customer opted out of automated outreach.")
        compliance = compliance_check(attempts_used=recovery_attempts.get(payload.case_id, 0), max_attempts=int(FAILURE_STRATEGIES.get(str(row.get("failure_reason", "")), {"max_attempts": 1})["max_attempts"]), channel=payload.channel)
        if not compliance["allowed"]:
            raise HTTPException(status_code=403, detail={"message": "Message generation blocked by compliance policy.", "compliance": compliance})
        result = ai_service.generate_recovery_message(customer_name="Customer", amount=float(row["amount"]), failure_reason=str(row.get("failure_reason", "payment issue")), channel=payload.channel, language=payload.language, tone=payload.tone, payment_link=payload.payment_link)
        record_audit("PAYMENT", payload.case_id, "AI Recovery Message Generated", "EXECUTED", reason="Compliance passed before message generation.", compliance="ALLOWED", outcome="MESSAGE_READY", metadata={"channel": payload.channel, "language": payload.language, "mode": result["mode"]})
        return {**result, "compliance": compliance}
    if case_type == "CHECKOUT":
        row = row_for(checkout_df, "checkout_id", payload.case_id)
        strategy = checkout_strategy(row, checkout_priority(row, float(checkout_df["cart_amount"].max() or 1))["recovery_priority"])
        compliance = compliance_check(consent=row.get("recovery_consent"), attempts_used=checkout_recovery_attempts.get(payload.case_id, 0), max_attempts=strategy["max_attempts"], channel=payload.channel)
        if not compliance["allowed"]:
            raise HTTPException(status_code=403, detail={"message": "Message generation blocked by compliance policy.", "compliance": compliance})
        result = ai_service.generate_recovery_message(customer_name=str(row.get("customer_id", "Customer")), amount=float(row["cart_amount"]), failure_reason="checkout abandonment", channel=payload.channel, language=payload.language, tone=payload.tone)
        record_audit("CHECKOUT", payload.case_id, "AI Recovery Message Generated", "EXECUTED", reason="Compliance passed before message generation.", compliance="ALLOWED", outcome="MESSAGE_READY", metadata={"channel": payload.channel, "language": payload.language, "mode": result["mode"]})
        return {**result, "compliance": compliance}
    if case_type == "INVOICE":
        row = row_for(invoices_df, "invoice_id", payload.case_id).to_dict()
        reasoning = invoice_reasoning(row)
        compliance = compliance_check(consent=row.get("consent"), dnd=row.get("dnd"), attempts_used=receivables_recovery_attempts.get(payload.case_id, 0), max_attempts=reasoning["max_attempts"], channel="EMAIL")
        if not compliance["allowed"]:
            raise HTTPException(status_code=403, detail={"message": "Message generation blocked by compliance policy.", "compliance": compliance})
        result = ai_service.generate_b2b_reminder(customer_name=str(row["customer_name"]), invoice_id=payload.case_id, amount=float(row["invoice_amount"]), days_overdue=int(row["days_overdue"]), language=payload.language, tone=payload.tone)
        record_audit("INVOICE", payload.case_id, "AI B2B Reminder Generated", "EXECUTED", reason="Compliance passed before message generation.", compliance="ALLOWED", outcome="MESSAGE_READY", metadata={"language": payload.language, "mode": result["mode"]})
        return {**result, "compliance": compliance}
    raise HTTPException(status_code=400, detail="Unsupported case_type")


@app.post("/compliance/opt-out")
def opt_out(payload: OptOutPayload) -> Dict[str, Any]:
    keyword = " ".join(payload.keyword.strip().upper().split())
    if keyword not in {"STOP", "UNSUBSCRIBE", "OPT OUT", "OPTOUT", "CANCEL"}:
        raise HTTPException(status_code=400, detail="Unsupported opt-out keyword")
    entity_type = payload.entity_type.strip().upper()
    if entity_type == "PAYMENT":
        row_for(df, "transaction_id", payload.entity_id)
        opted_out_entities.add(f"PAYMENT:{payload.entity_id}")
    elif entity_type == "CHECKOUT":
        row_for(checkout_df, "checkout_id", payload.entity_id)
        checkout_df.loc[checkout_df["checkout_id"].astype(str) == payload.entity_id, "recovery_consent"] = "NO"
    elif entity_type == "INVOICE":
        row_for(invoices_df, "invoice_id", payload.entity_id)
        invoices_df.loc[invoices_df["invoice_id"].astype(str) == payload.entity_id, "consent"] = "NO"
    else:
        raise HTTPException(status_code=400, detail="entity_type must be PAYMENT, CHECKOUT, or INVOICE")
    record_audit("COMPLIANCE", payload.entity_id, "Opt-Out Received", "BLOCKED", reason=f"Customer supplied {keyword}.", compliance="BLOCKED", outcome="OUTREACH_SUPPRESSED", metadata={"entity_type": entity_type, "keyword": keyword})
    return {"success": True, "message": "Opt-out recorded. Automated outreach is suppressed for this case.", "entity_type": entity_type, "entity_id": payload.entity_id}


@app.get("/mandates")
def get_mandates() -> Any:
    mandates: List[Dict[str, Any]] = []
    failed = df[df["status"] == "FAILED"]
    for _, row in failed.head(20).iterrows():
        tid = str(row["transaction_id"])
        if tid not in mandate_state:
            mandate_state[tid] = {"mandate_id": f"MND-{tid.upper()}", "transaction_id": tid, "status": "RETRY_ELIGIBLE", "attempts_used": 0, "max_attempts": 3, "next_retry": None}
        state = mandate_state[tid]
        mandates.append({**state, "amount": int(row["amount"]), "payment_method": str(row["payment_method"]), "failure_reason": str(row["failure_reason"])})
    return sanitize_for_json(mandates)


@app.post("/mandates/{mandate_id}/retry")
def retry_mandate(mandate_id: str, payload: MandateRetryPayload = MandateRetryPayload()) -> Any:
    mandate = next((m for m in mandate_state.values() if m["mandate_id"] == mandate_id), None)
    if mandate is None:
        # Ensure derived mandates are initialized before lookup.
        get_mandates()
        mandate = next((m for m in mandate_state.values() if m["mandate_id"] == mandate_id), None)
    if mandate is None:
        raise HTTPException(status_code=404, detail="Mandate not found")
    tid = mandate["transaction_id"]
    if mandate["attempts_used"] >= mandate["max_attempts"]:
        mandate["status"] = "ESCALATED"
        escalated = escalate("MANDATE", mandate_id, float(row_for(df, "transaction_id", tid)["amount"]), 90, "HIGH", mandate["attempts_used"], mandate["max_attempts"], "Maximum mandate retries reached.", "Manual review of recurring payment required")
        return {"success": False, "stopped": True, "status": "MANUAL_REVIEW", "escalated": escalated, "message": "Mandate retry limit reached; escalated to manual review."}
    row = row_for(df, "transaction_id", tid)
    compliance = compliance_check(attempts_used=mandate["attempts_used"], max_attempts=mandate["max_attempts"], channel="PAYMENT")
    record_audit("MANDATE", mandate_id, "Mandate Compliance Check", compliance["decision"], reason=" ".join(compliance["reasons"]), compliance=compliance["decision"])
    if not compliance["allowed"]:
        return {"success": False, "stopped": True, "status": "COMPLIANCE_BLOCKED", "message": "Mandate retry blocked by policy.", "compliance": compliance}
    mandate["attempts_used"] += 1
    attempt = mandate["attempts_used"]
    record_audit("MANDATE", mandate_id, "Mandate Retry", "EXECUTED", strategy="Mandate Retry Sequencer", reason="Bounded recurring-payment retry.", compliance="ALLOWED", metadata={"attempt": attempt, "max_attempts": mandate["max_attempts"]})
    success = int(FAILURE_PROBABILITIES.get(str(row.get("failure_reason", "")), 30)) >= 80 or (attempt >= mandate["max_attempts"] and int(FAILURE_PROBABILITIES.get(str(row.get("failure_reason", "")), 30)) >= 60)
    if success:
        mandate["status"] = "RECOVERED"
        df.loc[df["transaction_id"].astype(str) == tid, "status"] = "SUCCESS"
        recovered_transactions[tid] = int(row["amount"])
        record_audit("MANDATE", mandate_id, "Mandate Recovered", "SUCCESS", strategy="Mandate Retry Sequencer", reason="Retry succeeded in demo mode.", compliance="ALLOWED", outcome="RECOVERED", metadata={"amount_recovered": int(row["amount"])})
        return {"success": True, "stopped": True, "status": "RECOVERED", "amount_recovered": int(row["amount"]), "attempts_used": attempt, "max_attempts": mandate["max_attempts"], "message": "Mandate payment recovered."}
    if attempt >= mandate["max_attempts"]:
        mandate["status"] = "ESCALATED"
        escalated = escalate("MANDATE", mandate_id, float(row["amount"]), 90, "HIGH", attempt, mandate["max_attempts"], "Mandate retry sequence exhausted without recovery.", "Manual review required")
        return {"success": False, "stopped": True, "status": "MANUAL_REVIEW", "escalated": escalated, "attempts_used": attempt, "max_attempts": mandate["max_attempts"], "message": "Mandate retry sequence exhausted and was escalated."}
    mandate["status"] = "RETRY_ELIGIBLE"
    mandate["next_retry"] = (datetime.now(LOCAL_TZ) + timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    return {"success": False, "stopped": False, "status": "RETRY_ELIGIBLE", "attempts_used": attempt, "max_attempts": mandate["max_attempts"], "attempts_remaining": mandate["max_attempts"] - attempt, "next_retry": mandate["next_retry"], "message": "Mandate retry failed; next bounded retry is scheduled."}


@app.get("/analytics")
def analytics() -> Dict[str, Any]:
    failed = df[df["status"] == "FAILED"]
    reason_counts = failed["failure_reason"].value_counts().to_dict() if not failed.empty else {}
    method_counts = failed["payment_method"].value_counts().to_dict() if not failed.empty and "payment_method" in failed else {}
    priority = get_prioritized_transactions()
    priority_counts = {p: sum(1 for x in priority if x["recovery_priority"] == p) for p in ["HIGH", "MEDIUM", "LOW"]}
    strategy_revenue: Dict[str, int] = {}
    failure_revenue: Dict[str, int] = {}
    for x in priority:
        strategy_revenue[x["strategy_name"]] = strategy_revenue.get(x["strategy_name"], 0) + int(x["estimated_recoverable_revenue"])
        failure_revenue[x["failure_reason"]] = failure_revenue.get(x["failure_reason"], 0) + int(x["estimated_recoverable_revenue"])
    recovered_by_strategy: Dict[str, int] = {}
    for event in audit_log:
        if event.get("outcome") == "RECOVERED" and event.get("strategy"):
            recovered_by_strategy[event["strategy"]] = recovered_by_strategy.get(event["strategy"], 0) + int((event.get("metadata") or {}).get("amount_recovered", 0) or 0)
    aging: Dict[str, int] = {}
    if not invoices_df.empty:
        aging = invoices_df["aging_bucket"].fillna("Unknown").value_counts().to_dict()
    payment_at_risk = int(failed["amount"].sum()) if not failed.empty else 0
    checkout_at_risk = int(checkout_df[checkout_df["status"] == "ABANDONED"]["cart_amount"].sum()) if not checkout_df.empty else 0
    overdue_invoices = invoices_df[(invoices_df["payment_status"].isin(["UNPAID", "PARTIALLY_PAID"])) & (invoices_df["days_overdue"] > 0)]
    receivable_at_risk = int(overdue_invoices["invoice_amount"].sum()) if not overdue_invoices.empty else 0
    total_at_risk = payment_at_risk + checkout_at_risk + receivable_at_risk
    recovered = sum(recovered_transactions.values()) + sum(recovered_checkouts.values()) + sum(recovered_invoices.values())
    return sanitize_for_json({
        "failure_reasons": [{"name": k, "value": int(v)} for k, v in reason_counts.items()],
        "payment_methods": [{"name": k, "value": int(v)} for k, v in method_counts.items()],
        "priority_distribution": [{"name": k, "value": v} for k, v in priority_counts.items()],
        "revenue_by_failure": [{"name": k, "value": int(v)} for k, v in failure_revenue.items()],
        "aging_buckets": [{"name": k, "value": int(v)} for k, v in aging.items()],
        "strategy_recovery_estimate": [{"strategy": k, "estimated_recoverable": int(v), "actual_recovered": int(recovered_by_strategy.get(k, 0))} for k, v in sorted(strategy_revenue.items(), key=lambda x: x[1], reverse=True)],
        "strategy_recovery_actual": [{"strategy": k, "actual_recovered": int(v)} for k, v in sorted(recovered_by_strategy.items(), key=lambda x: x[1], reverse=True)],
        "workflow_recovery": [
            {"workflow": "Payments", "recovered": int(sum(recovered_transactions.values()))},
            {"workflow": "Checkouts", "recovered": int(sum(recovered_checkouts.values()))},
            {"workflow": "B2B Receivables", "recovered": int(sum(recovered_invoices.values()))},
        ],
        "payment_revenue_at_risk": payment_at_risk,
        "checkout_revenue_at_risk": checkout_at_risk,
        "receivables_revenue_at_risk": receivable_at_risk,
        "total_revenue_at_risk": total_at_risk,
        "actual_revenue_recovered": recovered,
        "recovery_rate": round(recovered / total_at_risk * 100, 2) if total_at_risk else 0,
    })


@app.post("/run-recovery-batch")
def run_recovery_batch() -> Dict[str, Any]:
    batch_id = f"BATCH-{uuid.uuid4().hex[:8].upper()}"
    record_audit("BATCH", batch_id, "Batch Recovery Started", "STARTED", reason="Prioritized multi-workflow recovery run started.")
    payment_cases = get_prioritized_transactions()
    checkout_cases = get_prioritized_checkouts()
    queue = [("PAYMENT", x["transaction_id"], x["priority_score"]) for x in payment_cases[:20]] + [("CHECKOUT", x["checkout_id"], x["priority_score"]) for x in checkout_cases[:10]]
    queue.sort(key=lambda x: x[2], reverse=True)
    results = {"payments_recovered": 0, "checkouts_recovered": 0, "payment_revenue_recovered": 0, "checkout_revenue_recovered": 0, "cases_stopped": 0, "cases_escalated_to_manual_review": 0, "payment_cases_processed": 0, "checkout_cases_processed": 0, "errors": []}
    for case_type, case_id, _ in queue:
        try:
            result = retry_payment(case_id) if case_type == "PAYMENT" else recover_checkout(case_id)
            if case_type == "PAYMENT":
                results["payment_cases_processed"] += 1
                if result.get("success"):
                    results["payments_recovered"] += 1
                    results["payment_revenue_recovered"] += int(result.get("amount_recovered", 0))
            else:
                results["checkout_cases_processed"] += 1
                if result.get("success"):
                    results["checkouts_recovered"] += 1
                    results["checkout_revenue_recovered"] += int(result.get("amount_recovered", 0))
            if result.get("stopped"):
                results["cases_stopped"] += 1
            if result.get("escalated"):
                results["cases_escalated_to_manual_review"] += 1
        except Exception as exc:
            results["errors"].append({"case_type": case_type, "case_id": case_id, "error": str(exc)})
    results["total_cases_processed"] = results["payment_cases_processed"] + results["checkout_cases_processed"]
    results["total_cases_recovered"] = results["payments_recovered"] + results["checkouts_recovered"]
    results["total_revenue_recovered"] = results["payment_revenue_recovered"] + results["checkout_revenue_recovered"]
    record_audit("BATCH", batch_id, "Batch Recovery Completed", "COMPLETED", reason=f"Processed {results['total_cases_processed']} cases.", outcome="COMPLETED", metadata=results)
    return sanitize_for_json({"batch_id": batch_id, "message": "Batch recovery completed", **results})


@app.get("/overall-recovery-summary")
def get_overall_recovery_summary() -> Dict[str, Any]:
    payment_summary = get_recovery_summary()
    checkout_summary = get_checkout_recovery_summary()
    receivables_summary = get_receivables_summary()
    payment_failed = df[df["status"] == "FAILED"]
    abandoned = checkout_df[checkout_df["status"] == "ABANDONED"]
    unpaid = invoices_df[invoices_df["payment_status"].isin(["UNPAID", "PARTIALLY_PAID"])]
    payment_risk = int(payment_failed["amount"].sum()) if not payment_failed.empty else 0
    checkout_risk = int(abandoned["cart_amount"].sum()) if not abandoned.empty else 0
    overdue_unpaid = unpaid[unpaid["days_overdue"] > 0]
    invoice_risk = int(overdue_unpaid["invoice_amount"].sum()) if not overdue_unpaid.empty else 0
    at_risk = payment_risk + checkout_risk + invoice_risk
    estimated = int(payment_summary["estimated_recoverable_revenue"] + sum(x["estimated_recoverable_revenue"] for x in get_prioritized_checkouts()) + invoice_risk * 0.70)
    recovered = int(payment_summary["actual_recovered_revenue"] + checkout_summary["actual_recovered_revenue"] + receivables_summary["actual_recovered_revenue"])
    active_cases = len(payment_failed) + len(abandoned) + len(unpaid)
    recovered_cases = payment_summary["recovered_transactions"] + checkout_summary["recovered_checkouts"] + receivables_summary["recovered_invoices_count"]
    return sanitize_for_json({"ai_mode": ai_service.mode, "total_revenue_at_risk": at_risk, "estimated_recoverable_revenue": estimated, "actual_revenue_recovered": recovered, "payment_revenue_recovered": payment_summary["actual_recovered_revenue"], "checkout_revenue_recovered": checkout_summary["actual_recovered_revenue"], "receivables_revenue_recovered": receivables_summary["actual_recovered_revenue"], "payments_recovered": payment_summary["recovered_transactions"], "checkouts_recovered": checkout_summary["recovered_checkouts"], "invoices_recovered": receivables_summary["recovered_invoices_count"], "total_cases": active_cases + recovered_cases, "active_recovery_cases": active_cases, "total_recovered_cases": recovered_cases, "overall_recovery_rate": round(recovered / at_risk * 100, 2) if at_risk else 0, "manual_review_cases": sum(1 for c in manual_review_cases.values() if c.get("status") == "MANUAL_REVIEW")})


@app.get("/recent-recovery-activity")
def get_recent_recovery_activity() -> Any:
    events = []
    for e in audit_log:
        events.append({"case_type": e["entity_type"], "case_id": e["entity_id"], "action": e["action"], "details": e["reason"], "status": e["decision"], "timestamp": e["timestamp"], "strategy": e.get("strategy", ""), "outcome": e.get("outcome", "")})
    events.sort(key=lambda x: x["timestamp"], reverse=True)
    return sanitize_for_json(events[:50])


@app.post("/demo/run-flow")
def demo_run_flow() -> Dict[str, Any]:
    # The demo uses real local rows and executes actual backend functions.
    failed = get_prioritized_transactions()
    checkout = get_prioritized_checkouts()
    steps: List[Dict[str, Any]] = []
    if failed:
        tid = failed[0]["transaction_id"]
        analysis = analyze_transaction(tid)
        steps.append({"step": 1, "title": "Payment failure detected", "status": "COMPLETE", "summary": f"{tid} entered the recovery queue."})
        steps.append({"step": 2, "title": "Revenue prioritized", "status": "COMPLETE", "summary": f"{analysis['recovery_priority']} priority at {analysis['priority_score']}/100."})
        steps.append({"step": 3, "title": "Failure diagnosed", "status": "COMPLETE", "summary": analysis["diagnosis"]})
        steps.append({"step": 4, "title": "Strategy selected", "status": "COMPLETE", "summary": analysis["strategy"]})
        steps.append({"step": 5, "title": "Compliance evaluated", "status": analysis["compliance"]["decision"], "summary": " ".join(analysis["compliance"]["reasons"])})
        link = generate_payment_link(PaymentLinkPayload(transaction_id=tid, amount=float(analysis["amount"])))
        steps.append({"step": 6, "title": "Recovery payment link generated", "status": "SIMULATION", "summary": f"Sandbox link {link['link_id']} generated."})
        try:
            msg = generate_message(MessagePayload(case_type="PAYMENT", case_id=tid, channel="WHATSAPP", language="Hinglish", payment_link=link["url"]))
            steps.append({"step": 7, "title": "AI recovery message generated", "status": "COMPLETE", "summary": msg["message"]})
        except HTTPException as exc:
            steps.append({"step": 7, "title": "AI recovery message generated", "status": "BLOCKED", "summary": str(exc.detail)})
        result = retry_payment(tid)
        steps.append({"step": 8, "title": "Recovery action executed", "status": "SUCCESS" if result.get("success") else result.get("status", "IN_PROGRESS"), "summary": result.get("message", "Action executed.")})
        steps.append({"step": 9, "title": "Outcome evaluated", "status": "COMPLETE", "summary": "Recovery state updated and bounded stopping rules evaluated."})
        steps.append({"step": 10, "title": "Audit trail updated", "status": "COMPLETE", "summary": f"{len(events_for('PAYMENT', tid))} backend audit events now exist for {tid}."})
    elif checkout:
        cid = checkout[0]["checkout_id"]
        result = recover_checkout(cid)
        steps.append({"step": 1, "title": "Checkout abandonment detected", "status": "COMPLETE", "summary": f"{cid} entered the recovery queue."})
        steps.append({"step": 2, "title": "Checkout recovery action executed", "status": result.get("status", "IN_PROGRESS"), "summary": result.get("message", "Action executed.")})
    return sanitize_for_json({"demo_id": f"DEMO-{uuid.uuid4().hex[:8].upper()}", "ai_mode": ai_service.mode, "steps": steps, "message": "Live demo flow executed using the local dataset."})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)

"""AI service abstraction for RevenueRescue AI.

The service always has a deterministic local fallback and can optionally use an
OpenAI-compatible HTTP endpoint when configured through environment variables.
No API key is ever sent to the frontend.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, Optional


class AIService:
    def __init__(self) -> None:
        self.api_key = os.getenv("OPENAI_API_KEY", "").strip()
        self.base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        self.model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        self.timeout = float(os.getenv("AI_TIMEOUT_SECONDS", "8"))

    @property
    def mode(self) -> str:
        return "external_llm" if self.api_key else "deterministic_fallback"

    def _llm(self, system: str, user: str) -> Optional[str]:
        if not self.api_key:
            return None
        payload = {
            "model": self.model,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
            return str(body["choices"][0]["message"]["content"]).strip()
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, KeyError, IndexError, json.JSONDecodeError):
            return None

    @staticmethod
    def _channel_prefix(channel: str) -> str:
        return {"SMS": "SMS", "WHATSAPP": "WhatsApp", "EMAIL": "Email"}.get(channel.upper(), channel)

    def generate_recovery_message(
        self,
        *,
        customer_name: str = "Customer",
        amount: float,
        failure_reason: str = "payment issue",
        channel: str = "WHATSAPP",
        language: str = "English",
        tone: str = "professional",
        payment_link: Optional[str] = None,
    ) -> Dict[str, Any]:
        language = "Hinglish" if language.lower() == "hinglish" else "English"
        channel = self._channel_prefix(channel)
        link_text = f" Complete your payment here: {payment_link}" if payment_link else ""

        if language == "Hinglish":
            message = (
                f"Hi {customer_name}, aapka ₹{int(amount):,} payment {failure_reason.lower()} ki wajah se complete nahi ho paya. "
                f"Aap payment dobara complete kar sakte hain.{link_text} Agar aapko help chahiye, reply karein."
            )
        else:
            message = (
                f"Hi {customer_name}, your ₹{int(amount):,} payment could not be completed due to {failure_reason.lower()}. "
                f"You can securely complete the payment again.{link_text} Reply if you need help."
            )

        llm = self._llm(
            "You write concise, professional payment-recovery messages. Never invent customer facts. Respect opt-out and compliance decisions already made by the caller.",
            f"Create a {tone} {language} {channel} recovery message for {customer_name}. Amount ₹{amount:.0f}. Reason: {failure_reason}. Link: {payment_link or 'none'}.",
        )
        if llm:
            message = llm

        return {
            "message": message,
            "channel": channel,
            "language": language,
            "tone": tone,
            "mode": self.mode if llm else "deterministic_fallback",
            "reason": f"Generated for {failure_reason} using the selected recovery channel and tone.",
        }

    def generate_b2b_reminder(
        self,
        *,
        customer_name: str,
        invoice_id: str,
        amount: float,
        days_overdue: int,
        language: str = "English",
        tone: str = "professional",
    ) -> Dict[str, Any]:
        if language.lower() == "hinglish":
            message = (
                f"Hello {customer_name}, invoice {invoice_id} of ₹{int(amount):,} is {days_overdue} days overdue. "
                "Kindly share the expected payment date so we can update our records. Thank you."
            )
        else:
            message = (
                f"Hello {customer_name}, invoice {invoice_id} for ₹{int(amount):,} is {days_overdue} days overdue. "
                "Please confirm the expected settlement date so we can update our records. Thank you."
            )
        llm = self._llm(
            "Draft concise professional B2B accounts-receivable reminders. Never invent facts or threaten customers.",
            f"Draft a {tone} {language} reminder. Customer: {customer_name}; invoice: {invoice_id}; amount: ₹{amount:.0f}; overdue: {days_overdue} days.",
        )
        if llm:
            message = llm
        return {
            "message": message,
            "channel": "EMAIL",
            "language": "Hinglish" if language.lower() == "hinglish" else "English",
            "tone": tone,
            "mode": self.mode if llm else "deterministic_fallback",
        }


ai_service = AIService()

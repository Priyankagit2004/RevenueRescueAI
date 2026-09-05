import { useCallback, useEffect, useState } from "react";
import "./App.css";

const API = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000";
const money = (value) => `₹${Number(value || 0).toLocaleString("en-IN")}`;

function CheckoutAnalysis() {
  const checkoutId = window.location.pathname.split("/").pop();
  const [checkout, setCheckout] = useState(null);
  const [audit, setAudit] = useState([]);
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState(false);
  const [message, setMessage] = useState("");
  const [generatedMessage, setGeneratedMessage] = useState(null);
  const [paymentLink, setPaymentLink] = useState(null);

  // Recovery message controls
  const [messageChannel, setMessageChannel] = useState("WHATSAPP");
  const [messageLanguage, setMessageLanguage] = useState("HINGLISH");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [analysisRes, auditRes] = await Promise.all([
        fetch(`${API}/analyze-checkout/${checkoutId}`),
        fetch(`${API}/checkout-audit/${checkoutId}`)
      ]);

      const analysis = await analysisRes.json();

      if (!analysisRes.ok) {
        throw new Error(analysis.detail || "Checkout not found");
      }

      const auditJson = await auditRes
        .json()
        .catch(() => ({ events: [] }));

      setCheckout(analysis);
      setAudit(
        Array.isArray(auditJson.events)
          ? auditJson.events
          : []
      );
    } catch (err) {
      setMessage(err.message);
      setCheckout(null);
    } finally {
      setLoading(false);
    }
  }, [checkoutId]);

  useEffect(() => {
    load();
  }, [load]);

  const recover = async () => {
    setWorking(true);
    setMessage("");

    try {
      const res = await fetch(
        `${API}/recover-checkout/${checkoutId}`,
        {
          method: "POST"
        }
      );

      const json = await res.json();

      if (!res.ok) {
        throw new Error(
          json.detail ||
          json.message ||
          "Recovery failed"
        );
      }

      setMessage(json.message);
      await load();
    } catch (err) {
      setMessage(err.message);
    } finally {
      setWorking(false);
    }
  };

  const generateMessage = async () => {
    try {
      const res = await fetch(
        `${API}/ai/generate-message`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json"
          },
          body: JSON.stringify({
            case_type: "CHECKOUT",
            case_id: checkoutId,
            channel: messageChannel,
            language: messageLanguage,
            tone: "professional"
          })
        }
      );

      const json = await res.json();

      if (!res.ok) {
        throw new Error(
          json.detail?.message ||
          json.detail ||
          "Message generation blocked"
        );
      }

      setGeneratedMessage(json);
    } catch (err) {
      setMessage(err.message);
    }
  };

  const generateLink = async () => {
    try {
      const res = await fetch(
        `${API}/payment-links/generate`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json"
          },
          body: JSON.stringify({
            checkout_id: checkoutId,
            amount: Number(checkout.cart_amount)
          })
        }
      );

      const json = await res.json();

      if (!res.ok) {
        throw new Error(
          json.detail || "Unable to generate link"
        );
      }

      setPaymentLink(json);
      setMessage("Sandbox payment link generated.");
      await load();
    } catch (err) {
      setMessage(err.message);
    }
  };

  if (loading) {
    return (
      <div className="app analysis-page">
        <div className="loading-panel">
          🤖 Loading checkout agent analysis…
        </div>
      </div>
    );
  }

  if (!checkout) {
    return (
      <div className="app analysis-page">
        <button
          className="back-button"
          onClick={() => (window.location.href = "/")}
        >
          ← Back to Command Center
        </button>

        <div className="alert error">
          {message || "Checkout not found."}
        </div>
      </div>
    );
  }

  const compliance = checkout.compliance || {};

  return (
    <div className="app analysis-page">
      <button
        className="back-button"
        onClick={() => (window.location.href = "/")}
      >
        ← Back to Command Center
      </button>

      <div className="analysis-hero">
        <div>
          <span className="eyebrow">
            ABANDONED CHECKOUT RECOVERY
          </span>

          <h1>Checkout Agent Analysis</h1>

          <p className="subtitle">
            AI prioritization, compliance and bounded outreach.
          </p>
        </div>

        <span
          className={`priority-badge ${String(
            checkout.recovery_priority
          ).toLowerCase()}`}
        >
          {checkout.recovery_priority} ·{" "}
          {Number(checkout.priority_score).toFixed(1)}
        </span>
      </div>

      <div className="transaction-summary">
        <div className="info-card">
          <span>Checkout ID</span>
          <h3>{checkout.checkout_id}</h3>
        </div>

        <div className="info-card">
          <span>Cart Value</span>
          <h3>{money(checkout.cart_amount)}</h3>
        </div>

        <div className="info-card">
          <span>Stage</span>
          <h3>{checkout.abandonment_stage}</h3>
        </div>

        <div className="info-card">
          <span>Consent</span>
          <h3>{checkout.recovery_consent}</h3>
        </div>
      </div>

      <section className="analysis-card decision-box">
        <div className="card-heading">
          <span>🧠 AI Abandonment Diagnosis</span>

          <span className="status-chip">
            {checkout.strategy_name}
          </span>
        </div>

        <h2>{checkout.diagnosis}</h2>

        <p>{checkout.strategy_reason}</p>
      </section>

      <section className="analysis-card">
        <div className="analysis-grid">
          <div className="info-box">
            <span>Recovery Probability</span>
            <strong>
              {checkout.recovery_probability}%
            </strong>
          </div>

          <div className="info-box">
            <span>Priority Score</span>
            <strong>
              {Number(checkout.priority_score).toFixed(1)}/100
            </strong>
          </div>

          <div className="info-box">
            <span>Estimated Recovery</span>
            <strong>
              {money(
                checkout.estimated_recoverable_revenue
              )}
            </strong>
          </div>

          <div className="info-box">
            <span>Attempts</span>
            <strong>
              {checkout.recovery_attempts_used}/
              {checkout.max_recovery_attempts}
            </strong>
          </div>
        </div>
      </section>

      <section className="analysis-card">
        <div className="card-heading">
          <span>🛡️ Compliance Gate</span>

          <span
            className={`status-chip ${
              compliance.allowed
                ? "allowed"
                : "blocked"
            }`}
          >
            {compliance.decision}
          </span>
        </div>

        <p>
          {(compliance.reasons || []).join(" ")}
        </p>

        <small>{compliance.policy}</small>
      </section>

      <section className="analysis-card">
        <div className="card-heading">
          <span>🤖 Agent Workflow</span>
          <span>Structured decision trace</span>
        </div>

        <div className="agent-stages">
          {(checkout.stages || []).map((stage) => (
            <div
              className="agent-stage"
              key={stage.stage}
            >
              <span
                className={`stage-dot ${String(
                  stage.status
                ).toLowerCase()}`}
              />

              <div>
                <strong>{stage.stage}</strong>

                <small>
                  {stage.status} · {stage.summary}
                </small>
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* Recovery Message */}
      <section className="analysis-card">
        <div className="card-heading">
          <span>💬 Recovery Message</span>

          <div
            style={{
              display: "flex",
              gap: "8px",
              alignItems: "center",
              flexWrap: "wrap"
            }}
          >
            <select
              value={messageChannel}
              onChange={(e) =>
                setMessageChannel(e.target.value)
              }
            >
              <option value="WHATSAPP">
                WhatsApp
              </option>

              <option value="SMS">
                SMS
              </option>

              <option value="EMAIL">
                Email
              </option>
            </select>

            <select
              value={messageLanguage}
              onChange={(e) =>
                setMessageLanguage(e.target.value)
              }
            >
              <option value="ENGLISH">
                English
              </option>

              <option value="HINGLISH">
                Hinglish
              </option>
            </select>

            <button
              className="secondary-button"
              onClick={generateMessage}
            >
              Generate
            </button>
          </div>
        </div>

        {generatedMessage ? (
          <div className="message-preview">
            <div>
              <span>
                {generatedMessage.channel}
              </span>

              <span>
                {generatedMessage.language}
              </span>

              <span>
                {generatedMessage.mode}
              </span>
            </div>

            <p>{generatedMessage.message}</p>
          </div>
        ) : (
          <p>
            Generate a compliant customer recovery
            message.
          </p>
        )}
      </section>

      <section className="analysis-card">
        <div className="card-heading">
          <span>🔗 Payment Link</span>

          <span className="status-chip">
            TEST MODE SIMULATION
          </span>
        </div>

        {paymentLink && (
          <div className="link-preview">
            <strong>{paymentLink.url}</strong>

            <small>
              {money(paymentLink.amount)} ·{" "}
              {paymentLink.mode}
            </small>
          </div>
        )}

        <button
          className="secondary-button"
          onClick={generateLink}
        >
          Generate Payment Link
        </button>
      </section>

      <section className="action-panel">
        <div>
          <span className="eyebrow">
            ACTION EXECUTION
          </span>

          <h2>
            {checkout.status === "RECOVERED"
              ? "Checkout recovered"
              : "Recover this checkout"}
          </h2>

          <p>{checkout.recommended_action}</p>
        </div>

        <button
          className="primary-button"
          disabled={
            working ||
            checkout.status === "RECOVERED" ||
            checkout.attempts_remaining <= 0
          }
          onClick={recover}
        >
          {working
            ? "Executing…"
            : checkout.status === "RECOVERED"
            ? "Recovered"
            : "Execute Recovery"}
        </button>
      </section>

      {message && (
        <div className="alert success">
          {message}
        </div>
      )}

      <section className="analysis-card">
        <div className="card-heading">
          <span>📜 Backend Audit Trail</span>

          <span>{audit.length} events</span>
        </div>

        {audit.length ? (
          audit
            .slice()
            .reverse()
            .map((event, i) => (
              <div
                className="audit-row"
                key={`${event.timestamp}-${i}`}
              >
                <strong>{event.action}</strong>

                <span>{event.decision}</span>

                <p>
                  {event.reason ||
                    event.outcome ||
                    "Decision recorded"}
                </p>

                <small>{event.timestamp}</small>
              </div>
            ))
        ) : (
          <p>No audit events yet.</p>
        )}
      </section>
    </div>
  );
}

export default CheckoutAnalysis;
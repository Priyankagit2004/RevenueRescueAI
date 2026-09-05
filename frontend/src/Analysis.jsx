import { useCallback, useEffect, useMemo, useState } from "react";
import "./App.css";

const API = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000";
const money = (value) => `₹${Number(value || 0).toLocaleString("en-IN")}`;

function Analysis() {
  const transactionId = window.location.pathname.split("/").pop();
  const [data, setData] = useState(null);
  const [auditTrail, setAuditTrail] = useState([]);
  const [message, setMessage] = useState("");
  const [messageGenerationMessage, setMessageGenerationMessage] = useState("");
  const [optOutMessage, setOptOutMessage] = useState("");
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState(false);
  const [messageLanguage, setMessageLanguage] = useState("Hinglish");
  const [channel, setChannel] = useState("WHATSAPP");
  const [generatedMessage, setGeneratedMessage] = useState(null);
  const [paymentLink, setPaymentLink] = useState(null);

  const fetchAudit = useCallback(async () => {
    const res = await fetch(`${API}/audit/${transactionId}`);
    if (res.ok) {
      const json = await res.json();
      setAuditTrail(Array.isArray(json.events) ? json.events : []);
    }
  }, [transactionId]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch(`${API}/analyze/${transactionId}`);
      const json = await res.json();
      if (!res.ok) throw new Error(json.detail || "Transaction not found");
      setData(json);
      await fetchAudit();
    } catch (err) {
      setMessage(err.message);
    } finally {
      setLoading(false);
    }
  }, [transactionId, fetchAudit]);

  useEffect(() => { load(); }, [load]);

  const retry = async () => {
    setWorking(true);
    setMessage("");
    try {
      const res = await fetch(`${API}/retry/${transactionId}`, { method: "POST" });
      const json = await res.json();
      if (!res.ok) throw new Error(json.detail || json.message || "Retry failed");
      setMessage(json.message || "Recovery action completed.");
      await load();
    } catch (err) {
      setMessage(err.message);
      await fetchAudit();
    } finally {
      setWorking(false);
    }
  };

  const generateLink = async () => {
    try {
      const res = await fetch(`${API}/payment-links/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          transaction_id: transactionId,
          amount: Number(data.amount)
        })
      });
      const json = await res.json();
      if (!res.ok) throw new Error(json.detail || "Unable to generate payment link");
      setPaymentLink(json);
      setMessage("Sandbox payment link generated. No live Razorpay API call was made.");
      await fetchAudit();
    } catch (err) {
      setMessage(err.message);
    }
  };

  const generateMessage = async () => {
    setMessageGenerationMessage("");
    try {
      const res = await fetch(`${API}/ai/generate-message`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          case_type: "PAYMENT",
          case_id: transactionId,
          channel,
          language: messageLanguage,
          tone: "professional",
          payment_link: paymentLink?.url || ""
        })
      });

      const json = await res.json();

      if (!res.ok) {
        const errorMessage =
          typeof json.detail === "string"
            ? json.detail
            : json.detail?.message ||
              "Message generation blocked by compliance policy.";

        throw new Error(errorMessage);
      }

      setGeneratedMessage(json);
      await fetchAudit();
    } catch (err) {
      setGeneratedMessage(null);
      setMessageGenerationMessage(err.message);
    }
  };

  const optOut = async () => {
    try {
      const res = await fetch(`${API}/compliance/opt-out`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          entity_type: "PAYMENT",
          entity_id: transactionId,
          keyword: "STOP"
        })
      });

      const json = await res.json();

      if (!res.ok) throw new Error(json.detail || "Unable to record opt-out");

      setOptOutMessage(json.message);
      await fetchAudit();
    } catch (err) {
      setMessage(err.message);
    }
  };

  const stageStatus = useMemo(() => data?.stages || [], [data]);

  if (loading) {
    return (
      <div className="app analysis-page">
        <div className="loading-panel">🤖 Agent is analyzing transaction…</div>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="app analysis-page">
        <button
          className="back-button"
          onClick={() => window.location.href = "/"}
        >
          ← Back to Command Center
        </button>

        <div className="alert error">
          {message || "Unable to analyze transaction."}
        </div>
      </div>
    );
  }

  const compliance = data.compliance || {};

  const stopped =
    data.status === "SUCCESS" ||
    data.attempts_remaining <= 0 ||
    data.recovery_priority === "LOW" &&
      data.recovery_attempts_used >= data.max_recovery_attempts;

  return (
    <div className="app analysis-page">

      <button
        className="back-button"
        onClick={() => window.location.href = "/"}
      >
        ← Back to Command Center
      </button>

      {message && (
        <div className="alert success">
          {message}
        </div>
      )}

      <div className="analysis-hero">
        <div>
          <span className="eyebrow">
            AUTONOMOUS PAYMENT RECOVERY
          </span>

          <h1>AI Transaction Analysis</h1>

          <p className="subtitle">
            Structured decision summary — not hidden chain-of-thought.
          </p>
        </div>

        <span
          className={`priority-badge ${String(
            data.recovery_priority
          ).toLowerCase()}`}
        >
          {data.recovery_priority} ·{" "}
          {Number(data.priority_score).toFixed(1)}
        </span>
      </div>

      <div className="transaction-summary">
        <div className="info-card">
          <span>Transaction ID</span>
          <h3>{data.transaction_id}</h3>
        </div>

        <div className="info-card">
          <span>Amount</span>
          <h3>{money(data.amount)}</h3>
        </div>

        <div className="info-card">
          <span>Payment Method</span>
          <h3>{data.payment_method}</h3>
        </div>

        <div className="info-card">
          <span>Failure</span>
          <h3>{data.failure_reason}</h3>
        </div>
      </div>

      <section className="analysis-card decision-box">
        <div className="card-heading">
          <span>🧠 AI Diagnosis</span>
          <span className="status-chip">{data.strategy}</span>
        </div>

        <h2>{data.diagnosis}</h2>

        <p>{data.strategy_reason}</p>

        <div className="factor-list">
          {(data.factors_considered || []).map((factor) => (
            <span key={factor}>{factor}</span>
          ))}
        </div>
      </section>

      <section className="analysis-card">
        <div className="card-heading">
          <span>🎯 Recovery Decision</span>
          <span className="eyebrow">PRIORITIZATION</span>
        </div>

        <div className="analysis-grid">
          <div className="info-box">
            <span>Recovery Probability</span>
            <strong>{data.recovery_probability}%</strong>
          </div>

          <div className="info-box">
            <span>Priority Score</span>
            <strong>
              {Number(data.priority_score).toFixed(1)}/100
            </strong>
          </div>

          <div className="info-box">
            <span>Estimated Recoverable</span>
            <strong>
              {money(data.estimated_recoverable_revenue)}
            </strong>
          </div>

          <div className="info-box">
            <span>Next Action</span>
            <strong>{data.next_action}</strong>
          </div>
        </div>

        <p className="explanation">
          The score combines recovery likelihood and business value.
          HIGH ≥ 92, MEDIUM ≥ 75, LOW &lt; 75.
        </p>
      </section>

      <section className="analysis-card">
        <div className="card-heading">
          <span>🛡️ Compliance Gate</span>

          <span
            className={`status-chip ${
              compliance.allowed ? "allowed" : "blocked"
            }`}
          >
            {compliance.decision}
          </span>
        </div>

        <p>{(compliance.reasons || []).join(" ")}</p>

        <small>{compliance.policy}</small>
      </section>

      <section className="analysis-card">
        <div className="card-heading">
          <span>🤖 9-Stage Agent Workflow</span>
          <span>
            Attempt {data.recovery_attempts_used} /{" "}
            {data.max_recovery_attempts}
          </span>
        </div>

        <div className="agent-stages">
          {stageStatus.map((stage) => (
            <div className="agent-stage" key={stage.stage}>
              <span
                className={`stage-dot ${stage.status.toLowerCase()}`}
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

      <section className="analysis-card">
        <div className="card-heading">
          <span>💬 AI Recovery Message</span>

          <div className="inline-controls">
            <select
              value={channel}
              onChange={(e) => setChannel(e.target.value)}
            >
              <option value="WHATSAPP">WhatsApp</option>
              <option value="SMS">SMS</option>
              <option value="EMAIL">Email</option>
            </select>

            <select
              value={messageLanguage}
              onChange={(e) => setMessageLanguage(e.target.value)}
            >
              <option>English</option>
              <option>Hinglish</option>
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
              <span>{generatedMessage.channel}</span>
              <span>{generatedMessage.language}</span>
              <span>{generatedMessage.mode}</span>
            </div>

            <p>{generatedMessage.message}</p>

            <small>{generatedMessage.reason}</small>
          </div>
        ) : (
          <p>
            Generate a compliant message after the agent checks
            consent, DND, quiet hours and retry limits.
          </p>
        )}
      </section>

      {messageGenerationMessage && (
        <div className="alert error">
          {messageGenerationMessage}
        </div>
      )}

      <section className="analysis-card">
        <div className="card-heading">
          <span>🔗 Payment Link Recovery</span>
          <span className="status-chip">
            TEST MODE SIMULATION
          </span>
        </div>

        {paymentLink ? (
          <div className="link-preview">
            <strong>{paymentLink.url}</strong>

            <small>
              {money(paymentLink.amount)} · {paymentLink.mode}
            </small>
          </div>
        ) : (
          <p>
            Generate a sandbox/demo link. This implementation does
            not call the live Razorpay API.
          </p>
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
          <span className="eyebrow">ACTION EXECUTION</span>

          <h2>
            {stopped
              ? "Recovery is stopped"
              : "Execute bounded recovery"}
          </h2>

          <p>
            {data.attempts_remaining} attempt(s) remaining.
            Backend enforces the limit.
          </p>
        </div>

        <button
          className="primary-button"
          disabled={working || stopped}
          onClick={retry}
        >
          {working
            ? "Executing…"
            : data.status === "SUCCESS"
              ? "Already Recovered"
              : "Retry Recovery"}
        </button>
      </section>

      <section className="analysis-card">
        <div className="card-heading">
          <span>🚫 Customer Opt-Out</span>
          <span>Demo compliance control</span>
        </div>

        <p>
          Record a STOP request to suppress automated outreach
          for this payment case.
        </p>

        <button
          className="secondary-button danger-button"
          onClick={optOut}
        >
          Record STOP / Opt-Out
        </button>
      </section>

      {optOutMessage && (
      <div className="alert success">
        {optOutMessage}
      </div>
      )}

      <section className="analysis-card">
        <div className="card-heading">
          <span>📜 Backend Audit Trail</span>
          <span>{auditTrail.length} events</span>
        </div>

        {auditTrail.length ? (
          <div className="audit-timeline">
            {auditTrail
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

                  <small>
                    {event.entity_type} · {event.entity_id} ·{" "}
                    {event.timestamp}
                  </small>
                </div>
              ))}
          </div>
        ) : (
          <p>No audit events yet.</p>
        )}
      </section>
    </div>
  );
}

export default Analysis;
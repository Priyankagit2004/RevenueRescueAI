import { useCallback, useEffect, useMemo, useState } from "react";
import Analysis from "./Analysis";
import CheckoutAnalysis from "./CheckoutAnalysis";
import "./App.css";

const API = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000";

const TABS = [
  ["COMMAND_CENTER", "Command Center"],
  ["PAYMENTS", "Payments"],
  ["CHECKOUTS", "Checkouts"],
  ["B2B", "B2B Receivables"],
  ["MANDATES", "Mandates"],
  ["MANUAL_REVIEW", "Manual Review"],
  ["ANALYTICS", "Analytics"],
];

const money = (value) => `₹${Number(value || 0).toLocaleString("en-IN")}`;
const safe = (value, fallback = "—") => (value === null || value === undefined || value === "" ? fallback : value);

function App() {
  const [view, setView] = useState(window.location.pathname.startsWith("/analyze-checkout/") ? "CHECKOUT_ANALYSIS" : window.location.pathname.startsWith("/analyze/") ? "ANALYSIS" : "DASHBOARD");
  const [activeTab, setActiveTab] = useState("COMMAND_CENTER");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [transactions, setTransactions] = useState([]);
  const [checkouts, setCheckouts] = useState([]);
  const [receivables, setReceivables] = useState([]);
  const [mandates, setMandates] = useState([]);
  const [manualReviews, setManualReviews] = useState([]);
  const [activities, setActivities] = useState([]);
  const [overallSummary, setOverallSummary] = useState(null);
  const [receivablesSummary, setReceivablesSummary] = useState(null);
  const [analytics, setAnalytics] = useState(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [filterPriority, setFilterPriority] = useState("ALL");
  const [invoiceSearch, setInvoiceSearch] = useState("");
  const [agingFilter, setAgingFilter] = useState("ALL");
  const [batchRunning, setBatchRunning] = useState(false);
  const [batchMessage, setBatchMessage] = useState("");
  const [demoRunning, setDemoRunning] = useState(false);
  const [demoSteps, setDemoSteps] = useState([]);
  const [selectedInvoice, setSelectedInvoice] = useState(null);
  const [promiseDate, setPromiseDate] = useState("");
  const [promiseAmount, setPromiseAmount] = useState("");
  const [invoiceFeedback, setInvoiceFeedback] = useState("");
  const [manualFeedback, setManualFeedback] = useState("");
  const [mandateFeedback, setMandateFeedback] = useState("");

  const loadJson = useCallback(async (path, fallback) => {
    const response = await fetch(`${API}${path}`);
    const data = await response.json().catch(() => fallback);
    if (!response.ok) throw new Error(data?.detail || `Request failed: ${path}`);
    return data;
  }, []);

  const fetchDashboardData = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [tx, co, inv, mandatesData, man, activity, summary, invSummary, analyticsData] = await Promise.all([
        loadJson("/transactions/prioritized", []),
        loadJson("/checkout-abandonments/prioritized", []),
        loadJson("/receivables", []),
        loadJson("/mandates", []),
        loadJson("/manual-review-cases", []),
        loadJson("/recent-recovery-activity", []),
        loadJson("/overall-recovery-summary", null),
        loadJson("/receivables-summary", null),
        loadJson("/analytics", null),
      ]);
      setTransactions(Array.isArray(tx) ? tx : []);
      setCheckouts(Array.isArray(co) ? co : []);
      setReceivables(Array.isArray(inv) ? inv : []);
      setMandates(Array.isArray(mandatesData) ? mandatesData : []);
      setManualReviews(Array.isArray(man) ? man : []);
      setActivities(Array.isArray(activity) ? activity : []);
      setOverallSummary(summary || {});
      setReceivablesSummary(invSummary || {});
      setAnalytics(analyticsData || {});
    } catch (err) {
      console.error(err);
      setError(err.message || "Unable to load dashboard data.");
    } finally {
      setLoading(false);
    }
  }, [loadJson]);

  useEffect(() => {
    const handlePopState = () => {
      const path = window.location.pathname;
      setView(path.startsWith("/analyze-checkout/") ? "CHECKOUT_ANALYSIS" : path.startsWith("/analyze/") ? "ANALYSIS" : "DASHBOARD");
    };
    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
  }, []);

  useEffect(() => {
    if (view === "DASHBOARD") fetchDashboardData();
  }, [view, fetchDashboardData]);

  const navigate = (path) => {
    window.history.pushState({}, "", path);
    window.dispatchEvent(new PopStateEvent("popstate"));
  };

  const filteredTransactions = useMemo(() => transactions.filter((item) => {
    const q = searchQuery.toLowerCase().trim();
    const matchesSearch = !q || [item.transaction_id, item.payment_method, item.failure_reason].some((v) => String(v || "").toLowerCase().includes(q));
    return matchesSearch && (filterPriority === "ALL" || item.recovery_priority === filterPriority);
  }), [transactions, searchQuery, filterPriority]);

  const filteredCheckouts = useMemo(() => checkouts.filter((item) => {
    const q = searchQuery.toLowerCase().trim();
    const matchesSearch = !q || [item.checkout_id, item.customer_id, item.abandonment_stage].some((v) => String(v || "").toLowerCase().includes(q));
    return matchesSearch;
  }), [checkouts, searchQuery]);

  const filteredInvoices = useMemo(() => receivables.filter((item) => {
    const q = invoiceSearch.toLowerCase().trim();
    const matchesSearch = !q || [item.invoice_id, item.customer_name, item.customer_email].some((v) => String(v || "").toLowerCase().includes(q));
    return matchesSearch && (agingFilter === "ALL" || item.aging_bucket === agingFilter);
  }), [receivables, invoiceSearch, agingFilter]);

  const openAnalysis = (id) => navigate(`/analyze/${id}`);
  const openCheckoutAnalysis = (id) => navigate(`/analyze-checkout/${id}`);

  const runBatch = async () => {
    setBatchRunning(true);
    setBatchMessage("");
    try {
      const result = await loadJson("/run-recovery-batch", undefined);
      setBatchMessage(`${result.total_cases_recovered || 0} recovered from ${result.total_cases_processed || 0} processed cases • ${money(result.total_revenue_recovered || 0)} recovered.`);
      await fetchDashboardData();
    } catch (err) {
      setBatchMessage(err.message || "Batch recovery failed.");
    } finally {
      setBatchRunning(false);
    }
  };

  const runDemo = async () => {
  setDemoRunning(true);
  setDemoSteps([]);

  try {
    const response = await fetch(`${API}/demo/run-flow`, {
      method: "POST",
    });

    const result = await response.json();

    if (!response.ok) {
      throw new Error(result?.detail || "Demo failed");
    }

    setDemoSteps(Array.isArray(result?.steps) ? result.steps : []);
    await fetchDashboardData();
  } catch (err) {
    setDemoSteps([
      {
        step: 1,
        title: "Demo failed",
        status: "ERROR",
        summary: err.message || "Unable to run demo",
      },
    ]);
  } finally {
    setDemoRunning(false);
  }
 };

  const openInvoice = (invoice) => {
    setSelectedInvoice(invoice);
    setPromiseDate(invoice.promise_to_pay_date || "");
    setPromiseAmount(invoice.promise_to_pay_amount || invoice.invoice_amount || "");
    setInvoiceFeedback("");
  };

  const refreshInvoice = async (invoiceId) => {
    const data = await loadJson(`/receivables/${invoiceId}`, null);
    setSelectedInvoice(data);
  };

  const runInvoiceRecovery = async () => {
    if (!selectedInvoice) return;
    try {
      const response = await fetch(`${API}/receivables/${selectedInvoice.invoice_id}/run-recovery`, { method: "POST" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || data.message || "Recovery failed");
      setInvoiceFeedback(data.message || "Recovery action completed.");
      await refreshInvoice(selectedInvoice.invoice_id);
      await fetchDashboardData();
    } catch (err) {
      setInvoiceFeedback(err.message);
    }
  };

  const savePromise = async (event) => {
    event.preventDefault();
    if (!selectedInvoice || !promiseDate || !promiseAmount) return;
    try {
      const response = await fetch(`${API}/receivables/${selectedInvoice.invoice_id}/promise-to-pay`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ promise_date: promiseDate, promise_amount: Number(promiseAmount) }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || data.message || "Unable to record promise");
      setInvoiceFeedback(data.message);
      await refreshInvoice(selectedInvoice.invoice_id);
      await fetchDashboardData();
    } catch (err) {
      setInvoiceFeedback(err.message);
    }
  };

  const actOnManualReview = async (caseItem, action) => {
    try {
      const response = await fetch(`${API}/manual-review-cases/${encodeURIComponent(caseItem.case_key)}/action`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Manual review action failed");
      setManualFeedback(data.message);
      await fetchDashboardData();
    } catch (err) {
      setManualFeedback(err.message);
    }
  };

  const retryMandate = async (mandateId) => {
    setMandateFeedback("");
    try {
      const response = await fetch(`${API}/mandates/${encodeURIComponent(mandateId)}/retry`, {
        method: "POST",
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || data.message || "Mandate retry failed");
      setMandateFeedback(data.message || "Mandate retry completed.");
      await fetchDashboardData();
    } catch (err) {
      setMandateFeedback(err.message || "Mandate retry failed.");
    }
  };

  const priorityCounts = useMemo(() => ({
    HIGH: transactions.filter((x) => x.recovery_priority === "HIGH").length,
    MEDIUM: transactions.filter((x) => x.recovery_priority === "MEDIUM").length,
    LOW: transactions.filter((x) => x.recovery_priority === "LOW").length,
  }), [transactions]);

  if (view === "ANALYSIS") return <Analysis />;
  if (view === "CHECKOUT_ANALYSIS") return <CheckoutAnalysis />;

  return (
    <div className="app">
      <header className="topbar">
        <div>
          <div className="brand-row"><span className="brand-mark">RR</span><span className="eyebrow">AI REVENUE RECOVERY</span></div>
          <h1>RevenueRescue AI</h1>
          <p className="subtitle">Detect. Diagnose. Recover. — autonomous recovery across payments, checkouts and receivables.</p>
        </div>
        <div className="header-actions">
          <span className="ai-mode-pill">● AI {safe(overallSummary?.ai_mode, "READY")}</span>
          <button className="primary-button" onClick={runDemo} disabled={demoRunning}>{demoRunning ? "Running…" : "▶ Run AI Recovery Demo"}</button>
        </div>
      </header>

      <nav className="tabs">
        {TABS.map(([key, label]) => <button key={key} className={activeTab === key ? "tab active" : "tab"} onClick={() => setActiveTab(key)}>{label}</button>)}
      </nav>

      {error && <div className="alert error">{error}</div>}
      {loading ? <div className="loading-panel">Loading recovery command center…</div> : (
        <>
          <section className="cards metric-grid">
            <MetricCard label="Revenue at Risk" value={money(overallSummary?.total_revenue_at_risk)} hint="Payments + checkouts + B2B" />
            <MetricCard label="Estimated Recoverable" value={money(overallSummary?.estimated_recoverable_revenue)} hint="Based on current recovery likelihood" />
            <MetricCard label="Revenue Recovered" value={money(overallSummary?.actual_revenue_recovered)} hint="Backend-confirmed demo recoveries" />
            <MetricCard label="Recovery Rate" value={`${Number(overallSummary?.overall_recovery_rate || 0).toFixed(2)}%`} hint={`${overallSummary?.active_recovery_cases || 0} active cases`} />
          </section>

          {activeTab === "COMMAND_CENTER" && <CommandCenter {...{ priorityCounts, transactions, checkouts, receivablesSummary, manualReviews, activities, batchRunning, batchMessage, runBatch, openAnalysis, openCheckoutAnalysis, runDemo, demoRunning, demoSteps }} />}
          {activeTab === "PAYMENTS" && <PaymentsView transactions={filteredTransactions} searchQuery={searchQuery} setSearchQuery={setSearchQuery} filterPriority={filterPriority} setFilterPriority={setFilterPriority} openAnalysis={openAnalysis} />}
          {activeTab === "CHECKOUTS" && <CheckoutsView checkouts={filteredCheckouts} searchQuery={searchQuery} setSearchQuery={setSearchQuery} openCheckoutAnalysis={openCheckoutAnalysis} />}
          {activeTab === "B2B" && <B2BView invoices={filteredInvoices} search={invoiceSearch} setSearch={setInvoiceSearch} agingFilter={agingFilter} setAgingFilter={setAgingFilter} summary={receivablesSummary} openInvoice={openInvoice} />}
          {activeTab === "MANDATES" && <MandatesView mandates={mandates} feedback={mandateFeedback} onRetry={retryMandate} />}
          {activeTab === "MANUAL_REVIEW" && <ManualReviewView cases={manualReviews} feedback={manualFeedback} onAction={actOnManualReview} />}
          {activeTab === "ANALYTICS" && <AnalyticsView analytics={analytics} overallSummary={overallSummary} />}
        </>
      )}

      {selectedInvoice && <InvoiceModal invoice={selectedInvoice} promiseDate={promiseDate} setPromiseDate={setPromiseDate} promiseAmount={promiseAmount} setPromiseAmount={setPromiseAmount} feedback={invoiceFeedback} onClose={() => setSelectedInvoice(null)} onRecover={runInvoiceRecovery} onPromise={savePromise} />}
    </div>
  );
}

function MetricCard({ label, value, hint }) {
  return <div className="card metric-card"><span className="metric-label">{label}</span><strong>{value}</strong><small>{hint}</small></div>;
}

function CommandCenter({ priorityCounts, transactions, checkouts, receivablesSummary, manualReviews, activities, batchRunning, batchMessage, runBatch, openAnalysis, openCheckoutAnalysis, runDemo, demoRunning, demoSteps }) {
  return <>
    <section className="priority-cards">
      <SummaryCard title="High Priority" value={priorityCounts.HIGH} text="Immediate intervention" cls="high-card" />
      <SummaryCard title="Medium Priority" value={priorityCounts.MEDIUM} text="Monitor and recover" cls="medium-card" />
      <SummaryCard title="Low Priority" value={priorityCounts.LOW} text="Lower urgency queue" cls="low-card" />
      <SummaryCard title="Manual Review" value={manualReviews.filter((x) => x.status === "MANUAL_REVIEW").length} text="Human decision required" cls="recoverable-card" />
    </section>

    <section className="command-grid">
      <div className="panel wide-panel">
        <div className="panel-heading"><div><span className="eyebrow">AUTONOMOUS QUEUE</span><h2>AI Recovery Priority Queue</h2></div><button className="secondary-button" onClick={runBatch} disabled={batchRunning}>{batchRunning ? "Running…" : "⚡ Run Batch Recovery"}</button></div>
        <div className="mini-stats"><span>{transactions.length} payment cases</span><span>{checkouts.length} checkout cases</span><span>{receivablesSummary?.overdue_invoices_count || 0} overdue invoices</span></div>
        <div className="queue-table-wrapper"><table className="priority-table"><thead><tr><th>Case</th><th>Value</th><th>Priority</th><th>Diagnosis</th><th>Action</th></tr></thead><tbody>{transactions.map((tx) => <tr key={tx.transaction_id}><td><button className="link-button" onClick={() => openAnalysis(tx.transaction_id)}>{tx.transaction_id}</button><small>{tx.failure_reason}</small></td><td>{money(tx.amount)}</td><td><PriorityBadge value={tx.recovery_priority} score={tx.priority_score} /></td><td>{tx.failure_reason}</td><td><button className="table-button" onClick={() => openAnalysis(tx.transaction_id)}>Analyze →</button></td></tr>)}</tbody></table></div>
        {batchMessage && <div className="alert success">{batchMessage}</div>}
      </div>
      <div className="panel activity-panel"><div className="panel-heading"><div><span className="eyebrow">AUDIT STREAM</span><h2>Agent Activity</h2></div></div><ActivityTimeline activities={activities.slice(0, 12)} /></div>
    </section>

    {demoSteps.length > 0 && <section className="panel demo-panel"><div className="panel-heading"><div><span className="eyebrow">LIVE DEMO</span><h2>Agent Execution Trace</h2></div><span className="status-chip">{demoRunning ? "RUNNING" : "COMPLETE"}</span></div><div className="demo-steps">{demoSteps.map((step) => <div className="demo-step" key={`${step.step}-${step.title}`}><span>{step.step}</span><div><strong>{step.title}</strong><small>{step.status} · {step.summary}</small></div></div>)}</div></section>}
  </>;
}

function SummaryCard({ title, value, text, cls }) { return <div className={`priority-summary-card ${cls}`}><span>{title}</span><strong>{value}</strong><p>{text}</p></div>; }
function PriorityBadge({ value, score }) { return <span className={`priority-badge ${String(value || "LOW").toLowerCase()}`}>{value} · {Number(score || 0).toFixed(1)}</span>; }

function PaymentsView({ transactions, searchQuery, setSearchQuery, filterPriority, setFilterPriority, openAnalysis }) {
  return <section className="panel"><div className="panel-heading"><div><span className="eyebrow">PAYMENT FAILURE RECOVERY</span><h2>AI Priority Queue</h2></div></div><div className="search-filter-container"><input value={searchQuery} onChange={(e) => setSearchQuery(e.target.value)} placeholder="Search transaction, method or failure reason…" /><div className="filter-buttons">{["ALL", "HIGH", "MEDIUM", "LOW"].map((p) => <button key={p} className={filterPriority === p ? "filter-button active-filter" : "filter-button"} onClick={() => setFilterPriority(p)}>{p}</button>)}</div></div><div className="queue-table-wrapper"><table className="priority-table"><thead><tr><th>Transaction</th><th>Amount</th><th>Method</th><th>Failure</th><th>Priority</th><th>Attempts</th><th></th></tr></thead><tbody>{transactions.map((tx) => <tr key={tx.transaction_id}><td><button className="link-button" onClick={() => openAnalysis(tx.transaction_id)}>{tx.transaction_id}</button></td><td>{money(tx.amount)}</td><td>{tx.payment_method}</td><td>{tx.failure_reason}</td><td><PriorityBadge value={tx.recovery_priority} score={tx.priority_score} /></td><td>{tx.recovery_attempts_used}/{tx.max_recovery_attempts}</td><td><button className="table-button" onClick={() => openAnalysis(tx.transaction_id)}>Analyze</button></td></tr>)}</tbody></table></div></section>;
}

function CheckoutsView({ checkouts, searchQuery, setSearchQuery, openCheckoutAnalysis }) {
  return <section className="panel"><div className="panel-heading"><div><span className="eyebrow">ABANDONED CHECKOUT RECOVERY</span><h2>High-Intent Checkout Queue</h2></div></div><div className="search-filter-container"><input value={searchQuery} onChange={(e) => setSearchQuery(e.target.value)} placeholder="Search checkout or customer…" /></div><div className="queue-table-wrapper"><table className="priority-table"><thead><tr><th>Checkout</th><th>Cart</th><th>Stage</th><th>Probability</th><th>Priority</th><th>Consent</th><th></th></tr></thead><tbody>{checkouts.map((item) => <tr key={item.checkout_id}><td><button className="link-button" onClick={() => openCheckoutAnalysis(item.checkout_id)}>{item.checkout_id}</button><small>{item.customer_id}</small></td><td>{money(item.cart_amount)}</td><td>{item.abandonment_stage}</td><td>{item.recovery_probability}%</td><td><PriorityBadge value={item.recovery_priority} score={item.priority_score} /></td><td>{item.recovery_consent}</td><td><button className="table-button" onClick={() => openCheckoutAnalysis(item.checkout_id)}>Analyze</button></td></tr>)}</tbody></table></div></section>;
}

function B2BView({ invoices, search, setSearch, agingFilter, setAgingFilter, summary, openInvoice }) {
  const buckets = ["ALL", "Current", "1-30 Days", "31-60 Days", "61-90 Days", "90+ Days"];
  return <section><div className="b2b-summary-cards"><SummaryMetric title="Outstanding" value={money(summary?.total_outstanding_receivables)} /><SummaryMetric title="Overdue" value={money(summary?.total_overdue_amount)} /><SummaryMetric title="Overdue Invoices" value={summary?.overdue_invoices_count || 0} /><SummaryMetric title="Broken Promises" value={summary?.broken_promises_count || 0} /><SummaryMetric title="Manual Review" value={summary?.manual_review_needed_count || 0} /></div><div className="panel"><div className="panel-heading"><div><span className="eyebrow">B2B CASHFLOW</span><h2>Receivables Recovery Track</h2></div></div><div className="search-filter-container"><input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search invoice, company or email…" /><div className="filter-buttons">{buckets.map((b) => <button key={b} className={agingFilter === b ? "filter-button active-filter" : "filter-button"} onClick={() => setAgingFilter(b)}>{b}</button>)}</div></div><div className="queue-table-wrapper"><table className="priority-table"><thead><tr><th>Invoice</th><th>Customer</th><th>Amount</th><th>Aging</th><th>Status</th><th>Promise</th><th></th></tr></thead><tbody>{invoices.map((inv) => <tr key={inv.invoice_id}><td><button className="link-button" onClick={() => openInvoice(inv)}>{inv.invoice_id}</button></td><td>{inv.customer_name}<small>{inv.customer_email}</small></td><td>{money(inv.invoice_amount)}</td><td><span className="aging-pill">{inv.aging_bucket}</span><small>{inv.days_overdue} days overdue</small></td><td>{inv.payment_status}</td><td>{safe(inv.promise_status, "NONE")}</td><td><button className="table-button" onClick={() => openInvoice(inv)}>Manage</button></td></tr>)}</tbody></table></div></div></section>;
}
function SummaryMetric({ title, value }) { return <div className="b2b-card"><span>{title}</span><strong>{value}</strong></div>; }

function MandatesView({ mandates, feedback, onRetry }) {
  return <section className="panel"><div className="panel-heading"><div><span className="eyebrow">RECURRING PAYMENT RECOVERY</span><h2>Mandate Retry Sequencer</h2></div></div>{feedback && <div className="alert success">{feedback}</div>}<div className="mandate-grid">{mandates.length === 0 ? <Empty text="No derived mandate recovery cases available." /> : mandates.map((m) => <div className="mandate-card" key={m.mandate_id}><div className="mandate-head"><strong>{m.mandate_id}</strong><span className={`status-chip ${String(m.status).toLowerCase()}`}>{m.status}</span></div><p>{m.transaction_id} · {money(m.amount)}</p><div className="retry-sequence">{Array.from({ length: m.max_attempts }, (_, i) => <span key={i} className={i < m.attempts_used ? "retry-dot done" : "retry-dot"}>A{i + 1}</span>)}</div><small>Attempt {m.attempts_used} of {m.max_attempts}{m.next_retry ? ` · Next: ${m.next_retry}` : ""}</small>{m.status === "RETRY_ELIGIBLE" && m.attempts_used < m.max_attempts && <button className="secondary-button mandate-retry-button" onClick={() => onRetry(m.mandate_id)}>Retry A{m.attempts_used + 1}</button>}</div>)}</div></section>;
}

function ManualReviewView({ cases, feedback, onAction }) {
  return <section className="panel"><div className="panel-heading"><div><span className="eyebrow">HUMAN-IN-THE-LOOP</span><h2>Manual Review Center</h2></div></div>{feedback && <div className="alert success">{feedback}</div>}<div className="review-grid">{cases.length === 0 ? <Empty text="No cases currently escalated." /> : cases.map((item) => <div className="review-card" key={item.case_key}><div className="review-head"><span className="status-chip">{item.case_type}</span><PriorityBadge value={item.recovery_priority} score={item.priority_score} /></div><h3>{item.case_id}</h3><strong>{money(item.amount)} at risk</strong><p>{item.reason_for_escalation}</p><small>Attempts: {item.attempts_used}/{item.max_attempts} · Escalated {item.escalated_at}</small>{item.status === "MANUAL_REVIEW" && <div className="review-actions"><button className="primary-button" onClick={() => onAction(item, "APPROVE")}>Approve</button><button className="secondary-button" onClick={() => onAction(item, "REJECT")}>Reject</button><button className="table-button" onClick={() => onAction(item, "CLOSE")}>Close</button></div>}</div>)}</div></section>;
}

function AnalyticsView({ analytics, overallSummary }) {
  const failure = analytics?.failure_reasons || [];
  const strategies = analytics?.strategy_recovery_estimate || [];
  const aging = analytics?.aging_buckets || [];
  const maxFailure = Math.max(1, ...failure.map((x) => Number(x.value || 0)));
  const maxStrategy = Math.max(1, ...strategies.map((x) => Number(x.estimated_recoverable || 0)));
  return <section className="analytics-grid"><div className="panel chart-card"><div className="panel-heading"><div><span className="eyebrow">FAILURE MIX</span><h2>Failure Reasons</h2></div></div>{failure.map((x) => <Bar key={x.name} label={x.name} value={x.value} max={maxFailure} />)}</div><div className="panel chart-card"><div className="panel-heading"><div><span className="eyebrow">RECOVERY ECONOMICS</span><h2>Strategy Opportunity</h2></div></div>{strategies.map((x) => <Bar key={x.strategy} label={x.strategy} value={money(x.estimated_recoverable)} raw={x.estimated_recoverable} max={maxStrategy} />)}</div><div className="panel chart-card"><div className="panel-heading"><div><span className="eyebrow">B2B AGING</span><h2>Aging Buckets</h2></div></div>{aging.map((x) => <Bar key={x.name} label={x.name} value={x.value} max={Math.max(1, ...aging.map((a) => Number(a.value || 0)))} />)}</div><div className="panel analytics-summary"><span className="eyebrow">RECOVERY SNAPSHOT</span><div><strong>{money(analytics?.actual_revenue_recovered)}</strong><small>Actual recovered</small></div><div><strong>{money(analytics?.total_revenue_at_risk)}</strong><small>Total at risk</small></div><div><strong>{Number(overallSummary?.overall_recovery_rate ?? analytics?.recovery_rate ?? 0).toFixed(2)}%</strong><small>Revenue recovery rate</small></div></div></section>;
}
function Bar({ label, value, raw, max }) { return <div className="bar-row"><div><span>{label}</span><strong>{value}</strong></div><div className="bar-track"><div className="bar-fill" style={{ width: `${Math.min(100, Number(raw ?? value ?? 0) / max * 100)}%` }} /></div></div>; }

function ActivityTimeline({ activities }) {
  if (!activities.length) return <Empty text="No agent activity yet." />;
  return <div className="timeline">{activities.map((a, i) => <div className="timeline-item" key={`${a.timestamp}-${i}`}><span className="timeline-dot" /><div><strong>{a.action}</strong><p>{a.case_type} · {a.case_id}</p><small>{a.details || a.outcome || "Decision recorded"} · {a.timestamp}</small></div></div>)}</div>;
}
function Empty({ text }) { return <div className="empty-state">{text}</div>; }

function InvoiceModal({ invoice, promiseDate, setPromiseDate, promiseAmount, setPromiseAmount, feedback, onClose, onRecover, onPromise }) {
  const reasoning = invoice.reasoning || {};
  const audit = invoice.audit_trail || [];
  return <div className="modal-backdrop" onClick={onClose}><div className="modal" onClick={(e) => e.stopPropagation()}><div className="modal-head"><div><span className="eyebrow">INVOICE RECOVERY</span><h2>{invoice.invoice_id}</h2></div><button className="icon-button" onClick={onClose}>×</button></div><div className="analysis-grid"><div className="info-box"><span>Customer</span><strong>{invoice.customer_name}</strong></div><div className="info-box"><span>Amount</span><strong>{money(invoice.invoice_amount)}</strong></div><div className="info-box"><span>Aging</span><strong>{invoice.days_overdue} days</strong></div><div className="info-box"><span>Status</span><strong>{invoice.payment_status}</strong></div></div><div className="decision-box"><span className="eyebrow">AGENT DECISION</span><h3>{reasoning.recommended_action}</h3><p>{reasoning.diagnosis}</p><small>{reasoning.reason}</small></div><div className="modal-actions"><button className="primary-button" onClick={onRecover}>Run AI Recovery</button></div><form className="promise-form" onSubmit={onPromise}><h3>Promise-to-Pay</h3><div className="form-row"><input type="date" value={promiseDate || ""} onChange={(e) => setPromiseDate(e.target.value)} required /><input type="number" min="1" value={promiseAmount || ""} onChange={(e) => setPromiseAmount(e.target.value)} placeholder="Promise amount" required /><button className="secondary-button">Record</button></div></form>{feedback && <div className="alert success">{feedback}</div>}<div className="modal-audit"><h3>Backend Audit Trail</h3>{audit.length ? audit.slice(-8).reverse().map((event, i) => <div className="audit-row" key={`${event.timestamp}-${i}`}><strong>{event.action}</strong><span>{event.decision}</span><small>{event.reason || ""} · {event.timestamp}</small></div>) : <Empty text="No audit events yet." />}</div></div></div>;
}

export default App;

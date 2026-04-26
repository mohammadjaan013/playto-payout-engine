import React, { useState, useEffect, useCallback, useRef } from 'react';
import { api } from './api';
import { formatINR, formatDateTime, generateUUID } from './utils';

// ─── Status badge ──────────────────────────────────────────────────────────────
const STATUS_STYLES = {
  pending:    'bg-yellow-100 text-yellow-800',
  processing: 'bg-blue-100 text-blue-800',
  completed:  'bg-green-100 text-green-800',
  failed:     'bg-red-100 text-red-800',
};

function StatusBadge({ status }) {
  return (
    <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold ${STATUS_STYLES[status] || 'bg-gray-100 text-gray-700'}`}>
      {status}
    </span>
  );
}

// ─── Balance card ──────────────────────────────────────────────────────────────
function BalanceCard({ label, amount, sub }) {
  return (
    <div className="bg-white rounded-xl border border-gray-200 p-5 flex flex-col gap-1">
      <span className="text-xs font-medium text-gray-500 uppercase tracking-wide">{label}</span>
      <span className="text-2xl font-bold text-gray-900">{formatINR(amount)}</span>
      {sub && <span className="text-xs text-gray-400">{sub}</span>}
    </div>
  );
}

// ─── Payout form ───────────────────────────────────────────────────────────────
function PayoutForm({ merchant, onSuccess }) {
  const [amountINR, setAmountINR] = useState('');
  const [bankAccountId, setBankAccountId] = useState(
    merchant.bank_accounts?.[0]?.id || ''
  );
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null); // { success, message }

  async function handleSubmit(e) {
    e.preventDefault();
    setResult(null);

    const amount = parseFloat(amountINR);
    if (isNaN(amount) || amount <= 0) {
      setResult({ success: false, message: 'Enter a valid amount.' });
      return;
    }

    const amountPaise = Math.round(amount * 100);
    const idempotencyKey = generateUUID();

    setLoading(true);
    try {
      const { status, data } = await api.createPayout(
        merchant.id,
        { amount_paise: amountPaise, bank_account_id: bankAccountId },
        idempotencyKey
      );

      if (status === 201) {
        setResult({ success: true, message: `Payout of ${formatINR(amountPaise)} created. ID: ${data.payout.id}` });
        setAmountINR('');
        onSuccess?.();
      } else {
        const msg = data.error || JSON.stringify(data);
        setResult({ success: false, message: msg });
      }
    } catch (err) {
      setResult({ success: false, message: 'Network error. Please try again.' });
    } finally {
      setLoading(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="bg-white rounded-xl border border-gray-200 p-5 space-y-4">
      <h2 className="text-base font-semibold text-gray-800">Request Payout</h2>

      <div className="space-y-3">
        <div>
          <label className="block text-sm text-gray-600 mb-1">Amount (INR)</label>
          <div className="relative">
            <span className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400 text-sm">₹</span>
            <input
              type="number"
              step="0.01"
              min="1"
              value={amountINR}
              onChange={e => setAmountINR(e.target.value)}
              placeholder="500.00"
              className="w-full pl-7 pr-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              required
            />
          </div>
        </div>

        <div>
          <label className="block text-sm text-gray-600 mb-1">Bank Account</label>
          <select
            value={bankAccountId}
            onChange={e => setBankAccountId(e.target.value)}
            className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            required
          >
            {merchant.bank_accounts?.map(acc => (
              <option key={acc.id} value={acc.id}>
                {acc.account_holder_name} — ···{acc.account_number.slice(-4)} ({acc.ifsc_code})
                {acc.is_primary ? ' [Primary]' : ''}
              </option>
            ))}
          </select>
        </div>
      </div>

      {result && (
        <div className={`text-sm rounded-lg px-3 py-2 ${result.success ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'}`}>
          {result.message}
        </div>
      )}

      <button
        type="submit"
        disabled={loading}
        className="w-full bg-blue-600 hover:bg-blue-700 disabled:bg-blue-300 text-white text-sm font-medium py-2 px-4 rounded-lg transition-colors"
      >
        {loading ? 'Submitting…' : 'Request Payout'}
      </button>
    </form>
  );
}

// ─── Payout table ──────────────────────────────────────────────────────────────
function PayoutTable({ payouts, loading }) {
  if (loading) return <div className="text-sm text-gray-500 py-4 text-center">Loading payouts…</div>;
  if (!payouts.length) return <div className="text-sm text-gray-500 py-4 text-center">No payouts yet.</div>;

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-gray-200">
            <th className="text-left py-2 pr-4 font-medium text-gray-500">ID</th>
            <th className="text-left py-2 pr-4 font-medium text-gray-500">Amount</th>
            <th className="text-left py-2 pr-4 font-medium text-gray-500">Status</th>
            <th className="text-left py-2 pr-4 font-medium text-gray-500">Created</th>
            <th className="text-left py-2 font-medium text-gray-500">Updated</th>
          </tr>
        </thead>
        <tbody>
          {payouts.map(p => (
            <tr key={p.id} className="border-b border-gray-100 hover:bg-gray-50">
              <td className="py-2 pr-4 font-mono text-xs text-gray-400 truncate max-w-[100px]">
                {p.id.slice(0, 8)}…
              </td>
              <td className="py-2 pr-4 font-medium">{formatINR(p.amount_paise)}</td>
              <td className="py-2 pr-4"><StatusBadge status={p.status} /></td>
              <td className="py-2 pr-4 text-gray-500">{formatDateTime(p.created_at)}</td>
              <td className="py-2 text-gray-500">{formatDateTime(p.updated_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ─── Ledger table ──────────────────────────────────────────────────────────────
function LedgerTable({ entries, loading }) {
  if (loading) return <div className="text-sm text-gray-500 py-4 text-center">Loading…</div>;
  if (!entries.length) return <div className="text-sm text-gray-500 py-4 text-center">No ledger entries.</div>;

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-gray-200">
            <th className="text-left py-2 pr-4 font-medium text-gray-500">Type</th>
            <th className="text-left py-2 pr-4 font-medium text-gray-500">Amount</th>
            <th className="text-left py-2 pr-4 font-medium text-gray-500">Description</th>
            <th className="text-left py-2 font-medium text-gray-500">Date</th>
          </tr>
        </thead>
        <tbody>
          {entries.map(e => (
            <tr key={e.id} className="border-b border-gray-100 hover:bg-gray-50">
              <td className="py-2 pr-4">
                <span className={`inline-flex items-center gap-1 text-xs font-semibold ${e.entry_type === 'credit' ? 'text-green-600' : 'text-red-600'}`}>
                  {e.entry_type === 'credit' ? '↑' : '↓'} {e.entry_type}
                </span>
              </td>
              <td className={`py-2 pr-4 font-medium ${e.entry_type === 'credit' ? 'text-green-700' : 'text-red-700'}`}>
                {e.entry_type === 'credit' ? '+' : ''}{formatINR(Math.abs(e.amount_paise))}
              </td>
              <td className="py-2 pr-4 text-gray-600 max-w-xs truncate">{e.description}</td>
              <td className="py-2 text-gray-500">{formatDateTime(e.created_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ─── Merchant dashboard ────────────────────────────────────────────────────────
function MerchantDashboard({ merchantId }) {
  const [dashboard, setDashboard] = useState(null);
  const [payouts, setPayouts] = useState([]);
  const [ledger, setLedger] = useState([]);
  const [loadingDash, setLoadingDash] = useState(true);
  const [loadingPayouts, setLoadingPayouts] = useState(true);
  const [loadingLedger, setLoadingLedger] = useState(true);
  const [activeTab, setActiveTab] = useState('payouts');
  const pollRef = useRef(null);

  const fetchDashboard = useCallback(async () => {
    const { ok, data } = await api.getDashboard(merchantId);
    if (ok) setDashboard(data);
    setLoadingDash(false);
  }, [merchantId]);

  const fetchPayouts = useCallback(async () => {
    const { ok, data } = await api.getPayouts(merchantId);
    if (ok) setPayouts(data);
    setLoadingPayouts(false);
  }, [merchantId]);

  const fetchLedger = useCallback(async () => {
    const { ok, data } = await api.getLedger(merchantId);
    if (ok) setLedger(data);
    setLoadingLedger(false);
  }, [merchantId]);

  const refreshAll = useCallback(() => {
    fetchDashboard();
    fetchPayouts();
    fetchLedger();
  }, [fetchDashboard, fetchPayouts, fetchLedger]);

  useEffect(() => {
    refreshAll();
    // Poll every 4 seconds for live status updates on in-flight payouts
    pollRef.current = setInterval(refreshAll, 4000);
    return () => clearInterval(pollRef.current);
  }, [refreshAll]);

  if (loadingDash) return <div className="text-sm text-gray-500 py-8 text-center">Loading dashboard…</div>;
  if (!dashboard) return <div className="text-sm text-red-500 py-8 text-center">Failed to load merchant data.</div>;

  const availablePaise = dashboard.available_balance_paise - dashboard.held_balance_paise;

  return (
    <div className="space-y-6">
      {/* Balance cards */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <BalanceCard
          label="Available Balance"
          amount={availablePaise}
          sub="Ready to withdraw"
        />
        <BalanceCard
          label="Held Balance"
          amount={dashboard.held_balance_paise}
          sub="Pending or processing payouts"
        />
        <BalanceCard
          label="Total Balance"
          amount={dashboard.available_balance_paise}
          sub="Credits minus completed debits"
        />
      </div>

      {/* Payout form */}
      <PayoutForm
        merchant={dashboard}
        onSuccess={() => setTimeout(refreshAll, 500)}
      />

      {/* Tabs: Payouts / Ledger */}
      <div className="bg-white rounded-xl border border-gray-200">
        <div className="flex border-b border-gray-200">
          {['payouts', 'ledger'].map(tab => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`px-5 py-3 text-sm font-medium capitalize transition-colors ${
                activeTab === tab
                  ? 'border-b-2 border-blue-600 text-blue-600'
                  : 'text-gray-500 hover:text-gray-700'
              }`}
            >
              {tab === 'payouts' ? `Payouts (${payouts.length})` : `Ledger (${ledger.length})`}
            </button>
          ))}
        </div>
        <div className="p-5">
          {activeTab === 'payouts' && (
            <PayoutTable payouts={payouts} loading={loadingPayouts} />
          )}
          {activeTab === 'ledger' && (
            <LedgerTable entries={ledger} loading={loadingLedger} />
          )}
        </div>
      </div>
    </div>
  );
}

// ─── Root App ──────────────────────────────────────────────────────────────────
export default function App() {
  const [merchants, setMerchants] = useState([]);
  const [selectedMerchant, setSelectedMerchant] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.getMerchants().then(({ ok, data }) => {
      if (ok && data.length > 0) {
        setMerchants(data);
        setSelectedMerchant(data[0].id);
      }
      setLoading(false);
    });
  }, []);

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Header */}
      <header className="bg-white border-b border-gray-200 px-6 py-4 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 bg-blue-600 rounded-lg flex items-center justify-center">
            <span className="text-white font-bold text-sm">P</span>
          </div>
          <div>
            <h1 className="text-base font-semibold text-gray-900">Playto Pay</h1>
            <p className="text-xs text-gray-400">Payout Engine</p>
          </div>
        </div>

        {/* Merchant selector */}
        {merchants.length > 0 && (
          <select
            value={selectedMerchant || ''}
            onChange={e => setSelectedMerchant(e.target.value)}
            className="text-sm border border-gray-300 rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            {merchants.map(m => (
              <option key={m.id} value={m.id}>{m.name}</option>
            ))}
          </select>
        )}
      </header>

      {/* Main content */}
      <main className="max-w-4xl mx-auto px-4 py-8">
        {loading ? (
          <div className="text-sm text-gray-500 text-center py-16">Loading merchants…</div>
        ) : !selectedMerchant ? (
          <div className="text-sm text-red-500 text-center py-16">
            No merchants found. Run the seed script: <code className="font-mono bg-red-50 px-1">python seed.py</code>
          </div>
        ) : (
          <MerchantDashboard key={selectedMerchant} merchantId={selectedMerchant} />
        )}
      </main>
    </div>
  );
}

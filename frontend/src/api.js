const API_BASE = process.env.REACT_APP_API_URL || '/api/v1';

async function apiFetch(path, options = {}) {
  const { headers: extraHeaders, ...restOptions } = options;
  const url = `${API_BASE}${path}`;
  const res = await fetch(url, {
    headers: {
      'Content-Type': 'application/json',
      ...extraHeaders,
    },
    ...restOptions,
  });
  const data = await res.json();
  return { status: res.status, ok: res.ok, data };
}

export const api = {
  getMerchants: () => apiFetch('/merchants/'),
  
  getDashboard: (merchantId) => apiFetch(`/merchants/${merchantId}/`),
  
  getLedger: (merchantId) => apiFetch(`/merchants/${merchantId}/ledger/`),
  
  getPayouts: (merchantId) => apiFetch(`/merchants/${merchantId}/payouts/`),
  
  getPayout: (merchantId, payoutId) =>
    apiFetch(`/merchants/${merchantId}/payouts/${payoutId}/`),
  
  createPayout: (merchantId, payload, idempotencyKey) =>
    apiFetch(`/merchants/${merchantId}/payouts/create/`, {
      method: 'POST',
      headers: { 'Idempotency-Key': idempotencyKey },
      body: JSON.stringify(payload),
    }),
};

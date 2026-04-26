/**
 * Converts paise (integer) to a human-readable INR string.
 * e.g. 250000 → "₹2,500.00"
 */
export function formatINR(paise) {
  const rupees = paise / 100;
  return new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency: 'INR',
    minimumFractionDigits: 2,
  }).format(rupees);
}

export function formatDateTime(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleString('en-IN', {
    dateStyle: 'medium',
    timeStyle: 'short',
  });
}

export function generateUUID() {
  return crypto.randomUUID();
}

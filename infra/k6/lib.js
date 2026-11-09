// Shared helpers for the k6 scenarios. Run through `make loadtest`, which starts the gateway
// with load-test settings (rate limits lifted, mock LLM + mock Jev) and collects the summaries.
import http from 'k6/http';

export const BASE = __ENV.GATEWAY_URL || 'http://127.0.0.1:8080';

export function login(userId) {
  const r = http.post(`${BASE}/v1/auth/login`, JSON.stringify({ user_id: userId }), {
    headers: { 'content-type': 'application/json' },
  });
  if (r.status !== 200) throw new Error(`login ${userId}: ${r.status}`);
  return r.json('token');
}

export function auth(token) {
  return { headers: { 'content-type': 'application/json', authorization: `Bearer ${token}` } };
}

// The chat endpoint streams SSE; k6 reads the whole body. The last `done` event carries the status.
export function chatStatus(body) {
  const m = /event: done\ndata: (\{.*\})/.exec(body || '');
  return m ? JSON.parse(m[1]).status : 'none';
}

export const QUESTIONS = [
  'What is the hotel cap per night in tier-1 cities?',
  'How many quotes are needed above USD 25,000?',
  'What is the replacement cycle for a standard laptop?',
  'Who approves purchase requests above USD 25,000?',
  'What are the payment terms in the standard services contract?',
  'Can I use a corporate card for software subscriptions?',
  'What is the per diem for meals on international travel?',
  'How long does vendor onboarding take?',
  'What documents are required to add a new vendor?',
  'When must suspected procurement fraud be reported?',
];

export const SUMMARY_STATS = ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'];

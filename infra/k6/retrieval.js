// Retrieval path only (embed + BM25 + hybrid Qdrant query with the ACL filter), no Jev and no LLM,
// through the admin eval endpoint (uncached by design). Target: p95 < 100 ms at 100 VUs.
import http from 'k6/http';
import { check } from 'k6';
import { BASE, auth, login, QUESTIONS, SUMMARY_STATS } from './lib.js';

export const options = {
  summaryTrendStats: SUMMARY_STATS,
  scenarios: {
    retrieve: { executor: 'constant-vus', vus: Number(__ENV.VUS || 100), duration: __ENV.DURATION || '60s' },
  },
  thresholds: { checks: ['rate>0.99'] },
};

export function setup() {
  return { token: login('olivia.grant') };
}

export default function (data) {
  // Vary the text so the embedding cache never answers (each request embeds for real).
  const q = `${QUESTIONS[(__VU + __ITER) % QUESTIONS.length]} (${__VU}-${__ITER})`;
  const r = http.post(
    `${BASE}/admin/eval/retrieve`,
    JSON.stringify({ query: q, as_user: 'priya.sharma' }),
    auth(data.token),
  );
  check(r, { '20 candidates': (x) => x.status === 200 && x.json('candidates').length > 0 });
}

// Full Q&A path with caches off: guard, classifier, mock Jev route + gate, OPA, embed, hybrid
// retrieval, mock LLM streaming, output guard. Measures the gateway, not model latency.
import http from 'k6/http';
import { check } from 'k6';
import { BASE, auth, chatStatus, login, QUESTIONS, SUMMARY_STATS } from './lib.js';

export const options = {
  summaryTrendStats: SUMMARY_STATS,
  scenarios: {
    e2e: { executor: 'constant-vus', vus: Number(__ENV.VUS || 32), duration: __ENV.DURATION || '60s' },
  },
  thresholds: { checks: ['rate>0.95'] },
};

const PERSONAS = ['priya.sharma', 'marcus.lee', 'sofia.alvarez', 'daniel.okafor', 'noah.williams'];

export function setup() {
  return { tokens: PERSONAS.map(login) };
}

export default function (data) {
  const token = data.tokens[__VU % data.tokens.length];
  const q = QUESTIONS[(__VU * 7 + __ITER) % QUESTIONS.length];
  const r = http.post(`${BASE}/v1/chat`, JSON.stringify({ message: q }), auth(token));
  // `no_answer` is a valid outcome for some persona/question pairs (ACL); errors are not.
  check(r, { 'completed': (x) => x.status === 200 && ['answered', 'no_answer'].includes(chatStatus(x.body)) });
}

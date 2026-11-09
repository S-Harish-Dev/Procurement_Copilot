// Gateway overhead on an exact answer-cache hit: auth, rate limit, guard (incl. local classifier),
// router (route cache), OPA, L1 lookup, SSE. Target: 1-5k RPS on the build machine.
import http from 'k6/http';
import { check } from 'k6';
import { BASE, auth, chatStatus, login, SUMMARY_STATS } from './lib.js';

export const options = {
  summaryTrendStats: SUMMARY_STATS,
  scenarios: {
    hits: { executor: 'constant-vus', vus: Number(__ENV.VUS || 64), duration: __ENV.DURATION || '60s' },
  },
  thresholds: { checks: ['rate>0.99'] },
};

const Q = 'What is the hotel cap per night in tier-1 cities?';

export function setup() {
  const token = login('priya.sharma');
  // Warm the cache once; every iteration afterwards is an L1 hit.
  const r = http.post(`${BASE}/v1/chat`, JSON.stringify({ message: Q }), auth(token));
  if (chatStatus(r.body) !== 'answered') throw new Error(`warm-up not answered: ${r.body}`);
  return { token };
}

export default function (data) {
  const r = http.post(`${BASE}/v1/chat`, JSON.stringify({ message: Q }), auth(data.token));
  check(r, { 'answered from cache': (x) => x.status === 200 && chatStatus(x.body) === 'answered' });
}

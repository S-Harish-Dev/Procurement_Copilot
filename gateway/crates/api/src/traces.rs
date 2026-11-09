//! In-memory ring of the last chat requests for the admin trace view (`/insights/traces`).
//!
//! Tempo holds the full OpenTelemetry trace when the observability stack runs; this ring makes the
//! waterfall available without it (the native stack has no Tempo) and costs one bounded VecDeque.
//! It holds stage timings and admin diagnostics only, never question or answer text.

use std::collections::VecDeque;
use std::sync::Mutex;

use serde::Serialize;

const CAPACITY: usize = 1_000;

#[derive(Clone, Debug, Serialize)]
pub struct Span {
    pub name: &'static str,
    pub start_ms: f64,
    pub duration_ms: f64,
}

#[derive(Clone, Serialize)]
pub struct TraceRecord {
    pub request_id: String,
    pub trace_id: Option<String>,
    #[serde(with = "time::serde::rfc3339")]
    pub at: time::OffsetDateTime,
    pub role: String,
    pub status: String,
    pub reason: Option<String>,
    pub total_ms: f64,
    pub cache_layer: Option<&'static str>,
    pub tier: Option<&'static str>,
    pub intent: Option<String>,
    #[serde(skip)]
    pub spans: Vec<Span>,
    #[serde(skip)]
    pub diagnostics: serde_json::Value,
}

#[derive(Default)]
pub struct TraceRing {
    items: Mutex<VecDeque<TraceRecord>>,
}

impl TraceRing {
    pub fn push(&self, record: TraceRecord) {
        let mut q = self.items.lock().expect("trace ring lock");
        if q.len() == CAPACITY {
            q.pop_back();
        }
        q.push_front(record);
    }

    /// Newest first.
    pub fn recent(&self, limit: usize) -> Vec<TraceRecord> {
        let q = self.items.lock().expect("trace ring lock");
        q.iter().take(limit).cloned().collect()
    }

    /// By request id or OpenTelemetry trace id.
    pub fn find(&self, id: &str) -> Option<TraceRecord> {
        let q = self.items.lock().expect("trace ring lock");
        q.iter()
            .find(|r| r.request_id == id || r.trace_id.as_deref() == Some(id))
            .cloned()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn rec(id: &str) -> TraceRecord {
        TraceRecord {
            request_id: id.into(),
            trace_id: Some(format!("t-{id}")),
            at: time::OffsetDateTime::UNIX_EPOCH,
            role: "employee".into(),
            status: "answered".into(),
            reason: None,
            total_ms: 1.0,
            cache_layer: None,
            tier: None,
            intent: None,
            spans: vec![],
            diagnostics: serde_json::Value::Null,
        }
    }

    #[test]
    fn ring_is_bounded_newest_first_and_searchable() {
        let ring = TraceRing::default();
        for i in 0..CAPACITY + 5 {
            ring.push(rec(&i.to_string()));
        }
        let recent = ring.recent(3);
        assert_eq!(recent[0].request_id, (CAPACITY + 4).to_string());
        assert_eq!(ring.recent(usize::MAX).len(), CAPACITY);
        assert!(ring.find("0").is_none(), "oldest entries are evicted");
        assert!(ring.find("t-10").is_some(), "trace ids resolve too");
    }
}

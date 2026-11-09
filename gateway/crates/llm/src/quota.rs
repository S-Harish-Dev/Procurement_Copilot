//! Free-tier quota tracker (requests/minute, requests/day, tokens/minute) per provider.
//!
//! Fixed windows: simple and cheap, slightly permissive at window edges. The point is to route
//! around a provider *before* it answers 429, not to be exact — a 429 still triggers fallback.

use std::sync::Mutex;
use std::time::{SystemTime, UNIX_EPOCH};

use crate::config::Limits;

#[derive(Debug, Default)]
struct Window {
    id: u64,
    count: u64,
}

impl Window {
    fn roll(&mut self, id: u64) {
        if self.id != id {
            self.id = id;
            self.count = 0;
        }
    }
}

#[derive(Debug, Default)]
struct Inner {
    minute: Window,
    day: Window,
    tokens_minute: Window,
}

#[derive(Debug)]
pub struct Quota {
    limits: Limits,
    inner: Mutex<Inner>,
}

fn now_secs() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

impl Quota {
    pub fn new(limits: Limits) -> Self {
        Self {
            limits,
            inner: Mutex::new(Inner::default()),
        }
    }

    /// Reserve one request if every configured limit has room.
    pub fn try_acquire(&self) -> bool {
        self.try_acquire_at(now_secs())
    }

    fn try_acquire_at(&self, now: u64) -> bool {
        let mut s = self.inner.lock().expect("quota lock");
        s.minute.roll(now / 60);
        s.day.roll(now / 86_400);
        s.tokens_minute.roll(now / 60);
        let over = |limit: u32, used: u64| limit > 0 && used >= u64::from(limit);
        if over(self.limits.rpm, s.minute.count)
            || over(self.limits.rpd, s.day.count)
            || over(self.limits.tpm, s.tokens_minute.count)
        {
            return false;
        }
        s.minute.count += 1;
        s.day.count += 1;
        true
    }

    pub fn record_tokens(&self, tokens: u64) {
        let mut s = self.inner.lock().expect("quota lock");
        s.tokens_minute.roll(now_secs() / 60);
        s.tokens_minute.count += tokens;
    }

    /// Provider said 429: treat the minute as used up so the next requests route elsewhere.
    pub fn exhaust_minute(&self) {
        let mut s = self.inner.lock().expect("quota lock");
        s.minute.roll(now_secs() / 60);
        s.minute.count = u64::MAX / 2;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rpm_limit_resets_next_minute() {
        let q = Quota::new(Limits {
            rpm: 2,
            rpd: 0,
            tpm: 0,
        });
        assert!(q.try_acquire_at(120));
        assert!(q.try_acquire_at(121));
        assert!(!q.try_acquire_at(122));
        assert!(q.try_acquire_at(180), "new minute window");
    }

    #[test]
    fn rpd_limit_holds_across_minutes() {
        let q = Quota::new(Limits {
            rpm: 0,
            rpd: 2,
            tpm: 0,
        });
        assert!(q.try_acquire_at(0));
        assert!(q.try_acquire_at(61));
        assert!(!q.try_acquire_at(3_600));
        assert!(q.try_acquire_at(86_400));
    }

    #[test]
    fn unlimited_when_zero() {
        let q = Quota::new(Limits::default());
        assert!((0..1000).all(|i| q.try_acquire_at(i)));
    }
}

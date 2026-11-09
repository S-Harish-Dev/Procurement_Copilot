//! Circuit breaker shared by the LLM router (per provider) and the Jev client: Closed -> (N consecutive failures) -> Open -> (cooldown) -> HalfOpen.
//! In HalfOpen exactly one trial request is let through; success closes, failure re-opens.

use std::sync::Mutex;
use std::time::{Duration, Instant};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum State {
    Closed,
    Open,
    HalfOpen,
}

#[derive(Debug)]
struct Inner {
    state: State,
    consecutive_failures: u32,
    opened_at: Option<Instant>,
    trial_in_flight: bool,
}

#[derive(Debug)]
pub struct Breaker {
    threshold: u32,
    cooldown: Duration,
    inner: Mutex<Inner>,
}

impl Breaker {
    pub fn new(threshold: u32, cooldown: Duration) -> Self {
        Self {
            threshold: threshold.max(1),
            cooldown,
            inner: Mutex::new(Inner {
                state: State::Closed,
                consecutive_failures: 0,
                opened_at: None,
                trial_in_flight: false,
            }),
        }
    }

    /// May a request go through now? Transitions Open -> HalfOpen after the cooldown.
    pub fn allow(&self) -> bool {
        let mut s = self.inner.lock().expect("breaker lock");
        match s.state {
            State::Closed => true,
            State::Open => {
                if s.opened_at.is_some_and(|t| t.elapsed() >= self.cooldown) {
                    s.state = State::HalfOpen;
                    s.trial_in_flight = true;
                    true
                } else {
                    false
                }
            }
            State::HalfOpen => {
                if s.trial_in_flight {
                    false
                } else {
                    s.trial_in_flight = true;
                    true
                }
            }
        }
    }

    pub fn on_success(&self) {
        let mut s = self.inner.lock().expect("breaker lock");
        s.state = State::Closed;
        s.consecutive_failures = 0;
        s.opened_at = None;
        s.trial_in_flight = false;
    }

    pub fn on_failure(&self) {
        let mut s = self.inner.lock().expect("breaker lock");
        s.consecutive_failures += 1;
        s.trial_in_flight = false;
        if s.state == State::HalfOpen || s.consecutive_failures >= self.threshold {
            s.state = State::Open;
            s.opened_at = Some(Instant::now());
        }
    }

    /// Open immediately (e.g. 401: a bad key will not fix itself on retry).
    pub fn trip(&self) {
        let mut s = self.inner.lock().expect("breaker lock");
        s.state = State::Open;
        s.opened_at = Some(Instant::now());
        s.trial_in_flight = false;
    }

    pub fn state(&self) -> State {
        self.inner.lock().expect("breaker lock").state
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn opens_after_threshold_and_recovers_via_half_open() {
        let b = Breaker::new(2, Duration::from_millis(20));
        assert!(b.allow());
        b.on_failure();
        assert_eq!(b.state(), State::Closed);
        b.on_failure();
        assert_eq!(b.state(), State::Open);
        assert!(!b.allow());
        std::thread::sleep(Duration::from_millis(25));
        assert!(
            b.allow(),
            "first call after cooldown is the half-open trial"
        );
        assert!(!b.allow(), "only one trial at a time");
        b.on_success();
        assert_eq!(b.state(), State::Closed);
    }

    #[test]
    fn failed_trial_reopens() {
        let b = Breaker::new(1, Duration::from_millis(10));
        b.on_failure();
        std::thread::sleep(Duration::from_millis(15));
        assert!(b.allow());
        b.on_failure();
        assert_eq!(b.state(), State::Open);
        assert!(!b.allow());
    }
}

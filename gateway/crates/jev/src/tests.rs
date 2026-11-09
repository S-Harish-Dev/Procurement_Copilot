use std::time::Duration;

use serde_json::json;
use wiremock::matchers::{body_partial_json, header, method, path};
use wiremock::{Mock, MockServer, ResponseTemplate};

use super::*;

const ROUTER: &str = include_str!("../../../../prompts/router_questions_v1.json");
const GATE: &str = include_str!("../../../../prompts/gate_questions_v1.json");

fn client(uri: &str, budget_usd: f64, spent_micros: u64) -> JevClient {
    let cfg = JevConfig {
        base_url: uri.into(),
        api_key: "k".into(),
        model: "jev-latest".into(),
        timeout: Duration::from_millis(300),
        price_in_per_m: 0.042,
        budget_usd,
    };
    JevClient::new(
        cfg,
        QuestionSets::from_json(ROUTER, GATE).unwrap(),
        spent_micros,
    )
}

#[tokio::test]
async fn route_parses_typed_answers() {
    let s = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path("/v1/systemone"))
        .and(header("authorization", "Bearer k"))
        .and(body_partial_json(json!({"model": "jev-latest", "state": {"query": "q"}})))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({
            "model": "jev-1.14.0",
            "answers": {
                "intent": {"type": "choice", "choice": "approve", "confidence": 0.82,
                           "probabilities": {"approve": 0.82, "policy_qa": 0.1, "data_lookup": 0.05, "create_request": 0.02, "out_of_scope": 0.01}},
                "complexity": {"type": "choice", "choice": "cheap", "confidence": 0.7, "probabilities": {"cheap": 0.7, "expensive": 0.3}},
                "prompt_injection": {"type": "noul", "noul": 0.03}
            },
            "usage": {"input_tokens": 1000, "output_tokens": 5}
        })))
        .mount(&s)
        .await;
    let c = client(&s.uri(), 5.0, 0);
    let d = c.route("q", &[]).await.unwrap();
    assert_eq!(d.intent, Intent::Approve);
    assert!(d.intent.is_action());
    assert!((d.p_expensive - 0.3).abs() < 1e-9 && (d.p_injection - 0.03).abs() < 1e-9);
    assert_eq!(d.model, "jev-1.14.0");
    assert_eq!(
        c.budget.spent_micros(),
        42,
        "1000 tokens * $0.042/M = $0.000042"
    );
}

#[tokio::test]
async fn gate_sends_one_batched_request_with_two_questions_per_passage() {
    let s = MockServer::start().await;
    Mock::given(method("POST"))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({
            "model": "m",
            "answers": {"rel_0": {"type": "noul", "noul": 0.9}, "inj_0": {"type": "noul", "noul": 0.01},
                        "rel_1": {"type": "noul", "noul": 0.2}, "inj_1": {"type": "noul", "noul": 0.97}},
            "usage": {"input_tokens": 10, "output_tokens": 0}
        })))
        .expect(1)
        .mount(&s)
        .await;
    let c = client(&s.uri(), 5.0, 0);
    let scores = c.gate("q", &["p0".into(), "p1".into()]).await.unwrap();
    assert_eq!(select(&scores, 0.5, 0.8, 8), vec![0]);
    let sent = &s.received_requests().await.unwrap()[0];
    let body: Value = serde_json::from_slice(&sent.body).unwrap();
    assert_eq!(body["questions"].as_object().unwrap().len(), 4);
    assert_eq!(body["questions"]["rel_1"]["instructions"]["passage"], "p1");
}

#[test]
fn select_drops_injection_and_caps_best_first() {
    let s = GateScores {
        relevance: vec![0.6, 0.95, 0.7, 0.99, 0.51],
        injection: vec![0.0, 0.0, 0.0, 0.9, 0.0],
    };
    assert_eq!(select(&s, 0.5, 0.8, 3), vec![1, 2, 0]); // 3 dropped as injected
    assert!(
        select(&s, 0.999, 0.8, 8).is_empty(),
        "nothing relevant -> abstain"
    );
}

#[tokio::test]
async fn overload_and_timeouts_are_unavailable_and_open_the_breaker() {
    let s = MockServer::start().await;
    Mock::given(method("POST"))
        .respond_with(ResponseTemplate::new(529))
        .mount(&s)
        .await;
    let c = client(&s.uri(), 5.0, 0);
    for _ in 0..3 {
        assert!(matches!(
            c.route("q", &[]).await,
            Err(JevError::Unavailable("overloaded"))
        ));
    }
    assert!(matches!(
        c.route("q", &[]).await,
        Err(JevError::Unavailable("breaker_open"))
    ));
    assert!(!c.available());

    let slow = MockServer::start().await;
    Mock::given(method("POST"))
        .respond_with(ResponseTemplate::new(200).set_delay(Duration::from_secs(2)))
        .mount(&slow)
        .await;
    let c = client(&slow.uri(), 5.0, 0);
    assert!(matches!(
        c.route("q", &[]).await,
        Err(JevError::Unavailable("timeout"))
    ));
}

#[tokio::test]
async fn exhausted_budget_refuses_without_calling() {
    let s = MockServer::start().await;
    Mock::given(method("POST"))
        .respond_with(ResponseTemplate::new(200))
        .expect(0)
        .mount(&s)
        .await;
    let c = client(&s.uri(), 1.0, 960_000); // 96% of $1 spent
    assert!(matches!(
        c.route("q", &[]).await,
        Err(JevError::Unavailable("budget"))
    ));
    assert_eq!(BudgetMeter::new(1.0, 850_000).status(), BudgetStatus::Warn);
}

#[tokio::test]
async fn bad_request_is_a_bug_not_an_outage() {
    let s = MockServer::start().await;
    Mock::given(method("POST"))
        .respond_with(ResponseTemplate::new(422).set_body_string("{\"detail\":[]}"))
        .mount(&s)
        .await;
    let c = client(&s.uri(), 5.0, 0);
    assert!(matches!(
        c.route("q", &[]).await,
        Err(JevError::BadRequest { status: 422, .. })
    ));
    assert!(c.available(), "a 422 must not open the breaker");
}

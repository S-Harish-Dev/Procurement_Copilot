//! gRPC client for the brain (proto/brain.proto). The channel connects lazily, so the gateway starts
//! (and Q&A keeps working) while the brain is down — action intents then get "temporarily
//! unavailable".

use std::time::Duration;

use tonic::transport::{Channel, Endpoint};

pub mod pb {
    tonic::include_proto!("brain.v1");
}

pub type BrainClient = pb::brain_client::BrainClient<Channel>;

pub fn connect_lazy(url: &str) -> anyhow::Result<BrainClient> {
    let channel = Endpoint::from_shared(url.to_owned())?
        .connect_timeout(Duration::from_secs(2))
        .timeout(Duration::from_secs(150))
        .connect_lazy();
    Ok(pb::brain_client::BrainClient::new(channel))
}

pub fn claims(id: &auth::Identity) -> pb::Claims {
    pb::Claims {
        sub: id.sub.clone(),
        name: id.name.clone(),
        role: id.role.as_str().to_owned(),
        dept: id.dept.clone(),
        spend_limit: id.spend_limit,
        approval_limit: id.approval_limit,
    }
}

/// Wrap a message in a request carrying the current W3C trace context (`traceparent`), so the
/// brain's spans join the gateway's trace.
pub fn traced<T>(msg: T) -> tonic::Request<T> {
    use opentelemetry::propagation::Injector;
    use tracing_opentelemetry::OpenTelemetrySpanExt;

    struct Meta<'a>(&'a mut tonic::metadata::MetadataMap);
    impl Injector for Meta<'_> {
        fn set(&mut self, key: &str, value: String) {
            if let (Ok(k), Ok(v)) = (
                tonic::metadata::MetadataKey::from_bytes(key.as_bytes()),
                tonic::metadata::MetadataValue::try_from(value.as_str()),
            ) {
                self.0.insert(k, v);
            }
        }
    }
    let mut req = tonic::Request::new(msg);
    let ctx = tracing::Span::current().context();
    opentelemetry::global::get_text_map_propagator(|p| {
        p.inject_context(&ctx, &mut Meta(req.metadata_mut()))
    });
    req
}

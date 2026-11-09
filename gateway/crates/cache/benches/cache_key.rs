//! Criterion bench for cache-key derivation on the request path.
use cache::{answer_key, key_terms, normalize_query};
use criterion::{Criterion, criterion_group, criterion_main};

const QUERY: &str =
    "Who has to approve a purchase request of USD 15,000 for new laptops from vendor V-0042?";

fn bench(c: &mut Criterion) {
    c.bench_function("normalize_query", |b| {
        b.iter(|| normalize_query(std::hint::black_box(QUERY)))
    });
    c.bench_function("answer_key", |b| {
        b.iter(|| answer_key(std::hint::black_box(QUERY), "3f2a9c", 4, "v1", "cheap"))
    });
    c.bench_function("key_terms", |b| {
        b.iter(|| key_terms(std::hint::black_box(QUERY)))
    });
}

criterion_group!(benches, bench);
criterion_main!(benches);

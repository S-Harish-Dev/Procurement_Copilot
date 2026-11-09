//! Criterion benches for the request-path guard: normalization, injection scan, PII masking.
use criterion::{Criterion, criterion_group, criterion_main};
use guard::{InjectionScanner, normalize, pii};

const PATTERNS: &str = include_str!("../../../../prompts/injection_patterns_v1.json");
const QUERY: &str = "Who has to approve a purchase request of USD 15,000 for new laptops from vendor V-0042? \
                     Contact me at priya.sharma@acme.example.";

fn bench(c: &mut Criterion) {
    let scanner = InjectionScanner::from_json(PATTERNS).unwrap();
    c.bench_function("normalize", |b| {
        b.iter(|| normalize(std::hint::black_box(QUERY)))
    });
    c.bench_function("injection_scan", |b| {
        b.iter(|| scanner.scan(std::hint::black_box(QUERY)))
    });
    c.bench_function("pii_mask", |b| {
        b.iter(|| {
            let mut m = pii::PiiMap::default();
            pii::mask_pii(std::hint::black_box(QUERY), &mut m)
        })
    });
}

criterion_group!(benches, bench);
criterion_main!(benches);

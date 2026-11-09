# ADR-01: Rust gateway for the hot path, Python "brain" for agents, MCP, ingestion and evals

- Status: accepted (2026-10-30)

## Context
The online request path (auth, rate limiting, guards, cache, retrieval, LLM streaming) is latency- and
concurrency-bound and needs one choke point for cost control, provider fallback and tracing. The AI tooling
ecosystem (Pydantic AI, MCP reference SDK, Presidio, docling, DeepEval) is Python-first.

## Decision
- Rust (axum/tokio) gateway owns the online hot path and *every* outbound LLM/Jev call.
- Python brain owns the agent loop, MCP servers, ingestion and evals; it reaches LLMs only through the
  gateway's internal OpenAI-compatible endpoint, so cost, fallback and tracing stay in one place.
- gRPC between them (typed contract in `proto/brain.proto`, streaming, W3C trace context in metadata).

## Alternatives
- *All Python (FastAPI):* simpler, one language; but GIL-bound concurrency and no single typed choke point.
- *All Rust:* the agent/MCP/NLP ecosystem would have to be reimplemented.
- *MCP servers in Rust (`rmcp`):* I assumed `rmcp` was pre-1.0; it is 3.8.0 as of 2026-10-30, so
  maturity is no longer the reason. MCP stays in Python because the servers live beside the Pydantic AI agent
  and share its Pydantic models, and the Python SDK is the protocol's reference implementation.

## Consequences
Two toolchains and a gRPC contract to maintain; embedding parity (Rust query vs Python documents) must be tested
(M3). In exchange the hot path gets predictable latency and the AI work uses the libraries it was built for.

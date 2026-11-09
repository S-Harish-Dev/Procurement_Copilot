"""M10 experiments: ablations and threshold sweeps for `/insights/experiments` (`make experiments`).

Each experiment writes `evals/experiments/<id>.json` = {id, title, kind, created_at, takeaway, data},
where `data` is `{bars: [{label, value}], unit}` or `{curves: [{name, points: [[x, y]]}], x_label,
y_label, chosen}` plus a `table` with every measured number (rendered as raw JSON by the UI).

    uv run python -m evals.experiments all            # needs the native stack (gateway, OPA, Qdrant)
    uv run python -m evals.experiments embedders      # one at a time

All retrieval numbers use the eval-fast definitions (`evals.metrics`) on the 185 answerable cases,
inside each persona's ACL scope (OPA `doc_grants`, applied exactly like the gateway's Qdrant filter).
"""

import json
import random
import re
import statistics
import sys
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx
import numpy as np

from common.settings import REPO_ROOT, get_settings
from evals.metrics import hit_at, ndcg_at, recall_at, reciprocal_rank, summarize

GATEWAY = "http://127.0.0.1:8080"
QDRANT = "http://127.0.0.1:6333"
OPA = "http://127.0.0.1:8181"
BUNDLE = REPO_ROOT / "data" / "index_bundle"
OUT = REPO_ROOT / "evals" / "experiments"
GUARD_CATEGORIES = {"direct_injection", "prompt_extraction", "jailbreak_roleplay", "canary_extraction"}
FASTEMBED_NAMES = {
    "nomic-v1.5": "nomic-ai/nomic-embed-text-v1.5",
    "nomic-v1.5-q": "nomic-ai/nomic-embed-text-v1.5-Q",
    "bge-small-v1.5": "BAAI/bge-small-en-v1.5",
    "bge-base-v1.5": "BAAI/bge-base-en-v1.5",
}


# ---- shared data ---------------------------------------------------------------------------------


def cases() -> list[dict[str, Any]]:
    path = REPO_ROOT / "evals" / "datasets" / "qa.jsonl"
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def answerable() -> list[dict[str, Any]]:
    return [c for c in cases() if c["answerable"] and c["gold_chunk_ids"]]


class Corpus:
    """Chunk metadata in bundle row order (the dense matrices are aligned with it)."""

    def __init__(self) -> None:
        docs = {
            d["id"]: d
            for d in (json.loads(x) for x in (BUNDLE / "documents.jsonl").read_text().splitlines() if x)
        }
        self.ids: list[str] = []
        cls, dept, quarantined = [], [], []
        for line in (BUNDLE / "chunks.jsonl").read_text().splitlines():
            if not line:
                continue
            c = json.loads(line)
            d = docs[c["doc_id"]]
            self.ids.append(c["chunk_id"])
            cls.append(d["classification"])
            dept.append(d["department"])
            quarantined.append(bool(c.get("quarantined")))
        self.cls = np.array(cls)
        self.dept = np.array(dept)
        self.indexed = ~np.array(quarantined)  # quarantined chunks never reach Qdrant
        self._masks: dict[str, np.ndarray] = {}
        self.personas = {u["id"]: u for u in httpx.get(f"{GATEWAY}/v1/auth/personas", timeout=10).json()}

    def grants(self, persona: str) -> dict[str, list[str]]:
        u = self.personas[persona]
        user = {
            "id": u["id"],
            "role": u["role"],
            "dept": u["department"],
            "spend_limit": u["spend_limit"],
            "approval_limit": u["approval_limit"],
        }
        r = httpx.post(
            f"{OPA}/v1/data/acme/authz",
            json={"input": {"user": user, "action": "retrieve", "resource": {}}},
            timeout=10,
        )
        grants: dict[str, list[str]] = r.json()["result"]["doc_grants"]
        return grants

    def mask(self, persona: str) -> np.ndarray:
        """Rows this persona may retrieve: the same rule as `retrieval::search::acl_filter`."""
        if persona not in self._masks:
            m = np.zeros(len(self.ids), dtype=bool)
            for classification, depts in self.grants(persona).items():
                sel = self.cls == classification
                if "*" not in depts:
                    sel &= np.isin(self.dept, depts)
                m |= sel
            self._masks[persona] = m & self.indexed
        return self._masks[persona]


def unit(m: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(m, axis=-1, keepdims=True)
    out: np.ndarray = m / np.where(n == 0, 1, n)
    return out


def embed_queries(model: str, texts: Sequence[str]) -> tuple[np.ndarray, float]:
    """Query vectors with the model's query prefix, and the mean ms per query (batch 1)."""
    from fastembed import TextEmbedding

    manifest = json.loads((BUNDLE / "manifest.json").read_text())
    prefix = manifest["dense_models"][model]["query_prefix"]
    emb = TextEmbedding(
        FASTEMBED_NAMES[model], cache_dir=str(get_settings().models_dir / "fastembed"), threads=4
    )
    started = time.perf_counter()
    vecs = [next(iter(emb.embed([prefix + t]))) for t in texts]
    ms = (time.perf_counter() - started) * 1000 / max(len(texts), 1)
    return unit(np.array(vecs, dtype=np.float32)), ms


def metrics_for(rankings: list[list[str]], golds: list[set[str]]) -> dict[str, dict[str, float]]:
    per = {
        "recall@20": [recall_at(r, g, 20) for r, g in zip(rankings, golds, strict=True)],
        "hit@5": [hit_at(r, g, 5) for r, g in zip(rankings, golds, strict=True)],
        "mrr": [reciprocal_rank(r, g) for r, g in zip(rankings, golds, strict=True)],
        "ndcg@10": [ndcg_at(r, g, 10) for r, g in zip(rankings, golds, strict=True)],
    }
    return {k: summarize(v) for k, v in per.items()}


def dense_rankings(
    corpus: Corpus, docs: np.ndarray, queries: np.ndarray, qs: list[dict[str, Any]], k: int = 20
) -> list[list[str]]:
    out = []
    scores = queries @ docs.T
    for i, c in enumerate(qs):
        s = np.where(corpus.mask(c["persona"]), scores[i], -np.inf)
        top = np.argpartition(-s, k)[:k]
        top = top[np.argsort(-s[top], kind="stable")]
        out.append([corpus.ids[j] for j in top])
    return out


def write(exp_id: str, title: str, kind: str, takeaway: str, data: dict[str, Any]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    doc = {
        "id": exp_id,
        "title": title,
        "kind": kind,
        "takeaway": takeaway,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "data": data,
    }
    (OUT / f"{exp_id}.json").write_text(json.dumps(doc, indent=2) + "\n")
    print(f"[{exp_id}] {takeaway}")


def r3(x: float) -> float:
    return round(float(x), 3)


# ---- experiments -----------------------------------------------------------------------------------


def embedders() -> None:
    corpus, qs = Corpus(), answerable()
    golds = [set(c["gold_chunk_ids"]) for c in qs]
    manifest = json.loads((BUNDLE / "manifest.json").read_text())
    table: list[dict[str, Any]] = []
    for model, meta in manifest["dense_models"].items():
        docs = unit(np.load(BUNDLE / meta["file"]).astype(np.float32))
        qv, ms = embed_queries(model, [c["question"] for c in qs])
        m = metrics_for(dense_rankings(corpus, docs, qv, qs), golds)
        table.append(
            {
                "model": model,
                "dim": meta["dim"],
                "query_ms": round(ms, 1),
                "index_mb": round(docs.nbytes / 1e6, 1),
                **{k: r3(v["mean"]) for k, v in m.items()},
                "recall@20_ci": [r3(m["recall@20"]["ci_low"]), r3(m["recall@20"]["ci_high"])],
            }
        )
    best = max(table, key=lambda t: t["mrr"])
    prod = next(t for t in table if t["model"] == "nomic-v1.5")
    write(
        "embedders",
        "Embedders (dense only)",
        "embedders",
        f"{best['model']} has the best MRR ({best['mrr']:.3f}); "
        f"production nomic-v1.5: MRR {prod['mrr']:.3f}, "
        f"recall@20 {prod['recall@20']:.3f} (dense only; production adds BM25)",
        {
            "bars": [{"label": t["model"], "value": t["recall@20"]} for t in table],
            "unit": "recall@20",
            "table": table,
        },
    )


def matryoshka() -> None:
    corpus, qs = Corpus(), answerable()
    golds = [set(c["gold_chunk_ids"]) for c in qs]
    full = np.load(BUNDLE / "dense" / "nomic-v1.5.npy").astype(np.float32)
    qv_full, _ = embed_queries("nomic-v1.5", [c["question"] for c in qs])
    rows, recall_pts, mrr_pts = [], [], []
    for d in (768, 512, 384, 256, 128, 64):
        m = metrics_for(dense_rankings(corpus, unit(full[:, :d]), unit(qv_full[:, :d]), qs), golds)
        rows.append(
            {
                "dims": d,
                "index_mb_fp32": round(full.shape[0] * d * 4 / 1e6, 1),
                **{k: r3(v["mean"]) for k, v in m.items()},
            }
        )
        recall_pts.append([d, r3(m["recall@20"]["mean"])])
        mrr_pts.append([d, r3(m["mrr"]["mean"])])
    r768, r256 = rows[0]["recall@20"], rows[3]["recall@20"]
    write(
        "matryoshka",
        "Matryoshka dimensions (nomic, dense only)",
        "matryoshka",
        f"256 dims keep recall@20 at {r256:.3f} vs {r768:.3f} at 768 for a third of the memory",
        {
            "curves": [{"name": "recall@20", "points": recall_pts}, {"name": "MRR", "points": mrr_pts}],
            "x_label": "dimensions",
            "y_label": "score",
            "chosen": {"x": 768, "y": r768, "label": "production"},
            "table": rows,
        },
    )


def _branch(token: str, case: dict[str, Any], dense: bool) -> list[str]:
    r = httpx.post(
        f"{GATEWAY}/admin/eval/retrieve",
        headers={"authorization": f"Bearer {token}"},
        timeout=60,
        json={
            "query": case["question"],
            "as_user": case["persona"],
            "use_dense": dense,
            "use_sparse": not dense,
            "dense_k": 40,
            "sparse_k": 40,
            "top_k": 40,
        },
    )
    r.raise_for_status()
    return [c["chunk_id"] for c in r.json()["candidates"]]


def rrf(branches: list[list[str]], k: float, weights: Sequence[float], top: int = 20) -> list[str]:
    """Same tie-break as the gateway: score, then best single-branch rank, then chunk id."""
    score: dict[str, float] = {}
    best: dict[str, int] = {}
    for w, branch in zip(weights, branches, strict=True):
        for i, cid in enumerate(branch):
            score[cid] = score.get(cid, 0.0) + w / (k + i + 1)
            best[cid] = min(best.get(cid, i), i)
    return sorted(score, key=lambda c: (-score[c], best[c], c))[:top]


def fusion() -> None:
    qs = answerable()
    golds = [set(c["gold_chunk_ids"]) for c in qs]
    token = httpx.post(f"{GATEWAY}/v1/auth/login", json={"user_id": "olivia.grant"}).json()["token"]
    with ThreadPoolExecutor(4) as pool:
        dense = list(pool.map(lambda c: _branch(token, c, True), qs))
        sparse = list(pool.map(lambda c: _branch(token, c, False), qs))
    variants: dict[str, Callable[[int], list[str]]] = {
        "dense only": lambda i: dense[i][:20],
        "BM25 only": lambda i: sparse[i][:20],
        "RRF k=2 (production)": lambda i: rrf([dense[i], sparse[i]], 2, [1, 1]),
        "RRF k=10": lambda i: rrf([dense[i], sparse[i]], 10, [1, 1]),
        "RRF k=60": lambda i: rrf([dense[i], sparse[i]], 60, [1, 1]),
        "RRF k=2, dense 0.7": lambda i: rrf([dense[i], sparse[i]], 2, [0.7, 0.3]),
        "RRF k=2, dense 0.3": lambda i: rrf([dense[i], sparse[i]], 2, [0.3, 0.7]),
    }
    table: list[dict[str, Any]] = []
    for name, fn in variants.items():
        m = metrics_for([fn(i) for i in range(len(qs))], golds)
        table.append(
            {
                "variant": name,
                **{k: r3(v["mean"]) for k, v in m.items()},
                "mrr_ci": [r3(m["mrr"]["ci_low"]), r3(m["mrr"]["ci_high"])],
            }
        )
    best = max(table, key=lambda t: t["mrr"])
    prod = next(t for t in table if t["variant"].endswith("(production)"))
    write(
        "fusion",
        "Dense vs BM25 vs hybrid fusion",
        "fusion",
        f"Hybrid beats either branch alone; best MRR: {best['variant']} ({best['mrr']:.3f}) vs production "
        f"{prod['mrr']:.3f} (CI {prod['mrr_ci'][0]}–{prod['mrr_ci'][1]})",
        {"bars": [{"label": t["variant"], "value": t["mrr"]} for t in table], "unit": "MRR", "table": table},
    )


def _qdrant_filter(grants: dict[str, list[str]], version: int) -> dict[str, Any]:
    should = []
    for classification, depts in grants.items():
        must: list[dict[str, Any]] = [{"key": "classification", "match": {"value": classification}}]
        if "*" not in depts:
            must.append({"key": "department", "match": {"any": depts}})
        should.append({"must": must})
    return {"should": should, "must": [{"key": "index_version", "match": {"value": version}}]}


def hnsw() -> None:
    corpus, qs = Corpus(), answerable()
    golds = [set(c["gold_chunk_ids"]) for c in qs]
    version = int(httpx.get(f"{GATEWAY}/readyz", timeout=10).json()["index_version"])
    qv, _ = embed_queries("nomic-v1.5", [c["question"] for c in qs])
    filters = {p: _qdrant_filter(corpus.grants(p), version) for p in {c["persona"] for c in qs}}
    http = httpx.Client(timeout=30)

    def search(i: int, params: dict[str, Any]) -> tuple[list[str], float]:
        body = {
            "query": qv[i].tolist(),
            "using": "dense",
            "limit": 20,
            "filter": filters[qs[i]["persona"]],
            "params": params,
            "with_payload": ["chunk_id"],
        }
        t = time.perf_counter()
        r = http.post(f"{QDRANT}/collections/chunks/points/query", json=body)
        ms = (time.perf_counter() - t) * 1000
        r.raise_for_status()
        return [p["payload"]["chunk_id"] for p in r.json()["result"]["points"]], ms

    def run(params: dict[str, Any]) -> tuple[list[list[str]], float]:
        res = [search(i, params) for i in range(len(qs))]
        return [r for r, _ in res], statistics.median(ms for _, ms in res)

    exact, exact_ms = run({"exact": True, "quantization": {"ignore": True}})
    rows: list[dict[str, Any]] = []

    def row(name: str, params: dict[str, Any], x: float | None = None) -> dict[str, Any]:
        ranked, ms = run(params)
        overlap = statistics.mean(len(set(a) & set(b)) / 20 for a, b in zip(ranked, exact, strict=True))
        m = metrics_for(ranked, golds)
        out = {
            "variant": name,
            "x": x,
            "ann_recall_vs_exact": r3(overlap),
            "p50_ms": round(ms, 2),
            "recall@20": r3(m["recall@20"]["mean"]),
            "mrr": r3(m["mrr"]["mean"]),
        }
        rows.append(out)
        return out

    curve = [row(f"hnsw_ef={ef}", {"hnsw_ef": ef}, ef) for ef in (8, 16, 32, 64, 128, 256)]
    row("int8 + rescore (production default)", {})
    row("int8, no rescore", {"quantization": {"rescore": False}})
    row("fp32 HNSW (quantization ignored)", {"quantization": {"ignore": True}})
    exact_row = {
        "variant": "exact (brute force, fp32)",
        "x": None,
        "ann_recall_vs_exact": 1.0,
        "p50_ms": round(exact_ms, 2),
        **{k: r3(v["mean"]) for k, v in metrics_for(exact, golds).items() if k in ("recall@20", "mrr")},
    }
    rows.append(exact_row)
    knee = next((c for c in curve if c["ann_recall_vs_exact"] >= 0.99), curve[-1])
    write(
        "hnsw_ef",
        "HNSW ef: recall vs latency (vs exact search)",
        "hnsw_ef",
        f"ef={int(knee['x'])} already returns {knee['ann_recall_vs_exact']:.1%} of the exact top-20 at "
        f"{knee['p50_ms']} ms p50 (exact: {exact_ms:.1f} ms); "
        "filtered ACL searches are small, so HNSW is cheap",
        {
            "curves": [
                {
                    "name": "share of exact top-20",
                    "points": [[c["x"], c["ann_recall_vs_exact"]] for c in curve],
                },
                {"name": "gold recall@20", "points": [[c["x"], c["recall@20"]] for c in curve]},
            ],
            "x_label": "hnsw_ef",
            "y_label": "recall",
            "chosen": {
                "x": knee["x"],
                "y": knee["ann_recall_vs_exact"],
                "label": "smallest ef ≥ 99% of exact",
            },
            "table": rows,
        },
    )
    quant = [r for r in rows if "int8" in r["variant"] or "fp32" in r["variant"] or "exact" in r["variant"]]
    write(
        "quantization",
        "int8 scalar quantization vs fp32",
        "quantization",
        "int8 with rescoring matches fp32: "
        + ", ".join(f"{r['variant']}: recall@20 {r['recall@20']}" for r in quant),
        {
            "bars": [{"label": r["variant"], "value": r["recall@20"]} for r in quant],
            "unit": "recall@20",
            "table": quant,
        },
    )


def _guard_scores(texts: list[str]) -> list[dict[str, Any]]:
    token = httpx.post(f"{GATEWAY}/v1/auth/login", json={"user_id": "olivia.grant"}).json()["token"]
    with ThreadPoolExecutor(4) as pool:
        return list(
            pool.map(
                lambda t: httpx.post(
                    f"{GATEWAY}/admin/eval/guard",
                    json={"text": t},
                    timeout=60,
                    headers={"authorization": f"Bearer {token}"},
                ).json(),
                texts,
            )
        )


COMMANDS = [  # ordinary things approvers and requesters type (negatives; added in M8 after a real FP)
    "approve this",
    "reject this request",
    "approve my own request",
    "approve it",
    "reject it",
    "approve PR-2026-00041",
    "reject PR-2026-00309, budget is frozen",
    "sign off on this",
    "approve the pending request",
    "please approve this one",
    "decline this request",
    "approve all pending requests from IT",
    "cancel my request",
    "submit it",
    "create a purchase request for 2 laptops",
    "I need to buy 5 monitors from Dell",
    "show my requests",
    "what is pending for me?",
    "approve this, looks fine",
    "go ahead and approve",
    "yes, approve",
    "override the limit and approve",
]


def tau_classifier() -> None:
    rt = REPO_ROOT / "evals" / "redteam"
    attacks = [
        json.loads(x)["prompt"]
        for x in (rt / "cases.jsonl").read_text().splitlines()
        if x and json.loads(x)["category"] in GUARD_CATEGORIES
    ]
    benign = [json.loads(x)["prompt"] for x in (rt / "benign.jsonl").read_text().splitlines() if x]
    pos = [s["p_classifier"] for s in _guard_scores(attacks)]
    neg = [s["p_classifier"] for s in _guard_scores(benign + COMMANDS)]
    pr, prec_pts, rec_pts, rows = [], [], [], []
    for tau in [round(x * 0.05, 2) for x in range(1, 20)] + [0.97, 0.99]:
        tp = sum(p >= tau for p in pos)
        fp = sum(p >= tau for p in neg)
        precision = tp / (tp + fp) if tp + fp else 1.0
        recall = tp / len(pos)
        rows.append(
            {
                "tau": tau,
                "tp": tp,
                "fp": fp,
                "fn": len(pos) - tp,
                "tn": len(neg) - fp,
                "precision": r3(precision),
                "recall": r3(recall),
            }
        )
        pr.append([r3(recall), r3(precision)])
        prec_pts.append([tau, r3(precision)])
        rec_pts.append([tau, r3(recall)])
    chosen = next(r for r in rows if r["tau"] == 0.9)
    write(
        "tau_classifier",
        "Injection classifier τ (alone, no word-count rule)",
        "tau_sweep",
        f"τ=0.9 (production): recall {chosen['recall']:.1%}, precision {chosen['precision']:.1%} on "
        f"{len(pos)} attacks vs {len(neg)} benign + command messages. Scores are bimodal: τ 0.5→0.9 changes "
        "only a few detections, and the false positives score ≥ 0.95, so no τ removes them "
        "(the 6-word rule does)",
        {
            "curves": [{"name": "precision", "points": prec_pts}, {"name": "recall", "points": rec_pts}],
            "x_label": "τ (classifier score)",
            "y_label": "rate",
            "chosen": {"x": 0.9, "y": chosen["recall"], "label": "τ_cls = 0.9"},
            "table": rows,
            "pr_curve": pr,
        },
    )


def key_terms(q: str) -> str:
    """Mirror of `cache::key_terms` (Rust): tokens carrying a digit, trimmed, commas dropped, lower-cased."""
    tokens = re.split(r'[\s?!()"]+', q)
    out = set()
    for t in tokens:
        t = re.sub(r"^[^0-9A-Za-z]+|[^0-9A-Za-z]+$", "", t).replace(",", "").lower()
        if any(ch.isdigit() for ch in t):
            out.add(t)
    return " ".join(sorted(out))


def tau_semcache() -> None:
    """Paraphrase pairs vs hard negatives (different questions with the same numbers/identifiers)."""
    all_cases = cases()
    by_id = {c["id"]: c for c in all_cases}
    pairs = [
        (by_id[c["paraphrase_of"]]["question"], c["question"])
        for c in all_cases
        if c["category"] == "paraphrase" and c.get("paraphrase_of") in by_id
    ]
    questions = sorted({c["question"] for c in all_cases})
    qv, _ = embed_queries("nomic-v1.5", questions)
    index = {q: i for i, q in enumerate(questions)}
    para_vecs, _ = embed_queries("nomic-v1.5", [p for _, p in pairs])
    pos = [float(qv[index[a]] @ para_vecs[i]) for i, (a, _) in enumerate(pairs)]
    para_set = {frozenset(p) for p in pairs}
    sims = qv @ qv.T
    neg: list[float] = []
    for i, a in enumerate(questions):
        for j in range(i + 1, len(questions)):
            b = questions[j]
            if frozenset((a, b)) in para_set or key_terms(a) != key_terms(b):
                continue  # the key-term filter already separates pairs with different numbers/ids
            neg.append(float(sims[i, j]))
    rows, hit_pts, false_pts = [], [], []
    for t in [round(0.80 + 0.01 * x, 2) for x in range(0, 20)]:
        hits = sum(s >= t for s in pos) / len(pos)
        false = sum(s >= t for s in neg) / len(neg)
        rows.append(
            {
                "threshold": t,
                "paraphrase_hit_rate": r3(hits),
                "false_hit_rate": round(false, 5),
                "false_hits": sum(s >= t for s in neg),
            }
        )
        hit_pts.append([t, r3(hits)])
        false_pts.append([t, round(false, 5)])
    safe = next((r for r in rows if r["false_hits"] == 0), rows[-1])
    write(
        "tau_semcache",
        "Semantic cache threshold",
        "tau_sweep",
        f"Hardest negative {max(neg):.3f}; paraphrases {min(pos):.3f}–{max(pos):.3f}. Lowest threshold with "
        f"0 false hits among {len(neg):,} same-key-term question pairs: {safe['threshold']} "
        f"({safe['paraphrase_hit_rate']:.0%} paraphrase hits); "
        f"0.95 hits {rows[15]['paraphrase_hit_rate']:.0%}",
        {
            "curves": [
                {"name": "paraphrase hit rate", "points": hit_pts},
                {"name": "false hit rate", "points": false_pts},
            ],
            "x_label": "cosine threshold",
            "y_label": "rate",
            "chosen": {"x": safe["threshold"], "y": safe["paraphrase_hit_rate"], "label": "0 false hits"},
            "table": rows,
            "positives": [r3(p) for p in pos],
            "hardest_negatives": sorted(map(r3, neg))[-10:],
            "n_negative_pairs": len(neg),
        },
    )


EXPERIMENTS: dict[str, Callable[[], None]] = {
    "embedders": embedders,
    "matryoshka": matryoshka,
    "fusion": fusion,
    "hnsw": hnsw,
    "tau_classifier": tau_classifier,
    "tau_semcache": tau_semcache,
}

if __name__ == "__main__":
    random.seed(7)
    names = list(EXPERIMENTS) if sys.argv[1:] in ([], ["all"]) else sys.argv[1:]
    for n in names:
        EXPERIMENTS[n]()

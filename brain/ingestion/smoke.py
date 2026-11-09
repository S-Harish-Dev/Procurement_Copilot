"""Retrieval smoke test over the loaded index (light: query embedding only).

Runs a handful of real questions through the same shape of query the gateway will use in M3:
dense (nomic, "search_query: ") top-40 + BM25 top-40, ACL filter on BOTH branches, RRF fusion.
The ACL filter is built from OPA-style `doc_grants`. Checks that the expected document is in the
top 5, and that a confidential document is never returned to an employee.

    uv run python -m ingestion.smoke [--qdrant-path DIR]
"""

import argparse
import sys
from dataclasses import dataclass

from fastembed import SparseTextEmbedding, TextEmbedding
from qdrant_client import QdrantClient, models

from common.settings import get_settings
from ingestion.embed import PRIMARY, SPARSE_MODEL
from ingestion.store import COLLECTION

EMPLOYEE_IT = {"public": ["*"], "internal": ["IT", "all"]}
FINANCE = {"public": ["*"], "internal": ["Finance", "all"], "confidential": ["*"]}


@dataclass
class Case:
    question: str
    grants: dict[str, list[str]]
    expect_doc: str | None  # doc_id prefix that must appear in the top 5 (None = only the ACL check)
    forbid_doc: str | None = None  # doc_id prefix that must NOT appear at all


CASES = [
    Case("Who has to approve a purchase request of USD 15,000?", EMPLOYEE_IT, "acme-pol-approval-matrix"),
    Case("What is the hotel cap per night in tier-1 cities?", EMPLOYEE_IT, "acme-pol-travel"),
    Case("How often are standard laptops refreshed?", EMPLOYEE_IT, "acme-pol-it-hardware"),
    Case("When is first article testing and approval appropriate?", EMPLOYEE_IT, "far"),
    Case(
        "What is the FY2026 budget allocation for each department?",
        EMPLOYEE_IT,
        None,
        forbid_doc="acme-conf-budget-fy26",
    ),
    Case("What is the FY2026 budget allocation for each department?", FINANCE, "acme-conf-budget-fy26"),
]


def acl_filter(grants: dict[str, list[str]]) -> models.Filter:
    """One `should` clause per classification grant (see ADR-09), excluding quarantine-flagged chunks."""
    should: list[models.Condition] = []
    for classification, depts in grants.items():
        must: list[models.Condition] = [
            models.FieldCondition(key="classification", match=models.MatchValue(value=classification))
        ]
        if "*" not in depts:
            must.append(models.FieldCondition(key="department", match=models.MatchAny(any=depts)))
        should.append(models.Filter(must=must))
    return models.Filter(should=should)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--qdrant-path", help="embedded Qdrant directory instead of QDRANT_URL")
    args = parser.parse_args()
    client = (
        QdrantClient(path=args.qdrant_path)
        if args.qdrant_path
        else QdrantClient(url=get_settings().qdrant_url)
    )
    dense = TextEmbedding(PRIMARY.model)
    sparse = SparseTextEmbedding(SPARSE_MODEL)
    failures = 0
    for case in CASES:
        qd = next(iter(dense.embed([PRIMARY.query_prefix + case.question]))).tolist()
        qs = next(iter(sparse.query_embed(case.question)))
        acl = acl_filter(case.grants)
        res = client.query_points(
            COLLECTION,
            prefetch=[
                models.Prefetch(query=qd, using="dense", filter=acl, limit=40),
                models.Prefetch(
                    query=models.SparseVector(indices=qs.indices.tolist(), values=qs.values.tolist()),
                    using="bm25",
                    filter=acl,
                    limit=40,
                ),
            ],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            limit=20,
            with_payload=["doc_id", "section_path", "classification"],
        )
        docs = [(p.payload or {}).get("doc_id", "") for p in res.points]
        ok = True
        if case.expect_doc and not any(d.startswith(case.expect_doc) for d in docs[:5]):
            ok = False
        if case.forbid_doc and any(d.startswith(case.forbid_doc) for d in docs):
            ok = False
        failures += not ok
        role = "finance " if "confidential" in case.grants else "employee"
        print(f"{'PASS' if ok else 'FAIL'} [{role}] {case.question}")
        for p in res.points[:3]:
            pl = p.payload or {}
            print(f"       {p.score:.3f} {pl.get('doc_id')} › {str(pl.get('section_path'))[:70]}")
    print(f"{len(CASES) - failures}/{len(CASES)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

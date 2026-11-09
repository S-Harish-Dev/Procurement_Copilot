"""Persist an ingestion run: Postgres registry (+ encrypted originals) and Qdrant points.

Versioning: every run gets a new index_version. Points are written with that
version, then the version is activated in Postgres (one active at a time).
Retrieval filters on the active version and cache keys include it, so a new
index invalidates caches without any explicit flush. The previous version's
points are kept for rollback; older ones are deleted.
"""

import uuid
from dataclasses import dataclass

import numpy as np
import psycopg
from qdrant_client import QdrantClient, models

from common.crypto import encrypt
from ingestion.embed import DENSE_DIM, SparseVec

COLLECTION = "chunks"
PAYLOAD_KEYWORD_INDEXES = (
    "doc_id",
    "source",
    "department",
    "classification",
    "index_version",
    "injection_flag",
)


@dataclass
class ChunkRecord:
    chunk_id: str
    doc_id: str
    ord: int
    section_path: str
    text_masked: str
    text_original: str
    token_count: int
    injection_flag: bool
    injection_score: float | None
    quarantined: bool


def ensure_collection(client: QdrantClient) -> None:
    if client.collection_exists(COLLECTION):
        return
    client.create_collection(
        COLLECTION,
        vectors_config={
            "dense": models.VectorParams(
                size=DENSE_DIM,
                distance=models.Distance.COSINE,
                hnsw_config=models.HnswConfigDiff(m=16, ef_construct=128),
                # int8 scalar quantization kept in RAM; full vectors on disk for rescoring.
                quantization_config=models.ScalarQuantization(
                    scalar=models.ScalarQuantizationConfig(type=models.ScalarType.INT8, always_ram=True)
                ),
                on_disk=True,
            )
        },
        sparse_vectors_config={"bm25": models.SparseVectorParams(modifier=models.Modifier.IDF)},
    )
    for name in PAYLOAD_KEYWORD_INDEXES:
        schema = (
            models.PayloadSchemaType.BOOL
            if name == "injection_flag"
            else (
                models.PayloadSchemaType.INTEGER
                if name == "index_version"
                else models.PayloadSchemaType.KEYWORD
            )
        )
        client.create_payload_index(COLLECTION, name, field_schema=schema)


def new_index_version(conn: psycopg.Connection, notes: str) -> int:
    row = conn.execute(
        "INSERT INTO index_versions (version, notes) "
        "SELECT COALESCE(MAX(version), 0) + 1, %s FROM index_versions RETURNING version",
        (notes,),
    ).fetchone()
    assert row is not None
    return int(row[0])


def write_document(conn: psycopg.Connection, doc: dict[str, object], version: int) -> None:
    conn.execute(
        """INSERT INTO documents (id, source, title, path, sha256, department, classification, effective_date,
                                  index_version, ingested_at)
           VALUES (%(id)s, %(source)s, %(title)s, %(path)s, %(sha256)s, %(department)s, %(classification)s,
                   %(effective_date)s, %(version)s, now())
           ON CONFLICT (id) DO UPDATE SET title = EXCLUDED.title, path = EXCLUDED.path,
               sha256 = EXCLUDED.sha256, department = EXCLUDED.department,
               classification = EXCLUDED.classification, effective_date = EXCLUDED.effective_date,
               index_version = EXCLUDED.index_version, ingested_at = now()""",
        {**doc, "version": version},
    )
    # Chunk rows belong to the active version only; the old rows go with the re-ingest.
    conn.execute("DELETE FROM chunks WHERE doc_id = %s", (doc["id"],))


def write_chunks(conn: psycopg.Connection, chunks: list[ChunkRecord], version: int) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO chunks (id, doc_id, ord, section_path, text_masked, text_original_enc, token_count,
                                   injection_flag, injection_score, quarantined, index_version)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            [
                (
                    c.chunk_id,
                    c.doc_id,
                    c.ord,
                    c.section_path,
                    c.text_masked,
                    encrypt(c.text_original, aad=f"chunks:{c.chunk_id}".encode()),
                    c.token_count,
                    c.injection_flag,
                    c.injection_score,
                    c.quarantined,
                    version,
                )
                for c in chunks
            ],
        )


def upsert_points(
    client: QdrantClient,
    doc: dict[str, object],
    chunks: list[ChunkRecord],
    dense: np.ndarray,
    sparse: list[SparseVec],
    version: int,
) -> int:
    points = []
    for c, d, s in zip(chunks, dense, sparse, strict=True):
        if c.quarantined:
            continue  # high-risk chunks stay in Postgres for review, never searchable
        points.append(
            models.PointStruct(
                id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{c.chunk_id}@v{version}")),
                vector={"dense": d.tolist(), "bm25": models.SparseVector(indices=s.indices, values=s.values)},
                payload={
                    "chunk_id": c.chunk_id,
                    "doc_id": c.doc_id,
                    "source": doc["source"],
                    "title": doc["title"],
                    "section_path": c.section_path,
                    "department": doc["department"],
                    "classification": doc["classification"],
                    "effective_date": doc["effective_date"],
                    "index_version": version,
                    "text": c.text_masked,
                    "injection_flag": c.injection_flag,
                },
            )
        )
    for i in range(0, len(points), 256):
        client.upsert(COLLECTION, points=points[i : i + 256], wait=True)
    return len(points)


def activate(conn: psycopg.Connection, client: QdrantClient, version: int) -> None:
    with conn.transaction():
        conn.execute("UPDATE index_versions SET is_active = false WHERE is_active")
        conn.execute("UPDATE index_versions SET is_active = true WHERE version = %s", (version,))
    # Keep the previous version for rollback; delete anything older.
    client.delete(
        COLLECTION,
        points_selector=models.FilterSelector(
            filter=models.Filter(
                must=[models.FieldCondition(key="index_version", range=models.Range(lt=version - 1))]
            )
        ),
    )

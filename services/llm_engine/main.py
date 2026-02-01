import os
import time
import uuid
from typing import List, Dict

import psycopg2
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from pinecone import Pinecone
from dotenv import load_dotenv

from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel

from guardrails import Guard
from guardrails.hub import ValidLength

from ragas import evaluate
from ragas.metrics import faithfulness, answer_relevancy
from datasets import Dataset

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_community.embeddings import HuggingFaceEmbeddings

from common.analytics import (
    record_stage_execution,
    record_query_result,
    init_analytics_schema,
    get_conn
)

import promptlayer
from langsmith import traceable

load_dotenv()

os.environ["LANGCHAIN_TRACING_V2"] = "true"
os.environ["LANGCHAIN_API_KEY"] = os.environ.get("LANGCHAIN_API_KEY")
os.environ["LANGCHAIN_PROJECT"] = os.environ.get("LANGCHAIN_PROJECT")

promptlayer_client = promptlayer.PromptLayer()

OpenAI = promptlayer_client.openai.OpenAI

groq_client = OpenAI(
    api_key=os.environ["GROQ_API_KEY"],
    base_url="https://api.groq.com/openai/v1",
)

init_analytics_schema()

PROMPTLAYER_API_KEY = os.environ["PROMPTLAYER_API_KEY"]
PINECONE_API_KEY = os.environ["PINECONE_API_KEY"]
NEON_DB_URL = os.environ["NEON_DB_URL"]
RERANKER_URL = os.environ["RERANKER_URL"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]
GOOGLE_API_KEY = os.environ["GOOGLE_API_KEY"]
NLI_URL = os.environ["NLI_URL"]

GROQ_MODEL = "llama-3.3-70b-versatile"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

NLI_CONTRADICTION_THRESHOLD = 0.7

KB_CHUNKS_INDEX = "kb-chunks"
KB_PAGES_INDEX = "kb-pages"
KB_IMAGES_INDEX = "kb-images"

TOP_K_CHUNKS = 50
TOP_K_PAGES = 20
FINAL_TOP_K = 8
TOP_K_IMAGES = 5

W_CHUNK = 0.7
W_PAGE = 0.3

evaluation_llm = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash",
    google_api_key=GOOGLE_API_KEY,
    temperature=0,
)

ragas_embeddings = HuggingFaceEmbeddings(
    model_name="sentence-transformers/all-MiniLM-L6-v2"
)

ragas_metrics = [faithfulness, answer_relevancy]

length_guard = Guard().use_many(
    ValidLength(min=20, max=4000)
)

pc = Pinecone(api_key=PINECONE_API_KEY)
chunks_index = pc.Index(KB_CHUNKS_INDEX)
pages_index = pc.Index(KB_PAGES_INDEX)
images_index = pc.Index(KB_IMAGES_INDEX)

app = FastAPI()

retry_strategy = Retry(
    total=3,
    status_forcelist=[429, 500, 502, 503, 504],
    backoff_factor=1,
)
adapter = HTTPAdapter(max_retries=retry_strategy)
http_session = requests.Session()
http_session.mount("https://", adapter)
http_session.mount("http://", adapter)

class QueryRequest(BaseModel):
    query: str

@traceable
def search_with_text(trace_id, index, text: str, top_k: int):
    start = time.time()
    try:
        response = index.search(
            namespace="default",
            query={"inputs": {"text": text}, "top_k": top_k},
        )
        record_stage_execution(
            trace_id=trace_id,
            pipeline="query",
            stage_name="vector_search",
            status="success",
            latency_ms=int((time.time() - start) * 1000),
        )
        hits = response.get("result", {}).get("hits") or []
        return {hit["_id"]: hit["_score"] for hit in hits}
    except Exception:
        record_stage_execution(
            trace_id=trace_id,
            pipeline="query",
            stage_name="vector_search",
            status="failure",
            latency_ms=int((time.time() - start) * 1000),
        )
        raise

@traceable
def fetch_chunks_from_neon(trace_id, chunk_ids: List[str]) -> Dict[str, dict]:
    start = time.time()
    try:
        if not chunk_ids:
            return {}
        placeholders = ",".join(["%s"] * len(chunk_ids))
        query = f"""
            SELECT chunk_hash, raw_chunk, section_path, page_id
            FROM kb_chunks
            WHERE chunk_hash IN ({placeholders})
        """
        with psycopg2.connect(NEON_DB_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(query, chunk_ids)
                rows = cur.fetchall()
        record_stage_execution(
            trace_id=trace_id,
            pipeline="query",
            stage_name="fetch_chunks",
            status="success",
            latency_ms=int((time.time() - start) * 1000),
        )
        return {
            row[0]: {"text": row[1], "section": row[2], "page_id": row[3]}
            for row in rows
        }
    except Exception:
        record_stage_execution(
            trace_id=trace_id,
            pipeline="query",
            stage_name="fetch_chunks",
            status="failure",
            latency_ms=int((time.time() - start) * 1000),
        )
        raise

@traceable
def fetch_images_from_neon(trace_id, image_ids: List[str]) -> List[Dict]:
    start = time.time()
    try:
        if not image_ids:
            return []
        placeholders = ",".join(["%s"] * len(image_ids))
        query = f"""
            SELECT image_hash, page_id, image_src, caption
            FROM kb_images
            WHERE image_hash IN ({placeholders})
        """
        with psycopg2.connect(NEON_DB_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(query, image_ids)
                rows = cur.fetchall()
        record_stage_execution(
            trace_id=trace_id,
            pipeline="query",
            stage_name="fetch_images",
            status="success",
            latency_ms=int((time.time() - start) * 1000),
        )
        return [
            {"image_hash": row[0], "page_id": row[1], "url": row[2], "caption": row[3]}
            for row in rows
        ]
    except Exception:
        record_stage_execution(
            trace_id=trace_id,
            pipeline="query",
            stage_name="fetch_images",
            status="failure",
            latency_ms=int((time.time() - start) * 1000),
        )
        raise

@traceable
def call_reranker(trace_id, query: str, texts: List[str]) -> List[float]:
    start = time.time()
    try:
        r = http_session.post(
            RERANKER_URL,
            json={"query": query, "texts": texts},
            timeout=120,
        )
        r.raise_for_status()
        record_stage_execution(
            trace_id=trace_id,
            pipeline="query",
            stage_name="rerank",
            status="success",
            latency_ms=int((time.time() - start) * 1000),
        )
        return r.json()["scores"]
    except Exception:
        record_stage_execution(
            trace_id=trace_id,
            pipeline="query",
            stage_name="rerank",
            status="failure",
            latency_ms=int((time.time() - start) * 1000),
        )
        raise

def build_context(chunks: list[dict]) -> str:
    return "\n\n".join(
        f"[Page ID: {c['page_id']}]\nSection: {c['section']}\n{c['text']}"
        for c in chunks
    )

@traceable
def call_groq_llm(trace_id, query: str, context: str) -> str:
    start = time.time()
    try:
        response = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an internal company knowledge assistant. "
                        "Answer only using the provided context. "
                        "If the answer is not explicitly in the context, reply: "
                        "'Not found in knowledge base.'"
                    ),
                },
                {
                    "role": "user",
                    "content": f"Context:\n{context}\n\nQuestion: {query}",
                },
            ],
            temperature=0.2,
            max_tokens=800,
        )
        answer = response.choices[0].message.content
        record_stage_execution(
            trace_id=trace_id,
            pipeline="query",
            stage_name="llm_generate",
            status="success",
            latency_ms=int((time.time() - start) * 1000),
        )
        return answer
    except Exception:
        record_stage_execution(
            trace_id=trace_id,
            pipeline="query",
            stage_name="llm_generate",
            status="failure",
            latency_ms=int((time.time() - start) * 1000),
        )
        raise

@traceable
def validate_answer_length(trace_id, answer: str) -> bool:
    start = time.time()
    result = length_guard.validate(answer)
    record_stage_execution(
        trace_id=trace_id,
        pipeline="query",
        stage_name="length_check",
        status="success" if result.validation_passed else "failure",
        latency_ms=int((time.time() - start) * 1000),
    )
    return result.validation_passed

@traceable
def call_nli(trace_id, premise: str, hypothesis: str) -> dict:
    start = time.time()
    try:
        r = http_session.post(
            NLI_URL,
            json={"premise": premise, "hypothesis": hypothesis},
            timeout=60,
        )
        r.raise_for_status()
        record_stage_execution(
            trace_id=trace_id,
            pipeline="query",
            stage_name="contradiction_check",
            status="success",
            latency_ms=int((time.time() - start) * 1000),
        )
        return r.json()
    except Exception:
        record_stage_execution(
            trace_id=trace_id,
            pipeline="query",
            stage_name="contradiction_check",
            status="failure",
            latency_ms=int((time.time() - start) * 1000),
        )
        raise

@traceable(run_type="eval", name="RAGAS Evaluation")
def evaluate_with_ragas(query, answer, top_chunks):
    dataset = Dataset.from_dict({
        "question": [query],
        "answer": [answer],
        "contexts": [[c["text"] for c in top_chunks]],
    })

    result = evaluate(
        dataset=dataset,
        metrics=ragas_metrics,
        llm=evaluation_llm,
        embeddings=ragas_embeddings,
    )

    return {
        "faithfulness": result["faithfulness"][0],
        "answer_relevancy": result["answer_relevancy"][0],
    }


@app.post("/query")
@traceable(run_type="chain", name="RAG Query")
def run_query(req: QueryRequest):
    trace_id = str(uuid.uuid4())
    start_total = time.time()

    try:
        query = req.query

        chunk_scores = search_with_text(
            trace_id, chunks_index, query, TOP_K_CHUNKS
        )
        page_scores = search_with_text(
            trace_id, pages_index, query, TOP_K_PAGES
        )

        chunk_metadata = fetch_chunks_from_neon(trace_id, list(chunk_scores.keys()))

        fused = []
        for chunk_id, chunk_score in chunk_scores.items():
            if chunk_id not in chunk_metadata:
                continue
            meta = chunk_metadata[chunk_id]
            page_score = page_scores.get(str(meta["page_id"]), 0.0)
            fused.append({
                "chunk_id": chunk_id,
                "page_id": meta["page_id"],
                "section": meta["section"],
                "text": meta["text"],
                "fused_score": (W_CHUNK * chunk_score) + (W_PAGE * page_score),
            })

        if not fused:
            record_query_result(
                trace_id,
                query,
                "no_context",
                0,
                0,
                0,
                0.0,
                None,
                None,
                int((time.time() - start_total) * 1000),
            )
            return {
                "query": query,
                "answer": "Not found in knowledge base.",
                "sources": [],
                "images": []
            }

        rerank_scores = call_reranker(trace_id, query, [f["text"] for f in fused])
        for item, score in zip(fused, rerank_scores):
            item["rerank_score"] = score

        fused.sort(key=lambda x: x["rerank_score"], reverse=True)
        top_chunks = fused[:FINAL_TOP_K]

        final_images = []
        if top_chunks:
            top_chunk_page_ids = {c["page_id"] for c in top_chunks}
            image_scores = search_with_text(trace_id, images_index, query, 20)
            if image_scores:
                fetched_images = fetch_images_from_neon(trace_id, list(image_scores.keys()))
                ordered_images = sorted(
                    fetched_images,
                    key=lambda img: image_scores.get(img.get("image_hash"), 0),
                    reverse=True,
                )

                for img in ordered_images:
                    if img.get("page_id") in top_chunk_page_ids:
                        final_images.append({
                            "url": img.get("url"),
                            "page_id": img.get("page_id"),
                            "caption": img.get("caption"),
                        })
                final_images = final_images[:TOP_K_IMAGES]

        context = build_context(top_chunks)
        answer = call_groq_llm(trace_id, query, context)
        nli = call_nli(trace_id, context, answer)
        contradiction_score = nli.get("contradiction", 0.0)

        if contradiction_score >= NLI_CONTRADICTION_THRESHOLD:
            record_query_result(
                trace_id,
                query,
                "contradicted",
                len(top_chunks),
                len(context),
                len(answer),
                contradiction_score,
                None,
                None,
                int((time.time() - start_total) * 1000),
            )
            return {
                "query": query,
                "answer": "LLM response contradicted the knowledge base.",
                "sources": [],
                "images": []
            }

        if not validate_answer_length(trace_id, answer):
            record_query_result(
                trace_id,
                query,
                "invalid_output",
                len(top_chunks),
                len(context),
                len(answer),
                contradiction_score,
                None,
                None,
                int((time.time() - start_total) * 1000),
            )
            return {
                "query": query,
                "answer": "Invalid LLM output.",
                "sources": [],
                "images": []
            }

        faithfulness_score = None
        answer_relevancy_score = None

        if answer.strip() != "Not found in knowledge base.":
            ragas_start = time.time()
            try:
                ragas_result = evaluate_with_ragas(
                    query=query,
                    answer=answer,
                    top_chunks=top_chunks,
                )
                faithfulness_score = ragas_result["faithfulness"]
                answer_relevancy_score = ragas_result["answer_relevancy"]

                record_stage_execution(
                    trace_id=trace_id,
                    pipeline="query",
                    stage_name="ragas_evaluation",
                    status="success",
                    latency_ms=int((time.time() - ragas_start) * 1000),
                )
            except Exception:
                record_stage_execution(
                    trace_id=trace_id,
                    pipeline="query",
                    stage_name="ragas_evaluation",
                    status="failure",
                    latency_ms=int((time.time() - ragas_start) * 1000),
                )

        total_latency_ms = int((time.time() - start_total) * 1000)

        record_query_result(
            trace_id,
            query,
            "success",
            len(top_chunks),
            len(context),
            len(answer),
            contradiction_score,
            faithfulness_score,
            answer_relevancy_score,
            total_latency_ms,
        )

        sources = [
            {
                "page_id": c["page_id"],
                "section": c["section"],
                "text": c["text"],
            }
            for c in top_chunks
        ]

        return {
            "query": query,
            "answer": answer,
            "sources": sources,
            "images": final_images,
        }

    except Exception as e:
        record_query_result(
            trace_id,
            req.query,
            "failure",
            0,
            0,
            0,
            0.0,
            None,
            None,
            int((time.time() - start_total) * 1000),
        )
        raise HTTPException(status_code=500, detail=str(e))

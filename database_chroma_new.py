import os
import sys
import math
import uuid
import collections
import re
import threading
from datetime import datetime
import numpy as np

# Lazy load models to keep startup fast
_model_lock = threading.Lock()
_reranker_lock = threading.Lock()
_model_cache = None
_reranker_cache = None
_chroma_client = None
_chroma_collection = None
_model_exec_lock = threading.Lock()
_db_query_lock = threading.Lock()

CHROMA_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "chroma_data"))
DB_PATH = CHROMA_PATH  # For compatibility with diagnostics pages expecting DB_PATH
HYBRID_THRESHOLD = 0.40  # Semantic search pre-filtering threshold (adjusted for Nomic sensitivity)

try:
    import chromadb
    from chromadb.config import Settings
    from chromadb.utils.embedding_functions import ChromaBm25EmbeddingFunction
    HAS_CHROMA = True
except ImportError:
    HAS_CHROMA = False

def get_transformer_model():
    """Lazy loader for Nomic Embedding model."""
    global _model_cache
    with _model_lock:
        if _model_cache is None:
            try:
                from sentence_transformers import SentenceTransformer
                print("Loading sentence-transformers model: nomic-ai/nomic-embed-text-v1.5...")
                _model_cache = SentenceTransformer("nomic-ai/nomic-embed-text-v1.5", trust_remote_code=True)
                print("Model loaded successfully.")
            except Exception as e:
                print(f"Error loading embedding model: {e}")
                _model_cache = None
    return _model_cache

def get_reranker_model():
    """Lazy loader for Cross-Encoder model."""
    global _reranker_cache
    with _reranker_lock:
        if _reranker_cache is None:
            try:
                from sentence_transformers import CrossEncoder
                print("Loading CrossEncoder: cross-encoder/ms-marco-MiniLM-L-12-v2...")
                _reranker_cache = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-12-v2")
                print("CrossEncoder loaded successfully.")
            except Exception as e:
                print(f"Error loading reranker: {e}")
                _reranker_cache = None
    return _reranker_cache

# Define global class for picklability in PersistentClient
try:
    from chromadb.api.types import EmbeddingFunction, Documents, Embeddings
    class LocalNomicEmbeddingFunction(EmbeddingFunction):
        def __init__(self):
            self.model = None
            self.model_name = "nomic-ai/nomic-embed-text-v1.5"
            
        def __call__(self, input: Documents) -> Embeddings:
            if self.model is None:
                self.model = get_transformer_model()
            processed_input = []
            for x in input:
                if x.startswith("search_query:") or x.startswith("search_document:"):
                    processed_input.append(x)
                else:
                    processed_input.append(f"search_document: {x}")
            with _model_exec_lock:
                return self.model.encode(processed_input).astype(np.float32).tolist()
except ImportError:
    class LocalNomicEmbeddingFunction:
        pass

def get_embedding_function():
    """Returns local embedding function wrapper."""
    return LocalNomicEmbeddingFunction()

def init_db():
    """Initializes ChromaDB collection."""
    _get_chroma_collection()

def _get_chroma_collection():
    """Returns a persistent disk-based ChromaDB collection or None if library is missing."""
    global _chroma_client, _chroma_collection
    if not HAS_CHROMA:
        return None
    try:
        if _chroma_client is None:
            _chroma_client = chromadb.PersistentClient(
                path=CHROMA_PATH,
                settings=Settings(anonymized_telemetry=False)
            )
        if _chroma_collection is None:
            embedding_fn = get_embedding_function()
            _chroma_collection = _chroma_client.get_or_create_collection(
                name="memories",
                metadata={"hnsw:space": "cosine"},
                embedding_function=embedding_fn
            )
        return _chroma_collection
    except Exception as e:
        print(f"Error fetching ChromaDB collection: {e}")
        return None

def save_memory(username: str, tag: str, query: str, response: str, subtag: str = "implicit", timestamp: str = None) -> bool:
    """Saves a memory directly in ChromaDB."""
    collection = _get_chroma_collection()
    if collection is None:
        return False
    
    username = username.strip().lower()
    tag = tag.strip().lower()
    subtag = subtag.strip().lower()
    
    if timestamp is None:
        timestamp = datetime.now().isoformat()
        
    mem_id = uuid.uuid4().hex
    combined_text = f"{query} {response}"
    
    try:
        collection.upsert(
            ids=[mem_id],
            documents=[combined_text],
            metadatas=[{
                "id": mem_id,
                "username": username,
                "tag": tag,
                "subtag": subtag,
                "timestamp": timestamp,
                "query": query,
                "response": response
            }]
        )
        return True
    except Exception as e:
        print(f"Error saving memory to ChromaDB: {e}")
        return False

def save_or_update_explicit_memory(username: str, query: str, response: str) -> bool:
    """Saves or updates an explicit semantic memory (profile choice) directly in ChromaDB."""
    collection = _get_chroma_collection()
    if collection is None:
        return False
        
    username = username.strip().lower()
    tag = "semantic"
    subtag = "explicit"
    timestamp = datetime.now().isoformat()
    
    try:
        # Check if this explicit question already exists for the user
        results = collection.get(
            where={
                "$and": [
                    {"username": username},
                    {"tag": tag},
                    {"subtag": subtag},
                    {"query": query}
                ]
            }
        )
        
        # If exists, reuse the same ID to overwrite it
        if results and "ids" in results and results["ids"]:
            mem_id = results["ids"][0]
        else:
            mem_id = uuid.uuid4().hex
            
        combined_text = f"{query} {response}"
        collection.upsert(
            ids=[mem_id],
            documents=[combined_text],
            metadatas=[{
                "id": mem_id,
                "username": username,
                "tag": tag,
                "subtag": subtag,
                "timestamp": timestamp,
                "query": query,
                "response": response
            }]
        )
        return True
    except Exception as e:
        print(f"Error saving/updating explicit memory in ChromaDB: {e}")
        return False

def get_memories_by_tag(username: str, tag: str) -> list[dict]:
    """Retrieves all memories for a specific user and tag from ChromaDB directly."""
    collection = _get_chroma_collection()
    if collection is None:
        return []
        
    username = username.strip().lower()
    tag = tag.strip().lower()
    
    try:
        results = collection.get(
            where={"$and": [{"username": username}, {"tag": tag}]}
        )
        
        memories = []
        if results and "ids" in results and results["ids"]:
            for idx, str_id in enumerate(results["ids"]):
                meta = results["metadatas"][idx]
                memories.append({
                    "id": str_id,
                    "query": meta.get("query", ""),
                    "response": meta.get("response", ""),
                    "timestamp": meta.get("timestamp", ""),
                    "subtag": meta.get("subtag", "implicit")
                })
            # Sort chronologically (newest first)
            memories.sort(key=lambda x: x["timestamp"], reverse=True)
        return memories
    except Exception as e:
        print(f"Error getting memories from ChromaDB: {e}")
        return []
 
def vector_query_memories(username: str, tag: str, query_text: str, top_k: int = 5, q_emb: list | None = None) -> list[dict]:
    """
    Query memories using pure ChromaDB, with dense cosine similarity,
    lexical BM25 (tanh-scaled), and Cross-Encoder reranking.
    """
    collection = _get_chroma_collection()
    if collection is None:
        return []
        
    username = username.strip().lower()
    tag = tag.strip().lower()
    query_text = query_text.strip()
    
    if not query_text:
        return get_memories_by_tag(username, tag)
        
    try:
        # 1. Fetch all documents for this user & tag to get database size/details
        all_docs = collection.get(
            where={"$and": [{"username": username}, {"tag": tag}]}
        )
        
        if not all_docs or not all_docs["ids"]:
            return []
            
        total_docs = len(all_docs["ids"])
        memories_map = {}
        for idx, str_id in enumerate(all_docs["ids"]):
            meta = all_docs["metadatas"][idx]
            memories_map[str_id] = {
                "id": str_id,
                "query": meta.get("query", ""),
                "response": meta.get("response", ""),
                "timestamp": meta.get("timestamp", ""),
                "subtag": meta.get("subtag", "implicit")
            }
            
        # 2. Calculate Semantic similarity (Cosine)
        semantic_scores = {m_id: 0.0 for m_id in memories_map}
        if q_emb is None:
            embedding_fn = get_embedding_function()
            q_emb = embedding_fn([f"search_query: {query_text}"])[0]
        print(f"Embedded Query: {query_text} - Embedding: {q_emb[:20]}")
        search_results = collection.query(
            query_embeddings=[q_emb],
            n_results=min(25, total_docs),
            where={"$and": [{"username": username}, {"tag": tag}]}
        )
        
        if search_results and "ids" in search_results and search_results["ids"]:
            ids_list = search_results["ids"][0]
            distances_list = search_results["distances"][0] if "distances" in search_results and search_results["distances"] else []
            for idx, str_id in enumerate(ids_list):
                if str_id in semantic_scores:
                    dist = distances_list[idx] if idx < len(distances_list) else 0.0
                    sim = 1.0 - dist
                    semantic_scores[str_id] = max(0.0, min(1.0, sim))
                    
        # 3. Calculate Lexical Similarity (ChromaBm25EmbeddingFunction)
        keyword_scores = {m_id: 0.0 for m_id in memories_map}
        try:
            bm25_ef = ChromaBm25EmbeddingFunction(k=1.5, b=0.75)
            memories_list = list(memories_map.values())
            documents = [f"{m['query']} {m['response']}" for m in memories_list]
            all_texts = documents + [query_text]
            all_embs = bm25_ef(all_texts)
            
            doc_embs = all_embs[:-1]
            query_emb = all_embs[-1]
            
            def get_sparse_dict(emb):
                if isinstance(emb, dict):
                    return dict(zip(emb.get("indices", []), emb.get("values", [])))
                indices = getattr(emb, "indices", [])
                values = getattr(emb, "values", [])
                return dict(zip(indices, values))
                
            q_dict = get_sparse_dict(query_emb)
            for idx, doc_emb in enumerate(doc_embs):
                d_dict = get_sparse_dict(doc_emb)
                score = sum(d_dict.get(term_idx, 0.0) for term_idx in q_dict.keys())
                m_id = memories_list[idx]["id"]
                keyword_scores[m_id] = float(score)
                
            # Normalize with tanh scaling
            for m_id in keyword_scores:
                keyword_scores[m_id] = math.tanh(keyword_scores[m_id] / 3.0)
        except Exception as e:
            print(f"BM25 Error: {e}")
            
        # 4. Select Candidates: Combine top semantic matches and top keyword matches (no pre-filtering threshold)
        by_semantic = sorted(memories_map.items(), key=lambda x: semantic_scores.get(x[0], 0.0), reverse=True)
        by_keyword = sorted(memories_map.items(), key=lambda x: keyword_scores.get(x[0], 0.0), reverse=True)
        
        candidate_ids = set()
        candidates = []
        
        # Interleave to ensure equal representation from both search methods
        for i in range(max(len(by_semantic), len(by_keyword))):
            if i < len(by_semantic):
                m_id, m = by_semantic[i]
                if m_id not in candidate_ids:
                    candidate_ids.add(m_id)
                    sem_s = semantic_scores.get(m_id, 0.0)
                    key_s = keyword_scores.get(m_id, 0.0)
                    h_score = (0.7 * sem_s) + (0.3 * key_s)
                    candidates.append((h_score, sem_s, m))
            if i < len(by_keyword):
                m_id, m = by_keyword[i]
                if m_id not in candidate_ids:
                    candidate_ids.add(m_id)
                    sem_s = semantic_scores.get(m_id, 0.0)
                    key_s = keyword_scores.get(m_id, 0.0)
                    h_score = (0.7 * sem_s) + (0.3 * key_s)
                    candidates.append((h_score, sem_s, m))
            if len(candidates) >= 15:
                break
        
        # 5. Rerank using Cross-Encoder
        reranker = get_reranker_model()
        final_scored = []
        now = datetime.now()
        
        if reranker is not None and len(candidates) > 0:
            try:
                pairs = [(query_text, f"{m['query']} {m['response']}") for _, _, m in candidates]
                with _model_exec_lock:
                    rerank_scores = reranker.predict(pairs)
                
                for idx, (_, sem_s, m) in enumerate(candidates):
                    raw_score = float(rerank_scores[idx])
                    rerank_score = 1.0 / (1.0 + math.exp(-raw_score))
                    
                    # Minimum Semantic Relevance Gate (Cross-Encoder score must be >= 0.002)
                    if rerank_score < 0.002:
                        continue
                        
                    try:
                        dt = datetime.fromisoformat(m["timestamp"])
                        age_hours = (now - dt).total_seconds() / 3600.0
                        if age_hours < 0:
                            age_hours = 0.0
                        recency_score = 1.0 / (1.0 + age_hours / 168.0)
                    except Exception:
                        recency_score = 0.0
                        
                    is_explicit = 1.0 if m["subtag"] == "explicit" else 0.0
                    metadata_boost = (0.6 * is_explicit) + (0.4 * recency_score)
                    final_score = (0.7 * rerank_score) + (0.3 * metadata_boost)
                    
                    # Add to final list for ranking
                    final_scored.append((final_score, m))
            except Exception as e:
                print(f"Reranking error: {e}")
                reranker = None
                
        if reranker is None:
            print("Reranker is None, or not set")
            for h_score, sem_s, m in candidates:
                # Minimum Semantic Relevance Gate for fallback (Hybrid score or strong Bi-Encoder semantic similarity)
                if h_score < 0.25 and sem_s < 0.55:
                    continue
                    
                try:
                    dt = datetime.fromisoformat(m["timestamp"])
                    age_hours = (now - dt).total_seconds() / 3600.0
                    if age_hours < 0:
                        age_hours = 0.0
                    recency_score = 1.0 / (1.0 + age_hours / 168.0)
                except Exception:
                    recency_score = 0.0
                is_explicit = 1.0 if m["subtag"] == "explicit" else 0.0
                metadata_boost = (0.6 * is_explicit) + (0.4 * recency_score)
                final_score = (0.7 * h_score) + (0.3 * metadata_boost)
                final_scored.append((final_score, m))
                
        final_scored.sort(key=lambda x: x[0], reverse=True)
        return [item[1] for item in final_scored[:top_k]]
    except Exception as e:
        print(f"Query Error: {e}")
        return []

def get_db_status() -> dict:
    """Returns the current status of the vector database engine."""
    collection = _get_chroma_collection()
    if collection is None:
        return {
            "chroma_available": False,
            "chroma_library": "Missing",
            "sentence_transformers_library": "Missing",
            "model_loaded": "No",
            "total_records": 0,
            "records_with_vectors": 0,
            "active_users": 0,
            "memory_tags": [],
            "engine_mode": "Missing"
        }
        
    try:
        total = collection.count()
        
        # Inspect metadata to get active users and tags
        results = collection.get()
        users = set()
        tags = set()
        if results and "metadatas" in results and results["metadatas"]:
            for meta in results["metadatas"]:
                users.add(meta.get("username"))
                tags.add(meta.get("tag"))
                
        return {
            "chroma_available": HAS_CHROMA,
            "chroma_library": "Available" if HAS_CHROMA else "Missing",
            "sentence_transformers_library": "Available",
            "model_loaded": "Yes (Local SentenceTransformer)" if _model_cache is not None else "Lazy Loaded",
            "total_records": total,
            "records_with_vectors": total,
            "active_users": len(users),
            "memory_tags": list(tags),
            "engine_mode": "ChromaDB Standalone (Persistent Vector Store)"
        }
    except Exception:
        return {
            "chroma_available": HAS_CHROMA,
            "chroma_library": "Available" if HAS_CHROMA else "Missing",
            "sentence_transformers_library": "Available",
            "model_loaded": "No",
            "total_records": 0,
            "records_with_vectors": 0,
            "active_users": 0,
            "memory_tags": [],
            "engine_mode": "ChromaDB Initialization Error"
        }

def warm_up_cache(username: str):
    """Warm up cache is a lazy-loading trigger for standalone persistent database."""
    import threading
    def _warm():
        _get_chroma_collection()
        get_transformer_model()
        get_reranker_model()
    threading.Thread(target=_warm, daemon=True).start()

def vector_query_semantic_store(username: str, query_text: str, top_k: int = 5, q_emb: list | None = None) -> list[dict]:
    """
    Query memories from semantic_memory_store collection using ChromaDB vector search.
    """
    import semantic_memory_store
    collection = semantic_memory_store._get_collection()
    if collection is None:
        return []
        
    username = username.strip().lower()
    query_text = query_text.strip()
    
    if not query_text:
        return get_semantic_store_memories_mapped(username)[:top_k]
        
    try:
        # Fetch all documents for this user to get database size/details
        all_docs = collection.get(
            where={"username": username}
        )
        
        if not all_docs or not all_docs["ids"]:
            return []
            
        total_docs = len(all_docs["ids"])
        memories_map = {}
        for idx, str_id in enumerate(all_docs["ids"]):
            meta = all_docs["metadatas"][idx]
            memories_map[str_id] = {
                "id": str_id,
                "query": "",
                "response": meta.get("memory", ""),
                "timestamp": meta.get("created_timestamp", ""),
                "subtag": meta.get("sub_tag", "implicit"),
                "category": meta.get("category", ""),
                "evidence": meta.get("evidence", "")
            }
            
        # Semantic similarity query
        if q_emb is None:
            embedding_fn = get_embedding_function()
            q_emb = embedding_fn([f"search_query: {query_text}"])[0]
        search_results = collection.query(
            query_embeddings=[q_emb],
            n_results=min(25, total_docs),
            where={"username": username}
        )
        
        candidates = []
        if search_results and "ids" in search_results and search_results["ids"]:
            ids_list = search_results["ids"][0]
            distances_list = search_results["distances"][0] if "distances" in search_results and search_results["distances"] else []
            for idx, str_id in enumerate(ids_list):
                if str_id in memories_map:
                    dist = distances_list[idx] if idx < len(distances_list) else 0.0
                    sim = 1.0 - dist
                    candidates.append((sim, memories_map[str_id]))
                    
        candidates.sort(key=lambda x: x[0], reverse=True)
        return [item[1] for item in candidates[:top_k]]
    except Exception as e:
        print(f"vector_query_semantic_store error: {e}")
        return []

def get_semantic_store_memories_mapped(username: str) -> list[dict]:
    import semantic_memory_store
    raw = semantic_memory_store.get_semantic_memories(username)
    mapped = []
    for m in raw:
        mapped.append({
            "id": m.get("source_id", uuid.uuid4().hex),
            "query": "",
            "response": m.get("memory", ""),
            "timestamp": m.get("created_timestamp", ""),
            "subtag": m.get("sub_tag", "implicit"),
            "category": m.get("category", ""),
            "evidence": m.get("evidence", "")
        })
    return mapped

"""
cognee_integration.py
=====================
Cognee exercise knowledge-graph integration for the AI Workout Coach Streamlit app.
All core logic is derived from cognee_retrieval_eval_4.ipynb — that notebook is NOT modified.

What this module does
---------------------
- Locates existing Cognee databases (from notebook runs) or prepares a fresh location.
- Checks whether exercise data is already ingested (fast file-level check on LanceDB tables).
- Ingests exercises from JSON into Cognee (Neo4j graph + LanceDB vectors) on demand.
- Provides synchronous wrappers around every async Cognee call so Streamlit can use them.
- Returns full-graph HTML and per-query context-subgraph HTML for Streamlit components.

Import in app.py (safe, no crash if cognee isn't installed):
    try:
        import cognee_integration as _cog
        COGNEE_AVAILABLE = _cog.COGNEE_IMPORTABLE
    except Exception:
        _cog = None
        COGNEE_AVAILABLE = False
"""

import os
import pathlib
import time
import json
import base64
import asyncio
import threading
from contextlib import contextmanager
from typing import Optional, List, Tuple, Literal

try:
    from dotenv import load_dotenv
    load_dotenv(pathlib.Path(__file__).parent.resolve() / ".env", override=False)
except ImportError:
    pass

# ── Paths — fully dynamic, no hardcoded local paths ───────────────────────────
# Override via env var COGNEE_BASE_DIR (useful for local dev pointing at a
# pre-populated directory). Defaults to a .cognee_system/ folder next to app.py.
_APP_DIR = pathlib.Path(__file__).parent.resolve()

COGNEE_BASE   = pathlib.Path(os.environ.get("COGNEE_BASE_DIR", str(_APP_DIR))).resolve()
COGNEE_DATA   = COGNEE_BASE / ".cognee_data"
COGNEE_SYSTEM = COGNEE_BASE / ".cognee_system"
ARTIFACTS_DIR   = COGNEE_BASE / ".artifacts"
GRAPHS_DIR      = ARTIFACTS_DIR / "graphs"
FULL_GRAPH_HTML = ARTIFACTS_DIR / "exercises_graph.html"
INGEST_FLAG     = COGNEE_SYSTEM / "ingestion_complete.flag"

for _d in [COGNEE_DATA, COGNEE_SYSTEM, ARTIFACTS_DIR, GRAPHS_DIR]:
    _d.mkdir(parents=True, exist_ok=True)

# ── Seed env vars BEFORE importing cognee ────────────────────────────────────
# cognee/__init__.py calls dotenv.load_dotenv(override=True) and then
# get_base_config() is cached via @lru_cache using pydantic_settings.
# pydantic_settings reads env vars at construction time, so if
# DATA_ROOT_DIRECTORY / SYSTEM_ROOT_DIRECTORY aren't set yet, it falls back to
# get_absolute_path(".cognee_system") which resolves to the site-packages dir.
# Setting them here (with setdefault so explicit env overrides still win)
# guarantees the cached BaseConfig uses our project paths from the first call.
os.environ.setdefault("DATA_ROOT_DIRECTORY", str(COGNEE_DATA))
os.environ.setdefault("SYSTEM_ROOT_DIRECTORY", str(COGNEE_SYSTEM))

# Shared dict written by the ingest worker thread, read by the Streamlit main thread.
# Avoids calling st.* APIs across thread boundaries.
_ingest_progress: dict = {"msg": "", "pct": 0.0}

# ── Async → sync bridge ────────────────────────────────────────────────────────
# Cognee (and its dependencies — neo4j async driver, lancedb, litellm,
# asyncio.Lock/Queue, connection pools, etc.) caches asyncio primitives bound
# to the loop on which they were first created. Spawning a fresh loop per call
# triggers "got Future attached to a different loop" on the 2nd query.
#
# Solution: one persistent background thread running ONE event loop forever;
# every coroutine is scheduled onto that single loop.
_LOOP: Optional[asyncio.AbstractEventLoop] = None
_LOOP_THREAD: Optional[threading.Thread] = None
_LOOP_LOCK = threading.Lock()


def _ensure_loop() -> asyncio.AbstractEventLoop:
    global _LOOP, _LOOP_THREAD
    with _LOOP_LOCK:
        if _LOOP is not None and _LOOP.is_running():
            return _LOOP

        ready = threading.Event()

        def _runner():
            global _LOOP
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            _LOOP = loop
            
            # Pre-import Cognee and all its submodules on this loop thread
            # so that any loop-bound cached asyncio primitives (e.g. connections, clients)
            # are tied to this loop instead of Streamlit's Tornado loop.
            try:
                # Core libraries
                import litellm
                import lancedb
                import neo4j
                
                # Cognee modules
                import cognee
                import cognee.low_level
                import cognee.pipelines
                import cognee.tasks.storage
                import cognee.modules.users.methods
                import cognee.modules.data.methods
                import cognee.infrastructure.engine.models.Edge
                import cognee.infrastructure.llm.LLMGateway
                import cognee.modules.retrieval.graph_completion_retriever
                import cognee.modules.graph.cognee_graph.CogneeGraphElements
                import cognee.modules.search.methods.search
                import cognee.modules.search.methods.get_retriever_output
                import cognee.modules.search.methods.get_search_type_retriever_instance
                
                # Database engines and adapters
                import cognee.infrastructure.databases.graph.get_graph_engine
                import cognee.infrastructure.databases.vector.get_vector_engine
                import cognee.infrastructure.databases.relational.get_relational_engine
                import cognee.infrastructure.databases.graph.neo4j_driver.adapter
            except Exception:
                pass
                
            ready.set()
            try:
                loop.run_forever()
            finally:
                loop.close()

        _LOOP_THREAD = threading.Thread(
            target=_runner, name="cognee-asyncio-loop", daemon=True
        )
        _LOOP_THREAD.start()
        ready.wait()
        return _LOOP


# Start the background event loop immediately during module loading
_ensure_loop()


def run_async(coro):
    """
    Execute an async coroutine from Streamlit's synchronous context on a single
    long-lived background event loop, so cached asyncio primitives inside
    Cognee/neo4j/lancedb stay bound to the same loop across calls.
    """
    loop = _ensure_loop()
    fut = asyncio.run_coroutine_threadsafe(coro, loop)
    return fut.result()

# ── Cognee imports ─────────────────────────────────────────────────────────────
try:
    import cognee
    from cognee import config, search, SearchType, visualize_graph
    from cognee.low_level import setup, DataPoint
    from cognee.pipelines import run_tasks, Task
    from cognee.tasks.storage import add_data_points
    from cognee.tasks.storage.index_graph_edges import index_graph_edges
    from cognee.modules.users.methods import get_default_user
    from cognee.modules.data.methods import load_or_create_datasets
    from cognee.infrastructure.engine.models.Edge import Edge
    from cognee.infrastructure.llm.LLMGateway import LLMGateway
    from cognee.modules.retrieval.graph_completion_retriever import GraphCompletionRetriever
    from cognee.modules.graph.cognee_graph.CogneeGraphElements import Edge as CogneeEdge
    from pydantic import BaseModel
    COGNEE_IMPORTABLE = True
    _IMPORT_ERROR = ""
except Exception as _ie:
    COGNEE_IMPORTABLE = False
    _IMPORT_ERROR = str(_ie)

# ── Environment configuration ──────────────────────────────────────────────────
def _resolve_api_key(api_key: Optional[str]) -> str:
    if api_key:
        return api_key
    return (
        os.environ.get("LLM_API_KEY")
        or os.environ.get("GEMINI_API_KEY")
        or ""
    )


def configure_env(api_key: Optional[str] = None) -> None:
    """Apply all env vars required by Cognee; reads from .env when api_key is omitted."""
    key = _resolve_api_key(api_key)
    # Always assign (not setdefault) so we override any stale env vars from the process
    env_updates = {
        "LLM_API_KEY":             key,
        "LLM_PROVIDER":            "gemini",
        "LLM_MODEL":               "gemini/gemini-flash-lite-latest",
        "EMBEDDING_API_KEY":       key,
        "EMBEDDING_PROVIDER":      "gemini",
        "EMBEDDING_MODEL":         "gemini/gemini-embedding-001",
        "EMBEDDING_DIMENSIONS":    "768",
        "GRAPH_DATABASE_PROVIDER": os.environ.get("GRAPH_DATABASE_PROVIDER", "neo4j"),
        "GRAPH_DATABASE_URL":      os.environ.get("GRAPH_DATABASE_URL", ""),
        "GRAPH_DATABASE_USERNAME": os.environ.get("GRAPH_DATABASE_USERNAME", "neo4j"),
        "GRAPH_DATABASE_PASSWORD": os.environ.get("GRAPH_DATABASE_PASSWORD", ""),
        "GRAPH_DATABASE_NAME":     os.environ.get("GRAPH_DATABASE_NAME", "neo4j"),
    }
    for k, v in env_updates.items():
        os.environ[k] = v
    os.environ["ENABLE_BACKEND_ACCESS_CONTROL"] = "false"
    os.environ["COGNEE_SKIP_CONNECTION_TEST"]   = "true"
    if COGNEE_IMPORTABLE:
        config.data_root_directory(str(COGNEE_DATA))
        config.system_root_directory(str(COGNEE_SYSTEM))
        # get_llm_config() is @lru_cache — env var changes above won't affect an already-cached
        # instance. Mutate the cached objects directly so Gemini is always used.
        config.set_llm_provider("gemini")
        config.set_llm_model("gemini/gemini-flash-lite-latest")
        config.set_llm_api_key(key)
        config.set_sidebar = None # No effect, but clear
        config.set_embedding_provider("gemini")
        config.set_embedding_model("gemini/gemini-embedding-001")
        config.set_embedding_api_key(key)
        config.set_embedding_dimensions(768)
        # get_graph_config() is also @lru_cache — env var changes above won't reach an
        # already-cached GraphConfig instance. Mutate it directly so Neo4j credentials
        # are always up-to-date before setup() initialises the connection pool.
        config.set_graph_db_config({
            "graph_database_provider": os.environ.get("GRAPH_DATABASE_PROVIDER", "neo4j"),
            "graph_database_url":      os.environ.get("GRAPH_DATABASE_URL", ""),
            "graph_database_username": os.environ.get("GRAPH_DATABASE_USERNAME", "neo4j"),
            "graph_database_password": os.environ.get("GRAPH_DATABASE_PASSWORD", ""),
            "graph_database_name":     os.environ.get("GRAPH_DATABASE_NAME", "neo4j"),
        })


# ══════════════════════════════════════════════════════════════════════════════
# DataPoint models — from cognee_retrieval_eval_4.ipynb
# ══════════════════════════════════════════════════════════════════════════════
if COGNEE_IMPORTABLE:

    class Muscle(DataPoint):
        name: str
        metadata: dict = {"index_fields": ["name"]}

    class SecondaryMuscle(DataPoint):
        name: str
        metadata: dict = {"index_fields": ["name"]}

    class Equipment(DataPoint):
        name: str
        metadata: dict = {"index_fields": ["name"]}

    class Level(DataPoint):
        name: str
        metadata: dict = {"index_fields": ["name"]}

    class Category(DataPoint):
        name: str
        metadata: dict = {"index_fields": ["name"]}

    class Target(DataPoint):
        name: str
        metadata: dict = {"index_fields": ["name"]}

    class Exercise(DataPoint):
        exercise_id: str
        name: str
        instructions: str
        has_level:            Tuple[Edge, Level]
        in_category:          Tuple[Edge, Category]
        requires:             Tuple[Edge, Equipment]
        has_primary_muscle:   Tuple[Edge, Muscle]
        has_secondary_muscle: Tuple[Edge, List[SecondaryMuscle]] = (
            Edge(relationship_type="HAS_SECONDARY_MUSCLE"), []
        )
        targets: Tuple[Edge, Target]
        metadata: dict = {"index_fields": ["name", "instructions"]}

    # ── ExerciseGraphBuilder — from notebook ───────────────────────────────────
    class ExerciseGraphBuilder:
        def __init__(self):
            self._muscle_cache:    dict = {}
            self._target_cache:    dict = {}
            self._equipment_cache: dict = {}
            self._level_cache:     dict = {}
            self._category_cache:  dict = {}
            self._secondary_cache: dict = {}

        @staticmethod
        def load_exercises(path: str) -> list[dict]:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)

        def _get_or_create(self, cache: dict, cls, name: str):
            key = name.strip().lower()
            if key not in cache:
                cache[key] = cls(name=key)
            return cache[key]

        def build_exercise_node(self, ex: dict) -> "Exercise":
            secondary = [
                self._get_or_create(self._secondary_cache, SecondaryMuscle, n)
                for n in ex.get("secondary_muscles", [])
            ]
            return Exercise(
                exercise_id=ex["id"],
                name=ex["name"],
                instructions=ex["instructions"],
                has_level=(
                    Edge(edge_text="which level it belongs to"),
                    self._get_or_create(self._level_cache, Level, ex["level"]),
                ),
                in_category=(
                    Edge(edge_text="which body part it belongs to"),
                    self._get_or_create(self._category_cache, Category, ex["category"]),
                ),
                requires=(
                    Edge(edge_text="which equipment it requires"),
                    self._get_or_create(self._equipment_cache, Equipment, ex["equipment"]),
                ),
                targets=(
                    Edge(edge_text="which muscle it targets"),
                    self._get_or_create(self._target_cache, Target, ex["target"]),
                ),
                has_primary_muscle=(
                    Edge(edge_text="which primary muscle it works"),
                    self._get_or_create(self._muscle_cache, Muscle, ex["muscle_group"]),
                ),
                has_secondary_muscle=(
                    Edge(edge_text="which secondary muscles it works"),
                    secondary,
                ),
            )

        def build_all(self, exercises: list[dict]) -> list["Exercise"]:
            seen: set[str] = set()
            nodes: list     = []
            for ex in exercises:
                if ex["id"] not in seen:
                    seen.add(ex["id"])
                    nodes.append(self.build_exercise_node(ex))
            return nodes

    # ── CogneeExerciseStore — from notebook ────────────────────────────────────
    async def _add_exercise_data_points(data, ctx=None):
        return await add_data_points(data, embed_triplets=True)

    class CogneeExerciseStore:
        def __init__(self, dataset_name: str = "exercises_demo"):
            self.dataset_name = dataset_name

        async def push(self, nodes: list) -> None:
            user     = await get_default_user()
            datasets = await load_or_create_datasets([self.dataset_name], [], user)
            pipeline = run_tasks(
                [Task(_add_exercise_data_points)],
                datasets[0].id, nodes, user, "exercises_byog_pipeline",
            )
            async for _ in pipeline:
                pass
            await index_graph_edges()

    # ── TokenUsageTracker — from notebook ──────────────────────────────────────
    class TokenUsageTracker:
        def __init__(self):
            self.log: list[dict] = []
            self._phase = "unassigned"
            self._installed = False
            self._originals: dict = {}

        def install(self):
            import litellm
            if self._installed:
                return
            self._originals = {
                "completion":  litellm.completion,
                "acompletion": litellm.acompletion,
                "embedding":   litellm.embedding,
                "aembedding":  litellm.aembedding,
            }
            orig = self._originals

            def _usage(r):
                u = getattr(r, "usage", None) or (r.get("usage") if isinstance(r, dict) else None)
                if not u:
                    return 0, 0, 0
                g = u.get if isinstance(u, dict) else (lambda k, d=0: getattr(u, k, d))
                p = g("prompt_tokens", 0) or 0
                c = g("completion_tokens", 0) or 0
                return p, c, g("total_tokens", 0) or (p + c)

            def _rec(model, ct, resp, el):
                p, c, t = _usage(resp)
                self.log.append({
                    "phase": self._phase, "model": model, "call_type": ct,
                    "prompt_tokens": p, "completion_tokens": c,
                    "total_tokens": t, "elapsed_sec": el,
                })

            def pc(*a, **kw):
                t0 = time.perf_counter(); r = orig["completion"](*a, **kw)
                _rec(kw.get("model", "?"), "completion", r, time.perf_counter() - t0); return r

            async def pac(*a, **kw):
                t0 = time.perf_counter(); r = await orig["acompletion"](*a, **kw)
                _rec(kw.get("model", "?"), "completion", r, time.perf_counter() - t0); return r

            def pe(*a, **kw):
                t0 = time.perf_counter(); r = orig["embedding"](*a, **kw)
                _rec(kw.get("model", "?"), "embedding", r, time.perf_counter() - t0); return r

            async def pae(*a, **kw):
                t0 = time.perf_counter(); r = await orig["aembedding"](*a, **kw)
                _rec(kw.get("model", "?"), "embedding", r, time.perf_counter() - t0); return r

            litellm.completion  = pc
            litellm.acompletion = pac
            litellm.embedding   = pe
            litellm.aembedding  = pae
            self._installed = True

        @contextmanager
        def phase(self, name: str):
            prev, self._phase = self._phase, name
            try:
                yield
            finally:
                self._phase = prev

        def summarize(self, name: str) -> dict:
            es = [e for e in self.log if e["phase"] == name]
            return {
                "calls":        len(es),
                "total_tokens": sum(e["total_tokens"] for e in es),
            }

    # ── QuestionRouter — from notebook ─────────────────────────────────────────
    class QuestionScope(BaseModel):
        reasoning: str
        scope: Literal["local", "instruction", "multi_hop", "global"]

    class QuestionRouter:
        PROMPT = """
Classify the fitness-exercise question into exactly one scope:
- "local": about ONE specific exercise, muscle, or fact — NOT asking for step-by-step directions.
- "instruction": explicitly asks HOW to perform an exercise, steps/instructions/form.
- "multi_hop": combines two or more constraints (injury + body part, equipment + goal, "X but not Y").
- "global": broad dataset-wide view — summary, count, category list, "overview", "all".
Give brief reasoning, then exactly one label.
"""
        MAP = {
            "local":       SearchType.GRAPH_COMPLETION,
            "instruction": SearchType.TRIPLET_COMPLETION,
            "multi_hop":   SearchType.GRAPH_COMPLETION_COT,
            "global":      SearchType.GRAPH_SUMMARY_COMPLETION,
        }

        def __init__(self, tracker: "TokenUsageTracker"):
            self.tracker = tracker

        async def classify(self, question: str) -> "QuestionScope":
            with self.tracker.phase("classify"):
                return await LLMGateway.acreate_structured_output(
                    text_input=question,
                    system_prompt=self.PROMPT,
                    response_model=QuestionScope,
                )

        def search_type_for(self, scope: str) -> SearchType:
            return self.MAP[scope]

    # ── GraphContextRetriever — from notebook ──────────────────────────────────
    class GraphContextRetriever:
        def __init__(self, tracker: "TokenUsageTracker", top_k: int = 5):
            self.tracker = tracker
            self.top_k   = top_k

        @staticmethod
        def _node_label(node) -> str:
            a = node.attributes
            return a.get("name") or (a.get("text", "")[:40] + "…") or "Unnamed"

        @staticmethod
        def _edge_label(edge) -> str:
            return (
                edge.attributes.get("relationship_type")
                or edge.attributes.get("relationship_name")
                or edge.attributes.get("edge_text")
                or "related_to"
            )

        async def get_triples(self, question: str) -> list[tuple[str, str, str]]:
            with self.tracker.phase("ctx_retrieval"):
                r     = GraphCompletionRetriever(top_k=self.top_k)
                edges = await r.get_retrieved_objects(query=question)
            return [
                (self._node_label(e.node1), self._edge_label(e), self._node_label(e.node2))
                for e in (edges or [])
                if isinstance(e, CogneeEdge)
            ]


# ── QA system prompt — from notebook ──────────────────────────────────────────
EXERCISE_QA_SYSTEM_PROMPT = """
You are a fitness knowledge assistant answering questions using ONLY the exercise graph context provided.

Rules:
1. Use exact terminology from the context (e.g. "pectorals", "glutes", "body weight", "beginner").
2. Only mention equipment, muscles, or exercises that appear in the provided context.
3. Respect constraints the question implies (injury, no equipment, target body part).
4. Be concise but always name specific exercise(s) when relevant.
5. If context is insufficient, say so plainly instead of guessing.
"""


def _answer_to_str(results) -> str:
    if not results:
        return ""
    parts = []
    for r in results:
        if isinstance(r, str):
            parts.append(r)
        elif isinstance(r, dict):
            parts.append(" ".join(str(v) for v in r.values()))
        else:
            parts.append(str(r))
    return " ".join(parts)


# ══════════════════════════════════════════════════════════════════════════════
# Public synchronous API (for Streamlit)
# ══════════════════════════════════════════════════════════════════════════════

def _lance_table_exists() -> bool:
    """Local fallback check: True iff the LanceDB exercise table is on disk."""
    lance_table = COGNEE_SYSTEM / "databases" / "cognee.lancedb" / "Exercise_name.lance"
    return lance_table.exists()


def _triplet_table_exists() -> bool:
    """True iff the LanceDB Triplet_text collection has actual data files (not just an empty stub directory)."""
    triplet_table = COGNEE_SYSTEM / "databases" / "cognee.lancedb" / "Triplet_text.lance"
    if not triplet_table.is_dir():
        return False
    # A real LanceDB table has .txn data files; an empty stub only has _versions/
    return any(triplet_table.rglob("*.txn"))


def data_exists_in_cognee(api_key: Optional[str] = None) -> bool:
    """
    Returns True when exercise data has been successfully ingested before.

    Short-circuits to True when the disk sentinel flag AND the LanceDB triplet
    table both exist — this survives server restarts and prevents Neo4j returning
    an empty result set (e.g. after a Neo4j reset) from triggering re-ingestion.
    Falls back to a live Neo4j check only on the very first run (no flag yet).
    """
    if not COGNEE_IMPORTABLE:
        return False

    # Fast path: sentinel file written after a completed ingest + LanceDB intact.
    if INGEST_FLAG.exists() and _triplet_table_exists():
        return True

    # If the triplet collection is missing, always re-ingest regardless of Neo4j state.
    if not _triplet_table_exists():
        return False

    async def _check() -> bool:
        configure_env(api_key)
        try:
            await setup()
            from cognee.infrastructure.databases.graph import get_graph_engine
            engine = await get_graph_engine()
            nodes, _edges = await engine.get_graph_data()
            return bool(nodes)
        except Exception:
            return _lance_table_exists()

    try:
        return run_async(_check())
    except Exception:
        return _lance_table_exists()


# Path to the bundled exercises JSON file shipped with the app.
BUNDLED_JSON = _APP_DIR / "exercises_500_transformed.json"


def auto_ingest_if_needed(api_key: Optional[str] = None) -> str:
    """
    Ingest the bundled 'exercises_500_transformed.json' into Cognee exactly once.
    If data already exists this is a no-op (returns immediately).
    Raises FileNotFoundError if the bundled JSON is missing.
    """
    if data_exists_in_cognee(api_key):
        return "already_ingested"
    if not BUNDLED_JSON.exists():
        raise FileNotFoundError(
            f"Bundled exercise JSON not found: {BUNDLED_JSON}\n"
            "Place 'exercises_500_transformed.json' next to app.py."
        )
    return ingest_exercises(api_key, json_path=str(BUNDLED_JSON))


def load_graph_from_neo4j(api_key: Optional[str] = None) -> str:
    """
    Always pull the latest graph from Neo4j via Cognee's visualize_graph and
    return the resulting HTML as a UTF-8 string for st.components.html().
    Reads the file in UTF-8 (cognee's writer can default to cp1252 on Windows)
    and falls back to the in-memory return value if the file is missing.
    """
    if not COGNEE_IMPORTABLE:
        raise RuntimeError(f"Cognee not importable: {_IMPORT_ERROR}")

    async def _generate():
        configure_env(api_key)
        await setup()
        ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
        # Remove any stale artifact so we know any present file is fresh.
        try:
            if FULL_GRAPH_HTML.exists():
                FULL_GRAPH_HTML.unlink()
        except Exception:
            pass

        html = await visualize_graph(str(FULL_GRAPH_HTML))

        if FULL_GRAPH_HTML.exists() and FULL_GRAPH_HTML.stat().st_size > 500:
            try:
                return FULL_GRAPH_HTML.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                return FULL_GRAPH_HTML.read_text(encoding="cp1252", errors="replace")
        if isinstance(html, str) and len(html) > 500:
            return html
        raise RuntimeError(
            "Graph generation returned no data. "
            "Ensure Neo4j is reachable and the dataset has been ingested."
        )

    return run_async(_generate())


def init_cognee(api_key: Optional[str] = None) -> None:
    """Configure Cognee env vars + call setup() without touching any data."""
    if not COGNEE_IMPORTABLE:
        raise RuntimeError(f"Cognee not importable: {_IMPORT_ERROR}")

    async def _init():
        configure_env(api_key)
        await setup()

    run_async(_init())


def ingest_exercises(api_key: Optional[str] = None,
                     json_bytes: Optional[bytes] = None,
                     json_path: Optional[str] = None) -> str:
    """
    Sync: ingest exercises into Cognee (Neo4j graph + LanceDB vectors),
    then regenerate the full-graph HTML file.  Returns a status message.

    Data source priority:
      1. json_bytes  — raw bytes from st.file_uploader  (Streamlit Cloud safe)
      2. json_path   — absolute path string              (local dev only)

    Progress is written to _ingest_progress (thread-safe dict) so the
    Streamlit main thread can display it without crossing thread boundaries.
    """
    if not COGNEE_IMPORTABLE:
        raise RuntimeError(f"Cognee not importable: {_IMPORT_ERROR}")
    if json_bytes is None and json_path is None:
        raise ValueError(
            "Provide either json_bytes (from st.file_uploader) "
            "or json_path (absolute path to exercises JSON)."
        )

    async def _ingest():
        configure_env(api_key)
        await setup()

        _ingest_progress["msg"] = "Parsing exercises JSON…"
        _ingest_progress["pct"] = 0.05

        if json_bytes is not None:
            raw = json.loads(json_bytes.decode("utf-8"))
        else:
            with open(json_path, "r", encoding="utf-8") as f:
                raw = json.load(f)

        nodes = ExerciseGraphBuilder().build_all(raw)

        _ingest_progress["msg"] = f"Ingesting {len(nodes)} exercises into Neo4j + LanceDB…"
        _ingest_progress["pct"] = 0.15

        await CogneeExerciseStore("exercises_demo").push(nodes)

        _ingest_progress["msg"] = "Building full graph visualisation…"
        _ingest_progress["pct"] = 0.92

        ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
        await visualize_graph(str(FULL_GRAPH_HTML))

        _ingest_progress["msg"] = "Done."
        _ingest_progress["pct"] = 1.0
        INGEST_FLAG.touch()
        return f"Successfully ingested {len(nodes)} exercises."

    return run_async(_ingest())


def query_exercise_graph(question: str, api_key: Optional[str] = None) -> dict:
    """
    Sync: classify question → search Cognee → retrieve context triples.
    Returns dict: {answer, scope, search_type, triples, total_tokens}.
    """
    if not COGNEE_IMPORTABLE:
        raise RuntimeError(f"Cognee not importable: {_IMPORT_ERROR}")

    async def _query():
        configure_env(api_key)
        await setup()

        tracker      = TokenUsageTracker()
        tracker.install()
        router       = QuestionRouter(tracker)
        ctx_retriever = GraphContextRetriever(tracker, top_k=5)

        cls    = await router.classify(question)
        scope  = cls.scope
        stype  = router.search_type_for(scope)
        phase  = f"ui::{stype.value}"

        with tracker.phase(phase):
            try:
                raw = await search(
                    query_text=question,
                    query_type=stype,
                    system_prompt=EXERCISE_QA_SYSTEM_PROMPT,
                )
            except Exception as _e:
                err = str(_e)
                # Surface Neo4j connectivity failures clearly before checking for index issues.
                if "Cannot resolve address" in err or "ServiceUnavailable" in err or "7687" in err:
                    raise RuntimeError(
                        "Cannot reach the Neo4j graph database. "
                        "Please resume the AuraDB instance at console.neo4j.io and retry."
                    ) from _e
                if "Triplet_text" in err or "TRIPLET_COMPLETION" in err or "create_triplet_embeddings" in err:
                    raise RuntimeError(
                        "The triplet vector index is missing. "
                        "Please re-ingest the exercise data so triplet embeddings are rebuilt."
                    ) from _e
                raise

        answer  = _answer_to_str(raw) or "(No answer returned from Cognee.)"
        triples = await ctx_retriever.get_triples(question) if _answer_to_str(raw) else []

        # Flag: the retriever always returns top-K nodes via semantic similarity,
        # even when they are not relevant. The LLM is the authoritative signal for
        # whether those nodes actually grounded the answer.  When it says "no context",
        # suppress the triples so the caller sees an empty list and hides the subgraph.
        _NO_CONTEXT_PHRASES = (
            "does not contain information",
            "context does not contain",
            "context is insufficient",
            "no information",
            "not in the provided context",
            "cannot answer",
        )
        has_graph_context = bool(triples) and not any(
            p in answer.lower() for p in _NO_CONTEXT_PHRASES
        )

        usage   = tracker.summarize(phase)

        return {
            "answer":            answer,
            "scope":             scope,
            "search_type":       stype.value,
            "triples":           triples if has_graph_context else [],
            "has_graph_context": has_graph_context,
            "total_tokens":      usage["total_tokens"],
        }

    return run_async(_query())


def get_prebuilt_graph_html() -> str:
    """Read the pre-committed graph HTML from disk. No API key, no Neo4j call."""
    if not FULL_GRAPH_HTML.exists():
        return ""
    try:
        return FULL_GRAPH_HTML.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return FULL_GRAPH_HTML.read_text(encoding="cp1252", errors="replace")


def load_full_graph_html(api_key: Optional[str] = None) -> str:
    """
    Sync: return the full-graph HTML string.
    Loads existing file if present; regenerates from Cognee if missing.
    """
    if not COGNEE_IMPORTABLE:
        raise RuntimeError(f"Cognee not importable: {_IMPORT_ERROR}")

    async def _load():
        configure_env(api_key)
        if not FULL_GRAPH_HTML.exists():
            await setup()
            ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
            await visualize_graph(str(FULL_GRAPH_HTML))
        return FULL_GRAPH_HTML.read_text(encoding="utf-8")

    return run_async(_load())


def regenerate_full_graph_html(api_key: Optional[str] = None) -> str:
    """Sync: always regenerate the full-graph HTML from Cognee."""
    if not COGNEE_IMPORTABLE:
        raise RuntimeError(f"Cognee not importable: {_IMPORT_ERROR}")

    async def _regen():
        configure_env(api_key)
        await setup()
        ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
        await visualize_graph(str(FULL_GRAPH_HTML))
        return FULL_GRAPH_HTML.read_text(encoding="utf-8")

    return run_async(_regen())


# ── Visualisation helpers ──────────────────────────────────────────────────────
def _html_as_iframe(html: str, height: int) -> str:
    """Embed HTML string in a base64 data-URI iframe — works in all Streamlit versions."""
    b64 = base64.b64encode(html.encode("utf-8")).decode()
    return (
        f'<iframe src="data:text/html;base64,{b64}" '
        f'width="100%" height="{height}px" '
        'style="border:none;border-radius:6px;" '
        'sandbox="allow-scripts"></iframe>'
    )


def full_graph_as_iframe(html: str) -> str:
    return _html_as_iframe(html, height=680)


def triples_to_html(triples: list[tuple[str, str, str]], height: int = 420) -> str:
    """
    Build an interactive pyvis subgraph from (source, relation, target) triples.
    Returns an HTML string suitable for st.components.v1.html().
    """
    if not triples:
        return ""

    try:
        from pyvis.network import Network
    except ImportError:
        return "<p style='color:red;'>pyvis not installed — run: pip install pyvis</p>"

    net = Network(
        height=f"{height}px", width="100%",
        directed=True, notebook=False, cdn_resources="in_line",
    )
    net.set_options(
        '{"nodes":{"font":{"size":13},"shape":"dot","size":14},'
        '"edges":{"font":{"size":10},"smooth":{"type":"curvedCW","roundness":0.2}},'
        '"physics":{"stabilization":{"iterations":100}},'
        '"interaction":{"hover":true}}'
    )

    seen: set[str] = set()
    for src, rel, tgt in triples:
        if src not in seen:
            net.add_node(src, label=src, title=src, color="#4fc3f7")
            seen.add(src)
        if tgt not in seen:
            net.add_node(tgt, label=tgt, title=tgt, color="#81c784")
            seen.add(tgt)
        net.add_edge(src, tgt, label=rel, title=rel, color="#90a4ae")

    # pyvis' write_html() uses the platform default encoding (cp1252 on
    # Windows) and crashes on any non-ASCII char in node titles. Generate the
    # HTML in memory and write it ourselves as UTF-8.
    tmp = GRAPHS_DIR / f"ctx_{abs(hash(str(triples)))}.html"
    try:
        html_str = net.generate_html(notebook=False)
    except TypeError:
        html_str = net.generate_html()
    tmp.write_text(html_str, encoding="utf-8")
    raw = html_str

    # Inject a search bar that highlights matching nodes
    search_bar = (
        '<div style="position:absolute;top:8px;left:8px;z-index:999;'
        'background:rgba(255,255,255,.92);padding:4px 8px;border-radius:6px;'
        'box-shadow:0 1px 4px rgba(0,0,0,.15);">'
        '<input id="ns" type="text" placeholder="&#128269; Search node…" '
        'style="padding:4px 8px;border-radius:4px;border:1px solid #90a4ae;'
        'font-size:12px;width:170px;outline:none;"></div>'
        '<script>'
        'document.getElementById("ns").addEventListener("input",function(){'
        'var q=this.value.toLowerCase();'
        'nodes.forEach(function(n){'
        'var h=q.length>0&&n.label.toLowerCase().includes(q);'
        'nodes.update({id:n.id,color:h?{background:"#ff5252",border:"#c62828"}:undefined});});});'
        '</script>'
    )
    raw = raw.replace("</body>", search_bar + "</body>", 1)
    return raw  # raw HTML for st.components.v1.html

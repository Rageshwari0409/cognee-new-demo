"""

Extracts durable user-specific facts from coach chat conversations using the
conservative prompt, then
deduplicates and persists them in a dedicated ChromaDB collection named
"semantic_memory_store" (completely separate from the existing "memories" collection).

Only memories with sub_tag == "implicit" (conversation-inferred) are stored.

No existing modules are modified. Call extract_and_store_from_chat() after any
coach chat turn to populate long-term semantic memory.

Schema per stored record (ChromaDB metadata):
    memory          — the durable fact sentence (also used as the embedded document)
    category        — one of the 14 allowed categories from the extraction prompt
    tag             — always "semantic"
    sub_tag         — always "implicit" (conversation-inferred); explicit memories skipped
    evidence        — short quote or paraphrase from the user that supports the memory
    created_timestamp — ISO-8601 UTC timestamp set at storage time (not by the model)
    source_id       — caller-supplied turn/session identifier for provenance
    semantic_memory — copy of `memory`; stored as a dedicated column per requirement
    username        — owner of the memory
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ChromaDB setup — dedicated collection, shared persistent client path
# ---------------------------------------------------------------------------

_COLLECTION_NAME = "semantic_memory_store"
_CHROMA_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "chroma_data"))

_client_lock = threading.Lock()
_chroma_client: Any = None
_chroma_collection: Any = None

# Deduplication: skip storing if token-F1 vs any existing memory exceeds this
_DEDUP_F1_THRESHOLD = 0.82

# ---------------------------------------------------------------------------
# Extraction prompt — {conversation} is injected at call time
# ---------------------------------------------------------------------------

_EXTRACTION_SYSTEM_PROMPT = """
You are a long-term semantic memory extraction assistant for a fitness coach.

Your job is to read a user conversation and extract only durable, user-specific facts that would help a future fitness coach personalize training, nutrition, motivation, or communication.

You are conservative. False memories are worse than missed memories. When uncertain, return no memory.

A memory should be extracted only if all five conditions are true:
1. It is about the user, not another person.
2. It is explicitly stated by the user, or a safe, well-supported inference drawn directly from what the user said.
3. It would be useful for future fitness, nutrition, motivation, or coaching personalization.
4. It is likely to remain true for weeks or months.
5. It is not a one-off event, temporary state, weather detail, assistant suggestion, third-party fact, or irrelevant chat detail.

Allowed memory categories:
- fitness_goal
- body_goal
- workout_preference
- workout_dislike
- food_preference
- dietary_restriction
- nutrition_goal
- schedule
- injury_or_limitation
- coaching_preference
- tracking_preference
- equipment
- experience_level
- lifestyle_constraint

Do not use any other category.

Strong durability signals include:
usually, normally, always, never, I prefer, I hate, I avoid, I need, I have, I am allergic, I cannot, most days, every week, for years, long-term, my doctor told me, outside Ramadan I usually.

Weak or temporary signals include:
today, yesterday, this week, right now, maybe, I might, I was thinking, for now, this month only, once, later, tomorrow.

Do not extract:
- one-day events
- current mood
- today's soreness
- today's weather
- meals eaten once
- skipped workouts
- temporary schedule disruptions
- vague future intentions
- curiosity questions
- assistant advice
- jokes or small talk
- facts about friends, family, influencers, or coaches
- unsupported guesses
- weak preferences stated casually without durability
- casual food likes or dislikes from a single mention, such as "I love yogurt" or "I enjoy cake", unless the user clearly describes them as a long-term preference

Return only valid JSON in this exact schema:
{{
  "memories": [
    {{
      "memory": "Clear standalone long-term fact about the user.",
      "category": "fitness_goal | body_goal | workout_preference | workout_dislike | food_preference | dietary_restriction | nutrition_goal | schedule | injury_or_limitation | coaching_preference | tracking_preference | equipment | experience_level | lifestyle_constraint",
      "evidence": "Short quote or paraphrase from the user",
      "durability_signal": "Specific phrase that shows this is durable",
      "confidence": 0.0,
      "sub_tag": "implicit"
    }}
  ]
}}

Rules:
- sub_tag: always output "implicit" for facts inferred from the user's own words in conversation.

If there are no useful long-term semantic memories, return:
{{"memories": []}}

Each memory must be atomic. Store one fact per memory.

Few-shot examples:

Example 1

Conversation:
User: It was raining all day so I skipped my walk.
Coach: That's okay.
User: I usually walk every morning before work anyway.
Coach: Nice.
User: Yeah, morning workouts are easiest for me.

Output:
{{
  "memories": [
    {{
      "memory": "The user usually walks in the mornings before work.",
      "category": "schedule",
      "evidence": "I usually walk every morning before work",
      "durability_signal": "usually",
      "confidence": 0.95,
      "sub_tag": "implicit"
    }},
    {{
      "memory": "The user prefers morning workouts.",
      "category": "workout_preference",
      "evidence": "morning workouts are easiest for me",
      "durability_signal": "easiest for me",
      "confidence": 0.9,
      "sub_tag": "implicit"
    }}
  ]
}}

Example 2

Conversation:
User: I ate pizza and ice cream yesterday.
Coach: That's okay.
User: Normally I cook most of my meals at home.

Output:
{{
  "memories": [
    {{
      "memory": "The user usually cooks most meals at home.",
      "category": "food_preference",
      "evidence": "Normally I cook most of my meals at home",
      "durability_signal": "normally",
      "confidence": 0.94,
      "sub_tag": "implicit"
    }}
  ]
}}

Example 3

Conversation:
User: I skipped the gym today because I felt tired.
User: I might start running next week.
User: It was really hot outside.

Output:
{{"memories": []}}

Example 4

Conversation:
User: My friend Sarah follows keto.
Coach: Interesting.
User: I actually prefer eating rice and fruit.

Output:
{{
  "memories": [
    {{
      "memory": "The user prefers eating rice and fruit.",
      "category": "food_preference",
      "evidence": "I actually prefer eating rice and fruit",
      "durability_signal": "I actually prefer",
      "confidence": 0.93,
      "sub_tag": "implicit"
    }}
  ]
}}

Example 5

Conversation:
User: I hate burpees.
Coach: Why?
User: Every workout plan that includes them makes me quit.
User: Squats are fine though.

Output:
{{
  "memories": [
    {{
      "memory": "The user dislikes burpees.",
      "category": "workout_dislike",
      "evidence": "I hate burpees",
      "durability_signal": "I hate",
      "confidence": 0.96,
      "sub_tag": "implicit"
    }},
    {{
      "memory": "The user is comfortable performing squats.",
      "category": "workout_preference",
      "evidence": "Squats are fine though",
      "durability_signal": "are fine",
      "confidence": 0.86,
      "sub_tag": "implicit"
    }}
  ]
}}

Example 6

Conversation:
User: I don't respond well to harsh feedback.
Coach: Understood.
User: Encouragement works much better for me.

Output:
{{
  "memories": [
    {{
      "memory": "The user prefers encouraging coaching feedback.",
      "category": "coaching_preference",
      "evidence": "Encouragement works much better for me",
      "durability_signal": "works much better for me",
      "confidence": 0.95,
      "sub_tag": "implicit"
    }}
  ]
}}

Example 7

Conversation:
User: I used to run marathons.
Coach: Nice.
User: These days I mostly strength train and rarely run.

Output:
{{
  "memories": [
    {{
      "memory": "The user currently focuses mostly on strength training.",
      "category": "workout_preference",
      "evidence": "These days I mostly strength train",
      "durability_signal": "these days mostly",
      "confidence": 0.88,
      "sub_tag": "implicit"
    }},
    {{
      "memory": "The user rarely runs currently.",
      "category": "workout_preference",
      "evidence": "rarely run",
      "durability_signal": "rarely",
      "confidence": 0.86,
      "sub_tag": "implicit"
    }}
  ]
}}

Example 8

Conversation:
User: My shoulder is sore today from moving furniture.
Coach: Okay.
User: I do have a long-term knee issue that limits jumping exercises.

Output:
{{
  "memories": [
    {{
      "memory": "The user has a long-term knee issue that limits jumping exercises.",
      "category": "injury_or_limitation",
      "evidence": "I do have a long-term knee issue that limits jumping exercises",
      "durability_signal": "long-term",
      "confidence": 0.97,
      "sub_tag": "implicit"
    }}
  ]
}}

Example 9

Conversation:
User: I work out at home.
Coach: What equipment do you have?
User: Adjustable dumbbells, resistance bands, and a bench.

Output:
{{
  "memories": [
    {{
      "memory": "The user works out at home.",
      "category": "lifestyle_constraint",
      "evidence": "I work out at home",
      "durability_signal": "work out at home",
      "confidence": 0.91,
      "sub_tag": "implicit"
    }},
    {{
      "memory": "The user has adjustable dumbbells.",
      "category": "equipment",
      "evidence": "Adjustable dumbbells",
      "durability_signal": "equipment owned",
      "confidence": 0.94,
      "sub_tag": "implicit"
    }},
    {{
      "memory": "The user has resistance bands.",
      "category": "equipment",
      "evidence": "resistance bands",
      "durability_signal": "equipment owned",
      "confidence": 0.94,
      "sub_tag": "implicit"
    }},
    {{
      "memory": "The user has a bench.",
      "category": "equipment",
      "evidence": "a bench",
      "durability_signal": "equipment owned",
      "confidence": 0.94,
      "sub_tag": "implicit"
    }}
  ]
}}

Example 10

Conversation:
User: Work has been crazy lately.
Coach: Sorry to hear that.
User: Yeah. I missed two workouts this week.
Coach: Happens.
User: Usually I train four mornings per week.
Coach: Good consistency.
User: I also prefer shorter workouts around 30 minutes.

Output:
{{
  "memories": [
    {{
      "memory": "The user typically trains four mornings per week.",
      "category": "schedule",
      "evidence": "Usually I train four mornings per week",
      "durability_signal": "usually",
      "confidence": 0.95,
      "sub_tag": "implicit"
    }},
    {{
      "memory": "The user prefers workouts around 30 minutes.",
      "category": "workout_preference",
      "evidence": "I also prefer shorter workouts around 30 minutes",
      "durability_signal": "I also prefer",
      "confidence": 0.93,
      "sub_tag": "implicit"
    }}
  ]
}}

Example 11

Conversation:
User: Hello.
Coach: Hi.
User: Tell me a joke.
Coach: Why did the dumbbell cross the road?
User: Haha.

Output:
{{"memories": []}}

Example 12

Conversation:
User: I don't count calories because it makes me obsessive.
Coach: We can use a habit-based approach.
User: Yeah, portion guidance works better for me.

Output:
{{
  "memories": [
    {{
      "memory": "The user does not want to count calories.",
      "category": "tracking_preference",
      "evidence": "I don't count calories",
      "durability_signal": "I don't",
      "confidence": 0.95,
      "sub_tag": "implicit"
    }},
    {{
      "memory": "Calorie counting makes the user feel obsessive.",
      "category": "tracking_preference",
      "evidence": "because it makes me obsessive",
      "durability_signal": "makes me obsessive",
      "confidence": 0.91,
      "sub_tag": "implicit"
    }},
    {{
      "memory": "The user prefers portion guidance.",
      "category": "tracking_preference",
      "evidence": "portion guidance works better for me",
      "durability_signal": "works better for me",
      "confidence": 0.94,
      "sub_tag": "implicit"
    }}
  ]
}}

Now extract memories from the following conversation:

{conversation}
"""

_ALLOWED_CATEGORIES = frozenset([
    "fitness_goal", "body_goal", "workout_preference", "workout_dislike",
    "food_preference", "dietary_restriction", "nutrition_goal", "schedule",
    "injury_or_limitation", "coaching_preference", "tracking_preference",
    "equipment", "experience_level", "lifestyle_constraint",
])


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_and_store_from_chat(
    username: str,
    user_message: str,
    api_key: str | None,
    source_id: str | None = None,
) -> list[dict]:
    """
    Extract semantic memories from one coach chat turn and persist new ones.

    Only memories with sub_tag == "implicit" (conversation-inferred) are stored.
    Explicit memories (profile-stated) are skipped.

    Args:
        username:       The logged-in user's identifier.
        user_message:   The user's message for this turn.
        coach_response: The coach's reply for this turn.
        api_key:        Gemini API key. If None or empty, extraction is skipped.
        source_id:      Optional caller-supplied turn/session ID for provenance.

    Returns:
        List of metadata dicts for memories that were actually stored this call.
        Empty list when nothing qualifies or on any error.
    """
    # Normalize username to lower-case to prevent case mismatch bugs
    username = username.strip().lower()
    
    print(f"\n[SEMANTIC_STORE] extract_and_store_from_chat called for user: '{username}'")
    print(f"[SEMANTIC_STORE] User message: {user_message!r}")
    
    logger.debug("extract_and_store_from_chat called: username=%r message=%r", username, user_message[:80])
    if not api_key or not username or not user_message.strip():
        print("[SEMANTIC_STORE] Skipping extraction: Missing API key, username, or user message.")
        return []

    # Include both user query and coach response to capture full turn context
    conversation = f"User: {user_message}"
    print(f"[SEMANTIC_STORE] Calling Gemini to extract memories from conversation turns...")
    extracted = _call_gemini(conversation, api_key)
    
    print(f"[SEMANTIC_STORE] Extracted candidates from LLM: {extracted}")
    if not extracted:
        print("[SEMANTIC_STORE] No memories extracted by Gemini.")
        return []

    col = _get_collection()
    if col is None:
        print("[SEMANTIC_STORE] Error: Failed to fetch ChromaDB collection.")
        return []

    existing = _fetch_existing_memories(username)
    now_iso = datetime.now(timezone.utc).isoformat()
    src_id = source_id or f"chat-{uuid.uuid4().hex[:8]}"

    stored: list[dict] = []
    batch_memories: list[str] = []

    for item in extracted:
        memory_text = item.get("memory", "").strip()
        if not memory_text:
            continue

        # Only persist implicit (conversation-inferred) memories
        memory_type = item.get("sub_tag", item.get("memory_type", "implicit")).strip().lower()
        if memory_type != "implicit":
            logger.debug("Skipping non-implicit memory: %r", memory_text[:60])
            print(f"[SEMANTIC_STORE] Skipping non-implicit memory: {memory_text!r}")
            continue

        category = item.get("category", "").strip().lower()
        if category not in _ALLOWED_CATEGORIES:
            category = "lifestyle_constraint"

        evidence = item.get("evidence", "").strip()

        if _is_duplicate(memory_text, existing + batch_memories):
            logger.debug("Skipping duplicate: %r", memory_text[:60])
            print(f"[SEMANTIC_STORE] Skipping duplicate memory: {memory_text!r}")
            continue

        mem_id = uuid.uuid4().hex
        metadata = {
            "username":         username,
            "memory":           memory_text,
            "tag":              "semantic",
            "category":         category,
            "sub_tag":          "implicit",
            "evidence":         evidence,
            "created_timestamp": now_iso,
            "source_id":        src_id,
            "semantic_memory":  memory_text,
        }

        try:
            col.upsert(
                ids=[mem_id],
                documents=[memory_text],
                metadatas=[metadata],
            )
            stored.append(metadata)
            batch_memories.append(memory_text)
            print(f"[SEMANTIC_STORE] Successfully saved implicit memory: {memory_text!r} (Category: {category})")
            logger.info("Stored [%s] %r | metadata: %s", category, memory_text[:70], metadata)
        except Exception as exc:
            print(f"[SEMANTIC_STORE] Error during ChromaDB upsert: {exc}")
            logger.exception("Upsert error: %s", exc)

    return stored


def get_semantic_memories(username: str, limit: int = 100) -> list[dict]:
    """
    Retrieve all semantic memories for a user from the dedicated collection.

    Returns a list of metadata dicts sorted by created_timestamp descending (newest first).
    Each dict contains: memory, category, tag, sub_tag, evidence, created_timestamp,
    source_id, semantic_memory.
    """
    # Normalize username to lower-case to prevent case mismatch bugs
    username = username.strip().lower()
    
    col = _get_collection()
    if col is None:
        return []
    try:
        results = col.get(where={"username": username})
        if not results or not results.get("metadatas"):
            return []
        metas = results["metadatas"]
        # Normalize legacy field names from old records
        for m in metas:
            if "created_timestamp" not in m or not m["created_timestamp"]:
                m["created_timestamp"] = (
                    m.pop("output_timestamp", None)
                    or m.pop("created_time", None)
                    or ""
                )
        metas.sort(key=lambda m: m.get("created_timestamp", ""), reverse=True)
        return metas[:limit]
    except Exception as exc:
        logger.exception("get_semantic_memories error: %s", exc)
        return []

# ---------------------------------------------------------------------------
# ChromaDB helpers
# ---------------------------------------------------------------------------

def _get_collection() -> Any:
    """Return the persistent ChromaDB collection, creating it if needed."""
    global _chroma_client, _chroma_collection
    with _client_lock:
        if _chroma_collection is not None:
            return _chroma_collection
        try:
            import chromadb
            from chromadb.config import Settings
            import numpy as np
            from chromadb.api.types import EmbeddingFunction, Documents, Embeddings

            class _NomicEF(EmbeddingFunction):
                def __init__(self) -> None:
                    self._model: Any = None

                def __call__(self, input: Documents) -> Embeddings:
                    if self._model is None:
                        from sentence_transformers import SentenceTransformer
                        self._model = SentenceTransformer(
                            "nomic-ai/nomic-embed-text-v1.5", trust_remote_code=True
                        )
                    prefixed = [
                        x if x.startswith(("search_query:", "search_document:"))
                        else f"search_document: {x}"
                        for x in input
                    ]
                    return self._model.encode(prefixed).astype(np.float32).tolist()

            if _chroma_client is None:
                _chroma_client = chromadb.PersistentClient(
                    path=_CHROMA_PATH,
                    settings=Settings(anonymized_telemetry=False),
                )
            _chroma_collection = _chroma_client.get_or_create_collection(
                name=_COLLECTION_NAME,
                metadata={"hnsw:space": "cosine"},
                embedding_function=_NomicEF(),
            )
            return _chroma_collection
        except Exception as exc:
            logger.exception("ChromaDB init error: %s", exc)
            return None


# ---------------------------------------------------------------------------
# Deduplication helpers
# ---------------------------------------------------------------------------

def _normalize_tokens(text: str) -> set[str]:
    lowered = text.lower()
    return set(re.sub(r"[^a-z0-9\s]", " ", lowered).split())


def _token_f1(a: str, b: str) -> float:
    t_a, t_b = _normalize_tokens(a), _normalize_tokens(b)
    if not t_a or not t_b:
        return 0.0
    common = len(t_a & t_b)
    p = common / len(t_b)
    r = common / len(t_a)
    return 2 * p * r / (p + r) if (p + r) > 0 else 0.0


def _fetch_existing_memories(username: str) -> list[str]:
    """Return all existing memory strings for this user (for dedup check)."""
    col = _get_collection()
    if col is None:
        return []
    try:
        results = col.get(where={"username": username})
        if not results or not results.get("metadatas"):
            return []
        return [m.get("memory", "") for m in results["metadatas"] if m.get("memory")]
    except Exception as exc:
        logger.exception("fetch error: %s", exc)
        return []


def _is_duplicate(new_memory: str, existing: list[str]) -> bool:
    """Return True if new_memory is too similar to any entry in existing."""
    for mem in existing:
        if _token_f1(new_memory, mem) >= _DEDUP_F1_THRESHOLD:
            return True
    return False


# ---------------------------------------------------------------------------
# Gemini extraction
# ---------------------------------------------------------------------------

def _call_gemini(conversation: str, api_key: str) -> list[dict]:
    """
    Call Gemini with the extraction prompt and return the parsed memory list.
    The conversation is injected into the system prompt as a dynamic variable.
    Returns an empty list on any failure (never raises).
    """
    try:
        from google import genai  # type: ignore

        client = genai.Client(api_key=api_key)
        system_prompt = _EXTRACTION_SYSTEM_PROMPT.format(conversation=conversation)
        response = client.models.generate_content(
            model="gemini-flash-lite-latest",
            contents="Return the semantic memory JSON now.",
            config={
                "system_instruction": system_prompt,
                "response_mime_type": "application/json",
                "temperature": 0.0,
                "max_output_tokens": 768,
            },
        )
        logger.debug("Gemini raw response: %s", response)
        raw = getattr(response, "text", None) or ""
        raw = raw.strip()
        raw = re.sub(r"^```(?:json)?", "", raw).strip()
        raw = re.sub(r"```$", "", raw).strip()

        parsed = json.loads(raw)
        memories = parsed.get("memories", [])
        logger.debug("Extracted memories: %s", memories)
        if not isinstance(memories, list):
            return []
        valid: list[dict] = []
        for m in memories:
            if isinstance(m, dict) and isinstance(m.get("memory"), str) and m["memory"].strip():
                valid.append(m)
        return valid
    except Exception as exc:
        logger.exception("Gemini extraction error: %s", exc)
        return []




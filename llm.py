import re
import urllib.request
import json
import os
from datetime import datetime

def call_gemini_api(
    api_key: str,
    user_query: str,
    semantic_memories: str = "",
    semantic_store_memories: str = "",
    procedural_memory: str = "",
    episodic_memories: str = "",
    kg_memory: str = "",
    chat_history: str = "",
    user_profile: str = "",
    use_memory: bool = True
) -> dict:
    from google import genai
    
    model_name = os.environ.get("GEMINI_MODEL_NAME", "gemini-flash-lite-latest")
    
    try:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        filename = "main_prompt_with_memory.txt" if use_memory else "main_prompt_without_memory.txt"
        filepath = os.path.join(base_dir, filename)
        with open(filepath, "r", encoding="utf-8") as f:
            prompt_template = f.read()
    except Exception as err:
        print(f"Error loading prompt template file: {err}")
        # Inline fallback prompt if file read fails (should not happen)
        if use_memory:
            prompt_template = """You are a professional AI Workout Coach.
CHAT_HISTORY: {chat_history}
Semantic: {semantic_memories}
Semantic Store: {semantic_store_memories}
Procedural: {procedural_memory}
Episodic: {episodic_memories}
Knowledge Graph: {kg_memory}
User Query: {user_query}
JSON Output:"""
        else:
            prompt_template = """You are a professional AI Workout Coach.
User Query: {user_query}
JSON Output:"""

    if use_memory:
        prompt = prompt_template.format(
            chat_history=chat_history,
            semantic_memories=semantic_memories,
            semantic_store_memories=semantic_store_memories,
            procedural_memory=procedural_memory,
            episodic_memories=episodic_memories,
            kg_memory=kg_memory,
            user_query=user_query
        )
    else:
        prompt = prompt_template.format(
            user_query=user_query
        )

    print(f"\n[GEMINI_API] call_gemini_api called with use_memory={use_memory}")
    print(f"[GEMINI_API] Prompt length: {len(prompt)} characters")
    print(f"[GEMINI_API] PROMPT SENT TO GEMINI:\n{prompt}\n[GEMINI_API] END OF PROMPT\n")
    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=model_name,
            contents=prompt,
            config={
                "response_mime_type": "application/json",
                "temperature": 0.4,
            }
        )
        
        text_content = getattr(response, "text", None) or ""
        if not text_content.strip():
            raise RuntimeError("Empty response from Gemini")
            
        try:
            json_match = re.search(r"\{.*\}", text_content, re.DOTALL)
            if json_match:
                result = json.loads(json_match.group(0), strict=False)
            else:
                result = json.loads(text_content, strict=False)
                
            for k in ["response", "reply", "coaching_response", "text"]:
                if k in result:
                    return {"response": result[k]}
            for k, v in result.items():
                if isinstance(v, str):
                    return {"response": v}
        except Exception:
            pass

        # Fallback to cleaning and returning the raw string if JSON parsing/extraction fails
        clean_text = text_content.strip()
        clean_text = re.sub(r"^```(?:json)?\n", "", clean_text)
        clean_text = re.sub(r"\n```$", "", clean_text)
        return {"response": clean_text}
    except Exception as e:
        print(f"Gemini API Call ({model_name}) error: {e}")
        raise e
            


def get_user_profile_dict(username: str) -> dict:
    """
    Query explicit semantic memories from ChromaDB and compile a profile dictionary
    matching DietCoach's schema.
    """
    import database_chroma_new as database
    memories = database.get_memories_by_tag(username, "semantic")
    explicit_map = {}
    for m in memories:
        if m.get("subtag") == "explicit":
            explicit_map[m["query"]] = m["response"]

    print(f"DEBUG: explicit_map for user '{username}' = {explicit_map}")

    def find_val(keywords, default=None):
        for q, r in explicit_map.items():
            if all(kw in q.lower() for kw in keywords):
                return r
        return default

    # Diet preference mapping
    diet_pref = find_val(["diet preference"], "Non Vegan")
    diet_types = ["vegan"] if diet_pref and diet_pref.lower() == "vegan" else ["non-vegan"]

    # Fitness goal mapping
    primary_goal_raw = find_val(["fitness goal"], "Improve General Fitness & Health")
    fitness_goal = "general_fitness_maintenance"
    if "muscle" in primary_goal_raw.lower() or "strength" in primary_goal_raw.lower():
        fitness_goal = "muscle_gain_hypertrophy"
    elif "loss" in primary_goal_raw.lower() or "weight" in primary_goal_raw.lower():
        fitness_goal = "fat_loss_body_recomposition"

    # Workout frequency mapping
    freq_raw = find_val(["how often", "work out"], "2-3 times a week")
    workout_days = 3
    if "daily" in freq_raw.lower():
        workout_days = 7
    elif "once" in freq_raw.lower():
        workout_days = 1
    elif "rarely" in freq_raw.lower():
        workout_days = 0

    # Focus area & workout types mapping
    focus_raw = find_val(["focus on"], "Full Body Core & Flexibility")
    workout_types = ["strength_hypertrophy"]
    if "cardio" in focus_raw.lower():
        workout_types = ["cardio_endurance"]
    elif "full" in focus_raw.lower() or "core" in focus_raw.lower():
        workout_types = ["strength_hypertrophy", "cardio_endurance"]

    # Weight parsing
    weight_raw = find_val(["weight"], None)
    weight_kg = None
    if weight_raw:
        try:
            nums = re.findall(r"\d+\.?\d*", weight_raw)
            if nums:
                weight_kg = float(nums[0])
        except Exception:
            pass

    # Height parsing
    height_raw = find_val(["height"], None)
    height_cm = None
    if height_raw:
        try:
            nums = re.findall(r"\d+\.?\d*", height_raw)
            if nums:
                height_cm = float(nums[0])
        except Exception:
            pass

    # Country mapping
    country_raw = find_val(["country"], "India")
    country_code = "IN"
    if "united states" in country_raw.lower() or "us" in country_raw.lower():
        country_code = "US"
    elif "united kingdom" in country_raw.lower() or "uk" in country_raw.lower() or "gb" in country_raw.lower():
        country_code = "GB"
    elif "canada" in country_raw.lower() or "ca" in country_raw.lower():
        country_code = "CA"
    elif "australia" in country_raw.lower() or "au" in country_raw.lower():
        country_code = "AU"

    # Allergies and intolerances parsing (filter out 'none' values)
    combined_raw = find_val(["allergies or intolerances"], None)
    if combined_raw is not None:
        selected_items = [item.strip().lower() for item in combined_raw.split(",") if item.strip() and item.strip().lower() != "none"]
        allergies_set = {"peanuts", "tree nuts", "soy", "gluten", "dairy", "eggs", "sesame", "fish / shellfish"}
        intolerances_set = {"lactose", "fructose", "histamine", "gluten sensitivity", "caffeine"}
        
        allergies = [item for item in selected_items if item in allergies_set]
        intolerances = [item for item in selected_items if item in intolerances_set]
    else:
        # Backward compatibility fallback for existing users
        allergies_raw = find_val(["allergies"], "None")
        allergies = [a.strip().lower() for a in allergies_raw.split(",") if a.strip() and a.strip().lower() != "none"]

        intolerances_raw = find_val(["intolerances"], "None")
        intolerances = [i.strip().lower() for i in intolerances_raw.split(",") if i.strip() and i.strip().lower() != "none"]

    # Meal prep time limit parsing
    max_prep_raw = find_val(["meal prep"], "20 mins")
    max_prep = 20
    try:
        nums = re.findall(r"\d+", max_prep_raw)
        if nums:
            max_prep = int(nums[0])
    except Exception:
        pass

    # Preferred cuisines parsing
    cuisines_raw = find_val(["cuisines"], "Indian, Mediterranean")
    cuisines = [c.strip() for c in cuisines_raw.split(",") if c.strip() and c.strip().lower() != "none"]

    # Typical workout time
    typical_time = find_val(["typical workout time"], "Evening")

    # Extra onboarding questions
    life_stage = find_val(["life stage"], "Working Professional")
    injuries = find_val(["injuries"], "None")
    workout_level = find_val(["workout level"], "Beginner")
    training_location = find_val(["prefer to train"], "At Home")
    workout_duration_limit = find_val(["time can you dedicate"], "30-60 mins")

    # Build the profile dict containing standard and raw fields
    profile = {
        "country": country_code,
        "onboarding_complete": True,
        "life_stage": life_stage,
        "injuries": injuries,
        "workout_level": workout_level,
        "training_location": training_location,
        "workout_duration_limit": workout_duration_limit,
        "diet_constraints": {
            "diet_type": diet_types,
            "allergies": allergies,
            "intolerances": intolerances
        },
        "preferences": {
            "max_prep_minutes": max_prep,
            "cuisines_liked": cuisines
        },
        "goals": {
            "primary": primary_goal_raw.lower()
        },
        "fitness_profile": {
            "workout_types": workout_types,
            "workout_days_per_week": workout_days,
            "typical_workout_time": typical_time.lower() if typical_time else "evening",
            "fitness_goal": fitness_goal,
            "body_weight_kg": weight_kg,
            "height_cm": height_cm
        },
        "raw_onboarding_answers": explicit_map
    }
    return profile


def get_user_memories_string(username: str) -> str:
    """
    Fetch memories from ChromaDB and format them as a single string.
    Each memory is prefixed with its subtag, e.g. '-[explicit]' or '-[implicit]'.
    """
    import database_chroma_new as database
    
    # Get all semantic, episodic, and procedural memories
    sem = database.get_memories_by_tag(username, "semantic")
    epi = database.get_memories_by_tag(username, "episodic")
    pro = database.get_memories_by_tag(username, "procedural")
    
    # Combine them
    all_mems = []
    for m in sem:
        all_mems.append((m.get("timestamp", ""), m.get("subtag", "implicit"), m.get("response", "")))
    for m in epi:
        all_mems.append((m.get("timestamp", ""), m.get("subtag", "implicit"), m.get("response", "")))
    for m in pro:
        all_mems.append((m.get("timestamp", ""), m.get("subtag", "implicit"), m.get("response", "")))
        
    # Sort by timestamp ascending so older context is first and newer is last
    all_mems.sort(key=lambda x: x[0])
    
    # Keep the latest 30 memories to prevent prompt bloating
    all_mems = all_mems[-30:]
    
    lines = []
    for ts, subtag, response in all_mems:
        cleaned_subtag = subtag.strip(" -[]")
        lines.append(f"-[{cleaned_subtag}] {response}")
        
    if not lines:
        return "-[explicit] User has initialized a new profile."
        
    return "\n".join(lines)


def get_dynamic_memories_parallel(username: str, query_text: str, api_key: str | None) -> dict:
    """
    Query memories across the 5 systems in parallel using a ThreadPoolExecutor.
    """
    import database_chroma_new as database
    import concurrent.futures
    
    try:
        import cognee_integration as _cog
        COGNEE_AVAILABLE = _cog.COGNEE_IMPORTABLE
    except Exception as e:
        print(f"DEBUG: cognee_integration import failed: {e}")
        _cog = None
        COGNEE_AVAILABLE = False

    # Pre-compute the query embedding once to avoid repeating slow PyTorch encoding across threads
    try:
        embedding_fn = database.get_embedding_function()
        q_emb = embedding_fn([f"search_query: {query_text}"])[0]
    except Exception as e:
        print(f"Error pre-computing query embedding: {e}")
        q_emb = None

    def fetch_semantic():
        try:
            return database.vector_query_memories(username, "semantic", query_text, top_k=5, q_emb=q_emb)
        except Exception as e:
            print(f"Error querying semantic memories: {e}")
            return []

    def fetch_semantic_store():
        try:
            return database.vector_query_semantic_store(username, query_text, top_k=5, q_emb=q_emb)
        except Exception as e:
            print(f"Error querying semantic store memories: {e}")
            return []

    def fetch_episodic():
        try:
            return database.vector_query_memories(username, "episodic", query_text, top_k=5, q_emb=q_emb)
        except Exception as e:
            print(f"Error querying episodic memories: {e}")
            return []

    def fetch_procedural():
        try:
            return database.vector_query_memories(username, "procedural", query_text, top_k=5, q_emb=q_emb)
        except Exception as e:
            print(f"Error querying procedural memories: {e}")
            return []

    def fetch_kg():
        if COGNEE_AVAILABLE and _cog and api_key:
            try:
                print(f"DEBUG: Querying Cognee Knowledge Graph with '{query_text}'")
                return _cog.query_exercise_graph(query_text, api_key)
            except Exception as e:
                print(f"Error querying Cognee Knowledge Graph: {e}")
                return {"error": str(e)}
        return None

    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = {
            executor.submit(fetch_semantic): "semantic",
            executor.submit(fetch_semantic_store): "semantic_store",
            executor.submit(fetch_episodic): "episodic",
            executor.submit(fetch_procedural): "procedural",
            executor.submit(fetch_kg): "kg"
        }
        for future in concurrent.futures.as_completed(futures):
            key = futures[future]
            try:
                results[key] = future.result()
            except Exception as e:
                print(f"Error in parallel fetch for {key}: {e}")
                results[key] = [] if key != "kg" else None
                
    return results


def format_memories_string(parallel_results: dict) -> str:
    """
    Format parallel retrieved memory structures into a single prompt string.
    """
    sem_mems = parallel_results.get("semantic") or []
    sem_store_mems = parallel_results.get("semantic_store") or []
    epi_mems = parallel_results.get("episodic") or []
    pro_mems = parallel_results.get("procedural") or []
    kg_result = parallel_results.get("kg")
    
    kg_context = ""
    if kg_result:
        if isinstance(kg_result, dict):
            answer = kg_result.get("answer") or ""
            triples = kg_result.get("triples") or []
            parts = []
            if answer and "no answer returned" not in answer.lower():
                parts.append(f"Answer: {answer}")
            if triples:
                triples_str = "\n".join([f"- ({t[0]} - {t[1]} -> {t[2]})" for t in triples[:10]])
                parts.append(f"Relationships:\n{triples_str}")
            if parts:
                kg_context = "\n".join(parts)
        elif isinstance(kg_result, str):
            kg_context = kg_result

    lines = []
    
    # Format Semantic memories
    if sem_mems or sem_store_mems:
        lines.append("### Semantic Memories (General Facts & Principles)")
        for m in sem_mems:
            subtag = m.get("subtag", "implicit").strip(" -[]")
            lines.append(f"-[{subtag}] {m['response']}")
        for m in sem_store_mems:
            subtag = m.get("subtag", "implicit").strip(" -[]")
            evidence_str = f" (Evidence: {m['evidence']})" if m.get("evidence") else ""
            lines.append(f"-[{subtag}] {m['response']}{evidence_str}")
        lines.append("")
        
    # Format Episodic memories
    if epi_mems:
        lines.append("### Episodic Memories (Personal Logs & Experiences)")
        for m in epi_mems:
            subtag = m.get("subtag", "implicit").strip(" -[]")
            lines.append(f"-[{subtag}] {m['response']}")
        lines.append("")
        
    # Format Procedural memories
    if pro_mems:
        lines.append("### Procedural Memories (Setup Guides & Rules)")
        for m in pro_mems:
            subtag = m.get("subtag", "implicit").strip(" -[]")
            lines.append(f"-[{subtag}] {m['response']}")
        lines.append("")
        
    # Format Knowledge Graph
    if kg_context:
        lines.append("### Knowledge Graph (Exercise Definitions & Relations)")
        lines.append(kg_context)
        lines.append("")

    if not lines:
        return "-[explicit] User has initialized a new profile."
        
    return "\n".join(lines)


def get_dynamic_memories_string(username: str, query_text: str, api_key: str | None) -> str:
    """
    Query memories across the 4 systems based on the user's search query,
    and format the retrieved context.
    
    This function calls the parallel retriever to run searches concurrently.
    """
    parallel_res = get_dynamic_memories_parallel(username, query_text, api_key)
    return format_memories_string(parallel_res).strip()


def extract_memories(api_key: str, user_query: str, response_text: str) -> dict:
    """
    Call Gemini to extract new memories (semantic, episodic, procedural)
    from the current conversation turn.
    """
    from google import genai
    
    model_name = os.environ.get("GEMINI_MODEL_NAME", "gemini-flash-lite-latest")
    if model_name == "gemini-flash-lite-latest":
        model_name = "gemini-flash-lite-latest"
    
    prompt = f"""You are an advanced cognitive memory compiler for an AI Workout Coach.
Your job is to analyze a single conversation turn between a User and their Coach, and extract key facts/guidelines to save in the Coach's long-term memory database.

Input:
User Query: {user_query}
Coach Response: {response_text}

Rules for Extraction:
1. "semantic": Extract general fitness concepts, physiological rules, nutrition/sports science facts, or hard rules mentioned.
   - Do NOT write personal details here.
   - Every entry MUST be a complete, third-person declarative sentence summarizing the fact (e.g. "The user loves to have yogurt as their main preference" or "Creatine baseline levels are lower for vegetarians"). Do NOT store as a Q&A or conversation fragment.
2. "episodic": Extract the user's personal experiences, workout logs, symptoms, pain/injuries, preferences, or specific physical status mentioned in this turn.
   - Every entry MUST be a complete, third-person declarative sentence summarizing the user's details, choice, or log (e.g. "The user has knee pain when performing squats" or "The user wants to incorporate eggs into their diet"). Do NOT store as a Q&A or conversation fragment.
3. "procedural": Extract actionable step-by-step guides, exercise instructions, split setup rules, or execution manuals mentioned in the coach's response.

Format your output strictly as a JSON object matching this schema (with no markdown formatting or extra text outside the JSON):
{{
  "semantic": ["fact 1", "fact 2"],
  "episodic": ["personal log 1"],
  "procedural": ["action guide step 1"]
}}
"""

    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=model_name,
            contents=prompt,
            config={
                "response_mime_type": "application/json",
                "temperature": 0.2,
            }
        )
        
        # Safely extract text parts to avoid thought_signature warnings
        text_parts = []
        try:
            if response.candidates and response.candidates[0].content.parts:
                for part in response.candidates[0].content.parts:
                    if hasattr(part, "text") and part.text:
                        text_parts.append(part.text)
        except Exception:
            pass
            
        text_content = "".join(text_parts) if text_parts else (getattr(response, "text", None) or "")
        if not text_content.strip():
            raise RuntimeError("Empty response from Gemini")
            
        json_match = re.search(r"\{.*\}", text_content, re.DOTALL)
        if json_match:
            result = json.loads(json_match.group(0), strict=False)
        else:
            result = json.loads(text_content, strict=False)
            
        proc_list = result.get("procedural", []) or result.get("procedics", []) or result.get("procedurals", [])
        return {
            "semantic": result.get("semantic", []) or [],
            "episodic": result.get("episodic", []) or [],
            "procedural": proc_list or []
        }
    except Exception as e:
        print(f"Failed to extract memories using Gemini: {e}")
        return {"semantic": [], "episodic": [], "procedural": []}


def simulate_memory_extraction(user_query: str) -> dict:
    """Fallback offline logic to extract memories based on query keywords."""
    query_lower = user_query.lower()
    memories = {"semantic": [], "episodic": [], "procedural": []}
    
    if any(k in query_lower for k in ["pain", "hurt", "sore", "injury", "knee", "shoulder", "back"]):
        body_part = "knees" if "knee" in query_lower else ("shoulders" if "shoulder" in query_lower else ("lower back" if "back" in query_lower else "joints"))
        memories["episodic"].append(f"User experienced discomfort or pain in the {body_part} during training and was advised to rest.")
        memories["semantic"].append("Ligaments and tendons have lower blood supply than muscles, causing them to recover and adapt slower to load.")
        memories["procedural"].append(f"Joint Pain Recovery Protocol: 1. Reduce workout load immediately. 2. Switch to low-impact exercise variations. 3. Monitor pain levels for 48 hours.")
    elif any(k in query_lower for k in ["squat", "leg", "quad", "glute"]):
        memories["episodic"].append("User queried about lower body training (squats/legs) and is focused on quad/glute development.")
        memories["semantic"].append("Squats target the quadriceps, glutes, and core. Keeping knees in line with toes prevents patellar shear stress.")
        memories["procedural"].append("Barbell Squat Form Guide: 1. Place bar on upper traps. 2. Set feet shoulder-width apart. 3. Hinge at hips and sit back. 4. Keep knees tracking over toes. 5. Push through mid-foot to stand.")
    elif any(k in query_lower for k in ["deadlift", "back", "lats", "pullup", "row"]):
        memories["episodic"].append("User asked about back exercises or deadlift technique, highlighting posterior chain training.")
        memories["semantic"].append("Deadlifts engage the posterior chain (hamstrings, glutes, lower back). A rounded spine under load causes lumbar compression.")
        memories["procedural"].append("Deadlift Form Protocol: 1. Stand with mid-foot under the bar. 2. Bend and grip the bar. 3. Drop hips slightly and flatten back. 4. Pull slack out of the bar. 5. Drive legs into the floor and stand.")
    elif any(k in query_lower for k in ["protein", "eat", "diet", "nutrition", "meal", "calorie", "bulk", "cut"]):
        memories["episodic"].append("User checked nutrition and dietary recommendations, focusing on protein intake or weight goals.")
        memories["semantic"].append("Daily protein intake for muscle building should be 1.6 to 2.2 grams per kilogram of body weight, spread across meals.")
        memories["procedural"].append("Daily Nutrition Setup: 1. Calculate target daily caloric intake. 2. Set protein target (1.8g/kg). 3. Divide protein intake into 4 equal meals. 4. Track hydration (3-4 liters daily).")
    elif any(k in query_lower for k in ["routine", "split", "plan", "program", "schedule", "week"]):
        memories["episodic"].append("User is designing or adjusting their weekly workout schedule and training split.")
        memories["semantic"].append("Muscle groups require 48 to 72 hours of rest between intense training sessions to optimize recovery and growth.")
        memories["procedural"].append("Weekly PPL Split Setup: 1. Day 1: Push (Chest, Shoulders, Triceps). 2. Day 2: Pull (Back, Biceps). 3. Day 3: Legs (Quads, Hamstrings, Calves). 4. Day 4: Rest. 5. Repeat or Rest.")
    else:
        memories["episodic"].append("User initiated conversation about general fitness goals and workout consistency.")
        memories["semantic"].append("Progressive overload (increasing weight, reps, or reducing rest) is required to trigger muscle hypertrophy.")
        memories["procedural"].append("Progressive Overload Application: 1. Keep a workout log. 2. Aim to add 1 rep or small weight increment each session. 3. Maintain strict form.")
        
    return memories


import asyncio
import threading

_EPISODIC_LOOP = None
_EPISODIC_LOOP_THREAD = None
_EPISODIC_LOOP_LOCK = threading.Lock()

def _ensure_episodic_loop():
    global _EPISODIC_LOOP, _EPISODIC_LOOP_THREAD
    with _EPISODIC_LOOP_LOCK:
        if _EPISODIC_LOOP is not None and _EPISODIC_LOOP.is_running():
            return _EPISODIC_LOOP
        ready = threading.Event()
        def _runner():
            global _EPISODIC_LOOP
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            _EPISODIC_LOOP = loop
            ready.set()
            try:
                loop.run_forever()
            finally:
                loop.close()
        _EPISODIC_LOOP_THREAD = threading.Thread(
            target=_runner, name="episodic-asyncio-loop", daemon=True
        )
        _EPISODIC_LOOP_THREAD.start()
        ready.wait()
        return _EPISODIC_LOOP

def run_episodic_async(coro):
    loop = _ensure_episodic_loop()
    fut = asyncio.run_coroutine_threadsafe(coro, loop)
    return fut.result()

def get_episodic_memory_block(username: str, api_key: str) -> str:
    from episodic_memory.storage.chroma_store import ChromaStore
    from episodic_memory.api.memory_api import MemoryAPI
    from episodic_memory.config import settings

    async def _get():
        store = ChromaStore(
            persist_dir=settings.chroma_persist_dir,
            api_key=api_key,
            embedding_model=settings.embedding_model,
        )
        await store.initialise()
        api = MemoryAPI(store)
        return await api.format_coaching_prompt_block(username)

    try:
        if api_key:
            os.environ["GEMINI_API_KEY"] = api_key
        return run_episodic_async(_get())
    except Exception as e:
        print(f"Error fetching episodic memory block: {e}")
        return ""

def save_turn_to_episodic_session(username: str, user_query: str, response_text: str, api_key: str):
    from episodic_memory.storage.chroma_store import ChromaStore
    from episodic_memory.models.session import Session, ConversationTurn
    from episodic_memory.config import settings
    import uuid
    from datetime import date, datetime

    async def _save():
        store = ChromaStore(
            persist_dir=settings.chroma_persist_dir,
            api_key=api_key,
            embedding_model=settings.embedding_model,
        )
        await store.initialise()
        
        # Check for unprocessed session for today
        unprocessed = await store.list_unprocessed_sessions(username)
        today = date.today()
        
        today_session = None
        for s in unprocessed:
            if s.occurred_on == today:
                today_session = s
                break
                
        if today_session is None:
            today_session = Session(
                id=str(uuid.uuid4()),
                user_id=username,
                turns=[],
                occurred_on=today,
            )
            
        # Append turns
        today_session.turns.append(ConversationTurn(
            role="user",
            content=user_query,
            timestamp=datetime.utcnow()
        ))
        today_session.turns.append(ConversationTurn(
            role="assistant",
            content=response_text,
            timestamp=datetime.utcnow()
        ))
        
        # Save back to store
        await store.save_session(today_session)
        
        # Keep only the latest 5 sessions in the database to prevent database bloating
        def _prune_old_sessions():
            res = store._sessions.get(
                where={"user_id": username},
                include=["metadatas"]
            )
            if res and res["ids"]:
                sessions_list = []
                for idx, meta in enumerate(res["metadatas"]):
                    s_id = res["ids"][idx]
                    s_obj = Session.model_validate_json(meta["data"])
                    sessions_list.append((s_id, s_obj))
                # Sort newest first based on occurred_on
                sessions_list.sort(key=lambda x: x[1].occurred_on, reverse=True)
                if len(sessions_list) > 5:
                    to_delete = [x[0] for x in sessions_list[5:]]
                    store._sessions.delete(ids=to_delete)
                    print(f"Purged {len(to_delete)} old sessions for user {username} to maintain a limit of 5.")
        await asyncio.to_thread(_prune_old_sessions)
        
    try:
        if api_key:
            os.environ["GEMINI_API_KEY"] = api_key
        run_episodic_async(_save())
    except Exception as e:
        print(f"Error saving turn to episodic session: {e}")


def generate_coach_response(user_query: str, username: str, use_memory: bool = True, prefetched_memories: dict | None = None) -> dict:
    """
    Generates a response from the coach. Instantiates and calls DietCoach
    (integrating user profile and memories fetched live from ChromaDB).
    """
    # 1. Fetch API Key
    api_key = None
    try:
        import streamlit as st
        api_key = st.session_state.get("gemini_api_key")
    except Exception:
        pass

    if not api_key:
        try:
            import streamlit as st
            if "GEMINI_API_KEY" in st.secrets:
                api_key = st.secrets["GEMINI_API_KEY"]
            elif "gemini_api_key" in st.secrets:
                api_key = st.secrets["gemini_api_key"]
        except Exception:
            pass

    if not api_key:
        api_key = os.environ.get("GEMINI_API_KEY")

    if not api_key:
        try:
            env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
            if os.path.exists(env_path):
                with open(env_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            if k.strip() == "GEMINI_API_KEY":
                                os.environ["GEMINI_API_KEY"] = v.strip().strip('"').strip("'")
                                api_key = os.environ["GEMINI_API_KEY"]
                                break
        except Exception:
            pass

    # Ensure GEMINI_API_KEY is in environment variables for episodic memory configuration
    if api_key:
        os.environ["GEMINI_API_KEY"] = api_key

    # If memory is disabled, bypass all memory retrievals and sub-coaches completely.
    # It executes a generic, stateless LLM call with no user profile, no conversation history, and no memories.
    if not use_memory:
        from fitness_coach import DietCoach
        response_text = ""
        extracted_memories = {"semantic": [], "episodic": [], "procedural": []}
        try:
            if not api_key:
                raise RuntimeError("API key missing, falling back to mock response")
            res = call_gemini_api(
                api_key=api_key,
                user_query=user_query,
                semantic_memories="",
                semantic_store_memories="",
                procedural_memory="",
                episodic_memories="",
                kg_memory="",
                chat_history="",
                user_profile="",
                use_memory=False
            )
            response_text = res["response"]
        except Exception as e:
            print(f"Error generating stateless response via call_gemini_api: {e}")
            coach = DietCoach(api_key=None, mock=True)
            fallback_session = {
                "coaching_paused": False,
                "pause_reason": None,
                "last_safety_event": None,
                "turn_count": 1,
                "chat_history": []
            }
            fallback_result = coach.chat(
                message=user_query,
                session=fallback_session,
                profile={"_no_memory": True},
                memories=""
            )
            response_text = fallback_result["reply"]
        return {
            "response": response_text,
            "memories": extracted_memories,
            "citations": None
        }

    # 2. Get user profile and memories from ChromaDB
    profile = get_user_profile_dict(username)
    parallel_res = prefetched_memories or get_dynamic_memories_parallel(username, user_query, api_key)
    # Fetch and append episodic memory block
    episodic_block = get_episodic_memory_block(username, api_key)

    # Helper function to format semantic memories for the procedural memory run
    def format_semantic_memories_only(semantic_mems: list, semantic_store_mems: list) -> str:
        lines = []
        if semantic_mems or semantic_store_mems:
            lines.append("### Semantic Memories (General Facts & Principles)")
            for m in semantic_mems:
                subtag = m.get("subtag", "implicit").strip(" -[]")
                lines.append(f"-[{subtag}] {m['response']}")
            for m in semantic_store_mems:
                subtag = m.get("subtag", "implicit").strip(" -[]")
                evidence_str = f" (Evidence: {m['evidence']})" if m.get("evidence") else ""
                lines.append(f"-[{subtag}] {m['response']}{evidence_str}")
        return "\n".join(lines)

    # Helper function to format user profile summary
    def format_user_profile_summary(prof: dict) -> str:
        if prof.get("_no_memory"):
            return "General fitness/nutrition coaching profile (no memory)."
        dc = prof.get("diet_constraints", {})
        pr = prof.get("preferences", {})
        gs = prof.get("goals", {})
        fp = prof.get("fitness_profile", {})

        fitness_summary = ""
        if fp:
            fitness_summary = (
                f" Workout types: {', '.join(fp.get('workout_types', [])) or 'none'}. "
                f"Trains {fp.get('workout_days_per_week', '?')}x/week, "
                f"typical workout time: {fp.get('typical_workout_time', 'unspecified')}. "
                f"Fitness goal: {fp.get('fitness_goal', 'general').replace('_', ' ')}. "
                f"Body weight: {fp.get('body_weight_kg') or 'not shared'} kg. "
                f"Height: {fp.get('height_cm') or 'not shared'} cm. "
                f"Workout level: {prof.get('workout_level', 'unspecified')}. "
                f"Training location: {prof.get('training_location', 'unspecified')}. "
                f"Workout duration limit: {prof.get('workout_duration_limit', 'unspecified')}."
            )

        raw_answers = ""
        if prof.get("raw_onboarding_answers"):
            raw_answers = "\nDetailed Onboarding: " + ", ".join(f"[{q}] {r}" for q, r in prof["raw_onboarding_answers"].items())

        return (
            f"Life stage: {prof.get('life_stage', 'unspecified')}. "
            f"Diet: {', '.join(dc.get('diet_type', [])) or 'none'}. "
            f"Allergies (HARD BLOCK): {', '.join(dc.get('allergies', [])) or 'none'}. "
            f"Intolerances: {', '.join(dc.get('intolerances', [])) or 'none'}. "
            f"Max prep: {pr.get('max_prep_minutes', 20)} min. "
            f"Cuisines liked: {', '.join(pr.get('cuisines_liked', []))}. "
            f"Goal: {gs.get('primary', 'general health')}. "
            f"Injuries/Health conditions: {prof.get('injuries', 'none')}."
            f"{fitness_summary}"
            f"{raw_answers}"
        )

    # 3. Instantiate and run DietCoach SOLELY for procedural memory
    from fitness_coach import DietCoach
    
    # We call procedural memory with user query, profile, and semantic memories only, with no conversation history.
    procedural_session = {
        "coaching_paused": False,
        "pause_reason": None,
        "last_safety_event": None,
        "turn_count": 1,
        "chat_history": []
    }
    
    semantic_memories_only_str = format_semantic_memories_only(
        parallel_res.get("semantic", []),
        parallel_res.get("semantic_store", [])
    )
    
    coach = DietCoach(api_key=api_key, mock=not bool(api_key))
    
    # Generate procedural guideline answer
    procedural_result = coach.chat(
        message=user_query,
        session=procedural_session,
        profile=profile,
        memories=semantic_memories_only_str
    )
    procedural_answer = procedural_result.get("reply", "")

    # Increment active turn count if use_memory is True and session exists
    try:
        import streamlit as st
        if "coach_session" not in st.session_state:
            st.session_state["coach_session"] = {
                "coaching_paused": False,
                "pause_reason": None,
                "last_safety_event": None,
                "turn_count": 0
            }
        st.session_state["coach_session"]["turn_count"] = st.session_state["coach_session"].get("turn_count", 0) + 1
    except Exception:
        pass

    # 4. Generate final response using call_gemini_api or fallback to DietCoach mock
    # Format all 5 memory types for the main prompt placeholders:
    # 1. Semantic memories (tag semantic)
    sem_mems = parallel_res.get("semantic") or []
    semantic_memories_formatted = "\n".join([f"-[{m.get('subtag', 'implicit').strip(' -[]')}] {m['response']}" for m in sem_mems]) if sem_mems else "No semantic memories found."
    
    # 2. Semantic store memories
    sem_store_mems = parallel_res.get("semantic_store") or []
    semantic_store_memories_formatted = "\n".join([
        f"-[{m.get('subtag', 'implicit').strip(' -[]')}] {m['response']}" + (f" (Evidence: {m['evidence']})" if m.get("evidence") else "")
        for m in sem_store_mems
    ]) if sem_store_mems else "No semantic store memories found."
    
    # 3. Procedural memory (response string from procedural run)
    procedural_memory_formatted = procedural_answer if procedural_answer else "No procedural instructions generated."
    
    # 4. Episodic memories (tag episodic + structured block)
    epi_mems = parallel_res.get("episodic") or []
    epi_parts = []
    for m in epi_mems:
        subtag = m.get("subtag", "implicit").strip(" -[]")
        epi_parts.append(f"-[{subtag}] {m['response']}")
    if episodic_block:
        epi_parts.append(episodic_block)
    episodic_memories_formatted = "\n".join(epi_parts) if epi_parts else "No episodic memories found."
    
    # 5. Knowledge Graph
    kg_result = parallel_res.get("kg")
    kg_memory_formatted = "No knowledge graph relations found."
    if kg_result:
        if isinstance(kg_result, dict):
            answer = kg_result.get("answer") or ""
            triples = kg_result.get("triples") or []
            parts = []
            if answer and "no answer returned" not in answer.lower():
                parts.append(f"Answer: {answer}")
            if triples:
                triples_str = "\n".join([f"- ({t[0]} - {t[1]} -> {t[2]})" for t in triples[:10]])
                parts.append(f"Relationships:\n{triples_str}")
            if parts:
                kg_memory_formatted = "\n".join(parts)
        elif isinstance(kg_result, str):
            kg_memory_formatted = kg_result

    # Format CHAT_HISTORY (top 5 conversation history turns)
    chat_history_str = ""
    history_turns = []
    try:
        import streamlit as st
        if "messages" in st.session_state:
            raw_history = st.session_state["messages"]
            # Exclude current user message at the end
            if raw_history and raw_history[-1].get("role") == "user":
                history_to_use = raw_history[:-1]
            else:
                history_to_use = raw_history
            for msg in history_to_use[-5:]:
                role = "User" if msg.get("role") == "user" else "Assistant"
                text = msg.get("text") or msg.get("content") or ""
                if text:
                    history_turns.append(f"{role}: {text}")
            if history_turns:
                chat_history_str = "\n".join(history_turns)
    except Exception:
        pass
        
    if not chat_history_str:
        chat_history_str = "No prior conversation history."

    # Format User Profile
    user_profile_str = format_user_profile_summary(profile)

    response_text = ""
    extracted_memories = {"semantic": [], "episodic": [], "procedural": []}

    try:
        if not api_key:
            raise RuntimeError("API key missing, falling back to mock response")
            
        res = call_gemini_api(
            api_key=api_key,
            user_query=user_query,
            semantic_memories=semantic_memories_formatted,
            semantic_store_memories=semantic_store_memories_formatted,
            procedural_memory=procedural_memory_formatted,
            episodic_memories=episodic_memories_formatted,
            kg_memory=kg_memory_formatted,
            chat_history=chat_history_str,
            user_profile=user_profile_str,
            use_memory=True
        )
        response_text = res["response"]
        extracted_memories = extract_memories(api_key, user_query, response_text)
    except Exception as e:
        print(f"Error generating response via call_gemini_api: {e}")
        # Build full memories string for coach.chat fallback compatibility
        fallback_memories_list = []
        if semantic_memories_only_str:
            fallback_memories_list.append(semantic_memories_only_str)
        if episodic_memories_formatted and "No episodic memories" not in episodic_memories_formatted:
            fallback_memories_list.append(episodic_memories_formatted)
        if kg_memory_formatted and "No knowledge graph" not in kg_memory_formatted:
            fallback_memories_list.append(kg_memory_formatted)
        fallback_memories_str = "\n\n".join(fallback_memories_list)

        # Restore active history to session for fallback mock
        fallback_session = {
            "coaching_paused": False,
            "pause_reason": None,
            "last_safety_event": None,
            "turn_count": 1
        }
        try:
            import streamlit as st
            if "coach_session" in st.session_state:
                fallback_session = dict(st.session_state["coach_session"])
            if "messages" in st.session_state:
                fallback_session["chat_history"] = st.session_state["messages"]
        except Exception:
            pass

        fallback_result = coach.chat(
            message=user_query,
            session=fallback_session,
            profile=profile,
            memories=fallback_memories_str
        )
        response_text = fallback_result["reply"]
        if api_key:
            extracted_memories = extract_memories(api_key, user_query, response_text)
        else:
            extracted_memories = simulate_memory_extraction(user_query)

    # 5. Save turn to episodic session
    if api_key:
        save_turn_to_episodic_session(username, user_query, response_text, api_key)
    else:
        save_turn_to_episodic_session(username, user_query, response_text, "")


    citations = {
        "semantic": sem_mems,
        "semantic_store": sem_store_mems,
        "episodic": epi_mems,
        "episodic_block": episodic_block,
        "procedural": parallel_res.get("procedural") or [],
        "procedural_answer": procedural_answer,
        "kg": kg_result
    }

    return {
        "response": response_text,
        "memories": extracted_memories,
        "citations": citations
    }


def run_episodic_batch_one(username: str, api_key: str) -> dict:
    from episodic_memory.pipeline.frequent_batch import run_batch_one
    import database_chroma_new as database

    async def _run():
        # Get semantic memories
        sem_mems = database.get_memories_by_tag(username, "semantic")
        semantic_texts = [m["response"] for m in sem_mems]
        
        return await run_batch_one(username, semantic_texts, min_words=0)

    try:
        if api_key:
            os.environ["GEMINI_API_KEY"] = api_key
        res = run_episodic_async(_run())
        return {
            "success": True,
            "sessions_received": res.sessions_received,
            "sessions_filtered": res.sessions_filtered,
            "sessions_processed": res.sessions_processed,
            "records_created": res.records_created,
            "records_superseded": res.records_superseded,
            "records_merged": res.records_merged,
            "records_dropped_no_quotes": res.records_dropped_no_quotes,
            "records_dropped_invalid_date": res.records_dropped_invalid_date,
        }
    except Exception as e:
        print(f"Error running Batch 1: {e}")
        return {"success": False, "error": str(e)}

def run_episodic_batch_two(username: str, api_key: str) -> dict:
    from episodic_memory.pipeline.weekly_batch import run_batch_two

    async def _run():
        return await run_batch_two(username, processing_days=365)

    try:
        if api_key:
            os.environ["GEMINI_API_KEY"] = api_key
        res = run_episodic_async(_run())
        return {
            "success": True,
            "arcs_advanced": res.arc_result.arcs_advanced,
            "arcs_concluded": res.arc_result.arcs_concluded,
            "arcs_created": res.arc_result.arcs_created,
            "arcs_abandoned": res.arc_result.arcs_abandoned,
            "reflections_created": res.reflection_result.reflections_created,
            "reflections_updated": res.reflection_result.reflections_updated,
            "reflections_downgraded": res.reflection_result.reflections_downgraded,
            "reflections_deactivated": res.reflection_result.reflections_deactivated,
        }
    except Exception as e:
        print(f"Error running Batch 2: {e}")
        return {"success": False, "error": str(e)}

def get_episodic_snapshot(username: str, api_key: str):
    from episodic_memory.storage.chroma_store import ChromaStore
    from episodic_memory.config import settings

    async def _get():
        store = ChromaStore(
            persist_dir=settings.chroma_persist_dir,
            api_key=api_key,
            embedding_model=settings.embedding_model,
        )
        await store.initialise()
        return await store.get_snapshot(username)

    try:
        if api_key:
            os.environ["GEMINI_API_KEY"] = api_key
        return run_episodic_async(_get())
    except Exception as e:
        print(f"Error getting snapshot: {e}")
        return None

def get_episodic_arcs(username: str, api_key: str, state: str | None = None):
    from episodic_memory.storage.chroma_store import ChromaStore
    from episodic_memory.config import settings

    async def _get():
        store = ChromaStore(
            persist_dir=settings.chroma_persist_dir,
            api_key=api_key,
            embedding_model=settings.embedding_model,
        )
        await store.initialise()
        return await store.list_arcs(username, state=state)

    try:
        if api_key:
            os.environ["GEMINI_API_KEY"] = api_key
        return run_episodic_async(_get())
    except Exception as e:
        print(f"Error listing arcs: {e}")
        return []

def get_episodic_reflections(username: str, api_key: str, active_only: bool = True):
    from episodic_memory.storage.chroma_store import ChromaStore
    from episodic_memory.config import settings

    async def _get():
        store = ChromaStore(
            persist_dir=settings.chroma_persist_dir,
            api_key=api_key,
            embedding_model=settings.embedding_model,
        )
        await store.initialise()
        return await store.list_reflections(username, active_only=active_only)

    try:
        if api_key:
            os.environ["GEMINI_API_KEY"] = api_key
        return run_episodic_async(_get())
    except Exception as e:
        print(f"Error listing reflections: {e}")
        return []

def get_episodic_records(username: str, api_key: str, active_only: bool = False):
    from episodic_memory.storage.chroma_store import ChromaStore
    from episodic_memory.config import settings

    async def _get():
        store = ChromaStore(
            persist_dir=settings.chroma_persist_dir,
            api_key=api_key,
            embedding_model=settings.embedding_model,
        )
        await store.initialise()
        return await store.list_records(username, active_only=active_only)

    try:
        if api_key:
            os.environ["GEMINI_API_KEY"] = api_key
        return run_episodic_async(_get())
    except Exception as e:
        print(f"Error listing records: {e}")
        return []

def search_episodic_records(username: str, query: str, api_key: str, active_only: bool = False):
    from episodic_memory.storage.chroma_store import ChromaStore
    from episodic_memory.retrieval.semantic_search import SemanticRetriever
    from episodic_memory.config import settings

    async def _search():
        store = ChromaStore(
            persist_dir=settings.chroma_persist_dir,
            api_key=api_key,
            embedding_model=settings.embedding_model,
        )
        await store.initialise()
        retriever = SemanticRetriever(store=store)
        results = await retriever.search_episodes(
            user_id=username,
            query=query,
            top_k=10,
            active_only=active_only,
        )
        return [r.record for r in results]

    try:
        if api_key:
            os.environ["GEMINI_API_KEY"] = api_key
        return run_episodic_async(_search())
    except Exception as e:
        print(f"Error searching episodic records: {e}")
        return []

def edit_episodic_record(record_id: str, coach_note: str, api_key: str):
    from episodic_memory.storage.chroma_store import ChromaStore
    from episodic_memory.api.memory_api import MemoryAPI
    from episodic_memory.config import settings

    async def _edit():
        store = ChromaStore(
            persist_dir=settings.chroma_persist_dir,
            api_key=api_key,
            embedding_model=settings.embedding_model,
        )
        await store.initialise()
        api = MemoryAPI(store)
        return await api.edit_record(record_id, coach_note=coach_note)

    try:
        if api_key:
            os.environ["GEMINI_API_KEY"] = api_key
        return run_episodic_async(_edit())
    except Exception as e:
        print(f"Error editing record: {e}")
        return None

def deactivate_episodic_record(record_id: str, api_key: str):
    from episodic_memory.storage.chroma_store import ChromaStore
    from episodic_memory.api.memory_api import MemoryAPI
    from episodic_memory.config import settings

    async def _deactivate():
        store = ChromaStore(
            persist_dir=settings.chroma_persist_dir,
            api_key=api_key,
            embedding_model=settings.embedding_model,
        )
        await store.initialise()
        api = MemoryAPI(store)
        return await api.deactivate_record(record_id)

    try:
        if api_key:
            os.environ["GEMINI_API_KEY"] = api_key
        return run_episodic_async(_deactivate())
    except Exception as e:
        print(f"Error deactivating record: {e}")
        return None

def reinstate_episodic_record(record_id: str, api_key: str):
    from episodic_memory.storage.chroma_store import ChromaStore
    from episodic_memory.api.memory_api import MemoryAPI
    from episodic_memory.config import settings

    async def _reinstate():
        store = ChromaStore(
            persist_dir=settings.chroma_persist_dir,
            api_key=api_key,
            embedding_model=settings.embedding_model,
        )
        await store.initialise()
        api = MemoryAPI(store)
        return await api.reinstate_record(record_id)

    try:
        if api_key:
            os.environ["GEMINI_API_KEY"] = api_key
        return run_episodic_async(_reinstate())
    except Exception as e:
        print(f"Error reinstating record: {e}")
        return None

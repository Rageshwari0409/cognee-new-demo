import os
import sys
import uuid
from datetime import date, datetime, timedelta

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import auth
import database_chroma_new as database
from episodic_memory.config import settings
from episodic_memory.storage.chroma_store import ChromaStore
from episodic_memory.models.session import Session, ConversationTurn
from episodic_memory.models.episode_record import EpisodeRecord
from episodic_memory.models.episode_arc import EpisodeArc
from episodic_memory.models.reflection import Reflection
from episodic_memory.models.engagement_snapshot import EngagementSnapshot
from episodic_memory.models.episode_types import EpisodeType, ArcType, DominantFocus

def seed_sarah_if_needed(api_key: str = ""):
    username = "sarah"
    password = "sarah"
    
    # 1. Register User in auth DB
    if not auth.user_exists(username):
        print("Seeding user 'sarah'...")
        success, msg = auth.register_user(username, password)
        if not success:
            print(f"Failed to register user 'sarah': {msg}")
            return
    else:
        print("User 'sarah' already exists. Re-verifying database memories...")
        
    # Check if database is already seeded in ChromaDB to avoid duplicates
    existing_mems = database.get_memories_by_tag(username, "semantic")
    existing_epi = database.get_memories_by_tag(username, "episodic")
    if existing_mems and existing_epi:
        print("Sarah already has memories in ChromaDB. Skipping database seeding.")
        return
        
    print("Sarah needs seeding or re-seeding. Clearing existing memories and seeding now...")
    collection = database._get_chroma_collection()
    if collection:
        try:
            collection.delete(where={"username": username})
        except Exception as e:
            print(f"Error clearing existing memories for Sarah: {e}")

        
    # 2. Seed Explicit Profile and Semantic Memories
    # Questions and answers to match Sarah's profile
    onboarding_data = {
        "What is your current life stage?": "Working Professional",
        "What is your primary fitness goal?": "Training for a marathon",
        "How often do you currently work out?": "2-3 times a week",
        "Do you have any injuries or health conditions?": "None",
        "How would you describe your workout level?": "Intermediate (Comfortable with most exercises)",
        "Where do you prefer to train?": "At the Gym",
        "Which area would you like to focus on?": "Cardio & Endurance",
        "How much time can you dedicate to a single workout?": "30-60 mins",
        "What is your diet preference?": "Vegan",
        "What is your current height (in cm)?": "170",
        "What is your current weight (in kg)?": "75",
        "What is your country?": "Canada",
        "Do you have any food allergies or intolerances?": "lactose, soy",
        "What is the maximum time you can dedicate to meal prep?": "20 mins",
        "Which cuisines do you prefer?": "Indian, Mediterranean",
        "What is your typical workout time?": "Evening"
    }
    
    for q, r in onboarding_data.items():
        database.save_memory(username, "semantic", q, r, subtag="explicit")
        
    # Save a few implicit semantic memories (general sports science facts)
    database.save_memory(
        username, "semantic", "",
        "Iron absorption from non-heme vegan sources (like spinach) is significantly enhanced when consumed with Vitamin C.",
        subtag="implicit"
    )
    database.save_memory(
        username, "semantic", "",
        "High protein sources on a strict vegan diet include tofu, tempeh, lentils, seitan, and pea protein shakes.",
        subtag="implicit"
    )
    database.save_memory(
        username, "procedural", "",
        "Zone 2 Endurance Progression: 1. Target heart rate 60-70% of max. 2. Steady state pace. 3. Maintain 45-90 mins per session. 4. Run, cycle, or swim.",
        subtag="implicit"
    )

    # 3. Seed Episodic Memory Storage
    # Prepare ChromaStore
    store = ChromaStore(
        persist_dir=settings.chroma_persist_dir,
        api_key=api_key or os.environ.get("GEMINI_API_KEY", "mock_key"),
        embedding_model=settings.embedding_model,
    )
    
    import asyncio
    
    # We will use an inner async function to run with the store
    async def _async_seeding():
        await store.initialise()
        
        # Define conversation history turns
        sarah_test_history = [
            {"session": 1, "offset_days": 18, "user": "Hey there! I'm Sarah, just turned 29 and trying to clean up my routine. Can you help me map out a solid plant-based pre-run meal? I'm completely vegan and need sustained energy.", "bot": "Welcome, Sarah! For sustained plant-based energy, try whole-grain toast with almond butter and banana slices, or oatmeal with chia seeds. Aim to eat this 2 to 3 hours before your run."},
            {"session": 2, "offset_days": 17, "user": "Quick question about my weekly split. I'm currently training for a marathon, so my legs get a lot of mileage. I only hit upper body weights twice a week and do one separate cardio session on top of the running. Is that enough lifting?", "bot": "Yes, two dedicated upper body strength sessions are excellent for distance runners. It helps maintain posture and core stability without overtaxing your lower body."},
            {"session": 3, "offset_days": 15, "user": "My upper body workouts are feeling good, but I'm struggling with post-workout soreness. Since I don't eat dairy or meat, what are the best vegan protein sources to recover quickly after heavy lifting?", "bot": "To optimize muscle recovery on a vegan diet, focus on high-protein options like tofu, tempeh, lentils, and seitan. A pea or hemp protein shake within 45 minutes helps too."},
            {"session": 4, "offset_days": 14, "user": "For my one non-running cardio session a week, I usually do high-intensity rowing. Lately, it's feeling way too exhausting alongside my marathon mileage. Should I drop the difficulty or switch exercises?", "bot": "Rowing is intense. Switch that single cardio session to low-impact, moderate-intensity swimming or cycling at a steady Zone 2 endurance level."},
            {"session": 5, "offset_days": 12, "user": "I did my first upper body weight day of the week yesterday. I tried pushing the difficulty by increasing the dumbbells for my shoulder presses, but my form completely fell apart by the third set. Should I drop the weight back down?", "bot": "Yes, always prioritize form over weight. Drop the dumbbell weight slightly to a level where you can cleanly complete all sets with perfect control."},
            {"session": 6, "offset_days": 11, "user": "I've been feeling a bit sluggish on my mid-week runs. As a vegan runner, should I be worried about iron? What can I eat to fix this?", "bot": "Pair iron-rich vegan foods like spinach and lentils with Vitamin C to boost absorption. Avoid coffee or tea right after meals, as they block iron uptake."},
            {"session": 7, "offset_days": 9, "user": "For my twice-weekly upper body sessions, I want to make pull-ups harder since my bodyweight is feeling too easy. How can I safely bump up the difficulty level?", "bot": "You can increase the difficulty by slowing down the eccentric lowering phase to 4 seconds per repetition, or holding a small dumbbell between your feet."},
            {"session": 8, "offset_days": 8, "user": "I have bad news. During my long run this morning, I tripped on a curb and rolled my left ankle badly. It's swollen and hurts to put weight on it. What should I do about my marathon training plan now?", "bot": "Please prioritize medical evaluation. Immediately rest, elevate, and ice the ankle. Pause all running and standing exercises until a professional clears you."},
            {"session": 9, "offset_days": 7, "user": "Doctor said it's a moderate ankle sprain. No running or lower body impact for at least 3 weeks. Can I still do my twice-weekly upper body weights if I stay seated?", "bot": "Yes, you can safely continue upper body training using seated or lying exercises like chest presses on a flat bench or seated rows with zero weight on that foot."},
            {"session": 10, "offset_days": 6, "user": "Since my ankle injury cut out my running and my weekly swimming session, my activity level is way down. Should I change my vegan meal portions so I don't gain weight while resting?", "bot": "Reduce your overall carbohydrate intake slightly since your expenditure dropped, but maintain your high vegan protein intake to support healing tissues."},
            {"session": 11, "offset_days": 5, "user": "I did my seated upper body weight workout today. Keeping the difficulty level moderate felt good, but I noticed a little throbbing in my ankle just from moving dumbbells around the gym floor.", "bot": "Reduce the difficulty by using fixed gym machines instead of loose dumbbells. Machines eliminate the need to carry weights across the gym floor."},
            {"session": 12, "offset_days": 4, "user": "My ankle swelling is going down slowly. Are there any specific vegan foods I can eat that help naturally lower inflammation?", "bot": "Incorporate anti-inflammatory vegan staples like turmeric with black pepper, ginger, walnuts, chia seeds, and antioxidant-rich berries."},
            {"session": 13, "offset_days": 3, "user": "It’s been about two weeks since the injury. My ankle feels stable when walking on flat ground, but running is still out. Can I try bringing back my 1x weekly cardio session using an upper-body ergometer (arm bike)?", "bot": "Yes, an upper-body ergometer is a fantastic way to get your weekly cardio session in without placing any weight or stress on your healing ankle."},
            {"session": 14, "offset_days": 2, "user": "My ankle is feeling much stronger. For my upper body weight days, can I move from the machines back to dumbbells if I stay seated? I want to raise the difficulty back to where it was before the sprain.", "bot": "Yes, start with a lighter weight than your pre-injury baseline to test your stabilization. If there is zero discomfort, you can scale the difficulty back up."},
            {"session": 15, "offset_days": 1, "user": "The doctor says my ankle is healing perfectly, and I can start light jogging next week. Since I missed three weeks of marathon training, should I jump right back into my old mileage targets?", "bot": "Do not jump straight back. Reduce your initial running volume by 40-50% compared to where you left off, and gradually scale back up over 2 to 3 weeks."}
        ]
        
        today = date.today()
        
        # Save raw sessions as processed
        session_ids = []
        for s in sarah_test_history:
            s_date = today - timedelta(days=s["offset_days"])
            s_id = f"session-sarah-{s['session']}"
            session_ids.append(s_id)
            
            session_obj = Session(
                id=s_id,
                user_id=username,
                turns=[
                    ConversationTurn(role="user", content=s["user"], timestamp=datetime.utcnow() - timedelta(days=s["offset_days"])),
                    ConversationTurn(role="assistant", content=s["bot"], timestamp=datetime.utcnow() - timedelta(days=s["offset_days"]))
                ],
                occurred_on=s_date,
                processed=True,
                processed_at=datetime.utcnow()
            )
            await store.save_session(session_obj)
            
        # Seed 1 EpisodeArc (Injury Recovery)
        arc_id = "arc-sarah-ankle-sprain"
        arc_obj = EpisodeArc(
            id=arc_id,
            user_id=username,
            arc_type=ArcType.INJURY,
            state="open",
            opened_on=today - timedelta(days=8),
            title="Left Ankle Sprain Recovery",
            summary="Sarah sustained a moderate left ankle sprain during a long run in week 2. She paused running and transitioned to non-weight-bearing exercises (seated upper weights, arm ergometer) to keep fitness while avoiding loading the joint.",
            coach_note="Check on her ankle recovery. Do not prescribe high impact or standing movements. Begin running return at 50% baseline volume.",
            source_episode_ids=[], # will link
            source_episode_summaries=[]
        )
        
        # Seed 5 EpisodeRecords
        episodes_data = [
            {
                "id": "ep-sarah-onboarding-nutrition",
                "type": EpisodeType.NUTRITION,
                "situation": "Sarah is a 29yo female working professional, strict vegan, training for a marathon.",
                "intent": "Obtain a reliable plant-based pre-run meal split for sustained energy.",
                "outcome": "Prescribed whole-grain toast with almond butter/banana or oatmeal with chia seeds eaten 2-3 hours pre-run.",
                "sig": "notable",
                "coach_note": "Acknowledge vegan preferences and confirm energy status during runs.",
                "date_offset": 18,
                "quotes": ["Hey there! I'm Sarah, just turned 29... help me map out a solid plant-based pre-run meal? I'm completely vegan and need sustained energy."]
            },
            {
                "id": "ep-sarah-routine-weights",
                "type": EpisodeType.WORKOUT,
                "situation": "Sarah's marathon split features high leg mileage, twice-weekly upper body weight sessions, and one separate cardio slot.",
                "intent": "Validate if twice-weekly upper body strength training is sufficient.",
                "outcome": "Validated that two strength sessions are optimal for stability and posture without overtaxing lower body.",
                "sig": "one_off",
                "coach_note": "Maintain a 2x upper body training frequency to assist running economy.",
                "date_offset": 17,
                "quotes": ["I only hit upper body weights twice a week and do one separate cardio session on top of the running. Is that enough lifting?"]
            },
            {
                "id": "ep-sarah-ankle-injury",
                "type": EpisodeType.CHALLENGE,
                "subtype": "injury",
                "situation": "Sarah was running heavy mileage training for a marathon.",
                "intent": "Perform a scheduled long run session Sunday morning.",
                "outcome": "Rolled left ankle on a curb, causing a moderate sprain with swelling and inability to bear weight. Advised to rest, ice, and get medical clearance.",
                "sig": "turning_point",
                "coach_note": "CRITICAL: Left ankle sprain reported. Stop all standing exercises and running. Require check-in before prescribing legs.",
                "date_offset": 8,
                "quotes": ["During my long run this morning, I tripped on a curb and rolled my left ankle badly. It's swollen and hurts to put weight on it."]
            },
            {
                "id": "ep-sarah-post-injury-weights",
                "type": EpisodeType.WORKOUT,
                "situation": "Sarah has a sprained left ankle and is restricted from lower-body impact/running for 3 weeks.",
                "intent": "Continue twice-weekly upper body weights safely.",
                "outcome": "Transitioned successfully to seated chest presses and rows, ensuring zero weight load on the sprained foot.",
                "sig": "notable",
                "coach_note": "Strictly prescribe seated or lying upper body exercises.",
                "date_offset": 7,
                "quotes": ["Doctor said it's a moderate ankle sprain... Can I still do my twice-weekly upper body weights if I stay seated?"]
            },
            {
                "id": "ep-sarah-dumbbell-throbbing",
                "type": EpisodeType.CHALLENGE,
                "subtype": "injury",
                "situation": "Sarah is recovering from an ankle sprain but continues seated upper weights.",
                "intent": "Complete upper body dumbbells routine safely.",
                "outcome": "Experienced ankle throbbing due to carrying heavy dumbbells across the gym floor. Transitioned to fixed gym machines to eliminate walking with weights.",
                "sig": "notable",
                "coach_note": "Avoid loose weights to eliminate gym floor transport stress on her sprained foot. Stick to machine movements.",
                "date_offset": 5,
                "quotes": ["Keeping the difficulty level moderate felt good, but I noticed a little throbbing in my ankle just from moving dumbbells around the gym floor."]
            }
        ]
        
        episode_ids = []
        for ep in episodes_data:
            episode_ids.append(ep["id"])
            ep_date = today - timedelta(days=ep["date_offset"])
            
            record = EpisodeRecord(
                id=ep["id"],
                user_id=username,
                episode_type=ep["type"],
                situation=ep["situation"],
                intent=ep["intent"],
                outcome=ep["outcome"],
                significance=ep["sig"],
                coach_note=ep["coach_note"],
                occurred_on=ep_date,
                source_session_ids=session_ids,
                source_quotes=ep["quotes"],
                challenge_subtype=ep.get("subtype"),
                arc_id=arc_id if ep["id"] in ["ep-sarah-ankle-injury", "ep-sarah-post-injury-weights", "ep-sarah-dumbbell-throbbing"] else None,
                created_at=datetime.utcnow() - timedelta(days=ep["date_offset"]),
                user_verified=True,
                active=True
            )
            await store.save_record(record)
            
            # Propagate to raw ChromaDB memories collection
            ep_type_str = ep["type"].value if hasattr(ep["type"], "value") else str(ep["type"])
            database.save_memory(
                username,
                "episodic",
                "",
                f"Type: {ep_type_str} | Situation: {ep['situation']} | Intent: {ep['intent']} | Outcome: {ep['outcome']} | Coach Note: {ep['coach_note']}",
                subtag="implicit"
            )
            
        # Link episodes back to arc
        arc_obj.source_episode_ids = ["ep-sarah-ankle-injury", "ep-sarah-post-injury-weights", "ep-sarah-dumbbell-throbbing"]
        arc_obj.source_episode_summaries = [
            "Rolled left ankle on curb (moderate sprain).",
            "Transitioned to seated upper body chest presses.",
            "Throbbing from carrying weights, moved to fixed machine rows."
        ]
        await store.save_arc(arc_obj)
        
        # Seed 1 Reflection
        ref_id = "ref-sarah-injury-compliance"
        ref_obj = Reflection(
            id=ref_id,
            user_id=username,
            pattern_type="recovery",
            observation="Sarah demonstrates strong compliance and positive adaptations when addressing injury, shifting routines to seated movements and fixed machines to maintain workload safely.",
            coach_action="Always prioritize joint safety: check on injury recovery, and substitute free dumbbell standing splits with seated machine options.",
            supporting_episode_ids=["ep-sarah-ankle-injury", "ep-sarah-post-injury-weights", "ep-sarah-dumbbell-throbbing"],
            supporting_episode_summaries=[
                "Rolled left ankle on curb (moderate sprain).",
                "Transitioned to seated upper body chest presses.",
                "Throbbing from carrying weights, moved to fixed machine rows."
            ],
            confidence="high",
            first_observed=today - timedelta(days=8),
            last_confirmed=today - timedelta(days=5),
            episode_count=3,
            active=True
        )
        await store.save_reflection(ref_obj)
        
        # Seed 1 EngagementSnapshot
        counts = {t: 0 for t in EpisodeType}
        counts[EpisodeType.WORKOUT] = 2
        counts[EpisodeType.CHALLENGE] = 2
        counts[EpisodeType.NUTRITION] = 1
        
        snapshot_obj = EngagementSnapshot(
            user_id=username,
            window_days=14,
            episode_type_counts=counts,
            dominant_focus=DominantFocus.INJURY_CONCERNED,
            activity_level="reduced",
            coach_signal="ACTIVE INJURY SIGNAL: The user has reported recent challenge episodes involving left ankle injury. Open this conversation by checking in on their ankle recovery. Do not suggest running splits or loading standing weights until she confirms clearance.",
            created_at=datetime.utcnow()
        )
        await store.save_snapshot(snapshot_obj)
        
        print("Sarah's episodic records seeded successfully.")
        
    try:
        from episodic_memory.extraction.llm_client import LLMClient
        # Run the async loop helper in our main threads
        from llm import run_episodic_async
        run_episodic_async(_async_seeding())
    except Exception as e:
        print(f"Error seeding episodic database for Sarah: {e}")
        import traceback
        traceback.print_exc()

def seed_david_if_needed(api_key: str = ""):
    username = "david"
    password = "david"
    
    # 1. Register User in auth DB
    if not auth.user_exists(username):
        print("Seeding user 'david'...")
        success, msg = auth.register_user(username, password)
        if not success:
            print(f"Failed to register user 'david': {msg}")
            return
    else:
        print("User 'david' already exists. Re-verifying database memories...")
        
    # Check if database is already seeded in ChromaDB to avoid duplicates
    existing_mems = database.get_memories_by_tag(username, "semantic")
    existing_epi = database.get_memories_by_tag(username, "episodic")
    if existing_mems and existing_epi:
        print("David already has memories in ChromaDB. Skipping database seeding.")
        return
        
    print("David needs seeding or re-seeding. Clearing existing memories and seeding now...")
    collection = database._get_chroma_collection()
    if collection:
        try:
            collection.delete(where={"username": username})
        except Exception as e:
            print(f"Error clearing existing memories for David: {e}")
    
    # 2. Seed Explicit Profile and Semantic Memories
    onboarding_data = {
        "What is your current life stage?": "Working Professional",
        "What is your primary fitness goal?": "Muscle gain & hypertrophy",
        "How often do you currently work out?": "4-5 times a week",
        "Do you have any injuries or health conditions?": "None",
        "How would you describe your workout level?": "Advanced",
        "Where do you prefer to train?": "At the Gym",
        "Which area would you like to focus on?": "Strength & Hypertrophy",
        "How much time can you dedicate to a single workout?": "60-90 mins",
        "What is your diet preference?": "Non Vegan",
        "What is your current height (in cm)?": "182",
        "What is your current weight (in kg)?": "88",
        "What is your country?": "United States",
        "Do you have any food allergies or intolerances?": "None",
        "What is the maximum time you can dedicate to meal prep?": "30 mins",
        "Which cuisines do you prefer?": "American, Italian",
        "What is your typical workout time?": "Morning"
    }
    
    for q, r in onboarding_data.items():
        database.save_memory(username, "semantic", q, r, subtag="explicit")
        
    # Save a few implicit semantic memories
    database.save_memory(
        username, "semantic", "",
        "Muscle hypertrophy is driven primarily by mechanical tension, which requires consistently training close to concentric failure (1-3 reps in reserve).",
        subtag="implicit"
    )
    database.save_memory(
        username, "semantic", "",
        "Whey protein isolate is rapidly digested and has a high leucine content, making it highly effective for triggering muscle protein synthesis post-workout.",
        subtag="implicit"
    )
    database.save_memory(
        username, "procedural", "",
        "Push/Pull/Legs Hypertrophy Protocol: 1. Target 10-15 working sets per muscle group per week. 2. Lift with a 2-3 second control on the eccentric phase. 3. Track weights and reps to apply progressive overload weekly.",
        subtag="implicit"
    )

    # 3. Seed Episodic Memory Storage
    store = ChromaStore(
        persist_dir=settings.chroma_persist_dir,
        api_key=api_key or os.environ.get("GEMINI_API_KEY", "mock_key"),
        embedding_model=settings.embedding_model,
    )
    
    import asyncio
    
    async def _async_seeding():
        await store.initialise()
        
        david_test_history = [
            {"session": 1, "offset_days": 18, "user": "Hey! I'm David, 32. I'm looking to put on some serious muscle mass over the next 6 months. I'm currently training 4 days a week on an upper/lower split, but I want to transition to a PPL split. Can you help me set that up?", "bot": "Hey David! To maximize muscle growth, a Push/Pull/Legs (PPL) split is a fantastic choice for a 4-5 day frequency. We can structure a rotating PPL split or a 3-days-on, 1-day-off pattern to hit each muscle group every 4-5 days. Let's design the Push day first with chest, shoulders, and triceps."},
            {"session": 2, "offset_days": 17, "user": "Sounds good. For my diet, I'm not vegan and have no food allergies. I want to hit around 180 grams of protein daily. What are the best protein sources to structure my meals around?", "bot": "To hit 180g of protein, focus on high-quality sources like chicken breast, lean beef, eggs, wild salmon, and whey protein isolate. Distributing this into 4 meals of 45g each will optimize muscle protein synthesis throughout the day."},
            {"session": 3, "offset_days": 15, "user": "I started the push day yesterday and loved it. I did incline barbell bench presses. However, by my fourth set, I felt a sharp pinch in my front right shoulder. Should I switch to dumbbells or change the angle?", "bot": "A sharp pinch in the shoulder is a warning sign. Switch to incline dumbbell presses with a neutral grip (palms facing in), which reduces subacromial impingement, or lower the incline angle slightly to reduce stress on the anterior deltoid."},
            {"session": 4, "offset_days": 14, "user": "I tried the neutral-grip dumbbell press today and it felt much better, no shoulder pinch at all. I want to make sure my leg day is solid too. Should I stick to squats or add leg presses to minimize spine load?", "bot": "Excellent news on the shoulder! To maximize quad growth while minimizing spinal compression, combining barbell squats with hack squats or leg presses is ideal. This lets you push to failure safely."},
            {"session": 5, "offset_days": 12, "user": "For deadlifts on pull day, I'm trying to lift heavy, but my lower back feels extremely tight and fatigued the day after. My form might be breaking down on the final reps. What should I change?", "bot": "Lower back tightness after deadlifts usually indicates spine rounding or loss of core bracing. Reduce the barbell weight by 15-20%, focus on pulling the slack out of the bar, and keep your core braced as if about to be punched."}
        ]
        
        today = date.today()
        
        # Save raw sessions as processed
        session_ids = []
        for s in david_test_history:
            s_date = today - timedelta(days=s["offset_days"])
            s_id = f"session-david-{s['session']}"
            session_ids.append(s_id)
            
            session_obj = Session(
                id=s_id,
                user_id=username,
                turns=[
                    ConversationTurn(role="user", content=s["user"], timestamp=datetime.utcnow() - timedelta(days=s["offset_days"])),
                    ConversationTurn(role="assistant", content=s["bot"], timestamp=datetime.utcnow() - timedelta(days=s["offset_days"]))
                ],
                occurred_on=s_date,
                processed=True,
                processed_at=datetime.utcnow()
            )
            await store.save_session(session_obj)
            
        # Seed 1 EpisodeArc (Shoulder Pinch Management)
        arc_id = "arc-david-shoulder-pinch"
        arc_obj = EpisodeArc(
            id=arc_id,
            user_id=username,
            arc_type=ArcType.INJURY,
            state="open",
            opened_on=today - timedelta(days=15),
            title="Right Shoulder Pinch Management",
            summary="David experienced a sharp pinch in his right shoulder during incline barbell bench presses. He successfully adapted by shifting to neutral-grip incline dumbbell presses and reduced the incline angle to prevent impingement.",
            coach_note="Monitor shoulder status. Do not prescribe high-stress overhead pressing or heavy barbell bench presses. Emphasize neutral-grip dumbbell variations.",
            source_episode_ids=[], # will link
            source_episode_summaries=[]
        )
        
        # Seed 5 EpisodeRecords
        episodes_data = [
            {
                "id": "ep-david-onboarding-ppl",
                "type": EpisodeType.GOAL,
                "situation": "David is a 32yo working professional aiming to build muscle mass.",
                "intent": "Transition from an upper/lower split to a Push/Pull/Legs hypertrophy routine.",
                "outcome": "Structured a rotating PPL split focusing on chest, shoulders, and triceps for the Push day.",
                "sig": "notable",
                "coach_note": "Acknowledge goal to put on serious muscle mass and follow rotating PPL layout.",
                "date_offset": 18,
                "quotes": ["I'm looking to put on some serious muscle mass... Can you help me set that up?"]
            },
            {
                "id": "ep-david-protein-meals",
                "type": EpisodeType.NUTRITION,
                "situation": "David wants to optimize protein intake for hypertrophy.",
                "intent": "Structure daily meals to hit 180g of protein.",
                "outcome": "Prescribed a protein target of 180g distributed across 4 meals of 45g each using chicken, beef, eggs, and whey.",
                "sig": "one_off",
                "coach_note": "Verify if he's hitting 180g of protein across 4 meals.",
                "date_offset": 17,
                "quotes": ["I want to hit around 180 grams of protein daily. What are the best protein sources to structure my meals around?"]
            },
            {
                "id": "ep-david-shoulder-pinch",
                "type": EpisodeType.CHALLENGE,
                "subtype": "injury",
                "situation": "David experienced right shoulder pain during barbell chest presses.",
                "intent": "Perform incline barbell bench press heavy sets.",
                "outcome": "Felt a sharp pinch in the right shoulder. Advised to stop barbell work and switch to neutral-grip dumbbell variations.",
                "sig": "turning_point",
                "coach_note": "CRITICAL: Shoulder pinch reported. Discontinue incline barbell chest pressing immediately.",
                "date_offset": 15,
                "quotes": ["felt a sharp pinch in my front right shoulder. Should I switch to dumbbells or change the angle?"]
            },
            {
                "id": "ep-david-shoulder-dumbbell-adaptation",
                "type": EpisodeType.WORKOUT,
                "situation": "David is testing adaptations to chest pressing.",
                "intent": "Perform chest pressing without shoulder pain.",
                "outcome": "Successfully performed neutral-grip incline dumbbell presses with zero pain or pinch.",
                "sig": "notable",
                "coach_note": "Stick to neutral-grip dumbbell chest pressing for now to prevent shoulder re-injury.",
                "date_offset": 14,
                "quotes": ["I tried the neutral-grip dumbbell press today and it felt much better, no shoulder pinch at all."]
            },
            {
                "id": "ep-david-deadlift-back-tightness",
                "type": EpisodeType.CHALLENGE,
                "subtype": "injury",
                "situation": "David reported excessive lower back tightness/fatigue post-deadlifts.",
                "intent": "Perform heavy conventional barbell deadlifts.",
                "outcome": "Experienced lower back tightness, suggesting form breakdown. Advised to reduce load by 15-20% and focus on abdominal bracing.",
                "sig": "notable",
                "coach_note": "Prompt him to flat-back brace and reduce weight if lower back fatigue is present.",
                "date_offset": 12,
                "quotes": ["For deadlifts on pull day, I'm trying to lift heavy, but my lower back feels extremely tight and fatigued the day after."]
            }
        ]
        
        episode_ids = []
        for ep in episodes_data:
            episode_ids.append(ep["id"])
            ep_date = today - timedelta(days=ep["date_offset"])
            
            record = EpisodeRecord(
                id=ep["id"],
                user_id=username,
                episode_type=ep["type"],
                situation=ep["situation"],
                intent=ep["intent"],
                outcome=ep["outcome"],
                significance=ep["sig"],
                coach_note=ep["coach_note"],
                occurred_on=ep_date,
                source_session_ids=session_ids,
                source_quotes=ep["quotes"],
                challenge_subtype=ep.get("subtype"),
                arc_id=arc_id if ep["id"] in ["ep-david-shoulder-pinch", "ep-david-shoulder-dumbbell-adaptation"] else None,
                created_at=datetime.utcnow() - timedelta(days=ep["date_offset"]),
                user_verified=True,
                active=True
            )
            await store.save_record(record)
            
            # Propagate to raw ChromaDB memories collection
            ep_type_str = ep["type"].value if hasattr(ep["type"], "value") else str(ep["type"])
            database.save_memory(
                username,
                "episodic",
                "",
                f"Type: {ep_type_str} | Situation: {ep['situation']} | Intent: {ep['intent']} | Outcome: {ep['outcome']} | Coach Note: {ep['coach_note']}",
                subtag="implicit"
            )
            
        # Link episodes back to arc
        arc_obj.source_episode_ids = ["ep-david-shoulder-pinch", "ep-david-shoulder-dumbbell-adaptation"]
        arc_obj.source_episode_summaries = [
            "Felt a sharp pinch in the front right shoulder on barbell press.",
            "Switched to neutral-grip dumbbell press on incline with no pain."
        ]
        await store.save_arc(arc_obj)
        
        # Seed 1 Reflection
        ref_id = "ref-david-shoulder-impingement"
        ref_obj = Reflection(
            id=ref_id,
            user_id=username,
            pattern_type="recovery",
            observation="David is prone to right shoulder impingement during wide-grip barbell bench presses but adapts well to neutral-grip dumbbell movements and lower incline angles.",
            coach_action="Avoid wide-grip flat or incline barbell pressing; prescribe neutral-grip dumbbell pressing and monitor right shoulder comfort closely.",
            supporting_episode_ids=["ep-david-shoulder-pinch", "ep-david-shoulder-dumbbell-adaptation"],
            supporting_episode_summaries=[
                "Felt a sharp pinch in the front right shoulder on barbell press.",
                "Switched to neutral-grip dumbbell press on incline with no pain."
            ],
            confidence="high",
            first_observed=today - timedelta(days=15),
            last_confirmed=today - timedelta(days=14),
            episode_count=2,
            active=True
        )
        await store.save_reflection(ref_obj)
        
        # Seed 1 EngagementSnapshot
        counts = {t: 0 for t in EpisodeType}
        counts[EpisodeType.WORKOUT] = 1
        counts[EpisodeType.CHALLENGE] = 2
        counts[EpisodeType.NUTRITION] = 1
        counts[EpisodeType.GOAL] = 1
        
        snapshot_obj = EngagementSnapshot(
            user_id=username,
            window_days=14,
            episode_type_counts=counts,
            dominant_focus=DominantFocus.INJURY_CONCERNED,
            activity_level="reduced",
            coach_signal="ACTIVE SHOULDER SIGNAL: The user has reported a right shoulder pinch. Ask him how his shoulder feels and avoid wide-grip barbell presses or overhead presses.",
            created_at=datetime.utcnow()
        )
        await store.save_snapshot(snapshot_obj)
        
        print("David's episodic records seeded successfully.")
        
    try:
        from episodic_memory.extraction.llm_client import LLMClient
        # Run the async loop helper in our main threads
        from llm import run_episodic_async
        run_episodic_async(_async_seeding())
    except Exception as e:
        print(f"Error seeding episodic database for David: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    seed_sarah_if_needed()
    seed_david_if_needed()

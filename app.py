import os
# Workaround for streamlit cloud deployment protobuf descriptors error
# MUST be set before importing streamlit or any other library
os.environ["PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"] = "python"

import streamlit as st
import time
from datetime import datetime

# Import modular components
import auth
import database_chroma_new as database
import llm

# ── Cognee exercise knowledge-graph integration (optional) ────────────────────
# Cognee is absent — the Knowledge Graph page just hides the Cognee tab.
# We lazy-load cognee_integration to make application startup extremely fast.
class LazyCognee:
    def __init__(self):
        self._module = None
        self._error = ""
        self._importable = None

    def _load(self):
        if self._importable is not None:
            return
        try:
            import cognee_integration as _cog_mod
            self._module = _cog_mod
            self._importable = _cog_mod.COGNEE_IMPORTABLE
            self._error = _cog_mod._IMPORT_ERROR if not self._importable else ""
        except Exception as e:
            self._module = None
            self._importable = False
            self._error = str(e)



    @property
    def COGNEE_AVAILABLE(self) -> bool:
        self._load()
        return bool(self._importable)

    @property
    def _COG_ERR(self) -> str:
        self._load()
        return self._error

    def __getattr__(self, name):
        self._load()
        if self._module is None:
            raise AttributeError(f"cognee_integration failed to load: {self._error}")
        return getattr(self._module, name)

_cog = LazyCognee()

def is_cognee_available() -> bool:
    return _cog.COGNEE_AVAILABLE

def get_cognee_error() -> str:
    return _cog._COG_ERR


# Set page config




# Set page config
st.set_page_config(
    page_title="AI Workout Coach",
    page_icon="Gym",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Inject Neo4j credentials from Streamlit Secrets into os.environ so that
# cognee_integration.configure_env() picks them up (it reads from os.environ,
# not st.secrets directly).
_NEO4J_SECRET_KEYS = [
    "GRAPH_DATABASE_URL",
    "GRAPH_DATABASE_USERNAME",
    "GRAPH_DATABASE_PASSWORD",
    "GRAPH_DATABASE_NAME",
    "GRAPH_DATABASE_PROVIDER",
]
try:
    for _k in _NEO4J_SECRET_KEYS:
        _v = st.secrets.get(_k) or st.secrets.get(_k.lower())
        if _v:
            os.environ[_k] = str(_v)
except Exception:
    pass


# Inject custom modern CSS


css_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "styles.css")
if os.path.exists(css_path):
    with open(css_path, "r", encoding="utf-8") as f:
        st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)
else:
    st.warning("Visual styles stylesheet (styles.css) was not found in the workspace.")

# Initialize session state variables
if "logged_in" not in st.session_state:
    st.session_state["logged_in"] = False
if "username" not in st.session_state:
    st.session_state["username"] = None
if "messages" not in st.session_state:
    st.session_state["messages"] = []
if "current_page" not in st.session_state:
    st.session_state["current_page"] = "Chat Coach"
if "memory_mode" not in st.session_state:
    st.session_state["memory_mode"] = "Using Memory"

# ── Cognee session state (does not conflict with existing keys) ───────────────
if "cognee_data_ready" not in st.session_state:
    st.session_state["cognee_data_ready"] = None   # None=unchecked, True/False
if "cognee_last_result" not in st.session_state:
    st.session_state["cognee_last_result"] = {}    # {answer, scope, search_type, triples, total_tokens}
if "cognee_full_graph_html" not in st.session_state:
    st.session_state["cognee_full_graph_html"] = ""
if "cognee_chat_history" not in st.session_state:
    st.session_state["cognee_chat_history"] = []   # [(question, answer_meta)]

# Initialize API Configuration from .env file if available
try:
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    # Automatically load all keys into environment variables
                    os.environ[k] = v
                    # Populate session states for Streamlit UI fields
                    if k == "GEMINI_API_KEY" and "gemini_api_key" not in st.session_state:
                        st.session_state["gemini_api_key"] = v
                    elif k == "HF_TOKEN" and "hf_token" not in st.session_state:
                        st.session_state["hf_token"] = v
except Exception:
    pass

# ------------------------------------------------------------
# AUTHENTICATION PAGE
# ------------------------------------------------------------
def render_auth_page():
    st.markdown("<h1 class='landing-title' style='text-align: center; font-size: 2.6rem !important; font-weight: 800 !important; color: #0F172A !important; margin-top: 0.5rem !important; margin-bottom: 0.25rem !important; letter-spacing: -1.0px !important; line-height: 1.15 !important;'>AI Workout Coach</h1>", unsafe_allow_html=True)
    
    if "tab_reset_key" not in st.session_state:
        st.session_state["tab_reset_key"] = 0
        
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        tab_login, tab_register = st.tabs(["Sign In", "Sign Up"], key=f"auth_tabs_{st.session_state['tab_reset_key']}")

        with tab_login:
            # Display signup success alert if redirected
            if "signup_success" in st.session_state and st.session_state["signup_success"]:
                st.success(st.session_state["signup_success"])
                st.session_state["signup_success"] = None
                
            st.subheader("Login to Coach")
            login_user = st.text_input("Username", key="login_username").strip()
            login_pass = st.text_input("Password", type="password", key="login_password")
            
            if st.button("Log In", key="login_btn", use_container_width=True):
                if not login_user or not login_pass:
                    st.error("Please enter both username and password.")
                else:
                    if not auth.user_exists(login_user):
                        st.error("User not found. Kindly sign up first.")
                    elif auth.verify_user(login_user, login_pass):
                        st.session_state["logged_in"] = True
                        st.session_state["username"] = login_user
                        database.warm_up_cache(login_user)
                        st.session_state["messages"] = [
                            {"role": "assistant", "text": "Hello, I am your AI Workout Coach. Ask me about exercises, workout splits, form tips, or nutrition! I can store and query your records."}
                        ]
                        st.success("Successfully logged in!")
                        time.sleep(0.5)
                        st.rerun()
                    else:
                        st.error("Invalid password. Please try again.")
                        
        with tab_register:
            st.subheader("Create New Account")
            reg_user = st.text_input("Choose Username", key="reg_username").strip()
            reg_pass = st.text_input("Password (min 4 characters)", type="password", key="reg_password")
            reg_pass_conf = st.text_input("Confirm Password", type="password", key="reg_password_conf")
            
            st.markdown("---")
            st.markdown("<h3 style='color: #0F172A;'>Profile Setup & Onboarding</h3>", unsafe_allow_html=True)
            st.markdown("<p style='color: #475569; font-size: 1rem; margin-bottom: 15px;'>Please answer these onboarding questions. They will be saved in your Semantic Memory under explicit tags to customize your training plan.</p>", unsafe_allow_html=True)
            
            # Onboarding Questions
            q1 = st.selectbox(
                "1. What is your current life stage? (Select the option that best describes you)",
                options=["Student / Teenager", "Working Professional", "Middle-Aged", "Retired"],
                key="reg_q1"
            )
            q2 = st.selectbox(
                "2. What is your primary fitness goal? (What do you want to achieve?)",
                options=["Weight Loss", "Improve General Fitness & Health", "Build Muscle & Strength", "Prepare for Competition / Sports"],
                key="reg_q2"
            )
            q3 = st.selectbox(
                "3. How often do you currently work out? (Be honest so we can plan accordingly!)",
                options=["Daily", "2-3 times a week", "Once a week", "Rarely / Starting fresh"],
                key="reg_q3"
            )
            q4 = st.multiselect(
                "4. Do you have any injuries or health conditions? (Select all that apply)",
                options=[
                    "No injuries (I'm good to go!)", 
                    "Knee / Joint issues", 
                    "Back / Neck pain", 
                    "Asthma / Respiratory issues", 
                    "Heart condition / Cardiovascular issues", 
                    "High blood pressure (Hypertension)", 
                    "Diabetes", 
                    "Shoulder impingement / issues", 
                    "Herniated disc / spinal issues", 
                    "Arthritis / Joint inflammation", 
                    "Other"
                ],
                default=["No injuries (I'm good to go!)"],
                key="reg_q4"
            )
            
            other_injury = ""
            if "Other" in q4:
                other_injury = st.text_input("Please specify other conditions/injuries:", key="reg_other_injury").strip()
                
            q5 = st.selectbox(
                "5. How would you describe your workout level?",
                options=["Beginner (New to fitness)", "Intermediate (Comfortable with most exercises)", "Advanced / Pro (Experienced and looking for a challenge)"],
                key="reg_q5"
            )
            q6 = st.selectbox(
                "6. Where do you prefer to train?",
                options=["At Home", "At the Gym", "Outdoors"],
                key="reg_q6"
            )
            q7 = st.selectbox(
                "7. Which area would you like to focus on? (Select your main priority)",
                options=["Upper Body (Arms, Chest, Back)", "Lower Body (Legs and Glutes)", "Cardio & Endurance", "Full Body Core & Flexibility"],
                key="reg_q7"
            )
            q8 = st.selectbox(
                "8. How much time can you dedicate to a single workout? (Suggested Question for better planning)",
                options=["15-30 mins", "30-60 mins", "60+ mins"],
                key="reg_q8"
            )
            q9 = st.selectbox(
                "9. What is your diet preference? (Vegan or Non Vegan)",
                options=["Vegan", "Non Vegan"],
                key="reg_q9"
            )
            q10 = st.text_input("10. What is your current height (in cm)?", key="reg_q10").strip()
            q11 = st.text_input("11. What is your current weight (in kg)?", key="reg_q11").strip()
            q12 = st.selectbox(
                "12. What is your country?",
                options=["India", "United States", "United Kingdom", "Canada", "Australia", "Other"],
                key="reg_q12"
            )
            q13 = st.multiselect(
                "13. Do you have any food allergies or intolerances? (Select all that apply)",
                options=["None", "Peanuts", "Tree nuts", "Soy", "Gluten", "Dairy", "Eggs", "Sesame", "Fish / Shellfish", "Lactose", "Fructose", "Histamine", "Gluten sensitivity", "Caffeine"],
                default=["None"],
                key="reg_q13"
            )
            q15 = st.selectbox(
                "14. What is the maximum time you can dedicate to meal prep?",
                options=["10 mins", "20 mins", "30 mins", "45 mins", "60+ mins"],
                index=1,
                key="reg_q15"
            )
            q16 = st.multiselect(
                "15. Which cuisines do you prefer? (Select all that apply)",
                options=["Indian", "Mediterranean", "Mexican", "Italian", "Asian", "American", "Middle Eastern"],
                default=["Indian"],
                key="reg_q16"
            )
            q17 = st.selectbox(
                "16. What is your typical workout time?",
                options=["Morning", "Afternoon", "Evening", "Night"],
                index=2,
                key="reg_q17"
            )
            
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("Register", key="reg_btn", use_container_width=True):
                if not reg_user:
                    st.error("Username cannot be empty.")
                elif reg_pass != reg_pass_conf:
                    st.error("Passwords do not match.")
                elif len(reg_pass) < 4:
                    st.error("Password must be at least 4 characters.")
                elif not q10:
                    st.error("Please enter your current height.")
                elif not q11:
                    st.error("Please enter your current weight.")
                else:
                    success, msg = auth.register_user(reg_user, reg_pass)
                    if success:
                        # Compile injuries answer
                        injuries_list = [item for item in q4 if item != "Other"]
                        if "Other" in q4 and other_injury:
                            injuries_list.append(other_injury)
                        injuries_str = ", ".join(injuries_list) if injuries_list else "None"
                        
                        # Save explicit semantic memories
                        database.save_memory(reg_user, "semantic", "What is your current life stage?", q1, subtag="explicit")
                        database.save_memory(reg_user, "semantic", "What is your primary fitness goal?", q2, subtag="explicit")
                        database.save_memory(reg_user, "semantic", "How often do you currently work out?", q3, subtag="explicit")
                        database.save_memory(reg_user, "semantic", "Do you have any injuries or health conditions?", injuries_str, subtag="explicit")
                        database.save_memory(reg_user, "semantic", "How would you describe your workout level?", q5, subtag="explicit")
                        database.save_memory(reg_user, "semantic", "Where do you prefer to train?", q6, subtag="explicit")
                        database.save_memory(reg_user, "semantic", "Which area would you like to focus on?", q7, subtag="explicit")
                        database.save_memory(reg_user, "semantic", "How much time can you dedicate to a single workout?", q8, subtag="explicit")
                        database.save_memory(reg_user, "semantic", "What is your diet preference?", q9, subtag="explicit")
                        database.save_memory(reg_user, "semantic", "What is your current height (in cm)?", q10, subtag="explicit")
                        database.save_memory(reg_user, "semantic", "What is your current weight (in kg)?", q11, subtag="explicit")
                        database.save_memory(reg_user, "semantic", "What is your country?", q12, subtag="explicit")
                        database.save_memory(reg_user, "semantic", "Do you have any food allergies or intolerances?", ", ".join(q13), subtag="explicit")
                        database.save_memory(reg_user, "semantic", "What is the maximum time you can dedicate to meal prep?", q15, subtag="explicit")
                        database.save_memory(reg_user, "semantic", "Which cuisines do you prefer?", ", ".join(q16), subtag="explicit")
                        database.save_memory(reg_user, "semantic", "What is your typical workout time?", q17, subtag="explicit")
                        
                        st.session_state["signup_success"] = "Account created successfully! Kindly login now."
                        st.session_state["tab_reset_key"] += 1
                        st.rerun()
                    else:
                        st.error(msg)

# ------------------------------------------------------------
# MAIN APPLICATION PAGE
# ------------------------------------------------------------
def render_main_app():
    username = st.session_state["username"]
    
    # ── SIDEBAR NAVIGATION ──
    st.sidebar.markdown("<div class='logo-container'>", unsafe_allow_html=True)
    logo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logo.png")
    if os.path.exists(logo_path):
        st.sidebar.image(logo_path, width=170)
    st.sidebar.markdown("</div>", unsafe_allow_html=True)
    
    st.sidebar.markdown(f"<h3 style='text-align: center; margin-top: 0; margin-bottom: 2px; font-size: 2.15rem; color: #FFFFFF;'>AI Workout Coach</h3>", unsafe_allow_html=True)
    st.sidebar.markdown(f"<p style='text-align: center; color: #0284C7; font-weight: 600; margin-bottom: 8px;'>Active Athlete: {username}</p>", unsafe_allow_html=True)
    
    st.sidebar.markdown("<h4 style='padding-left: 10px; margin-top: 5px; margin-bottom: 4px; font-size: 0.95rem; color: #FFFFFF;'>Navigation Dashboard</h4>", unsafe_allow_html=True)
    
    pages = [
        "Chat Coach", 
        "Semantic Memory", 
        "Episodic Memory",
        "Procedural Memory", 
        "Knowledge Graph", 
        "System Diagnostics"
    ]
    
    for p in pages:
        is_active = st.session_state["current_page"] == p
        nav_class = "sidebar-nav-active" if is_active else "sidebar-nav-inactive"
        st.sidebar.markdown(f"<div class='{nav_class}'>", unsafe_allow_html=True)
        if st.sidebar.button(p, key=f"nav_{p.replace(' ', '_').lower()}", use_container_width=True):
            st.session_state["current_page"] = p
            st.rerun()
        st.sidebar.markdown("</div>", unsafe_allow_html=True)
            
    st.sidebar.markdown("---")
    with st.sidebar.expander("API Configuration"):
        # Load API keys from session state if available
        init_gemini = st.session_state.get("gemini_api_key", "")
        init_hf = st.session_state.get("hf_token", "")
        
        gemini_input = st.text_input("Gemini API Key", type="password", value=init_gemini)
        if gemini_input:
            st.session_state["gemini_api_key"] = gemini_input.strip()
            
        hf_input = st.text_input("Hugging Face Token (Optional)", type="password", value=init_hf)
        if hf_input:
            st.session_state["hf_token"] = hf_input.strip()
            os.environ["HF_TOKEN"] = hf_input.strip()
        else:
            st.session_state["hf_token"] = ""
            if "HF_TOKEN" in os.environ:
                del os.environ["HF_TOKEN"]
    
    st.sidebar.markdown("<div class='sidebar-logout-container'>", unsafe_allow_html=True)
    if st.sidebar.button("Sign Out", key="logout_btn", use_container_width=True):
        st.session_state["logged_in"] = False
        st.session_state["username"] = None
        st.session_state["messages"] = []
        st.session_state["current_page"] = "Chat Coach"
        st.success("Successfully logged out.")
        time.sleep(0.5)
        st.rerun()
    st.sidebar.markdown("</div>", unsafe_allow_html=True)

    # ── TOP RIGHT PROFILE SHORTCUT EMOJI & TEXT ──
    col_space, col_profile_icon = st.columns([10, 1.3])
    with col_profile_icon:
        st.markdown(
            """
            <style>
            div[data-testid="stColumn"]:last-child button {
                background-color: transparent !important;
                border: none !important;
                font-size: 1.15rem !important;
                font-weight: 700 !important;
                padding: 0px !important;
                box-shadow: none !important;
                cursor: pointer !important;
                color: #475569 !important;
                text-align: right !important;
                justify-content: flex-end !important;
                display: flex !important;
                align-items: center !important;
                gap: 4px !important;
            }
            div[data-testid="stColumn"]:last-child button:hover {
                color: #0284C7 !important;
                transform: scale(1.05) !important;
                background-color: transparent !important;
                box-shadow: none !important;
            }
            </style>
            """,
            unsafe_allow_html=True
        )
        if st.button("👤 Profile", key="profile_shortcut_btn", help="View / Edit Profile"):
            st.session_state["current_page"] = "Profile"
            st.rerun()

    current_page = st.session_state["current_page"]
    
    if current_page == "Chat Coach":
        render_chat_page(username)
    elif current_page == "Profile":
        render_profile_page(username)
    elif current_page == "Semantic Memory":
        render_memory_page(username, "semantic")
    elif current_page == "Episodic Memory":
        render_episodic_memory_dashboard(username)
    elif current_page == "Procedural Memory":
        render_memory_page(username, "procedural")
    elif current_page == "Knowledge Graph":
        render_knowledge_graph(username)
    elif current_page == "System Diagnostics":
        render_diagnostics_page()

# ── CHAT COACH PAGE ──
def _render_message_citations(msg) -> str:
    """Helper to render collapsible citations for retrieved memories inside the chat bubble."""
    cits = msg.get("citations")
    if not cits:
        return ""
        
    sem = cits.get("semantic", [])
    sem_store = cits.get("semantic_store", [])
    epi = cits.get("episodic", [])
    epi_block = cits.get("episodic_block", "")
    pro = cits.get("procedural", [])
    pro_ans = cits.get("procedural_answer", "")
    kg = cits.get("kg")
    
    # Check if there is anything to render
    has_kg = False
    if kg and isinstance(kg, dict):
        kg_ans = kg.get("answer") or ""
        kg_trips = kg.get("triples") or []
        has_kg = bool(kg_ans and "no answer returned" not in kg_ans.lower()) or bool(kg_trips)
        
    if not (sem or sem_store or epi or epi_block or pro or pro_ans or has_kg):
        return ""
        
    citations_html = """<details class="citations-details" style="margin-top: 12px; font-size: 0.88rem; border-top: 1px solid #E2E8F0; padding-top: 8px;">
<summary style="cursor: pointer; font-weight: 700; color: #0284C7; user-select: none;">
Source Citations
</summary>
<div style="margin-top: 8px; max-height: 200px; overflow-y: auto; padding-right: 5px;">"""
    
    if sem or sem_store:
        citations_html += "<div style='margin-bottom: 8px;'><strong>Semantic:</strong>"
        for m in sem:
            citations_html += f"<div style='margin-left: 8px; color: #475569; margin-top: 2px;'>• {m['response']}</div>"
        for m in sem_store:
            evidence_str = f" (Evidence: {m['evidence']})" if m.get("evidence") else ""
            citations_html += f"<div style='margin-left: 8px; color: #475569; margin-top: 2px;'>• {m['response']}{evidence_str}</div>"
        citations_html += "</div>"
        
    if epi_block:
        citations_html += "<div style='margin-bottom: 8px;'><strong>Episodic:</strong>"
        lines = [line.strip() for line in epi_block.split("\n") if line.strip()]
        for line in lines:
            if "YOUR MEMORY OF THIS USER" in line or "The following is your episodic memory" in line or "END OF MEMORY BLOCK" in line or "The user does not see this block" in line:
                continue
            if line.startswith("[") and line.endswith("]"):
                citations_html += f"<div style='margin-left: 8px; color: #0284C7; font-weight: 600; margin-top: 6px;'>{line}</div>"
            elif line.startswith("- "):
                citations_html += f"<div style='margin-left: 16px; color: #475569; margin-top: 2px;'>• {line[2:]}</div>"
            else:
                citations_html += f"<div style='margin-left: 16px; color: #475569; margin-top: 2px;'>{line}</div>"
        citations_html += "</div>"
    elif epi:
        citations_html += "<div style='margin-bottom: 8px;'><strong>Episodic:</strong>"
        for m in epi:
            citations_html += f"<div style='margin-left: 8px; color: #475569; margin-top: 2px;'>• {m['response']}</div>"
        citations_html += "</div>"
        
    if pro_ans:
        formatted_pro_ans = markdown_to_html(pro_ans)
        citations_html += "<div style='margin-bottom: 8px;'><strong>Procedural:</strong>"
        citations_html += f"<div style='margin-left: 8px; color: #475569; margin-top: 2px; border-left: 2px solid #E2E8F0; padding-left: 8px;'>{formatted_pro_ans}</div>"
        citations_html += "</div>"
    elif pro:
        citations_html += "<div style='margin-bottom: 8px;'><strong>Procedural:</strong>"
        for m in pro:
            citations_html += f"<div style='margin-left: 8px; color: #475569; margin-top: 2px;'>• {m['response']}</div>"
        citations_html += "</div>"
        
    if has_kg:
        kg_ans = kg.get("answer") or ""
        kg_trips = kg.get("triples") or []
        citations_html += "<div style='margin-bottom: 8px;'><strong>Knowledge Graph:</strong>"
        if kg_ans and "no answer returned" not in kg_ans.lower():
            citations_html += f"<div style='margin-left: 8px; color: #475569; margin-top: 2px;'>• Answer: {kg_ans}</div>"
        for t in kg_trips:
            citations_html += f"<div style='margin-left: 8px; color: #475569; margin-top: 2px;'>• Relation: ({t[0]} - {t[1]} -> {t[2]})</div>"
        citations_html += "</div>"
        
    citations_html += """</div></details>"""
    return citations_html


def markdown_to_html(text: str) -> str:
    import re
    # Escape HTML tags present in raw text to prevent layout injection
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    
    # Bold text **bold** or __bold__ -> <strong>bold</strong>
    text = re.sub(r"\*\*(.*?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"__(.*?)__", r"<strong>\1</strong>", text)
    
    # Italic text *italic* or _italic_ -> <em>italic</em>
    text = re.sub(r"\*(.*?)\*", r"<em>\1</em>", text)
    text = re.sub(r"_(.*?)_", r"<em>\1</em>", text)
    
    lines = text.split("\n")
    html_lines = []
    in_list = None  # None, 'ul', 'ol'
    
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if in_list:
                html_lines.append(f"</{in_list}>")
                in_list = None
            html_lines.append("<br>")
            continue
            
        # Bullet list item
        ul_match = re.match(r"^([\-\*\+])\s+(.*)$", line)
        if ul_match:
            content = ul_match.group(2)
            if in_list == "ol":
                html_lines.append("</ol>")
                in_list = None
            if not in_list:
                html_lines.append('<ul style="margin-top: 4px; margin-bottom: 4px; padding-left: 20px;">')
                in_list = "ul"
            html_lines.append(f"<li>{content}</li>")
            continue
            
        # Numbered list item
        ol_match = re.match(r"^(\d+)\.\s+(.*)$", line)
        if ol_match:
            content = ol_match.group(2)
            if in_list == "ul":
                html_lines.append("</ul>")
                in_list = None
            if not in_list:
                html_lines.append('<ol style="margin-top: 4px; margin-bottom: 4px; padding-left: 20px;">')
                in_list = "ol"
            html_lines.append(f"<li>{content}</li>")
            continue
            
        # Regular text line - close list if in one
        if in_list:
            html_lines.append(f"</{in_list}>")
            in_list = None
            
        html_lines.append(f"<div style='margin-bottom: 4px;'>{line}</div>")
        
    if in_list:
        html_lines.append(f"</{in_list}>")
        
    return "\n".join(html_lines)


def render_chat_page(username: str):
    st.markdown("<h1 style='font-size: 2.2rem; font-weight: 800; color: #0F172A; margin-top: 0px; margin-bottom: 5px; line-height: 1.1;'>Chat Coach</h1>", unsafe_allow_html=True)
    st.markdown("<p style='font-size: 1.05rem; font-weight: 500; color: #475569; margin-top: 4px; margin-bottom: 15px;'>Interact with your fitness trainer. Ask queries about nutrition, schedules, routines, or symptoms.</p>", unsafe_allow_html=True)
    
    memory_mode = st.radio(
        "Memory Settings",
        options=["Using Memory", "Without Memory"],
        key="memory_mode_selection",
        horizontal=True,
        index=0 if st.session_state["memory_mode"] == "Using Memory" else 1
    )
    st.session_state["memory_mode"] = memory_mode
    
    st.markdown("---")
    
    # Chat History
    chat_container = st.container()
    with chat_container:
        for msg in st.session_state["messages"]:
            formatted_text = markdown_to_html(msg["text"])
            if msg["role"] == "user":
                user_html = f'<div class="chat-container"><div class="chat-sender sender-user">Human</div><div class="chat-bubble chat-user">{formatted_text}</div></div>'
                st.markdown(user_html, unsafe_allow_html=True)
            else:
                citations_html = _render_message_citations(msg)
                bot_html = f'<div class="chat-container"><div class="chat-sender sender-bot">Bot</div><div class="chat-bubble chat-bot"><div>{formatted_text}</div>{citations_html}</div></div>'
                st.markdown(bot_html, unsafe_allow_html=True)
                
    user_input = st.chat_input("Ask your workout coach...")
    
    if user_input:
        st.session_state["messages"].append({"role": "user", "text": user_input})
        st.rerun()

    if len(st.session_state["messages"]) > 0 and st.session_state["messages"][-1]["role"] == "user":
        last_query = st.session_state["messages"][-1]["text"]
        
        # Thinking Indicator animation
        thinking_placeholder = st.empty()
        thinking_placeholder.markdown("""
        <div class="thinking-box">
            Thinking
            <div class="thinking-dots">
                <div class="thinking-dot"></div>
                <div class="thinking-dot"></div>
                <div class="thinking-dot"></div>
            </div>
        </div>
        """, unsafe_allow_html=True)
        
        use_memory = (st.session_state["memory_mode"] == "Using Memory")
        api_key = _get_api_key()
        
        parallel_res = None
        if use_memory:
            # 1. Fetch memories in parallel
            parallel_res = llm.get_dynamic_memories_parallel(username, last_query, api_key)
            
            # 2. Show context in the UI under the collapsible arrow button
            temp_msg = {"citations": parallel_res}
            citations_html = _render_message_citations(temp_msg)
            
            html_content = f"""<div class="thinking-box" style="animation: none; max-width: 100%; border: 1px solid #E2E8F0; background-color: #F8FAFC; display: flex; flex-direction: column; align-items: flex-start; padding: 14px 18px;">
<div style="font-weight: 700; color: #0284C7; font-size: 1.05rem; display: flex; align-items: center; gap: 8px; margin-bottom: 6px;">
<span>Thinking</span>
<div class="thinking-dots" style="display: inline-block;">
<div class="thinking-dot"></div>
<div class="thinking-dot"></div>
<div class="thinking-dot"></div>
</div>
</div>
{citations_html}
</div>"""
            thinking_placeholder.markdown(html_content, unsafe_allow_html=True)

        result = llm.generate_coach_response(
            last_query, 
            username, 
            use_memory=use_memory,
            prefetched_memories=parallel_res if use_memory else None
        )
        response_text = result["response"]
        extracted_memories = result["memories"]
        
        thinking_placeholder.empty()
        
        st.session_state["messages"].append({
            "role": "assistant", 
            "text": response_text,
            "citations": result.get("citations") if use_memory else None
        })
        
        if use_memory:
            # Save extracted memories to old collection
            for tag, list_of_texts in extracted_memories.items():
                clean_texts = [t.strip() for t in list_of_texts if t.strip()]
                if clean_texts:
                    # Unify all items for this tag into a single record per turn
                    combined_text = " ".join(clean_texts)
                    if tag in ["semantic", "episodic"]:
                        # Semantic and episodic are stored as pure sentences (no Q&A/prompt context)
                        database.save_memory(username, tag, "", combined_text, subtag="implicit")
                    else:
                        # Procedural is saved with the user query context
                        database.save_memory(username, tag, last_query, combined_text, subtag="implicit")
            
            # Save semantic memories to the new collection synchronously
            if api_key:
                import semantic_memory_store as _sms
                import uuid as _uuid
                source_id = f"chat-{_uuid.uuid4().hex[:8]}"
                try:
                    _sms.extract_and_store_from_chat(
                        username=username,
                        user_message=last_query,
                        api_key=api_key,
                        source_id=source_id
                    )
                except Exception as err:
                    print(f"Error saving to semantic_memory_store: {err}")
                        
        st.rerun()


def strip_emojis(text: str) -> str:
    """Remove 4-byte unicode emojis from text."""
    if not text:
        return ""
    return "".join(c for c in text if ord(c) < 0x10000)


# ── EPISODIC MEMORY DASHBOARD ──
def render_episodic_memory_dashboard(username: str):
    import llm
    from datetime import date, datetime
    
    api_key = _get_api_key()
    
    st.markdown("<h1 style='font-size: 2.2rem; font-weight: 800; color: #0F172A; margin-top: 0px; margin-bottom: 5px; line-height: 1.1;'>Episodic Memory Dashboard</h1>", unsafe_allow_html=True)
    st.markdown("<p style='font-size: 1.05rem; font-weight: 500; color: #475569; margin-top: 4px; margin-bottom: 15px;'>Manage your chronological fitness narrative, ongoing coaching arcs, behavioral reflections, and specific event records.</p>", unsafe_allow_html=True)
    
    st.markdown("<h3 style='color: #0F172A; margin-top: 20px; font-size: 1.45rem; font-weight: 700;'>Pipeline Control Panels</h3>", unsafe_allow_html=True)
    col_b1, col_b2 = st.columns(2)
    with col_b1:
        st.markdown("<h4 style='color: #0284C7; font-size: 1.15rem; font-weight: 600; margin-bottom: 2px;'>Batch 1: Episode Builder</h4>", unsafe_allow_html=True)
        st.caption("Synthesizes recent conversation turns into grounded EpisodeRecords. Run every 2-4 hours.")
        if st.button("Run Episode Builder (Batch 1)", key="run_b1", use_container_width=True):
            with st.spinner("Executing Batch 1 pipeline..."):
                res = llm.run_episodic_batch_one(username, api_key)
                if res["success"]:
                    st.success(f"Batch 1 Completed! Sessions: received={res['sessions_received']}, processed={res['sessions_processed']}. Records: created={res['records_created']}, superseded={res['records_superseded']}, merged={res['records_merged']}. Dropped due to quote checks={res['records_dropped_no_quotes']}.")
                    st.rerun()
                else:
                    st.error(f"Batch 1 Failed: {res['error']}")
    with col_b2:
        st.markdown("<h4 style='color: #0284C7; font-size: 1.15rem; font-weight: 600; margin-bottom: 2px;'>Batch 2: Weekly Insights</h4>", unsafe_allow_html=True)
        st.caption("Triggers Arc Detection to advance/conclude stories, and Reflection Generation with confidence degradation.")
        if st.button("Run Weekly Insights (Batch 2)", key="run_b2", use_container_width=True):
            with st.spinner("Executing Batch 2 pipeline..."):
                res = llm.run_episodic_batch_two(username, api_key)
                if res["success"]:
                    st.success(f"Batch 2 Completed! Arcs: advanced={res['arcs_advanced']}, created={res['arcs_created']}, abandoned={res['arcs_abandoned']}. Reflections: created={res['reflections_created']}, updated={res['reflections_updated']}, downgraded={res['reflections_downgraded']}, deactivated={res['reflections_deactivated']}.")
                    st.rerun()
                else:
                    st.error(f"Batch 2 Failed: {res['error']}")
                    
    snapshot = llm.get_episodic_snapshot(username, api_key)
    st.markdown("<h3 style='color: #0F172A; margin-top: 25px; font-size: 1.45rem; font-weight: 700;'>Current Session Engagement Snapshot</h3>", unsafe_allow_html=True)
    if snapshot:
        focus_color = {
            "INJURY_CONCERNED": "#EF4444",
            "LOW_ACTIVITY_CHALLENGED": "#F59E0B",
            "DISENGAGED": "#78716C",
            "LOW_ACTIVITY": "#F59E0B",
            "MEAL_FOCUSED": "#06B6D4",
            "MOMENTUM_PHASE": "#10B981",
            "NORMAL": "#3B82F6",
        }.get(snapshot.dominant_focus.name, "#64748B")
        
        # Get latest episode record info
        records = llm.get_episodic_records(username, api_key, active_only=False)
        latest_ep_html = ""
        if records:
            def sort_key(rec):
                cre = getattr(rec, "created_at", None) or datetime.min
                return (rec.occurred_on, cre)
            records.sort(key=sort_key, reverse=True)
            latest_r = records[0]
            occ_str = latest_r.occurred_on.strftime('%Y-%m-%d') if hasattr(latest_r.occurred_on, 'strftime') else str(latest_r.occurred_on)
            latest_ep_html = f'<div style="margin-top: 10px; background-color: #F0F9FF; border: 1px solid #E0F2FE; padding: 10px; border-radius: 4px; color: #0369A1; font-size: 0.92rem;"><strong>Latest Episode:</strong> <span style="color: #0284C7; font-weight: 600;">{strip_emojis(latest_r.episode_type.value)}</span> ({occ_str}) &mdash; <em>{strip_emojis(latest_r.outcome)}</em></div>'
            
        snapshot_html = f'<div style="background-color: #F8FAFC; border-left: 6px solid {focus_color}; padding: 15px; border-radius: 6px; box-shadow: 0 1px 3px rgba(0,0,0,0.05); margin-bottom: 20px;"><h4 style="margin: 0 0 8px 0; color: #0F172A; font-weight: 700;">Dominant Focus: <span style="color: {focus_color};">{strip_emojis(snapshot.dominant_focus.value)}</span></h4><p style="margin: 0 0 10px 0; font-size: 0.95rem; color: #475569;">Activity Level: <strong>{strip_emojis(snapshot.activity_level.upper())}</strong> | Window: {snapshot.window_days} days</p><div style="background-color: #FFFFFF; border: 1px solid #E2E8F0; padding: 10px; border-radius: 4px; font-style: italic; color: #1E293B;"><strong>Coaching Signal:</strong> {strip_emojis(snapshot.coach_signal)}</div>{latest_ep_html}</div>'
        st.markdown(snapshot_html, unsafe_allow_html=True)
    else:
        st.info("No active Engagement Snapshot available. Run the Episode Builder first.")
        
    tab_arcs, tab_records, tab_db = st.tabs(["Active Stories & Reflections", "All Episode Records", "Episodic Memory Database"])
    
    with tab_arcs:
        st.markdown("<h4 style='color: #0F172A; font-size: 1.25rem; font-weight: 700;'>Ongoing Stories (Episode Arcs)</h4>", unsafe_allow_html=True)
        arcs = llm.get_episodic_arcs(username, api_key)
        if not arcs:
            st.info("No active arcs or stories found.")
        else:
            for arc in arcs:
                state_color = "#10B981" if arc.state == "open" else ("#64748B" if arc.state == "completed" else "#EF4444")
                st.markdown(f"""
                <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 8px; padding: 15px; margin-bottom: 12px;">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                        <span style="font-weight: 700; font-size: 1.1rem; color: #0F172A;">{strip_emojis(arc.title)}</span>
                        <span style="background-color: {state_color}; color: #FFFFFF; padding: 2px 8px; border-radius: 12px; font-size: 0.75rem; font-weight: 600; text-transform: uppercase;">{strip_emojis(arc.state)}</span>
                    </div>
                    <p style="margin: 0 0 6px 0; font-size: 0.9rem; color: #475569;"><strong>Type:</strong> {strip_emojis(arc.arc_type.value)} | Opened on: {arc.opened_on}</p>
                    <p style="margin: 0 0 8px 0; color: #1E293B;"><strong>Story Summary:</strong> {strip_emojis(arc.summary)}</p>
                    <div style="background: #EFF6FF; border-left: 3px solid #3B82F6; padding: 8px; font-size: 0.9rem; color: #1E3A8A;">
                        <strong>Coach Note:</strong> {strip_emojis(arc.coach_note)}
                    </div>
                </div>
                """, unsafe_allow_html=True)
                
        st.markdown("<h4 style='color: #0F172A; margin-top: 25px; font-size: 1.25rem; font-weight: 700;'>Behavioral Reflections</h4>", unsafe_allow_html=True)
        reflections = llm.get_episodic_reflections(username, api_key, active_only=True)
        if not reflections:
            st.info("No active behavioral reflections found.")
        else:
            for ref in reflections:
                conf_color = {"high": "#10B981", "medium": "#3B82F6", "low": "#F59E0B"}.get(ref.confidence, "#64748B")
                st.markdown(f"""
                <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 8px; padding: 15px; margin-bottom: 12px;">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                        <span style="font-weight: 700; font-size: 1.05rem; color: #0F172A;">Pattern: {strip_emojis(ref.pattern_type)}</span>
                        <span style="background-color: {conf_color}; color: #FFFFFF; padding: 2px 8px; border-radius: 12px; font-size: 0.75rem; font-weight: 600; text-transform: uppercase;">{strip_emojis(ref.confidence)} confidence</span>
                    </div>
                    <p style="margin: 0 0 8px 0; color: #1E293B;"><strong>Observation:</strong> {strip_emojis(ref.observation)}</p>
                    <div style="background: #F0FDF4; border-left: 3px solid #10B981; padding: 8px; font-size: 0.9rem; color: #14532D; margin-bottom: 8px;">
                        <strong>Coaching Rule:</strong> {strip_emojis(ref.coach_action)}
                    </div>
                    <p style="margin: 0; font-size: 0.8rem; color: #64748B;">Supported by {ref.episode_count} episodes | Last confirmed: {ref.last_confirmed}</p>
                </div>
                """, unsafe_allow_html=True)
                
    with tab_records:
        st.markdown("<h5 style='margin-top: 10px; margin-bottom: 5px; font-size: 1.1rem; font-weight: 600; color: #0F172A;'>Search Episodic Memories</h5>", unsafe_allow_html=True)
        search_query = st.text_input("Search inside episodic memories...", placeholder="Type to search episodic memories...", key="search_episodic", label_visibility="collapsed")
        st.markdown("---")
        
        if search_query.strip():
            records = llm.search_episodic_records(username, search_query.strip(), api_key, active_only=False)
            st.subheader(f"Vector Query Results for: '{search_query}'")
        else:
            records = llm.get_episodic_records(username, api_key, active_only=False)
            st.subheader("All Chronological Entries (Newest First)")
            
        if not records:
            st.info("No EpisodeRecords found.")
        else:
            # Sort by occurred_on descending, then created_at descending
            def sort_key(rec):
                cre = getattr(rec, "created_at", None) or datetime.min
                return (rec.occurred_on, cre)
            records.sort(key=sort_key, reverse=True)
            
            for r in records:
                card_type = strip_emojis(r.episode_type.value)
                card_sit = strip_emojis(r.situation)
                card_intent = strip_emojis(r.intent)
                card_out = strip_emojis(r.outcome)
                card_sig = strip_emojis(r.significance.replace('_', ' ').title())
                
                occurred_str = r.occurred_on.strftime('%Y-%m-%d') if hasattr(r.occurred_on, 'strftime') else str(r.occurred_on)
                created_str = r.created_at.strftime('%Y-%m-%d %H:%M:%S') if hasattr(r, 'created_at') and r.created_at else 'N/A'
                
                card_html = f"""
                <div style="background: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 8px; padding: 16px; margin-bottom: 12px; box-shadow: 0 1px 2px rgba(0,0,0,0.05);">
                    <div style="display: flex; justify-content: space-between; align-items: center; font-weight: 700; font-size: 1.1rem; color: #0F172A; margin-bottom: 10px; border-bottom: 1px solid #F1F5F9; padding-bottom: 6px;">
                        <span>Type: {card_type}</span>
                        <span style="font-size: 0.8rem; color: #64748B; font-weight: normal;">Occurred: {occurred_str} | Created: {created_str}</span>
                    </div>
                    <div style="margin-bottom: 6px; font-size: 0.95rem; color: #334155;"><strong>Situation:</strong> {card_sit}</div>
                    <div style="margin-bottom: 6px; font-size: 0.95rem; color: #334155;"><strong>Intent:</strong> {card_intent}</div>
                    <div style="margin-bottom: 6px; font-size: 0.95rem; color: #334155;"><strong>Outcome:</strong> {card_out}</div>
                    <div style="margin-bottom: 6px; font-size: 0.95rem; color: #334155;"><strong>Significance:</strong> {card_sig}</div>
                """
                if r.source_quotes:
                    card_html += "<div style='font-size: 0.95rem; color: #334155; margin-top: 8px;'><strong>Source Quotes:</strong>"
                    for q in r.source_quotes:
                        card_html += f"<div style='font-style: italic; color: #475569; margin-left: 8px; margin-top: 2px;'>&ldquo;{strip_emojis(q)}&rdquo;</div>"
                    card_html += "</div>"
                card_html += "</div>"
                
                st.markdown(card_html, unsafe_allow_html=True)

    with tab_db:
        st.markdown("<h5 style='margin-top: 10px; margin-bottom: 5px; font-size: 1.1rem; font-weight: 600; color: #0F172A;'>Search Episodic Memories (Chat Context)</h5>", unsafe_allow_html=True)
        search_query_db = st.text_input("Search inside episodic memories (chat)...", placeholder="Type to search episodic memories...", key="search_db_tab_episodic", label_visibility="collapsed")
        st.markdown("---")
        
        if search_query_db.strip():
            memories = database.vector_query_memories(username, "episodic", search_query_db.strip())
            st.subheader(f"Vector Query Results for: '{search_query_db}'")
        else:
            memories = database.get_memories_by_tag(username, "episodic")
            st.subheader("All Chronological Entries (Newest First)")
            
        if not memories:
            st.info("No records found in episodic memory matching these criteria.")
        else:
            for idx, m in enumerate(memories):
                try:
                    dt = datetime.fromisoformat(m["timestamp"])
                    formatted_time = dt.strftime("%b %d, %Y - %H:%M:%S")
                except Exception:
                    formatted_time = m["timestamp"]
                    
                subtag_val = m.get("subtag", "implicit")
                subtag_badge = f'<span class="subtag-badge subtag-{subtag_val}">{subtag_val}</span>'
                
                st.markdown(f"""
                <div class="memory-card">
                    <div class="memory-header">
                        <div>
                            <span class="memory-tag tag-episodic">Episodic</span>
                            {subtag_badge}
                        </div>
                        <span class="memory-time">{formatted_time}</span>
                    </div>
                    <div class="memory-body">
                        {f'<div class="memory-q">Query: {m["query"]}</div><div class="memory-r">Response: {m["response"]}</div>' if m.get("query") else f'<div class="memory-r" style="font-size: 1.1rem; font-weight: 500; color: #1E293B;">{m["response"]}</div>'}
                    </div>
                </div>
                """, unsafe_allow_html=True)


# ── MEMORY DETAIL PAGE ──
def render_memory_page(username: str, tag: str):
    tag_capitalized = tag.capitalize()
    st.markdown(f"<h1 style='font-size: 2.2rem; font-weight: 800; color: #0F172A; margin-top: 0px; margin-bottom: 5px; line-height: 1.1;'>{tag_capitalized} Memory Database</h1>", unsafe_allow_html=True)
    st.markdown(f"<p style='font-size: 1.05rem; font-weight: 500; color: #475569; margin-top: 4px; margin-bottom: 15px;'>Displaying stored {tag} database entries filtered for user <strong>{username}</strong> in chronological order.</p>", unsafe_allow_html=True)
    
    # Memory Filter UX
    subtag_filter = "All"
    if tag in ["semantic", "procedural"]:
        st.markdown(f"<h4 style='margin-top: 15px; margin-bottom: 8px; font-size: 1.45rem; font-weight: 700; color: #0F172A;'>Filter {tag_capitalized} Memory Classification</h4>", unsafe_allow_html=True)
        options = ["All", "Onboarding Profile (Explicit)", "Chat Context (Implicit)"] if tag == "semantic" else ["All", "Procedural Guide (Explicit)", "Chat Context (Implicit)"]
        subtag_filter = st.radio(
            f"Filter {tag_capitalized} Memory Classification",
            options=options,
            horizontal=True,
            label_visibility="collapsed"
        )
    
    # Vector Query Search and Management
    st.markdown(f"<h5 style='margin-top: 10px; margin-bottom: 5px; font-size: 1.1rem; font-weight: 600; color: #0F172A;'>Search {tag_capitalized} Memories</h5>", unsafe_allow_html=True)
    search_query = st.text_input(f"Search inside {tag} memories...", placeholder=f"Type to search {tag} memories...", key=f"search_db_{tag}", label_visibility="collapsed")
    st.markdown("---")

    
    if tag == "semantic":
        if search_query.strip():
            explicit_mems = database.vector_query_memories(username, "semantic", search_query.strip())
            explicit_mems = [m for m in explicit_mems if m.get("subtag") == "explicit"]
            
            implicit_mems = database.vector_query_semantic_store(username, search_query.strip())
            st.subheader(f"Vector Query Results for: '{search_query}'")
        else:
            explicit_mems = database.get_memories_by_tag(username, "semantic")
            explicit_mems = [m for m in explicit_mems if m.get("subtag") == "explicit"]
            
            implicit_mems = database.get_semantic_store_memories_mapped(username)
            st.subheader("All Chronological Entries (Newest First)")
            
        if subtag_filter == "Onboarding Profile (Explicit)":
            memories = explicit_mems
        elif subtag_filter == "Chat Context (Implicit)":
            memories = implicit_mems
        else: # All
            memories = explicit_mems + implicit_mems
            def get_ts(mem):
                return mem.get("timestamp") or ""
            memories.sort(key=get_ts, reverse=True)
    else:
        if search_query.strip():
            memories = database.vector_query_memories(username, tag, search_query.strip())
            st.subheader(f"Vector Query Results for: '{search_query}'")
        else:
            memories = database.get_memories_by_tag(username, tag)
            st.subheader("All Chronological Entries (Newest First)")
            
        if tag == "procedural" and subtag_filter != "All":
            target = "explicit" if "Procedural" in subtag_filter else "implicit"
            memories = [m for m in memories if m.get("subtag") == target]
        
    if not memories:
        st.info(f"No records found in {tag} memory matching these criteria.")
    else:
        for idx, m in enumerate(memories):
            try:
                dt = datetime.fromisoformat(m["timestamp"])
                formatted_time = dt.strftime("%b %d, %Y - %H:%M:%S")
            except Exception:
                formatted_time = m["timestamp"]
                
            subtag_val = m.get("subtag", "implicit")
            subtag_badge = f'<span class="subtag-badge subtag-{subtag_val}">{subtag_val}</span>'
            
            evidence_html = ""
            if m.get("evidence"):
                evidence_html = f'<div class="memory-evidence" style="margin-top: 8px; padding-top: 6px; border-top: 1px dashed #E2E8F0; font-size: 0.9rem; color: #64748B; font-style: italic;"><strong>Evidence:</strong> {m["evidence"]}</div>'
            
            st.markdown(f"""<div class="memory-card">
<div class="memory-header">
<div>
<span class="memory-tag tag-{tag}">{tag_capitalized}</span>
{subtag_badge}
</div>
<span class="memory-time">{formatted_time}</span>
</div>
<div class="memory-body">
{f'<div class="memory-q">Query: {m["query"]}</div><div class="memory-r">Response: {m["response"]}</div>' if m.get("query") else f'<div class="memory-r" style="font-size: 1.1rem; font-weight: 500; color: #1E293B;">{m["response"]}</div>'}
{evidence_html}
</div>
</div>""", unsafe_allow_html=True)


# ── HELPER: resolve the Gemini API key from all sources ──────────────────────
def _get_api_key() -> str:
    key = st.session_state.get("gemini_api_key") or ""
    if not key:
        try:
            key = (st.secrets.get("GEMINI_API_KEY")
                   or st.secrets.get("gemini_api_key") or "")
        except Exception:
            pass
    if not key:
        key = (os.environ.get("GEMINI_API_KEY")
               or os.environ.get("LLM_API_KEY") or "")
    return key.strip()


# Path to the pre-built graph HTML committed in the repo (resolved relative to app.py)
_KG_HTML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".artifacts", "exercises_graph.html")


# ── KNOWLEDGE GRAPH PAGE ──────────────────────────────────────────────────────
def render_knowledge_graph(username: str):
    st.markdown(
        "<h1 style='font-size:2.2rem;font-weight:800;color:#0F172A;"
        "margin-top:0;margin-bottom:5px;line-height:1.1;'>"
        "Interactive Knowledge Graph</h1>",
        unsafe_allow_html=True,
    )
    st.markdown(
        "<p style='font-size:1.05rem;font-weight:500;color:#475569;"
        "margin-top:4px;margin-bottom:15px;'>"
        "Explore the Cognee exercise knowledge graph.</p>",
        unsafe_allow_html=True,
    )
    import streamlit.components.v1 as components

    # ── Auto-load pre-built graph directly from disk (no Cognee / API key needed) ──
    if not st.session_state.get("cognee_full_graph_html"):
        if os.path.exists(_KG_HTML_PATH):
            try:
                with open(_KG_HTML_PATH, "r", encoding="utf-8") as _f:
                    _html = _f.read()
            except UnicodeDecodeError:
                with open(_KG_HTML_PATH, "r", encoding="cp1252", errors="replace") as _f:
                    _html = _f.read()
            if _html:
                st.session_state["cognee_full_graph_html"] = _html
                st.session_state["cognee_data_ready"] = True
                if is_cognee_available():
                    _cognee_server_state()["ready"] = True
        elif is_cognee_available():
            prebuilt = _cog.get_prebuilt_graph_html()
            if prebuilt:
                st.session_state["cognee_full_graph_html"] = prebuilt
                st.session_state["cognee_data_ready"] = True
                _cognee_server_state()["ready"] = True

    full_html = st.session_state.get("cognee_full_graph_html", "")
    data_ready = bool(full_html)

    if full_html:
        st.success("**Exercise graph is ready.**")
        with st.expander("Full Exercise Knowledge Graph", expanded=True):
            st.caption(
                "Click and drag nodes · scroll to zoom · hover for details. "
                "Use the search box (top-left of the graph) to highlight nodes by name."
            )
            components.html(full_html, height=700, scrolling=True)
    else:
        st.error(
            "Pre-built graph not found. Ensure `.artifacts/exercises_graph.html` "
            "is present in the repository."
        )

    st.markdown("---")

    # ── Q&A requires Cognee + API key ─────────────────────────────────────
    if not is_cognee_available():
        st.info(
            "**Live Q&A is not available** — Cognee is not installed in this environment.\n\n"
            f"Import error: `{get_cognee_error()}`"
        )
        return

    api_key = _get_api_key()
    if not api_key:
        st.warning(
            "Enter your **Gemini API key** in the sidebar API Configuration panel "
            "to query the knowledge graph."
        )
        return

    # ── Chat + Context Graph panel ─────────────────────────────────────────
    st.markdown(
        "<h3 style='color:#0F172A;margin-bottom:4px;'>Query the Exercise Knowledge Graph</h3>",
        unsafe_allow_html=True,
    )
    st.caption(
        "Ask any fitness question. The right panel shows the knowledge-graph triplets "
        "Cognee retrieved to ground the answer."
    )

    col_chat, col_ctx = st.columns([1, 1], gap="medium")

    with col_chat:
        st.markdown("**Ask a question**")
        question = st.text_input(
            "Exercise question",
            placeholder="e.g. What muscles does the jack jump target?",
            label_visibility="collapsed",
            key="cognee_question_input",
            disabled=not data_ready,
        )
        ask_btn = st.button(
            "Ask ➤",
            key="cognee_ask_btn",
            disabled=not data_ready or not question.strip(),
            use_container_width=False,
        )

        # Example chips
        st.caption("Try an example:")
        examples = [
            "What muscles does the jack jump target?",
            "How do I perform a barbell floor calf raise?",
            "What beginner exercises can I do with just body weight?",
            "Which exercises in the dataset target the biceps?",
            "What back exercises are available and what muscles do they work?",
        ]
        for ex in examples:
            if st.button(ex, key=f"cog_ex_{ex[:20]}", use_container_width=True):
                st.session_state["cognee_prefill"] = ex
                st.rerun()

        # Handle prefill from example buttons
        if "cognee_prefill" in st.session_state and st.session_state["cognee_prefill"]:
            question = st.session_state.pop("cognee_prefill")
            ask_btn  = True   # treat as if Ask was clicked

    with col_ctx:
        st.markdown("**Context Subgraph**")
        st.caption("Triplets retrieved from the knowledge graph to answer your question. Blue = source · Green = target.")
        ctx_placeholder = st.empty()

    # Process query — render inline in the SAME script run.
    # Calling st.rerun() here would block ~60s on Cognee; by the time it
    # fires, the Streamlit WebSocket has often already dropped and the
    # client never sees the answer.
    if ask_btn and question.strip() and data_ready:
        with st.spinner("Querying exercise knowledge graph…"):
            try:
                result = _cog.query_exercise_graph(question.strip(), api_key)
                st.session_state["cognee_last_result"] = result
                st.session_state["cognee_chat_history"].append(
                    (question.strip(), result)
                )
            except Exception as exc:
                st.error(f"Query failed: {exc}")

    # Render last result
    last = st.session_state.get("cognee_last_result", {})
    if last:
        with col_chat:
            st.markdown("**Answer**")
            st.markdown(
                f"<div style='background:#F0F9FF;border-left:4px solid #0284C7;"
                f"padding:12px 14px;border-radius:6px;margin-top:6px;font-size:0.95rem;'>"
                f"{last['answer']}</div>",
                unsafe_allow_html=True,
            )
            st.caption(
                f"Scope: **{last.get('scope','')}** → `{last.get('search_type','')}` "
                f"| Tokens: {last.get('total_tokens', 0)} "
                f"| Graph triplets: {len(last.get('triples', []))}"
            )

        triples = last.get("triples", [])
        if triples:
            ctx_html = _cog.triples_to_html(triples, height=400)
            with ctx_placeholder:
                components.html(ctx_html, height=450, scrolling=False)
        else:
            ctx_placeholder.info("No graph context retrieved for this query.")
    else:
        ctx_placeholder.markdown(
            "<div style='display:flex;align-items:center;justify-content:center;"
            "height:300px;color:#aaa;font-size:14px;border:2px dashed #e0e0e0;border-radius:8px;'>"
            "Context graph will appear here after you ask a question.</div>",
            unsafe_allow_html=True,
        )

    # ── Chat history (collapsible) ─────────────────────────────────────────
    history = st.session_state.get("cognee_chat_history", [])
    if len(history) > 1:
        with st.expander(f"Chat History ({len(history)} questions)", expanded=False):
            for i, (q, r) in enumerate(reversed(history)):
                st.markdown(f"**Q{len(history)-i}:** {q}")
                st.markdown(
                    f"<div style='background:#F8FAFC;border:1px solid #E2E8F0;"
                    f"padding:8px 12px;border-radius:6px;margin-bottom:8px;font-size:0.9rem;'>"
                    f"{r['answer'][:300]}{'…' if len(r['answer'])>300 else ''}</div>",
                    unsafe_allow_html=True,
                )
                st.caption(
                    f"Scope: {r.get('scope','')} | Search: {r.get('search_type','')} | "
                    f"Tokens: {r.get('total_tokens',0)} | Triplets: {len(r.get('triples',[]))}"
                )

    if history:
        if st.button(" Clear Cognee Chat History", key="cog_clear_history"):
            st.session_state["cognee_chat_history"] = []
            st.session_state["cognee_last_result"]  = {}
            st.rerun()


# ── SYSTEM DIAGNOSTICS PAGE ──
def render_diagnostics_page():
    st.markdown("<h1 style='font-size: 2.2rem; font-weight: 800; color: #0F172A; margin-top: 0px; margin-bottom: 5px; line-height: 1.1;'>System Diagnostics</h1>", unsafe_allow_html=True)
    st.markdown("<p style='font-size: 1.05rem; font-weight: 500; color: #475569; margin-top: 4px; margin-bottom: 15px;'>Vector Database status, file validation metrics, and active runtime information.</p>", unsafe_allow_html=True)
    
    status = database.get_db_status()
    st.markdown("---")
    
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Database Mode", status["engine_mode"])
    c2.metric("Total Records", f"{status['total_records']:,}")
    c3.metric("Vectorized Records", f"{status['records_with_vectors']:,}")
    c4.metric("Active Athletes", f"{status['active_users']:,}")
    
    st.markdown("### Engine Components Diagnostics")
    
    lib_label = "ChromaDB Library" if "chroma_library" in status else "FAISS Library"
    lib_val = status.get("chroma_library") if "chroma_library" in status else status.get("faiss_library", "Missing")
    
    st.markdown(f"""
    <div class="diagnostic-container">
        <div class="diagnostic-item">
            <div class="diagnostic-label">{lib_label}</div>
            <div class="diagnostic-val">{lib_val}</div>
        </div>
        <div class="diagnostic-item">
            <div class="diagnostic-label">Sentence-Transformers Package</div>
            <div class="diagnostic-val">{status.get('sentence_transformers_library', 'Available')}</div>
        </div>
        <div class="diagnostic-item">
            <div class="diagnostic-label">Embedding Model Loaded</div>
            <div class="diagnostic-val">{status.get('model_loaded', 'Yes')}</div>
        </div>
        <div class="diagnostic-item">
            <div class="diagnostic-label">SQLite File Path</div>
            <div class="diagnostic-val">{database.DB_PATH}</div>
        </div>
        <div class="diagnostic-item">
            <div class="diagnostic-label">Active Memory Tags</div>
            <div class="diagnostic-val">{', '.join(status.get('memory_tags', [])) if status.get('memory_tags') else 'None'}</div>
        </div>
    </div>
    """, unsafe_allow_html=True)
    
    st.markdown("### Interactive Vector Similarity Sandbox")
    st.markdown("Input a search phrase to test the similarity match against all records.")
    
    test_username = st.session_state["username"]
    test_tag = st.selectbox("Select Memory Tag to Query", ["semantic", "episodic", "procedural"])
    test_query = st.text_input("Enter Sandbox Query Test...")
    
    if test_query.strip():
        results = database.vector_query_memories(test_username, test_tag, test_query)
        if not results:
            st.info("No matching records returned from query.")
        else:
            st.write(f"Found {len(results)} matches. Displaying sorted newest first:")
            for idx, r in enumerate(results):
                st.info(f"Match #{idx+1} | Timestamp: {r['timestamp']} | Subtag: {r.get('subtag', 'implicit')}\n\nQuery: {r['query']}\n\nResponse: {r['response']}")

# ── PROFILE PAGE ──
def render_profile_page(username: str):
    st.markdown("<h1 style='font-size: 2.2rem; font-weight: 800; color: #0F172A; margin-top: 0px; margin-bottom: 5px; line-height: 1.1;'>Edit Athlete Profile</h1>", unsafe_allow_html=True)
    st.markdown("<p style='font-size: 1.05rem; font-weight: 500; color: #475569; margin-top: 4px; margin-bottom: 15px;'>Modify your onboarding answers and fitness choices. Your vector database semantic memories will be updated automatically.</p>", unsafe_allow_html=True)
    
    st.markdown("---")
    
    # 1. Fetch current profile entries
    memories = database.get_memories_by_tag(username, "semantic")
    explicit_map = {}
    for m in memories:
        if m.get("subtag") == "explicit":
            explicit_map[m["query"]] = m["response"]
            
    # 2. Build form controls pre-populated with existing choices
    
    # Question 1: Life stage
    q1_val = explicit_map.get("What is your current life stage?", "Working Professional")
    q1_options = ["Student / Teenager", "Working Professional", "Middle-Aged", "Retired"]
    q1_index = q1_options.index(q1_val) if q1_val in q1_options else 1
    p_q1 = st.selectbox(
        "1. What is your current life stage?",
        options=q1_options,
        index=q1_index,
        key="prof_q1"
    )
    
    # Question 2: Fitness Goal
    q2_val = explicit_map.get("What is your primary fitness goal?", "Improve General Fitness & Health")
    q2_options = ["Weight Loss", "Improve General Fitness & Health", "Build Muscle & Strength", "Prepare for Competition / Sports"]
    q2_index = q2_options.index(q2_val) if q2_val in q2_options else 1
    p_q2 = st.selectbox(
        "2. What is your primary fitness goal?",
        options=q2_options,
        index=q2_index,
        key="prof_q2"
    )
    
    # Question 3: Frequency
    q3_val = explicit_map.get("How often do you currently work out?", "2-3 times a week")
    q3_options = ["Daily", "2-3 times a week", "Once a week", "Rarely / Starting fresh"]
    q3_index = q3_options.index(q3_val) if q3_val in q3_options else 1
    p_q3 = st.selectbox(
        "3. How often do you currently work out?",
        options=q3_options,
        index=q3_index,
        key="prof_q3"
    )
    
    # Question 4: Injuries
    raw_injuries = explicit_map.get("Do you have any injuries or health conditions?", "No injuries (I'm good to go!)")
    standard_options = [
        "No injuries (I'm good to go!)", 
        "Knee / Joint issues", 
        "Back / Neck pain", 
        "Asthma / Respiratory issues", 
        "Heart condition / Cardiovascular issues", 
        "High blood pressure (Hypertension)", 
        "Diabetes", 
        "Shoulder impingement / issues", 
        "Herniated disc / spinal issues", 
        "Arthritis / Joint inflammation", 
        "Other"
    ]
    
    if raw_injuries == "None" or raw_injuries == "No injuries (I'm good to go!)":
        default_injuries = ["No injuries (I'm good to go!)"]
        other_val = ""
    else:
        items = [item.strip() for item in raw_injuries.split(",") if item.strip()]
        default_injuries = []
        other_items = []
        for item in items:
            if item in standard_options[:-1]: # exclude 'Other'
                default_injuries.append(item)
            else:
                other_items.append(item)
        if other_items:
            default_injuries.append("Other")
            other_val = ", ".join(other_items)
        else:
            other_val = ""
            
    p_q4 = st.multiselect(
        "4. Do you have any injuries or health conditions? (Select all that apply)",
        options=standard_options,
        default=default_injuries,
        key="prof_q4"
    )
    
    p_other_injury = ""
    if "Other" in p_q4:
        p_other_injury = st.text_input("Please specify other conditions/injuries:", value=other_val, key="prof_other_injury").strip()
        
    # Question 5: Workout level
    q5_val = explicit_map.get("How would you describe your workout level?", "Beginner (New to fitness)")
    q5_options = ["Beginner (New to fitness)", "Intermediate (Comfortable with most exercises)", "Advanced / Pro (Experienced and looking for a challenge)"]
    q5_index = q5_options.index(q5_val) if q5_val in q5_options else 0
    p_q5 = st.selectbox(
        "5. How would you describe your workout level?",
        options=q5_options,
        index=q5_index,
        key="prof_q5"
    )
    
    # Question 6: Train location
    q6_val = explicit_map.get("Where do you prefer to train?", "At Home")
    q6_options = ["At Home", "At the Gym", "Outdoors"]
    q6_index = q6_options.index(q6_val) if q6_val in q6_options else 0
    p_q6 = st.selectbox(
        "6. Where do you prefer to train?",
        options=q6_options,
        index=q6_index,
        key="prof_q6"
    )
    
    # Question 7: Focus Area
    q7_val = explicit_map.get("Which area would you like to focus on?", "Full Body Core & Flexibility")
    q7_options = ["Upper Body (Arms, Chest, Back)", "Lower Body (Legs and Glutes)", "Cardio & Endurance", "Full Body Core & Flexibility"]
    q7_index = q7_options.index(q7_val) if q7_val in q7_options else 3
    p_q7 = st.selectbox(
        "7. Which area would you like to focus on?",
        options=q7_options,
        index=q7_index,
        key="prof_q7"
    )
    
    # Question 8: Dedicated Time
    q8_val = explicit_map.get("How much time can you dedicate to a single workout?", "30-60 mins")
    q8_options = ["15-30 mins", "30-60 mins", "60+ mins"]
    q8_index = q8_options.index(q8_val) if q8_val in q8_options else 1
    p_q8 = st.selectbox(
        "8. How much time can you dedicate to a single workout?",
        options=q8_options,
        index=q8_index,
        key="prof_q8"
    )
    
    # Question 9: Diet preference
    q9_val = explicit_map.get("What is your diet preference?", "Non Vegan")
    q9_options = ["Vegan", "Non Vegan"]
    q9_index = q9_options.index(q9_val) if q9_val in q9_options else 1
    p_q9 = st.selectbox(
        "9. What is your diet preference?",
        options=q9_options,
        index=q9_index,
        key="prof_q9"
    )
    
    # Question 10: Height
    q10_val = explicit_map.get("What is your current height (in cm)?", "")
    p_q10 = st.text_input("10. What is your current height (in cm)?", value=q10_val, key="prof_q10").strip()
    
    # Question 11: Weight
    q11_val = explicit_map.get("What is your current weight (in kg)?", "")
    p_q11 = st.text_input("11. What is your current weight (in kg)?", value=q11_val, key="prof_q11").strip()
    
    # Question 12: Country
    q12_val = explicit_map.get("What is your country?", "India")
    q12_options = ["India", "United States", "United Kingdom", "Canada", "Australia", "Other"]
    q12_index = q12_options.index(q12_val) if q12_val in q12_options else 0
    p_q12 = st.selectbox("12. What is your country?", options=q12_options, index=q12_index, key="prof_q12")
    
    # Question 13: Allergies & Intolerances
    q13_val = explicit_map.get("Do you have any food allergies or intolerances?", None)
    if q13_val is None:
        # Combine old separate values for legacy users
        old_alg = explicit_map.get("Do you have any food food allergies?", "None")
        if old_alg == "None":
            old_alg = explicit_map.get("Do you have any food allergies?", "None")
        old_int = explicit_map.get("Do you have any food intolerances?", "None")
        combined_list = []
        if old_alg and old_alg.lower() != "none":
            combined_list.extend([x.strip() for x in old_alg.split(",") if x.strip()])
        if old_int and old_int.lower() != "none":
            combined_list.extend([x.strip() for x in old_int.split(",") if x.strip()])
        q13_val = ", ".join(combined_list) if combined_list else "None"

    q13_options = ["None", "Peanuts", "Tree nuts", "Soy", "Gluten", "Dairy", "Eggs", "Sesame", "Fish / Shellfish", "Lactose", "Fructose", "Histamine", "Gluten sensitivity", "Caffeine"]
    q13_default = [item.strip() for item in q13_val.split(",") if item.strip()] if q13_val else ["None"]
    q13_default = [item for item in q13_default if item in q13_options]
    if not q13_default:
        q13_default = ["None"]
    p_q13 = st.multiselect("13. Do you have any food allergies or intolerances? (Select all that apply)", options=q13_options, default=q13_default, key="prof_q13")
    
    # Question 14: Max prep time
    q15_val = explicit_map.get("What is the maximum time you can dedicate to meal prep?", "20 mins")
    q15_options = ["10 mins", "20 mins", "30 mins", "45 mins", "60+ mins"]
    q15_index = q15_options.index(q15_val) if q15_val in q15_options else 1
    p_q15 = st.selectbox("14. What is the maximum time you can dedicate to meal prep?", options=q15_options, index=q15_index, key="prof_q15")
    
    # Question 15: Preferred cuisines
    q16_val = explicit_map.get("Which cuisines do you prefer?", "Indian, Mediterranean")
    q16_options = ["Indian", "Mediterranean", "Mexican", "Italian", "Asian", "American", "Middle Eastern"]
    q16_default = [item.strip() for item in q16_val.split(",") if item.strip()] if q16_val else ["Indian", "Mediterranean"]
    q16_default = [item for item in q16_default if item in q16_options]
    if not q16_default:
        q16_default = ["Indian", "Mediterranean"]
    p_q16 = st.multiselect("15. Which cuisines do you prefer? (Select all that apply)", options=q16_options, default=q16_default, key="prof_q16")
    
    # Question 16: Typical workout time
    q17_val = explicit_map.get("What is your typical workout time?", "Evening")
    q17_options = ["Morning", "Afternoon", "Evening", "Night"]
    q17_index = q17_options.index(q17_val) if q17_val in q17_options else 2
    p_q17 = st.selectbox("16. What is your typical workout time?", options=q17_options, index=q17_index, key="prof_q17")
    
    st.markdown("<br>", unsafe_allow_html=True)
    if st.button("Save Profile Settings", key="prof_save_btn", use_container_width=True):
        if not p_q10:
            st.error("Please enter your current height.")
        elif not p_q11:
            st.error("Please enter your current weight.")
        else:
            # Compile injuries answer
            injuries_list = [item for item in p_q4 if item != "Other"]
            if "Other" in p_q4 and p_other_injury:
                injuries_list.append(p_other_injury)
            injuries_str = ", ".join(injuries_list) if injuries_list else "None"
            
            # Save or update explicit memories
            database.save_or_update_explicit_memory(username, "What is your current life stage?", p_q1)
            database.save_or_update_explicit_memory(username, "What is your primary fitness goal?", p_q2)
            database.save_or_update_explicit_memory(username, "How often do you currently work out?", p_q3)
            database.save_or_update_explicit_memory(username, "Do you have any injuries or health conditions?", injuries_str)
            database.save_or_update_explicit_memory(username, "How would you describe your workout level?", p_q5)
            database.save_or_update_explicit_memory(username, "Where do you prefer to train?", p_q6)
            database.save_or_update_explicit_memory(username, "Which area would you like to focus on?", p_q7)
            database.save_or_update_explicit_memory(username, "How much time can you dedicate to a single workout?", p_q8)
            database.save_or_update_explicit_memory(username, "What is your diet preference?", p_q9)
            database.save_or_update_explicit_memory(username, "What is your current height (in cm)?", p_q10)
            database.save_or_update_explicit_memory(username, "What is your current weight (in kg)?", p_q11)
            database.save_or_update_explicit_memory(username, "What is your country?", p_q12)
            database.save_or_update_explicit_memory(username, "Do you have any food allergies or intolerances?", ", ".join(p_q13))
            database.save_or_update_explicit_memory(username, "What is the maximum time you can dedicate to meal prep?", p_q15)
            database.save_or_update_explicit_memory(username, "Which cuisines do you prefer?", ", ".join(p_q16))
            database.save_or_update_explicit_memory(username, "What is your typical workout time?", p_q17)
            
            st.success("Profile updated successfully!")
            time.sleep(0.5)
            st.rerun()


# ------------------------------------------------------------
# BOOTSTRAP RUNNER
# ------------------------------------------------------------
@st.cache_resource(show_spinner="Warming up model cache...")
def preload_models_once():
    import threading
    auth.init_auth_db()
    database.init_db()
    try:
        # Load embedding and reranker models in background thread so UI starts immediately
        threading.Thread(target=database.get_transformer_model, daemon=True).start()
        threading.Thread(target=database.get_reranker_model, daemon=True).start()
        return True
    except Exception as e:
        print(f"Background pre-load failed: {e}")
        return False


@st.cache_resource(show_spinner=False)
def _ensure_cognee_ingested() -> dict:
    """
    Runs once per server process (not per user login) via @st.cache_resource.
    Spawns a background thread to ingest exercise data if LanceDB is empty,
    so the UI stays responsive while the ingest runs.
    Returns a shared state dict: {"status": "pending"|"done"|"error"|"skipped", "error": str}
    """
    import threading
    state = {"status": "pending", "error": ""}

    if not is_cognee_available():
        state["status"] = "skipped"
        return state

    def _run():
        try:
            api_key = (
                os.environ.get("GEMINI_API_KEY")
                or os.environ.get("LLM_API_KEY", "")
            )
            result = _cog.auto_ingest_if_needed(api_key=api_key or None)
            state["status"] = "done" if result != "already_ingested" else "skipped"
        except Exception as e:
            state["status"] = "error"
            state["error"] = str(e)
            print(f"[cognee startup ingest] {e}")

    threading.Thread(target=_run, name="cognee-startup-ingest", daemon=True).start()
    return state


@st.cache_resource(show_spinner=False)
def _cognee_server_state() -> dict:
    """
    Mutable dict cached at the server process level — shared across all user sessions.
    Prevents re-ingestion on every relogin when data already exists in Neo4j/LanceDB.
    Keys: ready (None=unchecked, True=ready, False=failed)
    """
    return {"ready": None}

if __name__ == "__main__":
    preload_models_once()
    import threading
    threading.Thread(target=_ensure_cognee_ingested, name="cognee-startup-lazy", daemon=True).start()



    # Seeding test user profiles (Sarah & David) once on startup
    try:
        from seeding import seed_sarah_if_needed, seed_david_if_needed
        # Try to resolve any configured API key to seed embeddings if available
        api_key_for_seed = _get_api_key()
        seed_sarah_if_needed(api_key_for_seed)
        seed_david_if_needed(api_key_for_seed)
        print("Both user data seeded successfully.")
    except Exception as e:
        print(f"Auto-seeding notification (safe to ignore if db is locked): {e}")

    if not st.session_state["logged_in"]:
        render_auth_page()
    else:
        database.warm_up_cache(st.session_state["username"])
        render_main_app()

import sqlite3
import hashlib
import os

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "users.db")

def init_auth_db():
    """Initializes the database and creates the users table if it doesn't exist."""
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL
        )
    """)
    # Check if 'sarah' is already seeded
    cursor.execute("SELECT 1 FROM users WHERE username = ?", ("sarah",))
    if not cursor.fetchone():
        password_hash = hashlib.sha256("sarah".encode("utf-8")).hexdigest()
        cursor.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)", ("sarah", password_hash))
    
    # Check if 'david' is already seeded
    cursor.execute("SELECT 1 FROM users WHERE username = ?", ("david",))
    if not cursor.fetchone():
        password_hash = hashlib.sha256("david".encode("utf-8")).hexdigest()
        cursor.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)", ("david", password_hash))
    conn.commit()
    conn.close()

def _hash_password(password: str) -> str:
    """Helper function to hash a password using SHA-256."""
    return hashlib.sha256(password.encode("utf-8")).hexdigest()

def register_user(username: str, password: str) -> tuple[bool, str]:
    """Registers a new user. Returns (Success, Message)."""
    username = username.strip()
    if not username:
        return False, "Username cannot be empty."
    if not password:
        return False, "Password cannot be empty."
    if len(password) < 4:
        return False, "Password must be at least 4 characters long."

    init_auth_db()
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    cursor = conn.cursor()
    try:
        password_hash = _hash_password(password)
        cursor.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)", (username, password_hash))
        conn.commit()
        return True, "User registered successfully."
    except sqlite3.IntegrityError:
        return False, "Username already exists."
    finally:
        conn.close()

def verify_user(username: str, password: str) -> bool:
    """Verifies user credentials. Returns True if valid, False otherwise."""
    username = username.strip()
    if not username or not password:
        return False

    init_auth_db()
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    cursor = conn.cursor()
    cursor.execute("SELECT password_hash FROM users WHERE username = ?", (username,))
    row = cursor.fetchone()
    conn.close()

    if row:
        stored_hash = row[0]
        return stored_hash == _hash_password(password)
    return False

def user_exists(username: str) -> bool:
    """Checks if a username already exists in the database."""
    username = username.strip().lower()
    if not username:
        return False
        
    init_auth_db()
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM users WHERE username = ?", (username,))
    row = cursor.fetchone()
    conn.close()
    return row is not None

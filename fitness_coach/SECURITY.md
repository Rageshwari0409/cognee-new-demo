# Security files in this package

Two dot-files at the root of this folder are hidden by default in Windows Explorer:

| Hidden file | What it does | How to see it |
|-------------|-------------|---------------|
| `.gitignore` | Tells git what NOT to commit (API keys, user health data, caches) | Explorer: View → Show → Hidden items |
| `.env.example` | Template for your API key — copy to `.env` and fill in | Explorer: View → Show → Hidden items |

## Setting up your API key

```
# Step 1 — copy the template
copy .env.example .env          # Windows CMD
Copy-Item .env.example .env     # PowerShell

# Step 2 — open .env and add your key
GEMINI_API_KEY=your-key-here
```

The `.env` file is already listed in `.gitignore` so it will never be accidentally committed.

## What is protected by .gitignore

```
.env                    <- your API key
*.env                   <- any other env files
logs/                   <- conversation logs if you add logging
__pycache__/            <- Python bytecode
.ipynb_checkpoints/     <- Jupyter noise
venv/ .venv/            <- local virtual environments
```

## User health data in memory/

`memory/profile.json` and `memory/memories.md` hold diet constraints,
health notes, and personal preferences. For production use:

1. Uncomment the `memory/` lines in `.gitignore`
2. Store real user profiles in a database, not flat files
3. Never log the contents of these files

## API key exposure checklist before sharing this folder

- [ ] `.env` file is NOT in this folder (only `.env.example` should be)  
- [ ] No API key appears in `coach.py`, `demo/gemini_coach.py`, or any notebook output  
- [ ] `memory/profile.json` contains only demo data, not a real user's health info  
- [ ] Jupyter notebook cells have been cleared of any output containing personal data

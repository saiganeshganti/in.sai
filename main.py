from fastapi import FastAPI, HTTPException, Depends, Query, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from tavily import TavilyClient
from google import genai
from google.genai import types
from dotenv import load_dotenv
from pathlib import Path
import os
import re
import json
import time
import threading
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import urllib.request
import urllib.error
import urllib.parse
from urllib.parse import urljoin, urlparse
from html.parser import HTMLParser
from sqlalchemy import text as sql_text, inspect as sqlalchemy_inspect
from sqlalchemy.schema import CreateColumn

from database import engine, SessionLocal, Base
from models import Candidate, Recruiter
import auth_utils


# =========================================================
# ENVIRONMENT / API KEYS
# =========================================================

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"

load_dotenv(ENV_FILE)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")


print("========================================")
print("ENV FILE:", ENV_FILE)
print("ENV EXISTS:", ENV_FILE.exists())
print("GEMINI KEY LOADED:", bool(GEMINI_API_KEY))
print("TAVILY KEY LOADED:", bool(TAVILY_API_KEY))
print("GITHUB TOKEN LOADED:", bool(GITHUB_TOKEN))
print("========================================")


if not GEMINI_API_KEY:
    raise RuntimeError(
        "GEMINI_API_KEY is not configured in .env"
    )

if not TAVILY_API_KEY:
    raise RuntimeError(
        "TAVILY_API_KEY is not configured in .env"
    )


# =========================================================
# API CLIENTS
# =========================================================

gemini_client = genai.Client(
    api_key=GEMINI_API_KEY
)

tavily_client = TavilyClient(
    api_key=TAVILY_API_KEY
)


# =========================================================
# DATABASE
# =========================================================

Base.metadata.create_all(
    bind=engine
)


# =========================================================
# DATABASE SCHEMA MIGRATION
# =========================================================
# Base.metadata.create_all() creates missing tables, but it does NOT
# add newly introduced columns to an existing database table.
#
# This application runs locally with SQLite and in production with
# PostgreSQL on Render, so the old SQLite-only PRAGMA migration is not
# sufficient. The migration below uses SQLAlchemy's active database
# dialect and compares the actual database schema with the models.
#
# It adds only columns that are missing. Existing tables, rows, data,
# indexes and columns are left untouched. This is safe to run on every
# startup. In particular, it fixes the current production issue where
# recruiters.reset_token and recruiters.reset_token_expires exist in the
# SQLAlchemy model but not yet in the Render PostgreSQL table.

def _run_schema_migrations():
    try:
        inspector = sqlalchemy_inspect(engine)
        preparer = engine.dialect.identifier_preparer

        # Check every mapped table rather than maintaining a separate
        # SQLite-only list. This keeps SQLite and PostgreSQL in sync as
        # the models evolve.
        for table in Base.metadata.sorted_tables:
            table_name = table.name

            try:
                existing_columns = {
                    column["name"]
                    for column in inspector.get_columns(table_name)
                }
            except Exception as error:
                print(
                    f"MIGRATION: could not inspect table '{table_name}':",
                    repr(error)
                )
                continue

            missing_columns = [
                column
                for column in table.columns
                if column.name not in existing_columns
            ]

            if not missing_columns:
                continue

            quoted_table = preparer.quote(table_name)

            for column in missing_columns:
                # A newly-added NOT NULL column without a default cannot
                # safely be added to a populated table. Skip it rather
                # than risking a startup failure. New nullable columns
                # (including the password-reset fields) are added normally.
                if (
                    not column.nullable
                    and column.default is None
                    and column.server_default is None
                    and not column.primary_key
                ):
                    print(
                        f"MIGRATION: skipped non-nullable column "
                        f"'{table_name}.{column.name}' because it has no default"
                    )
                    continue

                column_definition = str(
                    CreateColumn(column).compile(
                        dialect=engine.dialect
                    )
                )

                statement = (
                    f"ALTER TABLE {quoted_table} "
                    f"ADD COLUMN {column_definition}"
                )

                print(
                    f"MIGRATION: adding missing column "
                    f"'{table_name}.{column.name}'"
                )

                with engine.begin() as connection:
                    connection.execute(
                        sql_text(statement)
                    )

            # Refresh the inspector after changes so subsequent checks
            # see the newly added columns.
            inspector = sqlalchemy_inspect(engine)

        print("DATABASE SCHEMA MIGRATION: completed")

    except Exception as error:
        # Log the migration failure clearly. Do not silently hide it.
        # The application may still start if the missing schema is not
        # needed by a particular request, but Render logs will show the
        # exact migration problem.
        print("DATABASE SCHEMA MIGRATION ERROR:", repr(error))


_run_schema_migrations()


# =========================================================
# SMTP / EMAIL SENDING CONFIGURATION
# =========================================================
# Each recruiter sends from their OWN Gmail account, entered once in
# their account settings and stored encrypted (see auth_utils.py and
# POST /auth/smtp below). There is no shared/global sending account.
#
# SMTP_HOST / SMTP_PORT are still configurable via environment
# variables in case Gmail is ever swapped for another provider, but
# the actual "From" address and password now always come from the
# logged-in recruiter's row in the database.

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))

# =========================================================
# SYSTEM EMAIL (separate from any recruiter's own Gmail)
# =========================================================
# Used ONLY for transactional emails the platform itself sends --
# right now, just password reset links. A locked-out recruiter may
# not have connected their own Gmail yet, so reset emails can't rely
# on per-recruiter SMTP credentials the way bulk outreach does.
#
# Set these on Render as environment variables (can be the same Gmail
# + App Password you used for the old shared SMTP_EMAIL, or a fresh one):
#   SYSTEM_SMTP_EMAIL
#   SYSTEM_SMTP_APP_PASSWORD

SYSTEM_SMTP_EMAIL = os.getenv("SYSTEM_SMTP_EMAIL")
SYSTEM_SMTP_APP_PASSWORD = os.getenv("SYSTEM_SMTP_APP_PASSWORD")

print("SYSTEM EMAIL CONFIGURED:", bool(SYSTEM_SMTP_EMAIL and SYSTEM_SMTP_APP_PASSWORD))

LOGIN_LOCKOUT_MAX_ATTEMPTS = 5
LOGIN_LOCKOUT_DURATION_SECONDS = 15 * 60  # 15 minutes
RESET_TOKEN_VALID_MINUTES = 30


def send_email_via_smtp(
    to_email: str,
    subject: str,
    body: str,
    from_email: str,
    from_app_password: str,
    sender_name: str = ""
):
    """
    Sends one plain-text email via SMTP (SSL) using the GIVEN sender's
    credentials -- never a global/shared account. Raises an exception
    on failure; callers catch it per-candidate so one bad send doesn't
    stop the whole batch.
    """
    if not from_email or not from_app_password:
        raise RuntimeError(
            "This recruiter has not connected a sending email yet. "
            "Add a Gmail address and App Password in Email Settings."
        )

    display_name = sender_name or from_email

    message = MIMEMultipart()
    message["From"] = f"{display_name} <{from_email}>"
    message["To"] = to_email
    message["Subject"] = subject
    message.attach(MIMEText(body, "plain"))

    context = ssl.create_default_context()

    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=context, timeout=20) as server:
        server.login(from_email, from_app_password)
        server.sendmail(from_email, to_email, message.as_string())


# =========================================================
# FAST EMAIL ENRICHMENT
# =========================================================
# Candidate search NEVER waits for an email to exist.
# Emails are optional: when a reliable public email is already present in
# Tavily's result content, or a GitHub profile exposes a public email,
# it is shown. Otherwise the candidate is still returned with email=None.

EMAIL_PATTERN = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
)

GENERIC_EMAIL_PREFIXES = {
    "info", "support", "contact", "hello", "admin", "sales",
    "careers", "career", "jobs", "hr", "team", "help", "office",
    "service", "services", "marketing", "privacy", "legal", "abuse",
    "webmaster", "noreply", "no-reply", "donotreply", "call"
}

GENERIC_EMAIL_DOMAINS = {
    "linkedin.com", "facebook.com", "instagram.com", "twitter.com",
    "x.com", "example.com"
}


def normalize_email(value):
    if not value:
        return None
    value = value.strip().strip("<>[](){}.,;:'\\\" ")
    match = EMAIL_PATTERN.search(value)
    if not match:
        return None
    return match.group(0).strip()


def _email_is_generic(email):
    email = normalize_email(email)
    if not email:
        return True
    local, _, domain = email.partition("@")
    return local.lower() in GENERIC_EMAIL_PREFIXES or domain.lower() in GENERIC_EMAIL_DOMAINS


def extract_candidate_email_from_text(text, candidate_name=""):
    if not text:
        return None

    normalized = re.sub(r"\s*\[at\]\s*|\s*\(at\)\s*|\s+at\s+", "@", text, flags=re.I)
    normalized = re.sub(r"\s*\[dot\]\s*|\s*\(dot\)\s*|\s+dot\s+", ".", normalized, flags=re.I)

    matches = []
    name_parts = [p.lower() for p in re.findall(r"[A-Za-z]+", candidate_name or "") if len(p) >= 3]

    for match in EMAIL_PATTERN.finditer(normalized):
        email = normalize_email(match.group(0))
        if not email or _email_is_generic(email):
            continue

        # Prefer an email that clearly resembles the candidate's name,
        # but DO NOT require a name match. Public profile pages often
        # place the contact email far away from the person's name.
        local = email.split("@", 1)[0].lower()
        window = normalized[max(0, match.start()-300):match.end()+300].lower()
        nearby = sum(1 for part in name_parts if part in window)
        name_score = sum(2 for part in name_parts if part in local)
        local_name_bonus = 1 if any(len(token) >= 4 and token in local for token in name_parts) else 0
        score = nearby * 3 + name_score + local_name_bonus
        matches.append((score, email))

    if not matches:
        return None

    # Highest-confidence email first; score 0 is still valid because
    # it is a real public email found in the source text.
    matches.sort(key=lambda item: item[0], reverse=True)
    return matches[0][1]


def _github_username_from_candidate(candidate):
    """Find a GitHub username from the candidate URL or Tavily content."""
    values = [candidate.get("url", ""), candidate.get("content", "")]
    pattern = re.compile(r"https?://(?:www\.)?github\.com/([A-Za-z0-9-]+)(?:/|\b)", re.I)
    for value in values:
        for match in pattern.finditer(value or ""):
            username = match.group(1)
            if username.lower() not in {"features", "pricing", "login", "signup", "orgs", "topics", "about", "marketplace"}:
                return username
    return None


def _github_api_get(path, timeout=2.0):
    """Small authenticated GitHub GET helper. Only public profile/repository data is read."""
    if not GITHUB_TOKEN:
        return None

    request = urllib.request.Request(
        f"https://api.github.com{path}",
        headers={
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2026-03-10",
            "User-Agent": "In-SAI-AI-Recruiter"
        }
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(
                response.read(300_000).decode("utf-8", errors="ignore")
            )
    except Exception as error:
        print("GitHub API error:", repr(error))
        return None


def _github_insights(candidate):
    """
    Automatically enrich a candidate only when a GitHub profile URL/username
    can be found in the candidate's public search data. Candidates without
    GitHub are not blocked and simply receive available=False.
    """
    username = _github_username_from_candidate(candidate)

    insights = {
        "available": False,
        "username": None,
        "profile_url": None,
        "avatar_url": None,
        "name": None,
        "bio": None,
        "company": None,
        "location": None,
        "website": None,
        "public_repos": 0,
        "followers": 0,
        "following": 0,
        "languages": [],
        "projects": [],
        "total_stars": 0,
        "public_email": None
    }

    if not username or not GITHUB_TOKEN:
        return insights

    safe_username = urllib.parse.quote(username, safe="")
    profile = _github_api_get(
        f"/users/{safe_username}",
        timeout=2.0
    )

    if not isinstance(profile, dict) or profile.get("message") == "Not Found":
        return insights

    public_email = normalize_email(profile.get("email"))
    if public_email and _email_is_generic(public_email):
        public_email = None

    insights.update({
        "available": True,
        "username": profile.get("login") or username,
        "profile_url": profile.get("html_url") or f"https://github.com/{username}",
        "avatar_url": profile.get("avatar_url"),
        "name": profile.get("name"),
        "bio": profile.get("bio"),
        "company": profile.get("company"),
        "location": profile.get("location"),
        "website": profile.get("blog"),
        "public_repos": profile.get("public_repos") or 0,
        "followers": profile.get("followers") or 0,
        "following": profile.get("following") or 0,
        "public_email": public_email
    })

    # A larger sample (still one API call) so "total stars" reflects more
    # than just the 5 most-recently-updated repos shown as projects.
    repos = _github_api_get(
        f"/users/{safe_username}/repos?sort=updated&direction=desc&per_page=30",
        timeout=2.5
    )

    if isinstance(repos, list):
        languages = []
        projects = []
        total_stars = 0

        for repo in repos:
            if not isinstance(repo, dict) or repo.get("fork"):
                continue

            stars = repo.get("stargazers_count") or 0
            total_stars += stars

            language = (repo.get("language") or "").strip()
            if language and language not in languages:
                languages.append(language)

            if len(projects) < 5:
                projects.append({
                    "name": repo.get("name"),
                    "description": repo.get("description"),
                    "language": language or None,
                    "url": repo.get("html_url"),
                    "stars": stars,
                    "updated_at": repo.get("updated_at")
                })

        insights["languages"] = languages[:8]
        insights["projects"] = projects
        insights["total_stars"] = total_stars

    return insights


def _github_public_email(candidate):
    """Read only the public GitHub profile email, when a GitHub profile is linked."""
    insights = _github_insights(candidate)
    # Keep this helper lightweight-compatible with the existing email flow.
    username = insights.get("username")
    email = None
    if username and GITHUB_TOKEN:
        profile = _github_api_get(
            f"/users/{urllib.parse.quote(username, safe='')}",
            timeout=1.5
        )
        if isinstance(profile, dict):
            email = normalize_email(profile.get("email"))

    if email and not _email_is_generic(email):
        return email
    return None


def discover_candidate_email(candidate):
    """Fast, optional enrichment. Never prevents a candidate from being returned."""
    content = candidate.get("content", "")
    name = candidate.get("title", "")

    email = extract_candidate_email_from_text(content, name)
    if email:
        return email

    return _github_public_email(candidate)


# =========================================================
# FASTAPI APPLICATION
# =========================================================

app = FastAPI(
    title="In SAI AI Recruiter",
    description=(
        "AI-Powered Talent Discovery, "
        "Recruitment & Outreach."
    ),
    version="1.5.0"
)
# =========================================================
# CORS - FRONTEND / LOCAL DEVELOPMENT
# =========================================================
# The browser error "TypeError: Failed to fetch" can happen before FastAPI
# receives the request when the frontend origin is not allowed by CORS.
# Keep the deployed frontend(s) and common local development origins
# explicitly allowed. An optional FRONTEND_URL can also be added to .env.

CONFIGURED_FRONTEND_URL = os.getenv(
    "FRONTEND_URL",
    "https://insaiairecruiter.netlify.app"
).strip().rstrip("/")

ALLOWED_ORIGINS = {
    "https://insaiairecruiter.netlify.app",
    "https://in-sai.vercel.app",
    "http://localhost:3000",
    "http://localhost:5173",
    "http://localhost:5500",
    "http://localhost:8000",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:5500",
    "http://127.0.0.1:8000",
}

if CONFIGURED_FRONTEND_URL:
    ALLOWED_ORIGINS.add(CONFIGURED_FRONTEND_URL)

app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(ALLOWED_ORIGINS),
    # If Vercel preview-deploy URLs (e.g. in-sai-git-branch-you.vercel.app)
    # also need to reach this API, uncomment the line below instead of
    # relying only on the exact-match list above:
    # allow_origin_regex=r"https://in-sai.*\.vercel\.app",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"]
)

print("CORS ALLOWED ORIGINS:", sorted(ALLOWED_ORIGINS))

# =========================================================
# DATABASE SESSION
# =========================================================

def get_db():
    db = SessionLocal()

    try:
        yield db

    finally:
        db.close()


# =========================================================
# RECRUITER AUTH MODELS
# =========================================================

class RecruiterRegister(BaseModel):
    name: str
    email: str
    password: str


class RecruiterLogin(BaseModel):
    email: str
    password: str


class RecruiterSMTPUpdate(BaseModel):
    smtp_email: str
    smtp_app_password: str


class ForgotPasswordRequest(BaseModel):
    email: str
    page_url: str = ""  # e.g. "https://your-frontend.com/login.html", used to build the reset link


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str


# =========================================================
# AUTH DEPENDENCY
# =========================================================
# Reads "Authorization: Bearer <token>" from the request, verifies
# the token, and returns the matching Recruiter row. Endpoints that
# need to know "which recruiter is doing this" depend on this.

def get_current_recruiter(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db)
):
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Not logged in.")

    token = authorization.split(" ", 1)[1].strip()

    recruiter_id = auth_utils.verify_session_token(token)

    if not recruiter_id:
        raise HTTPException(status_code=401, detail="Session expired or invalid. Please log in again.")

    recruiter = db.query(Recruiter).filter(Recruiter.id == recruiter_id).first()

    if not recruiter:
        raise HTTPException(status_code=401, detail="Account not found.")

    return recruiter


# =========================================================
# REQUEST MODELS
# =========================================================

class RecruitmentRequest(BaseModel):
    role: str = ""

    skills: list[str] = Field(
        default_factory=list
    )

    location: str = ""
    region: str = ""
    state: str = ""
    city: str = ""
    experience: str = ""
    gender: str = ""

    # =====================================================
    # SEARCH SOURCES
    # =====================================================

    sources: list[str] = Field(
        default_factory=lambda: [
            "linkedin",
            "public_web"
        ]
    )


# =========================================================
# CANDIDATE CREATE MODEL
# =========================================================

class GitHubSearchRequest(BaseModel):
    name: str = ""
    role: str = ""
    company: str = ""
    location: str = ""


class GitHubLookupRequest(BaseModel):
    name: str = ""
    email: str | None = None
    company: str | None = None
    location: str | None = None
    current_role: str | None = None
    linkedin_url: str | None = None
    skills: list[str] = Field(default_factory=list)


class CandidateCreate(BaseModel):
    name: str
    email: str | None = None
    linkedin_url: str | None = None
    current_role: str | None = None
    company: str | None = None
    skills: str | None = None
    experience: str | None = None
    region: str | None = None
    state: str | None = None
    city: str | None = None
    location: str | None = None
    gender: str | None = None
    finance_category: str | None = None
    finance_subcategory: str | None = None
    status: str = "New"


# =========================================================
# CANDIDATE STATUS UPDATE MODEL
# =========================================================

class CandidateStatusUpdate(BaseModel):
    status: str


# =========================================================
# CANDIDATE NOTES / DO-NOT-CONTACT UPDATE MODEL
# =========================================================

class CandidateNotesUpdate(BaseModel):
    notes: str | None = None
    do_not_contact: bool | None = None


# =========================================================
# BULK EMAIL REQUEST MODEL
# =========================================================

class BulkEmailRequest(BaseModel):
    candidate_ids: list[int] = Field(default_factory=list)
    email_type: str = "initial_outreach"
    tone: str = "professional"
    requested_role: str = ""
    search_query: str = ""
    search_role: str = ""
    search_skill: str = ""
    search_context: dict = Field(default_factory=dict)


# =========================================================
# EMAIL GENERATION MODEL
# =========================================================

class EmailGenerationRequest(BaseModel):
    candidate_id: int
    email_type: str = "initial_outreach"
    tone: str = "professional"

    # The role/category that the recruiter searched for.
    # This is the target position for the outreach email.
    requested_role: str = ""
    search_query: str = ""
    search_role: str = ""
    search_skill: str = ""
    search_context: dict = Field(default_factory=dict)


# =========================================================
# HOME PAGE
# =========================================================
# NOTE: This backend no longer serves the frontend HTML. The frontend is
# deployed separately (Vercel). These routes previously used FileResponse
# on relative paths pointing at a templates/ directory that isn't deployed
# on Render, which crashed with a 500 on every request -- including
# Render's health check ping to "/", which likely caused the service to
# be marked unhealthy and restarted intermittently. They now return plain
# JSON so they can never crash the process.

@app.get("/")
def home():
    return {"status": "ok", "service": "In SAI AI Recruiter API"}


# =========================================================
# TALENT POOL PAGE
# =========================================================

@app.get("/talent-pool")
def talent_pool_page():
    return {"status": "ok", "message": "Use the frontend app for this page."}


# =========================================================
# OUTREACH PAGE
# =========================================================

@app.get("/outreach")
def outreach_page():
    return {"status": "ok", "message": "Use the frontend app for this page."}


# =========================================================
# HEALTH CHECK
# =========================================================

@app.get("/health")
def health():
    return {
        "status": "healthy",
        "application": "In SAI AI Recruiter",
        "gemini_configured": bool(GEMINI_API_KEY),
        "tavily_configured": bool(TAVILY_API_KEY),
        "github_configured": bool(GITHUB_TOKEN)
    }


# =========================================================
# RECRUITER REGISTRATION
# =========================================================

@app.post("/auth/register")
def register_recruiter(
    data: RecruiterRegister,
    db: Session = Depends(get_db)
):

    name = data.name.strip()
    email = data.email.strip().lower()
    password = data.password

    if not name or not email or not password:
        return {"status": "error", "message": "Name, email, and password are required."}

    if len(password) < 8:
        return {"status": "error", "message": "Password must be at least 8 characters."}

    existing = db.query(Recruiter).filter(Recruiter.email == email).first()

    if existing:
        return {"status": "error", "message": "An account with this email already exists."}

    recruiter = Recruiter(
        name=name,
        email=email,
        password_hash=auth_utils.hash_password(password),
        created_at=datetime.now(timezone.utc)
    )

    db.add(recruiter)
    db.commit()
    db.refresh(recruiter)

    token = auth_utils.create_session_token(recruiter.id)

    return {
        "status": "success",
        "token": token,
        "recruiter": {
            "id": recruiter.id,
            "name": recruiter.name,
            "email": recruiter.email,
            "smtp_configured": bool(recruiter.smtp_email and recruiter.smtp_app_password_encrypted)
        }
    }


# =========================================================
# RECRUITER LOGIN
# =========================================================

@app.post("/auth/login")
def login_recruiter(
    data: RecruiterLogin,
    db: Session = Depends(get_db)
):

    email = data.email.strip().lower()

    recruiter = db.query(Recruiter).filter(Recruiter.email == email).first()

    now = datetime.now(timezone.utc)

    # -----------------------------------------------------
    # ALREADY LOCKED
    # -----------------------------------------------------

    if recruiter and recruiter.locked_until:
        locked_until = recruiter.locked_until
        if locked_until.tzinfo is None:
            locked_until = locked_until.replace(tzinfo=timezone.utc)

        if locked_until > now:
            retry_after = int((locked_until - now).total_seconds())
            return {
                "status": "error",
                "locked": True,
                "retry_after_seconds": retry_after,
                "message": "Too many wrong attempts. Please wait before trying again, or reset your password."
            }
        else:
            # Lock has expired -- clear it before continuing.
            recruiter.locked_until = None
            recruiter.failed_login_attempts = 0
            db.commit()

    # -----------------------------------------------------
    # WRONG EMAIL OR PASSWORD
    # -----------------------------------------------------

    if not recruiter or not auth_utils.verify_password(data.password, recruiter.password_hash):

        if recruiter:
            recruiter.failed_login_attempts = (recruiter.failed_login_attempts or 0) + 1

            if recruiter.failed_login_attempts >= LOGIN_LOCKOUT_MAX_ATTEMPTS:
                recruiter.locked_until = now + timedelta(seconds=LOGIN_LOCKOUT_DURATION_SECONDS)
                db.commit()
                return {
                    "status": "error",
                    "locked": True,
                    "retry_after_seconds": LOGIN_LOCKOUT_DURATION_SECONDS,
                    "message": "Too many wrong attempts. Your account is temporarily locked."
                }

            db.commit()

        return {"status": "error", "message": "Incorrect email or password."}

    # -----------------------------------------------------
    # SUCCESS
    # -----------------------------------------------------

    recruiter.failed_login_attempts = 0
    recruiter.locked_until = None
    db.commit()

    token = auth_utils.create_session_token(recruiter.id)

    return {
        "status": "success",
        "token": token,
        "recruiter": {
            "id": recruiter.id,
            "name": recruiter.name,
            "email": recruiter.email,
            "smtp_configured": bool(recruiter.smtp_email and recruiter.smtp_app_password_encrypted)
        }
    }


# =========================================================
# FORGOT PASSWORD -- send a reset link by email
# =========================================================
# Always returns a generic success-shaped message, whether or not the
# email matches an account, so this endpoint can't be used to check
# which emails have accounts (a common security practice). The actual
# email is only sent when a match is found.

@app.post("/auth/forgot-password")
def forgot_password(
    data: ForgotPasswordRequest,
    db: Session = Depends(get_db)
):

    if not SYSTEM_SMTP_EMAIL or not SYSTEM_SMTP_APP_PASSWORD:
        return {
            "status": "unavailable",
            "message": (
                "Password reset emails aren't configured on the server yet. "
                "Set SYSTEM_SMTP_EMAIL and SYSTEM_SMTP_APP_PASSWORD as environment variables."
            )
        }

    email = data.email.strip().lower()
    generic_message = "If an account exists for that email, a reset link has been sent."

    recruiter = db.query(Recruiter).filter(Recruiter.email == email).first()

    if not recruiter:
        return {"status": "success", "message": generic_message}

    token = auth_utils.generate_reset_token()

    recruiter.reset_token = token
    recruiter.reset_token_expires = datetime.now(timezone.utc) + timedelta(minutes=RESET_TOKEN_VALID_MINUTES)

    db.commit()

    base_url = (data.page_url or "").strip().rstrip("/")

    if base_url:
        reset_link = f"{base_url}?reset_token={token}"
    else:
        # No page_url provided -- still include the raw token so the
        # recruiter (or support) can construct the link manually.
        reset_link = f"(your login page)?reset_token={token}"

    email_body = (
        f"Hi {recruiter.name},\n\n"
        f"We received a request to reset your In SAI AI Recruiter password.\n\n"
        f"Click the link below to choose a new password. This link expires in "
        f"{RESET_TOKEN_VALID_MINUTES} minutes:\n\n"
        f"{reset_link}\n\n"
        f"If you didn't request this, you can safely ignore this email -- "
        f"your password will not be changed.\n\n"
        f"— In SAI AI Recruiter"
    )

    try:
        send_email_via_smtp(
            to_email=recruiter.email,
            subject="Reset your In SAI password",
            body=email_body,
            from_email=SYSTEM_SMTP_EMAIL,
            from_app_password=SYSTEM_SMTP_APP_PASSWORD,
            sender_name="In SAI AI Recruiter"
        )
    except Exception as error:
        print("PASSWORD RESET EMAIL ERROR:", repr(error))
        # Still return the generic success message -- don't reveal
        # whether the send failed due to a bad address vs a real error.

    return {"status": "success", "message": generic_message}


# =========================================================
# RESET PASSWORD -- consume the token, set a new password
# =========================================================

@app.post("/auth/reset-password")
def reset_password(
    data: ResetPasswordRequest,
    db: Session = Depends(get_db)
):

    token = (data.token or "").strip()

    if not token:
        return {"status": "error", "message": "Missing reset token."}

    if len(data.new_password) < 8:
        return {"status": "error", "message": "Password must be at least 8 characters."}

    recruiter = db.query(Recruiter).filter(Recruiter.reset_token == token).first()

    if not recruiter or not recruiter.reset_token_expires:
        return {"status": "error", "message": "This reset link is invalid. Please request a new one."}

    expires = recruiter.reset_token_expires
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)

    if expires < datetime.now(timezone.utc):
        return {"status": "error", "message": "This reset link has expired. Please request a new one."}

    recruiter.password_hash = auth_utils.hash_password(data.new_password)
    recruiter.reset_token = None
    recruiter.reset_token_expires = None
    recruiter.failed_login_attempts = 0
    recruiter.locked_until = None

    db.commit()

    return {"status": "success", "message": "Password updated. You can now sign in."}


# =========================================================
# CURRENT RECRUITER
# =========================================================

@app.get("/auth/me")
def get_me(recruiter: Recruiter = Depends(get_current_recruiter)):

    return {
        "status": "success",
        "recruiter": {
            "id": recruiter.id,
            "name": recruiter.name,
            "email": recruiter.email,
            "smtp_email": recruiter.smtp_email,
            "smtp_configured": bool(recruiter.smtp_email and recruiter.smtp_app_password_encrypted)
        }
    }


# =========================================================
# CONNECT / UPDATE THIS RECRUITER'S OWN SENDING EMAIL
# =========================================================
# The Gmail App Password is encrypted before being stored -- it is
# never saved or returned in plaintext.

@app.post("/auth/smtp")
def update_smtp_settings(
    data: RecruiterSMTPUpdate,
    recruiter: Recruiter = Depends(get_current_recruiter),
    db: Session = Depends(get_db)
):

    smtp_email = data.smtp_email.strip()
    smtp_app_password = data.smtp_app_password.strip().replace(" ", "")

    if not smtp_email or not smtp_app_password:
        return {"status": "error", "message": "Both the Gmail address and App Password are required."}

    try:
        encrypted = auth_utils.encrypt_secret(smtp_app_password)
    except RuntimeError as error:
        return {"status": "error", "message": str(error)}

    recruiter.smtp_email = smtp_email
    recruiter.smtp_app_password_encrypted = encrypted

    db.commit()

    return {
        "status": "success",
        "message": "Sending email connected.",
        "smtp_email": recruiter.smtp_email
    }


# =========================================================
# TEXT HELPER
# =========================================================

def clean_text(value):
    if not value:
        return ""

    return value.strip()


# =========================================================
# LOCATION TERMS
# =========================================================

def build_location_terms(
    request: RecruitmentRequest
):
    locations = []

    if request.city.strip():
        locations.append(
            f'"{request.city.strip()}"'
        )

    if request.state.strip():
        locations.append(
            f'"{request.state.strip()}"'
        )

    if request.region.strip():
        locations.append(
            f'"{request.region.strip()}"'
        )

    if request.location.strip():
        locations.append(
            f'"{request.location.strip()}"'
        )

    return " ".join(locations)


# =========================================================
# FRESHER DETECTION
# =========================================================

def check_fresher_profile(
    title: str,
    content: str
):

    combined_text = (
        f"{title} {content}"
    ).lower()

    positive_keywords = [
        "fresher",
        "fresh graduate",
        "recent graduate",
        "recent college graduate",
        "entry level",
        "entry-level",
        "graduate trainee",
        "trainee",
        "student",
        "undergraduate",
        "final year student",
        "final-year student",
        "recently graduated",
        "new graduate",
        "first job",
        "looking for first job",
        "seeking first job",
        "seeking opportunities",
        "looking for opportunities",
        "open to work",
        "open-to-work",
        "no experience",
        "zero experience",
        "0 years experience",
        "0 years of experience",
        "career starter",
        "early career"
    ]

    strong_experience_words = [
        "senior",
        "team lead",
        "manager",
        "director",
        "head of"
    ]

    positive_matches = [
        keyword
        for keyword in positive_keywords
        if keyword in combined_text
    ]

    # -----------------------------------------------------
    # NUMERIC EXPERIENCE DETECTION
    # -----------------------------------------------------

    numeric_experience = re.findall(
        r"(\d+)\+?\s*(?:years?|yrs?)"
        r"\s*(?:of\s*)?experience",
        combined_text
    )

    for value in numeric_experience:

        try:
            years = int(value)

            if years >= 1:
                return False, positive_matches

        except ValueError:
            pass

    # -----------------------------------------------------
    # STRONG EXPERIENCE WORDS
    # -----------------------------------------------------

    if any(
        word in combined_text
        for word in strong_experience_words
    ):
        return False, positive_matches

    # -----------------------------------------------------
    # FRESHER MATCH
    # -----------------------------------------------------

    if positive_matches:
        return True, positive_matches

    return False, []


# =========================================================
# LOCATION MATCHING
# =========================================================

def location_matches_profile(
    request: RecruitmentRequest,
    title: str,
    content: str
):

    combined_text = (
        f"{title} {content}"
    ).lower()

    requested_locations = []

    if request.city.strip():
        requested_locations.append(
            request.city.strip().lower()
        )

    if request.state.strip():
        requested_locations.append(
            request.state.strip().lower()
        )

    if request.region.strip():
        requested_locations.append(
            request.region.strip().lower()
        )

    if request.location.strip():
        requested_locations.append(
            request.location.strip().lower()
        )

    # No location requested
    if not requested_locations:
        return True

    for location in requested_locations:

        if location in combined_text:
            return True

    return False


# =========================================================
# BUILD SOURCE-SPECIFIC SEARCH QUERIES
# =========================================================

def build_source_queries(
    request: RecruitmentRequest,
    role: str,
    skills_text: str,
    location_text: str,
    experience_text: str,
    gender_text: str
):

    queries = []

    # =====================================================
    # COMMON SEARCH TERMS
    # =====================================================

    common_parts = [
        f'"{role}"'
    ]

    if skills_text:

        common_parts.append(
            skills_text
        )

    if location_text:

        common_parts.append(
            location_text
        )

    if experience_text:

        common_parts.append(
            experience_text
        )

    if gender_text:

        common_parts.append(
            gender_text
        )

    common_query = " ".join(
        common_parts
    )

    # =====================================================
    # LINKEDIN SEARCH
    # =====================================================

    if "linkedin" in request.sources:

        linkedin_query = (
            "site:linkedin.com/in/ "
            + common_query
        )

        queries.append({
            "source": "LinkedIn",
            "query": linkedin_query
        })

    # =====================================================
    # PUBLIC WEB SEARCH
    # =====================================================

    if "public_web" in request.sources:

        public_query = (
            common_query
            + " "
            + "("
            '"profile" OR '
            '"portfolio" OR '
            '"resume" OR '
            '"CV" OR '
            '"about me" OR '
            '"professional"'
            ") "
            "-site:linkedin.com/in/"
        )

        queries.append({
            "source": "Public Web",
            "query": public_query
        })

    return queries


# =========================================================
# RECRUITMENT SEARCH
# =========================================================

@app.post("/recruit")
def recruit(
    request: RecruitmentRequest
):

    role = request.role.strip()

    if not role:
        role = "professional"

    # =====================================================
    # SKILLS
    # =====================================================

    skills = [
        skill.strip()
        for skill in request.skills
        if skill.strip()
    ]

    skills_text = " ".join(
        f'"{skill}"'
        for skill in skills
    )

    # =====================================================
    # LOCATION
    # =====================================================

    location_text = build_location_terms(
        request
    )

    # =====================================================
    # EXPERIENCE
    # =====================================================

    requested_experience = (
        request.experience
        .strip()
        .lower()
    )

    experience_terms = []

    # =====================================================
    # FRESHER
    # =====================================================

    if requested_experience == "fresher":

        experience_terms = [
            '"fresher"',
            '"fresh graduate"',
            '"recent graduate"',
            '"recent college graduate"',
            '"entry level"',
            '"entry-level"',
            '"graduate trainee"',
            '"trainee"',
            '"student"',
            '"undergraduate"',
            '"final year student"',
            '"new graduate"',
            '"recently graduated"',
            '"first job"',
            '"looking for first job"',
            '"seeking first job"',
            '"no experience"',
            '"0 years experience"',
            '"open to work"',
            '"seeking opportunities"',
            '"looking for opportunities"'
        ]

    # =====================================================
    # 0-2 YEARS
    # =====================================================

    elif requested_experience == "0-2 years":

        experience_terms = [
            '"entry level"',
            '"entry-level"',
            '"0-2 years"',
            '"0 years experience"',
            '"1 year experience"',
            '"2 years experience"',
            '"junior"',
            '"graduate"',
            '"early career"',
            '"trainee"',
            '"recent graduate"',
            '"fresher"'
        ]

    # =====================================================
    # 2-5 YEARS
    # =====================================================

    elif requested_experience == "2-5 years":

        experience_terms = [
            '"2 years experience"',
            '"3 years experience"',
            '"4 years experience"',
            '"5 years experience"'
        ]

    # =====================================================
    # 5+ YEARS
    # =====================================================

    elif requested_experience == "5+ years":

        experience_terms = [
            '"5 years experience"',
            '"6 years experience"',
            '"7 years experience"',
            '"8 years experience"',
            '"senior"'
        ]

    # =====================================================
    # OTHER EXPERIENCE
    # =====================================================

    elif requested_experience:

        experience_terms = [
            f'"{request.experience.strip()}"'
        ]

    # =====================================================
    # EXPERIENCE TEXT
    # =====================================================

    experience_text = ""

    if experience_terms:

        experience_text = (
            "("
            + " OR ".join(
                experience_terms
            )
            + ")"
        )

    # =====================================================
    # GENDER
    # =====================================================

    gender_text = ""

    if request.gender.strip():

        gender_text = (
            f'"{request.gender.strip()}"'
        )

    # =====================================================
    # BUILD SOURCE QUERIES
    # =====================================================

    source_queries = build_source_queries(
        request=request,
        role=role,
        skills_text=skills_text,
        location_text=location_text,
        experience_text=experience_text,
        gender_text=gender_text
    )

    # =====================================================
    # NO SOURCE SELECTED
    # =====================================================

    if not source_queries:

        return {
            "status": "error",
            "message":
                "Please select at least one search source.",
            "results": []
        }

    # =====================================================
    # SEARCH SELECTED SOURCES
    # =====================================================
    all_results = []

    def _run_tavily_search(source_config):
        source_name = source_config["source"]
        search_query = source_config["query"]
        print("\n========================================")
        print("TAVILY SEARCH")
        print("SOURCE:", source_name)
        print("========================================")
        print(search_query)
        print("========================================")
        try:
            return source_config, tavily_client.search(
                query=search_query,
                max_results=30,
                search_depth="advanced",
                include_answer=False,
                include_raw_content=True
            )
        except Exception as error:
            print("Tavily search error:", repr(error))
            return source_config, {"results": []}

    search_results = []
    with ThreadPoolExecutor(max_workers=min(2, len(source_queries))) as executor:
        futures = [executor.submit(_run_tavily_search, config) for config in source_queries]
        for future in as_completed(futures):
            search_results.append(future.result())

    # Process both source result sets after the searches finish.
    for source_config, response in search_results:
        source_name = source_config["source"]

        # =================================================

        for result in response.get(
            "results",
            []
        ):

            title = result.get(
                "title",
                ""
            )

            url = result.get(
                "url",
                ""
            )

            content = result.get(
                "content",
                ""
            )

            # -------------------------------------------------
            # BASIC URL VALIDATION
            # -------------------------------------------------

            if not url:
                continue

            url_lower = url.lower()

            # -------------------------------------------------
            # LINKEDIN SOURCE
            # -------------------------------------------------

            if source_name == "LinkedIn":

                if "linkedin.com/in/" not in url_lower:
                    continue

            # -------------------------------------------------
            # PUBLIC WEB SOURCE
            # -------------------------------------------------

            elif source_name == "Public Web":

                # LinkedIn results are kept in the LinkedIn
                # source and are not duplicated here.

                if "linkedin.com/in/" in url_lower:
                    continue

                blocked_paths = (
                    "/reel/", "/posts/", "/jobs/", "/job/", "/blog/",
                    "/article/", "/articles/", "/news/", "/events/",
                    "/courses/", "/course/", "/company/", "/school/",
                    "/universities/", "/university/", "/feed/", "/search/"
                )
                if any(path in url_lower for path in blocked_paths):
                    continue

            # -------------------------------------------------
            # LOCATION FILTER
            # -------------------------------------------------

            if not location_matches_profile(
                request,
                title,
                content
            ):
                continue

            # -------------------------------------------------
            # FRESHER CHECK
            # -------------------------------------------------

            fresher_match, fresher_signals = (
                check_fresher_profile(
                    title,
                    content
                )
            )

            # -------------------------------------------------
            # STRICT FRESHER FILTER
            # -------------------------------------------------

            if requested_experience == "fresher":

                if not fresher_match:
                    continue

            # -------------------------------------------------
            # ADD RESULT
            # Email discovery is performed AFTER filtering and
            # de-duplication so we do not waste time inspecting
            # candidates that will never be returned.
            # -------------------------------------------------

            all_results.append({

                "title": title,

                "url": url,

                "content": content,

                "source": source_name,

                "location": (
                    request.city
                    or request.state
                    or request.region
                    or request.location
                ),

                "role": role,

                "skills": skills,

                "experience": request.experience,

                "fresher_match": fresher_match,

                "fresher_signals": fresher_signals,

                "email": None

            })

    # =====================================================
    # PRIORITIZE FRESHERS
    # =====================================================

    if requested_experience == "fresher":

        all_results.sort(
            key=lambda candidate:
            len(
                candidate.get(
                    "fresher_signals",
                    []
                )
            ),
            reverse=True
        )

    # =====================================================
    # PRIORITIZE LINKEDIN
    # =====================================================

    all_results.sort(
        key=lambda candidate:
        0
        if candidate.get("source") == "LinkedIn"
        else 1
    )

    # =====================================================
    # REMOVE DUPLICATES
    # =====================================================

    unique_results = []

    seen_urls = set()

    for result in all_results:

        url = result.get(
            "url",
            ""
        )

        if not url:
            continue

        normalized_url = (
            url
            .split("?")[0]
            .rstrip("/")
            .lower()
        )

        if normalized_url in seen_urls:
            continue

        seen_urls.add(
            normalized_url
        )

        unique_results.append(
            result
        )

    # =====================================================
    # LIMIT RESULTS
    # =====================================================

    unique_results = unique_results[:20]

    # =====================================================
    # PUBLIC EMAIL EXTRACTION
    # =====================================================
    # IMPORTANT: Do not reduce discovery because an email is missing.
    # Every returned profile is kept. If Tavily exposes a public email in
    # title/content/raw_content, keep that original email; otherwise None.
    # No email guessing and no extra per-candidate search calls.
    for result in unique_results:
        searchable_text = "\n".join(
            value
            for value in [
                result.get("title", ""),
                result.get("content", ""),
                result.get("raw_content", "")
            ]
            if value
        )

        result["email"] = extract_candidate_email_from_text(
            searchable_text,
            result.get("title", "")
        )

        username = _github_username_from_candidate(result)
        result["github"] = {
            "available": bool(username),
            "username": username,
            "profile_url": (
                f"https://github.com/{username}"
                if username
                else None
            )
        }

        print(
            "EMAIL DISCOVERY:",
            result.get("title", ""),
            "->",
            result.get("email") or "Email Not Available"
        )

    # Do NOT de-duplicate email addresses.
    # Ten returned profiles must be allowed to show ten public email IDs,
    # including the original value found for each profile.

    # =====================================================
    # SOURCE COUNTS
    # =====================================================

    linkedin_count = sum(
        1
        for result in unique_results
        if result.get("source") == "LinkedIn"
    )

    public_web_count = sum(
        1
        for result in unique_results
        if result.get("source") == "Public Web"
    )

    # =====================================================
    # RESPONSE
    # =====================================================

    return {
        "status": "success",

        "requirements": {
            "role": request.role,
            "skills": request.skills,
            "location": request.location,
            "region": request.region,
            "state": request.state,
            "city": request.city,
            "experience": request.experience,
            "gender": request.gender
        },

        "sources": request.sources,

        "source_counts": {
            "linkedin": linkedin_count,
            "public_web": public_web_count,
            "total": len(unique_results)
        },

        "result_count": len(
            unique_results
        ),

        "results": unique_results
    }


# =========================================================
# GITHUB INSIGHTS / ON-DEMAND SEARCH
# =========================================================

@app.get("/github/insights/{username}")
def get_github_insights(username: str):
    """Return public GitHub profile and recent public repository insights."""
    username = (username or "").strip().lstrip("@").strip("/")
    if not username or not GITHUB_TOKEN:
        return {"status": "unavailable", "message": "GitHub is not configured."}

    if not re.fullmatch(r"[A-Za-z0-9-]{1,39}", username):
        return {"status": "not_found", "message": "Invalid GitHub username."}

    insights = _github_insights({"url": f"https://github.com/{username}"})
    if not insights.get("available"):
        return {"status": "not_found", "message": "GitHub profile not found."}

    return {"status": "success", "insights": insights}


@app.post("/github/search")
def search_github_profiles(request: GitHubSearchRequest):
    """Find possible public GitHub profiles on demand; never auto-attaches a match."""
    name = request.name.strip()
    if not name:
        return {"status": "error", "message": "Candidate name is required.", "results": []}
    if not GITHUB_TOKEN:
        return {"status": "unavailable", "message": "GitHub is not configured.", "results": []}

    query = f'"{name}" in:name'
    search = _github_api_get(
        f"/search/users?q={urllib.parse.quote(query)}&per_page=5",
        timeout=3.0
    )
    if not isinstance(search, dict):
        return {"status": "error", "message": "GitHub search could not be completed.", "results": []}

    results = []
    for item in (search.get("items") or [])[:5]:
        if not isinstance(item, dict):
            continue
        login = item.get("login")
        if not login:
            continue
        profile = _github_api_get(
            f"/users/{urllib.parse.quote(login, safe='')}",
            timeout=2.0
        )
        if not isinstance(profile, dict) or profile.get("message") == "Not Found":
            continue

        results.append({
            "username": profile.get("login") or login,
            "profile_url": profile.get("html_url") or f"https://github.com/{login}",
            "name": profile.get("name") or login,
            "bio": profile.get("bio"),
            "company": profile.get("company"),
            "location": profile.get("location"),
            "website": profile.get("blog"),
            "avatar_url": profile.get("avatar_url"),
            "public_repos": profile.get("public_repos") or 0,
            "followers": profile.get("followers") or 0
        })

    return {
        "status": "success",
        "message": "Possible GitHub profiles found. Verify the correct person before using insights.",
        "results": results
    }


def _score_github_profile(profile, request):
    """Rough confidence score for whether a GitHub user is the candidate."""
    score = 0

    profile_location = (profile.get("location") or "").lower()
    profile_company = (profile.get("company") or "").lower()
    profile_name = (profile.get("name") or "").lower()

    if request.location and request.location.strip().lower() in profile_location:
        score += 2

    if request.company and request.company.strip().lower() in profile_company:
        score += 2

    candidate_name = request.name.strip().lower()
    if candidate_name and profile_name and candidate_name in profile_name:
        score += 1

    return score


@app.post("/github/lookup")
def github_lookup(request: GitHubLookupRequest):
    """
    Used by the 'GitHub Insights' button. Tries, in order:
      1. A GitHub username already visible in the candidate's known links/text.
      2. A GitHub username search by name, narrowed by location/company
         when available, auto-confirming only a clear single best match.
    Never invents a profile -- returns 'not_found' when nothing reliable exists.
    """

    if not GITHUB_TOKEN:
        return {
            "status": "unavailable",
            "message": "GitHub is not configured."
        }

    name = request.name.strip()

    if not name:
        return {
            "status": "not_found",
            "message": "No candidate name available to search GitHub with."
        }

    # -----------------------------------------------------
    # 1) A GitHub link already present on the candidate
    # -----------------------------------------------------

    direct_hint = {
        "url": request.linkedin_url or "",
        "content": " ".join(
            filter(None, [request.email, request.company, request.current_role])
        )
    }

    direct_username = _github_username_from_candidate(direct_hint)

    if direct_username:
        insights = _github_insights({"url": f"https://github.com/{direct_username}"})
        if insights.get("available"):
            return {"status": "found", "profile": insights}

    # -----------------------------------------------------
    # 2) Search GitHub users by name
    # -----------------------------------------------------

    query = f'"{name}" in:name'
    if request.location and request.location.strip():
        query += f' location:"{request.location.strip()}"'

    search = _github_api_get(
        f"/search/users?q={urllib.parse.quote(query)}&per_page=8",
        timeout=3.0
    )

    items = (search.get("items") if isinstance(search, dict) else None) or []

    # Retry without the location narrowing if it returned nothing.
    if not items and request.location:
        fallback_search = _github_api_get(
            f"/search/users?q={urllib.parse.quote(f'\"{name}\" in:name')}&per_page=8",
            timeout=3.0
        )
        items = (fallback_search.get("items") if isinstance(fallback_search, dict) else None) or []

    if not items:
        return {
            "status": "not_found",
            "message": "No public GitHub profile could be matched to this candidate."
        }

    profiles = []
    for item in items[:8]:
        login = item.get("login") if isinstance(item, dict) else None
        if not login:
            continue
        profile = _github_api_get(f"/users/{urllib.parse.quote(login, safe='')}", timeout=2.0)
        if isinstance(profile, dict) and profile.get("message") != "Not Found":
            profiles.append(profile)

    if not profiles:
        return {
            "status": "not_found",
            "message": "No public GitHub profile could be matched to this candidate."
        }

    scored = sorted(profiles, key=lambda p: _score_github_profile(p, request), reverse=True)
    top_score = _score_github_profile(scored[0], request)
    runner_up_score = _score_github_profile(scored[1], request) if len(scored) > 1 else -1

    if top_score >= 2 and top_score > runner_up_score:
        insights = _github_insights({
            "url": scored[0].get("html_url") or f"https://github.com/{scored[0].get('login')}"
        })
        if insights.get("available"):
            return {"status": "found", "profile": insights}

    matches = [
        {
            "username": profile.get("login"),
            "profile_url": profile.get("html_url") or f"https://github.com/{profile.get('login')}",
            "avatar_url": profile.get("avatar_url"),
            "name": profile.get("name") or profile.get("login"),
            "bio": profile.get("bio"),
            "company": profile.get("company"),
            "location": profile.get("location"),
            "website": profile.get("blog"),
            "public_repos": profile.get("public_repos") or 0,
            "followers": profile.get("followers") or 0
        }
        for profile in scored[:5]
    ]

    return {
        "status": "possible_matches",
        "message": "Multiple possible GitHub profiles found. Verify before using insights.",
        "matches": matches
    }


# =========================================================
# ADD CANDIDATE TO TALENT POOL
# =========================================================

@app.post("/candidates")
def add_candidate(
    candidate: CandidateCreate,
    db: Session = Depends(get_db)
):

    # =====================================================
    # CHECK LINKEDIN DUPLICATE
    # =====================================================

    if candidate.linkedin_url:

        existing = (
            db.query(Candidate)
            .filter(
                Candidate.linkedin_url
                == candidate.linkedin_url
            )
            .first()
        )

        if existing:

            return {
                "status": "exists",
                "message":
                    "Candidate already exists in Talent Pool",
                "candidate_id":
                    existing.id
            }

    # =====================================================
    # CHECK EMAIL DUPLICATE
    # =====================================================

    if candidate.email:

        existing_email = (
            db.query(Candidate)
            .filter(
                Candidate.email
                == candidate.email
            )
            .first()
        )

        if existing_email:

            return {
                "status": "exists",
                "message":
                    "A candidate with this email already exists",
                "candidate_id":
                    existing_email.id
            }

    # =====================================================
    # CREATE CANDIDATE
    # =====================================================

    new_candidate = Candidate(
        name=candidate.name,
        email=candidate.email,
        linkedin_url=candidate.linkedin_url,
        current_role=candidate.current_role,
        company=candidate.company,
        skills=candidate.skills,
        experience=candidate.experience,
        region=candidate.region,
        state=candidate.state,
        city=candidate.city,
        location=candidate.location,
        gender=candidate.gender,
        finance_category=candidate.finance_category,
        finance_subcategory=
            candidate.finance_subcategory,
        status=candidate.status or "New"
    )

    try:

        db.add(
            new_candidate
        )

        db.commit()

        db.refresh(
            new_candidate
        )

    except IntegrityError:

        db.rollback()

        return {
            "status": "error",
            "message":
                "This candidate could not be added because of a database constraint."
        }

    return {
        "status": "success",
        "message":
            "Candidate added to Talent Pool",
        "candidate_id":
            new_candidate.id
    }


# =========================================================
# GET ALL TALENT POOL CANDIDATES
# =========================================================

@app.get("/candidates")
def get_candidates(
    region: str = None,
    state: str = None,
    city: str = None,
    role: str = None,
    experience: str = None,
    gender: str = None,
    finance_category: str = None,
    finance_subcategory: str = None,
    db: Session = Depends(get_db)
):

    query = db.query(
        Candidate
    )

    # -----------------------------------------------------
    # REGION
    # -----------------------------------------------------

    if region:

        query = query.filter(
            Candidate.region.ilike(
                f"%{region}%"
            )
        )

    # -----------------------------------------------------
    # STATE
    # -----------------------------------------------------

    if state:

        query = query.filter(
            Candidate.state.ilike(
                f"%{state}%"
            )
        )

    # -----------------------------------------------------
    # CITY
    # -----------------------------------------------------

    if city:

        query = query.filter(
            Candidate.city.ilike(
                f"%{city}%"
            )
        )

    # -----------------------------------------------------
    # ROLE
    # -----------------------------------------------------

    if role:

        query = query.filter(
            Candidate.current_role.ilike(
                f"%{role}%"
            )
        )

    # -----------------------------------------------------
    # EXPERIENCE
    # -----------------------------------------------------

    if experience:

        query = query.filter(
            Candidate.experience.ilike(
                f"%{experience}%"
            )
        )

    # -----------------------------------------------------
    # GENDER
    # -----------------------------------------------------

    if gender:

        query = query.filter(
            Candidate.gender.ilike(
                f"%{gender}%"
            )
        )

    # -----------------------------------------------------
    # FINANCE CATEGORY
    # -----------------------------------------------------

    if finance_category:

        query = query.filter(
            Candidate.finance_category.ilike(
                f"%{finance_category}%"
            )
        )

    # -----------------------------------------------------
    # FINANCE SPECIALIZATION
    # -----------------------------------------------------

    if finance_subcategory:

        query = query.filter(
            Candidate.finance_subcategory.ilike(
                f"%{finance_subcategory}%"
            )
        )

    # -----------------------------------------------------
    # LATEST FIRST
    # -----------------------------------------------------

    candidates = (
        query
        .order_by(
            Candidate.id.desc()
        )
        .all()
    )

    return candidates


# =========================================================
# GET SINGLE CANDIDATE
# =========================================================

@app.get("/candidates/{candidate_id}")
def get_candidate(
    candidate_id: int,
    db: Session = Depends(get_db)
):

    candidate = (
        db.query(Candidate)
        .filter(
            Candidate.id == candidate_id
        )
        .first()
    )

    if not candidate:

        return {
            "status": "error",
            "message":
                "Candidate not found"
        }

    return candidate


# =========================================================
# UPDATE CANDIDATE STATUS
# =========================================================

@app.patch(
    "/candidates/{candidate_id}/status"
)
def update_candidate_status(
    candidate_id: int,
    data: CandidateStatusUpdate,
    db: Session = Depends(get_db)
):

    candidate = (
        db.query(Candidate)
        .filter(
            Candidate.id == candidate_id
        )
        .first()
    )

    if not candidate:

        return {
            "status": "error",
            "message":
                "Candidate not found"
        }

    candidate.status = data.status

    db.commit()

    db.refresh(
        candidate
    )

    return {
        "status": "success",
        "message":
            "Candidate status updated",
        "candidate":
            candidate
    }


# =========================================================
# DELETE CANDIDATE
# =========================================================

@app.delete(
    "/candidates/{candidate_id}"
)
def delete_candidate(
    candidate_id: int,
    db: Session = Depends(get_db)
):

    candidate = (
        db.query(Candidate)
        .filter(
            Candidate.id == candidate_id
        )
        .first()
    )

    if not candidate:

        return {
            "status": "error",
            "message":
                "Candidate not found"
        }

    db.delete(
        candidate
    )

    db.commit()

    return {
        "status": "success",
        "message":
            "Candidate deleted successfully",
        "candidate_id":
            candidate_id
    }


# =========================================================
# AI EMAIL GENERATION - GEMINI 3.6 FLASH
# =========================================================


def _clean_outreach_value(value):
    """Normalize a value before putting it into the recruiter prompt."""
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _get_target_role_for_email(request, candidate):
    """
    Determine the position being recruited for.

    Priority is deliberately given to the recruiter's search context,
    because the outreach position must follow what the recruiter searched,
    not automatically follow the candidate's current job title.
    """
    search_context = (
        request.search_context
        if isinstance(request.search_context, dict)
        else {}
    )

    candidates = [
        request.requested_role,
        search_context.get("requestedRole"),
        search_context.get("requested_role"),
        request.search_role,
        search_context.get("role"),
        request.search_query,
        search_context.get("query"),
        request.search_skill,
        search_context.get("skill"),
        getattr(candidate, "current_role", None),
        "the relevant position",
    ]

    for value in candidates:
        clean = _clean_outreach_value(value)
        if clean:
            return clean

    return "the relevant position"


def _skills_for_email(skills):
    """Convert stored candidate skills into a clean list."""
    if isinstance(skills, list):
        values = skills
    else:
        values = str(skills or "").split(",")

    return [
        _clean_outreach_value(value)
        for value in values
        if _clean_outreach_value(value)
    ]


@app.post("/generate-email")
def generate_email(
    request: EmailGenerationRequest,
    db: Session = Depends(get_db)
):

    # =====================================================
    # FIND CANDIDATE
    # =====================================================

    candidate = (
        db.query(Candidate)
        .filter(
            Candidate.id == request.candidate_id
        )
        .first()
    )

    if not candidate:
        raise HTTPException(
            status_code=404,
            detail="Candidate not found"
        )

    # =====================================================
    # CANDIDATE INFORMATION
    # =====================================================

    name = _clean_outreach_value(
        candidate.name
    ) or "Candidate"

    current_role = _clean_outreach_value(
        candidate.current_role
    )

    company = _clean_outreach_value(
        candidate.company
    )

    skills_list = _skills_for_email(
        candidate.skills
    )

    skills = ", ".join(skills_list)

    experience = _clean_outreach_value(
        candidate.experience
    )

    location = _clean_outreach_value(
        candidate.city
        or candidate.state
        or candidate.region
        or candidate.location
    )

    email_type = _clean_outreach_value(
        request.email_type
    ) or "initial_outreach"

    tone = _clean_outreach_value(
        request.tone
    ) or "professional"

    # =====================================================
    # SEARCH / TARGET POSITION
    # =====================================================

    target_role = _get_target_role_for_email(
        request,
        candidate
    )

    search_context = (
        request.search_context
        if isinstance(request.search_context, dict)
        else {}
    )

    search_query = _clean_outreach_value(
        request.search_query
        or search_context.get("query")
    )

    search_role = _clean_outreach_value(
        request.search_role
        or search_context.get("role")
    )

    search_skill = _clean_outreach_value(
        request.search_skill
        or search_context.get("skill")
    )

    # =====================================================
    # PROFILE INFORMATION FOR GEMINI
    # =====================================================

    profile_information = f"""
TARGET POSITION / SEARCH:
{target_role}

Original Search Query: {search_query or "Not provided"}
Search Role: {search_role or "Not provided"}
Search Skill: {search_skill or "Not provided"}

CANDIDATE:
Candidate Name: {name}
Current Role: {current_role or "Not provided"}
Current Company: {company or "Not provided"}
Verified Skills: {skills or "Not provided"}
Experience: {experience or "Not provided"}
Location: {location or "Not provided"}
Email Type: {email_type}
Tone: {tone}
"""

    # =====================================================
    # PERSONALIZED GEMINI PROMPT
    # =====================================================

    prompt = f"""
You are a professional corporate recruiter working for In SAI AI Recruiter.

Create ONE highly personalized recruitment email for the specific candidate below.

{profile_information}

CRITICAL TARGET-POSITION RULE:

The recruiter searched for the position/category "{target_role}".
That searched position is the POSITION WE ARE RECRUITING FOR.

The email MUST be written as outreach for the "{target_role}" position.
Do NOT silently replace the target position with the candidate's current role.

For example:
- If the recruiter searched "Software Developer", write the outreach for a Software Developer position.
- If the recruiter searched "Data Scientist", write the outreach for a Data Scientist position.
- If the recruiter searched "React Developer", write the outreach for a React Developer position.

CANDIDATE-SKILL PERSONALIZATION RULE:

Use the candidate's actual verified skills when they are relevant to the searched position.
Mention one or two relevant skills naturally as the reason the candidate's profile caught our attention.
Only use skills that appear in the Verified Skills field.
NEVER invent or assume a skill.

Example style for a Software Developer search:
"Your experience with Python and JavaScript caught our attention, and we believe your background may be relevant to a Software Developer opportunity."

That sentence is only an example of style. Use the candidate's REAL skills.

IMPORTANT PERSONALIZATION RULES:

1. The target position "{target_role}" MUST appear explicitly in the email body.

2. The target position "{target_role}" MUST appear in the subject.

3. The subject should clearly sound like a recruitment message for that specific position.

4. The email should explain that we are looking for candidates for the "{target_role}" position.

5. Use the candidate's actual skills to explain why their profile caught our attention when those skills are relevant.

6. Use the candidate's current role, company, experience or location only when those details are available and useful.

7. Do not invent education, qualifications, skills, experience, achievements, projects, companies, salary, responsibilities or job titles.

8. Do not say the candidate applied for this position.

9. Do not exaggerate the candidate's background.

10. Address the candidate naturally by name.

11. Keep the email professional, natural, recruiter-like and concise.

12. Ask whether the candidate would be open to a brief conversation.

13. Do not repeatedly use vague subjects such as "Career Opportunity" or "Job Opportunity" without the searched position.

14. The subject must contain the searched target position "{target_role}".

15. If relevant candidate skills are available, the subject MAY mention one verified skill, but it must still clearly contain "{target_role}".

16. Do not claim that a skill matches the position unless the candidate actually has that skill.

17. If there are no obvious matching skills, simply mention that the candidate's broader background caught our attention.

EMAIL TYPE:
Follow the selected email type: {email_type}

TONE:
Follow the selected tone: {tone}

SUBJECT:
Create a natural, professional subject specifically for the searched position.
It should contain "{target_role}".
Examples of acceptable structure:
- "{target_role} Position - Your Skills Caught Our Attention"
- "{target_role} Opportunity - Your Background Looks Relevant"
These are examples only. Create the best natural subject from the candidate's actual information.

BODY:
The body must:
- Address {name}
- Clearly mention the {target_role} position
- Explain that we are reaching out about that position
- Mention one or two actual relevant candidate skills when possible
- Explain why the candidate's profile caught our attention
- Avoid fabricated claims
- Ask for a brief conversation

The email must end exactly with:

Best regards,

Ganesh

In SAI AI Recruiter

OUTPUT:
Return ONLY valid JSON.
Do not use Markdown.
Do not use code fences.
Do not add explanations.

Return exactly:
{{
    "subject": "position-specific subject containing the searched position",
    "body": "personalized recruiter email containing the searched position and relevant verified skills"
}}
"""

    # =====================================================
    # GEMINI GENERATION
    # =====================================================

    try:
        print("")
        print("========================================")
        print("GEMINI EMAIL GENERATION")
        print("========================================")
        print("Candidate ID:", candidate.id)
        print("Candidate:", name)
        print("Current Role:", current_role)
        print("Target Position:", target_role)
        print("Search Query:", search_query)
        print("Search Role:", search_role)
        print("Search Skill:", search_skill)
        print("Skills:", skills)
        print("Experience:", experience)
        print("Email Type:", email_type)
        print("Tone:", tone)
        print("Model: gemini-3.6-flash")
        print("========================================")

        response = gemini_client.models.generate_content(
            model="gemini-3.6-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                thinking_config=types.ThinkingConfig(
                    thinking_level="minimal"
                ),
                max_output_tokens=500
            )
        )

        generated_text = ""

        if response:
            generated_text = (
                response.text
                or ""
            )

        generated_text = generated_text.strip()

        if not generated_text:
            raise Exception(
                "Gemini returned an empty response."
            )

        print("")
        print("========================================")
        print("RAW GEMINI RESPONSE")
        print("========================================")
        print(generated_text)
        print("========================================")

        # =================================================
        # REMOVE MARKDOWN CODE FENCES
        # =================================================

        generated_text = re.sub(
            r"^\s*```json\s*",
            "",
            generated_text,
            flags=re.IGNORECASE
        )

        generated_text = re.sub(
            r"^\s*```\s*",
            "",
            generated_text
        )

        generated_text = re.sub(
            r"\s*```\s*$",
            "",
            generated_text
        )

        generated_text = generated_text.strip()

        # =================================================
        # PARSE JSON
        # =================================================

        try:
            email_data = json.loads(
                generated_text
            )
        except json.JSONDecodeError:
            print(
                "Gemini returned invalid JSON."
            )
            raise Exception(
                "Gemini did not return valid JSON."
            )

        subject = _clean_outreach_value(
            email_data.get("subject", "")
        )

        body = str(
            email_data.get("body", "")
        ).strip()

        if not subject:
            raise Exception(
                "Gemini generated an empty subject."
            )

        if not body:
            raise Exception(
                "Gemini generated an empty email body."
            )

        # =================================================
        # GUARANTEE TARGET POSITION IN SUBJECT
        # =================================================

        if target_role.lower() not in subject.lower():
            subject = (
                f"{target_role} Position - "
                f"Your Background Caught Our Attention"
            )

        # =================================================
        # GUARANTEE TARGET POSITION IN BODY
        # =================================================

        if target_role.lower() not in body.lower():
            body = (
                f"Hi {name},\n\n"
                f"I am reaching out regarding a potential {target_role} position. "
                f"Your background caught our attention.\n\n"
                + body
            )

        # =================================================
        # NORMALIZE RECRUITER BRANDING
        # =================================================

        body = re.sub(
            r"TalentReach AI Recruiter",
            "In SAI AI Recruiter",
            body,
            flags=re.IGNORECASE
        )

        body = re.sub(
            r"TalentReach",
            "In SAI AI Recruiter",
            body,
            flags=re.IGNORECASE
        )

        # =================================================
        # GUARANTEE SIGNATURE
        # =================================================

        if "in sai ai recruiter" not in body.lower():
            body = (
                body.rstrip()
                + "\n\nBest regards,\n\nGanesh\n\nIn SAI AI Recruiter"
            )

        # =================================================
        # SUCCESS
        # =====================================================

        print("")
        print("========================================")
        print("EMAIL GENERATED SUCCESSFULLY")
        print("========================================")
        print("TARGET POSITION:")
        print(target_role)
        print("")
        print("SUBJECT:")
        print(subject)
        print("")
        print("BODY:")
        print(body)
        print("========================================")

        return {
            "status": "success",
            "subject": subject,
            "body": body,
            "email": body,
            "target_role": target_role,
            "search_context": {
                "query": search_query,
                "role": search_role,
                "skill": search_skill,
                "requestedRole": target_role,
            },
            "candidate": {
                "id": candidate.id,
                "name": name,
                "email": candidate.email,
                "current_role": current_role,
                "role": current_role or target_role,
                "company": company,
                "skills": skills,
                "experience": experience,
                "location": location,
            }
        }

    # =====================================================
    # ERROR HANDLING
    # =====================================================

    except Exception as error:
        print("")
        print("========================================")
        print("GEMINI EMAIL GENERATION ERROR")
        print("========================================")
        print(
            "Error Type:",
            type(error).__name__
        )
        print(
            "Error:",
            repr(error)
        )
        print("========================================")

        raise HTTPException(
            status_code=500,
            detail=(
                "AI email generation failed. "
                "Check the terminal for the exact Gemini error."
            )
        )


# =========================================================
# BULK EMAIL SENDING
# =========================================================
# Sends real emails via Gmail SMTP to a list of Talent Pool candidates.
# For each candidate:
#   1. Skips it if do_not_contact is set, or it has no email on file.
#   2. Generates a personalized subject/body using the exact same
#      Gemini logic as /generate-email (called directly -- no
#      duplicated prompt).
#   3. Sends it via SMTP.
#   4. Updates last_contacted / emails_sent on success.
# Returns a per-candidate result list so the UI can show exactly what
# happened for each person, even when some succeed and some fail.

@app.post("/send-bulk-emails")
def send_bulk_emails(
    request: BulkEmailRequest,
    recruiter: Recruiter = Depends(get_current_recruiter),
    db: Session = Depends(get_db)
):

    if not recruiter.smtp_email or not recruiter.smtp_app_password_encrypted:
        return {
            "status": "unavailable",
            "message": (
                "You haven't connected a sending email yet. "
                "Go to Email Settings and add your Gmail address and App Password."
            ),
            "results": []
        }

    try:
        sender_app_password = auth_utils.decrypt_secret(recruiter.smtp_app_password_encrypted)
    except RuntimeError as error:
        return {
            "status": "unavailable",
            "message": str(error),
            "results": []
        }

    sender_email = recruiter.smtp_email
    sender_name = recruiter.name

    candidate_ids = [
        cid for cid in dict.fromkeys(request.candidate_ids)
        if isinstance(cid, int)
    ]

    if not candidate_ids:
        return {
            "status": "error",
            "message": "No candidate IDs were provided.",
            "results": []
        }

    results = []
    sent_count = 0
    skipped_count = 0
    failed_count = 0

    print("")
    print("========================================")
    print("BULK EMAIL SEND STARTED")
    print("Candidate count:", len(candidate_ids))
    print("========================================")

    for candidate_id in candidate_ids:

        candidate = (
            db.query(Candidate)
            .filter(Candidate.id == candidate_id)
            .first()
        )

        if not candidate:
            results.append({
                "candidate_id": candidate_id,
                "status": "failed",
                "message": "Candidate not found."
            })
            failed_count += 1
            continue

        candidate_name = candidate.name or "Candidate"

        # -----------------------------------------------
        # SKIP: do not contact
        # -----------------------------------------------

        if getattr(candidate, "do_not_contact", False):
            results.append({
                "candidate_id": candidate_id,
                "candidate_name": candidate_name,
                "status": "skipped",
                "message": "Candidate is marked Do Not Contact."
            })
            skipped_count += 1
            continue

        # -----------------------------------------------
        # SKIP: no email on file
        # -----------------------------------------------

        if not candidate.email:
            results.append({
                "candidate_id": candidate_id,
                "candidate_name": candidate_name,
                "status": "skipped",
                "message": "No email address on file for this candidate."
            })
            skipped_count += 1
            continue

        # -----------------------------------------------
        # GENERATE the email (reuses /generate-email logic directly)
        # -----------------------------------------------

        try:
            generation = generate_email(
                EmailGenerationRequest(
                    candidate_id=candidate_id,
                    email_type=request.email_type,
                    tone=request.tone,
                    requested_role=request.requested_role,
                    search_query=request.search_query,
                    search_role=request.search_role,
                    search_skill=request.search_skill,
                    search_context=request.search_context,
                ),
                db
            )

        except HTTPException as error:
            results.append({
                "candidate_id": candidate_id,
                "candidate_name": candidate_name,
                "status": "failed",
                "message": f"Email generation failed: {error.detail}"
            })
            failed_count += 1
            continue

        except Exception as error:
            results.append({
                "candidate_id": candidate_id,
                "candidate_name": candidate_name,
                "status": "failed",
                "message": f"Email generation failed: {error}"
            })
            failed_count += 1
            continue

        subject = generation.get("subject", "")
        body = generation.get("body", "")

        # -----------------------------------------------
        # SEND via SMTP
        # -----------------------------------------------

        try:
            send_email_via_smtp(
                to_email=candidate.email,
                subject=subject,
                body=body,
                from_email=sender_email,
                from_app_password=sender_app_password,
                sender_name=sender_name
            )

        except Exception as error:
            print("SMTP SEND ERROR for candidate", candidate_id, ":", repr(error))

            results.append({
                "candidate_id": candidate_id,
                "candidate_name": candidate_name,
                "status": "failed",
                "message": f"Sending failed: {error}"
            })
            failed_count += 1
            continue

        # -----------------------------------------------
        # SUCCESS -- update tracking fields
        # -----------------------------------------------

        try:
            candidate.last_contacted = datetime.now(timezone.utc)
            candidate.emails_sent = (candidate.emails_sent or 0) + 1
            db.commit()
        except Exception as error:
            print("TRACKING UPDATE ERROR for candidate", candidate_id, ":", repr(error))
            db.rollback()

        results.append({
            "candidate_id": candidate_id,
            "candidate_name": candidate_name,
            "email": candidate.email,
            "status": "sent",
            "subject": subject
        })
        sent_count += 1

        # Small delay between sends to stay well under Gmail's
        # per-second/per-minute sending limits during a batch.
        time.sleep(1.2)

    print("")
    print("========================================")
    print("BULK EMAIL SEND COMPLETE")
    print("Sent:", sent_count, "| Skipped:", skipped_count, "| Failed:", failed_count)
    print("========================================")

    return {
        "status": "success",
        "sent_count": sent_count,
        "skipped_count": skipped_count,
        "failed_count": failed_count,
        "results": results
    }


# =========================================================
# UPDATE CANDIDATE NOTES / DO-NOT-CONTACT
# =========================================================

@app.patch("/candidates/{candidate_id}/notes")
def update_candidate_notes(
    candidate_id: int,
    data: CandidateNotesUpdate,
    db: Session = Depends(get_db)
):

    candidate = (
        db.query(Candidate)
        .filter(Candidate.id == candidate_id)
        .first()
    )

    if not candidate:
        return {
            "status": "error",
            "message": "Candidate not found"
        }

    if data.notes is not None:
        candidate.notes = data.notes

    if data.do_not_contact is not None:
        candidate.do_not_contact = data.do_not_contact

    db.commit()
    db.refresh(candidate)

    return {
        "status": "success",
        "message": "Candidate updated",
        "candidate": candidate
    }

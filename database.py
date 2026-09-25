import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

BASE_DIR = Path(__file__).resolve().parent

DATABASE_URL = os.getenv("DATABASE_URL")

if DATABASE_URL:
    # Production (Render): use Postgres from the DATABASE_URL environment variable.
    #
    # Render's connection string can arrive in several forms depending on
    # the account/plan -- "postgres://...", "postgresql://...", or already
    # "postgresql+psycopg://..." (explicitly requesting the newer psycopg v3
    # driver). Only psycopg2-binary is installed (see requirements.txt), so
    # ANY of those needs to be normalized to "postgresql+psycopg2://" or the
    # app crashes on startup with "ModuleNotFoundError: No module named 'psycopg'".
    #
    # Splitting on "://" and replacing the scheme entirely (rather than
    # string-replacing specific prefixes) makes this correct no matter which
    # of the above forms Render actually sends.
    if "://" in DATABASE_URL:
        _, _, rest = DATABASE_URL.partition("://")
        DATABASE_URL = f"postgresql+psycopg2://{rest}"

    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
else:
    # Local development: fall back to the SQLite file.
    DATABASE_URL = f"sqlite:///{BASE_DIR / 'talentreach.db'}"
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False},
    )

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)

Base = declarative_base()

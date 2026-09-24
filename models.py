from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class Candidate(Base):
    __tablename__ = "candidates"

    # =========================
    # Basic Candidate Details
    # =========================

    id = Column(Integer, primary_key=True, index=True)

    name = Column(
        String(150),
        nullable=False,
        index=True
    )

    email = Column(
        String(150),
        nullable=True,
        unique=True,
        index=True
    )

    linkedin_url = Column(
        String(500),
        nullable=True
    )

    # =========================
    # Professional Details
    # =========================

    current_role = Column(
        String(200),
        nullable=True,
        index=True
    )

    company = Column(
        String(200),
        nullable=True
    )

    skills = Column(
        Text,
        nullable=True
    )

    experience = Column(
        String(100),
        nullable=True
    )

    # =========================
    # Location Details
    # =========================

    # Example:
    # South India
    # North India
    # West India
    # East India

    region = Column(
        String(100),
        nullable=True,
        index=True
    )

    # Example:
    # Telangana
    # Andhra Pradesh
    # Karnataka

    state = Column(
        String(100),
        nullable=True,
        index=True
    )

    # Example:
    # Hyderabad
    # Vijayawada
    # Bengaluru

    city = Column(
        String(100),
        nullable=True,
        index=True
    )

    # Complete location
    # Example:
    # Hyderabad, Telangana, India

    location = Column(
        String(300),
        nullable=True,
        index=True
    )

    # =========================
    # Gender
    # =========================

    gender = Column(
        String(30),
        nullable=True,
        index=True
    )

    # Possible values:
    # Male
    # Female
    # Other
    # Not Specified

    # =========================
    # Finance Category
    # =========================

    finance_category = Column(
        String(150),
        nullable=True,
        index=True
    )

    # Examples:
    # Accounting
    # Finance
    # Banking
    # Investment
    # Taxation
    # Audit
    # Insurance

    # =========================
    # Finance Subcategory
    # =========================

    finance_subcategory = Column(
        String(200),
        nullable=True,
        index=True
    )

    # Examples:
    # Financial Accounting
    # Management Accounting
    # Financial Analysis
    # Financial Planning
    # Investment Banking
    # Corporate Finance
    # Taxation
    # Auditing
    # Risk Management
    # etc.

    # =========================
    # Candidate Status
    # =========================

    status: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        default="active",
        index=True
    )

    # =========================
    # Recruiter Notes / Tags
    # =========================

    notes = Column(
        Text,
        nullable=True
    )

    # =========================
    # Do Not Contact Flag
    # =========================
    # When true, this candidate is skipped by bulk email sending
    # and any future outreach automation, regardless of selection.

    do_not_contact = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default="0"
    )

    # =========================
    # Outreach Tracking
    # =========================

    last_contacted = Column(
        DateTime,
        nullable=True
    )

    emails_sent = Column(
        Integer,
        nullable=False,
        default=0,
        server_default="0"
    )

    # =========================
    # Representation
    # =========================

    def __repr__(self):
        return (
            f"<Candidate("
            f"id={self.id}, "
            f"name='{self.name}', "
            f"role='{self.current_role}', "
            f"company='{self.company}', "
            f"city='{self.city}', "
            f"state='{self.state}', "
            f"region='{self.region}', "
            f"gender='{self.gender}', "
            f"finance_category='{self.finance_category}', "
            f"finance_subcategory='{self.finance_subcategory}'"
            f")>"
        )


# =========================================================
# RECRUITER (LOGGED-IN USER) ACCOUNTS
# =========================================================
# Each recruiter using the site has their own login and their own
# Gmail sending credentials, so bulk emails go out from the person
# who actually clicked "Send", not one shared account.

class Recruiter(Base):
    __tablename__ = "recruiters"

    id = Column(Integer, primary_key=True, index=True)

    name = Column(
        String(150),
        nullable=False
    )

    email = Column(
        String(150),
        nullable=False,
        unique=True,
        index=True
    )

    # PBKDF2 password hash, stored as "salt$hash" (hex). Never the
    # plaintext password.
    password_hash = Column(
        String(300),
        nullable=False
    )

    # =========================
    # This recruiter's own Gmail sending credentials
    # =========================

    smtp_email = Column(
        String(150),
        nullable=True
    )

    # The Gmail App Password, encrypted at rest (Fernet). Never
    # stored or returned in plaintext.
    smtp_app_password_encrypted = Column(
        Text,
        nullable=True
    )

    created_at = Column(
        DateTime,
        nullable=True
    )

    # =========================
    # Login protection (wrong-password lockout)
    # =========================

    failed_login_attempts = Column(
        Integer,
        nullable=False,
        default=0,
        server_default="0"
    )

    locked_until = Column(
        DateTime,
        nullable=True
    )

    # =========================
    # Forgot-password reset link
    # =========================
    # Only a SHA-256 hash of the emailed token is stored, never the
    # token itself.

    reset_token_hash = Column(
        String(128),
        nullable=True
    )

    reset_token_expires = Column(
        DateTime,
        nullable=True
    )

    def __repr__(self):
        return f"<Recruiter(id={self.id}, email='{self.email}')>"

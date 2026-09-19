from sqlalchemy import Column, Integer, String, Text
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
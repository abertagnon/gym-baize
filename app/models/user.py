from sqlalchemy import Column, Integer, String, Boolean, DateTime
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from app.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    shaggyowl_email = Column(String(255), nullable=True)
    shaggyowl_password_encrypted = Column(String(512), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    is_admin = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    rules = relationship("BookingRule", back_populates="user", cascade="all, delete-orphan")
    rule_blackouts = relationship("BookingRuleBlackout", back_populates="user", cascade="all, delete-orphan")
    date_rules = relationship("BookingDateRule", back_populates="user", cascade="all, delete-orphan")
    booking_logs = relationship("BookingLog", back_populates="user", cascade="all, delete-orphan")

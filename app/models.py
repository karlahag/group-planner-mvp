from datetime import datetime
from sqlalchemy import String, Integer, Float, DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .database import Base

class Poll(Base):
    __tablename__ = "polls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    poll_type: Mapped[str] = mapped_column(String(30))  # availability / hotel
    admin_token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    participant_token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    total_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_mode: Mapped[str] = mapped_column(String(30), default="per_night")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    options = relationship("Option", back_populates="poll", cascade="all, delete-orphan")
    participants = relationship("Participant", back_populates="poll", cascade="all, delete-orphan")

class Option(Base):
    __tablename__ = "options"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    poll_id: Mapped[int] = mapped_column(ForeignKey("polls.id", ondelete="CASCADE"))
    date: Mapped[str] = mapped_column(String(10))
    start_time: Mapped[str | None] = mapped_column(String(5), nullable=True)
    end_time: Mapped[str | None] = mapped_column(String(5), nullable=True)

    poll = relationship("Poll", back_populates="options")
    responses = relationship("Response", back_populates="option", cascade="all, delete-orphan")

class Participant(Base):
    __tablename__ = "participants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    poll_id: Mapped[int] = mapped_column(ForeignKey("polls.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str] = mapped_column(String(320), default="", index=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    poll = relationship("Poll", back_populates="participants")
    responses = relationship("Response", back_populates="participant", cascade="all, delete-orphan")

class Response(Base):
    __tablename__ = "responses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    participant_id: Mapped[int] = mapped_column(ForeignKey("participants.id", ondelete="CASCADE"))
    option_id: Mapped[int] = mapped_column(ForeignKey("options.id", ondelete="CASCADE"))
    answer: Mapped[str] = mapped_column(String(20))  # yes / no / maybe / selected

    participant = relationship("Participant", back_populates="responses")
    option = relationship("Option", back_populates="responses")

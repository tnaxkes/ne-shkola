import enum
from datetime import datetime

from sqlalchemy import (
    BigInteger, Boolean, Column, Date, DateTime, Enum, ForeignKey,
    Integer, String, Text, Time, UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.database import Base


class BookingStatus(str, enum.Enum):
    new = "new"
    confirmed = "confirmed"
    cancelled = "cancelled"
    completed = "completed"


class BroadcastStatus(str, enum.Enum):
    draft = "draft"
    sending = "sending"
    sent = "sent"


class Service(Base):
    __tablename__ = "services"

    id = Column(Integer, primary_key=True, index=True)
    category = Column(String, nullable=False)
    name = Column(String, nullable=False)
    price = Column(Integer, nullable=False)
    duration = Column(Integer, nullable=False)
    description = Column(Text)
    is_active = Column(Boolean, default=True, nullable=False)

    master_services = relationship("MasterService", back_populates="service", cascade="all, delete-orphan")
    bookings = relationship("Booking", back_populates="service")


class Master(Base):
    __tablename__ = "masters"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)

    master_services = relationship("MasterService", back_populates="master", cascade="all, delete-orphan")
    bookings = relationship("Booking", back_populates="master")


class MasterService(Base):
    __tablename__ = "master_services"
    __table_args__ = (UniqueConstraint("master_id", "service_id"),)

    id = Column(Integer, primary_key=True, index=True)
    master_id = Column(Integer, ForeignKey("masters.id", ondelete="CASCADE"), nullable=False)
    service_id = Column(Integer, ForeignKey("services.id", ondelete="CASCADE"), nullable=False)

    master = relationship("Master", back_populates="master_services")
    service = relationship("Service", back_populates="master_services")


class Client(Base):
    __tablename__ = "clients"

    id = Column(Integer, primary_key=True, index=True)
    telegram_user_id = Column(BigInteger, unique=True, nullable=True, index=True)
    name = Column(String, nullable=False)
    phone = Column(String, nullable=False)
    first_seen_at = Column(DateTime, default=datetime.utcnow)
    last_seen_at = Column(DateTime, default=datetime.utcnow)
    visits_count = Column(Integer, default=1, nullable=False)

    bookings = relationship("Booking", back_populates="client")
    reminder_logs = relationship("ReminderLog", back_populates="client", cascade="all, delete-orphan")


class Booking(Base):
    __tablename__ = "bookings"

    id = Column(Integer, primary_key=True, index=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=False)
    service_id = Column(Integer, ForeignKey("services.id"), nullable=False)
    master_id = Column(Integer, ForeignKey("masters.id"), nullable=True)
    desired_date = Column(Date, nullable=False)
    desired_time = Column(Time, nullable=False)
    comment = Column(Text)
    status = Column(Enum(BookingStatus), default=BookingStatus.new, nullable=False)
    calendar_event_id = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)

    client = relationship("Client", back_populates="bookings")
    service = relationship("Service", back_populates="bookings")
    master = relationship("Master", back_populates="bookings")
    review = relationship("BookingReview", back_populates="booking", uselist=False, cascade="all, delete-orphan")


class BookingReview(Base):
    __tablename__ = "booking_reviews"

    id = Column(Integer, primary_key=True, index=True)
    booking_id = Column(Integer, ForeignKey("bookings.id", ondelete="CASCADE"), unique=True, nullable=False)
    rating = Column(Integer, nullable=False)
    comment = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    booking = relationship("Booking", back_populates="review")


class ReminderLog(Base):
    __tablename__ = "reminder_logs"

    id = Column(Integer, primary_key=True, index=True)
    client_id = Column(Integer, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    reminder_type = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    client = relationship("Client", back_populates="reminder_logs")


class Broadcast(Base):
    __tablename__ = "broadcasts"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    message_text = Column(Text, nullable=False)
    image_url = Column(String)
    audience_type = Column(String, nullable=False)
    status = Column(Enum(BroadcastStatus), default=BroadcastStatus.draft, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    sent_at = Column(DateTime, nullable=True)

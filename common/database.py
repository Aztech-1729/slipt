# common/database.py
import os
from datetime import datetime, timedelta
from sqlalchemy import (
    create_engine, Column, Integer, String, Boolean,
    DateTime, Text, Float, ForeignKey, BigInteger
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///slipt_bot.db")
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {}
)
Base = declarative_base()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def _now() -> datetime:
    """Naive UTC — consistent across all tables."""
    return datetime.utcnow()


# ─── Models ───────────────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"
    id            = Column(Integer, primary_key=True, index=True)
    telegram_id   = Column(BigInteger, unique=True, nullable=False)
    username      = Column(String(100), nullable=True)
    first_name    = Column(String(100), nullable=True)
    account_limit = Column(Integer, default=1)
    amount_paid   = Column(Float, default=0.0)
    days_granted  = Column(Integer, default=0)
    expires_at    = Column(DateTime, nullable=True)
    is_active     = Column(Boolean, default=False)  # Admin must grant access
    is_banned     = Column(Boolean, default=False)
    created_at    = Column(DateTime, default=_now)
    accounts      = relationship("TelegramAccount", back_populates="owner", cascade="all, delete-orphan")
    ad_stats      = relationship("AdStat", back_populates="owner", cascade="all, delete-orphan")


class TelegramAccount(Base):
    __tablename__ = "telegram_accounts"
    id            = Column(Integer, primary_key=True, index=True)
    user_id       = Column(Integer, ForeignKey("users.id"), nullable=False)
    phone         = Column(String(20), nullable=False)
    api_id        = Column(String(50), nullable=False)
    api_hash      = Column(String(100), nullable=False)
    session_file  = Column(String(200), nullable=True)
    is_active     = Column(Boolean, default=True)
    is_running    = Column(Boolean, default=False)
    group_delay   = Column(Integer, default=30)
    process_delay = Column(Integer, default=3600)
    track_channel = Column(String(100), nullable=True)
    created_at    = Column(DateTime, default=_now)
    owner         = relationship("User", back_populates="accounts")
    ad_stats      = relationship("AdStat", back_populates="account", cascade="all, delete-orphan")


class AdStat(Base):
    __tablename__ = "ad_stats"
    id         = Column(Integer, primary_key=True, index=True)
    user_id    = Column(Integer, ForeignKey("users.id"), nullable=False)
    account_id = Column(Integer, ForeignKey("telegram_accounts.id"), nullable=False)
    group_name = Column(String(200), nullable=True)
    group_id   = Column(BigInteger, nullable=True)
    topic_name = Column(String(200), nullable=True)
    message_id = Column(BigInteger, nullable=True)
    status     = Column(String(20), default="success")
    error_msg  = Column(Text, nullable=True)
    sent_at    = Column(DateTime, default=_now)
    owner      = relationship("User", back_populates="ad_stats")
    account    = relationship("TelegramAccount", back_populates="ad_stats")


class BotSettings(Base):
    __tablename__ = "bot_settings"
    id               = Column(Integer, primary_key=True, index=True)
    maintenance_mode = Column(Boolean, default=False)
    rules_text       = Column(Text, default="Welcome to Slipt Bot!")
    support_username = Column(String(100), default="@Sliptplug")
    updated_at       = Column(DateTime, default=_now)


class ExpiryNotified(Base):
    """Prevents duplicate expiry reminder spam. Cleaned up daily."""
    __tablename__ = "expiry_notified"
    id          = Column(Integer, primary_key=True)
    telegram_id = Column(BigInteger, nullable=False, index=True)
    notified_at = Column(DateTime, default=_now)


class AdTemplate(Base):
    """Saved ad campaign templates for 1-click reuse."""
    __tablename__ = "ad_templates"
    id          = Column(Integer, primary_key=True, index=True)
    user_id     = Column(Integer, ForeignKey("users.id"), nullable=False)
    name        = Column(String(100), nullable=False)
    config_json = Column(Text, nullable=False)
    created_at  = Column(DateTime, default=_now)
    owner       = relationship("User", foreign_keys=[user_id])


# ─── DB Manager ───────────────────────────────────────────────────────────────

class DBManager:

    def __init__(self):
        Base.metadata.create_all(bind=engine)
        self._ensure_settings()

    def _ensure_settings(self):
        db = SessionLocal()
        try:
            if not db.query(BotSettings).first():
                db.add(BotSettings())
                db.commit()
        finally:
            db.close()

    # ── Users ─────────────────────────────────────────────────────────────

    def create_or_update_user(self, telegram_id: int,
                               username: str = None, first_name: str = None) -> dict:
        db = SessionLocal()
        try:
            u = db.query(User).filter(User.telegram_id == telegram_id).first()
            if not u:
                u = User(telegram_id=telegram_id, username=username, first_name=first_name)
                db.add(u)
            else:
                if username:   u.username   = username
                if first_name: u.first_name = first_name
            db.commit()
            db.refresh(u)
            return {
                "id": u.id, "telegram_id": u.telegram_id,
                "is_active": u.is_active, "is_banned": u.is_banned,
                "expires_at": u.expires_at, "account_limit": u.account_limit,
                "first_name": u.first_name, "username": u.username,
            }
        finally:
            db.close()

    def grant_access(self, telegram_id: int, account_limit: int,
                     amount_paid: float, days: int, username: str = None) -> datetime:
        db = SessionLocal()
        try:
            u = db.query(User).filter(User.telegram_id == telegram_id).first()
            if not u:
                u = User(telegram_id=telegram_id, username=username)
                db.add(u)
            u.account_limit = account_limit
            u.amount_paid   = amount_paid
            u.days_granted  = days
            u.is_active     = True
            u.is_banned     = False
            expires         = datetime.utcnow() + timedelta(days=days)
            u.expires_at    = expires
            db.commit()
            return expires
        finally:
            db.close()

    def revoke_access(self, telegram_id: int) -> bool:
        db = SessionLocal()
        try:
            u = db.query(User).filter(User.telegram_id == telegram_id).first()
            if u:
                u.is_active = False
                u.is_banned = True
                db.commit()
                return True
            return False
        finally:
            db.close()

    def extend_validity(self, telegram_id: int, days: int):
        db = SessionLocal()
        try:
            u = db.query(User).filter(User.telegram_id == telegram_id).first()
            if not u:
                return None
            base = u.expires_at if (u.expires_at and u.expires_at > datetime.utcnow()) \
                   else datetime.utcnow()
            u.expires_at = base + timedelta(days=days)
            db.commit()
            return u.expires_at
        finally:
            db.close()

    def is_user_valid(self, telegram_id: int) -> bool:
        db = SessionLocal()
        try:
            u = db.query(User).filter(User.telegram_id == telegram_id).first()
            if not u:
                return False
            if u.is_banned or not u.is_active:
                return False
            # expires_at MUST be set — None means admin hasn't granted access yet
            if not u.expires_at:
                return False
            if u.expires_at < datetime.utcnow():
                return False
            return True
        finally:
            db.close()

    def get_all_users(self) -> list:
        db = SessionLocal()
        try:
            return [
                {
                    "id": u.id, "telegram_id": u.telegram_id,
                    "username": u.username, "first_name": u.first_name,
                    "is_active": u.is_active, "is_banned": u.is_banned,
                    "expires_at": u.expires_at, "account_limit": u.account_limit,
                }
                for u in db.query(User).all()
            ]
        finally:
            db.close()

    def get_expiring_users(self, hours: int = 24) -> list:
        db = SessionLocal()
        try:
            now       = datetime.utcnow()
            threshold = now + timedelta(hours=hours)
            return [
                {"telegram_id": u.telegram_id, "first_name": u.first_name, "expires_at": u.expires_at}
                for u in db.query(User).filter(
                    User.is_active  == True,
                    User.is_banned  == False,
                    User.expires_at != None,
                    User.expires_at <= threshold,
                    User.expires_at >= now,
                ).all()
            ]
        finally:
            db.close()

    def was_expiry_notified_today(self, telegram_id: int) -> bool:
        db = SessionLocal()
        try:
            today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            return db.query(ExpiryNotified).filter(
                ExpiryNotified.telegram_id == telegram_id,
                ExpiryNotified.notified_at >= today,
            ).first() is not None
        finally:
            db.close()

    def mark_expiry_notified(self, telegram_id: int):
        db = SessionLocal()
        try:
            db.add(ExpiryNotified(telegram_id=telegram_id))
            db.commit()
        finally:
            db.close()

    def cleanup_old_notifications(self):
        """Purge ExpiryNotified rows older than 2 days — call daily."""
        db = SessionLocal()
        try:
            cutoff = datetime.utcnow() - timedelta(days=2)
            db.query(ExpiryNotified).filter(ExpiryNotified.notified_at < cutoff).delete()
            db.commit()
        finally:
            db.close()

    # ── TelegramAccount ───────────────────────────────────────────────────

    def add_account(self, user_id_db: int, phone: str,
                    api_id: str, api_hash: str, session_file: str) -> int:
        db = SessionLocal()
        try:
            acc = TelegramAccount(
                user_id=user_id_db, phone=phone,
                api_id=api_id, api_hash=api_hash, session_file=session_file,
            )
            db.add(acc)
            db.commit()
            db.refresh(acc)
            return acc.id
        finally:
            db.close()

    def remove_account(self, account_id: int, user_id_db: int):
        db = SessionLocal()
        try:
            acc = db.query(TelegramAccount).filter(
                TelegramAccount.id      == account_id,
                TelegramAccount.user_id == user_id_db,
            ).first()
            if acc:
                session_file = acc.session_file
                db.delete(acc)
                db.commit()
                return session_file
            return None
        finally:
            db.close()

    def get_accounts(self, user_id_db: int, only_idle: bool = False) -> list:
        db = SessionLocal()
        try:
            q = db.query(TelegramAccount).filter(TelegramAccount.user_id == user_id_db)
            if only_idle:
                q = q.filter(TelegramAccount.is_running == False)
            return [
                {
                    "id": a.id, "phone": a.phone, "is_running": a.is_running,
                    "group_delay": a.group_delay, "process_delay": a.process_delay,
                    "track_channel": a.track_channel, "session_file": a.session_file,
                    "api_id": a.api_id, "api_hash": a.api_hash,
                }
                for a in q.all()
            ]
        finally:
            db.close()

    def get_account(self, account_id: int):
        db = SessionLocal()
        try:
            a = db.query(TelegramAccount).filter(TelegramAccount.id == account_id).first()
            if not a:
                return None
            return {
                "id": a.id, "phone": a.phone, "is_running": a.is_running,
                "group_delay": a.group_delay, "process_delay": a.process_delay,
                "track_channel": a.track_channel, "session_file": a.session_file,
                "api_id": a.api_id, "api_hash": a.api_hash,
                "user_id": a.user_id,
                "expires_at": None,   # accounts don't have their own expiry; use user expiry
            }
        finally:
            db.close()

    def set_account_running(self, account_id: int, running: bool):
        db = SessionLocal()
        try:
            acc = db.query(TelegramAccount).filter(TelegramAccount.id == account_id).first()
            if acc:
                acc.is_running = running
                db.commit()
        finally:
            db.close()

    def update_account_settings(self, account_id: int, group_delay: int = None,
                                 process_delay: int = None, track_channel: str = None):
        if group_delay is None and process_delay is None and track_channel is None:
            return
        db = SessionLocal()
        try:
            acc = db.query(TelegramAccount).filter(TelegramAccount.id == account_id).first()
            if acc:
                if group_delay   is not None: acc.group_delay   = group_delay
                if process_delay is not None: acc.process_delay = process_delay
                if track_channel is not None: acc.track_channel = track_channel
                db.commit()
        finally:
            db.close()

    def get_user_account_count(self, user_id_db: int) -> int:
        db = SessionLocal()
        try:
            return db.query(TelegramAccount).filter(TelegramAccount.user_id == user_id_db).count()
        finally:
            db.close()

    # ── AdStats ───────────────────────────────────────────────────────────

    def log_ad(self, user_id_db: int, account_id: int, group_name: str,
               group_id: int, topic_name: str, message_id: int,
               status: str = "success", error_msg: str = None):
        db = SessionLocal()
        try:
            db.add(AdStat(
                user_id=user_id_db, account_id=account_id,
                group_name=group_name, group_id=group_id,
                topic_name=topic_name, message_id=message_id,
                status=status, error_msg=error_msg,
            ))
            db.commit()
        finally:
            db.close()

    def get_stats(self, user_id_db: int) -> dict:
        db = SessionLocal()
        try:
            today         = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            total_success = db.query(AdStat).filter(
                AdStat.user_id == user_id_db, AdStat.status == "success").count()
            total_failed  = db.query(AdStat).filter(
                AdStat.user_id == user_id_db, AdStat.status == "failed").count()
            daily_success = db.query(AdStat).filter(
                AdStat.user_id == user_id_db,
                AdStat.status  == "success",
                AdStat.sent_at >= today,
            ).count()
            return {"total_success": total_success, "total_failed": total_failed,
                    "daily_success": daily_success}
        finally:
            db.close()

    # ── BotSettings ───────────────────────────────────────────────────────

    def get_settings(self) -> dict:
        db = SessionLocal()
        try:
            s = db.query(BotSettings).first()
            return {
                "maintenance_mode": s.maintenance_mode,
                "rules_text":       s.rules_text,
                "support_username": s.support_username,
            }
        finally:
            db.close()

    def update_settings(self, maintenance_mode: bool = None,
                         rules_text: str = None, support_username: str = None):
        db = SessionLocal()
        try:
            s = db.query(BotSettings).first()
            if maintenance_mode is not None: s.maintenance_mode = maintenance_mode
            if rules_text       is not None: s.rules_text       = rules_text
            if support_username is not None: s.support_username = support_username
            s.updated_at = datetime.utcnow()
            db.commit()
        finally:
            db.close()


    # ── AdTemplate ────────────────────────────────────────────────────────

    def save_template(self, user_db_id: int, name: str, config: dict) -> int:
        import json
        db = SessionLocal()
        try:
            tmpl = AdTemplate(user_id=user_db_id, name=name, config_json=json.dumps(config))
            db.add(tmpl)
            db.commit()
            db.refresh(tmpl)
            return tmpl.id
        finally:
            db.close()

    def get_templates(self, user_db_id: int) -> list:
        import json
        db = SessionLocal()
        try:
            rows = db.query(AdTemplate).filter(AdTemplate.user_id == user_db_id).order_by(AdTemplate.created_at.desc()).all()
            return [
                {"id": r.id, "name": r.name, "config": json.loads(r.config_json), "created_at": r.created_at}
                for r in rows
            ]
        finally:
            db.close()

    def delete_template(self, template_id: int, user_db_id: int) -> bool:
        db = SessionLocal()
        try:
            row = db.query(AdTemplate).filter(AdTemplate.id == template_id, AdTemplate.user_id == user_db_id).first()
            if not row:
                return False
            db.delete(row)
            db.commit()
            return True
        finally:
            db.close()


db_manager = DBManager()

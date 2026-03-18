"""
Livewatch - Plateforme complète de streaming
Version 6.5 ULTIMATE - IPTV.org complet (pays/subdivisions/villes/catégories) + YouTube + Multi-Decoder + Lecteur Universel
Auteur: Livewatch Team
Licence: MIT
Propriétaire: erickbenoit337@gmail.com / WALKER92259
"""

import os
import sys
import uuid
import hashlib
import json
import random
import string
import asyncio
import logging
import secrets
import httpx
import re
import subprocess
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Set, Union
from contextlib import asynccontextmanager
from ipaddress import ip_address as validate_ip
from urllib.parse import urlparse, quote, urljoin, parse_qs
import html
import platform
import socket
import ssl

# ==================== CONFIGURATION DE L'ENCODAGE POUR WINDOWS ====================
if platform.system() == "Windows":
    import codecs
    import io
    
    # Fix for Windows console encoding
    if sys.stdout is not None and hasattr(sys.stdout, 'buffer'):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    if sys.stderr is not None and hasattr(sys.stderr, 'buffer'):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# ==================== LOGGING AMÉLIORÉ ====================
class UnicodeStreamHandler(logging.StreamHandler):
    """Handler qui gère correctement l'Unicode sur Windows"""
    def emit(self, record):
        try:
            msg = self.format(record)
            stream = self.stream
            try:
                stream.write(msg + self.terminator)
            except UnicodeEncodeError:
                # Fallback: enlever les emojis
                msg_ascii = msg.encode('ascii', 'ignore').decode('ascii')
                stream.write(msg_ascii + self.terminator)
            self.flush()
        except Exception:
            self.handleError(record)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('livewatch.log', encoding='utf-8'),
        UnicodeStreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ==================== IMPORTS ====================
import uvicorn
from dotenv import load_dotenv
load_dotenv()  # Charge le fichier .env si présent
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, HTTPException, Depends, Form, UploadFile, File, Response, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from sqlalchemy import create_engine, Column, String, Integer, DateTime, Boolean, Text, Float, ForeignKey, Index, and_, or_, desc, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import sessionmaker, Session, relationship, declarative_base
from sqlalchemy.pool import QueuePool
from passlib.context import CryptContext
from jose import JWTError, jwt
import aiofiles
from PIL import Image
import aiohttp
import m3u8
import dateutil.parser

try:
    import yt_dlp
    YT_DLP_AVAILABLE = True
except ImportError:
    YT_DLP_AVAILABLE = False
    logger.warning("yt-dlp non disponible")

# ==================== CONFIGURATION AMÉLIORÉE ====================

class Settings:
    APP_NAME = "livewatch"
    APP_VERSION = "1.0 ULTIMATE"
    APP_DESCRIPTION = "Plateforme de streaming ultime — TV, Sports, IPTV Monde (pays/régions/villes), YouTube Live, Radio & Lives communautaires"

    # Sécurité
    SECRET_KEY = os.getenv("SECRET_KEY", secrets.token_urlsafe(32))
    ALGORITHM = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES = 30

    # ── Base de données PostgreSQL ──────────────────────────────────────────
    # Format : postgresql://user:password@host:port/dbname
    # Peut aussi être fourni via la variable d'environnement DATABASE_URL
    DATABASE_URL = os.getenv(
        "DATABASE_URL",
        "postgresql://livewatch_917w_user:nuZUykwJTQY13eBexSgfwPd0bzIQv7wz@dpg-d6t6hpvgi27c73dfv6n0-a/livewatch_917w"
    )
    # Alembic / psycopg2 veut "postgresql://" pas "postgres://" (Heroku legacy)
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

    DATABASE_POOL_SIZE     = int(os.getenv("DB_POOL_SIZE",     "20"))
    DATABASE_MAX_OVERFLOW  = int(os.getenv("DB_MAX_OVERFLOW",  "10"))
    DATABASE_POOL_TIMEOUT  = int(os.getenv("DB_POOL_TIMEOUT",  "30"))
    DATABASE_POOL_RECYCLE  = int(os.getenv("DB_POOL_RECYCLE",  "1800"))  # 30 min

    # Admin par défaut (propriétaire)
    ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "WALKER92259")
    ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "WALKER92259")
    ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "erickbenoit337@gmail.com")
    OWNER_ID = "erickbenoit337@gmail.com"

    # Streaming utilisateur
    MAX_STREAM_DURATION_HOURS = 12
    MAX_CONCURRENT_STREAMS_PER_USER = 3
    MAX_COMMENT_LENGTH = 500
    MAX_COMMENTS_PER_MINUTE = 5

    # Proxy
    PROXY_TIMEOUT = 60
    MAX_PROXY_SIZE = 200 * 1024 * 1024
    CACHE_TTL = 600
    STREAM_CACHE_TTL = 120
    MAX_RETRIES = 3
    RETRY_DELAY = 2

    # Fichiers
    MAX_UPLOAD_SIZE = 20 * 1024 * 1024
    ALLOWED_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.webp'}

    # Modération
    AUTO_BLOCK_THRESHOLD = 5
    COMMENT_FLAG_THRESHOLD = 3
    SESSION_MAX_AGE = 7 * 24 * 60 * 60

    # IPTV Sync amélioré
    IPTV_SYNC_INTERVAL = 12 * 60 * 60  # 12 heures
    IPTV_BASE_URL = "https://iptv-org.github.io/iptv"
    IPTV_TIMEOUT = 120
    IPTV_MAX_RETRIES = 3
    IPTV_CONCURRENT_DOWNLOADS = 3

    # YouTube
    YOUTUBE_TIMEOUT = 60
    YOUTUBE_CACHE_TTL = 300

    # Lecteur universel
    ENABLE_DASH = True
    ENABLE_HLS = True
    ENABLE_MP4 = True
    ENABLE_AUDIO = True
    ENABLE_YOUTUBE = True

    # Logo
    LOGO_PATH = "static/IMG.png"

settings = Settings()

# ==================== BASE DE DONNÉES POSTGRESQL ====================

engine = create_engine(
    settings.DATABASE_URL,
    pool_size        = settings.DATABASE_POOL_SIZE,
    max_overflow     = settings.DATABASE_MAX_OVERFLOW,
    pool_timeout     = settings.DATABASE_POOL_TIMEOUT,
    pool_recycle     = settings.DATABASE_POOL_RECYCLE,
    pool_pre_ping    = True,   # vérifie la connexion avant chaque usage
    echo             = False,  # passer à True pour déboguer les requêtes SQL
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Helper : UUID natif PostgreSQL avec génération automatique
def pg_uuid():
    return Column(PG_UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))

# ==================== MODÈLES POSTGRESQL ====================

class User(Base):
    __tablename__ = "users"
    id               = Column(PG_UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    username         = Column(String(50),  unique=True, index=True, nullable=False)
    email            = Column(String(100), unique=True, index=True, nullable=False)
    hashed_password  = Column(String(200), nullable=False)
    is_active        = Column(Boolean, default=True)
    is_admin         = Column(Boolean, default=False)
    is_owner         = Column(Boolean, default=False)
    created_at       = Column(DateTime, default=datetime.utcnow)
    last_login       = Column(DateTime, nullable=True)
    ip_address       = Column(String(50), nullable=True)
    is_blocked       = Column(Boolean, default=False)
    failed_login_attempts = Column(Integer, default=0)
    locked_until     = Column(DateTime, nullable=True)

class Visitor(Base):
    __tablename__ = "visitors"
    id                 = Column(PG_UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    visitor_id         = Column(String(100), unique=True, index=True, nullable=False)
    ip_address         = Column(String(50))
    user_agent         = Column(String(500))
    created_at         = Column(DateTime, default=datetime.utcnow)
    expires_at         = Column(DateTime, default=lambda: datetime.utcnow() + timedelta(days=7))
    last_seen          = Column(DateTime, default=datetime.utcnow)
    is_blocked         = Column(Boolean, default=False)
    total_comments     = Column(Integer, default=0)
    total_streams      = Column(Integer, default=0)
    preferred_language = Column(String(10), default="fr")
    theme              = Column(String(10), default="auto")

class ExternalStream(Base):
    __tablename__ = "external_streams"
    id           = Column(PG_UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    title        = Column(String(200), nullable=False)
    category     = Column(String(50),  nullable=False)
    subcategory  = Column(String(50),  nullable=True)
    country      = Column(String(10),  nullable=True)
    language     = Column(String(10),  default="fr")
    url          = Column(String(1000),nullable=False)
    logo         = Column(String(500), nullable=True)
    proxy_needed = Column(Boolean, default=False)
    is_active    = Column(Boolean, default=True)
    quality      = Column(String(20),  default="HD")
    viewers      = Column(Integer, default=0)
    created_at   = Column(DateTime, default=datetime.utcnow)
    last_checked = Column(DateTime, nullable=True)
    stream_type  = Column(String(20),  default="hls")
    bitrate      = Column(Integer, default=0)
    width        = Column(Integer, default=0)
    height       = Column(Integer, default=0)
    fps          = Column(Integer, default=0)
    user_agent   = Column(String(200), nullable=True)
    referer      = Column(String(500), nullable=True)
    headers      = Column(Text, nullable=True)

class IPTVChannel(Base):
    __tablename__ = "iptv_channels"
    id          = Column(PG_UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    playlist_id = Column(String(50), index=True)
    name        = Column(String(200), nullable=False)
    url         = Column(String(1000),nullable=False)
    logo        = Column(String(500), nullable=True)
    category    = Column(String(100), nullable=True)
    country     = Column(String(10),  nullable=True)
    language    = Column(String(10),  nullable=True)
    tvg_id      = Column(String(100), nullable=True)
    tvg_name    = Column(String(200), nullable=True)
    tvg_chno    = Column(Integer, nullable=True)
    tvg_shift   = Column(Float,   nullable=True)
    is_active   = Column(Boolean, default=True)
    viewers     = Column(Integer, default=0)
    last_seen   = Column(DateTime, default=datetime.utcnow)
    created_at  = Column(DateTime, default=datetime.utcnow)
    stream_type = Column(String(20), default="hls")
    bitrate     = Column(Integer, default=0)
    resolution  = Column(String(20), nullable=True)
    is_working  = Column(Boolean, default=True)
    last_check  = Column(DateTime, nullable=True)
    check_count = Column(Integer, default=0)
    fail_count  = Column(Integer, default=0)

    __table_args__ = (
        Index('idx_iptv_playlist',  'playlist_id'),
        Index('idx_iptv_category',  'category'),
        Index('idx_iptv_country',   'country'),
    )

class IPTVPlaylist(Base):
    __tablename__ = "iptv_playlists"
    id            = Column(PG_UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    name          = Column(String(100), unique=True, nullable=False)
    display_name  = Column(String(200), nullable=False)
    url           = Column(String(500), nullable=False)
    channel_count = Column(Integer, default=0)
    last_sync     = Column(DateTime, nullable=True)
    last_updated  = Column(DateTime, nullable=True)
    is_active     = Column(Boolean, default=True)
    category      = Column(String(50),  default="iptv")
    country       = Column(String(10),  nullable=True)
    playlist_type = Column(String(20),  default="country")
    sync_interval = Column(Integer, default=86400)
    sync_error    = Column(Text, nullable=True)
    sync_status   = Column(String(20),  default="pending")

    __table_args__ = (Index('idx_playlist_name', 'name'),)

class UserStream(Base):
    __tablename__ = "user_streams"
    id                 = Column(PG_UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    title              = Column(String(200), nullable=False)
    description        = Column(Text, nullable=True)
    category           = Column(String(50),  nullable=False)
    stream_key         = Column(String(100),  unique=True, nullable=False)
    thumbnail          = Column(String(500),  nullable=True)
    viewer_count       = Column(Integer, default=0)
    peak_viewers       = Column(Integer, default=0)
    like_count         = Column(Integer, default=0)
    is_live            = Column(Boolean, default=False)
    is_featured        = Column(Boolean, default=False)
    is_blocked         = Column(Boolean, default=False)
    created_at         = Column(DateTime, default=datetime.utcnow)
    started_at         = Column(DateTime, nullable=True)
    ended_at           = Column(DateTime, nullable=True)
    visitor_id         = Column(PG_UUID(as_uuid=False), ForeignKey("visitors.id"))
    stream_url         = Column(String(500), nullable=True)
    tags               = Column(String(500), nullable=True)
    language           = Column(String(10),  default="fr")
    report_count       = Column(Integer, default=0)
    is_mature          = Column(Boolean, default=False)
    chat_enabled       = Column(Boolean, default=True)
    recording_enabled  = Column(Boolean, default=False)

    visitor  = relationship("Visitor", backref="streams")
    comments = relationship("Comment",     back_populates="stream", cascade="all, delete-orphan")
    stats    = relationship("StreamStats", back_populates="stream", cascade="all, delete-orphan")

class Comment(Base):
    __tablename__ = "comments"
    id            = Column(PG_UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    content       = Column(Text, nullable=False)
    stream_id     = Column(PG_UUID(as_uuid=False), ForeignKey("user_streams.id"), nullable=False)
    visitor_id    = Column(PG_UUID(as_uuid=False), ForeignKey("visitors.id"),     nullable=False)
    created_at    = Column(DateTime, default=datetime.utcnow)
    is_flagged    = Column(Boolean, default=False)
    is_deleted    = Column(Boolean, default=False)
    is_auto_hidden= Column(Boolean, default=False)
    ip_address    = Column(String(50))
    report_count  = Column(Integer, default=0)
    likes         = Column(Integer, default=0)
    reply_to      = Column(PG_UUID(as_uuid=False), nullable=True)

    stream  = relationship("UserStream", back_populates="comments")
    visitor = relationship("Visitor")

class Report(Base):
    __tablename__ = "reports"
    id          = Column(PG_UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    reason      = Column(String(200), nullable=False)
    comment_id  = Column(PG_UUID(as_uuid=False), ForeignKey("comments.id"),     nullable=True)
    stream_id   = Column(PG_UUID(as_uuid=False), ForeignKey("user_streams.id"), nullable=True)
    visitor_id  = Column(PG_UUID(as_uuid=False), ForeignKey("visitors.id"),     nullable=False)
    created_at  = Column(DateTime, default=datetime.utcnow)
    resolved    = Column(Boolean, default=False)
    resolved_by = Column(PG_UUID(as_uuid=False), ForeignKey("users.id"), nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    stream_type = Column(String(20), nullable=True)

class BlockedIP(Base):
    __tablename__ = "blocked_ips"
    id          = Column(PG_UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    ip_address  = Column(String(50), unique=True, index=True, nullable=False)
    reason      = Column(String(500), nullable=False)
    blocked_at  = Column(DateTime, default=datetime.utcnow)
    expires_at  = Column(DateTime, nullable=True)
    blocked_by  = Column(PG_UUID(as_uuid=False), ForeignKey("users.id"), nullable=True)
    is_permanent= Column(Boolean, default=False)

class Favorite(Base):
    __tablename__ = "favorites"
    id          = Column(PG_UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    visitor_id  = Column(PG_UUID(as_uuid=False), ForeignKey("visitors.id"), nullable=False)
    stream_id   = Column(String(36), nullable=False)
    stream_type = Column(String(20), nullable=False)
    created_at  = Column(DateTime, default=datetime.utcnow)

class StreamStats(Base):
    __tablename__ = "stream_stats"
    id            = Column(PG_UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    stream_id     = Column(PG_UUID(as_uuid=False), ForeignKey("user_streams.id"), nullable=False)
    timestamp     = Column(DateTime, default=datetime.utcnow)
    viewer_count  = Column(Integer, default=0)
    like_count    = Column(Integer, default=0)
    comment_count = Column(Integer, default=0)

    stream = relationship("UserStream", back_populates="stats")

class StreamRecording(Base):
    __tablename__ = "stream_recordings"
    id         = Column(PG_UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    stream_id  = Column(PG_UUID(as_uuid=False), ForeignKey("user_streams.id"), nullable=False)
    start_time = Column(DateTime, default=datetime.utcnow)
    end_time   = Column(DateTime, nullable=True)
    file_path  = Column(String(500), nullable=True)
    file_size  = Column(Integer, default=0)
    duration   = Column(Integer, default=0)
    status     = Column(String(20), default="recording")

class SystemLog(Base):
    __tablename__ = "system_logs"
    id         = Column(PG_UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    level      = Column(String(20), nullable=False)
    message    = Column(Text, nullable=False)
    source     = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

# ==================== MODÈLES EPG / TV EVENTS ====================

class TVEvent(Base):
    __tablename__ = "tv_events"
    id           = Column(PG_UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    title        = Column(String(300), nullable=False)
    description  = Column(Text, nullable=True)
    channel_id   = Column(PG_UUID(as_uuid=False), ForeignKey("iptv_channels.id"), nullable=True)
    channel_name = Column(String(200), nullable=True)
    channel_logo = Column(String(500), nullable=True)
    country      = Column(String(10),  nullable=True)
    category     = Column(String(50),  nullable=False, default="other")
    start_time   = Column(DateTime, nullable=False)
    end_time     = Column(DateTime, nullable=False)
    poster       = Column(String(500), nullable=True)
    is_active    = Column(Boolean, default=True)
    created_at   = Column(DateTime, default=datetime.utcnow)

    channel = relationship("IPTVChannel", foreign_keys=[channel_id])

class EventReminder(Base):
    __tablename__ = "event_reminders"
    id         = Column(PG_UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    event_id   = Column(PG_UUID(as_uuid=False), ForeignKey("tv_events.id"),  nullable=False)
    visitor_id = Column(PG_UUID(as_uuid=False), ForeignKey("visitors.id"),   nullable=False)
    notified   = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    event = relationship("TVEvent")


class DailyVisitStats(Base):
    """Compteur de visiteurs uniques par jour — alimenté automatiquement."""
    __tablename__ = "daily_visit_stats"
    id           = Column(PG_UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    date         = Column(DateTime, nullable=False, index=True, unique=True)  # minuit UTC du jour
    unique_users = Column(Integer, default=0)   # visiteurs uniques du jour
    page_views   = Column(Integer, default=0)   # total requêtes pages
    peak_active  = Column(Integer, default=0)   # pic d'utilisateurs actifs simultanés

# ── Vérification connexion PostgreSQL ────────────────────────────────────────
def _check_db_connection() -> bool:
    """Teste la connexion PostgreSQL au démarrage. Affiche un message clair si échec."""
    try:
        with engine.connect() as conn:
            conn.execute(func.now())
        return True
    except Exception as e:
        print("\n" + "="*70)
        print("❌  IMPOSSIBLE DE SE CONNECTER À POSTGRESQL")
        print("="*70)
        print(f"   URL       : {settings.DATABASE_URL}")
        print(f"   Erreur    : {e}")
        print()
        print("   Solutions :")
        print("   1. Vérifiez que PostgreSQL est démarré")
        print("      → sudo systemctl start postgresql")
        print("      → docker compose up -d postgres")
        print()
        print("   2. Vérifiez votre DATABASE_URL dans le fichier .env")
        print("      → DATABASE_URL=postgresql://user:pass@host:5432/dbname")
        print()
        print("   3. Créez la base si elle n'existe pas :")
        print("      → bash setup_postgres.sh")
        print("="*70 + "\n")
        return False

_db_ok = _check_db_connection()
if not _db_ok:
    import sys as _sys
    _sys.exit(1)

# Note : Base.metadata.create_all() est appelé dans lifespan() après
# vérification de la connexion avec retry async

# ==================== FLUX EXTERNES VÉRIFIÉS AMÉLIORÉS ====================

EXTERNAL_STREAMS = [
    # ===== NEWS INTERNATIONALES =====
    {"title":"France 24 English","category":"news","subcategory":"international","country":"FR","language":"en","url":"https://static.france24.com/live/F24_EN_LO_HLS/live_web.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/a/a8/France_24_logo.svg/200px-France_24_logo.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"France 24 Français","category":"news","subcategory":"international","country":"FR","language":"fr","url":"https://static.france24.com/live/F24_FR_LO_HLS/live_web.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/a/a8/France_24_logo.svg/200px-France_24_logo.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"France 24 عربي","category":"news","subcategory":"international","country":"FR","language":"ar","url":"https://static.france24.com/live/F24_AR_LO_HLS/live_web.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/a/a8/France_24_logo.svg/200px-France_24_logo.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"euronews English","category":"news","subcategory":"international","country":"EU","language":"en","url":"https://euronews-cnx.akamaized.net/hls/live/694960/euronewsEN/master.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/2/25/Euronews_logo_2021.svg/200px-Euronews_logo_2021.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"euronews Français","category":"news","subcategory":"international","country":"EU","language":"fr","url":"https://euronews-cnx.akamaized.net/hls/live/694960/euronewsFR/master.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/2/25/Euronews_logo_2021.svg/200px-Euronews_logo_2021.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"euronews Deutsch","category":"news","subcategory":"international","country":"EU","language":"de","url":"https://euronews-cnx.akamaized.net/hls/live/694960/euronewsDE/master.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/2/25/Euronews_logo_2021.svg/200px-Euronews_logo_2021.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"euronews Español","category":"news","subcategory":"international","country":"EU","language":"es","url":"https://euronews-cnx.akamaized.net/hls/live/694960/euronewsES/master.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/2/25/Euronews_logo_2021.svg/200px-Euronews_logo_2021.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"euronews Italiano","category":"news","subcategory":"international","country":"EU","language":"it","url":"https://euronews-cnx.akamaized.net/hls/live/694960/euronewsIT/master.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/2/25/Euronews_logo_2021.svg/200px-Euronews_logo_2021.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"euronews Português","category":"news","subcategory":"international","country":"EU","language":"pt","url":"https://euronews-cnx.akamaized.net/hls/live/694960/euronewsPT/master.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/2/25/Euronews_logo_2021.svg/200px-Euronews_logo_2021.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"euronews Русский","category":"news","subcategory":"international","country":"EU","language":"ru","url":"https://euronews-cnx.akamaized.net/hls/live/694960/euronewsRU/master.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/2/25/Euronews_logo_2021.svg/200px-Euronews_logo_2021.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"euronews Türkçe","category":"news","subcategory":"international","country":"EU","language":"tr","url":"https://euronews-cnx.akamaized.net/hls/live/694960/euronewsTR/master.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/2/25/Euronews_logo_2021.svg/200px-Euronews_logo_2021.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"Deutsche Welle English","category":"news","subcategory":"international","country":"DE","language":"en","url":"https://dwamdstream102.akamaized.net/hls/live/2015525/dwstream102/index.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/7/75/Deutsche_Welle_symbol_2012.svg/200px-Deutsche_Welle_symbol_2012.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"Deutsche Welle Deutsch","category":"news","subcategory":"international","country":"DE","language":"de","url":"https://dwamdstream104.akamaized.net/hls/live/2015530/dwstream104/index.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/7/75/Deutsche_Welle_symbol_2012.svg/200px-Deutsche_Welle_symbol_2012.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"Deutsche Welle Español","category":"news","subcategory":"international","country":"DE","language":"es","url":"https://dwamdstream103.akamaized.net/hls/live/2015527/dwstream103/index.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/7/75/Deutsche_Welle_symbol_2012.svg/200px-Deutsche_Welle_symbol_2012.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"Deutsche Welle عربي","category":"news","subcategory":"international","country":"DE","language":"ar","url":"https://dwamdstream105.akamaized.net/hls/live/2015531/dwstream105/index.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/7/75/Deutsche_Welle_symbol_2012.svg/200px-Deutsche_Welle_symbol_2012.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"Al Jazeera English","category":"news","subcategory":"international","country":"QA","language":"en","url":"https://live-hls-web-aje.getaj.net/AJE/index.m3u8","logo":"https://upload.wikimedia.org/wikipedia/en/thumb/f/f2/Aljazeera_eng.svg/200px-Aljazeera_eng.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"Al Jazeera عربي","category":"news","subcategory":"international","country":"QA","language":"ar","url":"https://live-hls-web-aja.getaj.net/AJA/index.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/b/b3/Al_Jazeera_Arabic.svg/200px-Al_Jazeera_Arabic.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"TRT World","category":"news","subcategory":"international","country":"TR","language":"en","url":"https://trtworld.live.trt.com.tr/master.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/5/5d/TRT_World_logo.svg/200px-TRT_World_logo.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"France Info TV","category":"news","subcategory":"france","country":"FR","language":"fr","url":"https://simulcast.france.tv/stream/france_info","logo":"https://upload.wikimedia.org/wikipedia/fr/thumb/6/6e/Franceinfo-logo-2016.svg/200px-Franceinfo-logo-2016.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"BFM TV","category":"news","subcategory":"france","country":"FR","language":"fr","url":"https://ncdn-live-bfmtv.pfd.sfr.net/shls/LIVE$BFM_TV/index.m3u8?start=LIVE&end=END","logo":"https://upload.wikimedia.org/wikipedia/fr/thumb/5/5d/BFMTV_logo_2017.svg/200px-BFMTV_logo_2017.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"CNews","category":"news","subcategory":"france","country":"FR","language":"fr","url":"https://ncdn-live-cnews.pfd.sfr.net/shls/LIVE$CNEWS/index.m3u8?start=LIVE&end=END","logo":"https://upload.wikimedia.org/wikipedia/fr/thumb/1/10/CNews_logo.svg/200px-CNews_logo.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"LCI","category":"news","subcategory":"france","country":"FR","language":"fr","url":"https://lci-hls-secure.tf1.fr/lci/lci_hd/index.m3u8","logo":"https://upload.wikimedia.org/wikipedia/fr/thumb/d/de/LCI_logo_2016.svg/200px-LCI_logo_2016.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"CNN International","category":"news","subcategory":"international","country":"US","language":"en","url":"https://cnn-cnninternational-1-gb.samsung.wurl.com/manifest/playlist.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/b/b1/CNN_International_logo.svg/200px-CNN_International_logo.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"BBC World News","category":"news","subcategory":"international","country":"GB","language":"en","url":"https://bbcwscissorslive.akamaized.net/hls/live/2008499/bbc_world_news_ott/ott.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/6/6b/BBC_World_News_logo.svg/200px-BBC_World_News_logo.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"Sky News","category":"news","subcategory":"international","country":"GB","language":"en","url":"https://skynews24-lh.akamaihd.net/i/skynews_1@191118/master.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/9/94/Sky_News_logo.svg/200px-Sky_News_logo.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},

    # ===== TV FRANÇAISES =====
    {"title":"France 2","category":"entertainment","subcategory":"france","country":"FR","language":"fr","url":"https://simulcast.france.tv/stream/france_2","logo":"https://upload.wikimedia.org/wikipedia/fr/thumb/6/60/France_2_logo.svg/200px-France_2_logo.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"France 3","category":"entertainment","subcategory":"france","country":"FR","language":"fr","url":"https://simulcast.france.tv/stream/france_3_nationale","logo":"https://upload.wikimedia.org/wikipedia/fr/thumb/a/a5/France_3_logo.svg/200px-France_3_logo.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"France 4","category":"entertainment","subcategory":"jeune","country":"FR","language":"fr","url":"https://simulcast.france.tv/stream/france_4","logo":"https://upload.wikimedia.org/wikipedia/fr/thumb/8/86/France_4_logo.svg/200px-France_4_logo.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"France 5","category":"entertainment","subcategory":"culture","country":"FR","language":"fr","url":"https://simulcast.france.tv/stream/france_5","logo":"https://upload.wikimedia.org/wikipedia/fr/thumb/0/04/Logo_France_5.svg/200px-Logo_France_5.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"Arte","category":"entertainment","subcategory":"culture","country":"FR","language":"fr","url":"https://artesimulcast.akamaized.net/hls/live/2031003/artelive_fr/index.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/5/5e/Arte_logo.svg/200px-Arte_logo.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"Arte (Deutsch)","category":"entertainment","subcategory":"culture","country":"DE","language":"de","url":"https://artesimulcast.akamaized.net/hls/live/2031003/artelive_de/index.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/5/5e/Arte_logo.svg/200px-Arte_logo.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"M6","category":"entertainment","subcategory":"france","country":"FR","language":"fr","url":"https://ncdn-live-m6.pfd.sfr.net/shls/LIVE$M6/index.m3u8?start=LIVE&end=END","logo":"https://upload.wikimedia.org/wikipedia/fr/thumb/f/fc/M6_logo.svg/200px-M6_logo.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"W9","category":"entertainment","subcategory":"france","country":"FR","language":"fr","url":"https://ncdn-live-w9.pfd.sfr.net/shls/LIVE$W9/index.m3u8?start=LIVE&end=END","logo":"https://upload.wikimedia.org/wikipedia/fr/thumb/e/e5/W9_logo_2021.svg/200px-W9_logo_2021.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"6ter","category":"entertainment","subcategory":"france","country":"FR","language":"fr","url":"https://ncdn-live-6ter.pfd.sfr.net/shls/LIVE$6TER/index.m3u8?start=LIVE&end=END","logo":"https://upload.wikimedia.org/wikipedia/fr/thumb/c/c2/6ter_logo.svg/200px-6ter_logo.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"Gulli","category":"entertainment","subcategory":"jeune","country":"FR","language":"fr","url":"https://ncdn-live-gulli.pfd.sfr.net/shls/LIVE$GULLI/index.m3u8?start=LIVE&end=END","logo":"https://upload.wikimedia.org/wikipedia/fr/thumb/1/1f/Gulli_logo.svg/200px-Gulli_logo.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"TF1","category":"entertainment","subcategory":"france","country":"FR","language":"fr","url":"https://tf1-hls-live.tf1.fr/tf1/tf1_hd/index.m3u8","logo":"https://upload.wikimedia.org/wikipedia/fr/thumb/3/3f/TF1_Logo_2023.svg/200px-TF1_Logo_2023.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"TMC","category":"entertainment","subcategory":"france","country":"FR","language":"fr","url":"https://tmc-hls-live.tf1.fr/tmc/tmc_hd/index.m3u8","logo":"https://upload.wikimedia.org/wikipedia/fr/thumb/8/88/TMC_logo_2023.svg/200px-TMC_logo_2023.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"TFX","category":"entertainment","subcategory":"france","country":"FR","language":"fr","url":"https://tfx-hls-live.tf1.fr/tfx/tfx_hd/index.m3u8","logo":"https://upload.wikimedia.org/wikipedia/fr/thumb/b/bd/TFX_logo_2023.svg/200px-TFX_logo_2023.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"NRJ 12","category":"entertainment","subcategory":"france","country":"FR","language":"fr","url":"https://nrj12-hls-live.tf1.fr/nrj12/nrj12_hd/index.m3u8","logo":"https://upload.wikimedia.org/wikipedia/fr/thumb/8/87/NRJ12_logo_2023.svg/200px-NRJ12_logo_2023.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"Canal+","category":"entertainment","subcategory":"france","country":"FR","language":"fr","url":"https://canalplus-hls-live.tf1.fr/canalplus/canalplus_hd/index.m3u8","logo":"https://upload.wikimedia.org/wikipedia/fr/thumb/3/3e/Canal%2B_logo_2021.svg/200px-Canal%2B_logo_2021.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},

    # ===== SUISSE =====
    {"title":"RTS 1","category":"entertainment","subcategory":"suisse","country":"CH","language":"fr","url":"https://rtshls-rts1.akamaized.net/hls/live/2003422/rts1/master.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/a/a7/RTS_1_2011.svg/200px-RTS_1_2011.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"RTS 2","category":"entertainment","subcategory":"suisse","country":"CH","language":"fr","url":"https://rtshls-rts2.akamaized.net/hls/live/2003423/rts2/master.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/4/4b/RTS_2_2011.svg/200px-RTS_2_2011.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"RTS Info","category":"news","subcategory":"suisse","country":"CH","language":"fr","url":"https://rtshls-rtsinfo.akamaized.net/hls/live/2003424/rtsinfo/master.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/2/2c/RTS_Info_2011.svg/200px-RTS_Info_2011.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"SRF 1","category":"entertainment","subcategory":"suisse","country":"CH","language":"de","url":"https://srfhls-srf1.akamaized.net/hls/live/2003692/srf1/master.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/b/b1/SRF_1_logo.svg/200px-SRF_1_logo.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"SRF 2","category":"entertainment","subcategory":"suisse","country":"CH","language":"de","url":"https://srfhls-srf2.akamaized.net/hls/live/2003693/srf2/master.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/9/98/SRF_two_logo.svg/200px-SRF_two_logo.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"SRF info","category":"news","subcategory":"suisse","country":"CH","language":"de","url":"https://srfhls-srfinfo.akamaized.net/hls/live/2003694/srfinfo/master.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/4/47/SRF_info_logo.svg/200px-SRF_info_logo.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"RSI La 1","category":"entertainment","subcategory":"suisse","country":"CH","language":"it","url":"https://rsihls-rsila1.akamaized.net/hls/live/2003147/rsila1/master.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/1/1b/RSI_La_1_logo.svg/200px-RSI_La_1_logo.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"RSI La 2","category":"entertainment","subcategory":"suisse","country":"CH","language":"it","url":"https://rsihls-rsila2.akamaized.net/hls/live/2003148/rsila2/master.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/3/3b/RSI_La_2_logo.svg/200px-RSI_La_2_logo.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},

    # ===== BELGIQUE =====
    {"title":"RTBF La Une","category":"entertainment","subcategory":"belgique","country":"BE","language":"fr","url":"https://rtbflive.akamaized.net/hls/live/2039404/laune/index.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/e/e2/La_Une.svg/200px-La_Une.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"RTBF La Deux","category":"entertainment","subcategory":"belgique","country":"BE","language":"fr","url":"https://rtbflive.akamaized.net/hls/live/2039405/ladeux/index.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/1/1e/La_Deux.svg/200px-La_Deux.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"RTBF La Trois","category":"entertainment","subcategory":"belgique","country":"BE","language":"fr","url":"https://rtbflive.akamaized.net/hls/live/2039406/latrois/index.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/0/09/La_Trois_2012.svg/200px-La_Trois_2012.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"VRT 1","category":"entertainment","subcategory":"belgique","country":"BE","language":"nl","url":"https://live-vrt.akamaized.net/groupc/live/8edf470f-c9a7-4e7b-8a28-e6d3a0bdd820/live_aes.isml/.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/0/0a/Een_%28TV_channel%29_logo.png/200px-Een_%28TV_channel%29_logo.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"VRT Canvas","category":"entertainment","subcategory":"belgique","country":"BE","language":"nl","url":"https://live-vrt.akamaized.net/groupc/live/1efb1b6a-4bea-4687-802c-dad03a89db98/live_aes.isml/.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/8/8f/Canvas_logo_2019.svg/200px-Canvas_logo_2019.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"VRT Ketnet","category":"entertainment","subcategory":"jeune","country":"BE","language":"nl","url":"https://live-vrt.akamaized.net/groupc/live/4ef5179b-fb57-4df1-b58f-d64c0859e3ac/live_aes.isml/.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/5/5f/Ketnet_logo_2019.svg/200px-Ketnet_logo_2019.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},

    # ===== LUXEMBOURG =====
    {"title":"RTL Télé Lëtzebuerg","category":"entertainment","subcategory":"luxembourg","country":"LU","language":"lb","url":"https://otvlive.rtl.lu/crtp/fr/rtl-teiletu/amst/live/index.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/9/9c/RTL_T%C3%A9l%C3%A9_L%C3%ABtzebuerg_Logo.svg/200px-RTL_T%C3%A9l%C3%A9_L%C3%ABtzebuerg_Logo.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},

    # ===== CANADA =====
    {"title":"CBC News Network","category":"news","subcategory":"canada","country":"CA","language":"en","url":"https://cbclivedaily-1.akamaized.net/hls/live/2012226/cbc_news_network/master.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/0/0c/CBC_News_Network_2018_%28English%29.svg/200px-CBC_News_Network_2018_%28English%29.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"ICI RDI","category":"news","subcategory":"canada","country":"CA","language":"fr","url":"https://rcavlive.akamaized.net/hls/live/2006647/R-2O0000VA1P/master.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/3/39/ICI_R-D_I_logo.svg/200px-ICI_R-D_I_logo.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"TVA","category":"entertainment","subcategory":"canada","country":"CA","language":"fr","url":"https://tva.akamaized.net/hls/live/2006665/TVAHD/master.m3u8","logo":"https://upload.wikimedia.org/wikipedia/fr/thumb/f/fa/TVA_logo_2020.svg/200px-TVA_logo_2020.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},

    # ===== NASA =====
    {"title":"NASA TV Public","category":"science","subcategory":"espace","country":"US","language":"en","url":"https://ntv1.akamaized.net/hls/live/2014075/NASA-NTV1-HLS/master.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/e/e5/NASA_logo.svg/200px-NASA_logo.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"NASA TV Media","category":"science","subcategory":"espace","country":"US","language":"en","url":"https://ntv2.akamaized.net/hls/live/2014076/NASA-NTV2-HLS/master.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/e/e5/NASA_logo.svg/200px-NASA_logo.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"NASA TV UHD","category":"science","subcategory":"espace","country":"US","language":"en","url":"https://ntv3.akamaized.net/hls/live/2014077/NASA-NTV3-HLS/master.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/e/e5/NASA_logo.svg/200px-NASA_logo.svg.png","proxy_needed":False,"quality":"4K","stream_type":"hls"},

    # ===== SPORTS =====
    {"title":"Red Bull TV","category":"sports","subcategory":"extreme","country":"INT","language":"en","url":"https://rbmn-live.akamaized.net/hls/live/590964/BoRB-AT/master.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/a/a8/Red_Bull_logo.svg/200px-Red_Bull_logo.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"beIN Sports News","category":"sports","subcategory":"news","country":"QA","language":"en","url":"https://bein-sports-news-live.akamaized.net/hls/live/2031309/bein_sports_news/master.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/5/55/Bein_Sports_logo.svg/200px-Bein_Sports_logo.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"beIN Sports Français","category":"sports","subcategory":"news","country":"FR","language":"fr","url":"https://bein-sports-fr-live.akamaized.net/hls/live/2031310/bein_sports_fr/master.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/5/55/Bein_Sports_logo.svg/200px-Bein_Sports_logo.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"Eurosport 1","category":"sports","subcategory":"multisports","country":"INT","language":"en","url":"https://eurosport-hls-live.akamaized.net/hls/live/2031340/eurosport1/master.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/c/c2/Eurosport_1_logo.svg/200px-Eurosport_1_logo.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"Eurosport 2","category":"sports","subcategory":"multisports","country":"INT","language":"en","url":"https://eurosport-hls-live.akamaized.net/hls/live/2031341/eurosport2/master.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/4/48/Eurosport_2_logo.svg/200px-Eurosport_2_logo.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"Olympic Channel","category":"sports","subcategory":"olympique","country":"INT","language":"en","url":"https://olympic-channel.akamaized.net/hls/live/2016122/oc3/playlist.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/7/7c/Olympic_Channel_logo.svg/200px-Olympic_Channel_logo.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},

    # ===== NORDIQUES =====
    {"title":"NRK 1 (Norvège)","category":"entertainment","subcategory":"nordique","country":"NO","language":"no","url":"https://nrk-nrk1-la-hls-live.nrk-stream.no/nrk1_la_hls/index.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/d/d3/NRK1_logo.svg/200px-NRK1_logo.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"NRK 2 (Norvège)","category":"entertainment","subcategory":"nordique","country":"NO","language":"no","url":"https://nrk-nrk2-la-hls-live.nrk-stream.no/nrk2_la_hls/index.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/3/31/NRK2_logo.svg/200px-NRK2_logo.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"NRK Super (Norvège)","category":"entertainment","subcategory":"jeune","country":"NO","language":"no","url":"https://nrk-super-la-hls-live.nrk-stream.no/super_la_hls/index.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/5/5c/NRK_Super_logo.svg/200px-NRK_Super_logo.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"SVT 1 (Suède)","category":"entertainment","subcategory":"nordique","country":"SE","language":"sv","url":"https://svt-live.akamaized.net/hls/live/2028780/svt1/master.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/0/0b/SVT1_logo.svg/200px-SVT1_logo.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"SVT 2 (Suède)","category":"entertainment","subcategory":"nordique","country":"SE","language":"sv","url":"https://svt-live.akamaized.net/hls/live/2028781/svt2/master.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/a/ae/SVT2_logo.svg/200px-SVT2_logo.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"DR 1 (Danemark)","category":"entertainment","subcategory":"nordique","country":"DK","language":"da","url":"https://drlive01.akamaized.net/hls/live/2029524/dr1/master.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/4/40/DR1_logo.svg/200px-DR1_logo.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"DR 2 (Danemark)","category":"entertainment","subcategory":"nordique","country":"DK","language":"da","url":"https://drlive02.akamaized.net/hls/live/2029525/dr2/master.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/7/78/DR2_logo.svg/200px-DR2_logo.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"Yle TV1 (Finlande)","category":"entertainment","subcategory":"nordique","country":"FI","language":"fi","url":"https://yle-tv1.akamaized.net/hls/live/622365/yletv1/index.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/1/18/Yle_TV1.svg/200px-Yle_TV1.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"Yle TV2 (Finlande)","category":"entertainment","subcategory":"nordique","country":"FI","language":"fi","url":"https://yle-tv2.akamaized.net/hls/live/622366/yletv2/index.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/7/75/Yle_TV2.svg/200px-Yle_TV2.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"RÚV (Islande)","category":"entertainment","subcategory":"nordique","country":"IS","language":"is","url":"https://ruv-ruv-live.akamaized.net/hls/live/2004112/ruv/ruv/index.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/d/d2/RUV-logo.svg/200px-RUV-logo.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},

    # ===== PAYS-BAS =====
    {"title":"NPO 1 (Pays-Bas)","category":"entertainment","subcategory":"general","country":"NL","language":"nl","url":"https://npo-live.akamaized.net/hls/live/npo1/npo1/index.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/9/9d/NPO1_logo_2014.svg/200px-NPO1_logo_2014.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"NPO 2 (Pays-Bas)","category":"entertainment","subcategory":"general","country":"NL","language":"nl","url":"https://npo-live.akamaized.net/hls/live/npo2/npo2/index.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/1/15/NPO_2_logo_2014.svg/200px-NPO_2_logo_2014.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"NPO 3 (Pays-Bas)","category":"entertainment","subcategory":"general","country":"NL","language":"nl","url":"https://npo-live.akamaized.net/hls/live/npo3/npo3/index.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/7/7f/NPO_3_logo_2014.svg/200px-NPO_3_logo_2014.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},

    # ===== ALLEMAGNE =====
    {"title":"Das Erste (ARD)","category":"entertainment","subcategory":"general","country":"DE","language":"de","url":"https://mcdn.daserste.de/daserste/de/master.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/2/2f/Das_Erste_Logo_2019.svg/200px-Das_Erste_Logo_2019.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"ZDF","category":"entertainment","subcategory":"general","country":"DE","language":"de","url":"https://zdf-hls-18.akamaized.net/hls/live/2016502/de/geo/any/hd/master.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/6/6d/ZDF_Logo_2021.svg/200px-ZDF_Logo_2021.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"RTL","category":"entertainment","subcategory":"general","country":"DE","language":"de","url":"https://rtl-hls.akamaized.net/hls/live/2012020/rtl/master.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/6/66/RTL_Logo_2021.svg/200px-RTL_Logo_2021.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},

    # ===== AUTRICHE =====
    {"title":"ORF 1 (Autriche)","category":"entertainment","subcategory":"general","country":"AT","language":"de","url":"https://orf1ts.akamaized.net/hls/live/2012025/orf1/master.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/5/5e/ORF1_2019.svg/200px-ORF1_2019.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"ORF 2 (Autriche)","category":"entertainment","subcategory":"general","country":"AT","language":"de","url":"https://orf2ts.akamaized.net/hls/live/2012026/orf2/master.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/a/a9/ORF2_logo_2019.svg/200px-ORF2_logo_2019.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},

    # ===== RELIGION =====
    {"title":"KTO TV","category":"religion","subcategory":"catholique","country":"FR","language":"fr","url":"https://stream.ktotv.com/hls/live/ktotv/index.m3u8","logo":"https://upload.wikimedia.org/wikipedia/fr/thumb/1/14/KTO_tv_logo.svg/200px-KTO_tv_logo.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"EWTN English","category":"religion","subcategory":"catholique","country":"US","language":"en","url":"https://ewtn-lh.akamaihd.net/i/EWTN_AIS_001@393452/master.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/4/4f/EWTN_logo.svg/200px-EWTN_logo.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"EWTN Español","category":"religion","subcategory":"catholique","country":"US","language":"es","url":"https://ewtn-lh.akamaihd.net/i/EWTN_AIS_002@393453/master.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/4/4f/EWTN_logo.svg/200px-EWTN_logo.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"EWTN Français","category":"religion","subcategory":"catholique","country":"US","language":"fr","url":"https://ewtn-lh.akamaihd.net/i/EWTN_AIS_003@393454/master.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/4/4f/EWTN_logo.svg/200px-EWTN_logo.svg.png","proxy_needed":False,"quality":"HD","stream_type":"hls"},

    # ===== WEBCAM =====
    {"title":"EarthCam - Times Square NYC","category":"webcam","subcategory":"ville","country":"US","language":"none","url":"https://videos3.earthcam.com/fecnetwork/4.flv/playlist.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/a/a7/Camponotus_flavomarginatus_ant.jpg/200px-Camponotus_flavomarginatus_ant.jpg","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"EarthCam - Eiffel Tower","category":"webcam","subcategory":"ville","country":"FR","language":"none","url":"https://videos3.earthcam.com/fecnetwork/51.flv/playlist.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/a/a7/Camponotus_flavomarginatus_ant.jpg/200px-Camponotus_flavomarginatus_ant.jpg","proxy_needed":False,"quality":"HD","stream_type":"hls"},
    {"title":"EarthCam - London Eye","category":"webcam","subcategory":"ville","country":"GB","language":"none","url":"https://videos3.earthcam.com/fecnetwork/1001.flv/playlist.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/a/a7/Camponotus_flavomarginatus_ant.jpg/200px-Camponotus_flavomarginatus_ant.jpg","proxy_needed":False,"quality":"HD","stream_type":"hls"},

    # ===== RADIO =====
    {"title":"France Inter","category":"radio","subcategory":"general","country":"FR","language":"fr","url":"https://icecast.radiofrance.fr/franceinter-hifi.aac","logo":"https://upload.wikimedia.org/wikipedia/fr/thumb/2/25/France_Inter_logo_2021.svg/200px-France_Inter_logo_2021.svg.png","proxy_needed":False,"quality":"Audio","stream_type":"audio"},
    {"title":"France Culture","category":"radio","subcategory":"culture","country":"FR","language":"fr","url":"https://icecast.radiofrance.fr/franceculture-hifi.aac","logo":"https://upload.wikimedia.org/wikipedia/fr/thumb/b/b8/France_Culture_logo.svg/200px-France_Culture_logo.svg.png","proxy_needed":False,"quality":"Audio","stream_type":"audio"},
    {"title":"France Musique","category":"radio","subcategory":"musique","country":"FR","language":"fr","url":"https://icecast.radiofrance.fr/francemusique-hifi.aac","logo":"https://upload.wikimedia.org/wikipedia/fr/thumb/e/eb/France_Musique_logo_2021.svg/200px-France_Musique_logo_2021.svg.png","proxy_needed":False,"quality":"Audio","stream_type":"audio"},
    {"title":"France Info Radio","category":"radio","subcategory":"info","country":"FR","language":"fr","url":"https://icecast.radiofrance.fr/franceinfo-hifi.aac","logo":"https://upload.wikimedia.org/wikipedia/fr/thumb/6/6e/Franceinfo-logo-2016.svg/200px-Franceinfo-logo-2016.svg.png","proxy_needed":False,"quality":"Audio","stream_type":"audio"},
    {"title":"FIP","category":"radio","subcategory":"musique","country":"FR","language":"fr","url":"https://icecast.radiofrance.fr/fip-hifi.aac","logo":"https://upload.wikimedia.org/wikipedia/fr/thumb/5/56/FIP_Radio_logo.svg/200px-FIP_Radio_logo.svg.png","proxy_needed":False,"quality":"Audio","stream_type":"audio"},
    {"title":"France Bleu Paris","category":"radio","subcategory":"locale","country":"FR","language":"fr","url":"https://icecast.radiofrance.fr/fb1071-midfi.mp3","logo":"https://upload.wikimedia.org/wikipedia/fr/thumb/f/f7/France_Bleu_Logo_2015.svg/200px-France_Bleu_Logo_2015.svg.png","proxy_needed":False,"quality":"Audio","stream_type":"audio"},
    {"title":"RTL","category":"radio","subcategory":"general","country":"FR","language":"fr","url":"https://streamer-01.rtl.fr/rtl-1-44-128","logo":"https://upload.wikimedia.org/wikipedia/fr/thumb/a/ae/RTL_logo_2021.svg/200px-RTL_logo_2021.svg.png","proxy_needed":False,"quality":"Audio","stream_type":"audio"},
    {"title":"Europe 1","category":"radio","subcategory":"general","country":"FR","language":"fr","url":"https://stream.europe1.fr/europe1.aac","logo":"https://upload.wikimedia.org/wikipedia/fr/thumb/c/cd/Europe1_2022.svg/200px-Europe1_2022.svg.png","proxy_needed":False,"quality":"Audio","stream_type":"audio"},
    {"title":"RMC","category":"radio","subcategory":"info","country":"FR","language":"fr","url":"https://audio.bfmtv.com/rmcradio_128.mp3","logo":"https://upload.wikimedia.org/wikipedia/fr/thumb/f/f5/RMC_2022_%28rouge%29.svg/200px-RMC_2022_%28rouge%29.svg.png","proxy_needed":False,"quality":"Audio","stream_type":"audio"},
    {"title":"BBC Radio 1","category":"radio","subcategory":"musique","country":"GB","language":"en","url":"https://a.files.bbci.co.uk/media/live/manifesto/audio/simulcast/hls/uk/sbr_high/ak/bbc_radio_one.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/8/8b/BBC_Radio_1_%282021%29.svg/200px-BBC_Radio_1_%282021%29.svg.png","proxy_needed":False,"quality":"Audio","stream_type":"hls"},
    {"title":"BBC Radio 2","category":"radio","subcategory":"musique","country":"GB","language":"en","url":"https://a.files.bbci.co.uk/media/live/manifesto/audio/simulcast/hls/uk/sbr_high/ak/bbc_radio_two.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/3/3b/BBC_Radio_2_logo_2022.svg/200px-BBC_Radio_2_logo_2022.svg.png","proxy_needed":False,"quality":"Audio","stream_type":"hls"},
    {"title":"BBC Radio 3","category":"radio","subcategory":"musique","country":"GB","language":"en","url":"https://a.files.bbci.co.uk/media/live/manifesto/audio/simulcast/hls/uk/sbr_high/ak/bbc_radio_three.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/5/58/BBC_Radio_3_logo_2022.svg/200px-BBC_Radio_3_logo_2022.svg.png","proxy_needed":False,"quality":"Audio","stream_type":"hls"},
    {"title":"BBC Radio 4","category":"radio","subcategory":"general","country":"GB","language":"en","url":"https://a.files.bbci.co.uk/media/live/manifesto/audio/simulcast/hls/uk/sbr_high/ak/bbc_radio_fourfm.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/9/99/BBC_Radio_4_logo_2022.svg/200px-BBC_Radio_4_logo_2022.svg.png","proxy_needed":False,"quality":"Audio","stream_type":"hls"},
    {"title":"BBC World Service","category":"radio","subcategory":"international","country":"GB","language":"en","url":"https://a.files.bbci.co.uk/media/live/manifesto/audio/simulcast/hls/nonuk/sbr_low/ak/bbc_world_service.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/6/6b/BBC_World_Service.svg/200px-BBC_World_Service.svg.png","proxy_needed":False,"quality":"Audio","stream_type":"hls"},
    {"title":"NPR (USA)","category":"radio","subcategory":"info","country":"US","language":"en","url":"https://npr-ice.streamguys1.com/live.mp3","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/d/dc/NPR_logo_RGB.svg/200px-NPR_logo_RGB.svg.png","proxy_needed":False,"quality":"Audio","stream_type":"audio"},
    {"title":"Deutschlandfunk","category":"radio","subcategory":"info","country":"DE","language":"de","url":"https://st01.sslstream.dlf.de/dlf/01/high/aac/stream.aac","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/7/75/Deutschlandfunk_logo.svg/200px-Deutschlandfunk_logo.svg.png","proxy_needed":False,"quality":"Audio","stream_type":"audio"},
    {"title":"Deutschlandfunk Kultur","category":"radio","subcategory":"culture","country":"DE","language":"de","url":"https://st02.sslstream.dlf.de/dlf/02/high/aac/stream.aac","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/5/5a/Deutschlandfunk_Kultur_Logo.svg/200px-Deutschlandfunk_Kultur_Logo.svg.png","proxy_needed":False,"quality":"Audio","stream_type":"audio"},
    {"title":"RAI Radio 1","category":"radio","subcategory":"info","country":"IT","language":"it","url":"https://icestreaming.rai.it/1.mp3","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/2/23/RAI_Radio_1_-_Logo_2016.svg/200px-RAI_Radio_1_-_Logo_2016.svg.png","proxy_needed":False,"quality":"Audio","stream_type":"audio"},
    {"title":"RAI Radio 2","category":"radio","subcategory":"musique","country":"IT","language":"it","url":"https://icestreaming.rai.it/2.mp3","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/d/d6/RAI_Radio_2_-_Logo_2017.svg/200px-RAI_Radio_2_-_Logo_2017.svg.png","proxy_needed":False,"quality":"Audio","stream_type":"audio"},
    {"title":"RAI Radio 3","category":"radio","subcategory":"culture","country":"IT","language":"it","url":"https://icestreaming.rai.it/3.mp3","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/b/b9/RAI_Radio_3_-_Logo_2017.svg/200px-RAI_Radio_3_-_Logo_2017.svg.png","proxy_needed":False,"quality":"Audio","stream_type":"audio"},
    {"title":"RNE Radio Nacional (Espagne)","category":"radio","subcategory":"info","country":"ES","language":"es","url":"https://rne.rtveradio.cires21.com/rne_hc.mp3","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/a/aa/RNE_-_Radio_Nacional_de_Espa%C3%B1a_logo.png/200px-RNE_-_Radio_Nacional_de_Espa%C3%B1a_logo.png","proxy_needed":False,"quality":"Audio","stream_type":"audio"},
    {"title":"RNE Radio Clásica","category":"radio","subcategory":"musique","country":"ES","language":"es","url":"https://radioclasica.rtveradio.cires21.com/radioclasica_hc.mp3","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/e/e2/RNE_Radio_Cl%C3%A1sica_logo.png/200px-RNE_Radio_Cl%C3%A1sica_logo.png","proxy_needed":False,"quality":"Audio","stream_type":"audio"},
    {"title":"Radio Suisse Romande (RTS La 1ère)","category":"radio","subcategory":"info","country":"CH","language":"fr","url":"https://stream.srg-ssr.ch/m/la-1ere/mp3_128","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/9/9f/RTS_La_1%C3%A8re_2011.svg/200px-RTS_La_1%C3%A8re_2011.svg.png","proxy_needed":False,"quality":"Audio","stream_type":"audio"},
    {"title":"RTS Espace 2","category":"radio","subcategory":"culture","country":"CH","language":"fr","url":"https://stream.srg-ssr.ch/m/espace-2/mp3_128","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/c/c2/RTS_Espace_2_logo.svg/200px-RTS_Espace_2_logo.svg.png","proxy_needed":False,"quality":"Audio","stream_type":"audio"},
    {"title":"RTS Couleur 3","category":"radio","subcategory":"musique","country":"CH","language":"fr","url":"https://stream.srg-ssr.ch/m/couleur3/mp3_128","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/6/6e/RTS_Couleur_3_logo.svg/200px-RTS_Couleur_3_logo.svg.png","proxy_needed":False,"quality":"Audio","stream_type":"audio"},
    {"title":"RTS Option Musique","category":"radio","subcategory":"musique","country":"CH","language":"fr","url":"https://stream.srg-ssr.ch/m/option-musique/mp3_128","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/7/7d/RTS_Option_Musique_logo.svg/200px-RTS_Option_Musique_logo.svg.png","proxy_needed":False,"quality":"Audio","stream_type":"audio"},
    {"title":"RTBF La Première","category":"radio","subcategory":"info","country":"BE","language":"fr","url":"https://radios.rtbf.be/lapremiere-128.mp3","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/b/b9/La_Premi%C3%A8re_%28RTBF%29_Logo_2019.svg/200px-La_Premi%C3%A8re_%28RTBF%29_Logo_2019.svg.png","proxy_needed":False,"quality":"Audio","stream_type":"audio"},
    {"title":"RTBF Classic 21","category":"radio","subcategory":"musique","country":"BE","language":"fr","url":"https://radios.rtbf.be/classic21-128.mp3","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/c/c9/Classic_21_2019.svg/200px-Classic_21_2019.svg.png","proxy_needed":False,"quality":"Audio","stream_type":"audio"},
    {"title":"RTBF Musiq'3","category":"radio","subcategory":"musique","country":"BE","language":"fr","url":"https://radios.rtbf.be/musiq3-128.mp3","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/1/1d/Musiq3_logo_2019.svg/200px-Musiq3_logo_2019.svg.png","proxy_needed":False,"quality":"Audio","stream_type":"audio"},
    {"title":"Radio Canada Première","category":"radio","subcategory":"info","country":"CA","language":"fr","url":"https://rcavlive.akamaized.net/hls/live/2006639/P-2O0000VM1P/master.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/3/3d/Radio-Canada_Premi%C3%A8re_new_logo.png/200px-Radio-Canada_Premi%C3%A8re_new_logo.png","proxy_needed":False,"quality":"Audio","stream_type":"hls"},
    {"title":"ICI Musique","category":"radio","subcategory":"musique","country":"CA","language":"fr","url":"https://rcavlive.akamaized.net/hls/live/2006644/P-2O0000VK1P/master.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/4/4b/ICI_Musique_logo.png/200px-ICI_Musique_logo.png","proxy_needed":False,"quality":"Audio","stream_type":"hls"},
    {"title":"CBC Radio One (Canada)","category":"radio","subcategory":"info","country":"CA","language":"en","url":"https://cbcliveradio-lh.akamaihd.net/i/CBCR1_TOR@35348/index_96_a-p.m3u8","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/4/4c/CBC_Radio_One.svg/200px-CBC_Radio_One.svg.png","proxy_needed":False,"quality":"Audio","stream_type":"hls"},

    # ===== YOUTUBE LIVE PUBLIC =====
    {"title":"DW News (YouTube Live)","category":"news","subcategory":"youtube","country":"DE","language":"en","url":"https://www.youtube.com/watch?v=G39SM98MBhc","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/7/75/Deutsche_Welle_symbol_2012.svg/200px-Deutsche_Welle_symbol_2012.svg.png","proxy_needed":False,"quality":"HD","stream_type":"youtube"},
    {"title":"Al Jazeera English (YouTube Live)","category":"news","subcategory":"youtube","country":"QA","language":"en","url":"https://www.youtube.com/watch?v=KQKGKnP7xWs","logo":"https://upload.wikimedia.org/wikipedia/en/thumb/f/f2/Aljazeera_eng.svg/200px-Aljazeera_eng.svg.png","proxy_needed":False,"quality":"HD","stream_type":"youtube"},
    {"title":"France 24 (YouTube Live)","category":"news","subcategory":"youtube","country":"FR","language":"fr","url":"https://www.youtube.com/watch?v=h3MuIUNCCLI","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/a/a8/France_24_logo.svg/200px-France_24_logo.svg.png","proxy_needed":False,"quality":"HD","stream_type":"youtube"},
    {"title":"Euronews (YouTube Live)","category":"news","subcategory":"youtube","country":"EU","language":"fr","url":"https://www.youtube.com/watch?v=0GGFCwMFsdE","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/2/25/Euronews_logo_2021.svg/200px-Euronews_logo_2021.svg.png","proxy_needed":False,"quality":"HD","stream_type":"youtube"},
    {"title":"NASA TV (YouTube Live)","category":"science","subcategory":"youtube","country":"US","language":"en","url":"https://www.youtube.com/watch?v=21X5lGlDOfg","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/e/e5/NASA_logo.svg/200px-NASA_logo.svg.png","proxy_needed":False,"quality":"HD","stream_type":"youtube"},
    {"title":"RT Documentary (YouTube Live)","category":"entertainment","subcategory":"youtube","country":"INT","language":"en","url":"https://www.youtube.com/watch?v=sysqRIrAWbg","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/5/5d/RT_logo_new.svg/200px-RT_logo_new.svg.png","proxy_needed":False,"quality":"HD","stream_type":"youtube"},
    {"title":"Bloomberg (YouTube Live)","category":"news","subcategory":"youtube","country":"US","language":"en","url":"https://www.youtube.com/watch?v=dp8PhLsUcFE","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/5/5d/New_Bloomberg_Logo.svg/200px-New_Bloomberg_Logo.svg.png","proxy_needed":False,"quality":"HD","stream_type":"youtube"},
    {"title":"beIN Sports Arabic (YouTube Live)","category":"sports","subcategory":"youtube","country":"QA","language":"ar","url":"https://www.youtube.com/watch?v=YC0kNJGZ8Ug","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/5/55/Bein_Sports_logo.svg/200px-Bein_Sports_logo.svg.png","proxy_needed":False,"quality":"HD","stream_type":"youtube"},
    {"title":"CGTN (YouTube Live)","category":"news","subcategory":"youtube","country":"CN","language":"en","url":"https://www.youtube.com/watch?v=VYbLhR-1K-Q","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/a/a7/CGTN_Logo.svg/200px-CGTN_Logo.svg.png","proxy_needed":False,"quality":"HD","stream_type":"youtube"},
    {"title":"Sky News (YouTube Live)","category":"news","subcategory":"youtube","country":"GB","language":"en","url":"https://www.youtube.com/watch?v=9Auq9mYxFEE","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/9/94/Sky_News_logo.svg/200px-Sky_News_logo.svg.png","proxy_needed":False,"quality":"HD","stream_type":"youtube"},
    {"title":"CBC News (YouTube Live)","category":"news","subcategory":"youtube","country":"CA","language":"en","url":"https://www.youtube.com/watch?v=Qi58fJ6n2gQ","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/0/0c/CBC_News_Network_2018_%28English%29.svg/200px-CBC_News_Network_2018_%28English%29.svg.png","proxy_needed":False,"quality":"HD","stream_type":"youtube"},
    {"title":"RT France (YouTube Live)","category":"news","subcategory":"youtube","country":"FR","language":"fr","url":"https://www.youtube.com/watch?v=k1o7lR9e58c","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/5/5d/RT_logo_new.svg/200px-RT_logo_new.svg.png","proxy_needed":False,"quality":"HD","stream_type":"youtube"},
    {"title":"Red Bull (YouTube Live)","category":"sports","subcategory":"youtube","country":"INT","language":"en","url":"https://www.youtube.com/watch?v=4LdD2tHfyDc","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/a/a8/Red_Bull_logo.svg/200px-Red_Bull_logo.svg.png","proxy_needed":False,"quality":"HD","stream_type":"youtube"},
    {"title":"NASA Johnson (YouTube Live)","category":"science","subcategory":"youtube","country":"US","language":"en","url":"https://www.youtube.com/watch?v=FL6eIV2GtMM","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/e/e5/NASA_logo.svg/200px-NASA_logo.svg.png","proxy_needed":False,"quality":"HD","stream_type":"youtube"},
    {"title":"SpaceX (YouTube Live)","category":"science","subcategory":"youtube","country":"US","language":"en","url":"https://www.youtube.com/watch?v=5s4A_siD4Wc","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/d/de/SpaceX-Logo.svg/200px-SpaceX-Logo.svg.png","proxy_needed":False,"quality":"HD","stream_type":"youtube"},
    {"title":"BBC News (YouTube Live)","category":"news","subcategory":"youtube","country":"GB","language":"en","url":"https://www.youtube.com/watch?v=16y1AkoZkmQ","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/6/6b/BBC_World_News_logo.svg/200px-BBC_World_News_logo.svg.png","proxy_needed":False,"quality":"HD","stream_type":"youtube"},
    {"title":"CNN (YouTube Live)","category":"news","subcategory":"youtube","country":"US","language":"en","url":"https://www.youtube.com/watch?v=OS7M88r8H5w","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/b/b1/CNN_International_logo.svg/200px-CNN_International_logo.svg.png","proxy_needed":False,"quality":"HD","stream_type":"youtube"},
    {"title":"ABC News (YouTube Live)","category":"news","subcategory":"youtube","country":"US","language":"en","url":"https://www.youtube.com/watch?v=w_Ma8oQLmSM","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/0/05/ABC_News_logo_2021.svg/200px-ABC_News_logo_2021.svg.png","proxy_needed":False,"quality":"HD","stream_type":"youtube"},
    {"title":"Fox News (YouTube Live)","category":"news","subcategory":"youtube","country":"US","language":"en","url":"https://www.youtube.com/watch?v=8dOG7dYqF2c","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/6/67/Fox_News_Channel_logo.svg/200px-Fox_News_Channel_logo.svg.png","proxy_needed":False,"quality":"HD","stream_type":"youtube"},
    {"title":"NBC News (YouTube Live)","category":"news","subcategory":"youtube","country":"US","language":"en","url":"https://www.youtube.com/watch?v=mlbC2h9E2hQ","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/2/21/NBC_News_2013_logo.svg/200px-NBC_News_2013_logo.svg.png","proxy_needed":False,"quality":"HD","stream_type":"youtube"},
    {"title":"CBS News (YouTube Live)","category":"news","subcategory":"youtube","country":"US","language":"en","url":"https://www.youtube.com/watch?v=YA9rLrR7dLM","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/5/58/CBS_News_logo_2020.svg/200px-CBS_News_logo_2020.svg.png","proxy_needed":False,"quality":"HD","stream_type":"youtube"},
    {"title":"Global News (YouTube Live)","category":"news","subcategory":"youtube","country":"CA","language":"en","url":"https://www.youtube.com/watch?v=VdmY7Y61D1Q","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/9/92/Global_News_logo.svg/200px-Global_News_logo.svg.png","proxy_needed":False,"quality":"HD","stream_type":"youtube"},
    {"title":"CP24 (YouTube Live)","category":"news","subcategory":"youtube","country":"CA","language":"en","url":"https://www.youtube.com/watch?v=W13GkOXc1MU","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/3/3e/CP24_logo.svg/200px-CP24_logo.svg.png","proxy_needed":False,"quality":"HD","stream_type":"youtube"},
    {"title":"TVO (YouTube Live)","category":"entertainment","subcategory":"youtube","country":"CA","language":"en","url":"https://www.youtube.com/watch?v=HyO06aGz6Nk","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/f/f9/TVO_logo.svg/200px-TVO_logo.svg.png","proxy_needed":False,"quality":"HD","stream_type":"youtube"},
    {"title":"RTS (YouTube Live)","category":"entertainment","subcategory":"youtube","country":"CH","language":"fr","url":"https://www.youtube.com/watch?v=9Eo9mI0L0-s","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/7/7f/RTS_logo_2019.svg/200px-RTS_logo_2019.svg.png","proxy_needed":False,"quality":"HD","stream_type":"youtube"},
    {"title":"RTBF (YouTube Live)","category":"entertainment","subcategory":"youtube","country":"BE","language":"fr","url":"https://www.youtube.com/watch?v=0cJtFpXXwMc","logo":"https://upload.wikimedia.org/wikipedia/commons/thumb/9/95/RTBF_2019_logo.svg/200px-RTBF_2019_logo.svg.png","proxy_needed":False,"quality":"HD","stream_type":"youtube"},
]

# ==================== PLAYLISTS IPTV.ORG COMPLÈTES ====================

IPTV_PLAYLISTS = [
    # ---- INDEX MONDIAL ----
    {"name":"index","display_name":"🌍 Toutes les chaînes (Monde)","url":f"{settings.IPTV_BASE_URL}/index.m3u","category":"iptv","country":"INT","playlist_type":"category"},

    # ---- PAYS (environ 200 pays) ----
    {"name":"france","display_name":"🇫🇷 France","url":f"{settings.IPTV_BASE_URL}/countries/fr.m3u","category":"iptv","country":"FR","playlist_type":"country"},
    {"name":"canada","display_name":"🇨🇦 Canada","url":f"{settings.IPTV_BASE_URL}/countries/ca.m3u","category":"iptv","country":"CA","playlist_type":"country"},
    {"name":"belgique","display_name":"🇧🇪 Belgique","url":f"{settings.IPTV_BASE_URL}/countries/be.m3u","category":"iptv","country":"BE","playlist_type":"country"},
    {"name":"suisse","display_name":"🇨🇭 Suisse","url":f"{settings.IPTV_BASE_URL}/countries/ch.m3u","category":"iptv","country":"CH","playlist_type":"country"},
    {"name":"luxembourg","display_name":"🇱🇺 Luxembourg","url":f"{settings.IPTV_BASE_URL}/countries/lu.m3u","category":"iptv","country":"LU","playlist_type":"country"},
    {"name":"monaco","display_name":"🇲🇨 Monaco","url":f"{settings.IPTV_BASE_URL}/countries/mc.m3u","category":"iptv","country":"MC","playlist_type":"country"},
    {"name":"maroc","display_name":"🇲🇦 Maroc","url":f"{settings.IPTV_BASE_URL}/countries/ma.m3u","category":"iptv","country":"MA","playlist_type":"country"},
    {"name":"algerie","display_name":"🇩🇿 Algérie","url":f"{settings.IPTV_BASE_URL}/countries/dz.m3u","category":"iptv","country":"DZ","playlist_type":"country"},
    {"name":"tunisie","display_name":"🇹🇳 Tunisie","url":f"{settings.IPTV_BASE_URL}/countries/tn.m3u","category":"iptv","country":"TN","playlist_type":"country"},
    {"name":"senegal","display_name":"🇸🇳 Sénégal","url":f"{settings.IPTV_BASE_URL}/countries/sn.m3u","category":"iptv","country":"SN","playlist_type":"country"},
    {"name":"cote_ivoire","display_name":"🇨🇮 Côte d'Ivoire","url":f"{settings.IPTV_BASE_URL}/countries/ci.m3u","category":"iptv","country":"CI","playlist_type":"country"},
    {"name":"cameroun","display_name":"🇨🇲 Cameroun","url":f"{settings.IPTV_BASE_URL}/countries/cm.m3u","category":"iptv","country":"CM","playlist_type":"country"},
    {"name":"mali","display_name":"🇲🇱 Mali","url":f"{settings.IPTV_BASE_URL}/countries/ml.m3u","category":"iptv","country":"ML","playlist_type":"country"},
    {"name":"congo","display_name":"🇨🇩 Congo RDC","url":f"{settings.IPTV_BASE_URL}/countries/cd.m3u","category":"iptv","country":"CD","playlist_type":"country"},
    {"name":"congo_brazzaville","display_name":"🇨🇬 Congo-Brazzaville","url":f"{settings.IPTV_BASE_URL}/countries/cg.m3u","category":"iptv","country":"CG","playlist_type":"country"},
    {"name":"burkina_faso","display_name":"🇧🇫 Burkina Faso","url":f"{settings.IPTV_BASE_URL}/countries/bf.m3u","category":"iptv","country":"BF","playlist_type":"country"},
    {"name":"niger","display_name":"🇳🇪 Niger","url":f"{settings.IPTV_BASE_URL}/countries/ne.m3u","category":"iptv","country":"NE","playlist_type":"country"},
    {"name":"tchad","display_name":"🇹🇩 Tchad","url":f"{settings.IPTV_BASE_URL}/countries/td.m3u","category":"iptv","country":"TD","playlist_type":"country"},
    {"name":"gabon","display_name":"🇬🇦 Gabon","url":f"{settings.IPTV_BASE_URL}/countries/ga.m3u","category":"iptv","country":"GA","playlist_type":"country"},
    {"name":"guinee","display_name":"🇬🇳 Guinée","url":f"{settings.IPTV_BASE_URL}/countries/gn.m3u","category":"iptv","country":"GN","playlist_type":"country"},
    {"name":"benin","display_name":"🇧🇯 Bénin","url":f"{settings.IPTV_BASE_URL}/countries/bj.m3u","category":"iptv","country":"BJ","playlist_type":"country"},
    {"name":"togo","display_name":"🇹🇬 Togo","url":f"{settings.IPTV_BASE_URL}/countries/tg.m3u","category":"iptv","country":"TG","playlist_type":"country"},
    {"name":"mauritanie","display_name":"🇲🇷 Mauritanie","url":f"{settings.IPTV_BASE_URL}/countries/mr.m3u","category":"iptv","country":"MR","playlist_type":"country"},
    {"name":"libye","display_name":"🇱🇾 Libye","url":f"{settings.IPTV_BASE_URL}/countries/ly.m3u","category":"iptv","country":"LY","playlist_type":"country"},
    {"name":"egypte","display_name":"🇪🇬 Égypte","url":f"{settings.IPTV_BASE_URL}/countries/eg.m3u","category":"iptv","country":"EG","playlist_type":"country"},
    {"name":"arabie_saoudite","display_name":"🇸🇦 Arabie Saoudite","url":f"{settings.IPTV_BASE_URL}/countries/sa.m3u","category":"iptv","country":"SA","playlist_type":"country"},
    {"name":"emirats_arabes_unis","display_name":"🇦🇪 Émirats Arabes Unis","url":f"{settings.IPTV_BASE_URL}/countries/ae.m3u","category":"iptv","country":"AE","playlist_type":"country"},
    {"name":"qatar","display_name":"🇶🇦 Qatar","url":f"{settings.IPTV_BASE_URL}/countries/qa.m3u","category":"iptv","country":"QA","playlist_type":"country"},
    {"name":"koweit","display_name":"🇰🇼 Koweït","url":f"{settings.IPTV_BASE_URL}/countries/kw.m3u","category":"iptv","country":"KW","playlist_type":"country"},
    {"name":"bahrein","display_name":"🇧🇭 Bahreïn","url":f"{settings.IPTV_BASE_URL}/countries/bh.m3u","category":"iptv","country":"BH","playlist_type":"country"},
    {"name":"oman","display_name":"🇴🇲 Oman","url":f"{settings.IPTV_BASE_URL}/countries/om.m3u","category":"iptv","country":"OM","playlist_type":"country"},
    {"name":"jordanie","display_name":"🇯🇴 Jordanie","url":f"{settings.IPTV_BASE_URL}/countries/jo.m3u","category":"iptv","country":"JO","playlist_type":"country"},
    {"name":"irak","display_name":"🇮🇶 Irak","url":f"{settings.IPTV_BASE_URL}/countries/iq.m3u","category":"iptv","country":"IQ","playlist_type":"country"},
    {"name":"iran","display_name":"🇮🇷 Iran","url":f"{settings.IPTV_BASE_URL}/countries/ir.m3u","category":"iptv","country":"IR","playlist_type":"country"},
    {"name":"syrie","display_name":"🇸🇾 Syrie","url":f"{settings.IPTV_BASE_URL}/countries/sy.m3u","category":"iptv","country":"SY","playlist_type":"country"},
    {"name":"liban","display_name":"🇱🇧 Liban","url":f"{settings.IPTV_BASE_URL}/countries/lb.m3u","category":"iptv","country":"LB","playlist_type":"country"},
    {"name":"israel","display_name":"🇮🇱 Israël","url":f"{settings.IPTV_BASE_URL}/countries/il.m3u","category":"iptv","country":"IL","playlist_type":"country"},
    {"name":"palestine","display_name":"🇵🇸 Palestine","url":f"{settings.IPTV_BASE_URL}/countries/ps.m3u","category":"iptv","country":"PS","playlist_type":"country"},
    {"name":"turquie","display_name":"🇹🇷 Turquie","url":f"{settings.IPTV_BASE_URL}/countries/tr.m3u","category":"iptv","country":"TR","playlist_type":"country"},
    {"name":"etats_unis","display_name":"🇺🇸 États-Unis","url":f"{settings.IPTV_BASE_URL}/countries/us.m3u","category":"iptv","country":"US","playlist_type":"country"},
    {"name":"royaume_uni","display_name":"🇬🇧 Royaume-Uni","url":f"{settings.IPTV_BASE_URL}/countries/gb.m3u","category":"iptv","country":"GB","playlist_type":"country"},
    {"name":"allemagne","display_name":"🇩🇪 Allemagne","url":f"{settings.IPTV_BASE_URL}/countries/de.m3u","category":"iptv","country":"DE","playlist_type":"country"},
    {"name":"espagne","display_name":"🇪🇸 Espagne","url":f"{settings.IPTV_BASE_URL}/countries/es.m3u","category":"iptv","country":"ES","playlist_type":"country"},
    {"name":"italie","display_name":"🇮🇹 Italie","url":f"{settings.IPTV_BASE_URL}/countries/it.m3u","category":"iptv","country":"IT","playlist_type":"country"},
    {"name":"portugal","display_name":"🇵🇹 Portugal","url":f"{settings.IPTV_BASE_URL}/countries/pt.m3u","category":"iptv","country":"PT","playlist_type":"country"},
    {"name":"pays_bas","display_name":"🇳🇱 Pays-Bas","url":f"{settings.IPTV_BASE_URL}/countries/nl.m3u","category":"iptv","country":"NL","playlist_type":"country"},
    {"name":"russie","display_name":"🇷🇺 Russie","url":f"{settings.IPTV_BASE_URL}/countries/ru.m3u","category":"iptv","country":"RU","playlist_type":"country"},
    {"name":"pologne","display_name":"🇵🇱 Pologne","url":f"{settings.IPTV_BASE_URL}/countries/pl.m3u","category":"iptv","country":"PL","playlist_type":"country"},
    {"name":"ukraine","display_name":"🇺🇦 Ukraine","url":f"{settings.IPTV_BASE_URL}/countries/ua.m3u","category":"iptv","country":"UA","playlist_type":"country"},
    {"name":"roumanie","display_name":"🇷🇴 Roumanie","url":f"{settings.IPTV_BASE_URL}/countries/ro.m3u","category":"iptv","country":"RO","playlist_type":"country"},
    {"name":"bulgarie","display_name":"🇧🇬 Bulgarie","url":f"{settings.IPTV_BASE_URL}/countries/bg.m3u","category":"iptv","country":"BG","playlist_type":"country"},
    {"name":"serbie","display_name":"🇷🇸 Serbie","url":f"{settings.IPTV_BASE_URL}/countries/rs.m3u","category":"iptv","country":"RS","playlist_type":"country"},
    {"name":"croatie","display_name":"🇭🇷 Croatie","url":f"{settings.IPTV_BASE_URL}/countries/hr.m3u","category":"iptv","country":"HR","playlist_type":"country"},
    {"name":"slovenie","display_name":"🇸🇮 Slovénie","url":f"{settings.IPTV_BASE_URL}/countries/si.m3u","category":"iptv","country":"SI","playlist_type":"country"},
    {"name":"slovaquie","display_name":"🇸🇰 Slovaquie","url":f"{settings.IPTV_BASE_URL}/countries/sk.m3u","category":"iptv","country":"SK","playlist_type":"country"},
    {"name":"tchequie","display_name":"🇨🇿 Tchéquie","url":f"{settings.IPTV_BASE_URL}/countries/cz.m3u","category":"iptv","country":"CZ","playlist_type":"country"},
    {"name":"hongrie","display_name":"🇭🇺 Hongrie","url":f"{settings.IPTV_BASE_URL}/countries/hu.m3u","category":"iptv","country":"HU","playlist_type":"country"},
    {"name":"autriche","display_name":"🇦🇹 Autriche","url":f"{settings.IPTV_BASE_URL}/countries/at.m3u","category":"iptv","country":"AT","playlist_type":"country"},
    {"name":"grece","display_name":"🇬🇷 Grèce","url":f"{settings.IPTV_BASE_URL}/countries/gr.m3u","category":"iptv","country":"GR","playlist_type":"country"},
    {"name":"chypre","display_name":"🇨🇾 Chypre","url":f"{settings.IPTV_BASE_URL}/countries/cy.m3u","category":"iptv","country":"CY","playlist_type":"country"},
    {"name":"malte","display_name":"🇲🇹 Malte","url":f"{settings.IPTV_BASE_URL}/countries/mt.m3u","category":"iptv","country":"MT","playlist_type":"country"},
    {"name":"islande","display_name":"🇮🇸 Islande","url":f"{settings.IPTV_BASE_URL}/countries/is.m3u","category":"iptv","country":"IS","playlist_type":"country"},
    {"name":"norvege","display_name":"🇳🇴 Norvège","url":f"{settings.IPTV_BASE_URL}/countries/no.m3u","category":"iptv","country":"NO","playlist_type":"country"},
    {"name":"suede","display_name":"🇸🇪 Suède","url":f"{settings.IPTV_BASE_URL}/countries/se.m3u","category":"iptv","country":"SE","playlist_type":"country"},
    {"name":"finlande","display_name":"🇫🇮 Finlande","url":f"{settings.IPTV_BASE_URL}/countries/fi.m3u","category":"iptv","country":"FI","playlist_type":"country"},
    {"name":"danemark","display_name":"🇩🇰 Danemark","url":f"{settings.IPTV_BASE_URL}/countries/dk.m3u","category":"iptv","country":"DK","playlist_type":"country"},
    {"name":"irlande","display_name":"🇮🇪 Irlande","url":f"{settings.IPTV_BASE_URL}/countries/ie.m3u","category":"iptv","country":"IE","playlist_type":"country"},
    {"name":"bresil","display_name":"🇧🇷 Brésil","url":f"{settings.IPTV_BASE_URL}/countries/br.m3u","category":"iptv","country":"BR","playlist_type":"country"},
    {"name":"mexique","display_name":"🇲🇽 Mexique","url":f"{settings.IPTV_BASE_URL}/countries/mx.m3u","category":"iptv","country":"MX","playlist_type":"country"},
    {"name":"argentine","display_name":"🇦🇷 Argentine","url":f"{settings.IPTV_BASE_URL}/countries/ar.m3u","category":"iptv","country":"AR","playlist_type":"country"},
    {"name":"colombie","display_name":"🇨🇴 Colombie","url":f"{settings.IPTV_BASE_URL}/countries/co.m3u","category":"iptv","country":"CO","playlist_type":"country"},
    {"name":"chili","display_name":"🇨🇱 Chili","url":f"{settings.IPTV_BASE_URL}/countries/cl.m3u","category":"iptv","country":"CL","playlist_type":"country"},
    {"name":"perou","display_name":"🇵🇪 Pérou","url":f"{settings.IPTV_BASE_URL}/countries/pe.m3u","category":"iptv","country":"PE","playlist_type":"country"},
    {"name":"venezuela","display_name":"🇻🇪 Venezuela","url":f"{settings.IPTV_BASE_URL}/countries/ve.m3u","category":"iptv","country":"VE","playlist_type":"country"},
    {"name":"equateur","display_name":"🇪🇨 Équateur","url":f"{settings.IPTV_BASE_URL}/countries/ec.m3u","category":"iptv","country":"EC","playlist_type":"country"},
    {"name":"bolivie","display_name":"🇧🇴 Bolivie","url":f"{settings.IPTV_BASE_URL}/countries/bo.m3u","category":"iptv","country":"BO","playlist_type":"country"},
    {"name":"paraguay","display_name":"🇵🇾 Paraguay","url":f"{settings.IPTV_BASE_URL}/countries/py.m3u","category":"iptv","country":"PY","playlist_type":"country"},
    {"name":"uruguay","display_name":"🇺🇾 Uruguay","url":f"{settings.IPTV_BASE_URL}/countries/uy.m3u","category":"iptv","country":"UY","playlist_type":"country"},
    {"name":"chine","display_name":"🇨🇳 Chine","url":f"{settings.IPTV_BASE_URL}/countries/cn.m3u","category":"iptv","country":"CN","playlist_type":"country"},
    {"name":"japon","display_name":"🇯🇵 Japon","url":f"{settings.IPTV_BASE_URL}/countries/jp.m3u","category":"iptv","country":"JP","playlist_type":"country"},
    {"name":"coree_sud","display_name":"🇰🇷 Corée du Sud","url":f"{settings.IPTV_BASE_URL}/countries/kr.m3u","category":"iptv","country":"KR","playlist_type":"country"},
    {"name":"inde","display_name":"🇮🇳 Inde","url":f"{settings.IPTV_BASE_URL}/countries/in.m3u","category":"iptv","country":"IN","playlist_type":"country"},
    {"name":"pakistan","display_name":"🇵🇰 Pakistan","url":f"{settings.IPTV_BASE_URL}/countries/pk.m3u","category":"iptv","country":"PK","playlist_type":"country"},
    {"name":"bangladesh","display_name":"🇧🇩 Bangladesh","url":f"{settings.IPTV_BASE_URL}/countries/bd.m3u","category":"iptv","country":"BD","playlist_type":"country"},
    {"name":"indonesie","display_name":"🇮🇩 Indonésie","url":f"{settings.IPTV_BASE_URL}/countries/id.m3u","category":"iptv","country":"ID","playlist_type":"country"},
    {"name":"malaisie","display_name":"🇲🇾 Malaisie","url":f"{settings.IPTV_BASE_URL}/countries/my.m3u","category":"iptv","country":"MY","playlist_type":"country"},
    {"name":"singapour","display_name":"🇸🇬 Singapour","url":f"{settings.IPTV_BASE_URL}/countries/sg.m3u","category":"iptv","country":"SG","playlist_type":"country"},
    {"name":"philippines","display_name":"🇵🇭 Philippines","url":f"{settings.IPTV_BASE_URL}/countries/ph.m3u","category":"iptv","country":"PH","playlist_type":"country"},
    {"name":"vietnam","display_name":"🇻🇳 Vietnam","url":f"{settings.IPTV_BASE_URL}/countries/vn.m3u","category":"iptv","country":"VN","playlist_type":"country"},
    {"name":"thailande","display_name":"🇹🇭 Thaïlande","url":f"{settings.IPTV_BASE_URL}/countries/th.m3u","category":"iptv","country":"TH","playlist_type":"country"},
    {"name":"birmanie","display_name":"🇲🇲 Birmanie","url":f"{settings.IPTV_BASE_URL}/countries/mm.m3u","category":"iptv","country":"MM","playlist_type":"country"},
    {"name":"cambodge","display_name":"🇰🇭 Cambodge","url":f"{settings.IPTV_BASE_URL}/countries/kh.m3u","category":"iptv","country":"KH","playlist_type":"country"},
    {"name":"laos","display_name":"🇱🇦 Laos","url":f"{settings.IPTV_BASE_URL}/countries/la.m3u","category":"iptv","country":"LA","playlist_type":"country"},
    {"name":"nepal","display_name":"🇳🇵 Népal","url":f"{settings.IPTV_BASE_URL}/countries/np.m3u","category":"iptv","country":"NP","playlist_type":"country"},
    {"name":"sri_lanka","display_name":"🇱🇰 Sri Lanka","url":f"{settings.IPTV_BASE_URL}/countries/lk.m3u","category":"iptv","country":"LK","playlist_type":"country"},
    {"name":"afghanistan","display_name":"🇦🇫 Afghanistan","url":f"{settings.IPTV_BASE_URL}/countries/af.m3u","category":"iptv","country":"AF","playlist_type":"country"},
    {"name":"kazakhstan","display_name":"🇰🇿 Kazakhstan","url":f"{settings.IPTV_BASE_URL}/countries/kz.m3u","category":"iptv","country":"KZ","playlist_type":"country"},
    {"name":"ouzbekistan","display_name":"🇺🇿 Ouzbékistan","url":f"{settings.IPTV_BASE_URL}/countries/uz.m3u","category":"iptv","country":"UZ","playlist_type":"country"},
    {"name":"tadjikistan","display_name":"🇹🇯 Tadjikistan","url":f"{settings.IPTV_BASE_URL}/countries/tj.m3u","category":"iptv","country":"TJ","playlist_type":"country"},
    {"name":"kirghizistan","display_name":"🇰🇬 Kirghizistan","url":f"{settings.IPTV_BASE_URL}/countries/kg.m3u","category":"iptv","country":"KG","playlist_type":"country"},
    {"name":"turkmenistan","display_name":"🇹🇲 Turkménistan","url":f"{settings.IPTV_BASE_URL}/countries/tm.m3u","category":"iptv","country":"TM","playlist_type":"country"},
    {"name":"georgie","display_name":"🇬🇪 Géorgie","url":f"{settings.IPTV_BASE_URL}/countries/ge.m3u","category":"iptv","country":"GE","playlist_type":"country"},
    {"name":"armenie","display_name":"🇦🇲 Arménie","url":f"{settings.IPTV_BASE_URL}/countries/am.m3u","category":"iptv","country":"AM","playlist_type":"country"},
    {"name":"azerbaidjan","display_name":"🇦🇿 Azerbaïdjan","url":f"{settings.IPTV_BASE_URL}/countries/az.m3u","category":"iptv","country":"AZ","playlist_type":"country"},
    {"name":"moldavie","display_name":"🇲🇩 Moldavie","url":f"{settings.IPTV_BASE_URL}/countries/md.m3u","category":"iptv","country":"MD","playlist_type":"country"},
    {"name":"bielorussie","display_name":"🇧🇾 Biélorussie","url":f"{settings.IPTV_BASE_URL}/countries/by.m3u","category":"iptv","country":"BY","playlist_type":"country"},
    {"name":"lituanie","display_name":"🇱🇹 Lituanie","url":f"{settings.IPTV_BASE_URL}/countries/lt.m3u","category":"iptv","country":"LT","playlist_type":"country"},
    {"name":"lettonie","display_name":"🇱🇻 Lettonie","url":f"{settings.IPTV_BASE_URL}/countries/lv.m3u","category":"iptv","country":"LV","playlist_type":"country"},
    {"name":"estonie","display_name":"🇪🇪 Estonie","url":f"{settings.IPTV_BASE_URL}/countries/ee.m3u","category":"iptv","country":"EE","playlist_type":"country"},
    {"name":"afrique_du_sud","display_name":"🇿🇦 Afrique du Sud","url":f"{settings.IPTV_BASE_URL}/countries/za.m3u","category":"iptv","country":"ZA","playlist_type":"country"},
    {"name":"nigeria","display_name":"🇳🇬 Nigeria","url":f"{settings.IPTV_BASE_URL}/countries/ng.m3u","category":"iptv","country":"NG","playlist_type":"country"},
    {"name":"kenya","display_name":"🇰🇪 Kenya","url":f"{settings.IPTV_BASE_URL}/countries/ke.m3u","category":"iptv","country":"KE","playlist_type":"country"},
    {"name":"tanzanie","display_name":"🇹🇿 Tanzanie","url":f"{settings.IPTV_BASE_URL}/countries/tz.m3u","category":"iptv","country":"TZ","playlist_type":"country"},
    {"name":"ouganda","display_name":"🇺🇬 Ouganda","url":f"{settings.IPTV_BASE_URL}/countries/ug.m3u","category":"iptv","country":"UG","playlist_type":"country"},
    {"name":"rwanda","display_name":"🇷🇼 Rwanda","url":f"{settings.IPTV_BASE_URL}/countries/rw.m3u","category":"iptv","country":"RW","playlist_type":"country"},
    {"name":"ethiopie","display_name":"🇪🇹 Éthiopie","url":f"{settings.IPTV_BASE_URL}/countries/et.m3u","category":"iptv","country":"ET","playlist_type":"country"},
    {"name":"ghana","display_name":"🇬🇭 Ghana","url":f"{settings.IPTV_BASE_URL}/countries/gh.m3u","category":"iptv","country":"GH","playlist_type":"country"},
    {"name":"angola","display_name":"🇦🇴 Angola","url":f"{settings.IPTV_BASE_URL}/countries/ao.m3u","category":"iptv","country":"AO","playlist_type":"country"},
    {"name":"mozambique","display_name":"🇲🇿 Mozambique","url":f"{settings.IPTV_BASE_URL}/countries/mz.m3u","category":"iptv","country":"MZ","playlist_type":"country"},
    {"name":"madagascar","display_name":"🇲🇬 Madagascar","url":f"{settings.IPTV_BASE_URL}/countries/mg.m3u","category":"iptv","country":"MG","playlist_type":"country"},
    {"name":"maurice","display_name":"🇲🇺 Maurice","url":f"{settings.IPTV_BASE_URL}/countries/mu.m3u","category":"iptv","country":"MU","playlist_type":"country"},
    {"name":"seychelles","display_name":"🇸🇨 Seychelles","url":f"{settings.IPTV_BASE_URL}/countries/sc.m3u","category":"iptv","country":"SC","playlist_type":"country"},
    {"name":"comores","display_name":"🇰🇲 Comores","url":f"{settings.IPTV_BASE_URL}/countries/km.m3u","category":"iptv","country":"KM","playlist_type":"country"},
    {"name":"djibouti","display_name":"🇩🇯 Djibouti","url":f"{settings.IPTV_BASE_URL}/countries/dj.m3u","category":"iptv","country":"DJ","playlist_type":"country"},
    {"name":"soudan","display_name":"🇸🇩 Soudan","url":f"{settings.IPTV_BASE_URL}/countries/sd.m3u","category":"iptv","country":"SD","playlist_type":"country"},
    {"name":"soudan_sud","display_name":"🇸🇸 Soudan du Sud","url":f"{settings.IPTV_BASE_URL}/countries/ss.m3u","category":"iptv","country":"SS","playlist_type":"country"},
    {"name":"eritree","display_name":"🇪🇷 Érythrée","url":f"{settings.IPTV_BASE_URL}/countries/er.m3u","category":"iptv","country":"ER","playlist_type":"country"},
    {"name":"somalie","display_name":"🇸🇴 Somalie","url":f"{settings.IPTV_BASE_URL}/countries/so.m3u","category":"iptv","country":"SO","playlist_type":"country"},
    {"name":"botswana","display_name":"🇧🇼 Botswana","url":f"{settings.IPTV_BASE_URL}/countries/bw.m3u","category":"iptv","country":"BW","playlist_type":"country"},
    {"name":"namibie","display_name":"🇳🇦 Namibie","url":f"{settings.IPTV_BASE_URL}/countries/na.m3u","category":"iptv","country":"NA","playlist_type":"country"},
    {"name":"zambie","display_name":"🇿🇲 Zambie","url":f"{settings.IPTV_BASE_URL}/countries/zm.m3u","category":"iptv","country":"ZM","playlist_type":"country"},
    {"name":"zimbabwe","display_name":"🇿🇼 Zimbabwe","url":f"{settings.IPTV_BASE_URL}/countries/zw.m3u","category":"iptv","country":"ZW","playlist_type":"country"},
    {"name":"malawi","display_name":"🇲🇼 Malawi","url":f"{settings.IPTV_BASE_URL}/countries/mw.m3u","category":"iptv","country":"MW","playlist_type":"country"},
    {"name":"lesotho","display_name":"🇱🇸 Lesotho","url":f"{settings.IPTV_BASE_URL}/countries/ls.m3u","category":"iptv","country":"LS","playlist_type":"country"},
    {"name":"swaziland","display_name":"🇸🇿 Eswatini","url":f"{settings.IPTV_BASE_URL}/countries/sz.m3u","category":"iptv","country":"SZ","playlist_type":"country"},
    {"name":"australie","display_name":"🇦🇺 Australie","url":f"{settings.IPTV_BASE_URL}/countries/au.m3u","category":"iptv","country":"AU","playlist_type":"country"},
    {"name":"nouvelle_zelande","display_name":"🇳🇿 Nouvelle-Zélande","url":f"{settings.IPTV_BASE_URL}/countries/nz.m3u","category":"iptv","country":"NZ","playlist_type":"country"},
    {"name":"fidji","display_name":"🇫🇯 Fidji","url":f"{settings.IPTV_BASE_URL}/countries/fj.m3u","category":"iptv","country":"FJ","playlist_type":"country"},
    {"name":"papouasie_nouvelle_guinee","display_name":"🇵🇬 Papouasie-Nouvelle-Guinée","url":f"{settings.IPTV_BASE_URL}/countries/pg.m3u","category":"iptv","country":"PG","playlist_type":"country"},

    # ---- SUBDIVISIONS (régions/provinces) ----
    {"name":"ca_alberta","display_name":"🇨🇦 Canada — Alberta","url":f"{settings.IPTV_BASE_URL}/subdivisions/ca-ab.m3u","category":"iptv","country":"CA","playlist_type":"subdivision"},
    {"name":"ca_british_columbia","display_name":"🇨🇦 Canada — British Columbia","url":f"{settings.IPTV_BASE_URL}/subdivisions/ca-bc.m3u","category":"iptv","country":"CA","playlist_type":"subdivision"},
    {"name":"ca_ontario","display_name":"🇨🇦 Canada — Ontario","url":f"{settings.IPTV_BASE_URL}/subdivisions/ca-on.m3u","category":"iptv","country":"CA","playlist_type":"subdivision"},
    {"name":"ca_quebec","display_name":"🇨🇦 Canada — Québec","url":f"{settings.IPTV_BASE_URL}/subdivisions/ca-qc.m3u","category":"iptv","country":"CA","playlist_type":"subdivision"},
    {"name":"ca_manitoba","display_name":"🇨🇦 Canada — Manitoba","url":f"{settings.IPTV_BASE_URL}/subdivisions/ca-mb.m3u","category":"iptv","country":"CA","playlist_type":"subdivision"},
    {"name":"ca_saskatchewan","display_name":"🇨🇦 Canada — Saskatchewan","url":f"{settings.IPTV_BASE_URL}/subdivisions/ca-sk.m3u","category":"iptv","country":"CA","playlist_type":"subdivision"},
    {"name":"ca_nova_scotia","display_name":"🇨🇦 Canada — Nouvelle-Écosse","url":f"{settings.IPTV_BASE_URL}/subdivisions/ca-ns.m3u","category":"iptv","country":"CA","playlist_type":"subdivision"},
    {"name":"ca_new_brunswick","display_name":"🇨🇦 Canada — Nouveau-Brunswick","url":f"{settings.IPTV_BASE_URL}/subdivisions/ca-nb.m3u","category":"iptv","country":"CA","playlist_type":"subdivision"},
    {"name":"ca_newfoundland","display_name":"🇨🇦 Canada — Terre-Neuve","url":f"{settings.IPTV_BASE_URL}/subdivisions/ca-nl.m3u","category":"iptv","country":"CA","playlist_type":"subdivision"},
    {"name":"ca_prince_edward","display_name":"🇨🇦 Canada — Île-du-Prince-Édouard","url":f"{settings.IPTV_BASE_URL}/subdivisions/ca-pe.m3u","category":"iptv","country":"CA","playlist_type":"subdivision"},
    {"name":"ca_yukon","display_name":"🇨🇦 Canada — Yukon","url":f"{settings.IPTV_BASE_URL}/subdivisions/ca-yt.m3u","category":"iptv","country":"CA","playlist_type":"subdivision"},
    {"name":"ca_northwest","display_name":"🇨🇦 Canada — Territoires du Nord-Ouest","url":f"{settings.IPTV_BASE_URL}/subdivisions/ca-nt.m3u","category":"iptv","country":"CA","playlist_type":"subdivision"},
    {"name":"ca_nunavut","display_name":"🇨🇦 Canada — Nunavut","url":f"{settings.IPTV_BASE_URL}/subdivisions/ca-nu.m3u","category":"iptv","country":"CA","playlist_type":"subdivision"},

    {"name":"us_california","display_name":"🇺🇸 USA — Californie","url":f"{settings.IPTV_BASE_URL}/subdivisions/us-ca.m3u","category":"iptv","country":"US","playlist_type":"subdivision"},
    {"name":"us_texas","display_name":"🇺🇸 USA — Texas","url":f"{settings.IPTV_BASE_URL}/subdivisions/us-tx.m3u","category":"iptv","country":"US","playlist_type":"subdivision"},
    {"name":"us_new_york","display_name":"🇺🇸 USA — New York","url":f"{settings.IPTV_BASE_URL}/subdivisions/us-ny.m3u","category":"iptv","country":"US","playlist_type":"subdivision"},
    {"name":"us_florida","display_name":"🇺🇸 USA — Floride","url":f"{settings.IPTV_BASE_URL}/subdivisions/us-fl.m3u","category":"iptv","country":"US","playlist_type":"subdivision"},
    {"name":"us_illinois","display_name":"🇺🇸 USA — Illinois","url":f"{settings.IPTV_BASE_URL}/subdivisions/us-il.m3u","category":"iptv","country":"US","playlist_type":"subdivision"},
    {"name":"us_pennsylvania","display_name":"🇺🇸 USA — Pennsylvanie","url":f"{settings.IPTV_BASE_URL}/subdivisions/us-pa.m3u","category":"iptv","country":"US","playlist_type":"subdivision"},
    {"name":"us_ohio","display_name":"🇺🇸 USA — Ohio","url":f"{settings.IPTV_BASE_URL}/subdivisions/us-oh.m3u","category":"iptv","country":"US","playlist_type":"subdivision"},
    {"name":"us_georgia","display_name":"🇺🇸 USA — Géorgie","url":f"{settings.IPTV_BASE_URL}/subdivisions/us-ga.m3u","category":"iptv","country":"US","playlist_type":"subdivision"},
    {"name":"us_michigan","display_name":"🇺🇸 USA — Michigan","url":f"{settings.IPTV_BASE_URL}/subdivisions/us-mi.m3u","category":"iptv","country":"US","playlist_type":"subdivision"},
    {"name":"us_north_carolina","display_name":"🇺🇸 USA — Caroline du Nord","url":f"{settings.IPTV_BASE_URL}/subdivisions/us-nc.m3u","category":"iptv","country":"US","playlist_type":"subdivision"},
    {"name":"us_new_jersey","display_name":"🇺🇸 USA — New Jersey","url":f"{settings.IPTV_BASE_URL}/subdivisions/us-nj.m3u","category":"iptv","country":"US","playlist_type":"subdivision"},
    {"name":"us_virginia","display_name":"🇺🇸 USA — Virginie","url":f"{settings.IPTV_BASE_URL}/subdivisions/us-va.m3u","category":"iptv","country":"US","playlist_type":"subdivision"},
    {"name":"us_washington","display_name":"🇺🇸 USA — Washington","url":f"{settings.IPTV_BASE_URL}/subdivisions/us-wa.m3u","category":"iptv","country":"US","playlist_type":"subdivision"},
    {"name":"us_massachusetts","display_name":"🇺🇸 USA — Massachusetts","url":f"{settings.IPTV_BASE_URL}/subdivisions/us-ma.m3u","category":"iptv","country":"US","playlist_type":"subdivision"},
    {"name":"us_indiana","display_name":"🇺🇸 USA — Indiana","url":f"{settings.IPTV_BASE_URL}/subdivisions/us-in.m3u","category":"iptv","country":"US","playlist_type":"subdivision"},
    {"name":"us_tennessee","display_name":"🇺🇸 USA — Tennessee","url":f"{settings.IPTV_BASE_URL}/subdivisions/us-tn.m3u","category":"iptv","country":"US","playlist_type":"subdivision"},
    {"name":"us_missouri","display_name":"🇺🇸 USA — Missouri","url":f"{settings.IPTV_BASE_URL}/subdivisions/us-mo.m3u","category":"iptv","country":"US","playlist_type":"subdivision"},
    {"name":"us_maryland","display_name":"🇺🇸 USA — Maryland","url":f"{settings.IPTV_BASE_URL}/subdivisions/us-md.m3u","category":"iptv","country":"US","playlist_type":"subdivision"},
    {"name":"us_wisconsin","display_name":"🇺🇸 USA — Wisconsin","url":f"{settings.IPTV_BASE_URL}/subdivisions/us-wi.m3u","category":"iptv","country":"US","playlist_type":"subdivision"},
    {"name":"us_colorado","display_name":"🇺🇸 USA — Colorado","url":f"{settings.IPTV_BASE_URL}/subdivisions/us-co.m3u","category":"iptv","country":"US","playlist_type":"subdivision"},
    {"name":"us_minnesota","display_name":"🇺🇸 USA — Minnesota","url":f"{settings.IPTV_BASE_URL}/subdivisions/us-mn.m3u","category":"iptv","country":"US","playlist_type":"subdivision"},
    {"name":"us_south_carolina","display_name":"🇺🇸 USA — Caroline du Sud","url":f"{settings.IPTV_BASE_URL}/subdivisions/us-sc.m3u","category":"iptv","country":"US","playlist_type":"subdivision"},
    {"name":"us_alabama","display_name":"🇺🇸 USA — Alabama","url":f"{settings.IPTV_BASE_URL}/subdivisions/us-al.m3u","category":"iptv","country":"US","playlist_type":"subdivision"},
    {"name":"us_louisiana","display_name":"🇺🇸 USA — Louisiane","url":f"{settings.IPTV_BASE_URL}/subdivisions/us-la.m3u","category":"iptv","country":"US","playlist_type":"subdivision"},
    {"name":"us_kentucky","display_name":"🇺🇸 USA — Kentucky","url":f"{settings.IPTV_BASE_URL}/subdivisions/us-ky.m3u","category":"iptv","country":"US","playlist_type":"subdivision"},
    {"name":"us_oregon","display_name":"🇺🇸 USA — Oregon","url":f"{settings.IPTV_BASE_URL}/subdivisions/us-or.m3u","category":"iptv","country":"US","playlist_type":"subdivision"},
    {"name":"us_oklahoma","display_name":"🇺🇸 USA — Oklahoma","url":f"{settings.IPTV_BASE_URL}/subdivisions/us-ok.m3u","category":"iptv","country":"US","playlist_type":"subdivision"},
    {"name":"us_connecticut","display_name":"🇺🇸 USA — Connecticut","url":f"{settings.IPTV_BASE_URL}/subdivisions/us-ct.m3u","category":"iptv","country":"US","playlist_type":"subdivision"},
    {"name":"us_iowa","display_name":"🇺🇸 USA — Iowa","url":f"{settings.IPTV_BASE_URL}/subdivisions/us-ia.m3u","category":"iptv","country":"US","playlist_type":"subdivision"},
    {"name":"us_mississippi","display_name":"🇺🇸 USA — Mississippi","url":f"{settings.IPTV_BASE_URL}/subdivisions/us-ms.m3u","category":"iptv","country":"US","playlist_type":"subdivision"},
    {"name":"us_arkansas","display_name":"🇺🇸 USA — Arkansas","url":f"{settings.IPTV_BASE_URL}/subdivisions/us-ar.m3u","category":"iptv","country":"US","playlist_type":"subdivision"},
    {"name":"us_kansas","display_name":"🇺🇸 USA — Kansas","url":f"{settings.IPTV_BASE_URL}/subdivisions/us-ks.m3u","category":"iptv","country":"US","playlist_type":"subdivision"},
    {"name":"us_utah","display_name":"🇺🇸 USA — Utah","url":f"{settings.IPTV_BASE_URL}/subdivisions/us-ut.m3u","category":"iptv","country":"US","playlist_type":"subdivision"},
    {"name":"us_nevada","display_name":"🇺🇸 USA — Nevada","url":f"{settings.IPTV_BASE_URL}/subdivisions/us-nv.m3u","category":"iptv","country":"US","playlist_type":"subdivision"},
    {"name":"us_new_mexico","display_name":"🇺🇸 USA — Nouveau-Mexique","url":f"{settings.IPTV_BASE_URL}/subdivisions/us-nm.m3u","category":"iptv","country":"US","playlist_type":"subdivision"},
    {"name":"us_nebraska","display_name":"🇺🇸 USA — Nebraska","url":f"{settings.IPTV_BASE_URL}/subdivisions/us-ne.m3u","category":"iptv","country":"US","playlist_type":"subdivision"},
    {"name":"us_west_virginia","display_name":"🇺🇸 USA — Virginie-Occidentale","url":f"{settings.IPTV_BASE_URL}/subdivisions/us-wv.m3u","category":"iptv","country":"US","playlist_type":"subdivision"},
    {"name":"us_idaho","display_name":"🇺🇸 USA — Idaho","url":f"{settings.IPTV_BASE_URL}/subdivisions/us-id.m3u","category":"iptv","country":"US","playlist_type":"subdivision"},
    {"name":"us_hawaii","display_name":"🇺🇸 USA — Hawaï","url":f"{settings.IPTV_BASE_URL}/subdivisions/us-hi.m3u","category":"iptv","country":"US","playlist_type":"subdivision"},
    {"name":"us_maine","display_name":"🇺🇸 USA — Maine","url":f"{settings.IPTV_BASE_URL}/subdivisions/us-me.m3u","category":"iptv","country":"US","playlist_type":"subdivision"},
    {"name":"us_new_hampshire","display_name":"🇺🇸 USA — New Hampshire","url":f"{settings.IPTV_BASE_URL}/subdivisions/us-nh.m3u","category":"iptv","country":"US","playlist_type":"subdivision"},
    {"name":"us_rhode_island","display_name":"🇺🇸 USA — Rhode Island","url":f"{settings.IPTV_BASE_URL}/subdivisions/us-ri.m3u","category":"iptv","country":"US","playlist_type":"subdivision"},
    {"name":"us_montana","display_name":"🇺🇸 USA — Montana","url":f"{settings.IPTV_BASE_URL}/subdivisions/us-mt.m3u","category":"iptv","country":"US","playlist_type":"subdivision"},
    {"name":"us_delaware","display_name":"🇺🇸 USA — Delaware","url":f"{settings.IPTV_BASE_URL}/subdivisions/us-de.m3u","category":"iptv","country":"US","playlist_type":"subdivision"},
    {"name":"us_south_dakota","display_name":"🇺🇸 USA — Dakota du Sud","url":f"{settings.IPTV_BASE_URL}/subdivisions/us-sd.m3u","category":"iptv","country":"US","playlist_type":"subdivision"},
    {"name":"us_north_dakota","display_name":"🇺🇸 USA — Dakota du Nord","url":f"{settings.IPTV_BASE_URL}/subdivisions/us-nd.m3u","category":"iptv","country":"US","playlist_type":"subdivision"},
    {"name":"us_alaska","display_name":"🇺🇸 USA — Alaska","url":f"{settings.IPTV_BASE_URL}/subdivisions/us-ak.m3u","category":"iptv","country":"US","playlist_type":"subdivision"},
    {"name":"us_vermont","display_name":"🇺🇸 USA — Vermont","url":f"{settings.IPTV_BASE_URL}/subdivisions/us-vt.m3u","category":"iptv","country":"US","playlist_type":"subdivision"},
    {"name":"us_wyoming","display_name":"🇺🇸 USA — Wyoming","url":f"{settings.IPTV_BASE_URL}/subdivisions/us-wy.m3u","category":"iptv","country":"US","playlist_type":"subdivision"},

    {"name":"br_sao_paulo","display_name":"🇧🇷 Brésil — São Paulo","url":f"{settings.IPTV_BASE_URL}/subdivisions/br-sp.m3u","category":"iptv","country":"BR","playlist_type":"subdivision"},
    {"name":"br_rio_de_janeiro","display_name":"🇧🇷 Brésil — Rio de Janeiro","url":f"{settings.IPTV_BASE_URL}/subdivisions/br-rj.m3u","category":"iptv","country":"BR","playlist_type":"subdivision"},
    {"name":"br_minas_gerais","display_name":"🇧🇷 Brésil — Minas Gerais","url":f"{settings.IPTV_BASE_URL}/subdivisions/br-mg.m3u","category":"iptv","country":"BR","playlist_type":"subdivision"},
    {"name":"br_bahia","display_name":"🇧🇷 Brésil — Bahia","url":f"{settings.IPTV_BASE_URL}/subdivisions/br-ba.m3u","category":"iptv","country":"BR","playlist_type":"subdivision"},
    {"name":"br_parana","display_name":"🇧🇷 Brésil — Paraná","url":f"{settings.IPTV_BASE_URL}/subdivisions/br-pr.m3u","category":"iptv","country":"BR","playlist_type":"subdivision"},
    {"name":"br_rio_grande_sul","display_name":"🇧🇷 Brésil — Rio Grande do Sul","url":f"{settings.IPTV_BASE_URL}/subdivisions/br-rs.m3u","category":"iptv","country":"BR","playlist_type":"subdivision"},
    {"name":"br_pernambuco","display_name":"🇧🇷 Brésil — Pernambuco","url":f"{settings.IPTV_BASE_URL}/subdivisions/br-pe.m3u","category":"iptv","country":"BR","playlist_type":"subdivision"},
    {"name":"br_ceara","display_name":"🇧🇷 Brésil — Ceará","url":f"{settings.IPTV_BASE_URL}/subdivisions/br-ce.m3u","category":"iptv","country":"BR","playlist_type":"subdivision"},
    {"name":"br_para","display_name":"🇧🇷 Brésil — Pará","url":f"{settings.IPTV_BASE_URL}/subdivisions/br-pa.m3u","category":"iptv","country":"BR","playlist_type":"subdivision"},
    {"name":"br_santa_catarina","display_name":"🇧🇷 Brésil — Santa Catarina","url":f"{settings.IPTV_BASE_URL}/subdivisions/br-sc.m3u","category":"iptv","country":"BR","playlist_type":"subdivision"},

    {"name":"co_antioquia","display_name":"🇨🇴 Colombie — Antioquia","url":f"{settings.IPTV_BASE_URL}/subdivisions/co-ant.m3u","category":"iptv","country":"CO","playlist_type":"subdivision"},
    {"name":"co_atlantico","display_name":"🇨🇴 Colombie — Atlántico","url":f"{settings.IPTV_BASE_URL}/subdivisions/co-atl.m3u","category":"iptv","country":"CO","playlist_type":"subdivision"},
    {"name":"co_bogota","display_name":"🇨🇴 Colombie — Bogotá","url":f"{settings.IPTV_BASE_URL}/subdivisions/co-bog.m3u","category":"iptv","country":"CO","playlist_type":"subdivision"},
    {"name":"co_valle_cauca","display_name":"🇨🇴 Colombie — Valle del Cauca","url":f"{settings.IPTV_BASE_URL}/subdivisions/co-vac.m3u","category":"iptv","country":"CO","playlist_type":"subdivision"},

    {"name":"ar_buenos_aires","display_name":"🇦🇷 Argentine — Buenos Aires","url":f"{settings.IPTV_BASE_URL}/subdivisions/ar-ba.m3u","category":"iptv","country":"AR","playlist_type":"subdivision"},
    {"name":"ar_cordoba","display_name":"🇦🇷 Argentine — Córdoba","url":f"{settings.IPTV_BASE_URL}/subdivisions/ar-cb.m3u","category":"iptv","country":"AR","playlist_type":"subdivision"},
    {"name":"ar_santa_fe","display_name":"🇦🇷 Argentine — Santa Fe","url":f"{settings.IPTV_BASE_URL}/subdivisions/ar-sf.m3u","category":"iptv","country":"AR","playlist_type":"subdivision"},

    {"name":"gb_england","display_name":"🇬🇧 UK — Angleterre","url":f"{settings.IPTV_BASE_URL}/subdivisions/gb-eng.m3u","category":"iptv","country":"GB","playlist_type":"subdivision"},
    {"name":"gb_scotland","display_name":"🇬🇧 UK — Écosse","url":f"{settings.IPTV_BASE_URL}/subdivisions/gb-sct.m3u","category":"iptv","country":"GB","playlist_type":"subdivision"},
    {"name":"gb_wales","display_name":"🏴󠁧󠁢󠁷󠁬󠁳󠁿 UK — Pays de Galles","url":f"{settings.IPTV_BASE_URL}/subdivisions/gb-wls.m3u","category":"iptv","country":"GB","playlist_type":"subdivision"},
    {"name":"gb_northern_ireland","display_name":"🇬🇧 UK — Irlande du Nord","url":f"{settings.IPTV_BASE_URL}/subdivisions/gb-nir.m3u","category":"iptv","country":"GB","playlist_type":"subdivision"},

    {"name":"de_bayern","display_name":"🇩🇪 Allemagne — Bavière","url":f"{settings.IPTV_BASE_URL}/subdivisions/de-by.m3u","category":"iptv","country":"DE","playlist_type":"subdivision"},
    {"name":"de_nordrhein_westfalen","display_name":"🇩🇪 Allemagne — Rhénanie du Nord-Westphalie","url":f"{settings.IPTV_BASE_URL}/subdivisions/de-nw.m3u","category":"iptv","country":"DE","playlist_type":"subdivision"},
    {"name":"de_baden_wurttemberg","display_name":"🇩🇪 Allemagne — Bade-Wurtemberg","url":f"{settings.IPTV_BASE_URL}/subdivisions/de-bw.m3u","category":"iptv","country":"DE","playlist_type":"subdivision"},
    {"name":"de_niedersachsen","display_name":"🇩🇪 Allemagne — Basse-Saxe","url":f"{settings.IPTV_BASE_URL}/subdivisions/de-ni.m3u","category":"iptv","country":"DE","playlist_type":"subdivision"},
    {"name":"de_hessen","display_name":"🇩🇪 Allemagne — Hesse","url":f"{settings.IPTV_BASE_URL}/subdivisions/de-he.m3u","category":"iptv","country":"DE","playlist_type":"subdivision"},
    {"name":"de_berlin","display_name":"🇩🇪 Allemagne — Berlin","url":f"{settings.IPTV_BASE_URL}/subdivisions/de-be.m3u","category":"iptv","country":"DE","playlist_type":"subdivision"},

    {"name":"it_lombardia","display_name":"🇮🇹 Italie — Lombardie","url":f"{settings.IPTV_BASE_URL}/subdivisions/it-25.m3u","category":"iptv","country":"IT","playlist_type":"subdivision"},
    {"name":"it_lazio","display_name":"🇮🇹 Italie — Latium","url":f"{settings.IPTV_BASE_URL}/subdivisions/it-62.m3u","category":"iptv","country":"IT","playlist_type":"subdivision"},
    {"name":"it_campania","display_name":"🇮🇹 Italie — Campanie","url":f"{settings.IPTV_BASE_URL}/subdivisions/it-72.m3u","category":"iptv","country":"IT","playlist_type":"subdivision"},
    {"name":"it_sicilia","display_name":"🇮🇹 Italie — Sicile","url":f"{settings.IPTV_BASE_URL}/subdivisions/it-82.m3u","category":"iptv","country":"IT","playlist_type":"subdivision"},
    {"name":"it_veneto","display_name":"🇮🇹 Italie — Vénétie","url":f"{settings.IPTV_BASE_URL}/subdivisions/it-34.m3u","category":"iptv","country":"IT","playlist_type":"subdivision"},

    {"name":"es_madrid","display_name":"🇪🇸 Espagne — Madrid","url":f"{settings.IPTV_BASE_URL}/subdivisions/es-md.m3u","category":"iptv","country":"ES","playlist_type":"subdivision"},
    {"name":"es_cataluna","display_name":"🇪🇸 Espagne — Catalogne","url":f"{settings.IPTV_BASE_URL}/subdivisions/es-ct.m3u","category":"iptv","country":"ES","playlist_type":"subdivision"},
    {"name":"es_andalucia","display_name":"🇪🇸 Espagne — Andalousie","url":f"{settings.IPTV_BASE_URL}/subdivisions/es-an.m3u","category":"iptv","country":"ES","playlist_type":"subdivision"},
    {"name":"es_valencia","display_name":"🇪🇸 Espagne — Valence","url":f"{settings.IPTV_BASE_URL}/subdivisions/es-vc.m3u","category":"iptv","country":"ES","playlist_type":"subdivision"},
    {"name":"es_galicia","display_name":"🇪🇸 Espagne — Galice","url":f"{settings.IPTV_BASE_URL}/subdivisions/es-ga.m3u","category":"iptv","country":"ES","playlist_type":"subdivision"},

    # ---- VILLES ----
    {"name":"city_toronto","display_name":"🏙️ Toronto","url":f"{settings.IPTV_BASE_URL}/cities/cator.m3u","category":"iptv","country":"CA","playlist_type":"city"},
    {"name":"city_montreal","display_name":"🏙️ Montréal","url":f"{settings.IPTV_BASE_URL}/cities/camtl.m3u","category":"iptv","country":"CA","playlist_type":"city"},
    {"name":"city_vancouver","display_name":"🏙️ Vancouver","url":f"{settings.IPTV_BASE_URL}/cities/cavan.m3u","category":"iptv","country":"CA","playlist_type":"city"},
    {"name":"city_calgary","display_name":"🏙️ Calgary","url":f"{settings.IPTV_BASE_URL}/cities/cacal.m3u","category":"iptv","country":"CA","playlist_type":"city"},
    {"name":"city_edmonton","display_name":"🏙️ Edmonton","url":f"{settings.IPTV_BASE_URL}/cities/caedm.m3u","category":"iptv","country":"CA","playlist_type":"city"},
    {"name":"city_ottawa","display_name":"🏙️ Ottawa","url":f"{settings.IPTV_BASE_URL}/cities/caott.m3u","category":"iptv","country":"CA","playlist_type":"city"},
    {"name":"city_quebec","display_name":"🏙️ Québec","url":f"{settings.IPTV_BASE_URL}/cities/caque.m3u","category":"iptv","country":"CA","playlist_type":"city"},
    {"name":"city_winnipeg","display_name":"🏙️ Winnipeg","url":f"{settings.IPTV_BASE_URL}/cities/cawpg.m3u","category":"iptv","country":"CA","playlist_type":"city"},
    {"name":"city_hamilton","display_name":"🏙️ Hamilton","url":f"{settings.IPTV_BASE_URL}/cities/caham.m3u","category":"iptv","country":"CA","playlist_type":"city"},

    {"name":"city_new_york","display_name":"🏙️ New York","url":f"{settings.IPTV_BASE_URL}/cities/usnyc.m3u","category":"iptv","country":"US","playlist_type":"city"},
    {"name":"city_los_angeles","display_name":"🏙️ Los Angeles","url":f"{settings.IPTV_BASE_URL}/cities/uslax.m3u","category":"iptv","country":"US","playlist_type":"city"},
    {"name":"city_chicago","display_name":"🏙️ Chicago","url":f"{settings.IPTV_BASE_URL}/cities/uschi.m3u","category":"iptv","country":"US","playlist_type":"city"},
    {"name":"city_houston","display_name":"🏙️ Houston","url":f"{settings.IPTV_BASE_URL}/cities/ushou.m3u","category":"iptv","country":"US","playlist_type":"city"},
    {"name":"city_phoenix","display_name":"🏙️ Phoenix","url":f"{settings.IPTV_BASE_URL}/cities/usphx.m3u","category":"iptv","country":"US","playlist_type":"city"},
    {"name":"city_philadelphia","display_name":"🏙️ Philadelphie","url":f"{settings.IPTV_BASE_URL}/cities/usphi.m3u","category":"iptv","country":"US","playlist_type":"city"},
    {"name":"city_san_antonio","display_name":"🏙️ San Antonio","url":f"{settings.IPTV_BASE_URL}/cities/ussat.m3u","category":"iptv","country":"US","playlist_type":"city"},
    {"name":"city_san_diego","display_name":"🏙️ San Diego","url":f"{settings.IPTV_BASE_URL}/cities/ussan.m3u","category":"iptv","country":"US","playlist_type":"city"},
    {"name":"city_dallas","display_name":"🏙️ Dallas","url":f"{settings.IPTV_BASE_URL}/cities/usdal.m3u","category":"iptv","country":"US","playlist_type":"city"},
    {"name":"city_austin","display_name":"🏙️ Austin","url":f"{settings.IPTV_BASE_URL}/cities/usaus.m3u","category":"iptv","country":"US","playlist_type":"city"},
    {"name":"city_jacksonville","display_name":"🏙️ Jacksonville","url":f"{settings.IPTV_BASE_URL}/cities/usjax.m3u","category":"iptv","country":"US","playlist_type":"city"},
    {"name":"city_fort_worth","display_name":"🏙️ Fort Worth","url":f"{settings.IPTV_BASE_URL}/cities/usftw.m3u","category":"iptv","country":"US","playlist_type":"city"},
    {"name":"city_columbus","display_name":"🏙️ Columbus","url":f"{settings.IPTV_BASE_URL}/cities/uscmh.m3u","category":"iptv","country":"US","playlist_type":"city"},
    {"name":"city_charlotte","display_name":"🏙️ Charlotte","url":f"{settings.IPTV_BASE_URL}/cities/usclt.m3u","category":"iptv","country":"US","playlist_type":"city"},
    {"name":"city_seattle","display_name":"🏙️ Seattle","url":f"{settings.IPTV_BASE_URL}/cities/ussea.m3u","category":"iptv","country":"US","playlist_type":"city"},
    {"name":"city_denver","display_name":"🏙️ Denver","url":f"{settings.IPTV_BASE_URL}/cities/usden.m3u","category":"iptv","country":"US","playlist_type":"city"},
    {"name":"city_washington","display_name":"🏙️ Washington D.C.","url":f"{settings.IPTV_BASE_URL}/cities/uswdc.m3u","category":"iptv","country":"US","playlist_type":"city"},
    {"name":"city_boston","display_name":"🏙️ Boston","url":f"{settings.IPTV_BASE_URL}/cities/usbos.m3u","category":"iptv","country":"US","playlist_type":"city"},
    {"name":"city_detroit","display_name":"🏙️ Detroit","url":f"{settings.IPTV_BASE_URL}/cities/usdet.m3u","category":"iptv","country":"US","playlist_type":"city"},
    {"name":"city_nashville","display_name":"🏙️ Nashville","url":f"{settings.IPTV_BASE_URL}/cities/usbna.m3u","category":"iptv","country":"US","playlist_type":"city"},
    {"name":"city_memphis","display_name":"🏙️ Memphis","url":f"{settings.IPTV_BASE_URL}/cities/usmem.m3u","category":"iptv","country":"US","playlist_type":"city"},
    {"name":"city_portland","display_name":"🏙️ Portland","url":f"{settings.IPTV_BASE_URL}/cities/uspdx.m3u","category":"iptv","country":"US","playlist_type":"city"},
    {"name":"city_oklahoma_city","display_name":"🏙️ Oklahoma City","url":f"{settings.IPTV_BASE_URL}/cities/usokc.m3u","category":"iptv","country":"US","playlist_type":"city"},
    {"name":"city_las_vegas","display_name":"🏙️ Las Vegas","url":f"{settings.IPTV_BASE_URL}/cities/uslas.m3u","category":"iptv","country":"US","playlist_type":"city"},
    {"name":"city_baltimore","display_name":"🏙️ Baltimore","url":f"{settings.IPTV_BASE_URL}/cities/usbal.m3u","category":"iptv","country":"US","playlist_type":"city"},
    {"name":"city_louisville","display_name":"🏙️ Louisville","url":f"{settings.IPTV_BASE_URL}/cities/ussdf.m3u","category":"iptv","country":"US","playlist_type":"city"},
    {"name":"city_milwaukee","display_name":"🏙️ Milwaukee","url":f"{settings.IPTV_BASE_URL}/cities/usmil.m3u","category":"iptv","country":"US","playlist_type":"city"},
    {"name":"city_albuquerque","display_name":"🏙️ Albuquerque","url":f"{settings.IPTV_BASE_URL}/cities/usabq.m3u","category":"iptv","country":"US","playlist_type":"city"},
    {"name":"city_tucson","display_name":"🏙️ Tucson","url":f"{settings.IPTV_BASE_URL}/cities/ustus.m3u","category":"iptv","country":"US","playlist_type":"city"},
    {"name":"city_fresno","display_name":"🏙️ Fresno","url":f"{settings.IPTV_BASE_URL}/cities/usfat.m3u","category":"iptv","country":"US","playlist_type":"city"},
    {"name":"city_sacramento","display_name":"🏙️ Sacramento","url":f"{settings.IPTV_BASE_URL}/cities/ussac.m3u","category":"iptv","country":"US","playlist_type":"city"},
    {"name":"city_mesa","display_name":"🏙️ Mesa","url":f"{settings.IPTV_BASE_URL}/cities/usmes.m3u","category":"iptv","country":"US","playlist_type":"city"},
    {"name":"city_kansas_city","display_name":"🏙️ Kansas City","url":f"{settings.IPTV_BASE_URL}/cities/usmkc.m3u","category":"iptv","country":"US","playlist_type":"city"},
    {"name":"city_atlanta","display_name":"🏙️ Atlanta","url":f"{settings.IPTV_BASE_URL}/cities/usatl.m3u","category":"iptv","country":"US","playlist_type":"city"},
    {"name":"city_miami","display_name":"🏙️ Miami","url":f"{settings.IPTV_BASE_URL}/cities/usmia.m3u","category":"iptv","country":"US","playlist_type":"city"},
    {"name":"city_orlando","display_name":"🏙️ Orlando","url":f"{settings.IPTV_BASE_URL}/cities/usorl.m3u","category":"iptv","country":"US","playlist_type":"city"},
    {"name":"city_tampa","display_name":"🏙️ Tampa","url":f"{settings.IPTV_BASE_URL}/cities/ustpa.m3u","category":"iptv","country":"US","playlist_type":"city"},
    {"name":"city_st_louis","display_name":"🏙️ St. Louis","url":f"{settings.IPTV_BASE_URL}/cities/usstl.m3u","category":"iptv","country":"US","playlist_type":"city"},
    {"name":"city_pittsburgh","display_name":"🏙️ Pittsburgh","url":f"{settings.IPTV_BASE_URL}/cities/uspit.m3u","category":"iptv","country":"US","playlist_type":"city"},
    {"name":"city_cincinnati","display_name":"🏙️ Cincinnati","url":f"{settings.IPTV_BASE_URL}/cities/uscvg.m3u","category":"iptv","country":"US","playlist_type":"city"},
    {"name":"city_indianapolis","display_name":"🏙️ Indianapolis","url":f"{settings.IPTV_BASE_URL}/cities/usind.m3u","category":"iptv","country":"US","playlist_type":"city"},
    {"name":"city_cleveland","display_name":"🏙️ Cleveland","url":f"{settings.IPTV_BASE_URL}/cities/uscle.m3u","category":"iptv","country":"US","playlist_type":"city"},
    {"name":"city_minneapolis","display_name":"🏙️ Minneapolis","url":f"{settings.IPTV_BASE_URL}/cities/usmsp.m3u","category":"iptv","country":"US","playlist_type":"city"},
    {"name":"city_reno","display_name":"🏙️ Reno","url":f"{settings.IPTV_BASE_URL}/cities/usnvs.m3u","category":"iptv","country":"US","playlist_type":"city"},

    {"name":"city_london","display_name":"🏙️ Londres","url":f"{settings.IPTV_BASE_URL}/cities/gblon.m3u","category":"iptv","country":"GB","playlist_type":"city"},
    {"name":"city_manchester","display_name":"🏙️ Manchester","url":f"{settings.IPTV_BASE_URL}/cities/gbman.m3u","category":"iptv","country":"GB","playlist_type":"city"},
    {"name":"city_birmingham","display_name":"🏙️ Birmingham","url":f"{settings.IPTV_BASE_URL}/cities/gbbir.m3u","category":"iptv","country":"GB","playlist_type":"city"},
    {"name":"city_liverpool","display_name":"🏙️ Liverpool","url":f"{settings.IPTV_BASE_URL}/cities/gbliv.m3u","category":"iptv","country":"GB","playlist_type":"city"},
    {"name":"city_glasgow","display_name":"🏙️ Glasgow","url":f"{settings.IPTV_BASE_URL}/cities/gbglg.m3u","category":"iptv","country":"GB","playlist_type":"city"},
    {"name":"city_edinburgh","display_name":"🏙️ Édimbourg","url":f"{settings.IPTV_BASE_URL}/cities/gbedi.m3u","category":"iptv","country":"GB","playlist_type":"city"},
    {"name":"city_bristol","display_name":"🏙️ Bristol","url":f"{settings.IPTV_BASE_URL}/cities/gbbrs.m3u","category":"iptv","country":"GB","playlist_type":"city"},
    {"name":"city_leeds","display_name":"🏙️ Leeds","url":f"{settings.IPTV_BASE_URL}/cities/gblee.m3u","category":"iptv","country":"GB","playlist_type":"city"},
    {"name":"city_sheffield","display_name":"🏙️ Sheffield","url":f"{settings.IPTV_BASE_URL}/cities/gbshf.m3u","category":"iptv","country":"GB","playlist_type":"city"},
    {"name":"city_bradford","display_name":"🏙️ Bradford","url":f"{settings.IPTV_BASE_URL}/cities/gbbfd.m3u","category":"iptv","country":"GB","playlist_type":"city"},
    {"name":"city_newcastle","display_name":"🏙️ Newcastle","url":f"{settings.IPTV_BASE_URL}/cities/gbncl.m3u","category":"iptv","country":"GB","playlist_type":"city"},

    {"name":"city_paris","display_name":"🏙️ Paris","url":f"{settings.IPTV_BASE_URL}/cities/frpar.m3u","category":"iptv","country":"FR","playlist_type":"city"},
    {"name":"city_lyon","display_name":"🏙️ Lyon","url":f"{settings.IPTV_BASE_URL}/cities/frlys.m3u","category":"iptv","country":"FR","playlist_type":"city"},
    {"name":"city_marseille","display_name":"🏙️ Marseille","url":f"{settings.IPTV_BASE_URL}/cities/frmrs.m3u","category":"iptv","country":"FR","playlist_type":"city"},
    {"name":"city_toulouse","display_name":"🏙️ Toulouse","url":f"{settings.IPTV_BASE_URL}/cities/frtls.m3u","category":"iptv","country":"FR","playlist_type":"city"},
    {"name":"city_nice","display_name":"🏙️ Nice","url":f"{settings.IPTV_BASE_URL}/cities/frnc.m3u","category":"iptv","country":"FR","playlist_type":"city"},
    {"name":"city_nantes","display_name":"🏙️ Nantes","url":f"{settings.IPTV_BASE_URL}/cities/frnte.m3u","category":"iptv","country":"FR","playlist_type":"city"},
    {"name":"city_strasbourg","display_name":"🏙️ Strasbourg","url":f"{settings.IPTV_BASE_URL}/cities/frsxb.m3u","category":"iptv","country":"FR","playlist_type":"city"},
    {"name":"city_bordeaux","display_name":"🏙️ Bordeaux","url":f"{settings.IPTV_BASE_URL}/cities/frbod.m3u","category":"iptv","country":"FR","playlist_type":"city"},
    {"name":"city_lille","display_name":"🏙️ Lille","url":f"{settings.IPTV_BASE_URL}/cities/frill.m3u","category":"iptv","country":"FR","playlist_type":"city"},
    {"name":"city_rennes","display_name":"🏙️ Rennes","url":f"{settings.IPTV_BASE_URL}/cities/frren.m3u","category":"iptv","country":"FR","playlist_type":"city"},
    {"name":"city_grenoble","display_name":"🏙️ Grenoble","url":f"{settings.IPTV_BASE_URL}/cities/frgnb.m3u","category":"iptv","country":"FR","playlist_type":"city"},

    {"name":"city_berlin","display_name":"🏙️ Berlin","url":f"{settings.IPTV_BASE_URL}/cities/deber.m3u","category":"iptv","country":"DE","playlist_type":"city"},
    {"name":"city_hamburg","display_name":"🏙️ Hambourg","url":f"{settings.IPTV_BASE_URL}/cities/deham.m3u","category":"iptv","country":"DE","playlist_type":"city"},
    {"name":"city_munich","display_name":"🏙️ Munich","url":f"{settings.IPTV_BASE_URL}/cities/demuc.m3u","category":"iptv","country":"DE","playlist_type":"city"},
    {"name":"city_cologne","display_name":"🏙️ Cologne","url":f"{settings.IPTV_BASE_URL}/cities/decgn.m3u","category":"iptv","country":"DE","playlist_type":"city"},
    {"name":"city_frankfurt","display_name":"🏙️ Francfort","url":f"{settings.IPTV_BASE_URL}/cities/defra.m3u","category":"iptv","country":"DE","playlist_type":"city"},
    {"name":"city_stuttgart","display_name":"🏙️ Stuttgart","url":f"{settings.IPTV_BASE_URL}/cities/destr.m3u","category":"iptv","country":"DE","playlist_type":"city"},
    {"name":"city_dusseldorf","display_name":"🏙️ Düsseldorf","url":f"{settings.IPTV_BASE_URL}/cities/dedus.m3u","category":"iptv","country":"DE","playlist_type":"city"},

    {"name":"city_rome","display_name":"🏙️ Rome","url":f"{settings.IPTV_BASE_URL}/cities/itrom.m3u","category":"iptv","country":"IT","playlist_type":"city"},
    {"name":"city_milan","display_name":"🏙️ Milan","url":f"{settings.IPTV_BASE_URL}/cities/itmil.m3u","category":"iptv","country":"IT","playlist_type":"city"},
    {"name":"city_naples","display_name":"🏙️ Naples","url":f"{settings.IPTV_BASE_URL}/cities/itnap.m3u","category":"iptv","country":"IT","playlist_type":"city"},
    {"name":"city_turin","display_name":"🏙️ Turin","url":f"{settings.IPTV_BASE_URL}/cities/ittrn.m3u","category":"iptv","country":"IT","playlist_type":"city"},
    {"name":"city_palermo","display_name":"🏙️ Palerme","url":f"{settings.IPTV_BASE_URL}/cities/itpal.m3u","category":"iptv","country":"IT","playlist_type":"city"},
    {"name":"city_genoa","display_name":"🏙️ Gênes","url":f"{settings.IPTV_BASE_URL}/cities/itgoa.m3u","category":"iptv","country":"IT","playlist_type":"city"},

    {"name":"city_madrid","display_name":"🏙️ Madrid","url":f"{settings.IPTV_BASE_URL}/cities/esmad.m3u","category":"iptv","country":"ES","playlist_type":"city"},
    {"name":"city_barcelona","display_name":"🏙️ Barcelone","url":f"{settings.IPTV_BASE_URL}/cities/esbcn.m3u","category":"iptv","country":"ES","playlist_type":"city"},
    {"name":"city_valencia","display_name":"🏙️ Valence","url":f"{settings.IPTV_BASE_URL}/cities/esvll.m3u","category":"iptv","country":"ES","playlist_type":"city"},
    {"name":"city_seville","display_name":"🏙️ Séville","url":f"{settings.IPTV_BASE_URL}/cities/essvq.m3u","category":"iptv","country":"ES","playlist_type":"city"},
    {"name":"city_zaragoza","display_name":"🏙️ Saragosse","url":f"{settings.IPTV_BASE_URL}/cities/eszzg.m3u","category":"iptv","country":"ES","playlist_type":"city"},
    {"name":"city_malaga","display_name":"🏙️ Malaga","url":f"{settings.IPTV_BASE_URL}/cities/esmgz.m3u","category":"iptv","country":"ES","playlist_type":"city"},

    {"name":"city_moscow","display_name":"🏙️ Moscou","url":f"{settings.IPTV_BASE_URL}/cities/rumow.m3u","category":"iptv","country":"RU","playlist_type":"city"},
    {"name":"city_saint_petersburg","display_name":"🏙️ Saint-Pétersbourg","url":f"{settings.IPTV_BASE_URL}/cities/ruced.m3u","category":"iptv","country":"RU","playlist_type":"city"},
    {"name":"city_novosibirsk","display_name":"🏙️ Novossibirsk","url":f"{settings.IPTV_BASE_URL}/cities/ruovb.m3u","category":"iptv","country":"RU","playlist_type":"city"},

    {"name":"city_rio_de_janeiro","display_name":"🏙️ Rio de Janeiro","url":f"{settings.IPTV_BASE_URL}/cities/brrioa.m3u","category":"iptv","country":"BR","playlist_type":"city"},
    {"name":"city_sao_paulo","display_name":"🏙️ São Paulo","url":f"{settings.IPTV_BASE_URL}/cities/brsaopaulo.m3u","category":"iptv","country":"BR","playlist_type":"city"},
    {"name":"city_brasilia","display_name":"🏙️ Brasília","url":f"{settings.IPTV_BASE_URL}/cities/brbsb.m3u","category":"iptv","country":"BR","playlist_type":"city"},
    {"name":"city_salvador","display_name":"🏙️ Salvador","url":f"{settings.IPTV_BASE_URL}/cities/brssa.m3u","category":"iptv","country":"BR","playlist_type":"city"},

    {"name":"city_sydney","display_name":"🏙️ Sydney","url":f"{settings.IPTV_BASE_URL}/cities/ausyd.m3u","category":"iptv","country":"AU","playlist_type":"city"},
    {"name":"city_melbourne","display_name":"🏙️ Melbourne","url":f"{settings.IPTV_BASE_URL}/cities/aumel.m3u","category":"iptv","country":"AU","playlist_type":"city"},
    {"name":"city_brisbane","display_name":"🏙️ Brisbane","url":f"{settings.IPTV_BASE_URL}/cities/aubne.m3u","category":"iptv","country":"AU","playlist_type":"city"},
    {"name":"city_perth","display_name":"🏙️ Perth","url":f"{settings.IPTV_BASE_URL}/cities/auper.m3u","category":"iptv","country":"AU","playlist_type":"city"},
    {"name":"city_adelaide","display_name":"🏙️ Adélaïde","url":f"{settings.IPTV_BASE_URL}/cities/auadl.m3u","category":"iptv","country":"AU","playlist_type":"city"},
    {"name":"city_canberra","display_name":"🏙️ Canberra","url":f"{settings.IPTV_BASE_URL}/cities/aucbr.m3u","category":"iptv","country":"AU","playlist_type":"city"},

    {"name":"city_tokyo","display_name":"🏙️ Tokyo","url":f"{settings.IPTV_BASE_URL}/cities/jptyo.m3u","category":"iptv","country":"JP","playlist_type":"city"},
    {"name":"city_osaka","display_name":"🏙️ Osaka","url":f"{settings.IPTV_BASE_URL}/cities/jposa.m3u","category":"iptv","country":"JP","playlist_type":"city"},
    {"name":"city_nagoya","display_name":"🏙️ Nagoya","url":f"{settings.IPTV_BASE_URL}/cities/jpngo.m3u","category":"iptv","country":"JP","playlist_type":"city"},
    {"name":"city_sapporo","display_name":"🏙️ Sapporo","url":f"{settings.IPTV_BASE_URL}/cities/jpspk.m3u","category":"iptv","country":"JP","playlist_type":"city"},
    {"name":"city_fukuoka","display_name":"🏙️ Fukuoka","url":f"{settings.IPTV_BASE_URL}/cities/jpfuk.m3u","category":"iptv","country":"JP","playlist_type":"city"},

    {"name":"city_seoul","display_name":"🏙️ Séoul","url":f"{settings.IPTV_BASE_URL}/cities/krsel.m3u","category":"iptv","country":"KR","playlist_type":"city"},
    {"name":"city_busan","display_name":"🏙️ Busan","url":f"{settings.IPTV_BASE_URL}/cities/krpus.m3u","category":"iptv","country":"KR","playlist_type":"city"},

    {"name":"city_beijing","display_name":"🏙️ Pékin","url":f"{settings.IPTV_BASE_URL}/cities/cnbjs.m3u","category":"iptv","country":"CN","playlist_type":"city"},
    {"name":"city_shanghai","display_name":"🏙️ Shanghai","url":f"{settings.IPTV_BASE_URL}/cities/cnsha.m3u","category":"iptv","country":"CN","playlist_type":"city"},
    {"name":"city_guangzhou","display_name":"🏙️ Canton","url":f"{settings.IPTV_BASE_URL}/cities/cncan.m3u","category":"iptv","country":"CN","playlist_type":"city"},
    {"name":"city_shenzhen","display_name":"🏙️ Shenzhen","url":f"{settings.IPTV_BASE_URL}/cities/cnszx.m3u","category":"iptv","country":"CN","playlist_type":"city"},
    {"name":"city_chengdu","display_name":"🏙️ Chengdu","url":f"{settings.IPTV_BASE_URL}/cities/cnctu.m3u","category":"iptv","country":"CN","playlist_type":"city"},

    {"name":"city_mumbai","display_name":"🏙️ Bombay","url":f"{settings.IPTV_BASE_URL}/cities/inbom.m3u","category":"iptv","country":"IN","playlist_type":"city"},
    {"name":"city_delhi","display_name":"🏙️ Delhi","url":f"{settings.IPTV_BASE_URL}/cities/indel.m3u","category":"iptv","country":"IN","playlist_type":"city"},
    {"name":"city_bangalore","display_name":"🏙️ Bangalore","url":f"{settings.IPTV_BASE_URL}/cities/inblr.m3u","category":"iptv","country":"IN","playlist_type":"city"},
    {"name":"city_chennai","display_name":"🏙️ Chennai","url":f"{settings.IPTV_BASE_URL}/cities/inmaa.m3u","category":"iptv","country":"IN","playlist_type":"city"},
    {"name":"city_kolkata","display_name":"🏙️ Calcutta","url":f"{settings.IPTV_BASE_URL}/cities/inccu.m3u","category":"iptv","country":"IN","playlist_type":"city"},

    # ---- CATÉGORIES THÉMATIQUES ----
    {"name":"sports","display_name":"⚽ Sports (Monde)","url":f"{settings.IPTV_BASE_URL}/categories/sports.m3u","category":"iptv_sports","country":"INT","playlist_type":"category"},
    {"name":"news","display_name":"📰 Info (Monde)","url":f"{settings.IPTV_BASE_URL}/categories/news.m3u","category":"iptv_news","country":"INT","playlist_type":"category"},
    {"name":"documentary","display_name":"🎥 Documentaires","url":f"{settings.IPTV_BASE_URL}/categories/documentary.m3u","category":"iptv_documentary","country":"INT","playlist_type":"category"},
    {"name":"music","display_name":"🎵 Musique","url":f"{settings.IPTV_BASE_URL}/categories/music.m3u","category":"iptv_music","country":"INT","playlist_type":"category"},
    {"name":"kids","display_name":"🧸 Jeunesse","url":f"{settings.IPTV_BASE_URL}/categories/kids.m3u","category":"iptv_kids","country":"INT","playlist_type":"category"},
    {"name":"movies","display_name":"🎬 Films","url":f"{settings.IPTV_BASE_URL}/categories/movies.m3u","category":"iptv_movies","country":"INT","playlist_type":"category"},
    {"name":"science","display_name":"🔬 Science / Nature","url":f"{settings.IPTV_BASE_URL}/categories/science.m3u","category":"iptv_science","country":"INT","playlist_type":"category"},
    {"name":"travel","display_name":"✈️ Voyage","url":f"{settings.IPTV_BASE_URL}/categories/travel.m3u","category":"iptv_travel","country":"INT","playlist_type":"category"},
    {"name":"religion","display_name":"🕊️ Religion","url":f"{settings.IPTV_BASE_URL}/categories/religion.m3u","category":"iptv_religion","country":"INT","playlist_type":"category"},
    {"name":"business","display_name":"💼 Business / Finance","url":f"{settings.IPTV_BASE_URL}/categories/business.m3u","category":"iptv_business","country":"INT","playlist_type":"category"},
    {"name":"entertainment","display_name":"🎭 Divertissement","url":f"{settings.IPTV_BASE_URL}/categories/entertainment.m3u","category":"iptv","country":"INT","playlist_type":"category"},
    {"name":"cooking","display_name":"🍳 Cuisine","url":f"{settings.IPTV_BASE_URL}/categories/cooking.m3u","category":"iptv","country":"INT","playlist_type":"category"},
    {"name":"auto","display_name":"🚗 Auto / Moto","url":f"{settings.IPTV_BASE_URL}/categories/auto.m3u","category":"iptv","country":"INT","playlist_type":"category"},
    {"name":"animation","display_name":"🖌️ Animation","url":f"{settings.IPTV_BASE_URL}/categories/animation.m3u","category":"iptv_kids","country":"INT","playlist_type":"category"},
    {"name":"comedy","display_name":"😂 Comédie","url":f"{settings.IPTV_BASE_URL}/categories/comedy.m3u","category":"iptv","country":"INT","playlist_type":"category"},
    {"name":"education","display_name":"📚 Éducation","url":f"{settings.IPTV_BASE_URL}/categories/education.m3u","category":"iptv","country":"INT","playlist_type":"category"},
    {"name":"fashion","display_name":"👗 Mode","url":f"{settings.IPTV_BASE_URL}/categories/fashion.m3u","category":"iptv","country":"INT","playlist_type":"category"},
    {"name":"gaming","display_name":"🎮 Jeux vidéo","url":f"{settings.IPTV_BASE_URL}/categories/gaming.m3u","category":"gaming","country":"INT","playlist_type":"category"},
    {"name":"health","display_name":"💪 Santé / Bien-être","url":f"{settings.IPTV_BASE_URL}/categories/health.m3u","category":"iptv","country":"INT","playlist_type":"category"},
    {"name":"history","display_name":"📜 Histoire","url":f"{settings.IPTV_BASE_URL}/categories/history.m3u","category":"iptv","country":"INT","playlist_type":"category"},
    {"name":"horror","display_name":"👻 Horreur","url":f"{settings.IPTV_BASE_URL}/categories/horror.m3u","category":"iptv","country":"INT","playlist_type":"category"},
    {"name":"legislative","display_name":"🏛️ Parlement","url":f"{settings.IPTV_BASE_URL}/categories/legislative.m3u","category":"iptv","country":"INT","playlist_type":"category"},
    {"name":"lifestyle","display_name":"🌿 Lifestyle","url":f"{settings.IPTV_BASE_URL}/categories/lifestyle.m3u","category":"iptv","country":"INT","playlist_type":"category"},
    {"name":"local","display_name":"📍 Locale","url":f"{settings.IPTV_BASE_URL}/categories/local.m3u","category":"iptv","country":"INT","playlist_type":"category"},
    {"name":"nature","display_name":"🌲 Nature","url":f"{settings.IPTV_BASE_URL}/categories/nature.m3u","category":"iptv_science","country":"INT","playlist_type":"category"},
    {"name":"outdoor","display_name":"🏕️ Plein air","url":f"{settings.IPTV_BASE_URL}/categories/outdoor.m3u","category":"iptv","country":"INT","playlist_type":"category"},
    {"name":"quiz","display_name":"❓ Quiz / Jeux","url":f"{settings.IPTV_BASE_URL}/categories/quiz.m3u","category":"iptv","country":"INT","playlist_type":"category"},
    {"name":"radio","display_name":"📻 Radio","url":f"{settings.IPTV_BASE_URL}/categories/radio.m3u","category":"radio","country":"INT","playlist_type":"category"},
    {"name":"religious","display_name":"⛪ Religieux","url":f"{settings.IPTV_BASE_URL}/categories/religious.m3u","category":"religion","country":"INT","playlist_type":"category"},
    {"name":"series","display_name":"📺 Séries","url":f"{settings.IPTV_BASE_URL}/categories/series.m3u","category":"iptv","country":"INT","playlist_type":"category"},
    {"name":"shop","display_name":"🛍️ Téléachat","url":f"{settings.IPTV_BASE_URL}/categories/shop.m3u","category":"iptv","country":"INT","playlist_type":"category"},
    {"name":"soap","display_name":"🧼 Feuilletons","url":f"{settings.IPTV_BASE_URL}/categories/soap.m3u","category":"iptv","country":"INT","playlist_type":"category"},
    {"name":"tech","display_name":"💻 Technologie","url":f"{settings.IPTV_BASE_URL}/categories/tech.m3u","category":"iptv","country":"INT","playlist_type":"category"},
    {"name":"weather","display_name":"☀️ Météo","url":f"{settings.IPTV_BASE_URL}/categories/weather.m3u","category":"iptv","country":"INT","playlist_type":"category"},
    # ---- PAYS MANQUANTS (complétant les 196 pays) ----
    {"name":"albania","display_name":"🇦🇱 Albanie","url":f"{settings.IPTV_BASE_URL}/countries/al.m3u","category":"iptv","country":"AL","playlist_type":"country"},
    {"name":"andorra","display_name":"🇦🇩 Andorre","url":f"{settings.IPTV_BASE_URL}/countries/ad.m3u","category":"iptv","country":"AD","playlist_type":"country"},
    {"name":"antigua","display_name":"🇦🇬 Antigua-et-Barbuda","url":f"{settings.IPTV_BASE_URL}/countries/ag.m3u","category":"iptv","country":"AG","playlist_type":"country"},
    {"name":"bahamas","display_name":"🇧🇸 Bahamas","url":f"{settings.IPTV_BASE_URL}/countries/bs.m3u","category":"iptv","country":"BS","playlist_type":"country"},
    {"name":"bahrain","display_name":"🇧🇭 Bahreïn","url":f"{settings.IPTV_BASE_URL}/countries/bh.m3u","category":"iptv","country":"BH","playlist_type":"country"},
    {"name":"barbados","display_name":"🇧🇧 Barbade","url":f"{settings.IPTV_BASE_URL}/countries/bb.m3u","category":"iptv","country":"BB","playlist_type":"country"},
    {"name":"belize","display_name":"🇧🇿 Belize","url":f"{settings.IPTV_BASE_URL}/countries/bz.m3u","category":"iptv","country":"BZ","playlist_type":"country"},
    {"name":"bhutan","display_name":"🇧🇹 Bhoutan","url":f"{settings.IPTV_BASE_URL}/countries/bt.m3u","category":"iptv","country":"BT","playlist_type":"country"},
    {"name":"bosnia","display_name":"🇧🇦 Bosnie-Herzégovine","url":f"{settings.IPTV_BASE_URL}/countries/ba.m3u","category":"iptv","country":"BA","playlist_type":"country"},
    {"name":"brunei","display_name":"🇧🇳 Brunei","url":f"{settings.IPTV_BASE_URL}/countries/bn.m3u","category":"iptv","country":"BN","playlist_type":"country"},
    {"name":"cabo_verde","display_name":"🇨🇻 Cap-Vert","url":f"{settings.IPTV_BASE_URL}/countries/cv.m3u","category":"iptv","country":"CV","playlist_type":"country"},
    {"name":"comoros","display_name":"🇰🇲 Comores","url":f"{settings.IPTV_BASE_URL}/countries/km.m3u","category":"iptv","country":"KM","playlist_type":"country"},
    {"name":"costa_rica","display_name":"🇨🇷 Costa Rica","url":f"{settings.IPTV_BASE_URL}/countries/cr.m3u","category":"iptv","country":"CR","playlist_type":"country"},
    {"name":"cuba","display_name":"🇨🇺 Cuba","url":f"{settings.IPTV_BASE_URL}/countries/cu.m3u","category":"iptv","country":"CU","playlist_type":"country"},
    {"name":"djibouti","display_name":"🇩🇯 Djibouti","url":f"{settings.IPTV_BASE_URL}/countries/dj.m3u","category":"iptv","country":"DJ","playlist_type":"country"},
    {"name":"dominica","display_name":"🇩🇲 Dominique","url":f"{settings.IPTV_BASE_URL}/countries/dm.m3u","category":"iptv","country":"DM","playlist_type":"country"},
    {"name":"dominican_rep","display_name":"🇩🇴 République Dominicaine","url":f"{settings.IPTV_BASE_URL}/countries/do.m3u","category":"iptv","country":"DO","playlist_type":"country"},
    {"name":"equatorial_guinea","display_name":"🇬🇶 Guinée Équatoriale","url":f"{settings.IPTV_BASE_URL}/countries/gq.m3u","category":"iptv","country":"GQ","playlist_type":"country"},
    {"name":"eritrea","display_name":"🇪🇷 Érythrée","url":f"{settings.IPTV_BASE_URL}/countries/er.m3u","category":"iptv","country":"ER","playlist_type":"country"},
    {"name":"fiji","display_name":"🇫🇯 Fidji","url":f"{settings.IPTV_BASE_URL}/countries/fj.m3u","category":"iptv","country":"FJ","playlist_type":"country"},
    {"name":"grenada","display_name":"🇬🇩 Grenade","url":f"{settings.IPTV_BASE_URL}/countries/gd.m3u","category":"iptv","country":"GD","playlist_type":"country"},
    {"name":"guatemala","display_name":"🇬🇹 Guatemala","url":f"{settings.IPTV_BASE_URL}/countries/gt.m3u","category":"iptv","country":"GT","playlist_type":"country"},
    {"name":"guinea_bissau","display_name":"🇬🇼 Guinée-Bissau","url":f"{settings.IPTV_BASE_URL}/countries/gw.m3u","category":"iptv","country":"GW","playlist_type":"country"},
    {"name":"guyana","display_name":"🇬🇾 Guyana","url":f"{settings.IPTV_BASE_URL}/countries/gy.m3u","category":"iptv","country":"GY","playlist_type":"country"},
    {"name":"haiti","display_name":"🇭🇹 Haïti","url":f"{settings.IPTV_BASE_URL}/countries/ht.m3u","category":"iptv","country":"HT","playlist_type":"country"},
    {"name":"honduras","display_name":"🇭🇳 Honduras","url":f"{settings.IPTV_BASE_URL}/countries/hn.m3u","category":"iptv","country":"HN","playlist_type":"country"},
    {"name":"hongkong","display_name":"🇭🇰 Hong Kong","url":f"{settings.IPTV_BASE_URL}/countries/hk.m3u","category":"iptv","country":"HK","playlist_type":"country"},
    {"name":"jamaica","display_name":"🇯🇲 Jamaïque","url":f"{settings.IPTV_BASE_URL}/countries/jm.m3u","category":"iptv","country":"JM","playlist_type":"country"},
    {"name":"kiribati","display_name":"🇰🇮 Kiribati","url":f"{settings.IPTV_BASE_URL}/countries/ki.m3u","category":"iptv","country":"KI","playlist_type":"country"},
    {"name":"north_korea","display_name":"🇰🇵 Corée du Nord","url":f"{settings.IPTV_BASE_URL}/countries/kp.m3u","category":"iptv","country":"KP","playlist_type":"country"},
    {"name":"lesotho","display_name":"🇱🇸 Lesotho","url":f"{settings.IPTV_BASE_URL}/countries/ls.m3u","category":"iptv","country":"LS","playlist_type":"country"},
    {"name":"liberia","display_name":"🇱🇷 Libéria","url":f"{settings.IPTV_BASE_URL}/countries/lr.m3u","category":"iptv","country":"LR","playlist_type":"country"},
    {"name":"liechtenstein","display_name":"🇱🇮 Liechtenstein","url":f"{settings.IPTV_BASE_URL}/countries/li.m3u","category":"iptv","country":"LI","playlist_type":"country"},
    {"name":"maldives","display_name":"🇲🇻 Maldives","url":f"{settings.IPTV_BASE_URL}/countries/mv.m3u","category":"iptv","country":"MV","playlist_type":"country"},
    {"name":"marshall_islands","display_name":"🇲🇭 Îles Marshall","url":f"{settings.IPTV_BASE_URL}/countries/mh.m3u","category":"iptv","country":"MH","playlist_type":"country"},
    {"name":"micronesia","display_name":"🇫🇲 Micronésie","url":f"{settings.IPTV_BASE_URL}/countries/fm.m3u","category":"iptv","country":"FM","playlist_type":"country"},
    {"name":"moldova","display_name":"🇲🇩 Moldavie","url":f"{settings.IPTV_BASE_URL}/countries/md.m3u","category":"iptv","country":"MD","playlist_type":"country"},
    {"name":"mongolia","display_name":"🇲🇳 Mongolie","url":f"{settings.IPTV_BASE_URL}/countries/mn.m3u","category":"iptv","country":"MN","playlist_type":"country"},
    {"name":"montenegro","display_name":"🇲🇪 Monténégro","url":f"{settings.IPTV_BASE_URL}/countries/me.m3u","category":"iptv","country":"ME","playlist_type":"country"},
    {"name":"mozambique","display_name":"🇲🇿 Mozambique","url":f"{settings.IPTV_BASE_URL}/countries/mz.m3u","category":"iptv","country":"MZ","playlist_type":"country"},
    {"name":"myanmar","display_name":"🇲🇲 Myanmar","url":f"{settings.IPTV_BASE_URL}/countries/mm.m3u","category":"iptv","country":"MM","playlist_type":"country"},
    {"name":"nauru","display_name":"🇳🇷 Nauru","url":f"{settings.IPTV_BASE_URL}/countries/nr.m3u","category":"iptv","country":"NR","playlist_type":"country"},
    {"name":"nicaragua","display_name":"🇳🇮 Nicaragua","url":f"{settings.IPTV_BASE_URL}/countries/ni.m3u","category":"iptv","country":"NI","playlist_type":"country"},
    {"name":"north_macedonia","display_name":"🇲🇰 Macédoine du Nord","url":f"{settings.IPTV_BASE_URL}/countries/mk.m3u","category":"iptv","country":"MK","playlist_type":"country"},
    {"name":"palau","display_name":"🇵🇼 Palaos","url":f"{settings.IPTV_BASE_URL}/countries/pw.m3u","category":"iptv","country":"PW","playlist_type":"country"},
    {"name":"panama","display_name":"🇵🇦 Panama","url":f"{settings.IPTV_BASE_URL}/countries/pa.m3u","category":"iptv","country":"PA","playlist_type":"country"},
    {"name":"papua_new_guinea","display_name":"🇵🇬 Papouasie-Nouvelle-Guinée","url":f"{settings.IPTV_BASE_URL}/countries/pg.m3u","category":"iptv","country":"PG","playlist_type":"country"},
    {"name":"saint_kitts","display_name":"🇰🇳 Saint-Kitts-et-Nevis","url":f"{settings.IPTV_BASE_URL}/countries/kn.m3u","category":"iptv","country":"KN","playlist_type":"country"},
    {"name":"saint_lucia","display_name":"🇱🇨 Sainte-Lucie","url":f"{settings.IPTV_BASE_URL}/countries/lc.m3u","category":"iptv","country":"LC","playlist_type":"country"},
    {"name":"saint_vincent","display_name":"🇻🇨 Saint-Vincent","url":f"{settings.IPTV_BASE_URL}/countries/vc.m3u","category":"iptv","country":"VC","playlist_type":"country"},
    {"name":"samoa","display_name":"🇼🇸 Samoa","url":f"{settings.IPTV_BASE_URL}/countries/ws.m3u","category":"iptv","country":"WS","playlist_type":"country"},
    {"name":"san_marino","display_name":"🇸🇲 Saint-Marin","url":f"{settings.IPTV_BASE_URL}/countries/sm.m3u","category":"iptv","country":"SM","playlist_type":"country"},
    {"name":"sao_tome","display_name":"🇸🇹 Sao Tomé","url":f"{settings.IPTV_BASE_URL}/countries/st.m3u","category":"iptv","country":"ST","playlist_type":"country"},
    {"name":"sierra_leone","display_name":"🇸🇱 Sierra Leone","url":f"{settings.IPTV_BASE_URL}/countries/sl.m3u","category":"iptv","country":"SL","playlist_type":"country"},
    {"name":"solomon_islands","display_name":"🇸🇧 Îles Salomon","url":f"{settings.IPTV_BASE_URL}/countries/sb.m3u","category":"iptv","country":"SB","playlist_type":"country"},
    {"name":"somalia","display_name":"🇸🇴 Somalie","url":f"{settings.IPTV_BASE_URL}/countries/so.m3u","category":"iptv","country":"SO","playlist_type":"country"},
    {"name":"south_sudan","display_name":"🇸🇸 Soudan du Sud","url":f"{settings.IPTV_BASE_URL}/countries/ss.m3u","category":"iptv","country":"SS","playlist_type":"country"},
    {"name":"suriname","display_name":"🇸🇷 Suriname","url":f"{settings.IPTV_BASE_URL}/countries/sr.m3u","category":"iptv","country":"SR","playlist_type":"country"},
    {"name":"taiwan","display_name":"🇹🇼 Taïwan","url":f"{settings.IPTV_BASE_URL}/countries/tw.m3u","category":"iptv","country":"TW","playlist_type":"country"},
    {"name":"tajikistan","display_name":"🇹🇯 Tadjikistan","url":f"{settings.IPTV_BASE_URL}/countries/tj.m3u","category":"iptv","country":"TJ","playlist_type":"country"},
    {"name":"timor_leste","display_name":"🇹🇱 Timor-Leste","url":f"{settings.IPTV_BASE_URL}/countries/tl.m3u","category":"iptv","country":"TL","playlist_type":"country"},
    {"name":"tonga","display_name":"🇹🇴 Tonga","url":f"{settings.IPTV_BASE_URL}/countries/to.m3u","category":"iptv","country":"TO","playlist_type":"country"},
    {"name":"trinidad","display_name":"🇹🇹 Trinité-et-Tobago","url":f"{settings.IPTV_BASE_URL}/countries/tt.m3u","category":"iptv","country":"TT","playlist_type":"country"},
    {"name":"tuvalu","display_name":"🇹🇻 Tuvalu","url":f"{settings.IPTV_BASE_URL}/countries/tv.m3u","category":"iptv","country":"TV","playlist_type":"country"},
    {"name":"vanuatu","display_name":"🇻🇺 Vanuatu","url":f"{settings.IPTV_BASE_URL}/countries/vu.m3u","category":"iptv","country":"VU","playlist_type":"country"},
    {"name":"vatican","display_name":"🇻🇦 Vatican","url":f"{settings.IPTV_BASE_URL}/countries/va.m3u","category":"iptv","country":"VA","playlist_type":"country"},
    {"name":"yemen","display_name":"🇾🇪 Yémen","url":f"{settings.IPTV_BASE_URL}/countries/ye.m3u","category":"iptv","country":"YE","playlist_type":"country"},
]
# Contenu adulte retiré intentionnellement

# ==================== CATÉGORIES ====================

CATEGORIES = [
    {"id":"sports", "name":"Sports", "icon":"⚽", "color":"blue", "bg":"bg-blue-100 dark:bg-blue-900/20", "text":"text-blue-600"},
    {"id":"news", "name":"News", "icon":"📰", "color":"red", "bg":"bg-red-100 dark:bg-red-900/20", "text":"text-red-600"},
    {"id":"entertainment", "name":"Divertissement", "icon":"🎬", "color":"purple", "bg":"bg-purple-100 dark:bg-purple-900/20", "text":"text-purple-600"},
    {"id":"religion", "name":"Religion", "icon":"🕌", "color":"green", "bg":"bg-green-100 dark:bg-green-900/20", "text":"text-green-600"},
    {"id":"radio", "name":"Radio", "icon":"📻", "color":"orange", "bg":"bg-orange-100 dark:bg-orange-900/20", "text":"text-orange-600"},
    {"id":"webcam", "name":"Webcams", "icon":"📹", "color":"cyan", "bg":"bg-cyan-100 dark:bg-cyan-900/20", "text":"text-cyan-600"},
    {"id":"science", "name":"Science", "icon":"🔬", "color":"teal", "bg":"bg-teal-100 dark:bg-teal-900/20", "text":"text-teal-600"},
    {"id":"iptv", "name":"IPTV Monde", "icon":"🌍", "color":"indigo", "bg":"bg-indigo-100 dark:bg-indigo-900/20", "text":"text-indigo-600"},
    {"id":"iptv_sports", "name":"IPTV Sports", "icon":"⚽", "color":"blue", "bg":"bg-blue-100 dark:bg-blue-900/20", "text":"text-blue-600"},
    {"id":"iptv_news", "name":"IPTV Info", "icon":"📰", "color":"red", "bg":"bg-red-100 dark:bg-red-900/20", "text":"text-red-600"},
    {"id":"iptv_documentary", "name":"IPTV Docs", "icon":"🎥", "color":"amber", "bg":"bg-amber-100 dark:bg-amber-900/20", "text":"text-amber-600"},
    {"id":"iptv_music", "name":"IPTV Musique", "icon":"🎵", "color":"pink", "bg":"bg-pink-100 dark:bg-pink-900/20", "text":"text-pink-600"},
    {"id":"iptv_kids", "name":"IPTV Jeunesse", "icon":"🧸", "color":"yellow", "bg":"bg-yellow-100 dark:bg-yellow-900/20", "text":"text-yellow-600"},
    {"id":"iptv_movies", "name":"IPTV Films", "icon":"🎬", "color":"purple", "bg":"bg-purple-100 dark:bg-purple-900/20", "text":"text-purple-600"},
    {"id":"iptv_science", "name":"IPTV Science", "icon":"🔬", "color":"teal", "bg":"bg-teal-100 dark:bg-teal-900/20", "text":"text-teal-600"},
    {"id":"iptv_travel", "name":"IPTV Voyage", "icon":"✈️", "color":"emerald", "bg":"bg-emerald-100 dark:bg-emerald-900/20", "text":"text-emerald-600"},
    {"id":"iptv_business", "name":"IPTV Business", "icon":"💼", "color":"slate", "bg":"bg-slate-100 dark:bg-slate-900/20", "text":"text-slate-600"},
    {"id":"gaming", "name":"Gaming", "icon":"🎮", "color":"fuchsia", "bg":"bg-fuchsia-100 dark:bg-fuchsia-900/20", "text":"text-fuchsia-600"},
]

# Mapping catégories UI → mots-clés IPTV réels (group-title dans les M3U)
CATEGORY_IPTV_KEYWORDS = {
    "sports":           ["sport", "sports", "football", "soccer", "basketball", "tennis", "boxing", "wrestling"],
    "news":             ["news", "info", "actualit", "journal", "infos"],
    "entertainment":    ["entertainment", "general", "variety", "divertissement", "show"],
    "religion":         ["religion", "religious", "faith", "islamic", "christian", "prayer"],
    "radio":            ["radio", "music radio", "audio"],
    "science":          ["science", "nature", "documentary", "docu"],
    "gaming":           ["gaming", "game", "esport", "esports"],
    "iptv":             [],
    "iptv_sports":      ["sport", "sports", "football", "soccer", "basketball", "tennis"],
    "iptv_news":        ["news", "info", "actualit", "journal"],
    "iptv_documentary": ["documentary", "docu", "document"],
    "iptv_music":       ["music", "musique", "musical"],
    "iptv_kids":        ["kids", "children", "jeunesse", "child", "cartoon", "animation"],
    "iptv_movies":      ["movies", "movie", "cinema", "film", "films"],
    "iptv_science":     ["science", "nature", "educational"],
    "iptv_travel":      ["travel", "voyage", "geographic", "geography"],
    "iptv_business":    ["business", "finance", "economy", "economic"],
}

INAPPROPRIATE_WORDS = {
    'fr': [
        'merde', 'putain', 'connard', 'salope', 'enculé', 'bâtard', 'fils de pute', 'filsdepute',
        'niquer', 'bite', 'couille', 'chatte', 'pd', 'tapette', 'pédale', 'enfoiré', 'salaud',
        'ordure', 'racaille', 'bouffon', 'sac à merde', 'sacrement', 'nique ta mère', 'ntm',
        'tg', 'ta gueule', 'ferme ta gueule', 'gros con', 'grosse conne', 'abruti', 'débile',
        'attardé', 'crétin', 'imbécile', 'taré', 'fou', 'cinglé', 'malade mental', 'triso',
        'mongol', 'sous-merde', 'encule', 'enculer', 'nique', 'niquer', 'putain de merde',
        'bordel', 'putain de bordel', 'merdeux', 'merdeuse', 'chiard', 'chienne', 'saloperie',
        'connerie', 'con', 'conne', 'connasse', 'connard', 'connarde', 'batard', 'batarde',
    ],
    'en': [
        'fuck', 'shit', 'bitch', 'asshole', 'cunt', 'motherfucker', 'mother fucker', 'dick',
        'pussy', 'whore', 'slut', 'bastard', 'faggot', 'retard', 'nigger', 'nigga', 'chink',
        'spic', 'kike', 'gook', 'wetback', 'beaner', 'cracker', 'redneck', 'hillbilly',
        'douche', 'douchebag', 'jackass', 'dumbass', 'dumb ass', 'dipshit', 'dip shit',
        'piss off', 'suck my dick', 'blow me', 'eat shit', 'go to hell', 'fuck you', 'fuck off',
        'fuck this', 'bullshit', 'bull shit', 'horseshit', 'horse shit', 'crap', 'damn',
        'goddamn', 'hell', 'prick', 'twat', 'wanker', 'tosser', 'bollocks', 'arsehole',
    ]
}

# ==================== MOTS INAPPROPRIÉS ====================

class YouTubeService:
    """Résolution des URLs YouTube avec cache et fallback multiple"""

    def __init__(self):
        self.cache = {}
        self.session = None

    async def get_session(self):
        if not self.session:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=settings.YOUTUBE_TIMEOUT),
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            )
        return self.session

    @staticmethod
    def extract_video_id(url: str) -> Optional[str]:
        patterns = [
            r'(?:v=|youtu\.be/|embed/|shorts/|live/)([A-Za-z0-9_-]{11})',
            r'^([A-Za-z0-9_-]{11})$'
        ]
        for pattern in patterns:
            m = re.search(pattern, url)
            if m:
                return m.group(1)
        return None

    async def get_stream_url(self, youtube_url: str) -> dict:
        """Retourne l'URL du stream avec plusieurs stratégies de fallback"""
        cache_key = f"yt_{youtube_url}"
        if cache_key in self.cache:
            cached = self.cache[cache_key]
            if datetime.utcnow() < cached["expires"]:
                return cached["data"]

        video_id = self.extract_video_id(youtube_url)
        if not video_id:
            return {"error": "ID vidéo invalide", "type": "error"}

        # Stratégie 1 : yt-dlp (meilleure qualité)
        if YT_DLP_AVAILABLE:
            try:
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(None, self._ytdlp_extract, youtube_url)
                if result and not result.get("error"):
                    self.cache[cache_key] = {"data": result, "expires": datetime.utcnow() + timedelta(seconds=settings.YOUTUBE_CACHE_TTL)}
                    return result
            except Exception as e:
                logger.warning(f"yt-dlp error: {e}")

        # Stratégie 2 : API noembed (fallback)
        try:
            session = await self.get_session()
            async with session.get(f"https://noembed.com/embed?url={quote(youtube_url)}") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data and data.get("title"):
                        # Si on a des infos, on propose l'embed
                        result = {
                            "type": "embed",
                            "embed_url": f"https://www.youtube.com/embed/{video_id}?autoplay=1&mute=0&enablejsapi=1",
                            "video_id": video_id,
                            "title": data.get("title"),
                            "author": data.get("author_name"),
                        }
                        self.cache[cache_key] = {"data": result, "expires": datetime.utcnow() + timedelta(seconds=settings.YOUTUBE_CACHE_TTL)}
                        return result
        except Exception as e:
            logger.debug(f"Noembed error: {e}")

        # Stratégie 3 : embed basique (toujours disponible)
        result = {
            "type": "embed",
            "embed_url": f"https://www.youtube.com/embed/{video_id}?autoplay=1&mute=0&enablejsapi=1",
            "video_id": video_id,
            "fallback": True
        }
        self.cache[cache_key] = {"data": result, "expires": datetime.utcnow() + timedelta(seconds=300)}
        return result

    def _ytdlp_extract(self, url: str) -> dict:
        try:
            ydl_opts = {
                'format': 'best[ext=mp4]/bestvideo+bestaudio/best',
                'quiet': True,
                'no_warnings': True,
                'extract_flat': False,
                'socket_timeout': 30,
                'retries': 3,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if info:
                    # Pour les lives, chercher HLS
                    if info.get('is_live'):
                        for fmt in info.get('formats', []):
                            if fmt.get('protocol') in ('m3u8', 'm3u8_native') and fmt.get('url'):
                                return {
                                    "type": "hls",
                                    "url": fmt['url'],
                                    "video_id": info.get('id'),
                                    "title": info.get('title'),
                                    "is_live": True
                                }
                    # URL directe
                    direct = info.get('url')
                    if not direct and info.get('formats'):
                        direct = info['formats'][-1].get('url')
                    if direct:
                        return {
                            "type": "direct",
                            "url": direct,
                            "video_id": info.get('id'),
                            "title": info.get('title'),
                            "duration": info.get('duration'),
                            "thumbnail": info.get('thumbnail')
                        }
                    # Fallback embed
                    return {
                        "type": "embed",
                        "embed_url": f"https://www.youtube.com/embed/{info.get('id')}?autoplay=1",
                        "video_id": info.get('id'),
                        "title": info.get('title'),
                        "extracted": True
                    }
            return {"error": "Extraction échouée", "type": "error"}
        except Exception as e:
            return {"error": str(e), "type": "error"}

    async def close(self):
        if self.session:
            await self.session.close()

yt_service = YouTubeService()

# ==================== GESTIONNAIRE WEBSOCKET AMÉLIORÉ ====================

class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, List[WebSocket]] = {}
        self.stream_viewers: Dict[str, Dict[str, datetime]] = {}
        self.comment_rate_limit: Dict[str, List[datetime]] = {}
        self.viewer_ips: Dict[str, Set[str]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket, stream_id: str, visitor_id: str, ip: str):
        await websocket.accept()
        async with self._lock:
            self.active_connections.setdefault(stream_id, []).append(websocket)
            self.stream_viewers.setdefault(stream_id, {})[visitor_id] = datetime.utcnow()
            self.viewer_ips.setdefault(stream_id, set()).add(ip)
        await self.update_viewer_count(stream_id)

    async def disconnect(self, websocket: WebSocket, stream_id: str, visitor_id: str):
        async with self._lock:
            if stream_id in self.active_connections:
                if websocket in self.active_connections[stream_id]:
                    self.active_connections[stream_id].remove(websocket)

            if stream_id in self.stream_viewers and visitor_id in self.stream_viewers[stream_id]:
                del self.stream_viewers[stream_id][visitor_id]

    async def broadcast_to_stream(self, stream_id: str, message: dict):
        if stream_id not in self.active_connections:
            return
        disconnected = []
        for conn in list(self.active_connections[stream_id]):
            try:
                await conn.send_json(message)
            except Exception:
                disconnected.append(conn)
        async with self._lock:
            for conn in disconnected:
                if stream_id in self.active_connections and conn in self.active_connections[stream_id]:
                    self.active_connections[stream_id].remove(conn)

    def check_rate_limit(self, visitor_id: str) -> bool:
        now = datetime.utcnow()
        times = [t for t in self.comment_rate_limit.get(visitor_id, []) if (now - t).total_seconds() < 60]
        self.comment_rate_limit[visitor_id] = times
        if len(times) >= settings.MAX_COMMENTS_PER_MINUTE:
            return False
        self.comment_rate_limit[visitor_id].append(now)
        return True

    async def update_viewer_count(self, stream_id: str):
        db = SessionLocal()
        try:
            stream = db.query(UserStream).filter(UserStream.id == stream_id).first()
            if stream:
                count = len(self.stream_viewers.get(stream_id, {}))
                stream.viewer_count = count
                if count > stream.peak_viewers:
                    stream.peak_viewers = count
                db.commit()
        except Exception as e:
            logger.error(f"Erreur update viewer count: {e}")
        finally:
            db.close()

    def get_viewer_count(self, stream_id: str) -> int:
        return len(self.stream_viewers.get(stream_id, {}))

    def get_unique_ips(self, stream_id: str) -> int:
        return len(self.viewer_ips.get(stream_id, set()))

manager = ConnectionManager()

# ==================== SERVICE IPTV AMÉLIORÉ ====================

class IPTVSyncService:
    def __init__(self):
        self.is_syncing = False
        self.last_sync = None
        self.semaphore = asyncio.Semaphore(settings.IPTV_CONCURRENT_DOWNLOADS)
        self.session = None
        self.playlist_cache = {}
        self.failed_playlists = set()

    async def get_session(self):
        if not self.session:
            connector = aiohttp.TCPConnector(limit=settings.IPTV_CONCURRENT_DOWNLOADS, force_close=True, enable_cleanup_closed=True, ssl=False)
            self.session = aiohttp.ClientSession(
                connector=connector,
                timeout=aiohttp.ClientTimeout(total=settings.IPTV_TIMEOUT),
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"}
            )
        return self.session

    async def fetch_playlist(self, name: str, url: str, retry: int = 0) -> list:
        """Télécharge une playlist avec retry et cache"""
        if name in self.playlist_cache:
            cached = self.playlist_cache[name]
            if datetime.utcnow() < cached["expires"]:
                return cached["channels"]

        try:
            async with self.semaphore:
                session = await self.get_session()
                async with session.get(url) as resp:
                    if resp.status == 200:
                        content = await resp.text(errors='replace')
                        channels = self.parse_m3u(content, name)
                        self.playlist_cache[name] = {
                            "channels": channels,
                            "expires": datetime.utcnow() + timedelta(hours=6)
                        }
                        logger.info(f"✅ {name}: {len(channels)} chaînes")
                        return channels
                    elif resp.status in (404, 410):
                        # Ressource inexistante — inutile de retenter
                        logger.warning(f"⚠️ Playlist introuvable (HTTP {resp.status}), ignorée : {url}")
                        return []
                    else:
                        logger.error(f"❌ HTTP {resp.status} pour {url}")
                        if retry < settings.IPTV_MAX_RETRIES:
                            await asyncio.sleep(settings.RETRY_DELAY * (retry + 1))
                            return await self.fetch_playlist(name, url, retry + 1)
                        return []
        except asyncio.TimeoutError:
            logger.error(f"⏰ Timeout pour {url}")
            if retry < settings.IPTV_MAX_RETRIES:
                await asyncio.sleep(settings.RETRY_DELAY * (retry + 1))
                return await self.fetch_playlist(name, url, retry + 1)
            return []
        except Exception as e:
            logger.error(f"❌ Erreur fetch {url}: {e}")
            if retry < settings.IPTV_MAX_RETRIES:
                await asyncio.sleep(settings.RETRY_DELAY * (retry + 1))
                return await self.fetch_playlist(name, url, retry + 1)
            return []

    def parse_m3u(self, content: str, playlist_id: str) -> list:
        """Parse un fichier M3U en liste de chaînes"""
        channels = []
        lines = content.split('\n')
        i = 0
        total_lines = len(lines)

        while i < total_lines:
            line = lines[i].strip()
            if line.startswith('#EXTINF:'):
                # Extraire les attributs
                def get_attr(attr):
                    m = re.search(rf'{attr}="([^"]*)"', line)
                    return m.group(1) if m else None

                tvg_id = get_attr('tvg-id')
                tvg_name = get_attr('tvg-name')
                tvg_logo = get_attr('tvg-logo')
                tvg_chno = get_attr('tvg-chno')
                tvg_shift = get_attr('tvg-shift')
                group = get_attr('group-title') or "general"

                # Nom de la chaîne
                name_parts = line.split(',', 1)
                name = name_parts[1].strip() if len(name_parts) > 1 else "Inconnue"

                # URL de la chaîne (ligne suivante)
                if i + 1 < total_lines:
                    url_line = lines[i + 1].strip()
                    if url_line and not url_line.startswith('#') and url_line.startswith('http'):
                        # Déterminer le type de stream
                        stream_type = self._detect_type(url_line)
                        # Catégorie normalisée
                        category = re.sub(r'[^a-z0-9_]', '_', group.lower())[:50]
                        # Langue
                        language = self._guess_lang(playlist_id, name, group)

                        channels.append({
                            "playlist_id": playlist_id,
                            "name": name[:200],
                            "url": url_line[:1000],
                            "logo": tvg_logo,
                            "category": category,
                            "country": self._get_country(playlist_id),
                            "language": language,
                            "tvg_id": tvg_id,
                            "tvg_name": tvg_name,
                            "tvg_chno": int(tvg_chno) if tvg_chno and tvg_chno.isdigit() else None,
                            "tvg_shift": float(tvg_shift) if tvg_shift else None,
                            "stream_type": stream_type,
                        })
                        i += 1  # sauter l'URL déjà traitée
            i += 1
        return channels

    def _detect_type(self, url: str) -> str:
        u = url.lower()
        if 'youtube.com' in u or 'youtu.be' in u:
            return 'youtube'
        if u.endswith('.mpd') or 'manifest' in u or 'dash' in u:
            return 'dash'
        if u.endswith('.mp4') or u.endswith('.mkv') or u.endswith('.webm') or u.endswith('.mov'):
            return 'mp4'
        if u.endswith('.mp3') or u.endswith('.aac') or u.endswith('.ogg') or u.endswith('.wav') or 'icecast' in u or 'stream.mp3' in u:
            return 'audio'
        if u.startswith('rtmp://') or u.startswith('rtsp://'):
            return 'rtmp'
        return 'hls'

    def _guess_lang(self, playlist_id: str, name: str, group: str) -> str:
        lang_map = {
            "FR": "fr", "BE": "fr", "CH": "fr", "CA": "fr", "LU": "fr", "MC": "fr",
            "MA": "ar", "DZ": "ar", "TN": "ar", "SN": "fr", "CI": "fr", "CM": "fr", "ML": "fr", "CD": "fr",
            "US": "en", "GB": "en", "AU": "en", "NZ": "en", "IE": "en",
            "DE": "de", "AT": "de", "LI": "de",
            "IT": "it", "SM": "it", "VA": "it",
            "ES": "es", "MX": "es", "AR": "es", "CO": "es", "CL": "es", "PE": "es", "VE": "es", "EC": "es", "BO": "es", "PY": "es", "UY": "es", "GT": "es", "CU": "es", "DO": "es", "PR": "es",
            "PT": "pt", "BR": "pt", "AO": "pt", "MZ": "pt",
            "NL": "nl", "BE": "nl",
            "SE": "sv", "NO": "no", "DK": "da", "FI": "fi", "IS": "is",
            "RU": "ru", "UA": "uk", "BY": "be",
            "PL": "pl", "CZ": "cs", "SK": "sk", "HU": "hu", "RO": "ro", "BG": "bg", "RS": "sr", "HR": "hr", "SI": "sl",
            "TR": "tr", "SA": "ar", "AE": "ar", "EG": "ar", "KW": "ar", "QA": "ar", "BH": "ar", "OM": "ar", "YE": "ar",
            "IR": "fa", "IQ": "ar", "SY": "ar", "LB": "ar", "JO": "ar", "IL": "he",
            "IN": "hi", "PK": "ur", "BD": "bn", "LK": "si", "NP": "ne",
            "CN": "zh", "TW": "zh", "HK": "zh", "SG": "zh",
            "JP": "ja", "KR": "ko", "TH": "th", "VN": "vi", "ID": "id", "MY": "ms", "PH": "tl",
        }
        low = (name + " " + group).lower()
        if 'english' in low or 'bbc' in low or 'cnn' in low or 'sky news' in low:
            return 'en'
        if 'arabic' in low or 'عربي' in low or 'العربية' in low:
            return 'ar'
        if 'español' in low or 'espanol' in low:
            return 'es'
        if 'deutsch' in low or 'german' in low:
            return 'de'
        if 'français' in low or 'francais' in low or 'french' in low:
            return 'fr'
        country_code = self._get_country(playlist_id)
        return lang_map.get(country_code, 'en')

    def _get_country(self, playlist_id: str) -> str:
        country_map = {
            "france": "FR", "canada": "CA", "belgique": "BE", "suisse": "CH", "luxembourg": "LU",
            "maroc": "MA", "algerie": "DZ", "tunisie": "TN", "senegal": "SN", "cote_ivoire": "CI",
            "cameroun": "CM", "mali": "ML", "congo": "CD", "etats_unis": "US", "royaume_uni": "GB",
            "allemagne": "DE", "espagne": "ES", "italie": "IT", "portugal": "PT", "pays_bas": "NL",
            "russie": "RU", "pologne": "PL", "ukraine": "UA", "roumanie": "RO", "bulgarie": "BG",
            "serbie": "RS", "croatie": "HR", "slovenie": "SI", "slovaquie": "SK", "tchequie": "CZ",
            "hongrie": "HU", "autriche": "AT", "grece": "GR", "chypre": "CY", "malte": "MT",
            "islande": "IS", "norvege": "NO", "suede": "SE", "finlande": "FI", "danemark": "DK",
            "irlande": "IE", "bresil": "BR", "mexique": "MX", "argentine": "AR", "colombie": "CO",
            "chili": "CL", "perou": "PE", "venezuela": "VE", "equateur": "EC", "bolivie": "BO",
            "paraguay": "PY", "uruguay": "UY", "chine": "CN", "japon": "JP", "coree_sud": "KR",
            "inde": "IN", "pakistan": "PK", "bangladesh": "BD", "indonesie": "ID", "malaisie": "MY",
            "singapour": "SG", "philippines": "PH", "vietnam": "VN", "thailande": "TH", "australie": "AU",
            "nouvelle_zelande": "NZ", "afrique_du_sud": "ZA", "nigeria": "NG", "kenya": "KE", "egypte": "EG",
            "arabie_saoudite": "SA", "emirats_arabes_unis": "AE", "qatar": "QA", "koweit": "KW", "irak": "IQ",
            "iran": "IR", "turquie": "TR", "israel": "IL", "jordanie": "JO", "liban": "LB", "syrie": "SY",
        }
        # Pour les subdivisions, on garde le code pays
        if playlist_id.startswith("ca_"):
            return "CA"
        if playlist_id.startswith("us_"):
            return "US"
        if playlist_id.startswith("br_"):
            return "BR"
        if playlist_id.startswith("co_"):
            return "CO"
        if playlist_id.startswith("gb_"):
            return "GB"
        if playlist_id.startswith("de_"):
            return "DE"
        if playlist_id.startswith("it_"):
            return "IT"
        if playlist_id.startswith("es_"):
            return "ES"
        if playlist_id.startswith("city_"):
            # Deviner le pays à partir du nom de la playlist
            city_country = {
                "city_toronto": "CA", "city_montreal": "CA", "city_vancouver": "CA", "city_calgary": "CA",
                "city_edmonton": "CA", "city_ottawa": "CA", "city_quebec": "CA", "city_winnipeg": "CA",
                "city_new_york": "US", "city_los_angeles": "US", "city_chicago": "US", "city_houston": "US",
                "city_london": "GB", "city_paris": "FR", "city_berlin": "DE", "city_rome": "IT",
                "city_madrid": "ES", "city_moscow": "RU", "city_tokyo": "JP", "city_beijing": "CN",
                "city_sydney": "AU", "city_rio_de_janeiro": "BR", "city_mumbai": "IN",
            }
            return city_country.get(playlist_id, "INT")
        return country_map.get(playlist_id, "INT")

    async def sync_all_playlists(self):
        """Synchronise toutes les playlists IPTV"""
        if self.is_syncing:
            logger.info("⏳ Synchronisation déjà en cours")
            return

        self.is_syncing = True
        logger.info("🔄 Début de la synchronisation IPTV.org (version complète)...")

        db = SessionLocal()
        try:
            total_channels = 0
            total_playlists = len(IPTV_PLAYLISTS)
            successful = 0

            for idx, pl_data in enumerate(IPTV_PLAYLISTS, 1):
                try:
                    logger.info(f"📡 [{idx}/{total_playlists}] Synchronisation: {pl_data['display_name']}")

                    db_pl = db.query(IPTVPlaylist).filter(IPTVPlaylist.name == pl_data["name"]).first()
                    if not db_pl:
                        db_pl = IPTVPlaylist(**pl_data)
                        db.add(db_pl)
                        db.flush()

                    channels = await self.fetch_playlist(pl_data["name"], pl_data["url"])

                    if channels:
                        # Supprimer les anciennes chaînes
                        db.query(IPTVChannel).filter(IPTVChannel.playlist_id == pl_data["name"]).delete()

                        # Ajouter les nouvelles
                        for ch in channels:
                            db.add(IPTVChannel(**ch))

                        db_pl.channel_count = len(channels)
                        db_pl.last_sync = datetime.utcnow()
                        db_pl.sync_status = "success"
                        db_pl.sync_error = None
                        total_channels += len(channels)
                        successful += 1
                    else:
                        db_pl.sync_status = "empty"
                        db_pl.sync_error = "Aucune chaîne trouvée ou playlist introuvable"

                    db.commit()
                    await asyncio.sleep(0.5)

                except Exception as pl_err:
                    logger.error(f"❌ Erreur playlist {pl_data.get('name','?')}: {pl_err}")
                    try:
                        db.rollback()
                    except Exception:
                        pass
                    continue

            self.last_sync = datetime.utcnow()
            logger.info(f"✅ Synchronisation terminée: {total_channels} chaînes au total ({successful}/{total_playlists} playlists réussies)")

        except Exception as e:
            logger.error(f"❌ Erreur lors de la synchronisation: {e}")
            db.rollback()
        finally:
            self.is_syncing = False
            db.close()

    async def start_periodic_sync(self):
        """Lance la synchronisation périodique"""
        while True:
            try:
                await self.sync_all_playlists()
            except Exception as e:
                logger.error(f"Erreur dans le sync périodique: {e}")
            await asyncio.sleep(settings.IPTV_SYNC_INTERVAL)

    async def close(self):
        if self.session:
            await self.session.close()

iptv_sync = IPTVSyncService()

# ==================== PROXY HLS AMÉLIORÉ ====================

class HLSProxy:
    def __init__(self):
        self.client = httpx.AsyncClient(
            timeout=settings.PROXY_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"},
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=100)
        )
        self.cache: Dict[str, dict] = {}
        self._lock = asyncio.Lock()

    async def fetch_stream(self, url: str, headers: dict = None) -> StreamingResponse:
        """Proxy pour les flux HLS avec réécriture des URLs"""
        cache_key = f"proxy_{url}"

        async with self._lock:
            if cache_key in self.cache:
                cached = self.cache[cache_key]
                if datetime.utcnow() < cached["expires"]:
                    return StreamingResponse(
                        iter([cached["content"]]),
                        media_type=cached.get("content_type", "application/vnd.apple.mpegurl"),
                        headers={
                            "Access-Control-Allow-Origin": "*",
                            "Cache-Control": f"public, max-age={settings.STREAM_CACHE_TTL}"
                        }
                    )

        # Déduire le domaine source pour un Referer/Origin cohérent
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            origin = f"{parsed.scheme}://{parsed.netloc}"
        except Exception:
            origin = "https://www.google.com"

        try:
            request_headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                "Accept": "*/*",
                "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
                "Accept-Encoding": "gzip, deflate, br",
                "Origin": origin,
                "Referer": origin + "/",
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-origin",
                "Connection": "keep-alive",
            }
            if headers:
                request_headers.update(headers)

            response = await self.client.get(url, headers=request_headers)
            response.raise_for_status()

            content_type = response.headers.get("content-type", "")
            is_m3u8 = 'm3u8' in content_type or url.endswith('.m3u8') or '#EXTM3U' in response.text[:100]

            if is_m3u8:
                # Réécrire les URLs dans le manifest
                content = self._rewrite_m3u8(response.text, url)
                media_type = "application/vnd.apple.mpegurl"
            else:
                content = response.content
                media_type = content_type or "video/mp4"

            async with self._lock:
                self.cache[cache_key] = {
                    "content": content,
                    "content_type": media_type,
                    "expires": datetime.utcnow() + timedelta(seconds=settings.STREAM_CACHE_TTL)
                }

            # Retourner le stream
            if isinstance(content, str):
                content = content.encode('utf-8')

            return StreamingResponse(
                iter([content]),
                media_type=media_type,
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Cache-Control": f"public, max-age={settings.STREAM_CACHE_TTL}",
                    "Content-Disposition": "inline",
                }
            )

        except httpx.TimeoutException:
            logger.error(f"Timeout proxy pour {url}")
            raise HTTPException(status_code=504, detail="Timeout du proxy")
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            logger.error(f"HTTP {status} pour {url}")
            if status in (401, 403):
                # Accès refusé par la source — ne pas retenter côté client
                raise HTTPException(
                    status_code=403,
                    detail=f"Flux non disponible (accès refusé par la source)"
                )
            raise HTTPException(status_code=status, detail=f"Erreur HTTP: {status}")
        except Exception as e:
            logger.error(f"Erreur proxy: {e}")
            raise HTTPException(status_code=502, detail=f"Erreur proxy: {str(e)}")

    def _rewrite_m3u8(self, content: str, base_url: str) -> str:
        """Réécrit les URLs relatives dans un manifest M3U8"""
        base = '/'.join(base_url.split('/')[:-1]) + '/'
        lines = content.split('\n')
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped and not stripped.startswith('#'):
                if not stripped.startswith('http'):
                    stripped = urljoin(base, stripped)
                # Encoder l'URL pour le proxy
                lines[i] = f"/proxy/stream?url={quote(stripped, safe='')}"
        return '\n'.join(lines)

    async def close(self):
        await self.client.aclose()

proxy = HLSProxy()

# ==================== UTILITAIRES ====================

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)

def get_password_hash(pw: str) -> str:
    return pwd_context.hash(pw)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=15))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)

def get_visitor_id(request: Request) -> str:
    return request.cookies.get('visitor_id') or f"vis_{secrets.token_urlsafe(16)}"

def check_ip_blocked(ip: str, db: Session) -> bool:
    return db.query(BlockedIP).filter(
        and_(
            BlockedIP.ip_address == ip,
            or_(
                BlockedIP.expires_at.is_(None),
                BlockedIP.expires_at > datetime.utcnow()
            )
        )
    ).first() is not None

def filter_inappropriate(text: str, lang: str = 'fr') -> str:
    """Filtre les mots inappropriés dans un texte"""
    words = text.split()
    result = []
    for word in words:
        word_lower = word.lower()
        is_bad = False
        for lang_words in INAPPROPRIATE_WORDS.values():
            for bad_word in lang_words:
                if bad_word in word_lower or word_lower in bad_word:
                    is_bad = True
                    break
            if is_bad:
                break
        result.append('*' * len(word) if is_bad else html.escape(word))
    return ' '.join(result)

def get_language(request: Request) -> str:
    """Détecte la langue préférée de l'utilisateur"""
    accept = request.headers.get("accept-language", "fr-FR,fr;q=0.9")
    for part in accept.split(','):
        lang = part.split(';')[0].split('-')[0].strip()
        if lang in ['fr', 'en', 'ar', 'es', 'de', 'it', 'pt', 'nl', 'ru', 'zh', 'ja', 'ko']:
            return lang
    return 'fr'

def init_external_streams(db: Session):
    """Initialise les flux externes dans la base de données"""
    for stream_data in EXTERNAL_STREAMS:
        existing = db.query(ExternalStream).filter(
            ExternalStream.title == stream_data["title"],
            ExternalStream.url == stream_data["url"]
        ).first()
        if not existing:
            stream = ExternalStream(**stream_data)
            db.add(stream)
        elif not hasattr(existing, 'stream_type') or existing.stream_type != stream_data.get("stream_type", "hls"):
            existing.stream_type = stream_data.get("stream_type", "hls")
            existing.proxy_needed = stream_data.get("proxy_needed", False)
    db.commit()
    logger.info(f"✅ {len(EXTERNAL_STREAMS)} flux externes initialisés")

def init_iptv_playlists(db: Session):
    """Initialise les playlists IPTV dans la base de données"""
    for playlist_data in IPTV_PLAYLISTS:
        existing = db.query(IPTVPlaylist).filter(IPTVPlaylist.name == playlist_data["name"]).first()
        if not existing:
            playlist = IPTVPlaylist(**playlist_data)
            db.add(playlist)
    db.commit()
    logger.info(f"✅ {len(IPTV_PLAYLISTS)} playlists IPTV initialisées")

def require_admin(request: Request) -> dict:
    """Vérifie que l'utilisateur est administrateur"""
    token = request.cookies.get("admin_token")
    if not token:
        raise HTTPException(status_code=401, detail="Non autorisé")
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        if not payload.get("admin"):
            raise HTTPException(status_code=401, detail="Non autorisé")
        return payload
    except JWTError:
        raise HTTPException(status_code=401, detail="Non autorisé")

def require_owner(request: Request, db: Session) -> dict:
    """Vérifie que l'utilisateur est le propriétaire"""
    payload = require_admin(request)
    user = db.query(User).filter(User.id == payload.get("sub")).first()
    if not user or not user.is_owner:
        raise HTTPException(status_code=403, detail="Accès réservé au propriétaire")
    return payload

_last_epg_full_sync: Optional[datetime] = None   # timestamp du dernier téléchargement complet EPG
EPG_SYNC_INTERVAL_HOURS = 24                       # intervalle minimum entre deux téléchargements


async def _periodic_epg_cleanup():
    """
    Toutes les 10 minutes : supprime les événements expirés.
    Toutes les 24h : re-télécharge le fichier EPG complet.
    """
    global _last_epg_full_sync
    while True:
        try:
            await asyncio.sleep(10 * 60)   # vérifier toutes les 10 min
            db = SessionLocal()
            try:
                # ── 1. Nettoyage des événements expirés ─────────────────
                cutoff = datetime.utcnow() - timedelta(minutes=10)
                deleted = db.query(TVEvent).filter(TVEvent.end_time < cutoff).delete(synchronize_session=False)
                db.commit()
                if deleted:
                    logger.info(f"🗑️ EPG cleanup : {deleted} événements expirés supprimés")

                # ── 2. Re-téléchargement uniquement toutes les 24h ───────
                now = datetime.utcnow()
                should_sync = (
                    _last_epg_full_sync is None or
                    (now - _last_epg_full_sync).total_seconds() >= EPG_SYNC_INTERVAL_HOURS * 3600
                )
                future_count = db.query(TVEvent).filter(TVEvent.end_time > now).count()

                if should_sync or future_count < 20:
                    reason = "24h écoulées" if should_sync else f"seulement {future_count} événements futurs"
                    logger.info(f"🔄 EPG re-sync ({reason}) — prochain dans {EPG_SYNC_INTERVAL_HOURS}h")
                    _last_epg_full_sync = now
                    inserted = await fetch_smart_epg(db, max_per_category=50)
                    logger.info(f"✅ EPG re-sync : {inserted} événements insérés")
                else:
                    next_sync = _last_epg_full_sync + timedelta(hours=EPG_SYNC_INTERVAL_HOURS)
                    remaining = int((next_sync - now).total_seconds() / 3600)
                    logger.debug(f"📅 EPG OK : {future_count} événements futurs | prochain sync dans ~{remaining}h")
            finally:
                db.close()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Erreur EPG periodic sync : {e}")

async def seed_epg_events_async_bg():
    """
    Version entièrement non-bloquante du seeder EPG.
    Tourne en tâche de fond — la sync IPTV n'est pas bloquée.
    """
    global _last_epg_full_sync
    # Petit délai pour laisser le serveur démarrer proprement
    await asyncio.sleep(5)
    db = SessionLocal()
    try:
        cutoff = datetime.utcnow() - timedelta(minutes=10)
        deleted = db.query(TVEvent).filter(TVEvent.end_time < cutoff).delete(synchronize_session=False)
        db.commit()
        if deleted:
            logger.info(f"🗑️ EPG bg: {deleted} événements expirés supprimés")

        existing = db.query(TVEvent).filter(TVEvent.end_time >= datetime.utcnow()).count()
        if existing > 50:
            logger.info(f"✅ EPG bg: {existing} événements déjà en base, skip")
            # On considère que la sync a eu lieu récemment
            _last_epg_full_sync = datetime.utcnow()
            return

        logger.info("📅 EPG bg: démarrage SmartEPG en arrière-plan...")
        _last_epg_full_sync = datetime.utcnow()
        inserted = await fetch_smart_epg(db, max_per_category=50)
        if inserted == 0:
            logger.warning("⚠️ EPG bg: aucun événement inséré.")
            _last_epg_full_sync = None   # autoriser une nouvelle tentative
        else:
            logger.info(f"✅ EPG bg: {inserted} événements insérés")
    except Exception as e:
        logger.error(f"EPG bg error: {e}")
        _last_epg_full_sync = None
        try:
            db.rollback()
        except Exception:
            pass
    finally:
        db.close()


async def _daily_stats_recorder():
    """
    Enregistre chaque jour les statistiques de visite dans DailyVisitStats.
    Tourne toutes les heures et consolide les données des Visitors créés aujourd'hui.
    """
    while True:
        try:
            await asyncio.sleep(3600)  # toutes les heures
            db = SessionLocal()
            try:
                now      = datetime.utcnow()
                today    = datetime(now.year, now.month, now.day)  # minuit UTC

                # Visiteurs uniques créés aujourd'hui
                unique_today = db.query(Visitor).filter(
                    Visitor.created_at >= today,
                    Visitor.created_at <  today + timedelta(days=1)
                ).count()

                # Pic d'utilisateurs actifs (snapshot actuel)
                peak = active_tracker.count()

                # Upsert dans DailyVisitStats
                row = db.query(DailyVisitStats).filter(DailyVisitStats.date == today).first()
                if row:
                    row.unique_users = unique_today
                    row.peak_active  = max(row.peak_active, peak)
                    row.page_views   += 1  # incrémente à chaque passage horaire
                else:
                    db.add(DailyVisitStats(
                        date         = today,
                        unique_users = unique_today,
                        peak_active  = peak,
                        page_views   = 1,
                    ))
                db.commit()
                logger.debug(f"📊 Stats journalières: {unique_today} visiteurs uniques aujourd'hui")
            finally:
                db.close()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"_daily_stats_recorder error: {e}")


# ==================== LIFESPAN ====================

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=" * 70)
    logger.info(f"🚀 {settings.APP_NAME} v{settings.APP_VERSION}")
    logger.info(f"🐘 PostgreSQL : {settings.DATABASE_URL.split('@')[-1]}")
    logger.info("=" * 70)

    # ── 1. Vérifier la connexion PostgreSQL ────────────────────────────
    max_retries = 10
    for attempt in range(1, max_retries + 1):
        try:
            with engine.connect() as conn:
                from sqlalchemy import text
                conn.execute(text("SELECT 1"))
            logger.info("✅ PostgreSQL connecté")
            break
        except Exception as e:
            if attempt == max_retries:
                logger.critical(
                    f"❌ Impossible de se connecter à PostgreSQL après {max_retries} tentatives.\n"
                    f"   Vérifiez DATABASE_URL dans votre .env :\n"
                    f"   {settings.DATABASE_URL.split('@')[-1]}\n"
                    f"   Erreur : {e}"
                )
                raise SystemExit(1)
            wait = attempt * 2
            logger.warning(f"⏳ PostgreSQL non disponible (tentative {attempt}/{max_retries}), retry dans {wait}s… [{e}]")
            await asyncio.sleep(wait)

    # ── 2. Créer / mettre à jour les tables ────────────────────────────
    try:
        Base.metadata.create_all(bind=engine)
        logger.info("✅ Tables PostgreSQL synchronisées")
    except Exception as e:
        logger.error(f"❌ Erreur create_all : {e}")
        raise

    # Création des dossiers
    for directory in ["static", "templates", "static/uploads", "static/thumbnails", "static/recordings"]:
        os.makedirs(directory, exist_ok=True)

    if os.path.exists(settings.LOGO_PATH):
        logger.info(f"✅ Logo trouvé: {settings.LOGO_PATH}")
    else:
        logger.warning(f"⚠️ Logo non trouvé: {settings.LOGO_PATH}")

    # Écriture des templates
    write_all_templates()

    # ── 3. Initialisation des données ──────────────────────────────────
    db = SessionLocal()
    try:
        owner = db.query(User).filter(User.email == settings.OWNER_ID).first()
        if not owner:
            owner = User(
                username=settings.ADMIN_USERNAME,
                email=settings.OWNER_ID,
                hashed_password=get_password_hash(settings.ADMIN_PASSWORD),
                is_admin=True,
                is_owner=True
            )
            db.add(owner)
            logger.info("✅ Compte propriétaire créé")
        else:
            owner.is_owner = True
            owner.is_admin = True

        init_external_streams(db)
        init_iptv_playlists(db)

        # Nettoyage visiteurs expirés
        expired = db.query(Visitor).filter(Visitor.expires_at < datetime.utcnow()).delete(synchronize_session=False)
        db.commit()
        logger.info(f"✅ {expired} visiteurs expirés nettoyés")

        # Nettoyage événements EPG terminés depuis > 10 minutes
        cutoff = datetime.utcnow() - timedelta(minutes=10)
        old_events = db.query(TVEvent).filter(TVEvent.end_time < cutoff).delete(synchronize_session=False)
        db.commit()
        if old_events:
            logger.info(f"🗑️ {old_events} anciens événements EPG supprimés")

        # EPG lancé en arrière-plan (non bloquant)
        # La sync IPTV démarre immédiatement sans attendre le parsing XML
        logger.info("📅 SmartEPG : démarrage en arrière-plan (non bloquant)...")

    except Exception as e:
        logger.error(f"❌ Erreur initialisation DB : {e}")
        db.rollback()
    finally:
        db.close()

    # ── 4. Tâches de fond — toutes démarrent en parallèle ──────────────────
    logger.info("🔄 Démarrage synchronisation IPTV...")
    sync_task      = asyncio.create_task(iptv_sync.start_periodic_sync())
    reminder_task  = asyncio.create_task(epg_service.check_reminders())
    cleanup_task   = asyncio.create_task(_periodic_epg_cleanup())
    tracker_task   = asyncio.create_task(active_tracker.start_broadcast_loop())
    stats_task     = asyncio.create_task(_daily_stats_recorder())
    # EPG en arrière-plan : ne bloque pas le démarrage ni la sync IPTV
    epg_task       = asyncio.create_task(seed_epg_events_async_bg())
    logger.info("🔔 Services démarrés : IPTV sync + EPG (bg) + tracker + stats journalières")

    logger.info(f"🌐 http://localhost:8001")
    logger.info(f"👤 Admin : {settings.OWNER_ID} / {settings.ADMIN_PASSWORD}")
    logger.info(f"📺 {len(EXTERNAL_STREAMS)} flux externes | 🌍 {len(IPTV_PLAYLISTS)} playlists IPTV")
    logger.info("=" * 70 + "\n")

    yield

    sync_task.cancel()
    reminder_task.cancel()
    cleanup_task.cancel()
    tracker_task.cancel()
    stats_task.cancel()
    epg_task.cancel()
    await proxy.close()
    await yt_service.close()
    await iptv_sync.close()

# ==================== APPLICATION ====================

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description=settings.APP_DESCRIPTION,
    lifespan=lifespan
)

# Middlewares
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["*"])

# Fichiers statiques
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# Middleware de sécurité
@app.middleware("http")
async def security_middleware(request: Request, call_next):
    # Limite de taille pour les uploads
    if request.method in ["POST", "PUT", "DELETE"]:
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > settings.MAX_UPLOAD_SIZE:
            return JSONResponse(
                status_code=413,
                content={"error": "Fichier trop volumineux", "max_size": settings.MAX_UPLOAD_SIZE}
            )

    response = await call_next(request)

    # Headers de sécurité
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

    return response

# ==================== RATE LIMITING & SÉCURITÉ AVANCÉE ====================

_rate_limit_store: Dict[str, List[float]] = {}
_suspicious_ips: Set[str] = set()
_RATE_LIMIT_WINDOW = 60   # secondes
_RATE_LIMIT_MAX = 120     # requêtes par fenêtre (2/s max)
_RATE_LIMIT_API = 30      # requêtes API par fenêtre
_RATE_LIMIT_STREAM = 10   # requêtes proxy par fenêtre

# ==================== TRACKER UTILISATEURS ACTIFS EN TEMPS RÉEL ====================

class ActiveUsersTracker:
    """
    Suit en temps réel les utilisateurs actifs sur l'appli.
    Un utilisateur est considéré "actif" s'il a fait une requête
    dans les 5 dernières minutes.
    """
    def __init__(self):
        self._sessions: Dict[str, dict] = {}   # visitor_id → {ip, page, last_seen, ua}
        self._admin_ws: List[WebSocket] = []    # WebSockets admin connectés
        self._ACTIVE_WINDOW = 5 * 60            # 5 minutes

    def record(self, visitor_id: str, ip: str, page: str, ua: str = ""):
        """Enregistre ou met à jour une session active."""
        self._sessions[visitor_id] = {
            "ip":        ip,
            "page":      page,
            "ua":        ua[:120],
            "last_seen": datetime.utcnow(),
        }

    def cleanup(self):
        """Supprime les sessions inactives depuis plus de 5 min."""
        cutoff = datetime.utcnow() - timedelta(seconds=self._ACTIVE_WINDOW)
        stale = [k for k, v in self._sessions.items() if v["last_seen"] < cutoff]
        for k in stale:
            del self._sessions[k]

    def count(self) -> int:
        self.cleanup()
        return len(self._sessions)

    def snapshot(self) -> dict:
        """Retourne un snapshot complet pour le dashboard admin."""
        self.cleanup()
        now = datetime.utcnow()
        sessions = list(self._sessions.values())
        # Pages les plus vues
        page_counts: Dict[str, int] = {}
        for s in sessions:
            p = s["page"].split("?")[0]  # ignorer les query params
            page_counts[p] = page_counts.get(p, 0) + 1
        top_pages = sorted(page_counts.items(), key=lambda x: -x[1])[:5]
        return {
            "active_users":  len(sessions),
            "top_pages":     [{"page": p, "count": c} for p, c in top_pages],
            "ts":            now.isoformat(),
        }

    # ── WebSocket admin ──────────────────────────────────────────────────────

    async def connect_admin(self, ws: WebSocket):
        await ws.accept()
        self._admin_ws.append(ws)
        logger.info(f"Admin WS connecté ({len(self._admin_ws)} actifs)")

    def disconnect_admin(self, ws: WebSocket):
        if ws in self._admin_ws:
            self._admin_ws.remove(ws)

    async def broadcast_to_admins(self, data: dict):
        """Envoie les stats à tous les admins connectés via WebSocket."""
        dead = []
        for ws in list(self._admin_ws):
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect_admin(ws)

    async def start_broadcast_loop(self):
        """Boucle infinie : pousse les stats toutes les 5 secondes aux admins."""
        while True:
            try:
                await asyncio.sleep(5)
                if self._admin_ws:   # ne calcule que si quelqu'un écoute
                    snap = self.snapshot()
                    await self.broadcast_to_admins({"type": "stats", **snap})
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"ActiveUsersTracker broadcast error: {e}")


active_tracker = ActiveUsersTracker()

def _get_client_ip(request: Request) -> str:
    xff = request.headers.get("X-Forwarded-For")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "0.0.0.0"

def _check_rate_limit(ip: str, key_suffix: str, max_req: int) -> bool:
    """Retourne True si autorisé, False si bloqué"""
    key = f"{ip}:{key_suffix}"
    now = datetime.utcnow().timestamp()
    times = _rate_limit_store.get(key, [])
    # Purger les anciennes entrées
    times = [t for t in times if now - t < _RATE_LIMIT_WINDOW]
    if len(times) >= max_req:
        _suspicious_ips.add(ip)
        return False
    times.append(now)
    _rate_limit_store[key] = times
    return True

@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    ip = _get_client_ip(request)
    path = request.url.path

    # Ne pas rate-limiter les statiques
    if path.startswith("/static/"):
        return await call_next(request)

    # ── Tracker utilisateurs actifs (pages HTML uniquement) ──────────────
    if not path.startswith(("/api/", "/proxy/", "/static/", "/ws")):
        visitor_id = request.cookies.get("visitor_id") or ip
        ua = request.headers.get("user-agent", "")
        active_tracker.record(visitor_id, ip, path, ua)

    # Proxy — limite très stricte
    if path.startswith("/proxy/"):
        if not _check_rate_limit(ip, "proxy", _RATE_LIMIT_STREAM):
            return JSONResponse(status_code=429, content={"error": "Trop de requêtes proxy"})

    # API — limite stricte
    elif path.startswith("/api/"):
        if not _check_rate_limit(ip, "api", _RATE_LIMIT_API):
            return JSONResponse(status_code=429, content={"error": "Trop de requêtes API", "retry_after": _RATE_LIMIT_WINDOW})

    # Pages — limite générale
    else:
        if not _check_rate_limit(ip, "page", _RATE_LIMIT_MAX):
            return JSONResponse(status_code=429, content={"error": "Trop de requêtes"})

    response = await call_next(request)

    # Headers de sécurité complets
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    # Note: HSTS seulement en production HTTPS réelle
    # response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

    # CSP assouplie pour permettre les flux HLS/DASH de toutes sources
    if not path.startswith("/proxy/"):
        response.headers["Content-Security-Policy"] = (
            "default-src 'self' 'unsafe-inline' 'unsafe-eval' data: blob: *; "
            "script-src 'self' 'unsafe-inline' 'unsafe-eval' https: blob:; "
            "style-src 'self' 'unsafe-inline' https:; "
            "font-src 'self' https: data:; "
            "img-src * data: blob:; "
            "media-src * data: blob:; "
            "connect-src * wss: ws: blob:; "
            "worker-src blob: 'self'; "
            "frame-src *;"
        )
    return response

# ==================== ROUTES PRINCIPALES ====================

@app.get("/", response_class=HTMLResponse)
async def home(
    request: Request,
    category: str = None,
    playlist: str = None,
    ptype: str = None,
    db: Session = Depends(get_db)
):
    """Page d'accueil avec tous les contenus"""
    lang = get_language(request)
    visitor_id = get_visitor_id(request)

    # Streams communautaires en direct
    live_query = db.query(UserStream).filter(
        and_(UserStream.is_live == True, UserStream.is_blocked == False)
    )
    if category and not category.startswith("iptv"):
        live_query = live_query.filter(UserStream.category == category)
    live_streams = live_query.order_by(desc(UserStream.viewer_count)).limit(20).all()

    # Flux externes
    ext_query = db.query(ExternalStream).filter(ExternalStream.is_active == True)
    if category and not category.startswith("iptv"):
        ext_query = ext_query.filter(ExternalStream.category == category)
    external_streams = ext_query.order_by(desc(ExternalStream.viewers)).limit(60).all()

    # Playlists IPTV
    playlists_all = db.query(IPTVPlaylist).filter(IPTVPlaylist.is_active == True)
    if ptype:
        playlists_all = playlists_all.filter(IPTVPlaylist.playlist_type == ptype)
    playlists_all = playlists_all.all()

    # Grouper par type — le dashboard principal n'affiche que les pays
    pl_countries = [p for p in playlists_all if p.playlist_type == "country"]
    pl_subdivisions = []   # masqués du dashboard principal
    pl_cities = []         # masqués du dashboard principal
    pl_categories = [p for p in playlists_all if p.playlist_type == "category"]

    # Chaînes IPTV d'une playlist spécifique
    iptv_channels = []
    selected_playlist = None
    if playlist:
        selected_playlist = db.query(IPTVPlaylist).filter(IPTVPlaylist.name == playlist).first()
        if selected_playlist:
            channel_query = db.query(IPTVChannel).filter(
                IPTVChannel.playlist_id == playlist,
                IPTVChannel.is_active == True
            )
            if category and (category.startswith("iptv_") or category in CATEGORY_IPTV_KEYWORDS):
                keywords = CATEGORY_IPTV_KEYWORDS.get(category, [category.replace("iptv_", "")])
                if keywords:
                    filters = [IPTVChannel.category.ilike(f"%{kw}%") for kw in keywords]
                    channel_query = channel_query.filter(or_(*filters))
            iptv_channels = channel_query.order_by(IPTVChannel.name).limit(200).all()
    elif category and not category.startswith("iptv_"):
        # Filtre catégorie thématique sans pays sélectionné → afficher les chaînes IPTV de cette catégorie
        keywords = CATEGORY_IPTV_KEYWORDS.get(category, [category])
        if keywords:
            filters = [IPTVChannel.category.ilike(f"%{kw}%") for kw in keywords]
            iptv_channels = db.query(IPTVChannel).filter(
                or_(*filters), IPTVChannel.is_active == True
            ).order_by(desc(IPTVChannel.viewers)).limit(200).all()
    elif category and category.startswith("iptv_"):
        # Catégorie IPTV spécifique sans pays
        keywords = CATEGORY_IPTV_KEYWORDS.get(category, [category.replace("iptv_", "")])
        if keywords:
            filters = [IPTVChannel.category.ilike(f"%{kw}%") for kw in keywords]
            iptv_channels = db.query(IPTVChannel).filter(
                or_(*filters), IPTVChannel.is_active == True
            ).order_by(desc(IPTVChannel.viewers)).limit(200).all()

    # Statistiques par catégorie (ExternalStream + UserStream + IPTVChannel)
    categories_stats = []
    for cat in CATEGORIES:
        cat_id = cat["id"]
        keywords = CATEGORY_IPTV_KEYWORDS.get(cat_id, [cat_id.replace("iptv_", "")])

        if cat_id == "iptv":
            iptv_count = db.query(IPTVChannel).filter(IPTVChannel.is_active == True).count()
            categories_stats.append({**cat, "count": iptv_count})
            continue

        if keywords:
            filters = [IPTVChannel.category.ilike(f"%{kw}%") for kw in keywords]
            iptv_count = db.query(IPTVChannel).filter(
                or_(*filters), IPTVChannel.is_active == True
            ).count()
        else:
            iptv_count = 0

        ext_count = db.query(ExternalStream).filter(
            ExternalStream.category == cat_id, ExternalStream.is_active == True
        ).count()
        user_count = db.query(UserStream).filter(
            UserStream.category == cat_id,
            UserStream.is_live == True,
            UserStream.is_blocked == False
        ).count()
        categories_stats.append({**cat, "count": ext_count + user_count + iptv_count})

    response = templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "live_streams": live_streams,
            "external_streams": external_streams,
            "pl_countries": pl_countries,
            "pl_subdivisions": pl_subdivisions,
            "pl_cities": pl_cities,
            "pl_categories": pl_categories,
            "iptv_channels": iptv_channels,
            "selected_playlist": selected_playlist,
            "categories": categories_stats,
            "language": lang,
            "visitor_id": visitor_id,
            "app_name": settings.APP_NAME,
            "current_category": category,
            "current_playlist": playlist,
            "logo_path": settings.LOGO_PATH if os.path.exists(settings.LOGO_PATH) else None
        }
    )

    # Définir le cookie visiteur si nécessaire
    if not request.cookies.get('visitor_id'):
        response.set_cookie(
            key="visitor_id",
            value=visitor_id,
            max_age=settings.SESSION_MAX_AGE,
            httponly=True,
            samesite="lax"
        )

    return response

@app.get("/watch/external/{stream_id}", response_class=HTMLResponse)
async def watch_external(request: Request, stream_id: str, db: Session = Depends(get_db)):
    """Page de visionnage d'un flux externe"""
    stream = db.query(ExternalStream).filter(ExternalStream.id == stream_id).first()
    if not stream:
        return RedirectResponse(url="/", status_code=303)

    # Incrémenter le compteur de viewers
    stream.viewers += 1
    db.commit()

    # Résolution YouTube si nécessaire
    youtube_data = None
    if stream.stream_type == "youtube":
        youtube_data = await yt_service.get_stream_url(stream.url)

    # Recommandations
    recommendations = db.query(ExternalStream).filter(
        ExternalStream.category == stream.category,
        ExternalStream.id != stream.id,
        ExternalStream.is_active == True
    ).order_by(desc(ExternalStream.viewers)).limit(8).all()

    return templates.TemplateResponse(
        "watch_external.html",
        {
            "request": request,
            "stream": stream,
            "recommendations": recommendations,
            "language": get_language(request),
            "visitor_id": get_visitor_id(request),
            "app_name": settings.APP_NAME,
            "youtube_data": youtube_data,
            "categories": CATEGORIES,
            "logo_path": settings.LOGO_PATH if os.path.exists(settings.LOGO_PATH) else None
        }
    )

@app.get("/watch/iptv/{channel_id}", response_class=HTMLResponse)
async def watch_iptv(request: Request, channel_id: str, db: Session = Depends(get_db)):
    """Page de visionnage d'une chaîne IPTV"""
    channel = db.query(IPTVChannel).filter(IPTVChannel.id == channel_id).first()
    if not channel:
        return RedirectResponse(url="/", status_code=303)

    # Vérification de l'URL
    if not channel.url or not channel.url.strip():
        return templates.TemplateResponse(
            "error.html",
            {
                "request": request,
                "error": "L'URL de ce flux est manquante ou invalide.",
                "app_name": settings.APP_NAME,
                "categories": CATEGORIES,
                "logo_path": settings.LOGO_PATH if os.path.exists(settings.LOGO_PATH) else None
            }
        )

    # Normaliser le stream_type si None
    if not channel.stream_type:
        channel.stream_type = "hls"

    # Incrémenter le compteur de viewers
    channel.viewers += 1
    channel.last_seen = datetime.utcnow()
    db.commit()

    # Résolution YouTube si nécessaire
    youtube_data = None
    if channel.stream_type == "youtube":
        youtube_data = await yt_service.get_stream_url(channel.url)

    # Recommandations
    recommendations = db.query(IPTVChannel).filter(
        IPTVChannel.playlist_id == channel.playlist_id,
        IPTVChannel.id != channel.id,
        IPTVChannel.is_active == True
    ).order_by(desc(IPTVChannel.viewers)).limit(12).all()

    return templates.TemplateResponse(
        "watch_iptv.html",
        {
            "request": request,
            "channel": channel,
            "recommendations": recommendations,
            "language": get_language(request),
            "visitor_id": get_visitor_id(request),
            "app_name": settings.APP_NAME,
            "youtube_data": youtube_data,
            "categories": CATEGORIES,
            "logo_path": settings.LOGO_PATH if os.path.exists(settings.LOGO_PATH) else None
        }
    )

@app.get("/watch/user/{stream_id}", response_class=HTMLResponse)
async def watch_user(request: Request, stream_id: str, db: Session = Depends(get_db)):
    """Page de visionnage d'un stream utilisateur"""
    stream = db.query(UserStream).filter(UserStream.id == stream_id).first()
    if not stream:
        return RedirectResponse(url="/", status_code=303)

    # Vérifier si le stream est bloqué
    if stream.is_blocked:
        return templates.TemplateResponse(
            "blocked.html",
            {
                "request": request,
                "reason": "Ce stream a été bloqué par la modération",
                "app_name": settings.APP_NAME,
                "categories": CATEGORIES
            }
        )

    # Vérifier l'IP
    client_ip = request.client.host if request.client else "0.0.0.0"
    if check_ip_blocked(client_ip, db):
        return templates.TemplateResponse(
            "blocked.html",
            {
                "request": request,
                "reason": "Votre adresse IP a été bloquée",
                "app_name": settings.APP_NAME,
                "categories": CATEGORIES
            }
        )

    # Gérer le visiteur
    visitor_id = get_visitor_id(request)
    visitor = db.query(Visitor).filter(Visitor.visitor_id == visitor_id).first()
    if not visitor:
        visitor = Visitor(
            visitor_id=visitor_id,
            ip_address=client_ip,
            user_agent=request.headers.get("user-agent", "")
        )
        db.add(visitor)
        db.commit()
    else:
        visitor.last_seen = datetime.utcnow()
        db.commit()

    # Recommandations
    recommendations = db.query(UserStream).filter(
        and_(
            UserStream.category == stream.category,
            UserStream.id != stream.id,
            UserStream.is_live == True,
            UserStream.is_blocked == False
        )
    ).order_by(desc(UserStream.viewer_count)).limit(6).all()

    return templates.TemplateResponse(
        "watch_user.html",
        {
            "request": request,
            "stream": stream,
            "recommended": recommendations,
            "language": get_language(request),
            "visitor_id": visitor_id,
            "app_name": settings.APP_NAME,
            "max_comment_length": settings.MAX_COMMENT_LENGTH,
            "comments_per_minute": settings.MAX_COMMENTS_PER_MINUTE,
            "categories": CATEGORIES,
            "logo_path": settings.LOGO_PATH if os.path.exists(settings.LOGO_PATH) else None
        }
    )

@app.get("/playlist/{playlist_name}", response_class=HTMLResponse)
async def view_playlist(request: Request, playlist_name: str, db: Session = Depends(get_db)):
    """Page d'affichage d'une playlist IPTV"""
    playlist = db.query(IPTVPlaylist).filter(IPTVPlaylist.name == playlist_name).first()
    if not playlist:
        return RedirectResponse(url="/", status_code=303)

    channels = db.query(IPTVChannel).filter(
        IPTVChannel.playlist_id == playlist_name,
        IPTVChannel.is_active == True
    ).order_by(IPTVChannel.name).all()

    return templates.TemplateResponse(
        "playlist.html",
        {
            "request": request,
            "playlist": playlist,
            "channels": channels,
            "language": get_language(request),
            "visitor_id": get_visitor_id(request),
            "app_name": settings.APP_NAME,
            "categories": CATEGORIES,
            "logo_path": settings.LOGO_PATH if os.path.exists(settings.LOGO_PATH) else None
        }
    )

@app.get("/go-live", response_class=HTMLResponse)
async def go_live_page(request: Request, db: Session = Depends(get_db)):
    """Page de création de live"""
    client_ip = request.client.host if request.client else "0.0.0.0"

    # Vérifier l'IP
    if check_ip_blocked(client_ip, db):
        return templates.TemplateResponse(
            "blocked.html",
            {
                "request": request,
                "reason": "Votre adresse IP a été bloquée",
                "app_name": settings.APP_NAME,
                "categories": CATEGORIES
            }
        )

    return templates.TemplateResponse(
        "go_live.html",
        {
            "request": request,
            "language": get_language(request),
            "visitor_id": get_visitor_id(request),
            "categories": [c for c in CATEGORIES if not c["id"].startswith("iptv")],
            "app_name": settings.APP_NAME,
            "logo_path": settings.LOGO_PATH if os.path.exists(settings.LOGO_PATH) else None
        }
    )

@app.get("/search", response_class=HTMLResponse)
async def search_page(request: Request, q: str = "", db: Session = Depends(get_db)):
    """Page de recherche"""
    external_results = []
    iptv_results = []
    user_results = []

    if q and len(q.strip()) >= 2:
        query = q.strip()

        # Recherche dans les flux externes
        external_results = db.query(ExternalStream).filter(
            and_(
                ExternalStream.is_active == True,
                or_(
                    ExternalStream.title.ilike(f"%{query}%"),
                    ExternalStream.subcategory.ilike(f"%{query}%"),
                    ExternalStream.category.ilike(f"%{query}%"),
                    ExternalStream.country.ilike(f"%{query}%")
                )
            )
        ).all()

        # Recherche dans les chaînes IPTV
        iptv_results = db.query(IPTVChannel).filter(
            and_(
                IPTVChannel.is_active == True,
                or_(
                    IPTVChannel.name.ilike(f"%{query}%"),
                    IPTVChannel.category.ilike(f"%{query}%"),
                    IPTVChannel.country.ilike(f"%{query}%")
                )
            )
        ).limit(200).all()

        # Recherche dans les streams utilisateur
        user_results = db.query(UserStream).filter(
            and_(
                UserStream.is_live == True,
                UserStream.is_blocked == False,
                or_(
                    UserStream.title.ilike(f"%{query}%"),
                    UserStream.description.ilike(f"%{query}%"),
                    UserStream.tags.ilike(f"%{query}%")
                )
            )
        ).all()

    return templates.TemplateResponse(
        "search.html",
        {
            "request": request,
            "query": q,
            "external_results": external_results,
            "iptv_results": iptv_results,
            "user_results": user_results,
            "language": get_language(request),
            "visitor_id": get_visitor_id(request),
            "app_name": settings.APP_NAME,
            "categories": CATEGORIES,
            "logo_path": settings.LOGO_PATH if os.path.exists(settings.LOGO_PATH) else None
        }
    )

@app.get("/static/logo")
async def get_logo():
    """Retourne le logo de l'application"""
    if os.path.exists(settings.LOGO_PATH):
        return FileResponse(settings.LOGO_PATH, media_type="image/png")
    return RedirectResponse(url="https://via.placeholder.com/200x200?text=Livewatch")

# ==================== API YOUTUBE ====================

@app.get("/api/youtube/resolve")
async def resolve_youtube(url: str):
    """Résout une URL YouTube en flux direct"""
    data = await yt_service.get_stream_url(url)
    return JSONResponse(data)

# ==================== API STREAMS UTILISATEUR ====================

@app.post("/api/streams/create")
async def create_user_stream(
    request: Request,
    title: str = Form(..., min_length=3, max_length=100),
    category: str = Form(...),
    description: str = Form(None, max_length=1000),
    tags: str = Form(None),
    db: Session = Depends(get_db)
):
    """Crée un nouveau stream utilisateur"""
    visitor_id = get_visitor_id(request)
    client_ip = request.client.host if request.client else "0.0.0.0"

    # Vérifier l'IP
    if check_ip_blocked(client_ip, db):
        return JSONResponse(status_code=403, content={"error": "IP bloquée"})

    # Valider la catégorie
    valid_categories = [c["id"] for c in CATEGORIES if not c["id"].startswith("iptv")]
    if category not in valid_categories:
        return JSONResponse(status_code=400, content={"error": "Catégorie invalide"})

    # Nettoyer le titre
    title_clean = html.escape(title.strip())
    if len(title_clean) < 3:
        return JSONResponse(status_code=400, content={"error": "Titre trop court"})

    # Obtenir ou créer le visiteur
    visitor = db.query(Visitor).filter(Visitor.visitor_id == visitor_id).first()
    if not visitor:
        visitor = Visitor(
            visitor_id=visitor_id,
            ip_address=client_ip,
            user_agent=request.headers.get("user-agent", "")
        )
        db.add(visitor)
        db.commit()
        db.refresh(visitor)

    # Vérifier la limite de streams simultanés
    active_streams = db.query(UserStream).filter(
        and_(UserStream.visitor_id == visitor.id, UserStream.is_live == True)
    ).count()
    if active_streams >= settings.MAX_CONCURRENT_STREAMS_PER_USER:
        return JSONResponse(
            status_code=429,
            content={"error": f"Maximum {settings.MAX_CONCURRENT_STREAMS_PER_USER} streams simultanés"}
        )

    # Créer le stream
    stream_key = f"live_{secrets.token_urlsafe(32)}"
    stream = UserStream(
        title=title_clean,
        description=html.escape(description) if description else None,
        category=category,
        tags=html.escape(tags) if tags else None,
        stream_key=stream_key,
        visitor_id=visitor.id,
        stream_url=f"/live/{stream_key}/index.m3u8",
        language=get_language(request)
    )

    try:
        db.add(stream)
        db.commit()
        db.refresh(stream)

        # Mettre à jour les statistiques du visiteur
        visitor.total_streams += 1
        db.commit()

        return JSONResponse({
            "success": True,
            "stream_id": stream.id,
            "stream_key": stream_key,
            "rtmp_url": f"rtmp://localhost/live/{stream_key}",
            "hls_url": f"/live/{stream_key}/index.m3u8",
            "watch_url": f"/watch/user/{stream.id}"
        })
    except Exception as e:
        logger.error(f"Erreur création stream: {e}")
        db.rollback()
        return JSONResponse(status_code=500, content={"error": "Erreur lors de la création du stream"})

@app.post("/api/streams/{stream_id}/start")
async def start_user_stream(stream_id: str, request: Request, db: Session = Depends(get_db)):
    """Démarre un stream (marque comme en direct)"""
    stream = db.query(UserStream).filter(UserStream.id == stream_id).first()
    if not stream:
        return JSONResponse(status_code=404, content={"error": "Stream non trouvé"})

    # Vérifier que l'utilisateur est propriétaire du stream
    visitor_id = get_visitor_id(request)
    if not stream.visitor or stream.visitor.visitor_id != visitor_id:
        return JSONResponse(status_code=403, content={"error": "Non autorisé"})

    stream.is_live = True
    stream.started_at = datetime.utcnow()
    db.commit()

    return JSONResponse({"success": True})

@app.post("/api/streams/{stream_id}/stop")
async def stop_user_stream(stream_id: str, request: Request, db: Session = Depends(get_db)):
    """Arrête un stream"""
    stream = db.query(UserStream).filter(UserStream.id == stream_id).first()
    if not stream:
        return JSONResponse(status_code=404, content={"error": "Stream non trouvé"})

    visitor_id = get_visitor_id(request)
    if not stream.visitor or stream.visitor.visitor_id != visitor_id:
        return JSONResponse(status_code=403, content={"error": "Non autorisé"})

    stream.is_live = False
    stream.ended_at = datetime.utcnow()
    db.commit()

    return JSONResponse({"success": True})

@app.post("/api/streams/{stream_id}/like")
async def like_user_stream(stream_id: str, request: Request, db: Session = Depends(get_db)):
    """Ajoute un like à un stream"""
    stream = db.query(UserStream).filter(UserStream.id == stream_id).first()
    if not stream:
        return JSONResponse(status_code=404, content={"error": "Stream non trouvé"})

    stream.like_count += 1
    db.commit()

    return JSONResponse({"success": True, "likes": stream.like_count})

@app.post("/api/streams/{stream_id}/report")
async def report_user_stream(
    stream_id: str,
    request: Request,
    reason: str = Form(...),
    db: Session = Depends(get_db)
):
    """Signale un stream"""
    visitor_id = get_visitor_id(request)
    stream = db.query(UserStream).filter(UserStream.id == stream_id).first()
    if not stream:
        return JSONResponse(status_code=404, content={"error": "Stream non trouvé"})

    visitor = db.query(Visitor).filter(Visitor.visitor_id == visitor_id).first()
    if not visitor:
        return JSONResponse(status_code=400, content={"error": "Visiteur non trouvé"})

    # Créer le signalement
    report = Report(
        reason=html.escape(reason),
        stream_id=stream_id,
        visitor_id=visitor.id
    )
    db.add(report)

    # Incrémenter le compteur de signalements
    stream.report_count += 1

    # Bloquer automatiquement si trop de signalements
    if stream.report_count >= settings.AUTO_BLOCK_THRESHOLD:
        stream.is_blocked = True
        stream.is_live = False

        # Bloquer l'IP du streamer
        if stream.visitor and stream.visitor.ip_address:
            existing_block = db.query(BlockedIP).filter(
                BlockedIP.ip_address == stream.visitor.ip_address
            ).first()
            if not existing_block:
                blocked = BlockedIP(
                    ip_address=stream.visitor.ip_address,
                    reason=f"Auto-block - {stream.report_count} signalements"
                )
                db.add(blocked)

    db.commit()

    return JSONResponse({"success": True})

# ==================== WEBSOCKET CHAT ====================

@app.websocket("/ws/{stream_id}")
async def websocket_endpoint(websocket: WebSocket, stream_id: str):
    """Endpoint WebSocket pour le chat en direct"""
    db = SessionLocal()
    visitor_id = None

    try:
        # Récupérer l'ID du visiteur
        visitor_id = websocket.cookies.get('visitor_id') or f"vis_{secrets.token_urlsafe(16)}"

        # Vérifier que le stream existe
        stream = db.query(UserStream).filter(UserStream.id == stream_id).first()
        if not stream:
            await websocket.close(code=4004, reason="Stream non trouvé")
            return

        # Vérifier l'IP
        client_ip = websocket.client.host if websocket.client else "0.0.0.0"
        if check_ip_blocked(client_ip, db):
            await websocket.close(code=4003, reason="IP bloquée")
            return

        # Connecter le websocket
        await manager.connect(websocket, stream_id, visitor_id, client_ip)

        # Envoyer l'historique des 50 derniers commentaires
        recent_comments = db.query(Comment).filter(
            and_(
                Comment.stream_id == stream_id,
                Comment.is_deleted == False,
                Comment.is_auto_hidden == False
            )
        ).order_by(Comment.created_at.desc()).limit(50).all()

        for comment in reversed(recent_comments):
            await websocket.send_json({
                "type": "history",
                "id": comment.id,
                "content": comment.content,
                "visitor_id": (comment.visitor.visitor_id[:8] + "...") if comment.visitor else "Anonyme",
                "created_at": comment.created_at.isoformat()
            })

        # Envoyer le nombre de viewers
        await websocket.send_json({
            "type": "viewer_count",
            "count": manager.get_viewer_count(stream_id)
        })

        # Boucle principale de réception
        while True:
            data = await websocket.receive_json()

            if data.get("type") == "comment":
                # Vérifier la limite de taux
                if not manager.check_rate_limit(visitor_id):
                    await websocket.send_json({
                        "type": "error",
                        "message": f"Maximum {settings.MAX_COMMENTS_PER_MINUTE} commentaires par minute"
                    })
                    continue

                content = data.get("content", "").strip()
                if not content or len(content) > settings.MAX_COMMENT_LENGTH:
                    continue

                # Filtrer les mots inappropriés
                content = filter_inappropriate(content)

                # Obtenir ou créer le visiteur
                visitor = db.query(Visitor).filter(Visitor.visitor_id == visitor_id).first()
                if not visitor:
                    visitor = Visitor(
                        visitor_id=visitor_id,
                        ip_address=client_ip,
                        user_agent="WebSocket"
                    )
                    db.add(visitor)
                    db.commit()
                    db.refresh(visitor)

                # Créer le commentaire
                comment = Comment(
                    content=content,
                    stream_id=stream_id,
                    visitor_id=visitor.id,
                    ip_address=client_ip
                )
                db.add(comment)
                visitor.total_comments += 1
                db.commit()
                db.refresh(comment)

                # Diffuser le commentaire à tous les viewers
                await manager.broadcast_to_stream(stream_id, {
                    "type": "comment",
                    "id": comment.id,
                    "content": content,
                    "visitor_id": visitor_id[:8] + "...",
                    "created_at": comment.created_at.isoformat()
                })

                # Enregistrer les statistiques
                stat = StreamStats(
                    stream_id=stream_id,
                    viewer_count=manager.get_viewer_count(stream_id)
                )
                db.add(stat)
                db.commit()

            elif data.get("type") == "report_comment":
                # Signaler un commentaire
                comment_id = data.get("comment_id")
                reason = data.get("reason", "Contenu inapproprié")

                comment = db.query(Comment).filter(Comment.id == comment_id).first()
                if comment:
                    comment.is_flagged = True
                    comment.report_count += 1

                    if comment.report_count >= settings.COMMENT_FLAG_THRESHOLD:
                        comment.is_auto_hidden = True

                    visitor = db.query(Visitor).filter(Visitor.visitor_id == visitor_id).first()
                    if visitor:
                        report = Report(
                            reason=reason,
                            comment_id=comment_id,
                            visitor_id=visitor.id
                        )
                        db.add(report)
                    db.commit()

                    await websocket.send_json({
                        "type": "success",
                        "message": "Commentaire signalé"
                    })

            elif data.get("type") == "ping":
                await websocket.send_json({"type": "pong"})

    except WebSocketDisconnect:
        # Déconnexion normale
        if visitor_id:
            manager.disconnect(websocket, stream_id, visitor_id)
            await manager.update_viewer_count(stream_id)
    except Exception as e:
        logger.error(f"Erreur WebSocket: {e}")
        if visitor_id:
            manager.disconnect(websocket, stream_id, visitor_id)
    finally:
        db.close()

# ==================== PROXY HLS ====================

@app.get("/proxy/stream")
async def proxy_stream_route(url: str, headers: str = None):
    """Route proxy pour les flux HLS/DASH/MP4"""
    custom_headers = None
    if headers:
        try:
            custom_headers = json.loads(headers)
        except:
            pass
    return await proxy.fetch_stream(url, custom_headers)

@app.get("/proxy/audio")
async def proxy_audio_route(url: str):
    """
    Proxy dédié pour les flux audio (MP3, AAC, OGG…).
    Supporte le streaming progressif avec gestion des Range requests.
    """
    try:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        req_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "audio/mpeg, audio/aac, audio/ogg, audio/*, */*",
            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
            "Origin": origin,
            "Referer": origin + "/",
            "Connection": "keep-alive",
        }

        async def audio_stream_generator(stream_url: str, hdrs: dict):
            async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
                async with client.stream("GET", stream_url, headers=hdrs) as response:
                    response.raise_for_status()
                    async for chunk in response.aiter_bytes(chunk_size=8192):
                        yield chunk

        # Détecter le content-type via HEAD d'abord
        content_type = "audio/mpeg"
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
                head = await client.head(url, headers=req_headers)
                ct = head.headers.get("content-type", "")
                if ct:
                    content_type = ct.split(";")[0].strip()
        except:
            pass

        # Déduire depuis l'extension si HEAD ne répond pas
        if ";" in content_type or content_type == "application/octet-stream":
            ext = url.split("?")[0].split(".")[-1].lower()
            content_type = {
                "mp3": "audio/mpeg", "aac": "audio/aac", "ogg": "audio/ogg",
                "flac": "audio/flac", "opus": "audio/opus", "m4a": "audio/mp4",
                "wav": "audio/wav", "m3u8": "application/x-mpegURL",
            }.get(ext, "audio/mpeg")

        return StreamingResponse(
            audio_stream_generator(url, req_headers),
            media_type=content_type,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Cache-Control": "no-cache",
                "X-Content-Type-Options": "nosniff",
            }
        )
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail="Flux audio inaccessible")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Erreur proxy audio: {str(e)}")

# ==================== API FAVORIS ====================

@app.post("/api/favorites/add")
async def add_favorite(
    request: Request,
    stream_id: str = Form(...),
    stream_type: str = Form(...),
    db: Session = Depends(get_db)
):
    """Ajoute un favori"""
    visitor_id = get_visitor_id(request)

    # Obtenir ou créer le visiteur
    visitor = db.query(Visitor).filter(Visitor.visitor_id == visitor_id).first()
    if not visitor:
        visitor = Visitor(
            visitor_id=visitor_id,
            ip_address=request.client.host if request.client else "0.0.0.0"
        )
        db.add(visitor)
        db.commit()
        db.refresh(visitor)

    # Vérifier si déjà en favoris
    existing = db.query(Favorite).filter(
        Favorite.visitor_id == visitor.id,
        Favorite.stream_id == stream_id
    ).first()

    if not existing:
        fav = Favorite(
            visitor_id=visitor.id,
            stream_id=stream_id,
            stream_type=stream_type
        )
        db.add(fav)
        db.commit()

    return JSONResponse({"success": True})

@app.post("/api/favorites/remove")
async def remove_favorite(request: Request, stream_id: str = Form(...), db: Session = Depends(get_db)):
    """Supprime un favori"""
    visitor_id = request.cookies.get('visitor_id')
    if visitor_id:
        visitor = db.query(Visitor).filter(Visitor.visitor_id == visitor_id).first()
        if visitor:
            db.query(Favorite).filter(
                Favorite.visitor_id == visitor.id,
                Favorite.stream_id == stream_id
            ).delete()
            db.commit()
    return JSONResponse({"success": True})

@app.get("/api/favorites")
async def get_favorites(request: Request, db: Session = Depends(get_db)):
    """Récupère les favoris du visiteur"""
    visitor_id = get_visitor_id(request)  # cohérent avec add_favorite
    favorites = []

    visitor = db.query(Visitor).filter(Visitor.visitor_id == visitor_id).first()
    if visitor:
        favs = db.query(Favorite).filter(Favorite.visitor_id == visitor.id).all()
        for fav in favs:
            if fav.stream_type == "external":
                stream = db.query(ExternalStream).filter(ExternalStream.id == fav.stream_id).first()
                if stream:
                    favorites.append({
                        "id": str(stream.id), "title": stream.title,
                        "category": stream.category, "logo": stream.logo or "",
                        "type": "external", "url": f"/watch/external/{stream.id}"
                    })
            elif fav.stream_type == "iptv":
                channel = db.query(IPTVChannel).filter(IPTVChannel.id == fav.stream_id).first()
                if channel:
                    favorites.append({
                        "id": str(channel.id), "title": channel.name,
                        "category": "iptv", "logo": channel.logo or "",
                        "type": "iptv", "url": f"/watch/iptv/{channel.id}"
                    })
            else:
                stream = db.query(UserStream).filter(UserStream.id == fav.stream_id).first()
                if stream:
                    favorites.append({
                        "id": str(stream.id), "title": stream.title,
                        "category": stream.category, "logo": "",
                        "type": "user", "is_live": stream.is_live,
                        "url": f"/watch/user/{stream.id}"
                    })

    return JSONResponse(favorites)

# ==================== UPLOAD DE FICHIERS ====================

@app.post("/api/upload/thumbnail")
async def upload_thumbnail(request: Request, file: UploadFile = File(...)):
    """Upload une miniature pour un stream"""
    # Lire le contenu
    content = await file.read()
    if len(content) > settings.MAX_UPLOAD_SIZE:
        return JSONResponse(
            status_code=413,
            content={"error": f"Fichier trop volumineux (max {settings.MAX_UPLOAD_SIZE//1024//1024}MB)"}
        )

    # Vérifier l'extension
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in settings.ALLOWED_EXTENSIONS:
        return JSONResponse(
            status_code=400,
            content={"error": f"Extension non autorisée: {ext}"}
        )

    # Générer un nom de fichier unique
    filename = f"{uuid.uuid4()}{ext}"
    filepath = f"static/thumbnails/{filename}"

    # Sauvegarder le fichier
    async with aiofiles.open(filepath, 'wb') as f:
        await f.write(content)

    # Optimiser l'image
    try:
        img = Image.open(filepath)
        img.thumbnail((640, 360), Image.Resampling.LANCZOS)
        img.save(filepath, optimize=True, quality=85)
    except Exception as e:
        logger.warning(f"Impossible d'optimiser l'image: {e}")

    return JSONResponse({
        "success": True,
        "url": f"/static/thumbnails/{filename}"
    })

# ==================== API IPTV ====================

@app.post("/api/admin/iptv/sync")
async def admin_sync_iptv(request: Request):
    try: require_admin(request)
    except HTTPException: return JSONResponse(status_code=401, content={"success": False, "error": "Non autorisé"})
    if iptv_sync.is_syncing:
        return JSONResponse({"success": False, "message": "Synchronisation déjà en cours", "is_syncing": True})
    asyncio.create_task(iptv_sync.sync_all_playlists())
    return JSONResponse({"success": True, "message": "Synchronisation démarrée", "is_syncing": True})

@app.get("/api/iptv/stats")
async def iptv_stats(db: Session = Depends(get_db)):
    """Statistiques IPTV"""
    total_channels = db.query(IPTVChannel).count()
    active_channels = db.query(IPTVChannel).filter(IPTVChannel.is_active == True).count()
    total_playlists = db.query(IPTVPlaylist).count()

    last_sync = db.query(IPTVPlaylist.last_sync).order_by(desc(IPTVPlaylist.last_sync)).first()

    # Top catégories
    categories = db.query(
        IPTVChannel.category,
        func.count(IPTVChannel.id).label('count')
    ).group_by(IPTVChannel.category).order_by(desc('count')).limit(10).all()

    return JSONResponse({
        "total_channels": total_channels,
        "active_channels": active_channels,
        "total_playlists": total_playlists,
        "last_sync": last_sync[0].isoformat() if last_sync and last_sync[0] else None,
        "top_categories": [{"category": c[0], "count": c[1]} for c in categories],
        "is_syncing": iptv_sync.is_syncing
    })

@app.get("/api/iptv/playlists")
async def iptv_playlists(db: Session = Depends(get_db)):
    """Liste toutes les playlists IPTV"""
    playlists = db.query(IPTVPlaylist).filter(IPTVPlaylist.is_active == True).all()
    return JSONResponse([
        {
            "id": p.id,
            "name": p.name,
            "display_name": p.display_name,
            "channel_count": p.channel_count,
            "last_sync": p.last_sync.isoformat() if p.last_sync else None,
            "playlist_type": p.playlist_type,
            "country": p.country
        }
        for p in playlists
    ])

@app.get("/api/iptv/channels")
async def iptv_channels(
    playlist: str = None,
    category: str = None,
    country: str = None,
    search: str = None,
    limit: int = 100,
    db: Session = Depends(get_db)
):
    """Recherche de chaînes IPTV avec filtres"""
    query = db.query(IPTVChannel).filter(IPTVChannel.is_active == True)

    if playlist:
        query = query.filter(IPTVChannel.playlist_id == playlist)
    if category:
        query = query.filter(IPTVChannel.category.ilike(f"%{category}%"))
    if country:
        query = query.filter(IPTVChannel.country == country)
    if search:
        query = query.filter(IPTVChannel.name.ilike(f"%{search}%"))

    channels = query.order_by(IPTVChannel.name).limit(limit).all()

    return JSONResponse([
        {
            "id": ch.id,
            "name": ch.name,
            "url": ch.url,
            "logo": ch.logo,
            "category": ch.category,
            "country": ch.country,
            "language": ch.language,
            "stream_type": ch.stream_type
        }
        for ch in channels
    ])

# ==================== ADMIN ====================

@app.get("/admin", response_class=HTMLResponse)
async def admin_login_page(request: Request):
    """Page de connexion admin"""
    # Vérifier si déjà connecté
    token = request.cookies.get("admin_token")
    if token:
        try:
            payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
            if payload.get("admin"):
                return RedirectResponse(url="/admin/dashboard", status_code=303)
        except JWTError:
            pass

    return templates.TemplateResponse(
        "admin_login.html",
        {
            "request": request,
            "language": get_language(request),
            "app_name": settings.APP_NAME,
            "categories": CATEGORIES,
            "logo_path": settings.LOGO_PATH if os.path.exists(settings.LOGO_PATH) else None
        }
    )

@app.post("/admin/login")
async def admin_login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    """Traitement de la connexion admin"""
    user = db.query(User).filter(
        and_(User.username == username, User.is_admin == True, User.is_blocked == False)
    ).first()

    if user and verify_password(password, user.hashed_password):
        # Réinitialiser les tentatives échouées
        user.failed_login_attempts = 0
        user.locked_until = None
        user.last_login = datetime.utcnow()
        user.ip_address = request.client.host if request.client else "0.0.0.0"
        db.commit()

        # Créer le token
        token = create_access_token(
            data={
                "sub": user.id,
                "admin": True,
                "username": user.username,
                "is_owner": user.is_owner
            },
            expires_delta=timedelta(hours=24)
        )

        response = RedirectResponse(url="/admin/dashboard", status_code=303)
        response.set_cookie(
            key="admin_token",
            value=token,
            max_age=86400,
            httponly=True,
            samesite="lax",
            secure=False  # Mettre à True en production avec HTTPS
        )
        return response

    # Incrémenter les tentatives échouées
    if user:
        user.failed_login_attempts = (user.failed_login_attempts or 0) + 1
        db.commit()

    return templates.TemplateResponse(
        "admin_login.html",
        {
            "request": request,
            "error": "Identifiants incorrects",
            "language": get_language(request),
            "app_name": settings.APP_NAME,
            "categories": CATEGORIES
        }
    )

@app.get("/admin/logout")
async def admin_logout():
    """Déconnexion admin"""
    response = RedirectResponse(url="/admin", status_code=303)
    response.delete_cookie("admin_token")
    return response

@app.get("/admin/dashboard", response_class=HTMLResponse)
async def admin_dashboard(request: Request, db: Session = Depends(get_db)):
    """Tableau de bord admin"""
    # Vérifier l'authentification
    token = request.cookies.get("admin_token")
    if not token:
        return RedirectResponse(url="/admin", status_code=303)

    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        if not payload.get("admin"):
            return RedirectResponse(url="/admin", status_code=303)

        user_id = payload.get("sub")
        user = db.query(User).filter(User.id == user_id).first()
        if not user or not user.is_admin:
            return RedirectResponse(url="/admin", status_code=303)
    except JWTError:
        return RedirectResponse(url="/admin", status_code=303)

    # Statistiques
    now = datetime.utcnow()
    stats = {
        "total_streams":    db.query(UserStream).count(),
        "live_streams":     db.query(UserStream).filter(UserStream.is_live == True).count(),
        "total_comments":   db.query(Comment).count(),
        "total_visitors":   db.query(Visitor).count(),
        "total_reports":    db.query(Report).filter(Report.resolved == False).count(),
        "external_streams": db.query(ExternalStream).count(),
        "iptv_channels":    db.query(IPTVChannel).count(),
        "iptv_playlists":   db.query(IPTVPlaylist).count(),
        "blocked_ips":      db.query(BlockedIP).filter(
            or_(BlockedIP.expires_at.is_(None), BlockedIP.expires_at > now)
        ).count(),
        "youtube_streams":  db.query(ExternalStream).filter(ExternalStream.stream_type == "youtube").count(),
        "total_events":     db.query(TVEvent).filter(TVEvent.end_time >= now).count(),
        "upcoming_events":  db.query(TVEvent).filter(TVEvent.start_time >= now).count(),
        "total_reminders":  db.query(EventReminder).count(),
    }

    # Données pour les tableaux
    user_streams     = db.query(UserStream).order_by(desc(UserStream.created_at)).limit(100).all()
    external_streams = db.query(ExternalStream).order_by(desc(ExternalStream.created_at)).limit(200).all()
    iptv_playlists   = db.query(IPTVPlaylist).filter(
        IPTVPlaylist.playlist_type == "country"
    ).order_by(IPTVPlaylist.display_name).all()
    recent_comments  = db.query(Comment).filter(
        Comment.is_deleted == False
    ).order_by(desc(Comment.created_at)).limit(100).all()
    pending_reports  = db.query(Report).filter(Report.resolved == False).order_by(desc(Report.created_at)).all()
    blocked_ips      = db.query(BlockedIP).filter(
        or_(BlockedIP.expires_at.is_(None), BlockedIP.expires_at > now)
    ).all()
    epg_events       = db.query(TVEvent).order_by(TVEvent.start_time).all()

    return templates.TemplateResponse(
        "admin.html",
        {
            "request":         request,
            "language":        get_language(request),
            "user":            user,
            "stats":           stats,
            "user_streams":    user_streams,
            "external_streams":external_streams,
            "iptv_playlists":  iptv_playlists,
            "recent_comments": recent_comments,
            "pending_reports": pending_reports,
            "blocked_ips":     blocked_ips,
            "epg_events":      epg_events,
            "app_name":        settings.APP_NAME,
            "categories":      CATEGORIES,
            "is_syncing":      iptv_sync.is_syncing,
            "last_sync":       iptv_sync.last_sync,
            "logo_path":       settings.LOGO_PATH if os.path.exists(settings.LOGO_PATH) else None
        }
    )

# ==================== ACTIONS ADMIN ====================

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Retourne JSON pour les routes API, HTML sinon"""
    if request.url.path.startswith("/api/"):
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.detail, "success": False}
        )
    # Pour les routes HTML, rediriger vers login si 401
    if exc.status_code == 401:
        return RedirectResponse(url="/admin", status_code=303)
    raise exc

@app.post("/api/admin/streams/{stream_id}/block")
async def admin_block_stream(stream_id: str, request: Request, db: Session = Depends(get_db)):
    try: require_admin(request)
    except HTTPException: return JSONResponse(status_code=401, content={"success": False, "error": "Non autorisé"})
    stream = db.query(UserStream).filter(UserStream.id == stream_id).first()
    if stream:
        stream.is_blocked = True
        stream.is_live = False
        db.commit()
    return JSONResponse({"success": True})

@app.post("/api/admin/streams/{stream_id}/unblock")
async def admin_unblock_stream(stream_id: str, request: Request, db: Session = Depends(get_db)):
    try: require_admin(request)
    except HTTPException: return JSONResponse(status_code=401, content={"success": False, "error": "Non autorisé"})
    stream = db.query(UserStream).filter(UserStream.id == stream_id).first()
    if stream:
        stream.is_blocked = False
        db.commit()
    return JSONResponse({"success": True})

@app.post("/api/admin/comments/{comment_id}/delete")
async def admin_delete_comment(comment_id: str, request: Request, db: Session = Depends(get_db)):
    try: require_admin(request)
    except HTTPException: return JSONResponse(status_code=401, content={"success": False, "error": "Non autorisé"})
    comment = db.query(Comment).filter(Comment.id == comment_id).first()
    if comment:
        comment.is_deleted = True
        db.commit()
    return JSONResponse({"success": True})

@app.post("/api/admin/ips/block")
async def admin_block_ip(
    request: Request,
    ip_address: str = Form(...),
    reason: str = Form("Raison non spécifiée"),
    permanent: bool = Form(False),
    db: Session = Depends(get_db)
):
    try: require_admin(request)
    except HTTPException: return JSONResponse(status_code=401, content={"success": False, "error": "Non autorisé"})
    try:
        validate_ip(ip_address)
    except ValueError:
        return JSONResponse(status_code=400, content={"error": "Adresse IP invalide"})
    existing = db.query(BlockedIP).filter(BlockedIP.ip_address == ip_address).first()
    if existing:
        return JSONResponse(status_code=400, content={"error": "IP déjà bloquée"})
    blocked = BlockedIP(
        ip_address=ip_address,
        reason=html.escape(reason),
        is_permanent=permanent,
        expires_at=None if permanent else datetime.utcnow() + timedelta(days=30)
    )
    db.add(blocked)
    visitors = db.query(Visitor).filter(Visitor.ip_address == ip_address).all()
    for visitor in visitors:
        db.query(UserStream).filter(
            and_(UserStream.visitor_id == visitor.id, UserStream.is_live == True)
        ).update({"is_live": False, "is_blocked": True})
    db.commit()
    return JSONResponse({"success": True})

@app.post("/api/admin/ips/{ip_id}/unblock")
async def admin_unblock_ip(ip_id: str, request: Request, db: Session = Depends(get_db)):
    try: require_admin(request)
    except HTTPException: return JSONResponse(status_code=401, content={"success": False, "error": "Non autorisé"})
    db.query(BlockedIP).filter(BlockedIP.id == ip_id).delete()
    db.commit()
    return JSONResponse({"success": True})

@app.post("/api/admin/reports/{report_id}/resolve")
async def admin_resolve_report(report_id: str, request: Request, db: Session = Depends(get_db)):
    try: require_admin(request)
    except HTTPException: return JSONResponse(status_code=401, content={"success": False, "error": "Non autorisé"})
    report = db.query(Report).filter(Report.id == report_id).first()
    if report:
        report.resolved = True
        report.resolved_at = datetime.utcnow()
        db.commit()
    return JSONResponse({"success": True})

@app.post("/api/admin/external/{stream_id}/toggle")
async def admin_toggle_external(stream_id: str, request: Request, db: Session = Depends(get_db)):
    try: require_admin(request)
    except HTTPException: return JSONResponse(status_code=401, content={"success": False, "error": "Non autorisé"})
    stream = db.query(ExternalStream).filter(ExternalStream.id == stream_id).first()
    if stream:
        stream.is_active = not stream.is_active
        db.commit()
        return JSONResponse({"success": True, "is_active": stream.is_active})
    return JSONResponse(status_code=404, content={"error": "Stream non trouvé"})

@app.post("/api/admin/iptv/playlist/{playlist_name}/refresh")
async def admin_refresh_playlist(playlist_name: str, request: Request, db: Session = Depends(get_db)):
    try: require_admin(request)
    except HTTPException: return JSONResponse(status_code=401, content={"success": False, "error": "Non autorisé"})
    playlist = db.query(IPTVPlaylist).filter(IPTVPlaylist.name == playlist_name).first()
    if not playlist:
        return JSONResponse(status_code=404, content={"error": "Playlist non trouvée"})
    asyncio.create_task(iptv_sync.sync_all_playlists())
    return JSONResponse({"success": True, "message": "Synchronisation lancée"})

@app.get("/health")
async def health_check(db: Session = Depends(get_db)):
    """Endpoint de santé pour Docker / load balancer"""
    try:
        from sqlalchemy import text
        db.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception as e:
        db_status = f"error: {e}"
    return JSONResponse({
        "status":   "ok" if db_status == "ok" else "degraded",
        "version":  settings.APP_VERSION,
        "database": db_status,
        "syncing":  iptv_sync.is_syncing,
        "ts":       datetime.utcnow().isoformat(),
    })

@app.get("/api/admin/config/export")
async def admin_export_config(request: Request):
    """Télécharge le .env actuel"""
    try: require_admin(request)
    except HTTPException: return JSONResponse(status_code=401, content={"error": "Non autorisé"})
    env_content = (
        f"# Livewatch — exporté le {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC\n"
        f"DATABASE_URL={settings.DATABASE_URL}\n"
        f"SECRET_KEY={settings.SECRET_KEY}\n"
        f"ADMIN_USERNAME={settings.ADMIN_USERNAME}\n"
        f"ADMIN_PASSWORD={settings.ADMIN_PASSWORD}\n"
        f"ADMIN_EMAIL={settings.ADMIN_EMAIL}\n"
        f"DB_POOL_SIZE={settings.DATABASE_POOL_SIZE}\n"
        f"DB_MAX_OVERFLOW={settings.DATABASE_MAX_OVERFLOW}\n"
        f"DB_POOL_TIMEOUT={settings.DATABASE_POOL_TIMEOUT}\n"
        f"DB_POOL_RECYCLE={settings.DATABASE_POOL_RECYCLE}\n"
    )
    return Response(
        content=env_content, media_type="text/plain",
        headers={"Content-Disposition": "attachment; filename=.env"}
    )

@app.get("/api/admin/stats/live")
async def admin_live_stats(request: Request, db: Session = Depends(get_db)):
    """Stats temps réel pour le dashboard admin"""
    try: require_admin(request)
    except HTTPException: return JSONResponse(status_code=401, content={"error": "Non autorisé"})
    # Vérifier la santé PostgreSQL
    try:
        from sqlalchemy import text
        db.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception as e:
        db_status = str(e)[:100]
    snap = active_tracker.snapshot()
    return JSONResponse({
        "live_streams":    db.query(UserStream).filter(UserStream.is_live == True).count(),
        "total_visitors":  db.query(Visitor).count(),
        "active_users":    snap["active_users"],
        "top_pages":       snap["top_pages"],
        "iptv_channels":   db.query(IPTVChannel).count(),
        "upcoming_events": db.query(TVEvent).filter(TVEvent.start_time >= datetime.utcnow()).count(),
        "is_syncing":      iptv_sync.is_syncing,
        "db_status":       db_status,
        "db_host":         settings.DATABASE_URL.split("@")[-1],
        "ts":              datetime.utcnow().isoformat(),
    })


@app.websocket("/ws/admin/live")
async def admin_live_ws(websocket: WebSocket):
    """
    WebSocket temps réel pour le dashboard admin.
    Pousse les stats toutes les 5 secondes.
    Requiert un cookie admin_token valide.
    """
    # Vérifier le token admin via cookie
    token = websocket.cookies.get("admin_token")
    if not token:
        await websocket.close(code=4001)
        return
    try:
        from jose import jwt as _jwt
        payload = _jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        if not payload.get("admin"):
            await websocket.close(code=4001)
            return
    except Exception:
        await websocket.close(code=4001)
        return

    await active_tracker.connect_admin(websocket)
    try:
        # Envoyer un snapshot immédiat à la connexion
        await websocket.send_json({"type": "stats", **active_tracker.snapshot()})
        # Garder la connexion ouverte en attendant les messages du client (ping)
        while True:
            try:
                msg = await asyncio.wait_for(websocket.receive_text(), timeout=30)
                if msg == "ping":
                    await websocket.send_json({"type": "pong"})
            except asyncio.TimeoutError:
                # Envoyer un heartbeat
                await websocket.send_json({"type": "heartbeat", "ts": datetime.utcnow().isoformat()})
    except Exception:
        pass
    finally:
        active_tracker.disconnect_admin(websocket)

@app.get("/api/admin/stats/history")
async def admin_stats_history(
    request: Request,
    period: str = "month",   # "week" | "month" | "year"
    db: Session = Depends(get_db)
):
    """
    Retourne les statistiques historiques de visites.
    period: 'week' (7j), 'month' (30j), 'year' (365j)
    """
    try: require_admin(request)
    except HTTPException: return JSONResponse(status_code=401, content={"error": "Non autorisé"})

    days = {"week": 7, "month": 30, "year": 365}.get(period, 30)
    since = datetime.utcnow() - timedelta(days=days)

    # Récupérer les entrées DailyVisitStats existantes
    rows = db.query(DailyVisitStats).filter(
        DailyVisitStats.date >= since
    ).order_by(DailyVisitStats.date).all()

    # Construire un index date → stats
    stored: Dict[str, dict] = {}
    for r in rows:
        key = r.date.strftime("%Y-%m-%d")
        stored[key] = {
            "unique_users": r.unique_users,
            "peak_active":  r.peak_active,
            "page_views":   r.page_views,
        }

    # Compléter les jours manquants avec des données de la table Visitor
    # (pour les jours avant l'installation de DailyVisitStats)
    result_days = []
    for i in range(days):
        day    = datetime.utcnow() - timedelta(days=days - 1 - i)
        day_0  = datetime(day.year, day.month, day.day)
        key    = day_0.strftime("%Y-%m-%d")

        if key in stored:
            entry = stored[key]
        else:
            # Calculer depuis la table Visitor si pas encore enregistré
            count = db.query(Visitor).filter(
                Visitor.created_at >= day_0,
                Visitor.created_at <  day_0 + timedelta(days=1)
            ).count()
            entry = {"unique_users": count, "peak_active": 0, "page_views": count}

        result_days.append({
            "date":         key,
            "label":        day_0.strftime("%d/%m" if days <= 30 else "%b %Y"),
            "unique_users": entry["unique_users"],
            "peak_active":  entry["peak_active"],
            "page_views":   entry["page_views"],
        })

    # Agréger par mois pour la vue "year"
    if period == "year":
        monthly: Dict[str, dict] = {}
        for d in result_days:
            m = d["date"][:7]   # "2026-03"
            if m not in monthly:
                monthly[m] = {"date": m, "label": d["label"], "unique_users": 0, "peak_active": 0, "page_views": 0}
            monthly[m]["unique_users"] += d["unique_users"]
            monthly[m]["peak_active"]   = max(monthly[m]["peak_active"], d["peak_active"])
            monthly[m]["page_views"]   += d["page_views"]
        result_days = list(monthly.values())

    # Totaux de la période
    total_unique = sum(d["unique_users"] for d in result_days)
    total_views  = sum(d["page_views"]   for d in result_days)
    max_peak     = max((d["peak_active"] for d in result_days), default=0)

    return JSONResponse({
        "period":       period,
        "days":         result_days,
        "total_unique": total_unique,
        "total_views":  total_views,
        "max_peak":     max_peak,
    })
async def admin_epg_seed(request: Request, db: Session = Depends(get_db)):
    """Resynchronise les événements EPG depuis les sources XMLTV réelles"""
    try: require_admin(request)
    except HTTPException: return JSONResponse(status_code=401, content={"error": "Non autorisé"})
    db.query(EventReminder).delete(synchronize_session=False)
    db.query(TVEvent).delete(synchronize_session=False)
    db.commit()
    inserted = await fetch_xmltv_events(db, max_events=20000)
    if inserted == 0:
        return JSONResponse({
            "success": False,
            "message": "Sources XMLTV indisponibles. Vérifiez la connexion réseau et réessayez dans quelques minutes.",
            "source": "none"
        })
    return JSONResponse({"success": True, "message": f"{inserted} programmes TV réels insérés depuis les guides XMLTV", "source": "xmltv"})

@app.post("/api/admin/epg/{event_id}/delete")
async def admin_delete_event(event_id: str, request: Request, db: Session = Depends(get_db)):
    """Supprime un événement EPG"""
    try: require_admin(request)
    except HTTPException: return JSONResponse(status_code=401, content={"error": "Non autorisé"})
    ev = db.query(TVEvent).filter(TVEvent.id == event_id).first()
    if ev:
        db.delete(ev)
        db.commit()
    return JSONResponse({"success": True})

# ==================== SERVICE EPG INTELLIGENT (SmartEPG) ====================
#
# Architecture :
#   EPG global compressé (epgshare01)
#       ↓  téléchargement + décompression gzip
#   Parser XML en streaming (iterparse — faible mémoire)
#       ↓  filtrage : seules les chaînes présentes dans l'appli
#   Catégorisation intelligente (sport / cinéma / news / kids / docs / musique)
#       ↓  sélection des 50 meilleurs événements par catégorie par jour
#   PostgreSQL (table tv_events)
#       ↓  API + affichage HTML

# ── Sources EPG globales ─────────────────────────────────────────────────────
SMART_EPG_SOURCES = [
    # Source principale — couvre des milliers de chaînes mondiales
    "https://epgshare01.online/epgshare01/epg_ripper_ALL_SOURCES1.xml.gz",
    # Backup 1 — EPG complet epg.best
    "https://epg.best/epg.xml.gz",
    # Backup 2 — Freeview UK
    "https://raw.githubusercontent.com/dp247/Freeview-EPG/master/epg.xml",
    # Backup 3 — open-epg France (non-gzippé, fiable)
    "https://www.open-epg.com/files/france2.xml",
    "https://www.open-epg.com/files/unitedkingdom1.xml",
    "https://www.open-epg.com/files/germany1.xml",
    "https://www.open-epg.com/files/spain1.xml",
    "https://www.open-epg.com/files/italy1.xml",
    "https://www.open-epg.com/files/unitedstates3.xml",
    "https://www.open-epg.com/files/sports1.xml",
    "https://www.open-epg.com/files/sports2.xml",
    "https://www.open-epg.com/files/sports4.xml",
    "https://www.open-epg.com/files/canada1.xml",
    "https://www.open-epg.com/files/brazil1.xml",
    "https://www.open-epg.com/files/mexico1.xml",
    "https://www.open-epg.com/files/turkey1.xml",
    "https://www.open-epg.com/files/russia2.xml",
    "https://www.open-epg.com/files/portugal1.xml",
    "https://www.open-epg.com/files/poland1.xml",
    "https://www.open-epg.com/files/netherlands1.xml",
    "https://www.open-epg.com/files/belgium1.xml",
    "https://www.open-epg.com/files/switzerland2.xml",
    "https://www.open-epg.com/files/australia1.xml",
    "https://www.open-epg.com/files/india1.xml",
    "https://www.open-epg.com/files/saudiarabia1.xml",
    "https://www.open-epg.com/files/morocco1.xml",
    "https://www.open-epg.com/files/egypt1.xml",
    "https://www.open-epg.com/files/southafrica1.xml",
    "https://www.open-epg.com/files/nigeria1.xml",
    "https://www.open-epg.com/files/ivorycoast1.xml",
    "https://www.open-epg.com/files/argentina1.xml",
    "https://www.open-epg.com/files/japan1.xml",
    "https://www.open-epg.com/files/korea1.xml",
]

# Priorité des catégories (ordre d'importance pour la sélection des 50/catégorie)
SMART_EPG_PRIORITY_KEYWORDS = {
    "sport": [
        # Matchs & compétitions (priorité max)
        " vs ", " v ", "match", "final", "finale", "semifinal", "champions league",
        "ligue des champions", "world cup", "coupe du monde", "euro ", "copa ",
        "nba", "nfl", "mlb", "nhl", "ufc", "mma", "boxing", "boxe",
        "formula 1", "f1", "motogp", "tour de france", "roland garros", "wimbledon",
        "olympic", "olympique", "psg", "real madrid", "barcelona", "manchester",
        "liverpool", "chelsea", "juventus", "milan", "arsenal", "bundesliga",
        "premier league", "ligue 1", "serie a", "la liga",
        # Sports généraux
        "football", "soccer", "basketball", "tennis", "rugby", "golf",
        "volleyball", "handball", "hockey", "cricket", "athletics", "natation",
        "cyclisme", "ski", "swimming", "cycling", "sport", "sports",
    ],
    "cinema": [
        # Films premium (priorité max)
        "film", "movie", "cinéma", "cinema", "thriller", "action", "comédie",
        "comedy", "drame", "drama", "horror", "horreur", "romance", "aventure",
        "adventure", "science-fiction", "sci-fi", "western", "animation",
        "documentaire", "documentary", "série", "series", "saison", "season",
        "episode", "sitcom", "feuilleton", "téléfilm", "tv movie",
    ],
    "news": [
        "journal", "jt ", "news", "actualité", "actualites", "info",
        "breaking", "en direct", "live news", "bulletin", "édition",
        "weather", "météo", "politique", "economics", "économie",
        "reportage", "enquête", "débat", "interview", "parlement",
    ],
    "kids": [
        "enfant", "kids", "children", "cartoon", "animation", "animé",
        "jeunesse", "junior", "disney", "pixar", "dreamworks",
        "nickelodeon", "cartoon network", "manga", "pokémon", "winx",
        "play", "aventure jeunesse",
    ],
    "documentary": [
        "documentaire", "documentary", "nature", "science", "histoire",
        "history", "géographie", "geography", "discovery", "national geographic",
        "wildlife", "animal", "espace", "space", "biographie", "biography",
        "exploration", "enquête", "investigation", "voyage", "travel",
        "cuisine", "culture", "arte",
    ],
    "music": [
        "concert", "musique", "music", "live music", "festival", "jazz",
        "pop", "rock", "hip-hop", "rap", "opera", "opéra", "variétés",
        "chart", "clip", "video clip", "talent show", "star academy",
        "the voice", "x factor", "eurovision",
    ],
}

# Mots-clés pour détecter le pays d'un channel_id/nom
EPG_COUNTRY_HINTS = {
    ".fr": "FR", "france": "FR", "tf1": "FR", "m6": "FR", "canal": "FR",
    "bfm": "FR", "lci": "FR", "arte": "FR", "rmc": "FR", "tmc": "FR",
    ".gb": "GB", ".uk": "GB", "bbc": "GB", "sky": "GB", "itv": "GB",
    "channel4": "GB", "channel5": "GB",
    ".de": "DE", "ard": "DE", "zdf": "DE", "rtl.de": "DE",
    ".es": "ES", "rtve": "ES", "antena3": "ES", "cuatro": "ES",
    ".it": "IT", "rai": "IT", "mediaset": "IT",
    ".us": "US", ".com": "US", "cnn": "US", "fox": "US", "nbc": "US",
    "abc": "US", "cbs": "US", "espn": "US",
    ".ca": "CA", "cbc": "CA", "ctv": "CA", "tva": "CA",
    ".br": "BR", "globo": "BR", "record": "BR",
    ".ar": "AR", ".mx": "MX", ".be": "BE", ".ch": "CH", ".nl": "NL",
    ".pt": "PT", ".ru": "RU", ".pl": "PL", ".tr": "TR",
    "aljazeera": "QA", "al jazeera": "QA",
    "euronews": "EU", "eurosport": "EU",
    "nasa": "US", "redbull": "INT", "red bull": "INT",
}


def _smart_xmltv_category(title: str, cat_raw: str) -> str:
    """Détecte la catégorie à partir du titre ET de la balise category."""
    combined = (title + " " + cat_raw).lower()
    # Sport en priorité absolue (matchs = le contenu le plus recherché)
    for kw in SMART_EPG_PRIORITY_KEYWORDS["sport"]:
        if kw in combined:
            return "sport"
    for kw in SMART_EPG_PRIORITY_KEYWORDS["cinema"]:
        if kw in combined:
            return "cinema"
    for kw in SMART_EPG_PRIORITY_KEYWORDS["news"]:
        if kw in combined:
            return "news"
    for kw in SMART_EPG_PRIORITY_KEYWORDS["kids"]:
        if kw in combined:
            return "kids"
    for kw in SMART_EPG_PRIORITY_KEYWORDS["documentary"]:
        if kw in combined:
            return "documentary"
    for kw in SMART_EPG_PRIORITY_KEYWORDS["music"]:
        if kw in combined:
            return "music"
    return "other"


def _detect_country_from_channel(channel_id: str, channel_name: str) -> str:
    """Devine le pays depuis l'id ou le nom de la chaîne."""
    combined = (channel_id + " " + channel_name).lower()
    for hint, code in EPG_COUNTRY_HINTS.items():
        if hint in combined:
            return code
    return "INT"


def _is_priority_event(title: str, category: str) -> int:
    """Retourne un score de priorité (plus élevé = plus important)."""
    t = title.lower()
    score = 0
    # Matchs sportifs = score maximal
    if " vs " in t or " v " in t:
        score += 100
    if any(k in t for k in ["final", "finale", "champions", "world cup", "coupe du monde", "ufc", "mma"]):
        score += 80
    if any(k in t for k in ["nba", "nfl", "premier league", "ligue 1", "formula 1", "f1"]):
        score += 60
    # Films premium
    if any(k in t for k in ["film", "movie", "cinéma"]):
        score += 40
    # News importantes
    if any(k in t for k in ["breaking", "special", "direct", "live"]):
        score += 30
    # Contenu de qualité général
    if len(title) > 10:
        score += 5
    return score


def _build_app_channel_set(db) -> Dict[str, dict]:
    """
    Construit un dictionnaire de TOUTES les chaînes présentes dans l'appli.
    Clé : nom normalisé (minuscules, sans accents simples).
    Valeur : {logo, country, stream_id, stream_type}

    Inclut :
      - EXTERNAL_STREAMS (flux HLS/YouTube/Radio définis dans le code)
      - IPTVChannel (chaînes synchronisées depuis les playlists M3U)
    """
    app_channels: Dict[str, dict] = {}

    # ── 1. Flux externes (EXTERNAL_STREAMS) ──────────────────────────
    for s in EXTERNAL_STREAMS:
        name_norm = s["title"].lower().strip()
        app_channels[name_norm] = {
            "logo":        s.get("logo", ""),
            "country":     s.get("country", "INT"),
            "stream_id":   None,   # sera rempli depuis la DB
            "stream_type": "external",
            "orig_name":   s["title"],
        }
        # Aussi indexer par mots significatifs (ex. "TF1", "BBC World")
        words = [w for w in name_norm.split() if len(w) > 2]
        for w in words:
            if w not in app_channels:
                app_channels[w] = app_channels[name_norm].copy()

    # ── 2. Chaînes IPTV en base ───────────────────────────────────────
    try:
        from sqlalchemy import text as sa_text
        iptv_rows = db.execute(
            sa_text("SELECT id, name, logo, country FROM iptv_channels WHERE is_active = true LIMIT 50000")
        ).fetchall()
        for row in iptv_rows:
            name_norm = row[1].lower().strip() if row[1] else ""
            if name_norm:
                app_channels[name_norm] = {
                    "logo":        row[2] or "",
                    "country":     row[3] or "INT",
                    "stream_id":   str(row[0]),
                    "stream_type": "iptv",
                    "orig_name":   row[1],
                }
    except Exception as e:
        logger.warning(f"SmartEPG: impossible de charger les chaînes IPTV: {e}")

    logger.info(f"SmartEPG: {len(app_channels)} entrées dans le catalogue de l'appli")
    return app_channels


def _channel_in_app(channel_id: str, channel_name: str, app_channels: Dict[str, dict]) -> Optional[dict]:
    """
    Vérifie si une chaîne EPG correspond à une chaîne de l'appli.
    Retourne les infos de la chaîne appli si trouvée, None sinon.
    Stratégie de matching :
      1. Correspondance exacte sur channel_id ou channel_name
      2. Le channel_id contient le nom d'une chaîne appli
      3. Correspondance partielle (un mot significatif commun)
    """
    cid_l  = channel_id.lower().strip()
    cnam_l = channel_name.lower().strip()

    # 1. Exact match
    if cid_l in app_channels:
        return app_channels[cid_l]
    if cnam_l in app_channels:
        return app_channels[cnam_l]

    # 2. channel_id contient le nom
    for key, info in app_channels.items():
        if len(key) > 3 and key in cid_l:
            return info
        if len(key) > 3 and key in cnam_l:
            return info

    # 3. Matching par mots-clés (ex. "tf1" dans "tf1.fr")
    cid_words = set(w for w in re.split(r'[\s\.\-_]+', cid_l) if len(w) > 2)
    for key, info in app_channels.items():
        key_words = set(w for w in re.split(r'[\s\.\-_]+', key) if len(w) > 2)
        if cid_words & key_words:  # intersection non vide
            return info

    return None


def _parse_xmltv_datetime(raw: str) -> Optional[datetime]:
    """Parse un datetime XMLTV (ex: '20240315213000 +0100') en UTC naïf."""
    if not raw:
        return None
    try:
        from dateutil import parser as dp
        dt = dp.parse(raw)
        if dt.tzinfo:
            import calendar
            return datetime(*calendar.timegm(dt.utctimetuple())[:6])
        return dt
    except Exception:
        try:
            raw = raw.strip()
            dt = datetime(int(raw[0:4]), int(raw[4:6]), int(raw[6:8]),
                          int(raw[8:10]), int(raw[10:12]), int(raw[12:14]))
            if len(raw) > 14:
                tz_part = raw[15:].strip()
                sign = 1 if tz_part[0] == "+" else -1
                tz_h, tz_m = int(tz_part[1:3]), int(tz_part[3:5])
                dt -= timedelta(hours=sign*tz_h, minutes=sign*tz_m)
            return dt
        except Exception:
            return None


async def fetch_smart_epg(db, max_per_category: int = 50) -> int:
    """
    Télécharge un EPG global, filtre les chaînes de l'appli,
    catégorise et sélectionne les max_per_category meilleurs événements/catégorie/jour.
    Insère le résultat dans PostgreSQL.
    Retourne le nombre total d'événements insérés.
    """
    import xml.etree.ElementTree as ET
    import gzip as gz_mod
    import io

    now    = datetime.utcnow()
    cutoff = now + timedelta(hours=72)

    # Construire le catalogue des chaînes de l'appli
    app_channels = _build_app_channel_set(db)
    if not app_channels:
        logger.warning("SmartEPG: catalogue vide, abandon")
        return 0

    # Accumulateur : cat → liste d'events triés par priorité
    # Structure : { "sport": [(score, event_dict), ...], ... }
    accumulator: Dict[str, list] = {
        cat: [] for cat in ["sport", "cinema", "news", "kids", "documentary", "music", "other"]
    }

    total_parsed   = 0
    total_matched  = 0

    for epg_url in SMART_EPG_SOURCES:
        filename = epg_url.split("/")[-1]
        try:
            chunks     = []
            downloaded = 0
            start_time = datetime.utcnow()

            async with httpx.AsyncClient(
                timeout=httpx.Timeout(connect=15, read=120, write=30, pool=10),
                follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 LivewatchSmartEPG/3.0"}
            ) as client:
                async with client.stream("GET", epg_url) as r:
                    if r.status_code != 200:
                        logger.warning(f"SmartEPG: {epg_url} → HTTP {r.status_code}")
                        continue

                    total    = int(r.headers.get("content-length", 0))
                    total_mb = total / (1024 * 1024) if total else 0

                    # ── En-tête de téléchargement ─────────────────────────
                    print(f"\n{'═'*65}", flush=True)
                    print(f"  📥  SmartEPG — {filename}", flush=True)
                    if total_mb:
                        print(f"  📦  Taille : {total_mb:.1f} MB", flush=True)
                    else:
                        print(f"  📦  Taille : inconnue (streaming)", flush=True)
                    print(f"{'─'*65}", flush=True)

                    last_print_mb = -1.0

                    async for chunk in r.aiter_bytes(chunk_size=65536):  # 64 KB
                        chunks.append(chunk)
                        downloaded += len(chunk)
                        dl_mb     = downloaded / (1024 * 1024)

                        # Calcul vitesse
                        elapsed = max(0.1, (datetime.utcnow() - start_time).total_seconds())
                        speed   = dl_mb / elapsed   # MB/s

                        # Mise à jour toutes les 0.5 MB minimum
                        if dl_mb - last_print_mb >= 0.5:
                            last_print_mb = dl_mb
                            if total_mb:
                                pct      = min(100, int(dl_mb / total_mb * 100))
                                filled   = pct // 4                        # 25 blocs max
                                bar      = "█" * filled + "░" * (25 - filled)
                                eta_s    = ((total_mb - dl_mb) / speed) if speed > 0 else 0
                                eta_str  = f"{int(eta_s)}s" if eta_s < 60 else f"{int(eta_s/60)}m{int(eta_s%60)}s"
                                line     = (
                                    f"  [{bar}] {dl_mb:6.1f}/{total_mb:.1f} MB"
                                    f"  {pct:3d}%"
                                    f"  ⚡ {speed:.2f} MB/s"
                                    f"  ⏱ {eta_str}   "
                                )
                            else:
                                line = (
                                    f"  ⬇  {dl_mb:7.2f} MB téléchargés"
                                    f"  ⚡ {speed:.2f} MB/s   "
                                )
                            # \r pour écraser la ligne précédente dans le terminal
                            print(f"\r{line}", end="", flush=True)

            content  = b"".join(chunks)
            total_dl = len(content) / (1024 * 1024)
            elapsed  = max(0.1, (datetime.utcnow() - start_time).total_seconds())
            avg_spd  = total_dl / elapsed

            # Ligne finale propre
            print(f"\r{'─'*65}", flush=True)
            print(
                f"  ✅  Téléchargement terminé : {total_dl:.2f} MB"
                f"  |  durée : {elapsed:.1f}s"
                f"  |  moy. : {avg_spd:.2f} MB/s",
                flush=True
            )
            print(f"{'═'*65}\n", flush=True)

            # Décompression gzip
            if content[:2] == b'\x1f\x8b':
                print(f"   🔓 Décompression gzip...", flush=True)
                try:
                    content = gz_mod.decompress(content)
                    print(f"   ✅ Décompressé : {len(content)/(1024*1024):.2f} MB XML brut", flush=True)
                except Exception as e:
                    logger.warning(f"SmartEPG: erreur décompression {epg_url}: {e}")
                    continue

            print(f"   🔍 Parsing XML dans un thread séparé (non-bloquant)...", flush=True)

            # ── Parsing dans un thread — libère complètement asyncio ──────────
            def _parse_xml_in_thread(raw_content: bytes, app_ch: dict, src_url: str) -> dict:
                """
                Fonction synchrone exécutée dans un ThreadPoolExecutor.
                Parse le XML, filtre les chaînes appli, retourne l'accumulateur.
                """
                import xml.etree.ElementTree as _ET
                import io as _io

                _now    = datetime.utcnow()
                _cutoff = _now + timedelta(hours=72)

                _accumulator: Dict[str, list] = {
                    cat: [] for cat in ["sport", "cinema", "news", "kids", "documentary", "music", "other"]
                }
                _channels_map: Dict[str, dict] = {}
                _current_prog: Dict = {}
                _in_prog       = False
                _total_parsed  = 0
                _total_matched = 0
                _last_progress = 0

                try:
                    ctx = _ET.iterparse(_io.BytesIO(raw_content), events=("start", "end"))

                    for ev, elem in ctx:

                        # ── Chaîne ──────────────────────────────────────────
                        if ev == "end" and elem.tag == "channel":
                            cid     = elem.get("id", "")
                            nm_el   = elem.find("display-name")
                            ic_el   = elem.find("icon")
                            _channels_map[cid] = {
                                "name": (nm_el.text or cid)[:200] if nm_el is not None else cid,
                                "logo": (ic_el.get("src", "") if ic_el is not None else "")[:500],
                            }
                            elem.clear()

                        # ── Début programme ─────────────────────────────────
                        elif ev == "start" and elem.tag == "programme":
                            _in_prog = True
                            _current_prog = {
                                "cid":   elem.get("channel", ""),
                                "start": elem.get("start", ""),
                                "stop":  elem.get("stop", ""),
                            }

                        # ── Sous-éléments programme ──────────────────────────
                        elif _in_prog and ev == "end":
                            tag = elem.tag
                            if tag == "title"    and elem.text and "title" not in _current_prog:
                                _current_prog["title"] = elem.text
                            elif tag == "desc"   and elem.text and "desc" not in _current_prog:
                                _current_prog["desc"] = elem.text
                            elif tag == "category" and elem.text and "cat" not in _current_prog:
                                _current_prog["cat"] = elem.text
                            elif tag == "icon"   and elem.get("src") and "poster" not in _current_prog:
                                _current_prog["poster"] = elem.get("src", "")[:500]

                            # ── Fin programme ────────────────────────────────
                            if tag == "programme":
                                _in_prog = False
                                _total_parsed += 1

                                # Afficher progression tous les 200 000 programmes
                                if _total_parsed - _last_progress >= 200000:
                                    _last_progress = _total_parsed
                                    print(
                                        f"   ⚙️  Parsing : {_total_parsed:,} programmes lus"
                                        f" | {_total_matched} matchés",
                                        flush=True
                                    )

                                # Dates
                                sd = _parse_xmltv_datetime(_current_prog.get("start", ""))
                                ed = _parse_xmltv_datetime(_current_prog.get("stop", ""))
                                if not sd or not ed or sd < _now or sd > _cutoff:
                                    elem.clear()
                                    continue

                                # Matching chaîne appli
                                cid     = _current_prog["cid"]
                                ch_info = _channels_map.get(cid, {"name": cid, "logo": ""})
                                ch_name = ch_info["name"]
                                match   = _channel_in_app(cid, ch_name, app_ch)
                                if match is None:
                                    elem.clear()
                                    continue

                                title   = _current_prog.get("title", "Sans titre")[:300]
                                desc    = _current_prog.get("desc", "")[:500]
                                cat_raw = _current_prog.get("cat", "")
                                poster  = _current_prog.get("poster", "")
                                logo    = match["logo"] or ch_info["logo"]
                                country = match["country"] or _detect_country_from_channel(cid, ch_name)
                                category= _smart_xmltv_category(title, cat_raw)
                                score   = _is_priority_event(title, category)

                                _accumulator[category].append({
                                    "score":        score,
                                    "title":        title,
                                    "description":  desc,
                                    "channel_name": ch_name,
                                    "channel_logo": logo,
                                    "country":      country,
                                    "category":     category,
                                    "start_time":   sd,
                                    "end_time":     ed,
                                    "poster":       poster,
                                })
                                _total_matched += 1
                                elem.clear()

                except _ET.ParseError as parse_err:
                    print(f"   ⚠️  Erreur XML : {parse_err}", flush=True)

                return {
                    "accumulator":   _accumulator,
                    "total_parsed":  _total_parsed,
                    "total_matched": _total_matched,
                }

            # Lancer dans un executor thread — asyncio reste libre
            loop        = asyncio.get_event_loop()
            parse_result = await loop.run_in_executor(
                None, _parse_xml_in_thread, content, app_channels, epg_url
            )

            # Fusionner dans l'accumulateur global
            for cat, evs in parse_result["accumulator"].items():
                accumulator[cat].extend(evs)
            total_parsed  += parse_result["total_parsed"]
            total_matched += parse_result["total_matched"]

            print(
                f"   📊 {parse_result['total_parsed']:,} programmes analysés"
                f" | {parse_result['total_matched']} matchés avec les chaînes appli",
                flush=True
            )
            print(f"{'='*65}\n", flush=True)
            logger.info(
                f"SmartEPG: {epg_url.split('/')[-1]}"
                f" → {parse_result['total_matched']} matchés / {parse_result['total_parsed']:,} programmes"
            )

        except Exception as e:
            logger.warning(f"SmartEPG: erreur {epg_url}: {e}")
            continue

    # ── Sélectionner les top max_per_category par catégorie ──────────────
    inserted = 0
    for cat, events_list in accumulator.items():
        # Trier par score décroissant puis par heure de début
        events_list.sort(key=lambda x: (-x["score"], x["start_time"]))
        top_events = events_list[:max_per_category]

        for ev in top_events:
            try:
                db.add(TVEvent(
                    title        = ev["title"],
                    description  = ev["description"],
                    channel_name = ev["channel_name"],
                    channel_logo = ev["channel_logo"],
                    country      = ev["country"],
                    category     = ev["category"],
                    start_time   = ev["start_time"],
                    end_time     = ev["end_time"],
                    poster       = ev["poster"],
                ))
                inserted += 1
            except Exception:
                continue

    db.commit()
    logger.info(
        f"✅ SmartEPG terminé : {total_parsed} programmes analysés, "
        f"{total_matched} matchés avec les chaînes appli, "
        f"{inserted} événements sélectionnés et insérés (top {max_per_category}/catégorie)"
    )
    return inserted


async def seed_epg_events_async(db) -> None:
    """Sync EPG au démarrage avec SmartEPG."""
    # Nettoyer les événements expirés depuis plus de 10 min
    cutoff = datetime.utcnow() - timedelta(minutes=10)
    deleted = db.query(TVEvent).filter(TVEvent.end_time < cutoff).delete(synchronize_session=False)
    db.commit()
    if deleted:
        logger.info(f"🗑️ {deleted} événements EPG expirés supprimés au démarrage")

    # Sauter si déjà peuplé avec suffisamment d'événements variés
    existing = db.query(TVEvent).filter(TVEvent.end_time >= datetime.utcnow()).count()
    if existing > 50:
        logger.info(f"✅ SmartEPG: {existing} événements déjà en base, skip")
        return

    inserted = await fetch_smart_epg(db, max_per_category=50)
    if inserted == 0:
        logger.warning("⚠️ SmartEPG: aucun événement inséré. Vérifiez la connexion réseau.")
    else:
        logger.info(f"✅ SmartEPG initialisé : {inserted} événements insérés")


def seed_epg_events(db) -> None:
    """Wrapper synchrone pour le démarrage."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.create_task(seed_epg_events_async(db))
        else:
            loop.run_until_complete(seed_epg_events_async(db))
    except Exception as e:
        logger.error(f"SmartEPG seed error: {e}")


class EPGSyncService:
    """Service EPG : rappels + vérification périodique."""
    def __init__(self):
        self.is_running = False

    async def check_reminders(self):
        """Vérifie toutes les minutes les rappels à envoyer."""
        while True:
            try:
                await asyncio.sleep(60)
                db = SessionLocal()
                try:
                    window_start = datetime.utcnow() + timedelta(minutes=4)
                    window_end   = datetime.utcnow() + timedelta(minutes=6)
                    reminders = db.query(EventReminder).join(TVEvent).filter(
                        TVEvent.start_time >= window_start,
                        TVEvent.start_time <= window_end,
                        EventReminder.notified == False
                    ).all()
                    for r in reminders:
                        r.notified = True
                    if reminders:
                        db.commit()
                        logger.info(f"🔔 {len(reminders)} rappels marqués")
                finally:
                    db.close()
            except Exception as e:
                logger.error(f"Erreur check_reminders: {e}")


epg_service = EPGSyncService()

# Compatibilité : la route admin /api/admin/epg/seed appelle fetch_xmltv_events
# on la redirige vers le nouveau système
async def fetch_xmltv_events(db, max_events: int = 20000) -> int:
    """Alias de compatibilite vers fetch_smart_epg."""
    return await fetch_smart_epg(db, max_per_category=50)
# ==================== ROUTES EPG / ÉVÉNEMENTS ====================

@app.get("/events", response_class=HTMLResponse)
async def events_page(
    request: Request,
    category: str = None,
    country: str = None,
    db: Session = Depends(get_db)
):
    lang = get_language(request)
    now = datetime.utcnow()
    query = db.query(TVEvent).filter(TVEvent.end_time >= now, TVEvent.is_active == True)
    if category:
        query = query.filter(TVEvent.category == category)
    if country:
        query = query.filter(TVEvent.country == country)
    # Pas de limite — tous les événements disponibles
    events = query.order_by(TVEvent.start_time).all()

    # Grouper par catégorie
    events_by_cat = {}
    for ev in events:
        events_by_cat.setdefault(ev.category, []).append(ev)

    # Pays disponibles
    countries = db.query(TVEvent.country).filter(TVEvent.country != None).distinct().all()
    countries = sorted([c[0] for c in countries if c[0]])

    visitor_id = get_visitor_id(request)
    reminders = set()
    visitor = db.query(Visitor).filter(Visitor.visitor_id == visitor_id).first()
    if visitor:
        rems = db.query(EventReminder.event_id).filter(EventReminder.visitor_id == visitor.id).all()
        reminders = {r[0] for r in rems}

    return templates.TemplateResponse("events.html", {
        "request": request,
        "events": events,
        "events_by_cat": events_by_cat,
        "current_category": category,
        "current_country": country,
        "countries": countries,
        "reminders": reminders,
        "categories": CATEGORIES,
        "language": lang,
        "app_name": settings.APP_NAME,
        "visitor_id": visitor_id,
        "logo_path": settings.LOGO_PATH if os.path.exists(settings.LOGO_PATH) else None,
        "total_events": len(events),
    })

@app.get("/api/events/upcoming")
async def api_events_upcoming(db: Session = Depends(get_db)):
    """Événements à venir pour la sidebar — 8 max par catégorie, prochaines 48h"""
    now = datetime.utcnow()
    window_end = now + timedelta(hours=48)
    events = db.query(TVEvent).filter(
        TVEvent.start_time >= now,
        TVEvent.start_time <= window_end,
        TVEvent.is_active == True
    ).order_by(TVEvent.start_time).limit(500).all()

    result = {}
    for ev in events:
        cat = ev.category or "other"
        if cat not in result:
            result[cat] = []
        if len(result[cat]) < 8:
            result[cat].append({
                "id": str(ev.id),
                "title": ev.title,
                "channel": ev.channel_name or "",
                "country": ev.country or "",
                "category": cat,
                "start_time": ev.start_time.strftime("%H:%M"),
                "end_time": ev.end_time.strftime("%H:%M"),
                "logo": ev.channel_logo or "",
                "url": f"/events?category={cat}"
            })
    return JSONResponse(result)

@app.post("/api/events/{event_id}/remind")
async def add_reminder(event_id: str, request: Request, db: Session = Depends(get_db)):
    visitor_id = get_visitor_id(request)
    event = db.query(TVEvent).filter(TVEvent.id == event_id).first()
    if not event:
        return JSONResponse(status_code=404, content={"error": "Événement introuvable"})

    visitor = db.query(Visitor).filter(Visitor.visitor_id == visitor_id).first()
    if not visitor:
        visitor = Visitor(visitor_id=visitor_id, ip_address=request.client.host if request.client else "0.0.0.0")
        db.add(visitor)
        db.commit()
        db.refresh(visitor)

    existing = db.query(EventReminder).filter(
        EventReminder.event_id == event_id,
        EventReminder.visitor_id == visitor.id
    ).first()
    if existing:
        db.delete(existing)
        db.commit()
        return JSONResponse({"success": True, "action": "removed"})

    reminder = EventReminder(event_id=event_id, visitor_id=visitor.id)
    db.add(reminder)
    db.commit()
    return JSONResponse({"success": True, "action": "added"})

@app.get("/api/events/reminders/check")
async def check_my_reminders(request: Request, db: Session = Depends(get_db)):
    """Retourne les événements qui commencent dans moins de 6 minutes"""
    visitor_id = get_visitor_id(request)
    visitor = db.query(Visitor).filter(Visitor.visitor_id == visitor_id).first()
    if not visitor:
        return JSONResponse([])
    now = datetime.utcnow()
    window = now + timedelta(minutes=6)
    reminders = db.query(EventReminder).join(TVEvent).filter(
        EventReminder.visitor_id == visitor.id,
        EventReminder.notified == False,
        TVEvent.start_time >= now,
        TVEvent.start_time <= window
    ).all()
    result = []
    for r in reminders:
        ev = r.event
        result.append({
            "id": ev.id,
            "title": ev.title,
            "channel": ev.channel_name,
            "start_time": ev.start_time.strftime("%H:%M"),
            "minutes_left": max(0, int((ev.start_time - now).total_seconds() // 60))
        })
    return JSONResponse(result)

# ==================== ENREGISTREMENT FFMPEG ====================

@app.post("/api/record/start")
async def start_recording(
    request: Request,
    url: str = Form(...),
    filename: str = Form("stream"),
    duration: int = Form(3600),
    db: Session = Depends(get_db)
):
    """Lance l'enregistrement d'un flux HLS avec ffmpeg"""
    safe_name = re.sub(r'[^a-zA-Z0-9_\-]', '', filename)[:80]
    outdir = "static/recordings"
    os.makedirs(outdir, exist_ok=True)
    outfile = f"{outdir}/{safe_name}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.mp4"

    # Vérifier que ffmpeg est disponible
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-version",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL
        )
        await proc.wait()
    except FileNotFoundError:
        return JSONResponse(status_code=503, content={"success": False, "error": "ffmpeg non installé sur ce serveur"})

    # Lancer ffmpeg en arrière-plan
    asyncio.create_task(_run_ffmpeg(url, outfile, duration))
    return JSONResponse({"success": True, "filename": os.path.basename(outfile), "path": outfile})

async def _run_ffmpeg(url: str, outfile: str, duration: int):
    try:
        cmd = [
            "ffmpeg", "-y",
            "-user_agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "-i", url,
            "-t", str(min(duration, 7200)),  # max 2h
            "-c", "copy",
            "-movflags", "+faststart",
            outfile
        ]
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await proc.wait()
        logger.info(f"✅ Enregistrement terminé: {outfile}")
    except Exception as e:
        logger.error(f"❌ Erreur ffmpeg: {e}")

# ==================== TEMPLATES HTML ====================

@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, db: Session = Depends(get_db)):
    """Page des paramètres utilisateur."""
    visitor_id = get_visitor_id(request)
    # Charger les préférences sauvegardées en base si elles existent
    visitor = db.query(Visitor).filter(Visitor.visitor_id == visitor_id).first()
    saved_prefs = {}
    if visitor and visitor.theme:
        saved_prefs["theme"] = visitor.theme
    if visitor and visitor.preferred_language:
        saved_prefs["lang"] = visitor.preferred_language
    return templates.TemplateResponse("settings.html", {
        "request":     request,
        "app_name":    settings.APP_NAME,
        "categories":  CATEGORIES,
        "language":    get_language(request),
        "visitor_id":  visitor_id,
        "logo_path":   settings.LOGO_PATH if os.path.exists(settings.LOGO_PATH) else None,
        "saved_prefs": saved_prefs,
    })


@app.post("/api/settings/save")
async def api_save_settings(request: Request, db: Session = Depends(get_db)):
    """
    Sauvegarde les préférences utilisateur.
    Les préférences légères (theme, lang) sont persistées en base.
    Les autres sont stockées côté client (localStorage).
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"success": False, "error": "JSON invalide"})

    visitor_id = get_visitor_id(request)

    # Mettre à jour ou créer le visiteur avec ses préférences
    visitor = db.query(Visitor).filter(Visitor.visitor_id == visitor_id).first()
    if not visitor:
        client_ip = request.client.host if request.client else "0.0.0.0"
        visitor = Visitor(
            visitor_id  = visitor_id,
            ip_address  = client_ip,
            user_agent  = request.headers.get("user-agent", "")[:500],
        )
        db.add(visitor)

    # Persister theme et langue en base (les autres restent en localStorage)
    if "theme" in body:
        visitor.theme = body["theme"][:10]
    if "lang" in body:
        visitor.preferred_language = body["lang"][:10]

    db.commit()

    response = JSONResponse({"success": True, "message": "Paramètres sauvegardés avec succès"})
    # Poser/rafraîchir le cookie visitor_id
    response.set_cookie(
        key="visitor_id", value=visitor_id,
        max_age=settings.SESSION_MAX_AGE, httponly=True, samesite="lax"
    )
    return response


@app.post("/api/settings/reset")
async def api_reset_settings(request: Request, db: Session = Depends(get_db)):
    """
    Réinitialise les préférences utilisateur aux valeurs par défaut.
    """
    visitor_id = get_visitor_id(request)
    visitor = db.query(Visitor).filter(Visitor.visitor_id == visitor_id).first()
    if visitor:
        visitor.theme              = "auto"
        visitor.preferred_language = "fr"
        db.commit()
    return JSONResponse({"success": True, "message": "Paramètres réinitialisés aux valeurs par défaut"})


@app.get("/api/settings/load")
async def api_load_settings(request: Request, db: Session = Depends(get_db)):
    """
    Charge les préférences sauvegardées en base pour ce visiteur.
    """
    visitor_id = get_visitor_id(request)
    visitor = db.query(Visitor).filter(Visitor.visitor_id == visitor_id).first()
    if visitor:
        return JSONResponse({
            "success": True,
            "prefs": {
                "theme": visitor.theme or "auto",
                "lang":  visitor.preferred_language or "fr",
            }
        })
    return JSONResponse({"success": True, "prefs": {"theme": "auto", "lang": "fr"}})


def write_all_templates():
    """Écrit tous les templates HTML — supprime d'abord les anciens pour éviter le cache disque"""
    import shutil
    # Supprimer l'ancien dossier templates et le recréer proprement
    if os.path.exists("templates"):
        try:
            shutil.rmtree("templates")
        except Exception:
            pass
    os.makedirs("templates", exist_ok=True)

    # Template de base
    BASE_TEMPLATE = '''<!DOCTYPE html>
<html lang="{{ language }}">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{% block title %}{{ app_name }}{% endblock %}</title>
    <meta name="description" content="Plateforme de streaming ultime — TV, Sports, IPTV Monde (pays/régions/villes), YouTube Live, Radio & Lives communautaires">
    
    <!-- Tailwind CSS -->
    <script src="https://cdn.tailwindcss.com"></script>
    <script>
        tailwind.config = { darkMode: 'class' }
    </script>
    
    <!-- Font Awesome -->
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    
    <!-- Google Fonts -->
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
    
    <!-- Player Libraries -->
    <script src="https://cdn.jsdelivr.net/npm/hls.js@latest/dist/hls.min.js"></script>
    <script src="https://cdn.dashjs.org/latest/dash.all.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/mpegts.js/dist/mpegts.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/video.js@8.10.0/dist/video.min.js"></script>
    <link href="https://cdn.jsdelivr.net/npm/video.js@8.10.0/dist/video-js.min.css" rel="stylesheet">
    
    <style>
        * {
            font-family: 'Inter', sans-serif;
        }
        
        .transition-theme {
            transition: background-color 0.3s ease, color 0.3s ease;
        }
        
        .video-container {
            position: relative;
            padding-bottom: 56.25%;
            height: 0;
            overflow: hidden;
            background: #000;
            border-radius: 0.75rem;
        }
        
        .video-container video,
        .video-container iframe {
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            border-radius: 0.75rem;
        }

        /* Contrôle volume flottant sur le lecteur vidéo */
        .vol-overlay {
            position: absolute;
            bottom: 12px;
            right: 12px;
            z-index: 50;
            display: flex;
            align-items: center;
            gap: 8px;
            background: rgba(0,0,0,0.65);
            backdrop-filter: blur(6px);
            border-radius: 20px;
            padding: 6px 12px;
            opacity: 0;
            transition: opacity 0.25s;
        }
        .video-container:hover .vol-overlay { opacity: 1; }
        .vol-overlay input[type=range] {
            -webkit-appearance: none; appearance: none;
            width: 80px; height: 4px; border-radius: 2px;
            background: rgba(255,255,255,0.3); outline: none; cursor: pointer;
        }
        .vol-overlay input[type=range]::-webkit-slider-thumb {
            -webkit-appearance: none; appearance: none;
            width: 14px; height: 14px; border-radius: 50%;
            background: #e63946; cursor: pointer;
        }
        .vol-overlay button {
            background: none; border: none; color: #fff;
            font-size: 16px; cursor: pointer; padding: 0; line-height: 1;
        }
        
        .live-badge {
            animation: pulse 1.5s cubic-bezier(0.4, 0, 0.6, 1) infinite;
        }
        
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
        }
        
        .custom-scrollbar::-webkit-scrollbar {
            width: 6px;
        }
        
        .custom-scrollbar::-webkit-scrollbar-track {
            background: #f1f1f1;
            border-radius: 10px;
        }
        
        .custom-scrollbar::-webkit-scrollbar-thumb {
            background: #888;
            border-radius: 10px;
        }
        
        .custom-scrollbar::-webkit-scrollbar-thumb:hover {
            background: #555;
        }
        
        .dark .custom-scrollbar::-webkit-scrollbar-track {
            background: #2d3748;
        }
        
        .dark .custom-scrollbar::-webkit-scrollbar-thumb {
            background: #4a5568;
        }
        
        .stream-card {
            transition: transform 0.25s ease, box-shadow 0.25s ease;
        }
        
        .stream-card:hover {
            transform: translateY(-4px);
            box-shadow: 0 12px 28px rgba(0,0,0,0.15);
        }
        
        .pb-9-16 {
            padding-bottom: 56.25%;
        }
        
        .logo-container {
            max-width: 200px;
            max-height: 60px;
        }
        
        .logo-container img {
            width: auto;
            height: auto;
            max-width: 100%;
            max-height: 60px;
            object-fit: contain;
        }
    </style>
    
    <script>
        // Gestion du thème
        (function() {
            var saved = localStorage.getItem('theme');
            var dark = window.matchMedia('(prefers-color-scheme: dark)').matches;
            if (saved === 'dark' || (!saved && dark)) {
                document.documentElement.classList.add('dark');
            }
        })();
        
        function toggleTheme() {
            var html = document.documentElement;
            if (html.classList.contains('dark')) {
                html.classList.remove('dark');
                localStorage.setItem('theme', 'light');
            } else {
                html.classList.add('dark');
                localStorage.setItem('theme', 'dark');
            }
        }
    </script>
    <!-- Chart.js — chargé dans le head pour être disponible partout -->
    <script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>

    <!-- ═══ OPTIMISATION DONNÉES MOBILES ═══
         Détecte le type de connexion et adapte automatiquement :
         - Mobile 2G/3G lent  → images lazy, qualité réduite, animations OFF, banner avertissement
         - Mobile 4G/5G       → images lazy seulement (économie légère)
         - WiFi / câble       → comportement normal HD
         - Save-Data header   → mode économie maximum
    -->
    <script>
    (function() {
        var conn = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
        var saveData = conn && conn.saveData;
        var effectiveType = conn ? (conn.effectiveType || '') : '';
        var downlink = conn ? (conn.downlink || 10) : 10;

        // Classifier la connexion
        // 'slow'   : 2G, 3G lent, save-data activé ou < 1 Mbps
        // 'medium' : 4G moyen, 3G rapide, 1-5 Mbps
        // 'fast'   : WiFi, 5G, > 5 Mbps
        var netClass = 'fast';
        if (saveData || effectiveType === '2g' || effectiveType === 'slow-2g' || downlink < 0.5) {
            netClass = 'slow';
        } else if (effectiveType === '3g' || (downlink >= 0.5 && downlink < 2)) {
            netClass = 'medium';
        } else if (effectiveType === '4g' || downlink >= 2) {
            netClass = 'fast';
        }

        // Exposer globalement pour le player et les composants
        window._lwNetClass  = netClass;
        window._lwSaveData  = saveData;
        window._lwDownlink  = downlink;

        // Appliquer la classe sur <html> pour que le CSS puisse réagir
        document.documentElement.setAttribute('data-net', netClass);

        // En mode lent : désactiver les animations CSS lourdes
        if (netClass === 'slow') {
            var style = document.createElement('style');
            style.textContent = [
                '*, *::before, *::after { animation-duration: 0.01ms !important; transition-duration: 0.01ms !important; }',
                '.animate-ping, .animate-pulse, .live-badge { animation: none !important; }',
                '[data-net="slow"] img[loading!="eager"] { loading: lazy; }',
            ].join('');
            document.head.appendChild(style);
        }

        // Lazy-loading images pour mobile (slow + medium)
        if (netClass !== 'fast') {
            document.addEventListener('DOMContentLoaded', function() {
                document.querySelectorAll('img:not([loading])').forEach(function(img) {
                    img.setAttribute('loading', 'lazy');
                });
            });
        }

        // Afficher banner si connexion lente APRÈS chargement
        if (netClass === 'slow' || saveData) {
            document.addEventListener('DOMContentLoaded', function() {
                var banner = document.getElementById('data-saver-banner');
                if (banner) banner.style.display = 'flex';
            });
        }

        // Écouter les changements de connexion
        if (conn) {
            conn.addEventListener('change', function() {
                // Recharger la page si la connexion change significativement
                var newType = conn.effectiveType || '';
                if ((netClass === 'slow' && (newType === '4g' || conn.downlink > 2)) ||
                    (netClass === 'fast' && (newType === '2g' || newType === 'slow-2g'))) {
                    location.reload();
                }
            });
        }
    })();
    </script>
</head>
<body class="bg-gray-50 dark:bg-gray-900 text-gray-900 dark:text-gray-100 transition-theme">

    <!-- ═══ BANNER ÉCONOMIE DE DONNÉES ═══ (visible uniquement en connexion lente) -->
    <div id="data-saver-banner" style="display:none;position:fixed;bottom:0;left:0;right:0;z-index:9000;background:linear-gradient(to right,#1e3a5f,#1e40af);color:#fff;padding:8px 16px;font-size:12px;align-items:center;justify-content:space-between;gap:8px;border-top:2px solid #3b82f6;">
        <span>📶 Connexion lente détectée — Mode économie de données activé (qualité réduite, images allégées)</span>
        <button onclick="document.getElementById('data-saver-banner').style.display='none'" style="background:rgba(255,255,255,0.15);border:none;color:#fff;padding:4px 10px;border-radius:6px;cursor:pointer;font-size:11px;flex-shrink:0;">✕ Fermer</button>
    </div>

    <!-- ═══ ÉCRAN DE DÉMARRAGE — première visite uniquement ═══ -->
    <div id="splash-screen" style="display:none;position:fixed;inset:0;z-index:99999;background:#0f0f1a;flex-direction:column;align-items:center;justify-content:center;transition:opacity 0.6s ease;">
        <img src="/static/livewatch.png" alt="Livewatch"
             style="max-width:320px;width:80vw;height:auto;object-fit:contain;margin-bottom:2rem;animation:splashPulse 1.2s ease-in-out infinite alternate;"
             onerror="this.style.display='none';document.getElementById('splash-fallback').style.display='flex';">
        <div id="splash-fallback" style="display:none;flex-direction:column;align-items:center;gap:12px;margin-bottom:2rem;">
            <div style="width:80px;height:80px;background:linear-gradient(135deg,#dc2626,#f97316);border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:2.5rem;">▶</div>
            <span style="font-size:2rem;font-weight:900;color:#fff;letter-spacing:-1px;">Livewatch</span>
        </div>
        <div style="display:flex;gap:8px;align-items:center;">
            <div style="width:8px;height:8px;background:#dc2626;border-radius:50%;animation:splashDot 1s ease-in-out infinite;"></div>
            <div style="width:8px;height:8px;background:#f97316;border-radius:50%;animation:splashDot 1s ease-in-out 0.2s infinite;"></div>
            <div style="width:8px;height:8px;background:#dc2626;border-radius:50%;animation:splashDot 1s ease-in-out 0.4s infinite;"></div>
        </div>
        <p style="color:rgba(255,255,255,0.5);font-size:12px;margin-top:1.5rem;letter-spacing:2px;text-transform:uppercase;">Chargement…</p>
    </div>
    <style>
        @keyframes splashPulse { from { opacity:0.85; transform:scale(0.97); } to { opacity:1; transform:scale(1.03); } }
        @keyframes splashDot   { 0%,100%{ opacity:0.3; transform:scale(0.7); } 50%{ opacity:1; transform:scale(1.2); } }
    </style>
    <script>
        (function() {
            // ── Splash : uniquement à la PREMIÈRE entrée dans l'appli ──────
            // Règles :
            //  1. Jamais sur /admin, /api, /proxy, /static, /ws
            //  2. Jamais sur les pages /watch/* (watch external / IPTV / user)
            //  3. Uniquement si c'est la toute première page vue dans cette session
            //     (sessionStorage vide = nouvel onglet ou nouvelle fenêtre)

            var path = window.location.pathname;

            // Pages où le splash est INTERDIT (requêtes internes, admin, lecteur)
            var excluded = (
                path.startsWith('/admin') ||
                path.startsWith('/api/') ||
                path.startsWith('/proxy/') ||
                path.startsWith('/static/') ||
                path.startsWith('/ws') ||
                path.startsWith('/watch/') ||
                path.startsWith('/playlist/')
            );

            // Le splash n'est montré qu'une seule fois par session
            // (sessionStorage est vide si c'est le premier chargement de l'onglet)
            var alreadySeen = sessionStorage.getItem('_lw_entered');

            if (!excluded && !alreadySeen) {
                // Marquer immédiatement pour que toutes les navigations suivantes
                // dans cette session ne montrent plus le splash
                sessionStorage.setItem('_lw_entered', '1');

                var splash = document.getElementById('splash-screen');
                if (splash) {
                    splash.style.display = 'flex';

                    function hideSplash() {
                        if (splash.style.display === 'none') return;
                        splash.style.opacity = '0';
                        setTimeout(function() { splash.style.display = 'none'; }, 650);
                    }

                    // Masquer dès que la page est chargée
                    if (document.readyState === 'complete') {
                        setTimeout(hideSplash, 600);
                    } else {
                        window.addEventListener('load', function() {
                            setTimeout(hideSplash, 600);
                        });
                    }

                    // Sécurité absolue : disparaît après 3s dans tous les cas
                    setTimeout(hideSplash, 3000);
                }
            }
        })();
    </script>

    <!-- Navigation -->
    <nav class="bg-white dark:bg-gray-800 shadow-lg sticky top-0 z-50 border-b border-gray-200 dark:border-gray-700">
        <div class="container mx-auto px-4">
            <div class="flex justify-between items-center h-16">
                <!-- Logo -->
                <a href="/" class="flex items-center space-x-2">
                    <div class="w-10 h-10 bg-gradient-to-br from-red-600 to-red-800 rounded-full flex items-center justify-center shadow">
                        {% if logo_path %}
                        <img src="/static/logo" alt="{{ app_name }}" class="h-6 w-auto">
                        {% else %}
                        <i class="fas fa-play text-white"></i>
                        {% endif %}
                    </div>
                    <span class="text-2xl font-bold bg-gradient-to-r from-red-600 to-red-800 bg-clip-text text-transparent">{{ app_name }}</span>
                </a>

                <!-- Menu principal -->
                <div class="hidden md:flex items-center space-x-6">
                    <div class="relative group">
                        <button class="flex items-center text-gray-700 dark:text-gray-300 hover:text-red-600 dark:hover:text-red-500 transition font-medium">
                            <i class="fas fa-th-large mr-1 text-sm"></i> Catégories <i class="fas fa-chevron-down ml-1 text-xs"></i>
                        </button>
                        <div class="absolute top-full left-0 mt-2 w-64 bg-white dark:bg-gray-800 rounded-xl shadow-2xl hidden group-hover:block z-50 border border-gray-100 dark:border-gray-700 max-h-96 overflow-y-auto custom-scrollbar">
                            {% for cat in categories[:14] %}
                            <a href="/?category={{ cat.id }}" class="flex items-center justify-between px-4 py-2.5 hover:bg-gray-50 dark:hover:bg-gray-700 transition first:rounded-t-xl last:rounded-b-xl">
                                <span>{{ cat.icon }} {{ cat.name }}</span>
                                <span class="text-xs text-gray-400 bg-gray-100 dark:bg-gray-600 px-2 py-0.5 rounded-full">{{ cat.count }}</span>
                            </a>
                            {% endfor %}
                        </div>
                    </div>
                    <a href="/?category=sports" class="text-gray-700 dark:text-gray-300 hover:text-red-600 transition font-medium">⚽ Sports</a>
                    <a href="/?category=news" class="text-gray-700 dark:text-gray-300 hover:text-red-600 transition font-medium">📰 News</a>
                    <a href="/?category=radio" class="text-gray-700 dark:text-gray-300 hover:text-red-600 transition font-medium">📻 Radio</a>
                    <a href="/?category=iptv" class="text-gray-700 dark:text-gray-300 hover:text-red-600 transition font-medium">🌍 IPTV</a>
                    <a href="/?category=entertainment&filter=youtube" class="text-gray-700 dark:text-gray-300 hover:text-red-600 transition font-medium">▶️ YouTube</a>
                </div>

                <!-- Actions -->
                <div class="flex items-center space-x-2">
                    <a href="/search" class="text-gray-700 dark:text-gray-300 hover:text-red-600 transition p-2 rounded-full hover:bg-gray-100 dark:hover:bg-gray-700">
                        <i class="fas fa-search text-lg"></i>
                    </a>
                    <a href="/go-live" class="bg-gradient-to-r from-red-600 to-red-700 hover:from-red-700 hover:to-red-800 text-white px-4 py-2 rounded-full flex items-center space-x-2 transition shadow-md">
                        <i class="fas fa-circle text-xs animate-pulse"></i>
                        <span class="hidden sm:inline font-medium">Go Live</span>
                    </a>
                    <button onclick="toggleFavoritesPanel()" class="text-gray-700 dark:text-gray-300 hover:text-yellow-500 transition p-2 rounded-full hover:bg-gray-100 dark:hover:bg-gray-700">
                        <i class="fas fa-star text-lg"></i>
                    </button>
                    <button onclick="toggleTheme()" class="text-gray-700 dark:text-gray-300 hover:text-red-600 transition p-2 rounded-full hover:bg-gray-100 dark:hover:bg-gray-700">
                        <i class="fas fa-sun dark:hidden"></i>
                        <i class="fas fa-moon hidden dark:inline"></i>
                    </button>
                    <a href="/events" class="text-gray-700 dark:text-gray-300 hover:text-red-600 transition font-medium hidden md:inline-flex items-center gap-1 px-3 py-2 rounded-full hover:bg-gray-100 dark:hover:bg-gray-700">
                        📅 <span class="text-sm">Événements</span>
                    </a>
                    <a href="/settings" class="text-gray-700 dark:text-gray-300 hover:text-red-600 transition font-medium hidden md:inline-flex items-center gap-1 px-3 py-2 rounded-full hover:bg-gray-100 dark:hover:bg-gray-700">
                        ⚙️ <span class="text-sm">Paramètres</span>
                    </a>
                </div>
            </div>
        </div>
    </nav>

    <!-- Panneau des favoris -->
    <div id="favorites-panel" class="hidden fixed top-20 right-4 w-80 bg-white dark:bg-gray-800 rounded-xl shadow-2xl z-50 p-4 border border-gray-100 dark:border-gray-700">
        <div class="flex items-center justify-between mb-3">
            <h3 class="font-bold text-lg">⭐ Mes favoris</h3>
            <button onclick="toggleFavoritesPanel()" class="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200">
                <i class="fas fa-times"></i>
            </button>
        </div>
        <div id="favorites-list" class="space-y-2 max-h-96 overflow-y-auto custom-scrollbar"></div>
    </div>

    <!-- Toast notifications -->
    <!-- Toast notifications -->
    <div id="toast-container" class="fixed top-20 right-4 z-[100] space-y-2 pointer-events-none"></div>

    <!-- Notification EPG banner -->
    <div id="epg-notif-banner" style="display:none;position:fixed;top:64px;left:0;right:0;z-index:8000;background:#dc2626;color:#fff;font-size:13px;padding:8px 16px;text-align:center;box-shadow:0 2px 8px rgba(0,0,0,.3);">
        <span id="epg-notif-text"></span>
        <button onclick="document.getElementById('epg-notif-banner').style.display='none'" style="margin-left:16px;font-weight:700;background:none;border:none;color:#fff;cursor:pointer;font-size:16px;">✕</button>
    </div>

    <!-- Panneau EPG slide-over (caché par défaut) -->
    <div id="epg-overlay" onclick="closeEPGSidebar()"
         style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.5);z-index:9000;"></div>
    <aside id="epg-sidebar"
           style="position:fixed;top:0;right:0;height:100%;width:320px;background:#fff;z-index:9100;
                  transform:translateX(100%);transition:transform 0.3s cubic-bezier(0.4,0,0.2,1);
                  display:flex;flex-direction:column;box-shadow:-8px 0 32px rgba(0,0,0,0.2);"
           class="dark:bg-gray-800">
        <div style="background:linear-gradient(to right,#dc2626,#f97316);padding:12px 16px;display:flex;align-items:center;justify-content:space-between;flex-shrink:0;">
            <h2 style="color:#fff;font-weight:700;font-size:13px;margin:0;display:flex;align-items:center;gap:8px;">📅 PROGRAMMES À VENIR</h2>
            <div style="display:flex;align-items:center;gap:12px;">
                <a href="/events" style="color:rgba(255,255,255,0.8);font-size:12px;text-decoration:none;">Tout voir →</a>
                <button onclick="closeEPGSidebar()" style="color:rgba(255,255,255,0.8);background:none;border:none;cursor:pointer;font-size:20px;line-height:1;padding:0;">✕</button>
            </div>
        </div>
        <div id="epg-sidebar-content" style="flex:1;overflow-y:auto;">
            <div style="padding:16px;text-align:center;color:#9ca3af;font-size:12px;">Chargement...</div>
        </div>
        <div style="padding:12px;border-top:1px solid #e5e7eb;flex-shrink:0;" class="dark:border-gray-700">
            <a href="/events" style="display:block;text-align:center;font-size:12px;color:#dc2626;font-weight:600;text-decoration:none;padding:8px;border-radius:8px;">
                Voir tous les programmes →
            </a>
        </div>
    </aside>

    <!-- Contenu principal (pleine largeur) -->
    <main class="container mx-auto px-4 py-6">
        {% block content %}{% endblock %}
    </main>

    <!-- Footer -->
    <footer class="bg-white dark:bg-gray-800 border-t border-gray-200 dark:border-gray-700 mt-16">
        <div class="container mx-auto px-4 py-10">
            <div class="grid grid-cols-1 md:grid-cols-4 gap-8">
                <div>
                    <div class="flex items-center space-x-2 mb-4">
                        <div class="w-9 h-9 bg-gradient-to-br from-red-600 to-red-800 rounded-full flex items-center justify-center">
                            {% if logo_path %}
                            <img src="/static/logo" alt="{{ app_name }}" class="h-5 w-auto">
                            {% else %}
                            <i class="fas fa-play text-white text-sm"></i>
                            {% endif %}
                        </div>
                        <span class="text-xl font-bold">{{ app_name }}</span>
                    </div>
                    <p class="text-gray-500 dark:text-gray-400 text-sm leading-relaxed">
                        Plateforme de streaming ultime — TV, Sports, IPTV Monde (pays/régions/villes), YouTube Live, Radio & Lives communautaires.
                    </p>
                </div>
                <div>
                    <h3 class="font-semibold mb-4 text-gray-800 dark:text-gray-200">Catégories</h3>
                    <ul class="space-y-2 text-sm">
                        {% for cat in categories[:6] %}
                        <li><a href="/?category={{ cat.id }}" class="text-gray-500 dark:text-gray-400 hover:text-red-600 transition">{{ cat.icon }} {{ cat.name }}</a></li>
                        {% endfor %}
                    </ul>
                </div>
                <div>
                    <h3 class="font-semibold mb-4 text-gray-800 dark:text-gray-200">Navigation</h3>
                    <ul class="space-y-2 text-sm">
                        <li><a href="/go-live" class="text-gray-500 dark:text-gray-400 hover:text-red-600 transition">🎥 Démarrer un live</a></li>
                        <li><a href="/search" class="text-gray-500 dark:text-gray-400 hover:text-red-600 transition">🔍 Rechercher</a></li>
                        <li><a href="/admin" class="text-gray-500 dark:text-gray-400 hover:text-red-600 transition">⚙️ Administration</a></li>
                    </ul>
                </div>
                <div>
                    <h3 class="font-semibold mb-4 text-gray-800 dark:text-gray-200">Sources</h3>
                    <p class="text-sm text-gray-500 dark:text-gray-400">
                        IPTV via <a href="https://github.com/iptv-org/iptv" target="_blank" class="text-red-500 hover:underline">iptv-org</a>.<br>
                        YouTube via yt-dlp.<br>
                        Plus de 10 000 chaînes disponibles.
                    </p>
                    <p class="text-xs text-gray-400 mt-2">
                        Dévèloppé par : BEN CORPORATION
                    </p>
                </div>
            </div>
            <!-- Bannière soutien -->
            <div class="border-t border-gray-200 dark:border-gray-700 mt-8 pt-6">
                <div class="bg-gradient-to-r from-red-50 to-orange-50 dark:from-red-900/20 dark:to-orange-900/20 rounded-2xl p-5 text-center mb-6 border border-red-100 dark:border-red-800/30">
                    <p class="text-base font-semibold text-gray-700 dark:text-gray-200 mb-1">
                         Ce site est <span class="text-red-600 font-bold">gratuit</span>.
                    </p>
                    <p class="text-sm text-gray-500 dark:text-gray-400 mb-4">
                        Si vous aimez le projet, vous pouvez soutenir le développement 
                    </p>
                    <div class="flex flex-col sm:flex-row gap-4 items-center justify-center flex-wrap">
                        <!-- Airtel Money -->
                        <div class="flex flex-col items-center bg-white dark:bg-gray-700 rounded-xl px-5 py-3 border border-red-200 dark:border-red-800 shadow-sm">
                            <span class="text-xs font-bold text-gray-500 dark:text-gray-400 mb-1">📱 Airtel Money (Congo)</span>
                            <span class="text-lg font-black text-red-600 tracking-wide">+243996855061</span>
                        </div>
                        <!-- Ethereum / USDT ERC20 -->
                        <div class="flex flex-col items-center bg-white dark:bg-gray-700 rounded-xl px-5 py-3 border border-purple-200 dark:border-purple-800 shadow-sm">
                            <span class="text-xs font-bold text-gray-500 dark:text-gray-400 mb-1">💎 Ethereum / USDT (ERC20)</span>
                            <span class="text-xs font-mono font-bold text-purple-600 dark:text-purple-400 break-all text-center select-all">0x30B46539266EC13A2D3720bf0289d927647fdB3E</span>
                        </div>
                    </div>
                </div>
                <div class="text-center text-sm text-gray-400">
                    &copy; 2026 {{ app_name }} v1.0 — Tous droits réservés.
                </div>
            </div>
        </div>
    </footer>

    <!-- Scripts communs -->
    <script>
        function showNotification(message, type = 'info') {
            const colors = {
                error: 'bg-red-600',
                success: 'bg-green-600',
                warning: 'bg-yellow-500',
                info: 'bg-blue-600'
            };
            const icons = {
                error: 'fa-times-circle',
                success: 'fa-check-circle',
                warning: 'fa-exclamation-triangle',
                info: 'fa-info-circle'
            };
            
            const toast = document.createElement('div');
            toast.className = `flex items-center space-x-2 px-4 py-3 rounded-lg shadow-xl text-white pointer-events-auto transition-all duration-300 ${colors[type] || colors.info}`;
            toast.innerHTML = `<i class="fas ${icons[type] || icons.info}"></i><span>${message}</span>`;
            
            const container = document.getElementById('toast-container');
            container.appendChild(toast);
            
            setTimeout(() => {
                toast.style.opacity = '0';
                setTimeout(() => toast.remove(), 300);
            }, 3000);
        }

        async function toggleFavoritesPanel() {
            const panel = document.getElementById('favorites-panel');
            panel.classList.toggle('hidden');
            
            if (!panel.classList.contains('hidden')) {
                const list = document.getElementById('favorites-list');
                list.innerHTML = '<p class="text-gray-400 text-sm text-center py-4">Chargement...</p>';
                try {
                    const response = await fetch('/api/favorites', { credentials: 'include' });
                    const favorites = await response.json();
                    
                    if (favorites.length === 0) {
                        list.innerHTML = '<p class="text-gray-400 text-sm text-center py-4">Aucun favori enregistré<br><span class="text-xs">Cliquez ⭐ sur une chaîne pour l\'ajouter</span></p>';
                    } else {
                        list.innerHTML = favorites.map(function(f) {
                            return '<a href="' + f.url + '" class="flex items-center space-x-3 p-2 hover:bg-gray-50 dark:hover:bg-gray-700 rounded-lg transition">'
                                + '<div class="w-10 h-10 bg-gray-200 dark:bg-gray-600 rounded-lg overflow-hidden flex-shrink-0 flex items-center justify-center">'
                                + (f.logo ? '<img src="' + f.logo + '" class="w-full h-full object-contain p-1" loading="lazy" onerror="this.style.display=\'none\'">' : '<i class="fas fa-tv text-gray-400"></i>')
                                + '</div>'
                                + '<div class="flex-1 min-w-0">'
                                + '<div class="font-medium text-sm truncate">' + f.title + '</div>'
                                + '<div class="text-xs text-gray-400">' + (f.type === 'user' && f.is_live ? '🔴 EN DIRECT' : (f.category || f.type)) + '</div>'
                                + '</div></a>';
                        }).join('');
                    }
                } catch(e) {
                    list.innerHTML = '<p class="text-red-400 text-sm text-center py-4">Erreur de chargement</p>';
                }
            }
        }

        async function addToFavorites(streamId, type, event) {
            if (event) { event.preventDefault(); event.stopPropagation(); }
            try {
                const formData = new FormData();
                formData.append('stream_id', streamId);
                formData.append('stream_type', type);
                const response = await fetch('/api/favorites/add', {
                    method: 'POST',
                    body: formData,
                    credentials: 'include'
                });
                if (response.ok) {
                    showNotification('Ajouté aux favoris ⭐', 'success');
                    // Feedback visuel sur le bouton cliqué
                    if (event && event.currentTarget) {
                        var btn = event.currentTarget;
                        var icon = btn.querySelector('i');
                        if (icon) { icon.classList.remove('far'); icon.classList.add('fas'); }
                    }
                } else {
                    var data = await response.json().catch(function(){ return {}; });
                    showNotification(data.error || 'Erreur favori (' + response.status + ')', 'error');
                }
            } catch(e) {
                showNotification('Erreur réseau: ' + e.message, 'error');
            }
        }

        // ===== SIDEBAR EPG SLIDE-OVER =====
        const EPG_CAT_ICONS  = {sport:'⚽',cinema:'🎬',news:'📰',kids:'🧒',documentary:'🎥',music:'🎵',other:'📺'};
        const EPG_CAT_LABELS = {sport:'SPORT',cinema:'CINÉMA',news:'NEWS',kids:'KIDS',documentary:'DOCUMENTAIRES',music:'MUSIQUE',other:'AUTRES'};
        var _epgLoaded = false;
        var _epgSidebarOpen = false;

        function openEPGSidebar() {
            var sidebar = document.getElementById('epg-sidebar');
            var overlay = document.getElementById('epg-overlay');
            if (sidebar) sidebar.style.transform = 'translateX(0)';
            if (overlay) overlay.style.display = 'block';
            document.body.style.overflow = 'hidden';
            _epgSidebarOpen = true;
            // Toujours recharger pour que chaque visiteur voie les données fraîches
            loadEPGSidebar();
        }

        function closeEPGSidebar() {
            var sidebar = document.getElementById('epg-sidebar');
            var overlay = document.getElementById('epg-overlay');
            if (sidebar) sidebar.style.transform = 'translateX(100%)';
            if (overlay) overlay.style.display = 'none';
            document.body.style.overflow = '';
            _epgSidebarOpen = false;
        }

        async function loadEPGSidebar() {
            var container = document.getElementById('epg-sidebar-content');
            if (!container) return;
            container.innerHTML = '<div style="padding:24px 16px;text-align:center;color:#9ca3af;font-size:12px;"><div style="font-size:2rem;margin-bottom:8px;">⏳</div>Chargement des programmes...</div>';
            try {
                const r = await fetch('/api/events/upcoming', { credentials: 'include' });
                if (!r.ok) {
                    container.innerHTML = '<div style="padding:24px 16px;text-align:center;color:#9ca3af;font-size:12px;"><div style="font-size:2rem;margin-bottom:8px;">⚠️</div>Impossible de charger les programmes.<br><a href="/events" style="color:#dc2626;">Voir la page EPG →</a></div>';
                    return;
                }
                const data = await r.json();
                const cats = ['sport','cinema','news','kids','documentary','music','other'];
                let html = '';
                let hasAny = false;
                cats.forEach(function(cat) {
                    const evs = data[cat];
                    if (!evs || evs.length === 0) return;
                    hasAny = true;
                    const icon = EPG_CAT_ICONS[cat] || '📺';
                    const label = EPG_CAT_LABELS[cat] || cat;
                    html += '<div style="padding:8px 12px;background:linear-gradient(to right,#fef2f2,#fff7ed);border-bottom:1px solid #fecaca;">'
                          + '<span style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:#dc2626;">'
                          + icon + ' ' + label + '</span></div>';
                    evs.forEach(function(ev) {
                        var searchUrl = '/search?q=' + encodeURIComponent(ev.channel || ev.title);
                        html += '<div style="display:flex;align-items:flex-start;gap:10px;padding:10px 12px;border-bottom:1px solid #f3f4f6;background:#fff;" onmouseover="this.style.background=\'#fef2f2\'" onmouseout="this.style.background=\'#fff\'">'
                              + '<div style="width:36px;height:36px;border-radius:8px;overflow:hidden;flex-shrink:0;background:#f3f4f6;display:flex;align-items:center;justify-content:center;">'
                              + (ev.logo ? '<img src="' + ev.logo + '" style="width:100%;height:100%;object-fit:contain;padding:3px;" onerror="this.style.display=\'none\'"><span style="font-size:18px;display:none">' + icon + '</span>' : '<span style="font-size:18px;">' + icon + '</span>')
                              + '</div>'
                              + '<div style="flex:1;min-width:0;">'
                              + '<div style="font-size:12px;font-weight:600;color:#1f2937;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;" title="' + (ev.title||'') + '">' + (ev.title||'') + '</div>'
                              + '<div style="font-size:11px;color:#9ca3af;margin-top:2px;display:flex;align-items:center;gap:4px;flex-wrap:wrap;">'
                              + '<span style="background:#fef2f2;color:#dc2626;padding:1px 5px;border-radius:4px;font-weight:600;">🕐 ' + (ev.start_time||'') + '</span>'
                              + '<span style="max-width:120px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">' + (ev.channel||'') + '</span>'
                              + '</div>'
                              + '<div style="margin-top:6px;display:flex;gap:4px;">'
                              + '<a href="' + searchUrl + '" style="font-size:10px;background:#dc2626;color:#fff;padding:2px 8px;border-radius:4px;text-decoration:none;font-weight:600;">▶ Voir la chaîne</a>'
                              + '<a href="/events" style="font-size:10px;background:#f3f4f6;color:#374151;padding:2px 8px;border-radius:4px;text-decoration:none;">📅 Prog.</a>'
                              + '</div>'
                              + '</div></div>';
                    });
                });
                if (!hasAny) {
                    html = '<div style="padding:32px 16px;text-align:center;color:#9ca3af;">'
                         + '<div style="font-size:2rem;margin-bottom:8px;">📅</div>'
                         + '<p style="font-size:13px;font-weight:600;color:#374151;margin:0 0 4px;">Aucun programme disponible</p>'
                         + '<p style="font-size:11px;margin:0 0 12px;">Les données EPG se synchronisent au démarrage.<br>Si vide, allez dans Admin → EPG → Reset.</p>'
                         + '<a href="/events" style="font-size:12px;background:#dc2626;color:#fff;padding:6px 16px;border-radius:8px;text-decoration:none;font-weight:600;">Voir la page programmes →</a></div>';
                }
                container.innerHTML = html;
            } catch(e) {
                container.innerHTML = '<div style="padding:16px;text-align:center;color:#9ca3af;font-size:12px;">Erreur: ' + e.message + '<br><a href="/events" style="color:#dc2626;">Voir /events →</a></div>';
            }
        }

        // ===== RAPPELS EPG =====
        async function checkEPGReminders() {
            try {
                const r = await fetch('/api/events/reminders/check');
                if (!r.ok) return;
                const items = await r.json();
                if (items.length > 0) {
                    const ev = items[0];
                    const banner = document.getElementById('epg-notif-banner');
                    const txt = document.getElementById('epg-notif-text');
                    if (banner && txt) {
                        txt.textContent = '🔔 "' + ev.title + '" commence dans ' + ev.minutes_left + ' min sur ' + ev.channel + ' (' + ev.start_time + ')';
                        banner.style.display = 'block';
                        setTimeout(function() { banner.style.display = 'none'; }, 15000);
                    }
                    // Notification navigateur
                    if ('Notification' in window && Notification.permission === 'granted') {
                        items.forEach(e => {
                            new Notification(`📺 ${e.title}`, {
                                body: `Commence dans ${e.minutes_left} min sur ${e.channel} à ${e.start_time}`,
                                icon: '/static/favicon.ico'
                            });
                        });
                    }
                }
            } catch(e) { /* silencieux */ }
        }

        async function requestNotifPermission() {
            if ('Notification' in window && Notification.permission === 'default') {
                await Notification.requestPermission();
            }
        }

        document.addEventListener('DOMContentLoaded', function() {
            // Ne charge la sidebar EPG que quand l'utilisateur l'ouvre
            requestNotifPermission();
            // Vérifier les rappels toutes les minutes
            setInterval(checkEPGReminders, 60000);
        });
    </script>

    {% block scripts %}{% endblock %}
</body>
</html>'''

    # Template d'accueil
    INDEX_TEMPLATE = '''{% extends "base.html" %}

{% block content %}
<div class="space-y-10">
    <!-- Hero section -->
    <section class="relative bg-gradient-to-br from-red-700 via-red-600 to-orange-500 text-white rounded-2xl p-8 overflow-hidden">
        <div class="absolute inset-0 opacity-10">
            <div class="absolute top-4 right-8 text-9xl">📺</div>
            <div class="absolute bottom-4 right-40 text-6xl">🎬</div>
        </div>
        <div class="relative max-w-2xl">
            <h1 class="text-4xl font-bold mb-3">Bienvenue sur <span class="font-black">{{ app_name }}</span></h1>
            <p class="text-lg mb-6 text-red-100">Regardez des milliers de chaînes TV, IPTV Monde (pays/régions/villes), YouTube Live, Radio & lives communautaires.</p>
            <div class="flex flex-wrap gap-3">
                <a href="#live" class="bg-white text-red-600 px-5 py-2.5 rounded-full font-semibold hover:bg-red-50 transition shadow">
                    <i class="fas fa-circle text-red-500 mr-2 text-xs animate-pulse"></i>En direct
                </a>
                <a href="#iptv" class="bg-red-800/60 text-white px-5 py-2.5 rounded-full font-semibold hover:bg-red-800 transition">
                    <i class="fas fa-globe mr-2"></i>IPTV Monde
                </a>
                <a href="#external-streams" onclick="setTimeout(function(){var b=document.querySelector('[onclick*=youtube]');if(b)b.click();},300);" class="bg-white/20 text-white px-5 py-2.5 rounded-full font-semibold hover:bg-white/30 transition border border-white/30">
                    ▶️ YouTube
                </a>
                <a href="#external-streams" onclick="setTimeout(function(){var b=document.querySelector('[onclick*=audio]');if(b)b.click();},300);" class="bg-white/20 text-white px-5 py-2.5 rounded-full font-semibold hover:bg-white/30 transition border border-white/30">
                    📻 Radio
                </a>
                <a href="/events" class="bg-white/20 text-white px-5 py-2.5 rounded-full font-semibold hover:bg-white/30 transition border border-white/30">
                    📅 Événements
                </a>
                <a href="/go-live" class="bg-white/20 text-white px-5 py-2.5 rounded-full font-semibold hover:bg-white/30 transition border border-white/30">
                    <i class="fas fa-video mr-2"></i>Go Live
                </a>
            </div>
        </div>
    </section>

    <!-- Playlists IPTV par pays -->
    {% if pl_countries and not selected_playlist %}
    <section id="iptv">
        <div class="flex items-center justify-between mb-4">
            <h2 class="text-2xl font-bold flex items-center gap-2">
                <span class="w-8 h-8 bg-indigo-100 dark:bg-indigo-900/30 rounded-lg flex items-center justify-center text-lg">🌍</span>
                IPTV par pays
            </h2>
            <span class="text-sm text-gray-400">{{ pl_countries|length }} pays</span>
        </div>
        <div class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 xl:grid-cols-8 gap-3">
            {% for playlist in pl_countries %}
            <a href="/?playlist={{ playlist.name }}" class="stream-card group relative rounded-xl overflow-hidden hover:shadow-xl border border-gray-200 dark:border-gray-700 aspect-video flex flex-col justify-end"
               style="background: url('https://flagcdn.com/w320/{{ playlist.country|lower }}.png') center center / cover no-repeat; min-height: 80px;">
                <!-- Overlay dégradé -->
                <div class="absolute inset-0 bg-gradient-to-t from-black/80 via-black/30 to-transparent group-hover:from-black/90 transition-all duration-200"></div>
                <!-- Texte en bas -->
                <div class="relative z-10 p-2 text-white">
                    <h3 class="font-bold text-xs leading-tight drop-shadow">{{ playlist.display_name.split(' ',1)[1] if ' ' in playlist.display_name else playlist.display_name }}</h3>
                    <p class="text-xs text-white/70">{{ playlist.channel_count }} chaînes</p>
                </div>
            </a>
            {% endfor %}
        </div>
    </section>
    {% endif %}

    <!-- Subdivisions (régions/provinces) -->
    {% if pl_subdivisions and not selected_playlist %}
    <section>
        <h2 class="text-xl font-bold mb-4 flex items-center gap-2">
            <span class="w-7 h-7 bg-purple-100 dark:bg-purple-900/30 rounded-lg flex items-center justify-center text-sm">🗺️</span>
            Régions & Provinces
        </h2>
        <div class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 xl:grid-cols-8 gap-3">
            {% for playlist in pl_subdivisions %}
            <a href="/?playlist={{ playlist.name }}" class="stream-card bg-white dark:bg-gray-800 rounded-xl overflow-hidden hover:shadow-xl p-3 text-center border border-gray-100 dark:border-gray-700">
                <div class="text-2xl mb-1">🗺️</div>
                <h3 class="font-semibold text-xs leading-tight">{{ playlist.display_name }}</h3>
                <p class="text-xs text-gray-400 mt-1">{{ playlist.channel_count }} chaînes</p>
            </a>
            {% endfor %}
        </div>
    </section>
    {% endif %}

    <!-- Villes -->
    {% if pl_cities and not selected_playlist %}
    <section>
        <h2 class="text-xl font-bold mb-4 flex items-center gap-2">
            <span class="w-7 h-7 bg-cyan-100 dark:bg-cyan-900/30 rounded-lg flex items-center justify-center text-sm">🏙️</span>
            Villes
        </h2>
        <div class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 xl:grid-cols-8 gap-3">
            {% for playlist in pl_cities %}
            <a href="/?playlist={{ playlist.name }}" class="stream-card bg-white dark:bg-gray-800 rounded-xl overflow-hidden hover:shadow-xl p-3 text-center border border-gray-100 dark:border-gray-700">
                <div class="text-2xl mb-1">🏙️</div>
                <h3 class="font-semibold text-xs leading-tight">{{ playlist.display_name }}</h3>
                <p class="text-xs text-gray-400 mt-1">{{ playlist.channel_count }} chaînes</p>
            </a>
            {% endfor %}
        </div>
    </section>
    {% endif %}

    <!-- Catégories IPTV -->
    {% if pl_categories and not selected_playlist %}
    <section>
        <h2 class="text-xl font-bold mb-4 flex items-center gap-2">
            <span class="w-7 h-7 bg-amber-100 dark:bg-amber-900/30 rounded-lg flex items-center justify-center text-sm">📂</span>
            Catégories thématiques
        </h2>
        <div class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 xl:grid-cols-8 gap-3">
            {% for playlist in pl_categories %}
            {% set cat_bg = {
                'sport': 'from-blue-600 to-blue-900',
                'news': 'from-slate-600 to-slate-900',
                'documentary': 'from-amber-600 to-amber-900',
                'music': 'from-pink-500 to-rose-800',
                'kids': 'from-yellow-400 to-orange-500',
                'movies': 'from-purple-600 to-purple-900',
                'science': 'from-teal-600 to-teal-900',
                'travel': 'from-emerald-500 to-green-800',
                'religion': 'from-green-600 to-teal-800',
                'business': 'from-indigo-600 to-indigo-900',
                'cooking': 'from-orange-500 to-red-700',
                'auto': 'from-zinc-600 to-zinc-900',
                'gaming': 'from-fuchsia-600 to-purple-900',
                'education': 'from-sky-500 to-blue-800',
                'nature': 'from-lime-600 to-green-800',
                'fashion': 'from-rose-400 to-pink-700',
                'health': 'from-red-500 to-rose-700',
                'history': 'from-amber-700 to-yellow-900',
                'tech': 'from-cyan-600 to-blue-800',
                'weather': 'from-sky-400 to-blue-600',
                'general': 'from-gray-600 to-gray-900',
            } %}
            {% set matched_bg = namespace(val='from-gray-600 to-gray-900') %}
            {% for key, val in cat_bg.items() %}
                {% if key in playlist.name %}{% set matched_bg.val = val %}{% endif %}
            {% endfor %}
            {% set cat_icons = {
                'sport':'⚽','news':'📰','doc':'🎥','music':'🎵','kid':'🧸','movie':'🎬',
                'sci':'🔬','travel':'✈️','relig':'🕊️','busi':'💼','cook':'🍳','auto':'🚗',
                'gaming':'🎮','edu':'📚','nature':'🌲','fashion':'👗','health':'💪',
                'history':'📜','tech':'💻','weather':'🌤️','general':'📺'
            } %}
            {% set icon = namespace(val='📂') %}
            {% for key, val in cat_icons.items() %}
                {% if key in playlist.name %}{% set icon.val = val %}{% endif %}
            {% endfor %}
            <a href="/?playlist={{ playlist.name }}" class="stream-card group rounded-xl overflow-hidden hover:shadow-xl border border-gray-100 dark:border-gray-700 relative">
                <div class="bg-gradient-to-br {{ matched_bg.val }} h-24 flex flex-col items-center justify-center relative">
                    <span class="text-3xl mb-1 drop-shadow-lg group-hover:scale-110 transition-transform duration-200">{{ icon.val }}</span>
                    <div class="absolute inset-0 bg-black/10 group-hover:bg-black/0 transition-all duration-200"></div>
                </div>
                <div class="p-2 bg-white dark:bg-gray-800">
                    <h3 class="font-semibold text-xs leading-tight text-center truncate">{{ playlist.display_name }}</h3>
                    <p class="text-xs text-gray-400 text-center mt-0.5">{{ playlist.channel_count or 0 }} chaînes</p>
                </div>
            </a>
            {% endfor %}
        </div>
    </section>
    {% endif %}

    <!-- Chaînes IPTV d'une playlist sélectionnée -->
    {% if selected_playlist and iptv_channels %}
    <section>
        <div class="flex items-center justify-between mb-5">
            <h2 class="text-2xl font-bold flex items-center gap-2">
                <a href="/" class="text-gray-400 hover:text-red-600 transition">
                    <i class="fas fa-arrow-left"></i>
                </a>
                {{ selected_playlist.display_name }}
                <span class="text-sm text-gray-400 font-normal bg-gray-100 dark:bg-gray-700 px-2 py-0.5 rounded-full">{{ iptv_channels|length }}</span>
            </h2>
            {% if selected_playlist.last_sync %}
            <span class="text-xs text-gray-400 bg-gray-100 dark:bg-gray-700 px-2 py-1 rounded-full">
                <i class="fas fa-sync-alt mr-1"></i>{{ selected_playlist.last_sync.strftime('%d/%m/%Y') }}
            </span>
            {% endif %}
        </div>

        <!-- Filtres catégories des chaînes -->
        {% set ch_cats = [] %}
        {% for ch in iptv_channels %}
            {% if ch.category and ch.category not in ch_cats %}
                {% set _ = ch_cats.append(ch.category) %}
            {% endif %}
        {% endfor %}
        {% if ch_cats|length > 1 %}
        <div class="flex flex-wrap gap-2 mb-4" id="cat-filters">
            <button onclick="filterChannels('all', this)"
                class="px-3 py-1.5 rounded-full text-sm font-medium bg-red-600 text-white transition cat-filter-btn">
                🌐 Toutes
            </button>
            {% for cat in ch_cats | sort %}
            <button onclick="filterChannels('{{ cat }}', this)"
                class="cat-filter-btn px-3 py-1.5 rounded-full text-sm font-medium bg-gray-200 dark:bg-gray-700 text-gray-700 dark:text-gray-300 hover:bg-red-600 hover:text-white transition capitalize">
                {{ cat | replace('_', ' ') | title }}
            </button>
            {% endfor %}
        </div>
        {% endif %}

        <div class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-3" id="channels-grid">
            {% for channel in iptv_channels %}
            <a href="/watch/iptv/{{ channel.id }}" class="stream-card bg-white dark:bg-gray-800 rounded-xl overflow-hidden hover:shadow-xl border border-gray-100 dark:border-gray-700 channel-card" data-category="{{ channel.category }}">
                <div class="relative h-20 bg-gray-100 dark:bg-gray-700 flex items-center justify-center">
                    {% if channel.logo %}
                    <img src="{{ channel.logo }}" alt="{{ channel.name }}" class="h-full w-full object-contain p-2" loading="lazy">
                    {% else %}
                    <i class="fas fa-tv text-3xl text-gray-400"></i>
                    {% endif %}
                    <span class="absolute top-1 left-1 bg-red-600 text-white text-xs px-1.5 py-0.5 rounded live-badge">LIVE</span>
                    {% if channel.stream_type == 'youtube' %}
                    <span class="absolute top-1 right-1 bg-red-700 text-white text-xs px-1.5 py-0.5 rounded">▶️</span>
                    {% endif %}
                </div>
                <div class="p-2.5">
                    <h3 class="font-semibold text-xs truncate">{{ channel.name }}</h3>
                    <div class="flex items-center justify-between mt-1">
                        <span class="text-xs text-gray-400">{{ channel.country }}</span>
                        <button onclick="addToFavorites('{{ channel.id }}', 'iptv', event)" class="text-yellow-400 hover:text-yellow-500 transition">
                            <i class="far fa-star text-xs"></i>
                        </button>
                    </div>
                </div>
            </a>
            {% endfor %}
        </div>
    </section>
    {% endif %}

    {% if selected_playlist and not iptv_channels %}
    <section>
        <div class="flex items-center gap-4 mb-6">
            <a href="/" class="text-gray-400 hover:text-red-600 transition text-xl"><i class="fas fa-arrow-left"></i></a>
            <h2 class="text-2xl font-bold">{{ selected_playlist.display_name }}</h2>
        </div>
        <div class="bg-white dark:bg-gray-800 rounded-2xl p-12 text-center border border-gray-100 dark:border-gray-700">
            <div class="text-5xl mb-4">📡</div>
            <h3 class="text-xl font-semibold mb-2">Chaînes en cours de chargement</h3>
            <p class="text-gray-400 mb-6">La synchronisation IPTV est en cours. Revenez dans quelques minutes.</p>
            <a href="/" class="bg-red-600 hover:bg-red-700 text-white px-6 py-2.5 rounded-xl font-medium transition inline-flex items-center gap-2">
                <i class="fas fa-arrow-left"></i> Retour à l'accueil
            </a>
        </div>
    </section>
    {% endif %}

<script>
    function filterChannels(cat, btn) {
        // Update button styles
        document.querySelectorAll('.cat-filter-btn').forEach(b => {
            b.className = b.className.replace('bg-red-600 text-white', 'bg-gray-200 dark:bg-gray-700 text-gray-700 dark:text-gray-300 hover:bg-red-600 hover:text-white');
        });
        btn.className = btn.className.replace('bg-gray-200 dark:bg-gray-700 text-gray-700 dark:text-gray-300 hover:bg-red-600 hover:text-white', 'bg-red-600 text-white');

        // Filter cards
        document.querySelectorAll('.channel-card').forEach(card => {
            if (cat === 'all' || card.dataset.category === cat) {
                card.style.display = '';
            } else {
                card.style.display = 'none';
            }
        });
    }
</script>

    <!-- Streams communautaires en direct -->
    {% if live_streams %}
    <section id="live">
        <div class="flex items-center justify-between mb-5">
            <h2 class="text-2xl font-bold flex items-center gap-2">
                <span class="relative flex h-3 w-3">
                    <span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-red-400 opacity-75"></span>
                    <span class="relative inline-flex rounded-full h-3 w-3 bg-red-600"></span>
                </span>
                Lives communautaires
            </h2>
            <span class="text-sm text-gray-400">{{ live_streams|length }} en direct</span>
        </div>
        <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-5">
            {% for stream in live_streams %}
            <a href="/watch/user/{{ stream.id }}" class="stream-card bg-white dark:bg-gray-800 rounded-xl overflow-hidden hover:shadow-xl border border-gray-100 dark:border-gray-700 group">
                <div class="relative pb-9/16 bg-gray-200 dark:bg-gray-700">
                    {% if stream.thumbnail %}
                    <img src="{{ stream.thumbnail }}" alt="{{ stream.title }}" class="absolute inset-0 w-full h-full object-cover group-hover:scale-105 transition duration-300">
                    {% else %}
                    <div class="absolute inset-0 flex items-center justify-center">
                        <i class="fas fa-user text-4xl text-gray-400"></i>
                    </div>
                    {% endif %}
                    <span class="absolute top-2 left-2 bg-red-600 text-white text-xs px-2 py-0.5 rounded-full live-badge flex items-center gap-1">
                        <i class="fas fa-circle text-xs"></i> LIVE
                    </span>
                    <span class="absolute bottom-2 right-2 bg-black/70 backdrop-blur text-white text-xs px-2 py-0.5 rounded-full">
                        <i class="fas fa-eye mr-1"></i>{{ stream.viewer_count }}
                    </span>
                </div>
                <div class="p-4">
                    <h3 class="font-semibold truncate mb-1">{{ stream.title }}</h3>
                    <div class="flex items-center justify-between text-sm text-gray-500">
                        <span class="capitalize">{{ stream.category }}</span>
                        <span><i class="fas fa-heart text-red-400 mr-1"></i>{{ stream.like_count }}</span>
                    </div>
                </div>
            </a>
            {% endfor %}
        </div>
    </section>
    {% endif %}

    <!-- Chaînes TV, Radio & YouTube externes -->
    {% if external_streams and not selected_playlist %}
    <section id="external-streams">
        <h2 class="text-2xl font-bold mb-5 flex items-center gap-2">
            <span class="w-8 h-8 bg-red-100 dark:bg-red-900/30 rounded-lg flex items-center justify-center">📡</span>
            Chaînes TV, Radio & YouTube
        </h2>

        <!-- Filtres par type -->
        <div class="flex gap-2 mb-4 flex-wrap">
            <button onclick="filterExternal('all', this)" class="filter-btn px-3 py-1.5 rounded-full text-sm font-medium bg-red-600 text-white">Tous</button>
            <button onclick="filterExternal('hls', this)" class="filter-btn px-3 py-1.5 rounded-full text-sm font-medium bg-gray-200 dark:bg-gray-700 text-gray-700 dark:text-gray-300">📺 TV/HLS</button>
            <button onclick="filterExternal('dash', this)" class="filter-btn px-3 py-1.5 rounded-full text-sm font-medium bg-gray-200 dark:bg-gray-700 text-gray-700 dark:text-gray-300">📡 DASH</button>
            <button onclick="filterExternal('audio', this)" class="filter-btn px-3 py-1.5 rounded-full text-sm font-medium bg-gray-200 dark:bg-gray-700 text-gray-700 dark:text-gray-300">📻 Radio</button>
            <button onclick="filterExternal('youtube', this)" class="filter-btn px-3 py-1.5 rounded-full text-sm font-medium bg-gray-200 dark:bg-gray-700 text-gray-700 dark:text-gray-300">▶️ YouTube</button>
        </div>

        <!-- Grille des chaînes -->
        <div class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-3" id="external-grid">
            {% for stream in external_streams %}
            {% set bg_colors = {
                'audio': 'from-orange-500 to-orange-700',
                'youtube': 'from-red-600 to-red-800',
                'dash': 'from-purple-600 to-purple-800',
                'mp4': 'from-blue-600 to-blue-800',
                'hls': 'from-gray-700 to-gray-900'
            } %}
            {% set cat_bg = {
                'news': 'from-blue-700 to-blue-900',
                'sports': 'from-green-600 to-green-900',
                'entertainment': 'from-purple-600 to-pink-800',
                'radio': 'from-orange-500 to-red-700',
                'religion': 'from-teal-600 to-teal-900',
                'webcam': 'from-cyan-600 to-cyan-900',
                'science': 'from-indigo-600 to-indigo-900',
                'gaming': 'from-fuchsia-600 to-fuchsia-900'
            } %}
            {% set bg_gradient = cat_bg.get(stream.category, bg_colors.get(stream.stream_type, 'from-gray-700 to-gray-900')) %}
            <a href="/watch/external/{{ stream.id }}" class="stream-card group rounded-xl overflow-hidden hover:shadow-xl border border-gray-100 dark:border-gray-700 external-card bg-white dark:bg-gray-800" data-type="{{ stream.stream_type }}">
                <div class="relative h-24 bg-gradient-to-br {{ bg_gradient }} flex items-center justify-center overflow-hidden">
                    {% if stream.logo %}
                    <div class="absolute inset-0 bg-gradient-to-br {{ bg_gradient }} opacity-60"></div>
                    <img src="{{ stream.logo }}" alt="{{ stream.title }}" class="relative z-10 h-14 w-auto max-w-full object-contain drop-shadow-lg" loading="lazy" onerror="this.parentElement.classList.add('no-logo')">
                    {% else %}
                    <span class="text-4xl opacity-80">
                        {% if stream.stream_type == 'audio' %}📻
                        {% elif stream.stream_type == 'youtube' %}▶️
                        {% elif stream.category == 'news' %}📰
                        {% elif stream.category == 'sports' %}⚽
                        {% elif stream.category == 'gaming' %}🎮
                        {% elif stream.category == 'religion' %}🕌
                        {% elif stream.category == 'webcam' %}📹
                        {% else %}📺{% endif %}
                    </span>
                    {% endif %}
                    <span class="absolute top-1.5 left-1.5 bg-red-600/90 text-white text-xs px-1.5 py-0.5 rounded-full font-bold live-badge">LIVE</span>
                    {% if stream.stream_type == 'youtube' %}
                    <span class="absolute top-1.5 right-1.5 bg-red-700/90 backdrop-blur text-white text-xs px-1.5 py-0.5 rounded-full">▶️ YT</span>
                    {% elif stream.stream_type == 'audio' %}
                    <span class="absolute top-1.5 right-1.5 bg-orange-600/90 text-white text-xs px-1.5 py-0.5 rounded-full">📻 Radio</span>
                    {% elif stream.quality %}
                    <span class="absolute top-1.5 right-1.5 bg-black/50 text-white text-xs px-1.5 py-0.5 rounded-full">{{ stream.quality }}</span>
                    {% endif %}
                </div>
                <div class="p-2.5">
                    <h3 class="font-semibold text-xs truncate">{{ stream.title }}</h3>
                    <div class="flex items-center justify-between mt-1">
                        <span class="text-xs text-gray-400">{{ stream.country }} · {{ stream.quality }}</span>
                        <button onclick="addToFavorites('{{ stream.id }}', 'external', event)" class="text-yellow-400 hover:text-yellow-500 transition">
                            <i class="far fa-star text-xs"></i>
                        </button>
                    </div>
                </div>
            </a>
            {% endfor %}
        </div>
    </section>
    {% endif %}

    <!-- Message si aucun contenu -->
    {% if not live_streams and not external_streams and not iptv_channels and not pl_countries %}
    <div class="text-center py-20">
        <div class="text-8xl mb-6">📺</div>
        <h3 class="text-2xl font-bold text-gray-600 dark:text-gray-300 mb-2">Aucun contenu trouvé</h3>
        <p class="text-gray-400 mb-6">La synchronisation IPTV est peut-être en cours. Réessayez dans quelques minutes.</p>
        <a href="/" class="bg-red-600 text-white px-6 py-3 rounded-full hover:bg-red-700 transition shadow">
            <i class="fas fa-sync-alt mr-2"></i>Rafraîchir
        </a>
    </div>
    {% endif %}
</div>

<script>
    function filterExternal(type, btn) {
        // Mettre à jour les boutons
        document.querySelectorAll('.filter-btn').forEach(b => {
            b.className = 'filter-btn px-3 py-1.5 rounded-full text-sm font-medium bg-gray-200 dark:bg-gray-700 text-gray-700 dark:text-gray-300';
        });
        btn.className = 'filter-btn px-3 py-1.5 rounded-full text-sm font-medium bg-red-600 text-white';
        
        // Filtrer les cartes
        document.querySelectorAll('.external-card').forEach(card => {
            if (type === 'all' || card.dataset.type === type) {
                card.style.display = '';
            } else {
                card.style.display = 'none';
            }
        });
    }
</script>
{% endblock %}'''

    # Template pour regarder un flux externe
    WATCH_EXTERNAL_TEMPLATE = '''{% extends "base.html" %}

{% block title %}{{ stream.title }} - {{ app_name }}{% endblock %}

{% block content %}
<div class="grid grid-cols-1 lg:grid-cols-4 gap-6">
    <div class="lg:col-span-3 space-y-4">

        {% if stream.stream_type == 'audio' %}
        <!-- ═══ LECTEUR AUDIO DÉDIÉ ═══ -->
        <div class="rounded-2xl overflow-hidden shadow-2xl" style="background:linear-gradient(135deg,#1a1a2e 0%,#16213e 50%,#0f3460 100%);">
            <!-- Artwork + visualiseur -->
            <div class="relative flex flex-col items-center justify-center px-8 pt-10 pb-6">
                <!-- Artwork animé -->
                <div id="audio-artwork" class="relative w-40 h-40 rounded-full shadow-2xl mb-6 flex items-center justify-center overflow-hidden" style="background:linear-gradient(135deg,#e63946,#f4a261);transition:transform 0.3s;">
                    {% if stream.logo %}
                    <img src="{{ stream.logo }}" class="w-full h-full object-contain p-4" onerror="this.style.display='none'">
                    {% else %}
                    <span style="font-size:4rem;">📻</span>
                    {% endif %}
                    <!-- Pulse rings -->
                    <div id="pulse-ring" class="hidden absolute inset-0 rounded-full border-4 border-white/30" style="animation:audioPulse 1.5s ease-in-out infinite;"></div>
                </div>
                <!-- Titre + infos -->
                <h1 class="text-white text-2xl font-bold text-center mb-1">{{ stream.title }}</h1>
                <p class="text-white/60 text-sm text-center mb-1">{{ stream.country }}{% if stream.language %} · {{ stream.language|upper }}{% endif %}{% if stream.quality %} · {{ stream.quality }}{% endif %}</p>
                <span class="bg-red-600 text-white text-xs px-3 py-1 rounded-full font-bold live-badge flex items-center gap-1 mb-4">
                    <span style="width:6px;height:6px;border-radius:50%;background:#fff;animation:audioPulse 1s infinite;display:inline-block;"></span> EN DIRECT
                </span>
                <!-- Visualiseur de spectre canvas -->
                <canvas id="audio-visualizer" width="500" height="60" style="width:100%;max-width:500px;opacity:0.7;border-radius:8px;"></canvas>
            </div>
            <!-- Lecteur audio natif stylisé -->
            <div class="px-8 pb-8">
                <audio id="audio-player" controls autoplay preload="auto" class="w-full" style="border-radius:12px;background:#0f3460;height:44px;">
                    <source src="{{ stream.url|e }}" type="audio/mpeg">
                    <source src="{{ stream.url|e }}" type="audio/aac">
                    <source src="{{ stream.url|e }}" type="audio/ogg">
                </audio>
                <!-- Slider volume -->
                <div class="mt-4 flex items-center gap-3">
                    <span class="text-white/60 text-lg">🔇</span>
                    <input type="range" id="vol-slider" min="0" max="1" step="0.01" value="1"
                           class="vol-slider flex-1" oninput="var a=document.getElementById('audio-player');if(a)a.volume=parseFloat(this.value);">
                    <span class="text-white/60 text-lg">🔊</span>
                </div>
                <div class="flex justify-center gap-3 mt-3 flex-wrap">
                    <button onclick="toggleMute()" id="btn-mute" class="bg-white/10 hover:bg-white/20 text-white px-4 py-2 rounded-xl text-sm transition">🔊 Son</button>
                    <button onclick="addToFavorites('{{ stream.id }}', 'external', event)" class="bg-yellow-500/20 hover:bg-yellow-500/40 text-yellow-400 px-4 py-2 rounded-xl text-sm transition">⭐ Favori</button>
                    <button onclick="openMiniPlayer()" class="bg-blue-500/20 hover:bg-blue-500/40 text-blue-400 px-4 py-2 rounded-xl text-sm transition">📌 Mini</button>
                </div>
            </div>
        </div>

        {% else %}
        <!-- ═══ LECTEUR VIDÉO ═══ -->
        <div class="bg-black rounded-2xl overflow-hidden shadow-2xl">
            <div class="video-container" id="player-container">
                <video id="video-player" class="video-js vjs-default-skin vjs-big-play-centered" controls autoplay playsinline preload="auto"></video>
                <!-- Contrôle volume flottant -->
                <div class="vol-overlay" id="vol-overlay-ext">
                    <button onclick="var v=document.querySelector('#player-container video');if(v){v.muted=!v.muted;this.textContent=v.muted?'🔇':'🔊';}" title="Muet">🔊</button>
                    <input type="range" min="0" max="1" step="0.02" value="1"
                           oninput="var v=document.querySelector('#player-container video');if(v)v.volume=parseFloat(this.value);"
                           title="Volume">
                </div>
            </div>
        </div>
        {% endif %}

        <!-- Informations -->
        <div class="bg-white dark:bg-gray-800 rounded-2xl p-6 shadow border border-gray-100 dark:border-gray-700">
            <div class="flex items-start justify-between gap-4 flex-wrap">
                <div>
                    <h1 class="text-2xl font-bold mb-1">{{ stream.title }}</h1>
                    {% if stream.subcategory %}<span class="inline-block bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300 text-xs px-3 py-1 rounded-full mb-2">{{ stream.subcategory }}</span>{% endif %}
                    <div class="flex flex-wrap gap-2 text-sm text-gray-500 mt-2">
                        <span class="bg-gray-100 dark:bg-gray-700 px-3 py-1 rounded-full"><i class="fas fa-tag mr-1"></i>{{ stream.category }}</span>
                        <span class="bg-gray-100 dark:bg-gray-700 px-3 py-1 rounded-full"><i class="fas fa-globe mr-1"></i>{{ stream.country }}</span>
                        {% if stream.language %}<span class="bg-gray-100 dark:bg-gray-700 px-3 py-1 rounded-full"><i class="fas fa-language mr-1"></i>{{ stream.language|upper }}</span>{% endif %}
                        <span class="bg-gray-100 dark:bg-gray-700 px-3 py-1 rounded-full">
                            {% if stream.stream_type == 'youtube' %}▶️ YouTube
                            {% elif stream.stream_type == 'audio' %}📻 Radio
                            {% elif stream.stream_type == 'dash' %}📡 DASH
                            {% elif stream.stream_type == 'mp4' %}🎬 MP4
                            {% else %}📺 HLS{% endif %}
                        </span>
                    </div>
                </div>
                <div class="flex items-center gap-2">
                    <span class="bg-red-600 text-white px-4 py-1.5 rounded-full text-sm font-semibold flex items-center gap-1 live-badge"><i class="fas fa-circle text-xs"></i> EN DIRECT</span>
                    <button onclick="addToFavorites('{{ stream.id }}', 'external', event)" class="text-yellow-400 hover:text-yellow-500 transition text-xl"><i class="far fa-star"></i></button>
                </div>
            </div>
        </div>
    </div>

    <!-- Recommandations -->
    <div class="lg:col-span-1">
        <div class="bg-white dark:bg-gray-800 rounded-2xl p-4 shadow border border-gray-100 dark:border-gray-700 sticky top-20">
            <h3 class="font-bold mb-4 text-lg">{% if stream.stream_type == 'audio' %}📻 Autres radios{% else %}Recommandations{% endif %}</h3>
            <div class="space-y-2 max-h-[70vh] overflow-y-auto custom-scrollbar">
                {% for rec in recommendations %}
                <a href="/watch/external/{{ rec.id }}" class="flex items-center space-x-3 p-2.5 hover:bg-gray-50 dark:hover:bg-gray-700 rounded-xl transition">
                    <div class="w-12 h-12 rounded-lg overflow-hidden flex-shrink-0 flex items-center justify-center {% if rec.stream_type == 'audio' %}bg-gradient-to-br from-orange-400 to-red-600{% else %}bg-gray-100 dark:bg-gray-600{% endif %}">
                        {% if rec.logo %}<img src="{{ rec.logo }}" class="w-full h-full object-contain p-1" loading="lazy" onerror="this.style.display='none'">
                        {% elif rec.stream_type == 'audio' %}<span class="text-xl">📻</span>
                        {% else %}<i class="fas fa-tv text-gray-400"></i>{% endif %}
                    </div>
                    <div class="flex-1 min-w-0">
                        <h4 class="font-medium text-sm truncate">{{ rec.title }}</h4>
                        <p class="text-xs text-gray-400">{{ rec.country }}{% if rec.quality %} · {{ rec.quality }}{% endif %}</p>
                    </div>
                </a>
                {% else %}
                <p class="text-sm text-gray-400 text-center py-4">Aucune recommandation</p>
                {% endfor %}
            </div>
        </div>
    </div>
</div>
{% endblock %}

{% block scripts %}
<style>
@keyframes audioPulse { 0%,100%{transform:scale(1);opacity:.7} 50%{transform:scale(1.15);opacity:1} }
@keyframes audioSpin  { from{transform:rotate(0deg)} to{transform:rotate(360deg)} }
</style>
<script>
(function() {
    var streamUrl  = '{{ stream.url|e }}';
    var streamType = '{{ stream.stream_type }}';
    var youtubeData = {{ youtube_data|tojson|safe if youtube_data else 'null' }};
    var proxyUrl   = '/proxy/stream?url=' + encodeURIComponent(streamUrl);
    var attempt    = 0;
    var player;

    /* ── Utilitaires erreur ─── */
    function showError() {
        var c = document.getElementById('player-container');
        if (!c) return;
        c.innerHTML = '<div style="display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:220px;color:#fff;background:#111;border-radius:1rem;padding:2rem;gap:1rem;">'
            + '<div style="font-size:3rem;">📡</div><p style="font-weight:700;">Flux indisponible</p>'
            + '<div style="display:flex;gap:.75rem;">'
            + '<button onclick="location.reload()" style="background:#2563eb;color:#fff;border:0;padding:.5rem 1.25rem;border-radius:.75rem;cursor:pointer;">🔄 Réessayer</button>'
            + '<a href="/" style="background:#dc2626;color:#fff;text-decoration:none;padding:.5rem 1.25rem;border-radius:.75rem;">← Chaînes</a>'
            + '</div></div>';
    }

    /* ── Lecteur AUDIO ─── */
    if (streamType === 'audio') {
        function initAudio() {
            var audio = document.getElementById('audio-player');
            var artwork = document.getElementById('audio-artwork');
            var pulse   = document.getElementById('pulse-ring');
            var canvas  = document.getElementById('audio-visualizer');
            if (!audio) return;

            // Essayer les formats dans l'ordre
            var mimeTypes = ['audio/mpeg','audio/aac','audio/ogg','audio/flac','audio/mp4','audio/wav','application/x-mpegURL'];
            var srcTried = false;

            audio.addEventListener('play', function() {
                if (artwork) artwork.style.animation = 'audioSpin 4s linear infinite';
                if (pulse)   pulse.classList.remove('hidden');
                startVisualizer(audio, canvas);
            });
            audio.addEventListener('pause', function() {
                if (artwork) artwork.style.animation = '';
                if (pulse)   pulse.classList.add('hidden');
            });
            audio.addEventListener('error', function() {
                if (!srcTried) {
                    srcTried = true;
                    // Essayer proxy audio dédié
                    audio.src = '/proxy/audio?url=' + encodeURIComponent(streamUrl);
                    audio.load();
                    audio.play().catch(function(){
                        // Dernier recours: proxy HLS générique
                        audio.src = proxyUrl;
                        audio.load();
                        audio.play().catch(function(){});
                    });
                }
            });

            // Web Audio visualiseur
            function startVisualizer(audioEl, cv) {
                if (!cv || !window.AudioContext) return;
                try {
                    var ctx   = new (window.AudioContext || window.webkitAudioContext)();
                    var src   = ctx.createMediaElementSource(audioEl);
                    var anal  = ctx.createAnalyser();
                    anal.fftSize = 128;
                    src.connect(anal); anal.connect(ctx.destination);
                    var buf   = new Uint8Array(anal.frequencyBinCount);
                    var cCtx  = cv.getContext('2d');
                    var W = cv.width, H = cv.height;
                    (function draw() {
                        requestAnimationFrame(draw);
                        anal.getByteFrequencyData(buf);
                        cCtx.clearRect(0,0,W,H);
                        var bw = W / buf.length;
                        for (var i=0; i<buf.length; i++) {
                            var h = (buf[i]/255) * H;
                            var r = 230 + Math.floor((buf[i]/255)*25);
                            var g = Math.floor(57 + (buf[i]/255)*100);
                            var b = 70 + Math.floor((buf[i]/255)*50);
                            cCtx.fillStyle = 'rgb('+r+','+g+','+b+')';
                            cCtx.fillRect(i*bw, H-h, bw-1, h);
                        }
                    })();
                } catch(e) {}
            }
        }

        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', initAudio);
        } else { initAudio(); }

        /* ── Contrôles volume ─── */
        window.changeVolume = function(delta) {
            var a = document.getElementById('audio-player');
            if (a) a.volume = Math.max(0, Math.min(1, (a.volume||1) + delta));
        };
        window.toggleMute = function() {
            var a = document.getElementById('audio-player');
            var b = document.getElementById('btn-mute');
            if (!a) return;
            a.muted = !a.muted;
            if (b) b.textContent = a.muted ? '🔇 Muet' : '🔊 Son';
        };
        window.openMiniPlayer = function() {
            if (window.livewatchMiniPlayer) {
                window.livewatchMiniPlayer('{{ stream.url|e }}', '{{ stream.title|e }}', '{{ stream.logo|e }}');
            }
        };

    } else {
        /* ── Lecteur VIDÉO ─── */
        function tryHlsJs(url) {
            var c = document.getElementById('player-container');
            var video = document.createElement('video');
            video.controls = true; video.autoplay = true;
            video.style.cssText = 'width:100%;height:100%;position:absolute;top:0;left:0;background:#000;';
            if (c) { c.innerHTML = ''; c.appendChild(video); }
            if (window.Hls && Hls.isSupported()) {
                // Adapter la qualité selon la connexion réseau
                var netClass = window._lwNetClass || 'fast';
                var hlsConfig = { enableWorker: true, lowLatencyMode: true };
                if (netClass === 'slow') {
                    // 2G/3G lent : forcer la qualité la plus basse
                    hlsConfig.startLevel      = 0;
                    hlsConfig.capLevelToPlayerSize = true;
                    hlsConfig.maxBufferLength = 10;
                    hlsConfig.maxMaxBufferLength = 20;
                    hlsConfig.abrBandWidthFactor  = 0.5;
                    hlsConfig.abrBandWidthUpFactor = 0.3;
                } else if (netClass === 'medium') {
                    // 4G moyen : qualité auto mais limitée
                    hlsConfig.startLevel      = -1;
                    hlsConfig.capLevelToPlayerSize = true;
                    hlsConfig.maxBufferLength = 20;
                    hlsConfig.abrBandWidthFactor  = 0.7;
                }
                // fast : config par défaut (haute qualité)
                var hls = new Hls(hlsConfig);
                hls.loadSource(url); hls.attachMedia(video);
                hls.on(Hls.Events.MANIFEST_PARSED, function(){ video.play().catch(function(){}); });
                hls.on(Hls.Events.ERROR, function(ev,d){ if(d.fatal){ hls.destroy(); showError(); } });
            } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
                video.src = url; video.play().catch(function(){ showError(); });
            } else { showError(); }
        }

        function initVideoJs() {
            var netClass = window._lwNetClass || 'fast';
            var vhsOpts = {
                overrideNative: !videojs.browser.IS_SAFARI,
                enableLowInitialPlaylist: netClass !== 'fast',
            };
            if (netClass === 'slow') {
                vhsOpts.bandwidth     = 500000;    // limiter à 500 Kbps
                vhsOpts.limitRenditionByPlayerDimensions = true;
            } else if (netClass === 'medium') {
                vhsOpts.bandwidth     = 2000000;   // limiter à 2 Mbps
                vhsOpts.limitRenditionByPlayerDimensions = true;
            }
            player = videojs('video-player', {
                controls:true, autoplay:true, preload:'auto', fluid:true,
                techOrder:['html5'],
                html5:{ vhs: vhsOpts,
                        nativeVideoTracks:false, nativeAudioTracks:false, nativeTextTracks:false }
            });
            function loadSrc(url,type){ player.src({src:url,type:type}); player.load(); }
            if (streamType === 'youtube' && youtubeData) {
                if (youtubeData.embed_url || youtubeData.type === 'embed') {
                    var iframe = document.createElement('iframe');
                    iframe.src = youtubeData.embed_url || 'https://www.youtube.com/embed/' + youtubeData.video_id + '?autoplay=1';
                    iframe.allow = 'autoplay; encrypted-media; fullscreen'; iframe.allowFullscreen = true;
                    iframe.style.cssText = 'position:absolute;top:0;left:0;width:100%;height:100%;border:0;';
                    var c2 = document.getElementById('player-container'); if (c2) { c2.innerHTML=''; c2.appendChild(iframe); } return;
                }
                loadSrc(youtubeData.url || streamUrl, 'application/x-mpegURL');
            } else if (streamType === 'dash') { loadSrc(streamUrl, 'application/dash+xml');
            } else if (streamType === 'mp4')  { loadSrc(streamUrl, 'video/mp4');
            } else { loadSrc(streamUrl, 'application/x-mpegURL'); }
            player.on('error', function() {
                player.off('error');
                if (attempt === 0) {
                    attempt = 1; loadSrc(proxyUrl, 'application/x-mpegURL');
                    player.on('error', function() { player.off('error'); player.dispose(); tryHlsJs(proxyUrl); });
                } else { player.dispose(); tryHlsJs(proxyUrl); }
            });
        }
        if (document.readyState === 'loading') { document.addEventListener('DOMContentLoaded', initVideoJs); }
        else { initVideoJs(); }
    }
})();
</script>
{% endblock %}'''

    # Template pour regarder une chaîne IPTV
    WATCH_IPTV_TEMPLATE = '''{% extends "base.html" %}

{% block title %}{{ channel.name }} - {{ app_name }}{% endblock %}

{% block content %}
<div class="grid grid-cols-1 lg:grid-cols-4 gap-6">
    <!-- Lecteur principal (vidéo ou audio selon le type) -->
    <div class="lg:col-span-3 space-y-4">

        {% if channel.stream_type == 'audio' %}
        <!-- Lecteur audio dédié pour chaînes radio IPTV -->
        <div class="rounded-2xl overflow-hidden shadow-2xl" style="background:linear-gradient(135deg,#1a1a2e 0%,#16213e 50%,#0f3460 100%);">
            <div class="relative flex flex-col items-center justify-center px-8 pt-10 pb-6">
                <div id="audio-artwork" class="relative w-40 h-40 rounded-full shadow-2xl mb-6 flex items-center justify-center overflow-hidden" style="background:linear-gradient(135deg,#e63946,#f4a261);">
                    {% if channel.logo %}<img src="{{ channel.logo }}" class="w-full h-full object-contain p-4" onerror="this.style.display='none'">
                    {% else %}<span style="font-size:4rem;">📻</span>{% endif %}
                    <div id="pulse-ring" class="hidden absolute inset-0 rounded-full border-4 border-white/30" style="animation:audioPulse 1.5s ease-in-out infinite;"></div>
                </div>
                <h1 class="text-white text-2xl font-bold text-center mb-1">{{ channel.name }}</h1>
                <p class="text-white/60 text-sm text-center mb-1">{{ channel.country }}{% if channel.language %} · {{ channel.language|upper }}{% endif %}</p>
                <span class="bg-red-600 text-white text-xs px-3 py-1 rounded-full font-bold live-badge flex items-center gap-1 mb-4">
                    <span style="width:6px;height:6px;border-radius:50%;background:#fff;animation:audioPulse 1s infinite;display:inline-block;"></span> EN DIRECT
                </span>
                <canvas id="audio-visualizer" width="500" height="60" style="width:100%;max-width:500px;opacity:0.7;border-radius:8px;"></canvas>
            </div>
            <div class="px-8 pb-8">
                <audio id="audio-player" controls autoplay preload="auto" class="w-full" style="border-radius:12px;height:44px;">
                    <source src="{{ channel.url|e }}" type="audio/mpeg">
                    <source src="{{ channel.url|e }}" type="audio/aac">
                    <source src="{{ channel.url|e }}" type="audio/ogg">
                    <source src="{{ channel.url|e }}" type="application/x-mpegURL">
                </audio>
                <!-- Slider volume -->
                <div class="mt-4 flex items-center gap-3">
                    <span class="text-white/60 text-xl">🔇</span>
                    <input type="range" id="vol-slider" min="0" max="1" step="0.01" value="1"
                           class="vol-slider flex-1"
                           oninput="var a=document.getElementById('audio-player');if(a){a.volume=parseFloat(this.value);}">
                    <span class="text-white/60 text-xl">🔊</span>
                </div>
                <div class="flex justify-center gap-3 mt-3 flex-wrap">
                    <button onclick="toggleMute()" id="btn-mute" class="bg-white/10 hover:bg-white/20 text-white px-4 py-2 rounded-xl text-sm transition">🔊 Son</button>
                    <button onclick="addToFavorites('{{ channel.id }}', 'iptv', event)" class="bg-yellow-500/20 hover:bg-yellow-500/40 text-yellow-400 px-4 py-2 rounded-xl text-sm transition">⭐ Favori</button>
                    <button onclick="openMiniPlayer()" class="bg-blue-500/20 hover:bg-blue-500/40 text-blue-400 px-4 py-2 rounded-xl text-sm transition">📌 Mini</button>
                </div>
            </div>
        </div>

        {% else %}
        <!-- Lecteur vidéo standard -->
        <div class="bg-black rounded-2xl overflow-hidden shadow-2xl">
            <div class="video-container" id="player-container">
                <video id="video-player" class="video-js vjs-default-skin vjs-big-play-centered" controls autoplay playsinline preload="auto"></video>
                <!-- Contrôle volume flottant -->
                <div class="vol-overlay">
                    <button onclick="var v=document.querySelector('#player-container video');if(v){v.muted=!v.muted;this.textContent=v.muted?'🔇':'🔊';}" title="Muet">🔊</button>
                    <input type="range" min="0" max="1" step="0.02" value="1"
                           oninput="var v=document.querySelector('#player-container video');if(v)v.volume=parseFloat(this.value);"
                           title="Volume">
                </div>
            </div>
        </div>
        {% endif %}

        <!-- Informations de la chaîne -->
        <div class="bg-white dark:bg-gray-800 rounded-2xl p-6 shadow border border-gray-100 dark:border-gray-700">
            <div class="flex items-start justify-between gap-4 flex-wrap">
                <div>
                    <h1 class="text-2xl font-bold mb-1">{{ channel.name }}</h1>
                    <div class="flex flex-wrap gap-2 text-sm text-gray-500 mt-2">
                        <span class="bg-gray-100 dark:bg-gray-700 px-3 py-1 rounded-full"><i class="fas fa-tag mr-1"></i>{{ channel.category }}</span>
                        <span class="bg-gray-100 dark:bg-gray-700 px-3 py-1 rounded-full"><i class="fas fa-globe mr-1"></i>{{ channel.country }}</span>
                        <span class="bg-gray-100 dark:bg-gray-700 px-3 py-1 rounded-full"><i class="fas fa-language mr-1"></i>{{ channel.language|upper }}</span>
                        <span class="bg-gray-100 dark:bg-gray-700 px-3 py-1 rounded-full"><i class="fas fa-eye mr-1"></i>{{ channel.viewers }}</span>
                        <span class="bg-gray-100 dark:bg-gray-700 px-3 py-1 rounded-full">
                            {% if channel.stream_type == 'youtube' %}▶️ YouTube
                            {% elif channel.stream_type == 'dash' %}📡 DASH
                            {% elif channel.stream_type == 'audio' %}📻 Audio
                            {% elif channel.stream_type == 'mp4' %}🎬 MP4
                            {% elif channel.stream_type == 'rtmp' %}⚡ RTMP
                            {% else %}📺 HLS{% endif %}
                        </span>
                    </div>
                    <p class="text-xs text-gray-400 mt-3">
                        <i class="fas fa-info-circle mr-1"></i>Source: iptv-org · Dernier accès: {{ channel.last_seen.strftime('%d/%m/%Y %H:%M') if channel.last_seen else 'N/A' }}
                    </p>
                </div>
                <div class="flex items-center gap-2">
                    <span class="bg-red-600 text-white px-4 py-1.5 rounded-full text-sm font-semibold flex items-center gap-1 live-badge">
                        <i class="fas fa-circle text-xs"></i> EN DIRECT
                    </span>
                    <button onclick="addToFavorites('{{ channel.id }}', 'iptv', event)" class="text-yellow-400 hover:text-yellow-500 transition text-xl">
                        <i class="far fa-star"></i>
                    </button>
                </div>
            </div>

            <!-- Boutons d'action EPG -->
            <div class="mt-4 pt-4 border-t border-gray-100 dark:border-gray-700 flex flex-wrap gap-2">
                <button onclick="addToFavorites('{{ channel.id }}', 'iptv', event)"
                    class="flex items-center gap-2 bg-yellow-50 dark:bg-yellow-900/20 hover:bg-yellow-100 text-yellow-700 dark:text-yellow-400 px-4 py-2 rounded-xl text-sm font-medium transition">
                    ⭐ Ajouter aux favoris
                </button>
                <button id="btn-remind" onclick="toggleEPGReminder(this)"
                    data-channel="{{ channel.name }}" data-country="{{ channel.country }}"
                    class="flex items-center gap-2 bg-blue-50 dark:bg-blue-900/20 hover:bg-blue-100 text-blue-700 dark:text-blue-400 px-4 py-2 rounded-xl text-sm font-medium transition">
                    🔔 Me rappeler
                </button>
                {% if channel.stream_type in ['hls','mp4','dash'] %}
                <button onclick="downloadStream('{{ channel.url|e }}', '{{ channel.name|e }}')"
                    class="flex items-center gap-2 bg-green-50 dark:bg-green-900/20 hover:bg-green-100 text-green-700 dark:text-green-400 px-4 py-2 rounded-xl text-sm font-medium transition">
                    ⬇ Enregistrer le flux
                </button>
                {% endif %}
                <a href="/events?country={{ channel.country }}"
                    class="flex items-center gap-2 bg-purple-50 dark:bg-purple-900/20 hover:bg-purple-100 text-purple-700 dark:text-purple-400 px-4 py-2 rounded-xl text-sm font-medium transition">
                    📅 Programmes {{ channel.country }}
                </a>
            </div>
        </div>
    </div>

    <!-- Chaînes similaires -->
    <div class="lg:col-span-1">
        <div class="bg-white dark:bg-gray-800 rounded-2xl p-4 shadow border border-gray-100 dark:border-gray-700 sticky top-20">
            <h3 class="font-bold mb-4 text-lg">Chaînes similaires</h3>
            <div class="space-y-2 max-h-[70vh] overflow-y-auto custom-scrollbar">
                {% for rec in recommendations %}
                <a href="/watch/iptv/{{ rec.id }}" class="flex items-center space-x-3 p-2.5 hover:bg-gray-50 dark:hover:bg-gray-700 rounded-xl transition">
                    <div class="w-12 h-12 bg-gray-100 dark:bg-gray-600 rounded-lg overflow-hidden flex-shrink-0 flex items-center justify-center">
                        {% if rec.logo %}
                        <img src="{{ rec.logo }}" class="w-full h-full object-contain p-1" loading="lazy">
                        {% else %}
                        <i class="fas fa-tv text-gray-400"></i>
                        {% endif %}
                    </div>
                    <div class="flex-1 min-w-0">
                        <h4 class="font-medium text-sm truncate">{{ rec.name }}</h4>
                        <p class="text-xs text-gray-400"><i class="fas fa-eye mr-1"></i>{{ rec.viewers }}</p>
                    </div>
                </a>
                {% else %}
                <p class="text-sm text-gray-400 text-center py-4">Aucune chaîne similaire</p>
                {% endfor %}
            </div>
        </div>
    </div>
</div>

<!-- Script du lecteur universel multi-format -->
<script>
(function() {
    var streamUrl  = '{{ channel.url|e }}';
    var streamType = '{{ channel.stream_type }}';
    var youtubeData = {{ youtube_data|tojson|safe if youtube_data else 'null' }};
    var proxyUrl   = '/proxy/stream?url=' + encodeURIComponent(streamUrl);
    var attempt    = 0;
    var player;

    /* ── Utilitaires ─────────────────────────────── */
    function showError() {
        var c = document.getElementById('player-container');
        if (!c) return;
        c.innerHTML = '<div style="display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:220px;color:#fff;background:#111;border-radius:1rem;padding:2rem;gap:1rem;">'
            + '<div style="font-size:3rem;">📡</div>'
            + '<p style="font-weight:700;font-size:1.1rem;">Flux indisponible</p>'
            + '<p style="font-size:.8rem;color:#999;text-align:center;max-width:280px;">Ce flux est peut-être hors ligne, géo-bloqué ou dans un format non supporté.</p>'
            + '<div style="display:flex;gap:.75rem;flex-wrap:wrap;justify-content:center;">'
            + '<button onclick="location.reload()" style="background:#2563eb;color:#fff;border:0;padding:.5rem 1.25rem;border-radius:.75rem;cursor:pointer;">🔄 Réessayer</button>'
            + '<a href="/" style="background:#dc2626;color:#fff;text-decoration:none;padding:.5rem 1.25rem;border-radius:.75rem;">← Chaînes</a>'
            + '</div></div>';
    }

    /* ── Tentative suivante ──────────────────────── */
    function nextAttempt() {
        attempt++;
        if (attempt === 1) { tryHlsJs(proxyUrl); return; }
        showError();
    }

    /* ── HLS.js natif (sans Video.js) ───────────── */
    function tryHlsJs(url) {
        var video = document.querySelector('#player-container video') || document.querySelector('video');
        if (!video) { video = document.createElement('video'); video.controls = true; video.style.cssText = 'width:100%;height:100%;position:absolute;top:0;left:0;background:#000;'; var c = document.getElementById('player-container'); if (c) { c.innerHTML=''; c.appendChild(video); } }
        if (window.Hls && Hls.isSupported()) {
            var hls = new Hls({ enableWorker: true, lowLatencyMode: true });
            hls.loadSource(url);
            hls.attachMedia(video);
            hls.on(Hls.Events.MANIFEST_PARSED, function() { video.play().catch(function(){}); });
            hls.on(Hls.Events.ERROR, function(e, d) { if (d.fatal) { hls.destroy(); nextAttempt(); } });
        } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
            video.src = url;
            video.play().catch(function() { nextAttempt(); });
        } else {
            nextAttempt();
        }
    }

    /* ── Video.js (init principale) ─────────────── */
    function initVideoJs() {
        player = videojs('video-player', {
            controls: true, autoplay: true, preload: 'auto', fluid: true,
            techOrder: ['html5'],
            html5: {
                vhs: { overrideNative: !videojs.browser.IS_SAFARI, enableLowInitialPlaylist: true },
                nativeVideoTracks: false, nativeAudioTracks: false, nativeTextTracks: false
            }
        });

        function loadSrc(url, type) {
            player.src({ src: url, type: type });
            player.load();
        }

        if (streamType === 'youtube' && youtubeData) {
            if (youtubeData.embed_url || youtubeData.type === 'embed') {
                var iframe = document.createElement('iframe');
                iframe.src = youtubeData.embed_url || 'https://www.youtube.com/embed/' + youtubeData.video_id + '?autoplay=1';
                iframe.allow = 'autoplay; encrypted-media; fullscreen'; iframe.allowFullscreen = true;
                iframe.style.cssText = 'position:absolute;top:0;left:0;width:100%;height:100%;border:0;';
                var c2 = document.getElementById('player-container'); if (c2) { c2.innerHTML=''; c2.appendChild(iframe); }
                return;
            }
            loadSrc(youtubeData.url || streamUrl, 'application/x-mpegURL');
        } else if (streamType === 'dash') {
            loadSrc(streamUrl, 'application/dash+xml');
        } else if (streamType === 'mp4') {
            loadSrc(streamUrl, 'video/mp4');
        } else if (streamType === 'audio') {
            loadSrc(streamUrl, 'audio/mpeg');
        } else {
            loadSrc(streamUrl, 'application/x-mpegURL');
        }

        player.on('error', function() {
            console.warn('Video.js error attempt=' + attempt + ' url=' + streamUrl);
            player.off('error');
            if (attempt === 0) {
                // Tenter le proxy avec Video.js
                attempt = 1;
                loadSrc(proxyUrl, 'application/x-mpegURL');
                player.on('error', function() {
                    player.off('error');
                    player.dispose();
                    // Fallback HLS.js pur
                    tryHlsJs(proxyUrl);
                });
            } else {
                player.dispose();
                tryHlsJs(proxyUrl);
            }
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initVideoJs);
    } else {
        initVideoJs();
    }
})();
</script>
{% endblock %}

{% block scripts %}
<style>
@keyframes audioPulse { 0%,100%{transform:scale(1);opacity:.7} 50%{transform:scale(1.15);opacity:1} }
@keyframes audioSpin  { from{transform:rotate(0deg)} to{transform:rotate(360deg)} }
.vol-slider { -webkit-appearance:none; appearance:none; width:100%; height:6px; border-radius:3px; background:rgba(255,255,255,.2); outline:none; cursor:pointer; }
.vol-slider::-webkit-slider-thumb { -webkit-appearance:none; appearance:none; width:16px; height:16px; border-radius:50%; background:#e63946; cursor:pointer; }
</style>
<script>
(function() {
    var streamType = '{{ channel.stream_type }}';
    var streamUrl  = '{{ channel.url|e }}';
    var proxyAudioUrl = '/proxy/audio?url=' + encodeURIComponent(streamUrl);
    var proxyHlsUrl   = '/proxy/stream?url=' + encodeURIComponent(streamUrl);
    var youtubeData = {{ youtube_data|tojson|safe if youtube_data else 'null' }};

    /* ════════════════════ AUDIO ════════════════════ */
    if (streamType === 'audio') {
        function initAudio() {
            var audio   = document.getElementById('audio-player');
            var artwork = document.getElementById('audio-artwork');
            var pulse   = document.getElementById('pulse-ring');
            var canvas  = document.getElementById('audio-visualizer');
            var volSlider = document.getElementById('vol-slider');
            if (!audio) return;

            // Contrôle volume via slider
            if (volSlider) {
                volSlider.value = audio.volume;
                volSlider.addEventListener('input', function() { audio.volume = parseFloat(this.value); });
                audio.addEventListener('volumechange', function() { if (volSlider) volSlider.value = audio.volume; });
            }

            audio.addEventListener('play', function() {
                if (artwork) artwork.style.animation = 'audioSpin 4s linear infinite';
                if (pulse)   pulse.classList.remove('hidden');
                startVisualizer(audio, canvas);
            });
            audio.addEventListener('pause', function() {
                if (artwork) artwork.style.animation = '';
                if (pulse)   pulse.classList.add('hidden');
            });

            var tried = 0;
            audio.addEventListener('error', function() {
                tried++;
                if (tried === 1)      { audio.src = proxyAudioUrl; audio.load(); audio.play().catch(function(){}); }
                else if (tried === 2) { audio.src = proxyHlsUrl;   audio.load(); audio.play().catch(function(){}); }
            });

            // Visualiseur Web Audio
            function startVisualizer(audioEl, cv) {
                if (!cv || !window.AudioContext && !window.webkitAudioContext) return;
                try {
                    var actx = new (window.AudioContext || window.webkitAudioContext)();
                    var src  = actx.createMediaElementSource(audioEl);
                    var anal = actx.createAnalyser(); anal.fftSize = 256;
                    src.connect(anal); anal.connect(actx.destination);
                    var buf  = new Uint8Array(anal.frequencyBinCount);
                    var cCtx = cv.getContext('2d'); var W=cv.width, H=cv.height;
                    (function draw() {
                        requestAnimationFrame(draw); anal.getByteFrequencyData(buf);
                        cCtx.clearRect(0,0,W,H);
                        var bw = W / buf.length;
                        for (var i=0; i<buf.length; i++) {
                            var v = buf[i]/255;
                            var h = v * H;
                            var g = cCtx.createLinearGradient(0, H-h, 0, H);
                            g.addColorStop(0, 'rgba(230,57,70,' + (0.5+v*0.5) + ')');
                            g.addColorStop(1, 'rgba(244,162,97,0.8)');
                            cCtx.fillStyle = g;
                            cCtx.fillRect(i*bw, H-h, bw-1, h);
                        }
                    })();
                } catch(e) {}
            }

            window.changeVolume = function(d) {
                if (!audio) return;
                audio.volume = Math.max(0, Math.min(1, audio.volume + d));
                showNotification('Volume : ' + Math.round(audio.volume * 100) + '%', 'info');
            };
            window.toggleMute = function() {
                if (!audio) return;
                audio.muted = !audio.muted;
                var b = document.getElementById('btn-mute');
                if (b) b.innerHTML = audio.muted ? '🔇 Muet' : '🔊 Son';
                showNotification(audio.muted ? 'Son coupé' : 'Son activé', 'info');
            };
            window.openMiniPlayer = function() {
                if (window.livewatchMiniPlayer) window.livewatchMiniPlayer(streamUrl, '{{ channel.name|e }}', '{{ channel.logo|e }}');
            };
        }
        if (document.readyState === 'loading') { document.addEventListener('DOMContentLoaded', initAudio); }
        else { initAudio(); }

    /* ════════════════════ VIDÉO ════════════════════ */
    } else {
        var attempt = 0;
        var player;

        function showPlayerError() {
            var c = document.getElementById('player-container');
            if (!c) return;
            c.innerHTML = '<div style="display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:240px;color:#fff;background:linear-gradient(135deg,#111,#1a1a2e);border-radius:1rem;padding:2rem;gap:1rem;text-align:center;">'
                + '<div style="font-size:3.5rem;">📡</div>'
                + '<p style="font-weight:700;font-size:1.1rem;">Flux indisponible</p>'
                + '<p style="font-size:.8rem;color:#aaa;max-width:300px;">Ce flux est peut-être hors ligne, géo-bloqué ou dans un format non supporté.</p>'
                + '<div style="display:flex;gap:.75rem;flex-wrap:wrap;justify-content:center;">'
                + '<button onclick="location.reload()" style="background:#2563eb;color:#fff;border:0;padding:.6rem 1.4rem;border-radius:.75rem;cursor:pointer;font-size:.9rem;">🔄 Réessayer</button>'
                + '<a href="/" style="background:#dc2626;color:#fff;text-decoration:none;padding:.6rem 1.4rem;border-radius:.75rem;font-size:.9rem;">← Autres chaînes</a>'
                + '</div></div>';
        }

        /* Détection du format réel depuis l'URL */
        function detectType(url) {
            var u = (url || '').split('?')[0].toLowerCase();
            if (u.endsWith('.m3u8') || u.endsWith('.m3u'))  return 'hls';
            if (u.endsWith('.mpd'))                          return 'dash';
            if (u.endsWith('.mp4') || u.endsWith('.m4v'))    return 'mp4';
            if (u.endsWith('.ts')  || u.endsWith('.mts'))    return 'ts';
            if (u.endsWith('.mp3') || u.endsWith('.aac') || u.endsWith('.ogg')) return 'audio';
            if (u.includes('youtube.com') || u.includes('youtu.be')) return 'youtube';
            return streamType || 'hls'; // fallback sur le type déclaré
        }

        /* Créer un <video> nu dans player-container */
        function makeRawVideo() {
            var c = document.getElementById('player-container');
            if (!c) return null;
            c.innerHTML = '';
            var v = document.createElement('video');
            v.controls = true; v.autoplay = true; v.playsInline = true;
            v.style.cssText = 'width:100%;height:100%;position:absolute;top:0;left:0;background:#000;';
            c.appendChild(v);
            return v;
        }

        /* Chaîne de fallback : Video.js → HLS.js → mpegts.js → natif → erreur */
        var FALLBACKS = [];
        (function buildFallbacks() {
            var realType = detectType(streamUrl);

            if (streamType === 'youtube' && youtubeData) {
                FALLBACKS.push({ fn: tryYoutube });
                return;
            }
            // Direct Video.js
            FALLBACKS.push({ fn: function(){ tryVideoJs(streamUrl, realType); } });
            // Proxy Video.js
            FALLBACKS.push({ fn: function(){ tryVideoJs(proxyHlsUrl, 'hls'); } });
            // HLS.js natif direct
            if (realType === 'hls' || realType === 'ts') {
                FALLBACKS.push({ fn: function(){ tryHlsJs(streamUrl); } });
                FALLBACKS.push({ fn: function(){ tryHlsJs(proxyHlsUrl); } });
            }
            // mpegts.js pour TS/FLV
            if (realType === 'ts' || realType === 'hls') {
                FALLBACKS.push({ fn: function(){ tryMpegts(streamUrl); } });
                FALLBACKS.push({ fn: function(){ tryMpegts(proxyHlsUrl); } });
            }
            // DASH.js direct
            if (realType === 'dash') {
                FALLBACKS.push({ fn: function(){ tryDash(streamUrl); } });
                FALLBACKS.push({ fn: function(){ tryDash(proxyHlsUrl); } });
            }
            // Natif navigateur (MP4, etc.)
            FALLBACKS.push({ fn: function(){ tryNative(streamUrl); } });
            FALLBACKS.push({ fn: function(){ tryNative(proxyHlsUrl); } });
            // Tout échoué
            FALLBACKS.push({ fn: showPlayerError });
        })();

        function nextFallback() {
            if (FALLBACKS.length === 0) { showPlayerError(); return; }
            var f = FALLBACKS.shift();
            try { f.fn(); } catch(e) { console.warn('Fallback error:', e); nextFallback(); }
        }

        /* YouTube */
        function tryYoutube() {
            if (youtubeData && (youtubeData.embed_url || youtubeData.type === 'embed')) {
                var iframe = document.createElement('iframe');
                iframe.src = youtubeData.embed_url || 'https://www.youtube.com/embed/' + youtubeData.video_id + '?autoplay=1';
                iframe.allow = 'autoplay; encrypted-media; fullscreen'; iframe.allowFullscreen = true;
                iframe.style.cssText = 'position:absolute;top:0;left:0;width:100%;height:100%;border:0;';
                var c2 = document.getElementById('player-container'); if (c2) { c2.innerHTML=''; c2.appendChild(iframe); }
            } else {
                tryVideoJs(youtubeData ? (youtubeData.url || streamUrl) : streamUrl, 'hls');
            }
        }

        /* Video.js */
        function tryVideoJs(url, type) {
            if (player) { try { player.dispose(); } catch(e){} player = null; }
            var c = document.getElementById('player-container');
            if (!c) { nextFallback(); return; }
            // Restaurer l'élément video si Video.js l'a supprimé
            if (!document.getElementById('video-player')) {
                c.innerHTML = '<video id="video-player" class="video-js vjs-default-skin vjs-big-play-centered" controls autoplay playsinline preload="auto" style="width:100%;height:100%;position:absolute;top:0;left:0;"></video>';
            }
            try {
                player = videojs('video-player', {
                    controls:true, autoplay:true, preload:'auto', fluid:true,
                    techOrder:['html5'],
                    html5:{ vhs:{ overrideNative:true, enableLowInitialPlaylist:true, allowSeeksWithinUnsafeLiveWindow:true },
                             nativeVideoTracks:false, nativeAudioTracks:false, nativeTextTracks:false }
                });
                var mimeMap = { hls:'application/x-mpegURL', dash:'application/dash+xml', mp4:'video/mp4',
                                mp3:'audio/mpeg', aac:'audio/aac', ts:'video/mp2t', audio:'audio/mpeg' };
                player.src({ src: url, type: mimeMap[type] || 'application/x-mpegURL' });
                player.load();
                player.play().catch(function(){});
                player.one('error', function() {
                    console.warn('Video.js error, next fallback');
                    try { player.dispose(); } catch(e){} player = null;
                    nextFallback();
                });
            } catch(e) { nextFallback(); }
        }

        /* HLS.js natif */
        function tryHlsJs(url) {
            if (!window.Hls) { nextFallback(); return; }
            if (!Hls.isSupported()) { nextFallback(); return; }
            var video = makeRawVideo(); if (!video) { nextFallback(); return; }
            var hls = new Hls({
                enableWorker: true, lowLatencyMode: true, backBufferLength: 30,
                maxBufferLength: 30, maxMaxBufferLength: 60,
                fragLoadingMaxRetry: 4, manifestLoadingMaxRetry: 3
            });
            hls.loadSource(url);
            hls.attachMedia(video);
            hls.on(Hls.Events.MANIFEST_PARSED, function() { video.play().catch(function(){}); });
            hls.on(Hls.Events.ERROR, function(ev, d) {
                if (d.fatal) { console.warn('HLS.js fatal', d.type); hls.destroy(); nextFallback(); }
            });
        }

        /* mpegts.js pour TS et streams HTTP-FLV */
        function tryMpegts(url) {
            if (!window.mpegts || !mpegts.isSupported()) { nextFallback(); return; }
            var video = makeRawVideo(); if (!video) { nextFallback(); return; }
            try {
                var mt = mpegts.createPlayer({
                    type: url.includes('.ts') ? 'mpegts' : 'mse',
                    url: url, isLive: true
                });
                mt.attachMediaElement(video);
                mt.load();
                mt.play().catch(function(){});
                mt.on(mpegts.Events.ERROR, function() { mt.destroy(); nextFallback(); });
            } catch(e) { nextFallback(); }
        }

        /* DASH.js */
        function tryDash(url) {
            if (!window.dashjs) { nextFallback(); return; }
            var video = makeRawVideo(); if (!video) { nextFallback(); return; }
            try {
                var dash = dashjs.MediaPlayer().create();
                dash.initialize(video, url, true);
                dash.on(dashjs.MediaPlayer.events.ERROR, function() { dash.reset(); nextFallback(); });
            } catch(e) { nextFallback(); }
        }

        /* Natif navigateur */
        function tryNative(url) {
            var video = makeRawVideo(); if (!video) { nextFallback(); return; }
            video.src = url;
            video.load();
            video.play().catch(function(){});
            video.addEventListener('error', function() { nextFallback(); }, { once: true });
        }

        /* Contrôles volume vidéo */
        window.changeVolume = function(d) {
            var v = document.querySelector('#player-container video');
            if (v) { v.volume = Math.max(0, Math.min(1, v.volume + d)); showNotification('Volume : ' + Math.round(v.volume*100) + '%', 'info'); }
            else if (player) { player.volume(Math.max(0, Math.min(1, player.volume() + d))); }
        };
        window.toggleMute = function() {
            var v = document.querySelector('#player-container video');
            if (v) { v.muted = !v.muted; showNotification(v.muted ? 'Son coupé' : 'Son activé', 'info'); }
            else if (player) { player.muted(!player.muted()); }
        };

        // Démarrer la chaîne de fallback
        if (document.readyState === 'loading') { document.addEventListener('DOMContentLoaded', nextFallback); }
        else { nextFallback(); }
    }
})();

    // ── EPG reminder ────────────────────────────────────────────────────────
    async function toggleEPGReminder(btn) {
        if ('Notification' in window && Notification.permission === 'default') {
            await Notification.requestPermission();
        }
        try {
            var r = await fetch('/api/events/upcoming');
            var data = await r.json();
            var allEvents = Object.values(data).flat();
            var ch = btn.dataset.channel || '';
            var ev = allEvents.find(function(e){ return e.channel && e.channel.toLowerCase().indexOf(ch.toLowerCase().split(' ')[0]) !== -1; });
            if (!ev) { showNotification('Aucun programme prévu pour cette chaîne aujourd\'hui', 'info'); return; }
            var r2 = await fetch('/api/events/' + ev.id + '/remind', { method: 'POST', credentials: 'include' });
            var d2 = await r2.json();
            btn.innerHTML = d2.action === 'added' ? '🔔 Rappel activé ✅' : '🔔 Me rappeler';
            showNotification(d2.action === 'added' ? 'Rappel activé !' : 'Rappel supprimé', d2.action === 'added' ? 'success' : 'info');
        } catch(e) { showNotification('Erreur réseau', 'error'); }
    }

    function downloadStream(url, name) {
        if (!url) { showNotification('URL introuvable', 'error'); return; }
        var fd = new FormData();
        fd.append('url', url);
        fd.append('filename', (name||'stream').replace(/[^a-zA-Z0-9]/g,'_'));
        fd.append('duration', '3600');
        fetch('/api/record/start', { method:'POST', body:fd, credentials:'include' })
            .then(function(r){ return r.json(); })
            .then(function(d){ showNotification(d.success ? '✅ Enregistrement lancé' : 'ℹ️ Utilisez VLC pour enregistrer', d.success ? 'success' : 'info'); })
            .catch(function(){ showNotification('ℹ️ Utilisez VLC pour enregistrer ce flux', 'info'); });
    }
</script>
{% endblock %}'''

    # Template pour regarder un stream utilisateur
    WATCH_USER_TEMPLATE = '''{% extends "base.html" %}

{% block title %}{{ stream.title }} - {{ app_name }}{% endblock %}

{% block content %}
<div class="grid grid-cols-1 lg:grid-cols-4 gap-6">
    <!-- Lecteur vidéo -->
    <div class="lg:col-span-3 space-y-4">
        <div class="bg-black rounded-2xl overflow-hidden shadow-2xl">
            <div class="video-container">
                <video id="video-player" class="video-js vjs-default-skin vjs-big-play-centered" controls autoplay playsinline preload="auto"></video>
            </div>
        </div>

        <!-- Informations du stream -->
        <div class="bg-white dark:bg-gray-800 rounded-2xl p-6 shadow border border-gray-100 dark:border-gray-700">
            <div class="flex items-start justify-between gap-4 flex-wrap mb-4">
                <div>
                    <h1 class="text-2xl font-bold">{{ stream.title }}</h1>
                    {% if stream.description %}
                    <p class="text-gray-500 dark:text-gray-400 mt-1 text-sm">{{ stream.description }}</p>
                    {% endif %}
                    <div class="flex flex-wrap gap-2 text-sm text-gray-500 mt-2">
                        <span class="bg-gray-100 dark:bg-gray-700 px-3 py-1 rounded-full">
                            <i class="fas fa-eye mr-1"></i><span id="viewer-count">{{ stream.viewer_count }}</span> spectateurs
                        </span>
                        <span class="bg-gray-100 dark:bg-gray-700 px-3 py-1 rounded-full">
                            <i class="fas fa-tag mr-1"></i>{{ stream.category }}
                        </span>
                        <span class="bg-gray-100 dark:bg-gray-700 px-3 py-1 rounded-full">
                            <i class="fas fa-heart text-red-400 mr-1"></i><span id="like-count">{{ stream.like_count }}</span> likes
                        </span>
                        <span class="bg-gray-100 dark:bg-gray-700 px-3 py-1 rounded-full">
                            <i class="fas fa-calendar mr-1"></i>{{ stream.started_at.strftime('%H:%M') if stream.started_at else 'Maintenant' }}
                        </span>
                    </div>
                    {% if stream.tags %}
                    <div class="flex flex-wrap gap-1 mt-2">
                        {% for tag in stream.tags.split(',') %}
                        <span class="bg-red-100 dark:bg-red-900/20 text-red-600 text-xs px-2 py-0.5 rounded-full">{{ tag.strip() }}</span>
                        {% endfor %}
                    </div>
                    {% endif %}
                </div>
                <span class="bg-red-600 text-white px-4 py-1.5 rounded-full text-sm font-semibold flex items-center gap-1 live-badge self-start">
                    <i class="fas fa-circle text-xs"></i> EN DIRECT
                </span>
            </div>
            <div class="flex gap-2 flex-wrap">
                <button onclick="likeStream()" class="bg-red-600 hover:bg-red-700 text-white px-5 py-2 rounded-lg transition flex items-center gap-2 shadow">
                    <i class="fas fa-heart"></i> Liker
                </button>
                <button onclick="addToFavorites('{{ stream.id }}', 'user', event)" class="bg-yellow-400 hover:bg-yellow-500 text-white px-5 py-2 rounded-lg transition flex items-center gap-2 shadow">
                    <i class="fas fa-star"></i> Favori
                </button>
                <button onclick="document.getElementById('report-modal').classList.remove('hidden')" class="bg-gray-200 dark:bg-gray-600 hover:bg-gray-300 dark:hover:bg-gray-500 px-5 py-2 rounded-lg transition flex items-center gap-2">
                    <i class="fas fa-flag"></i> Signaler
                </button>
            </div>
        </div>
    </div>

    <!-- Chat -->
    <div class="lg:col-span-1">
        <div class="bg-white dark:bg-gray-800 rounded-2xl shadow border border-gray-100 dark:border-gray-700 flex flex-col" style="height: calc(100vh - 6rem); position: sticky; top: 5rem;">
            <div class="p-4 border-b border-gray-100 dark:border-gray-700 flex items-center justify-between">
                <h3 class="font-bold flex items-center gap-2">
                    <i class="fas fa-comments text-red-500"></i> Chat en direct
                </h3>
                <span class="text-xs bg-yellow-100 text-yellow-600 px-2 py-0.5 rounded-full" id="ws-status">Connexion...</span>
            </div>
            <div id="comments-list" class="flex-1 overflow-y-auto p-3 space-y-2 custom-scrollbar">
                <div class="text-center text-gray-400 py-8 text-sm">
                    <i class="fas fa-comments text-3xl mb-2 block opacity-30"></i>
                    Chargement des messages...
                </div>
            </div>
            <div class="p-3 border-t border-gray-100 dark:border-gray-700">
                <div class="flex gap-2">
                    <input type="text" id="comment-input"
                           placeholder="Votre message..."
                           maxlength="{{ max_comment_length }}"
                           class="flex-1 px-3 py-2 border border-gray-200 dark:border-gray-600 rounded-xl text-sm bg-white dark:bg-gray-700 focus:outline-none focus:ring-2 focus:ring-red-500">
                    <button onclick="sendComment()" class="bg-red-600 hover:bg-red-700 text-white px-3 py-2 rounded-xl transition">
                        <i class="fas fa-paper-plane text-sm"></i>
                    </button>
                </div>
                <p class="text-xs text-gray-400 mt-1.5">Limite : {{ comments_per_minute }} messages/minute</p>
            </div>
        </div>
    </div>
</div>

<!-- Modal de signalement -->
<div id="report-modal" class="hidden fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50 p-4">
    <div class="bg-white dark:bg-gray-800 rounded-2xl p-6 max-w-sm w-full shadow-2xl">
        <h3 class="text-xl font-bold mb-4">🚩 Signaler ce stream</h3>
        <select id="report-reason" class="w-full px-3 py-2.5 border border-gray-200 dark:border-gray-600 rounded-xl mb-4 bg-white dark:bg-gray-700 focus:outline-none focus:ring-2 focus:ring-red-500">
            <option value="Contenu inapproprié">Contenu inapproprié</option>
            <option value="Harcèlement">Harcèlement</option>
            <option value="Violence">Violence</option>
            <option value="Spam">Spam</option>
            <option value="Droits d'auteur">Droits d'auteur</option>
            <option value="Autre">Autre</option>
        </select>
        <div class="flex gap-2">
            <button onclick="reportStream()" class="flex-1 bg-red-600 hover:bg-red-700 text-white py-2.5 rounded-xl transition font-medium">Signaler</button>
            <button onclick="document.getElementById('report-modal').classList.add('hidden')" class="flex-1 bg-gray-200 dark:bg-gray-600 hover:bg-gray-300 py-2.5 rounded-xl transition">Annuler</button>
        </div>
    </div>
</div>

<!-- Scripts -->
<script>
(function() {
    var streamUrl = '{{ stream.stream_url|e }}';
    var streamId  = '{{ stream.id }}';
    var player;

    // ── Lecteur vidéo ────────────────────────────────────────
    function initPlayer() {
        player = videojs('video-player', {
            controls: true, autoplay: true, preload: 'auto', fluid: true,
            html5: { hls: { enableLowInitialPlaylist: true, smoothQualityChange: true, overrideNative: true } }
        });
        if (streamUrl) {
            player.src({ src: streamUrl, type: 'application/x-mpegURL' });
        }
    }

    // ── WebSocket chat ───────────────────────────────────────
    var wsProto = location.protocol === 'https:' ? 'wss' : 'ws';
    var ws = new WebSocket(wsProto + '://' + location.host + '/ws/' + streamId);
    var statusEl = document.getElementById('ws-status');

    ws.onopen  = function() {
        if (statusEl) { statusEl.textContent = 'Connecté'; statusEl.className = 'text-xs bg-green-100 text-green-600 px-2 py-0.5 rounded-full'; }
    };
    ws.onclose = function() {
        if (statusEl) { statusEl.textContent = 'Déconnecté'; statusEl.className = 'text-xs bg-red-100 text-red-600 px-2 py-0.5 rounded-full'; }
    };
    ws.onmessage = function(e) {
        var data = JSON.parse(e.data);
        if (data.type === 'comment' || data.type === 'history') {
            addComment(data);
        } else if (data.type === 'viewer_count') {
            var vc = document.getElementById('viewer-count');
            if (vc) vc.textContent = data.count;
        } else if (data.type === 'error') {
            showNotification(data.message, 'error');
        }
    };

    function escapeHtml(s) {
        return String(s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    function addComment(c) {
        var list = document.getElementById('comments-list');
        if (!list) return;
        var empty = list.querySelector('.text-center');
        if (empty) empty.remove();
        var div = document.createElement('div');
        div.className = 'bg-gray-50 dark:bg-gray-700/50 rounded-xl p-2.5 text-sm';
        var time = new Date(c.created_at).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'});
        div.innerHTML = '<div class="flex justify-between text-xs text-gray-400 mb-1">' +
            '<span class="font-semibold text-gray-600 dark:text-gray-300">&#128100; ' + escapeHtml(c.visitor_id) + '</span>' +
            '<span>' + time + '</span></div>' +
            '<p class="break-words text-gray-800 dark:text-gray-200">' + escapeHtml(c.content) + '</p>';
        list.appendChild(div);
        list.scrollTop = list.scrollHeight;
    }

    window.sendComment = function() {
        var input = document.getElementById('comment-input');
        var content = input ? input.value.trim() : '';
        if (content && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: 'comment', content: content }));
            input.value = '';
        }
    };

    window.likeStream = async function() {
        var r = await fetch('/api/streams/' + streamId + '/like', { method: 'POST' });
        var d = await r.json();
        var lc = document.getElementById('like-count');
        if (lc) lc.textContent = d.likes;
        showNotification('❤️ Stream liké !', 'success');
    };

    window.reportStream = async function() {
        var reason = document.getElementById('report-reason');
        var fd = new FormData();
        fd.append('reason', reason ? reason.value : '');
        var r = await fetch('/api/streams/' + streamId + '/report', { method: 'POST', body: fd });
        if (r.ok) {
            showNotification('Signalement envoyé. Merci !', 'success');
            var modal = document.getElementById('report-modal');
            if (modal) modal.classList.add('hidden');
        }
    };

    var commentInput = document.getElementById('comment-input');
    if (commentInput) {
        commentInput.addEventListener('keydown', function(e) {
            if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); window.sendComment(); }
        });
    }

    // Ping WebSocket toutes les 30s
    setInterval(function() {
        if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'ping' }));
    }, 30000);

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initPlayer);
    } else {
        initPlayer();
    }
})();
</script>
{% endblock %}'''

    # Template pour créer un live
    GO_LIVE_TEMPLATE = '''{% extends "base.html" %}

{% block title %}Go Live - {{ app_name }}{% endblock %}

{% block content %}
<div class="max-w-2xl mx-auto">
    <div class="text-center mb-8">
        <div class="w-20 h-20 bg-gradient-to-br from-red-500 to-red-700 rounded-full flex items-center justify-center mx-auto mb-4 shadow-xl">
            <i class="fas fa-video text-white text-3xl"></i>
        </div>
        <h1 class="text-3xl font-bold">Démarrer votre live</h1>
        <p class="text-gray-500 dark:text-gray-400 mt-2">Partagez votre stream avec la communauté en quelques secondes</p>
    </div>

    <div class="bg-white dark:bg-gray-800 rounded-2xl shadow-xl p-8 border border-gray-100 dark:border-gray-700">
        <!-- Formulaire de création -->
        <div id="stream-form-container">
            <div class="space-y-5">
                <div>
                    <label class="block text-sm font-semibold mb-2">
                        Titre du live <span class="text-red-500">*</span>
                    </label>
                    <input type="text" id="title" required maxlength="100"
                           class="w-full px-4 py-3 border border-gray-200 dark:border-gray-600 rounded-xl bg-white dark:bg-gray-700 focus:outline-none focus:ring-2 focus:ring-red-500 transition"
                           placeholder="Ex: Soirée gaming, Discussion politique, Concert live...">
                </div>

                <div>
                    <label class="block text-sm font-semibold mb-2">Description</label>
                    <textarea id="description" rows="3" maxlength="1000"
                              class="w-full px-4 py-3 border border-gray-200 dark:border-gray-600 rounded-xl bg-white dark:bg-gray-700 focus:outline-none focus:ring-2 focus:ring-red-500 transition resize-none"
                              placeholder="Décrivez votre live en quelques mots..."></textarea>
                </div>

                <div>
                    <label class="block text-sm font-semibold mb-2">
                        Catégorie <span class="text-red-500">*</span>
                    </label>
                    <select id="category" required
                            class="w-full px-4 py-3 border border-gray-200 dark:border-gray-600 rounded-xl bg-white dark:bg-gray-700 focus:outline-none focus:ring-2 focus:ring-red-500 transition">
                        <option value="">Choisissez une catégorie...</option>
                        {% for cat in categories %}
                        <option value="{{ cat.id }}">{{ cat.icon }} {{ cat.name }}</option>
                        {% endfor %}
                    </select>
                </div>

                <div>
                    <label class="block text-sm font-semibold mb-2">
                        Tags <span class="text-gray-400 font-normal">(séparés par des virgules)</span>
                    </label>
                    <input type="text" id="tags"
                           class="w-full px-4 py-3 border border-gray-200 dark:border-gray-600 rounded-xl bg-white dark:bg-gray-700 focus:outline-none focus:ring-2 focus:ring-red-500 transition"
                           placeholder="gaming, fun, live, music...">
                </div>

                <button id="submit-btn" onclick="createStream()"
                        class="w-full bg-gradient-to-r from-red-600 to-red-700 hover:from-red-700 hover:to-red-800 text-white py-3.5 rounded-xl font-semibold transition shadow-lg hover:shadow-xl flex items-center justify-center gap-2">
                    <i class="fas fa-video"></i> Commencer le live
                </button>
            </div>
        </div>

        <!-- Confirmation de création -->
        <div id="success-panel" class="hidden text-center">
            <div class="w-20 h-20 bg-green-100 dark:bg-green-900/30 rounded-full flex items-center justify-center mx-auto mb-5">
                <i class="fas fa-check-circle text-green-600 text-4xl"></i>
            </div>
            <h3 class="text-2xl font-bold mb-2">Live créé avec succès !</h3>
            <p class="text-gray-500 dark:text-gray-400 mb-5">Partagez ce lien pour inviter des spectateurs</p>
            <div class="bg-gray-100 dark:bg-gray-700 p-3 rounded-xl font-mono text-sm break-all mb-4" id="stream-url-display"></div>
            <div class="flex gap-3 justify-center flex-wrap">
                <a id="watch-link" href="#" class="bg-red-600 hover:bg-red-700 text-white px-6 py-2.5 rounded-xl transition font-medium flex items-center gap-2">
                    <i class="fas fa-play"></i> Regarder le live
                </a>
                <button onclick="copyStreamUrl()" class="bg-gray-200 dark:bg-gray-600 hover:bg-gray-300 px-6 py-2.5 rounded-xl transition flex items-center gap-2">
                    <i class="fas fa-copy"></i> Copier le lien
                </button>
            </div>
        </div>
    </div>
</div>

<script>
    async function createStream() {
        var title = document.getElementById('title').value.trim();
        var category = document.getElementById('category').value;

        if (!title) {
            showNotification('Le titre est obligatoire', 'error');
            return;
        }
        if (!category) {
            showNotification('Choisissez une catégorie', 'error');
            return;
        }

        var btn = document.getElementById('submit-btn');
        btn.disabled = true;
        btn.innerHTML = '<i class="fas fa-spinner fa-spin mr-2"></i> Création en cours...';

        var formData = new FormData();
        formData.append('title', title);
        formData.append('category', category);
        formData.append('description', document.getElementById('description').value);
        formData.append('tags', document.getElementById('tags').value);

        try {
            var response = await fetch('/api/streams/create', {
                method: 'POST',
                body: formData
            });
            var data = await response.json();

            if (data.success) {
                // Démarrer le stream
                await fetch(`/api/streams/${data.stream_id}/start`, { method: 'POST' });

                var streamUrl = `${location.origin}/watch/user/${data.stream_id}`;
                document.getElementById('stream-url-display').textContent = streamUrl;
                document.getElementById('watch-link').href = `/watch/user/${data.stream_id}`;

                document.getElementById('stream-form-container').classList.add('hidden');
                document.getElementById('success-panel').classList.remove('hidden');

                window._streamUrl = streamUrl;
            } else {
                showNotification(data.error || 'Erreur lors de la création', 'error');
                btn.disabled = false;
                btn.innerHTML = '<i class="fas fa-video mr-2"></i> Commencer le live';
            }
        } catch (error) {
            showNotification('Erreur réseau', 'error');
            btn.disabled = false;
            btn.innerHTML = '<i class="fas fa-video mr-2"></i> Commencer le live';
        }
    }

    function copyStreamUrl() {
        if (window._streamUrl) {
            navigator.clipboard.writeText(window._streamUrl).then(function() {
                showNotification('Lien copié !', 'success');
            });
        }
    }
</script>
{% endblock %}'''

    # Template de recherche
    SEARCH_TEMPLATE = '''{% extends "base.html" %}

{% block title %}Recherche{% if query %} : {{ query }}{% endif %} - {{ app_name }}{% endblock %}

{% block content %}
<div class="max-w-6xl mx-auto">
    <h1 class="text-3xl font-bold mb-8">🔍 Recherche</h1>

    <form class="mb-10">
        <div class="flex gap-2">
            <input type="text" name="q" value="{{ query }}" autofocus
                   placeholder="Rechercher une chaîne, un live, une catégorie..."
                   class="flex-1 px-5 py-3.5 border border-gray-200 dark:border-gray-600 rounded-xl bg-white dark:bg-gray-800 focus:outline-none focus:ring-2 focus:ring-red-500 text-lg">
            <button type="submit" class="bg-red-600 hover:bg-red-700 text-white px-6 py-3.5 rounded-xl transition shadow font-medium">
                <i class="fas fa-search mr-2"></i> Chercher
            </button>
        </div>
    </form>

    {% if query %}
        {% set total = external_results|length + iptv_results|length + user_results|length %}
        <p class="text-gray-500 dark:text-gray-400 mb-6">
            {{ total }} résultat{% if total > 1 %}s{% endif %} pour « <strong>{{ query }}</strong> »
        </p>

        <!-- Résultats des streams utilisateur -->
        {% if user_results %}
        <section class="mb-10">
            <h2 class="text-xl font-bold mb-4 flex items-center gap-2">
                <span class="w-7 h-7 bg-red-100 dark:bg-red-900/30 rounded-lg flex items-center justify-center text-sm">🔴</span>
                Lives en direct ({{ user_results|length }})
            </h2>
            <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
                {% for stream in user_results %}
                <a href="/watch/user/{{ stream.id }}" class="stream-card bg-white dark:bg-gray-800 rounded-xl overflow-hidden shadow border border-gray-100 dark:border-gray-700">
                    <div class="relative pb-9/16 bg-gray-200 dark:bg-gray-700">
                        {% if stream.thumbnail %}
                        <img src="{{ stream.thumbnail }}" class="absolute inset-0 w-full h-full object-cover">
                        {% else %}
                        <div class="absolute inset-0 flex items-center justify-center">
                            <i class="fas fa-user text-3xl text-gray-400"></i>
                        </div>
                        {% endif %}
                        <span class="absolute top-2 left-2 bg-red-600 text-white text-xs px-2 py-0.5 rounded-full live-badge">🔴 LIVE</span>
                    </div>
                    <div class="p-3">
                        <h3 class="font-semibold truncate">{{ stream.title }}</h3>
                        <p class="text-xs text-gray-400 mt-0.5">
                            <i class="fas fa-eye mr-1"></i>{{ stream.viewer_count }} spectateurs
                        </p>
                    </div>
                </a>
                {% endfor %}
            </div>
        </section>
        {% endif %}

        <!-- Résultats IPTV -->
        {% if iptv_results %}
        <section class="mb-10">
            <h2 class="text-xl font-bold mb-4 flex items-center gap-2">
                <span class="w-7 h-7 bg-indigo-100 dark:bg-indigo-900/30 rounded-lg flex items-center justify-center text-sm">🌍</span>
                Chaînes IPTV ({{ iptv_results|length }})
            </h2>
            <div class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-3">
                {% for channel in iptv_results %}
                <a href="/watch/iptv/{{ channel.id }}" class="stream-card bg-white dark:bg-gray-800 rounded-xl overflow-hidden shadow border border-gray-100 dark:border-gray-700">
                    <div class="relative h-16 bg-gray-100 dark:bg-gray-700 flex items-center justify-center">
                        {% if channel.logo %}
                        <img src="{{ channel.logo }}" class="h-full w-full object-contain p-2" loading="lazy">
                        {% else %}
                        <i class="fas fa-tv text-2xl text-gray-400"></i>
                        {% endif %}
                        {% if channel.stream_type == 'youtube' %}
                        <span class="absolute top-1 right-1 bg-red-600 text-white text-xs px-1 rounded">▶️</span>
                        {% endif %}
                    </div>
                    <div class="p-2">
                        <h3 class="font-medium text-xs truncate">{{ channel.name }}</h3>
                        <p class="text-xs text-gray-400">{{ channel.country }}</p>
                    </div>
                </a>
                {% endfor %}
            </div>
        </section>
        {% endif %}

        <!-- Résultats externes -->
        {% if external_results %}
        <section class="mb-10">
            <h2 class="text-xl font-bold mb-4 flex items-center gap-2">
                <span class="w-7 h-7 bg-red-100 dark:bg-red-900/30 rounded-lg flex items-center justify-center text-sm">📡</span>
                Chaînes TV & Radio ({{ external_results|length }})
            </h2>
            <div class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-3">
                {% for stream in external_results %}
                <a href="/watch/external/{{ stream.id }}" class="stream-card bg-white dark:bg-gray-800 rounded-xl overflow-hidden shadow border border-gray-100 dark:border-gray-700">
                    <div class="relative h-16 bg-gray-50 dark:bg-gray-700 flex items-center justify-center">
                        {% if stream.logo %}
                        <img src="{{ stream.logo }}" class="h-full w-full object-contain p-2" loading="lazy">
                        {% else %}
                        <i class="fas fa-tv text-2xl text-gray-400"></i>
                        {% endif %}
                    </div>
                    <div class="p-2">
                        <h3 class="font-medium text-xs truncate">{{ stream.title }}</h3>
                        <p class="text-xs text-gray-400">{{ stream.country }} · {{ stream.quality }}</p>
                    </div>
                </a>
                {% endfor %}
            </div>
        </section>
        {% endif %}

        <!-- Aucun résultat -->
        {% if total == 0 %}
        <div class="text-center py-20">
            <div class="text-7xl mb-4">🔍</div>
            <h3 class="text-xl font-bold text-gray-500 dark:text-gray-400 mb-2">Aucun résultat trouvé</h3>
            <p class="text-gray-400">Essayez un autre terme de recherche</p>
        </div>
        {% endif %}
    {% else %}
        <!-- Page de recherche vide -->
        <div class="text-center py-20 text-gray-400">
            <div class="text-7xl mb-4">🔍</div>
            <p class="text-lg">Tapez un terme pour commencer la recherche</p>
            <div class="mt-8 flex flex-wrap gap-2 justify-center">
                <span class="bg-gray-200 dark:bg-gray-700 px-3 py-1.5 rounded-full text-sm">⚽ Sports</span>
                <span class="bg-gray-200 dark:bg-gray-700 px-3 py-1.5 rounded-full text-sm">📰 News</span>
                <span class="bg-gray-200 dark:bg-gray-700 px-3 py-1.5 rounded-full text-sm">🎬 Films</span>
                <span class="bg-gray-200 dark:bg-gray-700 px-3 py-1.5 rounded-full text-sm">🎵 Musique</span>
                <span class="bg-gray-200 dark:bg-gray-700 px-3 py-1.5 rounded-full text-sm">🌍 IPTV</span>
                <span class="bg-gray-200 dark:bg-gray-700 px-3 py-1.5 rounded-full text-sm">▶️ YouTube</span>
            </div>
        </div>
    {% endif %}
</div>
{% endblock %}'''

    # Template playlist
    PLAYLIST_TEMPLATE = '''{% extends "base.html" %}

{% block title %}{{ playlist.display_name }} - {{ app_name }}{% endblock %}

{% block content %}
<div class="mb-6">
    <a href="/" class="inline-flex items-center gap-2 text-red-600 hover:text-red-700 transition mb-4 text-sm font-medium">
        <i class="fas fa-arrow-left"></i> Retour à l'accueil
    </a>
    <div class="flex items-center justify-between gap-4 flex-wrap">
        <div>
            <h1 class="text-3xl font-bold">{{ playlist.display_name }}</h1>
            <p class="text-gray-500 dark:text-gray-400 mt-1">
                {{ channels|length }} chaînes · Type: 
                <span class="capitalize">{{ playlist.playlist_type }}</span>
                {% if playlist.country and playlist.country != 'INT' %}
                · {{ playlist.country }}
                {% endif %}
            </p>
        </div>
        {% if playlist.last_sync %}
        <span class="text-sm text-gray-400 bg-gray-100 dark:bg-gray-700 px-3 py-1.5 rounded-full">
            <i class="fas fa-sync-alt mr-1"></i>MAJ : {{ playlist.last_sync.strftime('%d/%m/%Y %H:%M') }}
        </span>
        {% endif %}
    </div>
</div>

<div class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-3">
    {% for channel in channels %}
    <a href="/watch/iptv/{{ channel.id }}" class="stream-card bg-white dark:bg-gray-800 rounded-xl overflow-hidden hover:shadow-xl border border-gray-100 dark:border-gray-700">
        <div class="relative h-20 bg-gray-100 dark:bg-gray-700 flex items-center justify-center">
            {% if channel.logo %}
            <img src="{{ channel.logo }}" alt="{{ channel.name }}" class="h-full w-full object-contain p-2" loading="lazy">
            {% else %}
            <i class="fas fa-tv text-3xl text-gray-400"></i>
            {% endif %}
            <span class="absolute top-1 left-1 bg-red-600 text-white text-xs px-1.5 py-0.5 rounded live-badge">LIVE</span>
            {% if channel.stream_type == 'youtube' %}
            <span class="absolute top-1 right-1 bg-red-700 text-white text-xs px-1.5 py-0.5 rounded">▶️</span>
            {% endif %}
        </div>
        <div class="p-2.5">
            <h3 class="font-semibold text-xs truncate">{{ channel.name }}</h3>
            <div class="flex items-center justify-between mt-1">
                <span class="text-xs text-gray-400">{{ channel.country }}</span>
                <button onclick="addToFavorites('{{ channel.id }}', 'iptv', event)" class="text-yellow-400 hover:text-yellow-500 transition">
                    <i class="far fa-star text-xs"></i>
                </button>
            </div>
        </div>
    </a>
    {% else %}
    <div class="col-span-full text-center py-16 text-gray-400">
        <i class="fas fa-tv text-5xl mb-4 block opacity-20"></i>
        <p>Aucune chaîne dans cette playlist.</p>
        <a href="/admin/dashboard" class="text-red-500 hover:underline mt-2 inline-block">Lancer une synchronisation</a>
    </div>
    {% endfor %}
</div>
{% endblock %}'''

    # Template admin login
    ADMIN_LOGIN_TEMPLATE = '''{% extends "base.html" %}

{% block title %}Administration - {{ app_name }}{% endblock %}

{% block content %}
<div class="min-h-[80vh] flex items-center justify-center py-12">
    <div class="max-w-md w-full">
        <div class="text-center mb-8">
            <div class="w-20 h-20 bg-gradient-to-br from-gray-700 to-gray-900 rounded-2xl flex items-center justify-center mx-auto mb-4 shadow-xl">
                <i class="fas fa-shield-alt text-white text-3xl"></i>
            </div>
            <h2 class="text-3xl font-bold">Administration</h2>
            <p class="text-gray-500 dark:text-gray-400 mt-1">Espace réservé aux administrateurs</p>
        </div>

        {% if error %}
        <div class="bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 text-red-700 dark:text-red-400 p-4 rounded-xl mb-6 flex items-center gap-2">
            <i class="fas fa-exclamation-circle"></i> {{ error }}
        </div>
        {% endif %}

        <div class="bg-white dark:bg-gray-800 rounded-2xl shadow-xl p-8 border border-gray-100 dark:border-gray-700">
            <form action="/admin/login" method="POST" class="space-y-5">
                <div>
                    <label class="block text-sm font-semibold mb-2">Nom d'utilisateur</label>
                    <input type="text" name="username" required autocomplete="username"
                           class="w-full px-4 py-3 border border-gray-200 dark:border-gray-600 rounded-xl bg-white dark:bg-gray-700 focus:outline-none focus:ring-2 focus:ring-gray-500 transition">
                </div>
                <div>
                    <label class="block text-sm font-semibold mb-2">Mot de passe</label>
                    <input type="password" name="password" required autocomplete="current-password"
                           class="w-full px-4 py-3 border border-gray-200 dark:border-gray-600 rounded-xl bg-white dark:bg-gray-700 focus:outline-none focus:ring-2 focus:ring-gray-500 transition">
                </div>
                <button type="submit" class="w-full bg-gray-900 dark:bg-gray-100 dark:text-gray-900 hover:bg-gray-700 dark:hover:bg-gray-200 text-white py-3.5 rounded-xl font-semibold transition shadow-lg flex items-center justify-center gap-2">
                    <i class="fas fa-lock"></i> Se connecter
                </button>
            </form>
        </div>

        <div class="text-center mt-5">
            <a href="/" class="text-sm text-gray-400 hover:text-red-600 transition">
                <i class="fas fa-arrow-left mr-1"></i> Retour à l'accueil
            </a>
        </div>
    </div>
</div>
{% endblock %}'''

    # Template admin dashboard
    ADMIN_TEMPLATE = '''{% extends "base.html" %}
{% block title %}Dashboard Admin - {{ app_name }}{% endblock %}

{% block content %}
<style>
.tab-btn{padding:10px 18px;font-size:13px;font-weight:600;white-space:nowrap;cursor:pointer;border:none;border-bottom:3px solid transparent;background:transparent;color:#6b7280;border-radius:8px 8px 0 0;transition:all .15s}
.tab-btn:hover{color:#374151}
.tab-btn.active{border-bottom-color:#dc2626;color:#dc2626;background:#fff}
.dark .tab-btn.active{background:#1f2937}
.tab-panel{display:none}
.tab-panel.active{display:block}
.stat-card{background:#fff;border-radius:14px;padding:14px;box-shadow:0 1px 3px rgba(0,0,0,.07);border:1px solid #e5e7eb;transition:all .2s}
.stat-card:hover{box-shadow:0 4px 12px rgba(0,0,0,.1);transform:translateY(-1px)}
.dark .stat-card{background:#1f2937;border-color:#374151}
.pulse-dot{width:8px;height:8px;border-radius:50%;background:#22c55e;display:inline-block;animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
</style>

<div class="max-w-7xl mx-auto space-y-6">

<!-- Header -->
<div class="flex items-center justify-between flex-wrap gap-4">
    <div>
        <h1 class="text-2xl font-bold flex items-center gap-3">
            <span class="w-9 h-9 bg-gradient-to-br from-red-600 to-orange-500 rounded-xl flex items-center justify-center text-white shadow">⚙</span>
            Dashboard Admin
        </h1>
        <p class="text-gray-500 dark:text-gray-400 mt-0.5 text-sm flex items-center gap-2">
            <span class="pulse-dot"></span>
            Connecté : <strong>{{ user.username }}</strong>
            {% if user.is_owner %}<span class="text-xs bg-purple-100 text-purple-600 px-2 py-0.5 rounded-full ml-1">👑 Propriétaire</span>{% endif %}
            {% if is_syncing %}<span class="text-xs text-yellow-600 animate-pulse ml-2">🔄 Sync IPTV...</span>
            {% elif last_sync %}<span class="text-xs text-gray-400 ml-2">Sync: {{ last_sync.strftime('%d/%m %H:%M') }}</span>{% endif %}
        </p>
    </div>
    <div class="flex gap-2 flex-wrap">
        <button type="button" onclick="syncIPTV()" class="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded-xl text-sm font-medium transition flex items-center gap-1.5">🔄 Sync IPTV</button>
        <button type="button" onclick="adminAction('/api/admin/epg/seed','Régénérer tous les événements EPG ?')" class="bg-purple-600 hover:bg-purple-700 text-white px-4 py-2 rounded-xl text-sm font-medium transition flex items-center gap-1.5">📅 Reset EPG</button>
        <a href="/api/admin/config/export" class="bg-gray-200 dark:bg-gray-700 hover:bg-gray-300 dark:hover:bg-gray-600 px-4 py-2 rounded-xl text-sm font-medium transition">⬇ .env</a>
        <a href="/" class="bg-gray-200 dark:bg-gray-700 hover:bg-gray-300 dark:hover:bg-gray-600 px-4 py-2 rounded-xl text-sm font-medium transition">🏠 Site</a>
        <a href="/admin/logout" class="bg-red-100 hover:bg-red-200 text-red-600 px-4 py-2 rounded-xl text-sm font-medium transition">🚪 Déconnexion</a>
    </div>
</div>

<!-- ═══ CARTE UTILISATEURS ACTIFS EN TEMPS RÉEL ═══ -->
<div class="bg-gradient-to-br from-gray-900 to-gray-800 rounded-2xl p-5 border border-gray-700 shadow-xl mb-4">
    <div class="flex items-center justify-between mb-4">
        <div class="flex items-center gap-3">
            <div class="w-3 h-3 bg-green-400 rounded-full animate-ping absolute"></div>
            <div class="w-3 h-3 bg-green-400 rounded-full relative ml-0"></div>
            <h2 class="text-white font-bold text-lg">Utilisateurs actifs en ce moment</h2>
        </div>
        <div class="flex items-center gap-2">
            <span id="ws-badge" class="text-xs px-2 py-1 rounded-full bg-gray-700 text-gray-400">⚡ Connexion...</span>
            <span class="text-xs text-gray-500" id="last-update">--</span>
        </div>
    </div>

    <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
        <!-- Compteur principal -->
        <div class="bg-gray-800 rounded-xl p-5 flex flex-col items-center justify-center border border-gray-700">
            <div id="active-count" class="text-6xl font-black text-green-400 tabular-nums transition-all duration-500">0</div>
            <div class="text-gray-400 text-sm mt-2 font-medium">utilisateurs en ligne</div>
            <div class="text-gray-500 text-xs mt-1">(actifs dans les 5 dernières minutes)</div>
        </div>

        <!-- Graphique historique (sparkline) -->
        <div class="bg-gray-800 rounded-xl p-4 border border-gray-700">
            <div class="text-gray-400 text-xs font-semibold mb-3 uppercase tracking-wider">Activité (dernières 60s)</div>
            <div class="flex items-end gap-1 h-16" id="sparkline">
                <!-- Les barres sont générées par JS -->
            </div>
            <div class="flex justify-between text-xs text-gray-600 mt-1">
                <span>-60s</span><span>maintenant</span>
            </div>
        </div>

        <!-- Pages actives -->
        <div class="bg-gray-800 rounded-xl p-4 border border-gray-700">
            <div class="text-gray-400 text-xs font-semibold mb-3 uppercase tracking-wider">Pages visitées</div>
            <div id="top-pages" class="space-y-2">
                <div class="text-gray-600 text-xs">Chargement...</div>
            </div>
        </div>
    </div>
</div>

<!-- ═══ STATISTIQUES HISTORIQUES — Graphique ═══ -->
<div class="bg-white dark:bg-gray-800 rounded-2xl border border-gray-200 dark:border-gray-700 shadow p-5 mb-4">
    <div class="flex flex-wrap items-center justify-between gap-3 mb-4">
        <h2 class="font-bold text-lg flex items-center gap-2">
            📈 <span>Statistiques de fréquentation</span>
        </h2>
        <!-- Sélecteur de période -->
        <div class="flex gap-2">
            <button onclick="loadHistory('week')"  id="btn-week"  class="hist-btn active px-3 py-1.5 rounded-lg text-sm font-medium bg-red-600 text-white transition">7 jours</button>
            <button onclick="loadHistory('month')" id="btn-month" class="hist-btn px-3 py-1.5 rounded-lg text-sm font-medium bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-200 transition">30 jours</button>
            <button onclick="loadHistory('year')"  id="btn-year"  class="hist-btn px-3 py-1.5 rounded-lg text-sm font-medium bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-200 transition">12 mois</button>
        </div>
    </div>

    <!-- KPI rapides -->
    <div class="grid grid-cols-3 gap-3 mb-5" id="hist-kpis">
        <div class="bg-gray-50 dark:bg-gray-700 rounded-xl p-3 text-center">
            <div class="text-2xl font-black text-red-600" id="kpi-unique">—</div>
            <div class="text-xs text-gray-400 mt-0.5">Visiteurs uniques</div>
        </div>
        <div class="bg-gray-50 dark:bg-gray-700 rounded-xl p-3 text-center">
            <div class="text-2xl font-black text-blue-600" id="kpi-views">—</div>
            <div class="text-xs text-gray-400 mt-0.5">Pages vues</div>
        </div>
        <div class="bg-gray-50 dark:bg-gray-700 rounded-xl p-3 text-center">
            <div class="text-2xl font-black text-green-600" id="kpi-peak">—</div>
            <div class="text-xs text-gray-400 mt-0.5">Pic simultané</div>
        </div>
    </div>

    <!-- Canvas Chart.js -->
    <div style="position:relative;height:220px;">
        <canvas id="hist-chart"></canvas>
    </div>
    <div id="hist-loading" class="text-center text-gray-400 text-sm py-4">Chargement des données...</div>
</div>
<div id="stats-grid" class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
    <div class="stat-card"><div class="text-xl font-bold text-blue-600" id="s-streams">{{ stats.total_streams }}</div><div class="text-xs text-gray-400 mt-0.5">📺 Streams</div><div class="text-xs text-green-500" id="s-live">{{ stats.live_streams }} live</div></div>
    <div class="stat-card"><div class="text-xl font-bold text-green-600">{{ stats.total_comments }}</div><div class="text-xs text-gray-400 mt-0.5">💬 Commentaires</div><div class="text-xs text-red-400">{{ stats.total_reports }} signalements</div></div>
    <div class="stat-card"><div class="text-xl font-bold text-purple-600">{{ stats.iptv_channels }}</div><div class="text-xs text-gray-400 mt-0.5">🌍 Chaînes IPTV</div><div class="text-xs text-gray-500">{{ stats.iptv_playlists }} playlists</div></div>
    <div class="stat-card"><div class="text-xl font-bold text-orange-600" id="s-visitors">{{ stats.total_visitors }}</div><div class="text-xs text-gray-400 mt-0.5">👥 Visiteurs total</div></div>
    <div class="stat-card"><div class="text-xl font-bold text-indigo-600">{{ stats.total_events }}</div><div class="text-xs text-gray-400 mt-0.5">📅 Événements EPG</div><div class="text-xs text-blue-400">{{ stats.upcoming_events }} à venir</div></div>
    <div class="stat-card"><div class="text-xl font-bold text-gray-600">{{ stats.blocked_ips }}</div><div class="text-xs text-gray-400 mt-0.5">🔒 IPs bloquées</div></div>
</div>

<!-- DB Status bar -->
<div id="db-status-bar" class="hidden bg-red-50 border border-red-200 text-red-700 text-xs px-4 py-2 rounded-xl flex items-center gap-2">
    ⚠️ <span id="db-status-msg">PostgreSQL : vérification...</span>
</div>

<!-- Tabs nav -->
<div>
<div class="flex gap-0 overflow-x-auto border-b-2 border-gray-200 dark:border-gray-700">
    <button type="button" class="tab-btn active" onclick="showTab('streams',this)">📺 Streams <span class="text-xs bg-gray-100 dark:bg-gray-700 text-gray-500 px-1.5 py-0.5 rounded-full ml-1">{{ stats.total_streams }}</span></button>
    <button type="button" class="tab-btn" onclick="showTab('external',this)">📡 Flux ext. <span class="text-xs bg-gray-100 dark:bg-gray-700 text-gray-500 px-1.5 py-0.5 rounded-full ml-1">{{ stats.external_streams }}</span></button>
    <button type="button" class="tab-btn" onclick="showTab('iptv',this)">🌍 IPTV <span class="text-xs bg-gray-100 dark:bg-gray-700 text-gray-500 px-1.5 py-0.5 rounded-full ml-1">{{ stats.iptv_playlists }}</span></button>
    <button type="button" class="tab-btn" onclick="showTab('epg',this)">📅 EPG <span class="text-xs bg-indigo-100 text-indigo-600 px-1.5 py-0.5 rounded-full ml-1">{{ stats.upcoming_events }}</span></button>
    <button type="button" class="tab-btn" onclick="showTab('comments',this)">💬 Commentaires <span class="text-xs bg-gray-100 dark:bg-gray-700 text-gray-500 px-1.5 py-0.5 rounded-full ml-1">{{ stats.total_comments }}</span></button>
    <button type="button" class="tab-btn" onclick="showTab('reports',this)">🚨 Signalements{% if stats.total_reports > 0 %} <span class="text-xs bg-red-100 text-red-600 px-1.5 py-0.5 rounded-full ml-1">{{ stats.total_reports }}</span>{% endif %}</button>
    <button type="button" class="tab-btn" onclick="showTab('ips',this)">🔒 IPs <span class="text-xs bg-gray-100 dark:bg-gray-700 text-gray-500 px-1.5 py-0.5 rounded-full ml-1">{{ stats.blocked_ips }}</span></button>
</div>

<!-- Panel: Streams -->
<div id="tab-streams" class="tab-panel active bg-white dark:bg-gray-800 rounded-b-xl rounded-tr-xl shadow-sm border border-t-0 border-gray-200 dark:border-gray-700">
    <div class="p-4 border-b border-gray-100 dark:border-gray-700 flex items-center justify-between">
        <h2 class="font-semibold text-gray-700 dark:text-gray-300">Streams communautaires</h2>
        <span class="text-xs text-gray-400">{{ stats.live_streams }} en direct</span>
    </div>
    <div class="overflow-x-auto">
        <table class="min-w-full text-sm">
            <thead class="bg-gray-50 dark:bg-gray-900/50 text-xs text-gray-500 uppercase">
                <tr><th class="px-4 py-3 text-left">Titre</th><th class="px-4 py-3 text-left">Catégorie</th><th class="px-4 py-3 text-left">Statut</th><th class="px-4 py-3 text-left">Actions</th></tr>
            </thead>
            <tbody class="divide-y divide-gray-100 dark:divide-gray-700">
                {% for stream in user_streams %}
                <tr class="hover:bg-gray-50 dark:hover:bg-gray-700/50 transition">
                    <td class="px-4 py-3">
                        <a href="/watch/user/{{ stream.id }}" target="_blank" class="font-medium hover:text-red-600 transition">{{ stream.title[:40] }}</a>
                        <div class="text-xs text-gray-400">{{ stream.created_at.strftime('%d/%m/%Y %H:%M') if stream.created_at else '' }}</div>
                    </td>
                    <td class="px-4 py-3"><span class="text-xs bg-gray-100 dark:bg-gray-700 px-2 py-1 rounded-full">{{ stream.category }}</span></td>
                    <td class="px-4 py-3">
                        {% if stream.is_blocked %}<span class="text-xs bg-red-100 text-red-600 px-2 py-1 rounded-full">🚫 Bloqué</span>
                        {% elif stream.is_live %}<span class="text-xs bg-green-100 text-green-600 px-2 py-1 rounded-full">🟢 En direct</span>
                        {% else %}<span class="text-xs bg-gray-100 text-gray-500 px-2 py-1 rounded-full">⚫ Hors ligne</span>{% endif %}
                    </td>
                    <td class="px-4 py-3">
                        {% if stream.is_blocked %}
                        <button type="button" onclick="adminAction('/api/admin/streams/{{ stream.id }}/unblock')" class="text-xs bg-green-100 hover:bg-green-200 text-green-700 px-3 py-1.5 rounded-lg font-medium">✅ Débloquer</button>
                        {% else %}
                        <button type="button" onclick="adminAction('/api/admin/streams/{{ stream.id }}/block','Bloquer ce stream ?')" class="text-xs bg-red-100 hover:bg-red-200 text-red-700 px-3 py-1.5 rounded-lg font-medium">🚫 Bloquer</button>
                        {% endif %}
                    </td>
                </tr>
                {% else %}
                <tr><td colspan="4" class="px-4 py-8 text-center text-gray-400 text-sm">Aucun stream communautaire</td></tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
</div>

<!-- Panel: Flux externes -->
<div id="tab-external" class="tab-panel bg-white dark:bg-gray-800 rounded-b-xl rounded-tr-xl shadow-sm border border-t-0 border-gray-200 dark:border-gray-700">
    <div class="p-4 border-b border-gray-100 dark:border-gray-700"><h2 class="font-semibold text-gray-700 dark:text-gray-300">Flux externes vérifiés</h2></div>
    <div class="overflow-x-auto">
        <table class="min-w-full text-sm">
            <thead class="bg-gray-50 dark:bg-gray-900/50 text-xs text-gray-500 uppercase">
                <tr><th class="px-4 py-3 text-left">Titre</th><th class="px-4 py-3 text-left">Catégorie</th><th class="px-4 py-3 text-left">Pays</th><th class="px-4 py-3 text-left">Statut</th><th class="px-4 py-3 text-left">Actions</th></tr>
            </thead>
            <tbody class="divide-y divide-gray-100 dark:divide-gray-700">
                {% for stream in external_streams %}
                <tr class="hover:bg-gray-50 dark:hover:bg-gray-700/50 transition">
                    <td class="px-4 py-3">
                        <div class="flex items-center gap-2">
                            {% if stream.logo %}<img src="{{ stream.logo }}" class="w-7 h-7 object-contain rounded" loading="lazy" onerror="this.style.display='none'">{% endif %}
                            <span class="font-medium">{{ stream.title[:35] }}</span>
                        </div>
                    </td>
                    <td class="px-4 py-3"><span class="text-xs bg-gray-100 dark:bg-gray-700 px-2 py-1 rounded-full">{{ stream.category }}</span></td>
                    <td class="px-4 py-3 text-xs text-gray-500">
                        {% if stream.country %}<img src="https://flagcdn.com/w20/{{ stream.country|lower }}.png" class="inline w-4 h-3 mr-1 rounded-sm" onerror="this.style.display='none'">{% endif %}{{ stream.country }}
                    </td>
                    <td class="px-4 py-3">
                        {% if stream.is_active %}<span class="text-xs bg-green-100 text-green-600 px-2 py-1 rounded-full">✅ Actif</span>
                        {% else %}<span class="text-xs bg-red-100 text-red-600 px-2 py-1 rounded-full">❌ Désactivé</span>{% endif %}
                    </td>
                    <td class="px-4 py-3">
                        <button type="button" onclick="adminAction('/api/admin/external/{{ stream.id }}/toggle')" class="text-xs {% if stream.is_active %}bg-red-100 hover:bg-red-200 text-red-700{% else %}bg-green-100 hover:bg-green-200 text-green-700{% endif %} px-3 py-1.5 rounded-lg font-medium">
                            {% if stream.is_active %}⏸ Désactiver{% else %}▶ Activer{% endif %}
                        </button>
                    </td>
                </tr>
                {% else %}
                <tr><td colspan="5" class="px-4 py-8 text-center text-gray-400 text-sm">Aucun flux externe</td></tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
</div>

<!-- Panel: IPTV -->
<div id="tab-iptv" class="tab-panel bg-white dark:bg-gray-800 rounded-b-xl rounded-tr-xl shadow-sm border border-t-0 border-gray-200 dark:border-gray-700">
    <div class="p-4 border-b border-gray-100 dark:border-gray-700"><h2 class="font-semibold text-gray-700 dark:text-gray-300">Playlists IPTV — pays</h2></div>
    <div class="overflow-x-auto">
        <table class="min-w-full text-sm">
            <thead class="bg-gray-50 dark:bg-gray-900/50 text-xs text-gray-500 uppercase">
                <tr><th class="px-4 py-3 text-left">Pays</th><th class="px-4 py-3 text-left">Chaînes</th><th class="px-4 py-3 text-left">Dernière sync</th><th class="px-4 py-3 text-left">Statut</th><th class="px-4 py-3 text-left">Actions</th></tr>
            </thead>
            <tbody class="divide-y divide-gray-100 dark:divide-gray-700">
                {% for pl in iptv_playlists %}
                <tr class="hover:bg-gray-50 dark:hover:bg-gray-700/50 transition">
                    <td class="px-4 py-3">
                        <div class="flex items-center gap-2">
                            <img src="https://flagcdn.com/w20/{{ pl.country|lower if pl.country else 'xx' }}.png" class="w-5 h-4 object-cover rounded-sm" onerror="this.style.display='none'">
                            <span class="font-medium">{{ pl.display_name or pl.name }}</span>
                        </div>
                    </td>
                    <td class="px-4 py-3 text-xs text-gray-500">{{ pl.channel_count or 0 }}</td>
                    <td class="px-4 py-3 text-xs text-gray-400">{{ pl.last_sync.strftime('%d/%m %H:%M') if pl.last_sync else 'Jamais' }}</td>
                    <td class="px-4 py-3">
                        {% if pl.sync_status == 'success' %}<span class="text-xs bg-green-100 text-green-600 px-2 py-0.5 rounded-full">✅ OK</span>
                        {% elif pl.sync_status == 'error' %}<span class="text-xs bg-red-100 text-red-600 px-2 py-0.5 rounded-full" title="{{ pl.sync_error }}">❌ Erreur</span>
                        {% else %}<span class="text-xs bg-gray-100 text-gray-500 px-2 py-0.5 rounded-full">⏳ En attente</span>{% endif %}
                    </td>
                    <td class="px-4 py-3">
                        <button type="button" onclick="adminAction('/api/admin/iptv/playlist/{{ pl.name }}/refresh')" class="text-xs bg-blue-100 hover:bg-blue-200 text-blue-700 px-3 py-1.5 rounded-lg font-medium">🔄 Sync</button>
                    </td>
                </tr>
                {% else %}
                <tr><td colspan="5" class="px-4 py-8 text-center text-gray-400 text-sm">Aucune playlist IPTV</td></tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
</div>

<!-- Panel: EPG -->
<div id="tab-epg" class="tab-panel bg-white dark:bg-gray-800 rounded-b-xl rounded-tr-xl shadow-sm border border-t-0 border-gray-200 dark:border-gray-700">
    <div class="p-4 border-b border-gray-100 dark:border-gray-700 flex items-center justify-between flex-wrap gap-3">
        <div>
            <h2 class="font-semibold text-gray-700 dark:text-gray-300">Événements TV / EPG</h2>
            <p class="text-xs text-gray-400 mt-0.5">{{ stats.total_events }} événements · {{ stats.upcoming_events }} à venir · {{ stats.total_reminders }} rappels</p>
        </div>
        <div class="flex gap-2">
            <a href="/events" target="_blank" class="text-xs bg-indigo-100 hover:bg-indigo-200 text-indigo-700 px-3 py-1.5 rounded-lg font-medium">📅 Page publique</a>
            <button type="button" onclick="adminAction('/api/admin/epg/seed','Régénérer les événements EPG ?')" class="text-xs bg-purple-100 hover:bg-purple-200 text-purple-700 px-3 py-1.5 rounded-lg font-medium">🔄 Reset EPG</button>
        </div>
    </div>
    <div class="overflow-x-auto">
        <table class="min-w-full text-sm">
            <thead class="bg-gray-50 dark:bg-gray-900/50 text-xs text-gray-500 uppercase">
                <tr>
                    <th class="px-4 py-3 text-left">Titre</th>
                    <th class="px-4 py-3 text-left">Chaîne</th>
                    <th class="px-4 py-3 text-left">Catégorie</th>
                    <th class="px-4 py-3 text-left">Pays</th>
                    <th class="px-4 py-3 text-left">Horaire</th>
                    <th class="px-4 py-3 text-left">Actions</th>
                </tr>
            </thead>
            <tbody class="divide-y divide-gray-100 dark:divide-gray-700">
                {% for ev in epg_events %}
                <tr class="hover:bg-gray-50 dark:hover:bg-gray-700/50 transition">
                    <td class="px-4 py-3">
                        <div class="flex items-center gap-2">
                            {% if ev.channel_logo %}<img src="{{ ev.channel_logo }}" class="w-7 h-7 object-contain rounded" loading="lazy" onerror="this.style.display='none'">{% endif %}
                            <span class="font-medium max-w-xs truncate">{{ ev.title }}</span>
                        </div>
                    </td>
                    <td class="px-4 py-3 text-xs text-gray-600 dark:text-gray-400">{{ ev.channel_name or '—' }}</td>
                    <td class="px-4 py-3">
                        {% set cat_colors = {'sport':'blue','cinema':'purple','news':'gray','kids':'pink','documentary':'green','music':'yellow','other':'red'} %}
                        <span class="text-xs bg-{{ cat_colors.get(ev.category,'gray') }}-100 text-{{ cat_colors.get(ev.category,'gray') }}-700 px-2 py-0.5 rounded-full">{{ ev.category }}</span>
                    </td>
                    <td class="px-4 py-3 text-xs">
                        {% if ev.country %}<img src="https://flagcdn.com/w20/{{ ev.country|lower }}.png" class="inline w-4 h-3 mr-1 rounded-sm" onerror="this.style.display='none'">{% endif %}{{ ev.country or '—' }}
                    </td>
                    <td class="px-4 py-3 text-xs font-mono text-gray-600 dark:text-gray-400">
                        {{ ev.start_time.strftime('%d/%m %H:%M') }} → {{ ev.end_time.strftime('%H:%M') }}
                    </td>
                    <td class="px-4 py-3">
                        <button type="button" onclick="adminAction('/api/admin/epg/{{ ev.id }}/delete','Supprimer cet événement ?')" class="text-xs bg-red-100 hover:bg-red-200 text-red-700 px-3 py-1.5 rounded-lg font-medium">🗑 Suppr.</button>
                    </td>
                </tr>
                {% else %}
                <tr><td colspan="6" class="px-4 py-8 text-center text-gray-400 text-sm">
                    Aucun événement EPG · <button type="button" onclick="adminAction('/api/admin/epg/seed','Générer les événements de démonstration ?')" class="text-indigo-600 underline">Générer des événements</button>
                </td></tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
</div>

<!-- Panel: Commentaires -->
<div id="tab-comments" class="tab-panel bg-white dark:bg-gray-800 rounded-b-xl rounded-tr-xl shadow-sm border border-t-0 border-gray-200 dark:border-gray-700">
    <div class="p-4 border-b border-gray-100 dark:border-gray-700"><h2 class="font-semibold text-gray-700 dark:text-gray-300">Commentaires récents</h2></div>
    <div class="overflow-x-auto">
        <table class="min-w-full text-sm">
            <thead class="bg-gray-50 dark:bg-gray-900/50 text-xs text-gray-500 uppercase">
                <tr><th class="px-4 py-3 text-left">Contenu</th><th class="px-4 py-3 text-left">Date</th><th class="px-4 py-3 text-left">Actions</th></tr>
            </thead>
            <tbody class="divide-y divide-gray-100 dark:divide-gray-700">
                {% for comment in recent_comments %}
                <tr class="hover:bg-gray-50 dark:hover:bg-gray-700/50 transition">
                    <td class="px-4 py-3 max-w-md truncate">{{ comment.content[:100] }}</td>
                    <td class="px-4 py-3 text-xs text-gray-400 whitespace-nowrap">{{ comment.created_at.strftime('%d/%m %H:%M') if comment.created_at else '' }}</td>
                    <td class="px-4 py-3">
                        <button type="button" onclick="adminAction('/api/admin/comments/{{ comment.id }}/delete','Supprimer ce commentaire ?')" class="text-xs bg-red-100 hover:bg-red-200 text-red-700 px-3 py-1.5 rounded-lg font-medium">🗑 Supprimer</button>
                    </td>
                </tr>
                {% else %}
                <tr><td colspan="3" class="px-4 py-8 text-center text-gray-400 text-sm">Aucun commentaire</td></tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
</div>

<!-- Panel: Signalements -->
<div id="tab-reports" class="tab-panel bg-white dark:bg-gray-800 rounded-b-xl rounded-tr-xl shadow-sm border border-t-0 border-gray-200 dark:border-gray-700">
    <div class="p-4 border-b border-gray-100 dark:border-gray-700"><h2 class="font-semibold text-gray-700 dark:text-gray-300">Signalements en attente</h2></div>
    <div class="overflow-x-auto">
        <table class="min-w-full text-sm">
            <thead class="bg-gray-50 dark:bg-gray-900/50 text-xs text-gray-500 uppercase">
                <tr><th class="px-4 py-3 text-left">Raison</th><th class="px-4 py-3 text-left">Cible</th><th class="px-4 py-3 text-left">Date</th><th class="px-4 py-3 text-left">Actions</th></tr>
            </thead>
            <tbody class="divide-y divide-gray-100 dark:divide-gray-700">
                {% for report in pending_reports %}
                <tr class="hover:bg-gray-50 dark:hover:bg-gray-700/50 transition">
                    <td class="px-4 py-3 font-medium">{{ report.reason[:50] }}</td>
                    <td class="px-4 py-3 text-xs text-gray-500">{{ (report.stream_type or 'stream') }}: {{ report.stream_id[:8] if report.stream_id else '—' }}…</td>
                    <td class="px-4 py-3 text-xs text-gray-400 whitespace-nowrap">{{ report.created_at.strftime('%d/%m %H:%M') if report.created_at else '' }}</td>
                    <td class="px-4 py-3">
                        <button type="button" onclick="adminAction('/api/admin/reports/{{ report.id }}/resolve')" class="text-xs bg-green-100 hover:bg-green-200 text-green-700 px-3 py-1.5 rounded-lg font-medium">✅ Résoudre</button>
                    </td>
                </tr>
                {% else %}
                <tr><td colspan="4" class="px-4 py-8 text-center text-gray-400 text-sm">✅ Aucun signalement en attente</td></tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
</div>

<!-- Panel: IPs bloquées -->
<div id="tab-ips" class="tab-panel bg-white dark:bg-gray-800 rounded-b-xl rounded-tr-xl shadow-sm border border-t-0 border-gray-200 dark:border-gray-700">
    <div class="p-4 border-b border-gray-100 dark:border-gray-700">
        <h2 class="font-semibold text-gray-700 dark:text-gray-300 mb-3">Bloquer une IP</h2>
        <div class="flex flex-wrap gap-2 items-end">
            <div><label class="text-xs text-gray-500 block mb-1">Adresse IP</label>
            <input id="ip-addr" type="text" placeholder="192.168.1.1" class="border border-gray-200 dark:border-gray-600 dark:bg-gray-700 rounded-lg px-3 py-2 text-sm w-44 focus:ring-2 focus:ring-red-400 outline-none"></div>
            <div><label class="text-xs text-gray-500 block mb-1">Raison</label>
            <input id="block-reason" type="text" placeholder="Raison" class="border border-gray-200 dark:border-gray-600 dark:bg-gray-700 rounded-lg px-3 py-2 text-sm w-56 focus:ring-2 focus:ring-red-400 outline-none"></div>
            <label class="flex items-center gap-2 cursor-pointer pb-0.5"><input id="permanent" type="checkbox" class="rounded"><span class="text-sm text-gray-600 dark:text-gray-300">Permanent</span></label>
            <button type="button" onclick="blockIP()" class="bg-red-600 hover:bg-red-700 text-white px-4 py-2 rounded-lg text-sm font-medium transition">🔒 Bloquer</button>
        </div>
    </div>
    <div class="overflow-x-auto">
        <table class="min-w-full text-sm">
            <thead class="bg-gray-50 dark:bg-gray-900/50 text-xs text-gray-500 uppercase">
                <tr><th class="px-4 py-3 text-left">IP</th><th class="px-4 py-3 text-left">Raison</th><th class="px-4 py-3 text-left">Type</th><th class="px-4 py-3 text-left">Expire</th><th class="px-4 py-3 text-left">Actions</th></tr>
            </thead>
            <tbody class="divide-y divide-gray-100 dark:divide-gray-700">
                {% for ip in blocked_ips %}
                <tr class="hover:bg-gray-50 dark:hover:bg-gray-700/50 transition">
                    <td class="px-4 py-3 font-mono font-medium">{{ ip.ip_address }}</td>
                    <td class="px-4 py-3 text-xs text-gray-500 max-w-xs truncate">{{ ip.reason }}</td>
                    <td class="px-4 py-3">
                        {% if ip.is_permanent %}<span class="text-xs bg-red-100 text-red-600 px-2 py-0.5 rounded-full">♾ Permanent</span>
                        {% else %}<span class="text-xs bg-yellow-100 text-yellow-600 px-2 py-0.5 rounded-full">⏱ Temporaire</span>{% endif %}
                    </td>
                    <td class="px-4 py-3 text-xs text-gray-400">{{ ip.expires_at.strftime('%d/%m/%Y') if ip.expires_at else '∞' }}</td>
                    <td class="px-4 py-3">
                        <button type="button" onclick="adminAction('/api/admin/ips/{{ ip.id }}/unblock','Débloquer cette IP ?')" class="text-xs bg-green-100 hover:bg-green-200 text-green-700 px-3 py-1.5 rounded-lg font-medium">🔓 Débloquer</button>
                    </td>
                </tr>
                {% else %}
                <tr><td colspan="5" class="px-4 py-8 text-center text-gray-400 text-sm">Aucune IP bloquée</td></tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
</div>

</div><!-- end tabs -->
</div><!-- end max-w -->
{% endblock %}

{% block scripts %}
<script>
    // ── Onglets ────────────────────────────────────────────────
    function showTab(id, btn) {
        document.querySelectorAll('.tab-panel').forEach(function(p){ p.classList.remove('active'); });
        document.querySelectorAll('.tab-btn').forEach(function(b){ b.classList.remove('active'); });
        var panel = document.getElementById('tab-' + id);
        if (panel) panel.classList.add('active');
        if (btn) btn.classList.add('active');
    }

    // ── Action admin générique ─────────────────────────────────
    async function adminAction(url, confirmMsg) {
        if (confirmMsg && !confirm(confirmMsg)) return;
        try {
            var r = await fetch(url, { method: 'POST', credentials: 'include' });
            var data = {};
            try { data = await r.json(); } catch(e) {}
            if (r.ok) {
                showNotification(data.message || 'Action effectuée ✅', 'success');
                setTimeout(function(){ location.reload(); }, 900);
            } else if (r.status === 401) {
                showNotification('Session expirée — reconnectez-vous', 'error');
                setTimeout(function(){ window.location.href = '/admin'; }, 1500);
            } else {
                showNotification(data.error || 'Erreur (' + r.status + ')', 'error');
            }
        } catch(e) {
            showNotification('Erreur réseau: ' + e.message, 'error');
        }
    }

    // ── Bloquer une IP ────────────────────────────────────────
    async function blockIP() {
        var ip = document.getElementById('ip-addr').value.trim();
        if (!ip) { showNotification('Entrez une adresse IP', 'error'); return; }
        var fd = new FormData();
        fd.append('ip_address', ip);
        fd.append('reason', document.getElementById('block-reason').value || 'Raison non spécifiée');
        fd.append('permanent', document.getElementById('permanent').checked ? 'true' : 'false');
        try {
            var r = await fetch('/api/admin/ips/block', { method: 'POST', body: fd, credentials: 'include' });
            var data = {};
            try { data = await r.json(); } catch(e) {}
            if (r.ok) {
                showNotification('IP bloquée ✅', 'success');
                setTimeout(function(){ location.reload(); }, 900);
            } else {
                showNotification(data.error || 'Erreur', 'error');
            }
        } catch(e) {
            showNotification('Erreur réseau', 'error');
        }
    }

    // ── Sync IPTV ─────────────────────────────────────────────
    async function syncIPTV() {
        if (!confirm('Lancer la synchronisation IPTV.org ? (peut prendre plusieurs minutes)')) return;
        showNotification('Synchronisation démarrée...', 'info');
        try {
            var r = await fetch('/api/admin/iptv/sync', { method: 'POST', credentials: 'include' });
            var data = {};
            try { data = await r.json(); } catch(e) {}
            showNotification(r.ok ? 'Synchronisation en cours 🔄' : (data.error || 'Erreur'), r.ok ? 'success' : 'error');
            if (r.ok) setTimeout(function(){ location.reload(); }, 3000);
        } catch(e) {
            showNotification('Erreur réseau', 'error');
        }
    }

    // ── GRAPHIQUE HISTORIQUE — Chart.js ────────────────────────────────
    var _histChart = null;
    var _histPeriod = 'week';

    async function loadHistory(period) {
        _histPeriod = period;

        // Mettre à jour les boutons
        document.querySelectorAll('.hist-btn').forEach(function(b) {
            b.className = 'hist-btn px-3 py-1.5 rounded-lg text-sm font-medium bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-200 transition';
        });
        var active = document.getElementById('btn-' + period);
        if (active) active.className = 'hist-btn active px-3 py-1.5 rounded-lg text-sm font-medium bg-red-600 text-white transition';

        var loading = document.getElementById('hist-loading');
        if (loading) { loading.style.display = 'block'; loading.textContent = 'Chargement...'; }

        try {
            var r = await fetch('/api/admin/stats/history?period=' + period, { credentials: 'include' });
            if (!r.ok) { if (loading) loading.textContent = 'Erreur chargement'; return; }
            var data = await r.json();

            // KPIs
            var ku = document.getElementById('kpi-unique');
            var kv = document.getElementById('kpi-views');
            var kp = document.getElementById('kpi-peak');
            if (ku) ku.textContent = data.total_unique.toLocaleString('fr-FR');
            if (kv) kv.textContent = data.total_views.toLocaleString('fr-FR');
            if (kp) kp.textContent = data.max_peak.toLocaleString('fr-FR');

            var labels  = data.days.map(function(d) { return d.label; });
            var users   = data.days.map(function(d) { return d.unique_users; });
            var peaks   = data.days.map(function(d) { return d.peak_active; });

            var ctx = document.getElementById('hist-chart');
            if (!ctx) return;

            if (_histChart) _histChart.destroy();

            var isDark = document.documentElement.classList.contains('dark');
            var gridColor  = isDark ? 'rgba(255,255,255,0.07)' : 'rgba(0,0,0,0.06)';
            var labelColor = isDark ? '#9ca3af' : '#6b7280';

            _histChart = new Chart(ctx, {
                type: 'bar',
                data: {
                    labels: labels,
                    datasets: [
                        {
                            label: 'Visiteurs uniques',
                            data: users,
                            backgroundColor: 'rgba(220,38,38,0.75)',
                            borderColor:     'rgba(220,38,38,1)',
                            borderWidth: 1,
                            borderRadius: 4,
                            order: 2,
                        },
                        {
                            label: 'Pic simultané',
                            data: peaks,
                            type: 'line',
                            borderColor:     'rgba(34,197,94,0.9)',
                            backgroundColor: 'rgba(34,197,94,0.15)',
                            borderWidth: 2,
                            pointRadius: 3,
                            pointBackgroundColor: 'rgba(34,197,94,1)',
                            fill: true,
                            tension: 0.4,
                            order: 1,
                            yAxisID: 'yPeak',
                        }
                    ]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    interaction: { mode: 'index', intersect: false },
                    plugins: {
                        legend: {
                            labels: { color: labelColor, font: { size: 11 } }
                        },
                        tooltip: {
                            callbacks: {
                                label: function(ctx) {
                                    return ' ' + ctx.dataset.label + ' : ' + ctx.parsed.y.toLocaleString('fr-FR');
                                }
                            }
                        }
                    },
                    scales: {
                        x: {
                            grid:  { color: gridColor },
                            ticks: { color: labelColor, font: { size: 10 },
                                     maxRotation: 45, autoSkip: true, maxTicksLimit: 16 }
                        },
                        y: {
                            grid:  { color: gridColor },
                            ticks: { color: labelColor, font: { size: 10 } },
                            beginAtZero: true,
                            title: { display: true, text: 'Visiteurs uniques', color: labelColor, font: { size: 10 } }
                        },
                        yPeak: {
                            position: 'right',
                            grid: { drawOnChartArea: false },
                            ticks: { color: 'rgba(34,197,94,0.8)', font: { size: 10 } },
                            beginAtZero: true,
                            title: { display: true, text: 'Actifs simultanés', color: 'rgba(34,197,94,0.8)', font: { size: 10 } }
                        }
                    }
                }
            });

            if (loading) loading.style.display = 'none';
        } catch(e) {
            if (loading) loading.textContent = 'Erreur : ' + e.message;
        }
    }

    // ── TRACKER UTILISATEURS ACTIFS — WebSocket temps réel ────────────
    var _sparkHistory = new Array(20).fill(0);  // 20 points = 20×3s = 60s
    var _ws = null;
    var _wsRetries = 0;

    function updateActiveCard(data) {
        // Compteur principal
        var countEl = document.getElementById('active-count');
        if (countEl) {
            var prev = parseInt(countEl.textContent) || 0;
            var cur  = data.active_users || 0;
            countEl.textContent = cur;
            countEl.style.color = cur > prev ? '#4ade80' : cur < prev ? '#f87171' : '#4ade80';
        }

        // Sparkline
        _sparkHistory.push(data.active_users || 0);
        if (_sparkHistory.length > 20) _sparkHistory.shift();
        var spark = document.getElementById('sparkline');
        if (spark) {
            var max = Math.max(..._sparkHistory, 1);
            spark.innerHTML = _sparkHistory.map(function(v) {
                var pct = Math.max(4, Math.round((v / max) * 100));
                var color = v === 0 ? '#374151' : '#4ade80';
                return '<div style="flex:1;background:' + color + ';height:' + pct + '%;border-radius:2px;transition:height .4s ease;min-width:4px;" title="' + v + ' utilisateurs"></div>';
            }).join('');
        }

        // Pages actives
        var pagesEl = document.getElementById('top-pages');
        if (pagesEl && data.top_pages) {
            if (data.top_pages.length === 0) {
                pagesEl.innerHTML = '<div class="text-gray-600 text-xs">Aucune activité</div>';
            } else {
                var maxCount = Math.max(...data.top_pages.map(function(p){ return p.count; }), 1);
                pagesEl.innerHTML = data.top_pages.map(function(p) {
                    var pct = Math.round((p.count / maxCount) * 100);
                    var label = p.page || '/';
                    if (label.length > 25) label = label.substring(0, 24) + '…';
                    return '<div class="flex items-center gap-2">' +
                        '<div class="text-gray-300 text-xs font-mono w-28 truncate" title="' + p.page + '">' + label + '</div>' +
                        '<div class="flex-1 bg-gray-700 rounded-full h-1.5">' +
                        '  <div class="bg-green-400 h-1.5 rounded-full transition-all duration-500" style="width:' + pct + '%"></div>' +
                        '</div>' +
                        '<div class="text-green-400 text-xs font-bold w-4 text-right">' + p.count + '</div>' +
                        '</div>';
                }).join('');
            }
        }

        // Timestamp
        var tsEl = document.getElementById('last-update');
        if (tsEl) {
            var d = new Date();
            tsEl.textContent = d.toLocaleTimeString('fr-FR', {hour:'2-digit', minute:'2-digit', second:'2-digit'});
        }
    }

    function connectAdminWS() {
        var proto = location.protocol === 'https:' ? 'wss' : 'ws';
        var url   = proto + '://' + location.host + '/ws/admin/live';
        var badge = document.getElementById('ws-badge');

        try {
            _ws = new WebSocket(url);

            _ws.onopen = function() {
                _wsRetries = 0;
                if (badge) { badge.textContent = '⚡ En direct'; badge.className = 'text-xs px-2 py-1 rounded-full bg-green-900 text-green-300'; }
            };

            _ws.onmessage = function(evt) {
                try {
                    var data = JSON.parse(evt.data);
                    if (data.type === 'stats') {
                        updateActiveCard(data);
                        // Mettre aussi à jour les stats classiques
                        var el;
                        el = document.getElementById('s-visitors'); if (el && data.total_visitors !== undefined) el.textContent = data.total_visitors;
                    }
                } catch(e) {}
            };

            _ws.onclose = function() {
                if (badge) { badge.textContent = '🔄 Reconnexion...'; badge.className = 'text-xs px-2 py-1 rounded-full bg-yellow-900 text-yellow-300'; }
                _wsRetries++;
                var delay = Math.min(30000, 2000 * _wsRetries);
                setTimeout(connectAdminWS, delay);
            };

            _ws.onerror = function() {
                if (badge) { badge.textContent = '❌ Déconnecté'; badge.className = 'text-xs px-2 py-1 rounded-full bg-red-900 text-red-300'; }
            };

            // Envoyer un ping toutes les 20s pour garder la connexion
            setInterval(function() {
                if (_ws && _ws.readyState === WebSocket.OPEN) {
                    _ws.send('ping');
                }
            }, 20000);

        } catch(e) {
            setTimeout(connectAdminWS, 5000);
        }
    }

    // ── Auto-refresh stats classiques toutes les 30s ──────────────────
    async function refreshStats() {
        try {
            var r = await fetch('/api/admin/stats/live', { credentials: 'include' });
            if (!r.ok) return;
            var d = await r.json();
            var el;
            el = document.getElementById('s-streams');  if (el) el.textContent = d.live_streams ?? el.textContent;
            el = document.getElementById('s-live');      if (el) el.textContent = (d.live_streams ?? '?') + ' live';
            el = document.getElementById('s-visitors'); if (el) el.textContent = d.total_visitors ?? el.textContent;
            // Mettre aussi à jour la carte active users via l'API si le WS est déconnecté
            if (!_ws || _ws.readyState !== WebSocket.OPEN) {
                updateActiveCard(d);
            }
            // DB status
            var bar = document.getElementById('db-status-bar');
            var msg = document.getElementById('db-status-msg');
            if (d.db_status && d.db_status !== 'ok') {
                if (bar) bar.classList.remove('hidden');
                if (msg) msg.textContent = 'PostgreSQL : ' + d.db_status;
            } else {
                if (bar) bar.classList.add('hidden');
            }
        } catch(e) { /* silencieux */ }
    }

    // Démarrage
    connectAdminWS();
    setInterval(refreshStats, 30000);
    refreshStats();
    // Charger les stats historiques immédiatement (7 jours par défaut)
    loadHistory('week');
</script>
{% endblock %}'''

    # Template bloqué
    BLOCKED_TEMPLATE = '''{% extends "base.html" %}

{% block title %}Accès refusé - {{ app_name }}{% endblock %}

{% block content %}
<div class="min-h-[70vh] flex items-center justify-center">
    <div class="text-center max-w-md">
        <div class="w-24 h-24 bg-red-100 dark:bg-red-900/30 rounded-full flex items-center justify-center mx-auto mb-6">
            <i class="fas fa-ban text-red-600 text-4xl"></i>
        </div>
        <h1 class="text-3xl font-bold mb-3">Accès refusé</h1>
        <p class="text-gray-500 dark:text-gray-400 mb-8">{{ reason }}</p>
        <a href="/" class="bg-red-600 hover:bg-red-700 text-white px-6 py-3 rounded-xl font-semibold transition shadow-lg inline-flex items-center gap-2">
            <i class="fas fa-home"></i> Retour à l'accueil
        </a>
    </div>
</div>
{% endblock %}'''

    # Template erreur
    ERROR_TEMPLATE = '''{% extends "base.html" %}

{% block title %}Erreur - {{ app_name }}{% endblock %}

{% block content %}
<div class="min-h-[70vh] flex items-center justify-center">
    <div class="text-center max-w-md">
        <div class="w-24 h-24 bg-yellow-100 dark:bg-yellow-900/30 rounded-full flex items-center justify-center mx-auto mb-6">
            <i class="fas fa-exclamation-triangle text-yellow-500 text-4xl"></i>
        </div>
        <h1 class="text-3xl font-bold mb-3">Une erreur est survenue</h1>
        <p class="text-gray-500 dark:text-gray-400 mb-8">{{ error }}</p>
        <a href="/" class="bg-red-600 hover:bg-red-700 text-white px-6 py-3 rounded-xl font-semibold transition shadow-lg inline-flex items-center gap-2">
            <i class="fas fa-home"></i> Retour à l'accueil
        </a>
    </div>
</div>
{% endblock %}'''

    # Dictionnaire des templates
    EVENTS_TEMPLATE = '''{% extends "base.html" %}
{% block title %}Programmes TV — EPG - {{ app_name }}{% endblock %}

{% block content %}
<div class="space-y-6">
    <!-- Header -->
    <div class="bg-gradient-to-r from-red-700 via-red-600 to-orange-500 text-white rounded-2xl p-6">
        <h1 class="text-3xl font-bold flex items-center gap-3">📅 Programmes à venir</h1>
        <p class="text-red-100 mt-1 text-sm">
            <span class="font-bold text-white">{{ total_events }}</span> événements de <span class="font-bold text-white">{{ countries|length }}</span> pays — tous les programmes disponibles
        </p>
    </div>

    <!-- Filtres -->
    <div class="bg-white dark:bg-gray-800 rounded-2xl p-4 shadow border border-gray-100 dark:border-gray-700">
        <div class="flex flex-wrap gap-2 items-center">
            <span class="text-sm font-semibold text-gray-500 mr-2">Catégorie :</span>
            <a href="/events{% if current_country %}?country={{ current_country }}{% endif %}"
               class="px-3 py-1.5 rounded-xl text-sm font-medium transition {% if not current_category %}bg-red-600 text-white{% else %}bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-200{% endif %}">
                Toutes
            </a>
            {% for cat_id, cat_icon, cat_label in [('sport','⚽','Sport'),('cinema','🎬','Cinéma'),('news','📰','News'),('kids','🧒','Kids'),('documentary','🎥','Docs'),('music','🎵','Musique'),('other','📺','Autres')] %}
            <a href="/events?category={{ cat_id }}{% if current_country %}&country={{ current_country }}{% endif %}"
               class="px-3 py-1.5 rounded-xl text-sm font-medium transition {% if current_category == cat_id %}bg-red-600 text-white{% else %}bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-200{% endif %}">
                {{ cat_icon }} {{ cat_label }}
            </a>
            {% endfor %}
        </div>
        {% if countries %}
        <div class="flex flex-wrap gap-2 items-center mt-3 pt-3 border-t border-gray-100 dark:border-gray-700">
            <span class="text-sm font-semibold text-gray-500 mr-2">Pays :</span>
            <a href="/events{% if current_category %}?category={{ current_category }}{% endif %}"
               class="px-3 py-1.5 rounded-xl text-sm font-medium transition {% if not current_country %}bg-red-600 text-white{% else %}bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-200{% endif %}">
                Tous
            </a>
            {% for c in countries %}
            <a href="/events?country={{ c }}{% if current_category %}&category={{ current_category }}{% endif %}"
               class="px-3 py-1.5 rounded-xl text-sm font-medium flex items-center gap-1 transition {% if current_country == c %}bg-red-600 text-white{% else %}bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-200{% endif %}">
                <img src="https://flagcdn.com/w20/{{ c|lower }}.png" class="w-4 h-3 object-cover rounded-sm" onerror="this.style.display='none'">
                {{ c }}
            </a>
            {% endfor %}
        </div>
        {% endif %}
    </div>

    <!-- Événements groupés par catégorie -->
    {% set cat_meta = {'sport': ('⚽','Sport','from-blue-600 to-blue-700'), 'cinema': ('🎬','Cinéma','from-purple-600 to-purple-700'), 'news': ('📰','News','from-gray-600 to-gray-700'), 'kids': ('🧒','Kids','from-pink-500 to-pink-600'), 'documentary': ('🎥','Documentaires','from-green-600 to-green-700'), 'music': ('🎵','Musique','from-yellow-500 to-orange-500'), 'other': ('📺','Autres','from-red-600 to-red-700')} %}

    {% if events %}
    {% for cat_id in ['sport','cinema','news','kids','documentary','music','other'] %}
    {% if events_by_cat.get(cat_id) %}
    {% set meta = cat_meta.get(cat_id, ('📺', cat_id, 'from-red-600 to-red-700')) %}
    <div class="space-y-3">
        <div class="flex items-center gap-3">
            <div class="bg-gradient-to-r {{ meta[2] }} text-white px-4 py-1.5 rounded-xl font-bold text-sm flex items-center gap-2">
                {{ meta[0] }} {{ meta[1]|upper }}
            </div>
            <div class="h-px flex-1 bg-gray-200 dark:bg-gray-700"></div>
        </div>
            <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {% for ev in events_by_cat[cat_id] %}
            <div class="bg-white dark:bg-gray-800 rounded-2xl border border-gray-100 dark:border-gray-700 shadow-sm overflow-hidden hover:shadow-md transition group event-card"
                 data-endtime="{{ ev.end_time.strftime('%Y-%m-%dT%H:%M:%S') }}"
                 id="evcard-{{ ev.id }}">
                <div class="bg-gradient-to-r {{ meta[2] }} h-1"></div>
                <div class="p-4">
                    <div class="flex items-start gap-3">
                        <div class="w-12 h-12 rounded-xl overflow-hidden flex-shrink-0 bg-gray-100 dark:bg-gray-700 flex items-center justify-center">
                            {% if ev.channel_logo %}
                            <img src="{{ ev.channel_logo }}" class="w-full h-full object-contain p-1" loading="lazy" onerror="this.style.display='none'">
                            {% else %}
                            <span class="text-2xl">{{ meta[0] }}</span>
                            {% endif %}
                        </div>
                        <div class="flex-1 min-w-0">
                            <h3 class="font-bold text-sm leading-tight truncate group-hover:text-red-600 transition">{{ ev.title }}</h3>
                            <div class="text-xs text-gray-500 mt-0.5 flex items-center gap-1">
                                {% if ev.country %}
                                <img src="https://flagcdn.com/w20/{{ ev.country|lower }}.png" class="w-4 h-3 object-cover rounded-sm" onerror="this.style.display='none'">
                                {% endif %}
                                {{ ev.channel_name or 'Chaîne inconnue' }}
                            </div>
                        </div>
                    </div>
                    {% if ev.description %}
                    <p class="text-xs text-gray-400 mt-2 line-clamp-2">{{ ev.description }}</p>
                    {% endif %}
                    <div class="mt-3 flex items-center justify-between gap-2 flex-wrap">
                        <div class="flex items-center gap-1 text-xs font-bold text-gray-700 dark:text-gray-300 bg-gray-100 dark:bg-gray-700 px-2 py-1 rounded-lg">
                            🕐 {{ ev.start_time.strftime('%H:%M') }} → {{ ev.end_time.strftime('%H:%M') }}
                        </div>
                        <div class="flex items-center gap-1">
                            <a href="/search?q={{ ev.channel_name|urlencode }}" class="text-xs bg-red-600 hover:bg-red-700 text-white px-3 py-1 rounded-lg transition font-medium" title="Rechercher {{ ev.channel_name }} dans le catalogue">
                                ▶ Regarder
                            </a>
                            <button onclick="remindEvent('{{ ev.id }}', '{{ ev.title|e }}', '{{ ev.start_time.strftime('%H:%M') }}')"
                                id="remind-{{ ev.id }}"
                                class="text-xs {% if ev.id in reminders %}bg-blue-200 dark:bg-blue-800 text-blue-800 dark:text-blue-200{% else %}bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300 hover:bg-blue-100{% endif %} px-3 py-1 rounded-lg transition font-medium">
                                {% if ev.id in reminders %}🔔 Activé{% else %}🔔 Rappel{% endif %}
                            </button>
                        </div>
                    </div>
                </div>
            </div>
            {% endfor %}
        </div>
    </div>
    {% endif %}
    {% endfor %}
    {% else %}
    <div class="bg-white dark:bg-gray-800 rounded-2xl p-12 text-center shadow border border-gray-100 dark:border-gray-700">
        <div class="text-6xl mb-4">📅</div>
        <h2 class="text-xl font-bold mb-2">Aucun programme prévu</h2>
        <p class="text-gray-400 text-sm">Aucun événement correspondant à vos filtres n'est prévu pour aujourd'hui.</p>
        <a href="/events" class="mt-4 inline-block bg-red-600 text-white px-6 py-2 rounded-xl hover:bg-red-700 transition text-sm font-medium">Voir tous les programmes</a>
    </div>
    {% endif %}
</div>
{% endblock %}

{% block scripts %}
<script>
    async function remindEvent(eventId, title, startTime) {
        if ('Notification' in window && Notification.permission === 'default') {
            await Notification.requestPermission();
        }
        try {
            var r = await fetch('/api/events/' + eventId + '/remind', { method: 'POST', credentials: 'include' });
            var d = await r.json();
            var btn = document.getElementById('remind-' + eventId);
            if (d.action === 'added') {
                if (btn) { btn.textContent = '🔔 Rappel activé'; btn.classList.add('bg-blue-200','dark:bg-blue-800','text-blue-800'); btn.classList.remove('bg-gray-100','dark:bg-gray-700','text-gray-600'); }
                showNotification('🔔 Rappel activé pour "' + title + '" à ' + startTime, 'success');
            } else {
                if (btn) { btn.textContent = '🔔 Me rappeler'; btn.classList.remove('bg-blue-200','dark:bg-blue-800','text-blue-800'); btn.classList.add('bg-gray-100','dark:bg-gray-700','text-gray-600'); }
                showNotification('Rappel supprimé', 'info');
            }
        } catch(e) {
            showNotification('Erreur réseau', 'error');
        }
    }

    // ── Suppression automatique des événements passés (côté client) ──────
    // Vérifie toutes les 60 secondes et retire les cartes dont end_time est dépassé
    function removeExpiredEvents() {
        var now = new Date();
        var removed = 0;
        document.querySelectorAll('.event-card').forEach(function(card) {
            var et = card.getAttribute('data-endtime');
            if (!et) return;
            // data-endtime est en UTC (serveur), on compare en UTC
            var endUtc = new Date(et + 'Z');
            var cutoff = new Date(endUtc.getTime() + 10 * 60 * 1000); // +10 min
            if (now > cutoff) {
                card.style.transition = 'opacity 0.5s';
                card.style.opacity = '0';
                setTimeout(function() { card.remove(); }, 500);
                removed++;
            }
        });
        if (removed > 0) {
            // Recharger le compteur dans le header
            var h = document.querySelector('[data-event-count]');
            if (h) {
                var total = document.querySelectorAll('.event-card').length;
                h.textContent = total;
            }
        }
    }

    // Lancer immédiatement + toutes les 60s
    removeExpiredEvents();
    setInterval(removeExpiredEvents, 60000);
</script>
{% endblock %}'''

    SETTINGS_TEMPLATE = '''{% extends "base.html" %}
{% block title %}Paramètres — {{ app_name }}{% endblock %}

{% block content %}
<div class="max-w-3xl mx-auto space-y-6 pb-12">

    <!-- Header -->
    <div class="bg-gradient-to-r from-gray-800 to-gray-900 text-white rounded-2xl p-6">
        <h1 class="text-2xl font-bold flex items-center gap-3">⚙️ Paramètres</h1>
        <p class="text-gray-400 mt-1 text-sm">Personnalisez votre expérience {{ app_name }}. Tous les paramètres sont sauvegardés localement sur votre appareil.</p>
    </div>

    <!-- ═══ DONNÉES MOBILES ═══ -->
    <div class="bg-white dark:bg-gray-800 rounded-2xl border border-gray-200 dark:border-gray-700 shadow-sm overflow-hidden">
        <div class="bg-gradient-to-r from-blue-600 to-blue-700 px-5 py-3 flex items-center gap-2">
            <span class="text-white font-bold text-sm">📱 Économie de données mobiles</span>
        </div>
        <div class="p-5 space-y-5">

            <p class="text-xs text-gray-500 dark:text-gray-400 bg-blue-50 dark:bg-blue-900/20 rounded-xl p-3 border border-blue-100 dark:border-blue-800">
                💡 <strong>À quoi ça sert ?</strong> Sur une connexion mobile (4G/5G), chaque vidéo consomme des mégaoctets. Ces options vous permettent de regarder {{ app_name }} en utilisant moins de données.
            </p>

            <!-- Qualité vidéo -->
            <div class="setting-row">
                <div class="flex items-start justify-between gap-4">
                    <div class="flex-1">
                        <div class="font-semibold text-sm text-gray-800 dark:text-gray-200">🎬 Qualité vidéo par défaut</div>
                        <div class="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
                            <strong>Auto</strong> : s'adapte à votre connexion. <strong>SD (480p)</strong> : environ 3× moins de données que HD. <strong>HD (720p)</strong> : qualité standard. <strong>Full HD</strong> : meilleure qualité, consomme le plus.
                        </div>
                    </div>
                    <select id="s-quality" onchange="saveSetting('quality', this.value)"
                        class="mt-1 text-sm border border-gray-300 dark:border-gray-600 rounded-lg px-3 py-1.5 bg-white dark:bg-gray-700 text-gray-800 dark:text-gray-200 min-w-[110px]">
                        <option value="auto">🔄 Auto</option>
                        <option value="sd">📉 SD (480p)</option>
                        <option value="hd">📺 HD (720p)</option>
                        <option value="fhd">🎬 Full HD</option>
                    </select>
                </div>
            </div>

            <!-- Lecture automatique -->
            <div class="setting-row border-t border-gray-100 dark:border-gray-700 pt-4">
                <div class="flex items-start justify-between gap-4">
                    <div class="flex-1">
                        <div class="font-semibold text-sm text-gray-800 dark:text-gray-200">▶️ Lecture automatique</div>
                        <div class="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
                            Si activée, les vidéos se lancent dès l'ouverture d'une chaîne. <strong>Désactivez</strong> pour économiser des données : vous choisissez vous-même quand démarrer.
                        </div>
                    </div>
                    <label class="relative inline-flex items-center cursor-pointer mt-1">
                        <input type="checkbox" id="s-autoplay" onchange="saveSetting('autoplay', this.checked)" class="sr-only peer">
                        <div class="w-11 h-6 bg-gray-200 peer-focus:outline-none rounded-full peer dark:bg-gray-700 peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[\\'\\'] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-blue-600"></div>
                    </label>
                </div>
            </div>

            <!-- Préchargement -->
            <div class="setting-row border-t border-gray-100 dark:border-gray-700 pt-4">
                <div class="flex items-start justify-between gap-4">
                    <div class="flex-1">
                        <div class="font-semibold text-sm text-gray-800 dark:text-gray-200">⏩ Préchargement des thumbnails</div>
                        <div class="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
                            Les miniatures (vignettes) des chaînes sont chargées à l'avance pour un affichage plus rapide. <strong>Désactivez sur mobile</strong> pour économiser des données — les images se chargent à la demande uniquement.
                        </div>
                    </div>
                    <label class="relative inline-flex items-center cursor-pointer mt-1">
                        <input type="checkbox" id="s-preload" onchange="saveSetting('preload', this.checked)" class="sr-only peer">
                        <div class="w-11 h-6 bg-gray-200 peer-focus:outline-none rounded-full peer dark:bg-gray-700 peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[\\'\\'] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-blue-600"></div>
                    </label>
                </div>
            </div>

            <!-- Mode économie données -->
            <div class="setting-row border-t border-gray-100 dark:border-gray-700 pt-4">
                <div class="flex items-start justify-between gap-4">
                    <div class="flex-1">
                        <div class="font-semibold text-sm text-gray-800 dark:text-gray-200">🔋 Mode économie de données</div>
                        <div class="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
                            Active automatiquement SD + désactive le préchargement + réduit les animations. <strong>Recommandé sur réseau mobile ou connexion lente.</strong> Économise jusqu\'à 70% des données.
                        </div>
                    </div>
                    <label class="relative inline-flex items-center cursor-pointer mt-1">
                        <input type="checkbox" id="s-datasaver" onchange="applyDataSaver(this.checked)" class="sr-only peer">
                        <div class="w-11 h-6 bg-gray-200 peer-focus:outline-none rounded-full peer dark:bg-gray-700 peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[\\'\\'] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-green-500"></div>
                    </label>
                </div>
                <div id="datasaver-info" class="hidden mt-2 text-xs bg-green-50 dark:bg-green-900/20 text-green-700 dark:text-green-300 rounded-lg p-2 border border-green-200 dark:border-green-800">
                    ✅ Mode économie actif : SD forcé, thumbnails à la demande, animations réduites.
                </div>
            </div>

        </div>
    </div>

    <!-- ═══ LECTURE & LECTEUR ═══ -->
    <div class="bg-white dark:bg-gray-800 rounded-2xl border border-gray-200 dark:border-gray-700 shadow-sm overflow-hidden">
        <div class="bg-gradient-to-r from-purple-600 to-purple-700 px-5 py-3 flex items-center gap-2">
            <span class="text-white font-bold text-sm">🎬 Lecteur vidéo</span>
        </div>
        <div class="p-5 space-y-5">

            <!-- Volume par défaut -->
            <div class="setting-row">
                <div class="font-semibold text-sm text-gray-800 dark:text-gray-200 mb-1">🔊 Volume par défaut</div>
                <div class="text-xs text-gray-500 dark:text-gray-400 mb-3">Volume appliqué automatiquement à l\'ouverture d\'une chaîne (0 = muet, 100 = plein volume).</div>
                <div class="flex items-center gap-3">
                    <input type="range" id="s-volume" min="0" max="100" value="80"
                        oninput="updateVolume(this.value)"
                        class="flex-1 accent-purple-600">
                    <span id="vol-val" class="text-sm font-bold text-purple-600 w-12 text-right">80%</span>
                </div>
            </div>

            <!-- Sous-titres -->
            <div class="setting-row border-t border-gray-100 dark:border-gray-700 pt-4">
                <div class="flex items-start justify-between gap-4">
                    <div class="flex-1">
                        <div class="font-semibold text-sm text-gray-800 dark:text-gray-200">📝 Sous-titres automatiques</div>
                        <div class="text-xs text-gray-500 dark:text-gray-400 mt-0.5">Active les sous-titres si disponibles sur la chaîne. Utile pour les chaînes en langue étrangère.</div>
                    </div>
                    <label class="relative inline-flex items-center cursor-pointer mt-1">
                        <input type="checkbox" id="s-subtitles" onchange="saveSetting('subtitles', this.checked)" class="sr-only peer">
                        <div class="w-11 h-6 bg-gray-200 peer-focus:outline-none rounded-full peer dark:bg-gray-700 peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[\\'\\'] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-purple-600"></div>
                    </label>
                </div>
            </div>

            <!-- Plein écran auto -->
            <div class="setting-row border-t border-gray-100 dark:border-gray-700 pt-4">
                <div class="flex items-start justify-between gap-4">
                    <div class="flex-1">
                        <div class="font-semibold text-sm text-gray-800 dark:text-gray-200">🖥️ Plein écran automatique</div>
                        <div class="text-xs text-gray-500 dark:text-gray-400 mt-0.5">Passe automatiquement en plein écran dès qu\'une chaîne démarre. Pratique sur tablette ou TV connectée.</div>
                    </div>
                    <label class="relative inline-flex items-center cursor-pointer mt-1">
                        <input type="checkbox" id="s-fullscreen" onchange="saveSetting('fullscreen', this.checked)" class="sr-only peer">
                        <div class="w-11 h-6 bg-gray-200 peer-focus:outline-none rounded-full peer dark:bg-gray-700 peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[\\'\\'] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-purple-600"></div>
                    </label>
                </div>
            </div>

        </div>
    </div>

    <!-- ═══ INTERFACE ═══ -->
    <div class="bg-white dark:bg-gray-800 rounded-2xl border border-gray-200 dark:border-gray-700 shadow-sm overflow-hidden">
        <div class="bg-gradient-to-r from-orange-500 to-orange-600 px-5 py-3 flex items-center gap-2">
            <span class="text-white font-bold text-sm">🎨 Interface & Affichage</span>
        </div>
        <div class="p-5 space-y-5">

            <!-- Thème -->
            <div class="setting-row">
                <div class="font-semibold text-sm text-gray-800 dark:text-gray-200 mb-1">🌙 Thème</div>
                <div class="text-xs text-gray-500 dark:text-gray-400 mb-3">Le mode sombre réduit la fatigue oculaire et consomme moins de batterie sur les écrans OLED.</div>
                <div class="flex gap-3">
                    <button onclick="setTheme(\'light\')" id="theme-light"
                        class="theme-btn flex-1 py-2 rounded-xl border-2 border-gray-200 text-sm font-medium transition hover:border-orange-400">
                        ☀️ Clair
                    </button>
                    <button onclick="setTheme(\'dark\')" id="theme-dark"
                        class="theme-btn flex-1 py-2 rounded-xl border-2 border-gray-200 text-sm font-medium transition hover:border-orange-400">
                        🌙 Sombre
                    </button>
                    <button onclick="setTheme(\'auto\')" id="theme-auto"
                        class="theme-btn flex-1 py-2 rounded-xl border-2 border-orange-400 bg-orange-50 dark:bg-orange-900/20 text-sm font-medium transition">
                        🔄 Auto
                    </button>
                </div>
            </div>

            <!-- Langue -->
            <div class="setting-row border-t border-gray-100 dark:border-gray-700 pt-4">
                <div class="font-semibold text-sm text-gray-800 dark:text-gray-200 mb-1">🌐 Langue préférée des chaînes</div>
                <div class="text-xs text-gray-500 dark:text-gray-400 mb-3">Filtre les chaînes recommandées selon la langue choisie. N\'empêche pas de regarder les autres langues.</div>
                <select id="s-lang" onchange="saveSetting('lang', this.value)"
                    class="w-full text-sm border border-gray-300 dark:border-gray-600 rounded-lg px-3 py-2 bg-white dark:bg-gray-700 text-gray-800 dark:text-gray-200">
                    <option value="fr">🇫🇷 Français</option>
                    <option value="en">🇬🇧 English</option>
                    <option value="ar">🇸🇦 العربية</option>
                    <option value="es">🇪🇸 Español</option>
                    <option value="de">🇩🇪 Deutsch</option>
                    <option value="pt">🇵🇹 Português</option>
                    <option value="it">🇮🇹 Italiano</option>
                    <option value="ru">🇷🇺 Русский</option>
                    <option value="zh">🇨🇳 中文</option>
                    <option value="ja">🇯🇵 日本語</option>
                </select>
            </div>

            <!-- Animations -->
            <div class="setting-row border-t border-gray-100 dark:border-gray-700 pt-4">
                <div class="flex items-start justify-between gap-4">
                    <div class="flex-1">
                        <div class="font-semibold text-sm text-gray-800 dark:text-gray-200">✨ Animations de l\'interface</div>
                        <div class="text-xs text-gray-500 dark:text-gray-400 mt-0.5">Désactivez pour une interface plus rapide, notamment sur les appareils moins puissants ou les connexions lentes.</div>
                    </div>
                    <label class="relative inline-flex items-center cursor-pointer mt-1">
                        <input type="checkbox" id="s-animations" onchange="saveSetting('animations', this.checked)" class="sr-only peer" checked>
                        <div class="w-11 h-6 bg-gray-200 peer-focus:outline-none rounded-full peer dark:bg-gray-700 peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[\\'\\'] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-orange-500"></div>
                    </label>
                </div>
            </div>

        </div>
    </div>

    <!-- ═══ NOTIFICATIONS ═══ -->
    <div class="bg-white dark:bg-gray-800 rounded-2xl border border-gray-200 dark:border-gray-700 shadow-sm overflow-hidden">
        <div class="bg-gradient-to-r from-green-600 to-green-700 px-5 py-3 flex items-center gap-2">
            <span class="text-white font-bold text-sm">🔔 Notifications</span>
        </div>
        <div class="p-5 space-y-5">

            <!-- Rappels EPG -->
            <div class="setting-row">
                <div class="flex items-start justify-between gap-4">
                    <div class="flex-1">
                        <div class="font-semibold text-sm text-gray-800 dark:text-gray-200">📅 Rappels programmes TV</div>
                        <div class="text-xs text-gray-500 dark:text-gray-400 mt-0.5">Recevez une notification 5 minutes avant le début d\'un programme que vous avez mis en rappel depuis la page Événements.</div>
                    </div>
                    <label class="relative inline-flex items-center cursor-pointer mt-1">
                        <input type="checkbox" id="s-notif-epg" onchange="toggleNotifications(this.checked)" class="sr-only peer">
                        <div class="w-11 h-6 bg-gray-200 peer-focus:outline-none rounded-full peer dark:bg-gray-700 peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[\\'\\'] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-green-600"></div>
                    </label>
                </div>
                <div id="notif-status" class="mt-2 text-xs text-gray-400"></div>
            </div>

        </div>
    </div>

    <!-- ═══ CONFIDENTIALITÉ ═══ -->
    <div class="bg-white dark:bg-gray-800 rounded-2xl border border-gray-200 dark:border-gray-700 shadow-sm overflow-hidden">
        <div class="bg-gradient-to-r from-red-600 to-red-700 px-5 py-3 flex items-center gap-2">
            <span class="text-white font-bold text-sm">🔒 Confidentialité</span>
        </div>
        <div class="p-5 space-y-5">

            <p class="text-xs text-gray-500 dark:text-gray-400 bg-red-50 dark:bg-red-900/20 rounded-xl p-3 border border-red-100 dark:border-red-800">
                💡 {{ app_name }} ne collecte aucune donnée personnelle identifiable. Aucun compte requis. Vos préférences sont stockées uniquement sur votre appareil.
            </p>

            <!-- Historique local -->
            <div class="setting-row">
                <div class="flex items-start justify-between gap-4">
                    <div class="flex-1">
                        <div class="font-semibold text-sm text-gray-800 dark:text-gray-200">🕐 Historique de visionnage local</div>
                        <div class="text-xs text-gray-500 dark:text-gray-400 mt-0.5">Garde en mémoire les dernières chaînes regardées pour un accès rapide. Stocké uniquement sur votre appareil, jamais envoyé.</div>
                    </div>
                    <label class="relative inline-flex items-center cursor-pointer mt-1">
                        <input type="checkbox" id="s-history" onchange="saveSetting('history', this.checked)" class="sr-only peer" checked>
                        <div class="w-11 h-6 bg-gray-200 peer-focus:outline-none rounded-full peer dark:bg-gray-700 peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[\\'\\'] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-red-600"></div>
                    </label>
                </div>
            </div>

            <!-- Effacer données -->
            <div class="border-t border-gray-100 dark:border-gray-700 pt-4">
                <div class="font-semibold text-sm text-gray-800 dark:text-gray-200 mb-1">🗑️ Effacer toutes les données locales</div>
                <div class="text-xs text-gray-500 dark:text-gray-400 mb-3">Remet tous les paramètres à zéro et efface l\'historique, les favoris locaux et les préférences. Action irréversible.</div>
                <button onclick="clearAllData()"
                    class="bg-red-100 hover:bg-red-200 text-red-700 px-4 py-2 rounded-xl text-sm font-medium transition">
                    🗑️ Effacer mes données locales
                </button>
            </div>

        </div>
    </div>

    <!-- ═══ BOUTONS D'ACTION PRINCIPAUX ═══ -->
    <div class="bg-white dark:bg-gray-800 rounded-2xl border border-gray-200 dark:border-gray-700 shadow-sm p-5">
        <div class="flex flex-col sm:flex-row gap-3">

            <!-- Bouton Enregistrer -->
            <button id="btn-save" onclick="saveSettings()"
                class="flex-1 bg-gradient-to-r from-green-600 to-green-700 hover:from-green-700 hover:to-green-800 text-white font-bold px-6 py-3 rounded-xl shadow transition flex items-center justify-center gap-2 text-sm">
                <i class="fas fa-save"></i> Enregistrer les paramètres
            </button>

            <!-- Bouton Réinitialiser -->
            <button id="btn-reset" onclick="resetSettings()"
                class="flex-1 bg-gradient-to-r from-gray-500 to-gray-600 hover:from-gray-600 hover:to-gray-700 text-white font-bold px-6 py-3 rounded-xl shadow transition flex items-center justify-center gap-2 text-sm">
                <i class="fas fa-undo"></i> Réinitialiser les paramètres d'origine
            </button>

        </div>

        <!-- Zone de feedback -->
        <div id="action-feedback" class="hidden mt-3 rounded-xl px-4 py-3 text-sm font-medium text-center transition-all"></div>
    </div>

</div>
{% endblock %}

{% block scripts %}
<script>
    // ════════════════════════════════════════════════════════════════════
    //  LIVEWATCH — Gestionnaire de paramètres
    //  Stockage double :
    //    • localStorage  → tous les paramètres (rapide, sans réseau)
    //    • API /api/settings/* → theme + lang persistés en base (sync multi-appareils)
    // ════════════════════════════════════════════════════════════════════

    var SETTINGS_KEY = 'lw_settings_v2';

    var DEFAULTS = {
        quality:    'auto',
        autoplay:   true,
        preload:    true,
        datasaver:  false,
        volume:     80,
        subtitles:  false,
        fullscreen: false,
        theme:      'auto',
        lang:       'fr',
        animations: true,
        notif_epg:  false,
        history:    true,
    };

    // ── Lecture / écriture localStorage ─────────────────────────────────
    function getSettings() {
        try {
            return Object.assign({}, DEFAULTS, JSON.parse(localStorage.getItem(SETTINGS_KEY) || '{}'));
        } catch(e) { return Object.assign({}, DEFAULTS); }
    }

    function setLocalSettings(s) {
        try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(s)); } catch(e) {}
    }

    // ── Collecter TOUS les champs du formulaire ─────────────────────────
    function collectFormValues() {
        var s = {};
        // Select
        var sq = document.getElementById('s-quality');   if (sq) s.quality = sq.value;
        var sl = document.getElementById('s-lang');      if (sl) s.lang    = sl.value;
        // Checkboxes
        var toggleMap = {
            's-autoplay':   'autoplay',
            's-preload':    'preload',
            's-datasaver':  'datasaver',
            's-subtitles':  'subtitles',
            's-fullscreen': 'fullscreen',
            's-animations': 'animations',
            's-notif-epg':  'notif_epg',
            's-history':    'history',
        };
        for (var id in toggleMap) {
            var el = document.getElementById(id);
            if (el) s[toggleMap[id]] = el.checked;
        }
        // Volume
        var sv = document.getElementById('s-volume'); if (sv) s.volume = parseInt(sv.value);
        // Thème (stocké dans variable globale)
        s.theme = window._currentTheme || getSettings().theme;
        return s;
    }

    // ── Peupler le formulaire depuis un objet settings ──────────────────
    function populateForm(s) {
        var sq = document.getElementById('s-quality'); if (sq) sq.value = s.quality || 'auto';
        var sl = document.getElementById('s-lang');    if (sl) sl.value = s.lang    || 'fr';
        var sv = document.getElementById('s-volume');
        if (sv) {
            sv.value = s.volume !== undefined ? s.volume : 80;
            var vv = document.getElementById('vol-val');
            if (vv) vv.textContent = sv.value + '%';
        }
        var toggleMap = {
            's-autoplay':   'autoplay',
            's-preload':    'preload',
            's-datasaver':  'datasaver',
            's-subtitles':  'subtitles',
            's-fullscreen': 'fullscreen',
            's-animations': 'animations',
            's-notif-epg':  'notif_epg',
            's-history':    'history',
        };
        for (var id in toggleMap) {
            var el = document.getElementById(id);
            var key = toggleMap[id];
            if (el) el.checked = (s[key] !== undefined) ? s[key] : DEFAULTS[key];
        }
        window._currentTheme = s.theme || 'auto';
        updateThemeButtons(window._currentTheme);

        // Afficher/cacher info data saver
        var di = document.getElementById('datasaver-info');
        if (di) { if (s.datasaver) di.classList.remove('hidden'); else di.classList.add('hidden'); }

        updateNotifStatus();
    }

    // ── Feedback visuel ─────────────────────────────────────────────────
    function showFeedback(msg, type) {
        var fb = document.getElementById('action-feedback');
        if (!fb) return;
        fb.classList.remove('hidden', 'bg-green-100', 'bg-red-100', 'text-green-800', 'text-red-800',
                            'dark:bg-green-900/30', 'dark:bg-red-900/30', 'dark:text-green-300', 'dark:text-red-300');
        if (type === 'success') {
            fb.classList.add('bg-green-100', 'text-green-800', 'dark:bg-green-900/30', 'dark:text-green-300');
        } else {
            fb.classList.add('bg-red-100', 'text-red-800', 'dark:bg-red-900/30', 'dark:text-red-300');
        }
        fb.textContent = msg;
        fb.classList.remove('hidden');
        clearTimeout(window._fbTimer);
        window._fbTimer = setTimeout(function() { fb.classList.add('hidden'); }, 4000);
    }

    function setBtnLoading(id, loading, label) {
        var btn = document.getElementById(id);
        if (!btn) return;
        btn.disabled = loading;
        if (loading) {
            btn.dataset.orig = btn.innerHTML;
            btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> ' + label;
        } else if (btn.dataset.orig) {
            btn.innerHTML = btn.dataset.orig;
        }
    }

    // ── ✅ ENREGISTRER ──────────────────────────────────────────────────
    async function saveSettings() {
        setBtnLoading('btn-save', true, 'Enregistrement...');
        var s = collectFormValues();

        // 1. Sauvegarder en localStorage (immédiat)
        setLocalSettings(s);

        // 2. Appliquer le thème tout de suite
        applyThemeNow(s.theme);

        // 3. Envoyer theme + lang au serveur (persistance base de données)
        try {
            var r = await fetch('/api/settings/save', {
                method:      'POST',
                credentials: 'include',
                headers:     { 'Content-Type': 'application/json' },
                body:        JSON.stringify({ theme: s.theme, lang: s.lang }),
            });
            var data = await r.json();
            if (data.success) {
                showFeedback('✅ Paramètres enregistrés avec succès !', 'success');
            } else {
                showFeedback('✅ Paramètres sauvegardés localement (hors ligne).', 'success');
            }
        } catch(e) {
            // Pas de connexion — les préférences sont quand même dans localStorage
            showFeedback('✅ Paramètres sauvegardés sur cet appareil.', 'success');
        }
        setBtnLoading('btn-save', false);
    }

    // ── 🔄 RÉINITIALISER ────────────────────────────────────────────────
    async function resetSettings() {
        if (!confirm('Réinitialiser tous les paramètres aux valeurs d\'origine ?\\nCette action est irréversible.')) return;
        setBtnLoading('btn-reset', true, 'Réinitialisation...');

        // 1. Remettre les valeurs par défaut dans localStorage
        setLocalSettings(Object.assign({}, DEFAULTS));

        // 2. Repeupler le formulaire
        populateForm(DEFAULTS);

        // 3. Appliquer le thème par défaut
        applyThemeNow('auto');

        // 4. Notifier le serveur
        try {
            await fetch('/api/settings/reset', { method: 'POST', credentials: 'include' });
        } catch(e) {}

        showFeedback('🔄 Tous les paramètres ont été réinitialisés aux valeurs d\'origine.', 'success');
        setBtnLoading('btn-reset', false);
    }

    // ── Thème ────────────────────────────────────────────────────────────
    function setTheme(theme) {
        window._currentTheme = theme;
        updateThemeButtons(theme);
        applyThemeNow(theme);
    }

    function applyThemeNow(theme) {
        if (theme === 'dark') {
            document.documentElement.classList.add('dark');
            localStorage.setItem('theme', 'dark');
        } else if (theme === 'light') {
            document.documentElement.classList.remove('dark');
            localStorage.setItem('theme', 'light');
        } else {
            var prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
            if (prefersDark) document.documentElement.classList.add('dark');
            else document.documentElement.classList.remove('dark');
            localStorage.removeItem('theme');
        }
    }

    function updateThemeButtons(theme) {
        ['light','dark','auto'].forEach(function(t) {
            var btn = document.getElementById('theme-' + t);
            if (!btn) return;
            var active = (t === theme);
            btn.classList.toggle('border-orange-400', active);
            btn.classList.toggle('bg-orange-50', active);
            btn.classList.toggle('border-gray-200', !active);
        });
    }

    // ── Mode économie de données ─────────────────────────────────────────
    function applyDataSaver(enabled) {
        var sq = document.getElementById('s-quality');
        var sp = document.getElementById('s-preload');
        var sa = document.getElementById('s-autoplay');
        var di = document.getElementById('datasaver-info');
        if (enabled) {
            if (sq) sq.value = 'sd';
            if (sp) sp.checked = false;
            if (sa) sa.checked = false;
            if (di) di.classList.remove('hidden');
        } else {
            if (sq) sq.value = DEFAULTS.quality;
            if (sp) sp.checked = DEFAULTS.preload;
            if (sa) sa.checked = DEFAULTS.autoplay;
            if (di) di.classList.add('hidden');
        }
    }

    // ── Notifications ────────────────────────────────────────────────────
    async function toggleNotifications(enabled) {
        if (enabled) {
            if (!('Notification' in window)) {
                updateNotifStatus('❌ Non supporté par ce navigateur.');
                var el = document.getElementById('s-notif-epg'); if (el) el.checked = false;
                return;
            }
            var perm = await Notification.requestPermission();
            if (perm === 'granted') {
                updateNotifStatus('✅ Activées — rappels 5 min avant vos programmes.');
            } else {
                updateNotifStatus('⚠️ Refusées — activez dans les réglages du navigateur.');
                var el2 = document.getElementById('s-notif-epg'); if (el2) el2.checked = false;
            }
        } else {
            updateNotifStatus('🔕 Notifications désactivées.');
        }
    }

    function updateNotifStatus() {
        var el = document.getElementById('notif-status');
        if (!el) return;
        if (!('Notification' in window)) {
            el.textContent = '❌ Non supporté par ce navigateur.';
        } else if (Notification.permission === 'granted') {
            el.textContent = '✅ Autorisées par le navigateur.';
        } else if (Notification.permission === 'denied') {
            el.textContent = '❌ Bloquées — changez dans les réglages du navigateur.';
        } else {
            el.textContent = '⏳ Non encore autorisées.';
        }
    }

    // ── Volume ───────────────────────────────────────────────────────────
    function updateVolume(val) {
        var vv = document.getElementById('vol-val');
        if (vv) vv.textContent = val + '%';
    }

    // ── Effacer toutes les données locales ───────────────────────────────
    function clearAllData() {
        if (!confirm('Effacer tous vos paramètres et données locales ?\\nCette action est irréversible.')) return;
        try {
            localStorage.removeItem(SETTINGS_KEY);
            localStorage.removeItem('theme');
            localStorage.removeItem('lw_favorites');
            sessionStorage.clear();
        } catch(e) {}
        showFeedback('✅ Données effacées. Rechargement...', 'success');
        setTimeout(function() { location.reload(); }, 1500);
    }

    // ── Initialisation ────────────────────────────────────────────────────
    document.addEventListener('DOMContentLoaded', function() {
        // Charger depuis localStorage d'abord (instantané)
        var s = getSettings();
        window._currentTheme = s.theme;
        populateForm(s);

        // Puis essayer de récupérer les préférences serveur (theme + lang)
        fetch('/api/settings/load', { credentials: 'include' })
            .then(function(r) { return r.json(); })
            .then(function(data) {
                if (data.success && data.prefs) {
                    // Fusionner les prefs serveur (plus fiables pour theme/lang)
                    var merged = Object.assign({}, s, data.prefs);
                    setLocalSettings(merged);
                    populateForm(merged);
                }
            })
            .catch(function() { /* hors ligne — localStorage suffit */ });

        // Lier l'input volume
        var sv = document.getElementById('s-volume');
        if (sv) sv.addEventListener('input', function() { updateVolume(this.value); });
    });
</script>
{% endblock %}'''

    templates_map = {
        "base.html": BASE_TEMPLATE,
        "index.html": INDEX_TEMPLATE,
        "watch_external.html": WATCH_EXTERNAL_TEMPLATE,
        "watch_iptv.html": WATCH_IPTV_TEMPLATE,
        "watch_user.html": WATCH_USER_TEMPLATE,
        "go_live.html": GO_LIVE_TEMPLATE,
        "search.html": SEARCH_TEMPLATE,
        "playlist.html": PLAYLIST_TEMPLATE,
        "admin_login.html": ADMIN_LOGIN_TEMPLATE,
        "admin.html": ADMIN_TEMPLATE,
        "blocked.html": BLOCKED_TEMPLATE,
        "error.html": ERROR_TEMPLATE,
        "events.html": EVENTS_TEMPLATE,
        "settings.html": SETTINGS_TEMPLATE,
    }

    # Écriture des fichiers — avec gestion propre des erreurs d'encodage
    for filename, content in templates_map.items():
        filepath = f"templates/{filename}"
        try:
            # errors='replace' évite tout crash sur caractères non-UTF8
            with open(filepath, "w", encoding="utf-8", errors="replace") as f:
                f.write(content)
            logger.info(f"✅ Template écrit : {filename}")
        except Exception as e:
            logger.error(f"❌ Erreur écriture template {filename}: {e}")

# ==================== DÉMARRAGE ====================

if __name__ == "__main__":
    print("\n" + "=" * 80)
    print(f"🚀 {settings.APP_NAME} v{settings.APP_VERSION}")
    print("=" * 80)
    print(f"🌐 http://localhost:{port}")
    print(f"👤 Propriétaire: {settings.OWNER_ID} / {settings.ADMIN_PASSWORD}")
    print(f"📺 {len(EXTERNAL_STREAMS)} flux externes ({sum(1 for s in EXTERNAL_STREAMS if s['stream_type'] == 'youtube')} YouTube)")
    print(f"🌍 {len(IPTV_PLAYLISTS)} playlists IPTV (pays/subdivisions/villes/catégories)")
    print(f"▶️  yt-dlp: {'✅ disponible' if YT_DLP_AVAILABLE else '⚠️ non installé (fallback iframe)'}")
    print(f"🎬 Video.js: ✅ intégré")
    print("=" * 80 + "\n")

    # Démarrer le serveur
    port = int(os.environ.get("PORT", 8001))
    uvicorn.run(
        "livewatch:app",  
        host="0.0.0.0",   
        port=port,
        reload=False      
    )

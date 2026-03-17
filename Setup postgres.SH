#!/usr/bin/env bash
# =============================================================
#  Livewatch — Script d'installation PostgreSQL
#  Usage : bash setup_postgres.sh
# =============================================================
set -e

DB_USER=${PGUSER:-livewatch}
DB_PASS=${PGPASSWORD:-livewatch}
DB_NAME=${PGDATABASE:-livewatch}
DB_HOST=${PGHOST:-localhost}
DB_PORT=${PGPORT:-5432}

echo "╔══════════════════════════════════════════════╗"
echo "║   Livewatch — Setup PostgreSQL               ║"
echo "╚══════════════════════════════════════════════╝"

# ── 1. Vérifier que PostgreSQL est installé ──────────────────
if ! command -v psql &>/dev/null; then
    echo "⚠️  PostgreSQL n'est pas installé."
    echo "   Ubuntu/Debian : sudo apt install postgresql postgresql-contrib"
    echo "   macOS          : brew install postgresql@16"
    exit 1
fi
echo "✅ PostgreSQL trouvé : $(psql --version)"

# ── 2. Créer l'utilisateur et la base ───────────────────────
echo ""
echo "📦 Création de l'utilisateur et de la base de données..."

sudo -u postgres psql << SQL
-- Supprimer si existe déjà (pratique pour réinstall)
DROP DATABASE IF EXISTS ${DB_NAME};
DROP ROLE    IF EXISTS ${DB_USER};

-- Créer l'utilisateur
CREATE ROLE ${DB_USER} WITH
    LOGIN
    PASSWORD '${DB_PASS}'
    CREATEDB;

-- Créer la base
CREATE DATABASE ${DB_NAME}
    OWNER     = ${DB_USER}
    ENCODING  = 'UTF8'
    LC_COLLATE = 'fr_FR.UTF-8'
    LC_CTYPE   = 'fr_FR.UTF-8'
    TEMPLATE  = template0;

-- Extensions utiles
\connect ${DB_NAME}
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";  -- recherche full-text optimisée
SQL

echo "✅ Base '${DB_NAME}' créée avec l'utilisateur '${DB_USER}'"

# ── 3. Créer le .env si absent ──────────────────────────────
if [ ! -f .env ]; then
    SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(64))")
    cat > .env << ENV
DATABASE_URL=postgresql://${DB_USER}:${DB_PASS}@${DB_HOST}:${DB_PORT}/${DB_NAME}
SECRET_KEY=${SECRET}
ADMIN_USERNAME=WALKER92259
ADMIN_PASSWORD=WALKER92259
ADMIN_EMAIL=erickbenoit337@gmail.com
ENV
    echo "✅ Fichier .env créé"
else
    echo "ℹ️  Fichier .env déjà présent — non modifié"
fi

# ── 4. Installer les dépendances Python ─────────────────────
echo ""
echo "📦 Installation des dépendances Python..."
pip install psycopg2-binary alembic python-dotenv -q
echo "✅ Dépendances installées"

# ── 5. Initialiser Alembic si non déjà fait ─────────────────
if [ ! -d alembic ]; then
    echo ""
    echo "🔧 Initialisation d'Alembic..."
    alembic init alembic
    echo "✅ Alembic initialisé — éditez alembic/env.py si nécessaire"
fi

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║  ✅ Setup terminé !                          ║"
echo "║                                              ║"
echo "║  Lancer l'application :                      ║"
echo "║    python3 Livewatch.py                      ║"
echo "╚══════════════════════════════════════════════╝"
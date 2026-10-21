#!/usr/bin/env bash
# Create (or update) the Postgres user the public API connects as, and the discussion tables.
# It can only SELECT from the app's tables, every transaction is read-only unless the API opens
# it as read-write, and the only writes it's granted are to the discussion tables (new posts,
# reactions, hiding a post). Queries time out after 3s; at most 20 connections. Safe to re-run.
set -euo pipefail
source "$(dirname "$0")/env.sh"

PSQL="${PSQL:-/Library/PostgreSQL/18/bin/psql}"
ENV_FILE="$PROJECT_ROOT/.env"
env_value() { grep -E "^$1=" "$ENV_FILE" | head -1 | cut -d= -f2-; }

# Generate a password the first time and keep it in .env (never printed).
if [[ -z "$(env_value web_db_password)" ]]; then
  printf '\n# Read-only Postgres user for the public API (scripts/setup-web-db-user.sh)\nweb_db_password=%s\n' \
    "$(openssl rand -hex 24)" >> "$ENV_FILE"
  echo "Generated web_db_password in .env"
fi

export PGPASSWORD="$(env_value post_gres_password)"
export WEB_DB_PASSWORD="$(env_value web_db_password)"

# \getenv reads the password from the environment, so it never appears in the process list.
"$PSQL" -h localhost -U postgres -d billiontracker -v ON_ERROR_STOP=1 -q <<'SQL'
\getenv pw WEB_DB_PASSWORD
SELECT NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'billiontracker_web') AS missing \gset
\if :missing
  CREATE ROLE billiontracker_web LOGIN;
\endif
ALTER ROLE billiontracker_web WITH LOGIN PASSWORD :'pw' CONNECTION LIMIT 20;
ALTER ROLE billiontracker_web SET default_transaction_read_only = on;
ALTER ROLE billiontracker_web SET statement_timeout = '3s';

GRANT CONNECT ON DATABASE billiontracker TO billiontracker_web;
GRANT USAGE ON SCHEMA public TO billiontracker_web;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO billiontracker_web;
-- Tables Kafka Connect creates later (as postgres) are readable too.
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT SELECT ON TABLES TO billiontracker_web;

-- Discussion board (api/discussion.py): visitors' posts and reactions.
CREATE TABLE IF NOT EXISTS discussion_posts (
  id         bigserial PRIMARY KEY,
  author     text        NOT NULL CHECK (length(author) BETWEEN 1 AND 40),
  body       text        NOT NULL CHECK (length(body) BETWEEN 2 AND 1000),
  created_at timestamptz NOT NULL DEFAULT now(),
  hidden     boolean     NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS discussion_posts_created_idx ON discussion_posts (created_at, id) WHERE NOT hidden;
CREATE TABLE IF NOT EXISTS discussion_reactions (
  post_id    bigint      NOT NULL REFERENCES discussion_posts (id) ON DELETE CASCADE,
  visitor    text        NOT NULL,
  kind       text        NOT NULL CHECK (kind IN ('like', 'dislike')),
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (post_id, visitor, kind)
);
-- Two boards: the general discussion and the permanent feature-ideas board. A reply has a
-- parent_id (one level deep) and belongs to its parent's board.
ALTER TABLE discussion_posts ADD COLUMN IF NOT EXISTS board text NOT NULL DEFAULT 'general'
  CHECK (board IN ('general', 'ideas'));
ALTER TABLE discussion_posts ADD COLUMN IF NOT EXISTS parent_id bigint
  REFERENCES discussion_posts (id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS discussion_posts_board_idx ON discussion_posts (board, created_at, id)
  WHERE parent_id IS NULL AND NOT hidden;
CREATE INDEX IF NOT EXISTS discussion_posts_parent_idx ON discussion_posts (parent_id) WHERE parent_id IS NOT NULL;
-- Up/down votes on feature ideas: one per visitor per idea.
CREATE TABLE IF NOT EXISTS discussion_votes (
  post_id    bigint      NOT NULL REFERENCES discussion_posts (id) ON DELETE CASCADE,
  visitor    text        NOT NULL,
  value      smallint    NOT NULL CHECK (value IN (-1, 1)),
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (post_id, visitor)
);
-- Reactions are thumbs up or down only (earlier versions also had love/haha/wow/sad).
DELETE FROM discussion_reactions WHERE kind NOT IN ('like', 'dislike');
ALTER TABLE discussion_reactions DROP CONSTRAINT IF EXISTS discussion_reactions_kind_check;
ALTER TABLE discussion_reactions ADD CONSTRAINT discussion_reactions_kind_check CHECK (kind IN ('like', 'dislike'));
GRANT SELECT, INSERT ON discussion_posts TO billiontracker_web;
GRANT SELECT, INSERT, DELETE ON discussion_votes TO billiontracker_web;
GRANT UPDATE (value) ON discussion_votes TO billiontracker_web;
GRANT UPDATE (hidden) ON discussion_posts TO billiontracker_web;
GRANT USAGE ON SEQUENCE discussion_posts_id_seq TO billiontracker_web;
GRANT SELECT, INSERT, DELETE ON discussion_reactions TO billiontracker_web;

-- Indexes for the per-day history queries (tables exist once the JDBC Sink has written to them).
SELECT to_regclass('public.flight_enriched') IS NOT NULL AS has_flights,
       to_regclass('public.x_posts') IS NOT NULL AS has_posts \gset
\if :has_flights
  CREATE INDEX IF NOT EXISTS flight_enriched_ts_idx ON flight_enriched (ts);
\endif
\if :has_posts
  CREATE INDEX IF NOT EXISTS x_posts_created_at_idx ON x_posts (created_at);
\endif
SQL

echo "billiontracker_web is ready (read-only, plus writes to the discussion tables)."

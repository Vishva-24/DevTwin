-- devtwin_setup.sql
-- Run this as the PostgreSQL superuser (postgres) to create the DevTwin DB and user.
-- Execute from your terminal:
--   "C:\Program Files\PostgreSQL\16\bin\psql.exe" -U postgres -f devtwin_setup.sql

-- Create the application role (skip if it already exists)
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'devtwin') THEN
    CREATE ROLE devtwin LOGIN PASSWORD 'devtwin_pass';
    RAISE NOTICE 'Created role: devtwin';
  ELSE
    RAISE NOTICE 'Role devtwin already exists — skipping.';
  END IF;
END
$$;

-- Create the database (skip if it already exists)
SELECT 'CREATE DATABASE devtwin OWNER devtwin'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'devtwin')\gexec

-- Grant all privileges on the database
GRANT ALL PRIVILEGES ON DATABASE devtwin TO devtwin;

-- Confirm
\echo '--------------------------------------------'
\echo 'Setup complete. Databases:'
\l devtwin
\echo 'Roles:'
\du devtwin

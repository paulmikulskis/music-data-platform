-- Run against postgres as the cluster administrator, before roles.sql.
SELECT format('CREATE DATABASE %I', name)
FROM (VALUES ('control'), ('warehouse')) AS databases(name)
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = name)
\gexec

# Local MySQL with Docker

This setup gives Windows and macOS developers the same MySQL 8.0.46 development
environment without changing the snapshot schema owned by
`feat/contract-document-intake`.

## What Docker owns

- MySQL 8.0 server configuration;
- the `alphalawva` database and `alphalawva_app` application account;
- UTF-8 (`utf8mb4_0900_ai_ci`) defaults;
- a named volume that preserves local data across container restarts; and
- a health check used to distinguish startup from readiness.

Docker does not create application tables. The existing
`MySqlConfirmedSnapshotStore.ensure_schema()` remains responsible for creating
`confirmed_contract_snapshots` and `confirmed_registry_snapshots`. Later result
storage migrations should remain in application-owned migration or schema
code, not be duplicated in the Compose file.

## One-time setup

1. Install and start Docker Desktop.
2. Copy `.env.example` to the untracked `.env` if the project does not already
   have one.
3. In `.env`, replace both placeholder passwords with different strong local
   passwords. Do not commit that file or send the passwords in chat.
4. Keep these application connection values:

   ```dotenv
   SNAPSHOT_STORE_BACKEND=mysql
   MYSQL_HOST=127.0.0.1
   MYSQL_PORT=3307
   MYSQL_DATABASE=alphalawva
   MYSQL_USER=alphalawva_app
   ```

`MYSQL_HOST_PORT=3307` publishes the container on host port 3307 while MySQL
continues to use port 3306 inside the container. A Windows-installed MySQL
server may therefore remain on host port 3306 during the transition without a
port collision.

## Start and inspect

Run from the repository root:

```bash
docker compose -f compose.mysql.yml up -d
docker compose -f compose.mysql.yml ps
```

Wait until `alphalawva-mysql-dev` reports `healthy` before starting the API.
The first snapshot save or read after the friend's branch is integrated calls
`ensure_schema()` and creates the snapshot tables automatically.

To intentionally run the existing MySQL integration test after that branch is
integrated:

```bash
RUN_MYSQL_INTEGRATION_TESTS=1 python -m unittest tests.test_mysql_snapshot_store_integration
```

## Stop, reset, and port choices

Stop MySQL while preserving data:

```bash
docker compose -f compose.mysql.yml stop
```

Remove the container while preserving the named volume:

```bash
docker compose -f compose.mysql.yml down
```

`docker compose -f compose.mysql.yml down -v` also deletes the local database
volume and is destructive. Use it only when the team explicitly wants a clean
database.

If port 3307 is already occupied, the team must choose one other common host
port and update both `MYSQL_HOST_PORT` and `MYSQL_PORT` to the same value.

## Branch integration order

1. Validate this Compose setup on `chore/mysql-docker-dev`.
2. Integrate or merge the friend's `feat/contract-document-intake` branch.
3. Run the snapshot-store integration test against Docker MySQL.
4. Create `feat/user-result-persistence` from the integrated baseline.
5. Add user result tables and APIs without changing the existing snapshot
   tables unless the team agrees on a schema migration.

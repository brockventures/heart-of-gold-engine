# Upgrading Karakos

Manual upgrade instructions. Karakos does not auto-update.

## Version Check

The system checks for updates weekly and posts to #signals if a newer version is available. You can also check manually:

```bash
bin/check-updates.sh
```

## Upgrade Process

### 1. Stop the System

```bash
docker compose down
```

Wait for graceful shutdown (agents finalize sessions, up to 45 seconds).

### 2. Back Up Data

```bash
# Back up the data directory (messages, memory, databases)
cp -r data/ data-backup-$(date +%Y%m%d)/

# Back up config (contains credentials)
cp config/.env config/.env.backup
```

### 3. Pull Updates

```bash
git pull origin main
```

If you have local modifications, stash or commit them first:

```bash
git stash
git pull origin main
git stash pop
```

### 4. Check for Breaking Changes

Read the release notes for your version jump. Breaking changes are documented in the GitHub release.

### 5. Rebuild and Start

```bash
docker compose up --build -d
```

The `--build` flag rebuilds the container with any new dependencies or dashboard changes.

### 6. Verify

1. Check `docker compose logs -f` for startup errors
2. Open the dashboard and verify agent status
3. Check #signals for the startup health report

## Schema Migrations

If the upgrade includes database schema changes, the agent server handles them automatically on startup. It checks the current schema version and applies any pending migrations.

Manual migration (if needed):

```bash
docker exec -it karakos-karakos-1 python3 bin/migrate.py
```

## Rolling Back

If something breaks:

```bash
docker compose down

# Restore data backup
rm -rf data/
mv data-backup-YYYYMMDD/ data/

# Restore config
cp config/.env.backup config/.env

# Check out previous version
git checkout v1.0.0  # or whatever version you were on

# Rebuild
docker compose up --build -d
```

## Version History

Check the GitHub releases page for the full changelog.

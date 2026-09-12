# Upgrading

> [简体中文](upgrade_zh.md)

`bash scripts/upgrade.sh` installs the dependency versions approved by the
checked-out MuseLab release. The exact SDK pin and `uv.lock` are intentional;
this command does not claim to install the newest upstream SDK.

## Native installation

Before changing application code, let running tasks finish and back up the
state described in [Data and backup](data-and-backup.md). Preserve local edits
and record the current revision. Update the code using a fast-forward only:

```bash
cd ~/muselab
git status --short
git rev-parse HEAD
git pull --ff-only
bash scripts/upgrade.sh
```

The script installs frozen dependencies into a private candidate directory under
`.venv-builds/`, then runs unit/integration tests with a disposable workspace and
session directory. Only successful candidates replace `.venv`. Installation or
test failures leave the prior environment untouched; private logs identify the
failure. `bash scripts/upgrade.sh --check` validates without activation.

The SDK's bundled Claude CLI is part of that environment. The standalone system
`claude` executable, dependency files, `.env` and user data are not modified by
the script. Maintainers update SDK/CLI pins together and run compatibility checks
before releasing; upgrading a standalone CLI is a separate, deliberate action.

## Restart and recovery

Restart the exact application service after validation, when it has no running
tasks. For standard user installations:

```bash
# Linux user service; custom deployments may use a different unit/scope.
systemctl --user restart muselab
# macOS
launchctl kickstart -k gui/$UID/com.muselab
```

Verify the application health and open a fresh page to load the matching
frontend. The script retains the previous environment at the path printed on
success. Keep `.venv-builds/`: the active `.venv` points into it. If a later
runtime issue requires rollback, stop the exact service, restore the matching
code revision while preserving local edits, and switch `.venv` back to the
retained previous environment before restarting. Do not restore dependency
files with a command that discards unrelated working-tree edits.

This is environment staging, not a rollback of code, user files or data
migrations. [Data and backup](data-and-backup.md) describes the persistent state.

## Docker: choose the installation source

**First upgrade to the persistent configuration layout: migrate before replacing the container.** Older images wrote UI settings and third-party transcripts outside mounted directories. Follow the [migration guide](docker-state-migration.md) to stop the exact old container, export and migrate that state before the commands below. The migration tool never stops, removes or recreates containers.

For the source-build `docker-compose.yml`:

```bash
git pull --ff-only
docker compose build --pull
docker compose up -d
```

For the separate prebuilt-image configuration, set `MUSELAB_IMAGE` to a release
or SHA tag when you need a reproducible version:

```bash
# Example .env setting: MUSELAB_IMAGE=ghcr.io/hesorchen/muselab:sha-<revision>
docker compose -f docker-compose.image.yml pull
docker compose -f docker-compose.image.yml up -d
```

Use the same Compose file, project directory and data mounts throughout an
installation. Record the previous image tag/digest before replacing it; rollback
uses that image with the same mounts. In the new layout, workspace files, Claude state, sessions, UI settings and third-party transcripts live in host mounts. Compose `.env` supplies bootstrap values; UI edits persist in `sessions/config/.env` and take precedence on restart. Rolling back to an older-layout image also requires restoring the preserved backup to its old paths; changing only the image is insufficient. Check [Quick start](quickstart.md) for UID/GID compatibility.

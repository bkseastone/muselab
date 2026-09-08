# Migrate state from a legacy Docker container

[中文](docker-state-migration_zh.md)

Older images could keep runtime settings and SDK state in the container's writable layer. Preserve these files **before replacing the old container**. The migration tool copies from one explicitly named, stopped container and retains it. Docker supports copying files from stopped containers and streaming their contents as tar archives. [Docker `cp` reference](https://docs.docker.com/reference/cli/docker/container/cp/)

## Supported host and destination

Run this tool on **Linux or inside WSL**, with Python 3.11 or newer and access to the Docker daemon that owns the old container. It uses Linux `renameat2(RENAME_NOREPLACE)` for atomic directory publication. Native macOS and native Windows are unsupported; the tool exits before changing state on these hosts. Docker Desktop for Windows users can run it inside WSL when that distribution can access the same Docker daemon and host sessions directory. For Docker Desktop on macOS, retain the old container and make a separate, verified manual backup; do not run this Linux tool directly on macOS.

Use the existing host directory mounted at `/app/sessions`, usually `./sessions`. Run as that directory's owner. Migrated files use mode `0600`, directories `0700`, and the invoking user's ownership. The new image's `MUSE_UID`/`MUSE_GID` build values must match the bind-mount owner; published images default to UID/GID 1000. Check ownership before the later upgrade rather than broadening credential permissions.

| Legacy container path | New path relative to host `./sessions` |
|---|---|
| `/app/.env` | `config/.env` |
| `/app/mcp.json` | `config/mcp.json` |
| `/app/provider_overrides.json` | `config/provider_overrides.json` |
| `/home/muse/.local/state/muselab/vendor-cli` | `state/muselab/vendor-cli` |

The new container uses `MUSELAB_CONFIG_DIR=/app/sessions/config`, `MUSELAB_ENV_PATH=/app/sessions/config/.env`, and `XDG_STATE_HOME=/app/sessions/state`. Existing session files in `./sessions` stay in place. Workspace data, the host Compose `.env`, and mounted Claude credentials are separate backup items; this tool does not create a full-service backup.

## Run the migration

1. Identify the exact old container and its host sessions mount. Stop that container without removing it. For an instance actually named `muselab`:

   ```bash
   docker stop muselab
   python3 scripts/migrate-docker-state.py \
     --container muselab \
     --sessions-dir "$PWD/sessions"
   ```

2. Read the success receipt. It reports `phase: complete`, the published directories, and a private `.muselab-docker-migration-*` backup directory under sessions. That directory contains each original tar archive, its SHA256 in `manifest.json`, and staging/recovery information. Treat it as credential-bearing data.

3. Confirm the manifest's `present` entries match the state expected from the old installation. Only Docker's explicit source-file-absence response is accepted as missing; a daemon, permission, transport, malformed archive, or unrecognized error aborts the migration. An absent legacy `.env` can be legitimate when configuration came entirely from environment variables, but verify the host Compose configuration separately.

4. Continue the normal upgrade only after migration and backup verification. Verify the new instance's settings, MCP configuration, providers, and SDK session history before retiring the private backup. The tool itself never stops, removes, recreates, or starts a container.

## Conflicts and recovery

The tool backs up **all four sources before publication**. It refuses any existing `config` directory or `state/muselab/vendor-cli` target, even an empty directory, and never merges or overwrites it. Archive entries that are links, special files, or escape their source root also abort; their original tar archives remain available for careful manual recovery.

A failed run retains the old container, raw archives, and `manifest.json`. Ordinary publication failures attempt to return only this run's published directories to its private staging directory. A killed process or filesystem failure can leave partial publication; inspect the manifest and both target directories before retrying. Do not delete a conflicting target blindly: retain it separately, compare it with the staged copy, and resolve which state to keep. Once every conflict is resolved, rerun against the same stopped old container; a fresh backup is created.

Before replacing the old container, recovery can simply use that retained container and its original state. After replacement, the raw archives and the separate full-service backup are the recovery sources. Keep these backups until the new release has been validated. The migration has CLI-substitute tests; a real Docker recreate test remains part of release CI validation.

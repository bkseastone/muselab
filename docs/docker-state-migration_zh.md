# 从旧版 Docker 容器迁移状态

[English](docker-state-migration.md)

旧版镜像可能把运行时设置和 SDK 状态保存在容器可写层中。请在替换旧容器前保留这些文件。迁移工具只从明确指定、已经停止的容器复制数据，并保留原容器。Docker 支持从停止的容器复制文件，也支持将结果输出为 tar 归档。[Docker `cp` 官方说明](https://docs.docker.com/reference/cli/docker/container/cp/)

## 支持的平台与目标目录

工具仅支持 **Linux 或 WSL**，要求 Python 3.11 及以上，并能访问旧容器所在的 Docker daemon。它通过 Linux `renameat2(RENAME_NOREPLACE)` 原子发布目录，保证目标存在时不覆盖。原生 macOS、原生 Windows 不受支持，工具会在改变状态前退出。Windows Docker Desktop 用户可在能够访问同一个 Docker daemon 和宿主机 sessions 目录的 WSL 中运行。macOS Docker Desktop 用户应先保留旧容器并制作独立、经过验证的人工备份，不要直接在 macOS 运行这个 Linux 工具。

目标是原本挂载到 `/app/sessions` 的宿主机目录，通常为 `./sessions`。以该目录的拥有者运行工具。新文件权限为 `0600`，目录为 `0700`，拥有者为执行用户；后续新镜像的 `MUSE_UID`／`MUSE_GID` 构建值应与挂载目录拥有者一致，发布镜像默认 UID／GID 为 1000。升级前核对所属用户，不要通过放宽凭据权限解决权限不匹配。

| 旧容器路径 | 宿主机 `./sessions` 下的新相对路径 |
|---|---|
| `/app/.env` | `config/.env` |
| `/app/mcp.json` | `config/mcp.json` |
| `/app/provider_overrides.json` | `config/provider_overrides.json` |
| `/home/muse/.local/state/muselab/vendor-cli` | `state/muselab/vendor-cli` |

新容器使用 `MUSELAB_CONFIG_DIR=/app/sessions/config`、`MUSELAB_ENV_PATH=/app/sessions/config/.env`、`XDG_STATE_HOME=/app/sessions/state`。原有 `./sessions` 会话文件保留。工作空间、宿主机 Compose `.env`、单独挂载的 Claude 凭据仍需分别备份；这个工具不代替完整服务备份。

## 执行迁移

1. 先确认旧容器的准确名称以及它使用的宿主机 sessions 挂载目录。停止这个容器并保留它。例如，实际实例确实名为 `muselab` 时：

   ```bash
   docker stop muselab
   python3 scripts/migrate-docker-state.py \
     --container muselab \
     --sessions-dir "$PWD/sessions"
   ```

2. 查看成功回执。它包含 `phase: complete`、已发布目录及 sessions 下的私有 `.muselab-docker-migration-*` 备份目录。该目录保存原始 tar、`manifest.json` 中的 SHA256，以及暂存和恢复信息。备份可能包含凭据，应作为私有数据保留。

3. 核对 manifest 中各源的 `present` 状态是否符合旧安装的实际情况。只有 Docker 明确表示源路径不存在时才跳过；daemon、权限、传输、归档损坏或无法识别的错误均终止迁移。旧配置完全来自环境变量时，容器内 `.env` 不存在可能正常，仍应单独核对宿主机 Compose 配置。

4. 迁移完成并核对备份后，再继续正常升级流程。验证新版设置、MCP 配置、提供商和 SDK 会话历史后，再决定备份保留期限。工具本身不会停止、删除、重建或启动任何容器。

## 冲突与恢复

工具先备份全部四个源，再发布目标。`config` 或 `state/muselab/vendor-cli` 已存在时，即使只是空目录，也会停止，不自动合并或覆盖。归档含符号链接、硬链接、特殊文件或越过源根目录的条目时也会停止，原始 tar 保留供人工核查恢复。

失败后，旧容器、原始归档和 `manifest.json` 均保留。常规发布失败会尝试仅把本轮刚发布的目录退回私有暂存区。进程被强制终止或文件系统异常时，可能留下部分发布；重试前先核对 manifest 和两个目标目录。不要直接删除冲突目录：先单独保留它，与暂存副本比较，明确要保留的状态后再处理。解决全部冲突后，可针对同一个停止的旧容器重新运行，工具会制作新的备份。

替换旧容器前，可以使用保留的原容器及其原始状态恢复。替换之后，则以原始归档和独立的完整服务备份作为恢复来源。在新版完成验证前保留这些备份。本工具已用 Docker CLI 替身测试；真实 Docker 重建验证仍由发布 CI 完成。

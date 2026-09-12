# 升级

> [English](upgrade.md)

`bash scripts/upgrade.sh` 安装当前 MuseLab 版本已经批准的依赖组合。
SDK 精确版本与 `uv.lock` 是有意保留的兼容边界；脚本不声称安装上游最新 SDK。

## 原生安装

修改应用代码前，让运行中的任务完成，按[数据与备份](data-and-backup_zh.md)
备份状态，保留本地修改并记录当前版本。仅以快进方式更新代码：

```bash
cd ~/muselab
git status --short
git rev-parse HEAD
git pull --ff-only
bash scripts/upgrade.sh
```

脚本在私有的 `.venv-builds/` 候选目录中安装冻结依赖，使用临时工作区和会话
目录运行单元／集成测试。只有通过验证的候选环境才会替换 `.venv`。安装或
测试失败时，原环境保持不变，具体原因保存在私有日志中。
`bash scripts/upgrade.sh --check` 只验证候选环境，不启用它。

SDK 捆绑的 Claude CLI 随候选环境一起安装。脚本不修改系统独立安装的
`claude`、依赖文件、`.env` 或用户数据。维护者先同步更新 SDK／CLI pin 并
通过兼容性检查，再发布版本；系统独立 CLI 的升级是另一项明确操作。

## 重启与恢复

验证后，在没有运行任务时重启确切的应用服务。标准用户级安装示例：

```bash
# Linux 用户服务；自定义部署可能使用不同 unit 或 scope。
systemctl --user restart muselab
# macOS
launchctl kickstart -k gui/$UID/com.muselab
```

确认应用健康并重新打开页面以加载匹配的前端。脚本成功后会打印保留的旧环境
路径。请保留 `.venv-builds/`，当前 `.venv` 会指向其中的候选环境。
若随后出现运行时问题，先停止确切服务，保留本地修改并恢复匹配的代码版本，
再将 `.venv` 切回保留的旧环境后重启。不要用会丢弃既有修改的命令恢复依赖文件。

这套流程暂存和切换的是环境，不会回滚代码、用户文件或数据迁移。
持久化状态见[数据与备份](data-and-backup_zh.md)。

## Docker：按安装来源升级

**首次升级到持久配置布局：先迁移，再替换容器。** 旧镜像把网页配置和第三方会话正文放在未挂载路径，直接 recreate 会丢失它们。先按[迁移指南](docker-state-migration_zh.md)停止准确的旧容器、导出并迁移；确认完成后再执行以下命令。迁移工具不会停止、删除或重建任何容器。

使用源码构建的 `docker-compose.yml`：

```bash
git pull --ff-only
docker compose build --pull
docker compose up -d
```

使用独立的预构建镜像配置时，需要可复现版本可在 `.env` 中将 `MUSELAB_IMAGE`
设为具体 release 或 SHA tag：

```bash
# .env 示例：MUSELAB_IMAGE=ghcr.io/hesorchen/muselab:sha-<revision>
docker compose -f docker-compose.image.yml pull
docker compose -f docker-compose.image.yml up -d
```

同一安装应始终使用相同的 Compose 文件、项目目录和数据挂载。替换前记录旧
镜像 tag／digest；回退时使用旧镜像和同一组挂载。新布局的工作区、Claude 状态、会话、网页配置与第三方正文保留在宿主机挂载中。Compose 的 `.env` 是初始环境，网页修改写入 `sessions/config/.env` 并在重启时优先读取。回退到旧布局镜像时，必须保留迁移备份并按旧路径恢复，不能只切换镜像。UID／GID 兼容方式见[快速开始](quickstart_zh.md)。

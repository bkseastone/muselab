#!/usr/bin/env bash
# muse.sh - muselab Docker 日常运维
# 用法: ./muse.sh <command>    (./muse.sh help 看全部)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

CONTAINER=muselab
PORT=8765
LAN_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
LAN_IP=${LAN_IP:-127.0.0.1}

if [[ -t 1 ]]; then
  C_G=$'\033[32m'; C_Y=$'\033[33m'; C_R=$'\033[31m'; C_B=$'\033[1m'; C_0=$'\033[0m'
else
  C_G=''; C_Y=''; C_R=''; C_B=''; C_0=''
fi
ok()   { echo "${C_G}✓${C_0} $*"; }
warn() { echo "${C_Y}!${C_0} $*"; }
err()  { echo "${C_R}✗${C_0} $*" >&2; }

require_env()     { [[ -f .env ]] || { err ".env 不存在，先 cp .env.example .env 并填 MUSELAB_TOKEN"; exit 1; }; }
require_running() { docker ps --format '{{.Names}}' | grep -qx "$CONTAINER" || { err "容器 $CONTAINER 未运行，先 ./muse.sh up"; exit 1; }; }

# ---------- 生命周期 ----------
cmd_up()      { require_env; docker compose up -d; ok "已启动: http://$LAN_IP:$PORT"; }
cmd_down()    { docker compose down; ok "已停止 (数据/配置在挂载卷，未删除)"; }
cmd_restart() { require_running; docker compose restart; ok "已重启"; }
cmd_rebuild() { require_env; warn "重新构建镜像并启动..."; docker compose up -d --build; ok "已重建并启动"; }
cmd_update() {
  # docker-compose.yml 是 tracked 文件，若本地有定制改动，git pull 会冲突。
  # 建议把定制放进 docker-compose.override.yml (compose 自动合并，且可 gitignore)。
  if ! git diff --quiet || ! git diff --cached --quiet; then
    warn "有未提交的改动:"
    git status -s
    err "请先 commit 或 stash，避免 git pull 覆盖 docker-compose.yml 等定制"
    exit 1
  fi
  warn "git pull..."; git pull
  warn "重建并启动..."; docker compose up -d --build
  ok "更新完成"
}

# ---------- 查看 / 调试 ----------
cmd_logs()  { require_running; docker logs -f "$CONTAINER"; }
cmd_shell() { require_running; docker exec -it "$CONTAINER" bash; }

cmd_status() {
  echo "${C_B}== 容器 ==${C_0}"
  docker compose ps 2>/dev/null || warn "compose 未运行"
  echo
  echo "${C_B}== 健康 ==${C_0}"
  if curl -sf --max-time 3 "http://127.0.0.1:$PORT/api/health" >/dev/null; then
    ok "服务正常"
  else
    err "健康检查失败 (服务未启动?)"
  fi
  echo
  echo "${C_B}== 访问 ==${C_0}"
  echo "  本机: http://127.0.0.1:$PORT"
  echo "  LAN : http://$LAN_IP:$PORT"
  echo
  echo "${C_B}== 挂载点 (宿主机直接编辑，不进 docker) ==${C_0}"
  echo "  工作区 : ./data                       -> /data"
  echo "  会话   : ./sessions                   -> /app/sessions"
  echo "  后端   : ./backend                    -> /app/backend   (改后自动 reload)"
  echo "  前端   : ./frontend                   -> /app/frontend  (改后刷新浏览器)"
  echo "  skills : ./skills                     -> /app/skills"
  echo "  配置   : ./provider_overrides.json,"
  echo "           ./mcp.json                   -> /app/"
}

cmd_health() {
  if curl -sf --max-time 3 "http://127.0.0.1:$PORT/api/health"; then
    echo; ok "健康"
  else
    err "健康检查失败"; return 1
  fi
}

cmd_token() { require_env; grep '^MUSELAB_TOKEN=' .env | sed 's/^MUSELAB_TOKEN=/  token: /'; }

cmd_dev() {
  cat <<EOF
${C_B}开发模式热更新${C_0}
  改 backend/*.py  -> 自动 reload (polling, ~1-3s), 无需任何命令
  改 frontend/*    -> 浏览器刷新即见
  改 skills/*      -> 下次 Agent 调用生效
  改 Dockerfile/依赖 -> ${C_Y}./muse.sh rebuild${C_0}

看 reload 日志: ${C_Y}./muse.sh logs${C_0}
  (改代码后会显示 "WatchFiles detected changes ... Reloading")
EOF
}

# ---------- 备份 ----------
cmd_backup() {
  local ts dest
  ts=$(date +%Y%m%d-%H%M%S)
  dest="backups/muselab-$ts.tar.gz"
  mkdir -p backups
  tar czf "$dest" data sessions provider_overrides.json mcp.json .env 2>/dev/null
  ok "已备份: $dest"
  echo "  内容: data/ sessions/ provider_overrides.json mcp.json .env"
  echo "  恢复: tar xzf $dest  (在项目根解压覆盖)"
}

# ---------- 帮助 ----------
cmd_help() {
  cat <<EOF
${C_B}muselab 运维脚本${C_0}

${C_B}生命周期${C_0}
  up          启动 (用现有镜像)
  down        停止并删除容器 (保留数据/配置)
  restart     重启容器
  rebuild     重新构建镜像并启动 (改 Dockerfile/依赖后用)
  update      git pull + rebuild (升级到最新代码)

${C_B}查看 / 调试${C_0}
  status      容器状态 + 健康 + 挂载点一览
  logs        跟踪日志 (Ctrl+C 退出)
  health      健康检查 (/api/health)
  token       显示登录 token
  shell       进入容器 bash (调试用)
  dev         热更新说明

${C_B}备份${C_0}
  backup      备份 data/sessions/配置 到 backups/

${C_B}提示${C_0}
  源码已挂载: 改 backend/frontend/skills 不用进容器
  隔离保证:   容器只能访问上述挂载点, 碰不到本机其他文件
EOF
}

case "${1:-help}" in
  up|down|restart|rebuild|update|logs|status|health|token|shell|dev|backup) "cmd_$1" "${@:2}" ;;
  help|-h|--help|"") cmd_help ;;
  *) err "未知命令: $1"; echo; cmd_help; exit 1 ;;
esac

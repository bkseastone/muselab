# MuseLab 浏览器测试

Playwright＋pytest 覆盖实际前端 DOM、键盘／触摸、SSE、历史恢复和文件预览。
模型输入使用受控事件／夹具，测试不需要真实 provider 凭据。

## 安装与运行

在项目目录中同步已经声明的依赖，无需再次执行 `uv add`：

```bash
uv sync --frozen
uv run --frozen playwright install --with-deps chromium
RUN_E2E=1 uv run --frozen pytest tests/e2e/ -q
```

定向运行例子：

```bash
RUN_E2E=1 uv run --frozen pytest tests/e2e/test_ux_reliability.py -q
```

`backend_url` fixture 自动创建临时工作区、会话目录、随机端口和独立后端，并在
结束后停止它。无需另开终端启动服务器，也不要将测试指向生产服务。
浏览器二进制与 Playwright 版本应配套；依赖更新后重新安装 Chromium。

## 本地与 CI

未设置 `RUN_E2E=1` 时，浏览器用例会收集后跳过。CI 同时运行快速 core 子集和
完整浏览器套件，两者都阻塞发布；完整套件允许短暂失败重试，最终失败仍阻塞。
完整覆盖还依赖实际启用的环境，不能把本地默认 pytest 的跳过项称为浏览器通过。

fixture 使用较长的合成 token；应用实际最低校验门槛为 16 字符。推荐保持
fixture 的较长值。生产认证信息、真实对话正文和用户附件不得用于测试快照。

## 新增场景

按触发行为选择已有测试文件，优先验证最终可见内容、元素身份、网络边界和
实际控件几何。响应式检查要确认控件本身位于视口内；页面没有横向滚动条，
并不能证明内部内容没有被裁掉。更长的任务应使用有界数据量和明确等待条件。

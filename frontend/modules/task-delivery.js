/* Task results use server-observed evidence, never assistant success prose. */
window.museTaskDelivery = function () {
  return {
    taskDelivery: { show: false, loading: false, sid: "", selectedTurn: "", data: null, error: "", preview: null, restoring: false, confirmation: false, seq: 0 },
    runtimeIdentity: null,
    deliverySurfaceIdentity: null,
    _deliverySurfaceKey: "",
    _deliverySurfaceSeq: 0,
    _taskRuntimeSeq: 0,
    async fetchTaskRuntime(sid) {
      const seq = ++this._taskRuntimeSeq;
      this.runtimeIdentity = null;
      if (!sid || !this.authed || this.workspaceSwitching) return;
      const result = await this.api(`/api/chat/sessions/${encodeURIComponent(sid)}/runtime`);
      if (seq === this._taskRuntimeSeq && sid === this.currentId && result.ok) this.runtimeIdentity = result.data;
    },
    async fetchDeliverySurfaceIdentity(surface, cwd) {
      const sid = this.currentId;
      const key = [sid, surface, cwd, this.authed, this.workspaceSwitching].join("|");
      if (key === this._deliverySurfaceKey) return;
      this._deliverySurfaceKey = key;
      const seq = ++this._deliverySurfaceSeq;
      this.deliverySurfaceIdentity = null;
      if (!sid || !cwd || !this.authed || this.workspaceSwitching) return;
      const result = await this.api(`/api/chat/sessions/${encodeURIComponent(sid)}/runtime`, { query: { workspace: cwd } });
      if (seq === this._deliverySurfaceSeq && result.ok) this.deliverySurfaceIdentity = { ...result.data, surface };
    },
    deliverySurfaceLabel() {
      const r = this.deliverySurfaceIdentity;
      const source = this.previewSurface === "terminal" ? (this.lang === "zh" ? "终端进程" : "Terminal process") : (this.lang === "zh" ? "文件服务" : "File service");
      if (!r || r.surface !== this.previewSurface) return source;
      return [source, r.workspace.split("/").filter(Boolean).pop(), r.branch, r.is_worktree ? "worktree" : "", r.dirty === true ? (this.lang === "zh" ? "有修改" : "modified") : ""].filter(Boolean).join(" · ");
    },
    deliveryError(error, fallback) {
      if (typeof error === "string") return this.deliveryLabel(error);
      if (error && typeof error === "object") return [this.deliveryLabel(error.code || fallback), error.recovery_id ? `Recovery ID: ${error.recovery_id}` : ""].filter(Boolean).join(" · ");
      return fallback;
    },
    async openTaskDelivery(turnId = "") {
      const sid = this.currentId;
      if (!sid) return;
      this.taskDelivery.show = true;
      this.taskDelivery.sid = sid;
      this.taskDelivery.selectedTurn = turnId;
      this.taskDelivery.preview = null;
      this.taskDelivery.lastRestore = null;
      this.taskDelivery.confirmation = false;
      await this.refreshTaskDelivery();
    },
    async refreshTaskDelivery() {
      const state = this.taskDelivery;
      const seq = ++state.seq;
      const sid = state.sid;
      state.loading = true;
      state.error = "";
      const result = await this.api(`/api/chat/sessions/${encodeURIComponent(sid)}/delivery`, { query: { turn_id: state.selectedTurn } });
      if (seq !== state.seq || sid !== state.sid) return;
      state.loading = false;
      if (!result.ok) { state.error = String(result.error || "Delivery unavailable"); return; }
      state.data = result.data;
      if (sid === this.currentId) {
        this.runtimeIdentity = result.data.runtime;
        if (this.deliverySurfaceIdentity?.workspace === result.data.runtime.workspace) this.deliverySurfaceIdentity = { ...result.data.runtime, backend: "MuseLab workspace service", surface: this.previewSurface };
      }
    },
    closeTaskDelivery() {
      if (this.taskDelivery.restoring) return;
      this.taskDelivery.show = false;
      this.taskDelivery.seq++;
      this.taskDelivery.preview = null;
    },
    async previewTaskCheckpoint(checkpointId) {
      const state = this.taskDelivery;
      state.error = "";
      const seq = ++state.seq;
      state.confirmation = false;
      state.preview = null;
      const sid = state.sid;
      const result = await this.api(`/api/chat/sessions/${encodeURIComponent(sid)}/checkpoints/${encodeURIComponent(checkpointId)}/preview`);
      if (state.sid !== sid || !state.show || state.seq !== seq) return;
      if (!result.ok) { state.error = this.deliveryError(result.error, "Checkpoint unavailable"); return; }
      state.preview = result.data;
    },
    async restoreTaskCheckpoint() {
      const state = this.taskDelivery;
      if (!state.confirmation || !state.preview?.can_restore || state.restoring) return;
      state.restoring = true;
      const sid = state.sid;
      const result = await this.api(`/api/chat/sessions/${encodeURIComponent(sid)}/checkpoints/${encodeURIComponent(state.preview.checkpoint_id)}/restore`, { method: "POST", json: { token: state.preview.token, confirmed: true } });
      state.restoring = false;
      state.preview = null;
      state.confirmation = false;
      if (!result.ok) { state.error = this.deliveryError(result.error, "Restore could not be verified; refresh before retrying"); return; }
      state.error = result.data.verified ? "" : (this.lang === "zh" ? "部分文件未恢复，请查看恢复结果。" : "Some files were not restored.");
      state.lastRestore = result.data;
      this.toast(result.data.verified ? (this.lang === "zh" ? "文件恢复已核验，对话保持不变" : "File restore verified; conversation preserved") : state.error, result.data.verified ? "success" : "warn");
      await this.refreshTaskDelivery();
    },
    async openDeliveryArtifact(item) {
      const state = this.taskDelivery;
      const cwd = state.data?.runtime?.workspace;
      const opened = await this._openSessionFromDeeplink(state.sid, cwd || "");
      if (!opened) return;
      await this.openFile({ path: item.path, name: item.path.split("/").pop(), type: "file" }, { reveal: true });
      this.closeTaskDelivery();
    },
    async openDeliveryEvidence(item) {
      const sid = this.taskDelivery.sid;
      const id = item.message_id || item.id;
      this.closeTaskDelivery();
      await this._jumpToMessage(sid, id);
    },
    deliveryLabel(code) {
      const labels = {
        no_recorded_task_baseline: ["此历史任务没有记录起点，无法追溯精确变更。", "No recorded task baseline is available."],
        test_assertions_not_inferred_from_prose: ["工具完成不等于测试通过；测试断言请打开命令证据核对。", "A completed tool does not establish passing tests; inspect command evidence."],
        no_command_evidence: ["没有记录到执行命令的证据。", "No executed command evidence was recorded."],
        some_commands_have_no_structured_exit_code: ["部分命令未返回结构化退出码，状态保留为未知。", "Some commands have no structured exit code."],
        tool_evidence_limit_reached: ["工具证据达到保留上限，请查看完整对话。", "Tool evidence reached its retention limit; inspect the conversation."],
        historical_evidence_without_task_baseline: ["历史记录仅展示最近的规范事件；没有任务起点或完整运行状态。", "Recent canonical events only; no task baseline or complete lifecycle is available."],
        history_only: ["历史证据", "Historical evidence"], outcome_unrecorded: ["结果未记录", "Outcome unrecorded"],
        tool_completed: ["工具已完成", "Tool completed"], tool_failed: ["工具失败", "Tool failed"],
        running: ["运行中", "Running"], completed: ["任务已结束", "Task ended"], failed: ["任务失败", "Task failed"], interrupted: ["已中断", "Interrupted"],
        file_changed_outside_observed_tools: ["文件被其他操作修改，已阻止覆盖", "File changed outside observed tools"],
        external_edit_between_tools: ["工具执行间发生其他文件修改", "External edits occurred between tool calls"],
        checkpoint_already_invalidated: ["此检查点已失效", "Checkpoint invalidated"],
        no_observed_restorable_files: ["没有可核验的文件恢复范围", "No verifiable restore scope"],
        workspace_identity_changed: ["工作目录身份已变化", "Workspace identity changed"],
        unsafe_or_unobserved_path: ["包含无法安全核验的路径", "An unsafe or unobserved path is present"],
        incomplete_file_observation: ["文件修改记录尚未完整", "File observation is incomplete"],
        unsafe_or_missing_file: ["文件已移动或路径不安全", "A file moved or its path is unsafe"],
      };
      return labels[code]?.[this.lang === "zh" ? 0 : 1] || code;
    },
    deliveryRuntimeLabel() {
      const r = this.runtimeIdentity;
      if (!r || r.session_id !== this.currentId) return "";
      return [r.host, r.branch || (this.lang === "zh" ? "非 Git" : "No Git"), r.is_worktree ? "worktree" : "", r.dirty === true ? (this.lang === "zh" ? "有修改" : "modified") : ""].filter(Boolean).join(" · ");
    },
  };
};

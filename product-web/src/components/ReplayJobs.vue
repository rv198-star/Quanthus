<template>
  <section class="panel replay-jobs" data-testid="replay-jobs">
    <div class="panel-head"><div><h2>回放任务</h2><p>五个候选共用一个批次，单 Worker 顺序执行</p></div><a-button v-if="canReplay && !activeJob" type="primary" icon="play-circle" @click="openSubmit">重新回放</a-button></div>
    <div v-if="loading" class="replay-empty"><a-icon type="loading" /> 正在读取任务</div>
    <div v-else-if="loadError" class="replay-empty replay-error"><span>回放任务读取失败</span><a-button icon="reload" @click="refresh">重试</a-button></div>
    <div v-else-if="latestJob" class="replay-job-body">
      <div class="replay-job-state"><span :class="['status-tag', stateClass(latestJob.state)]">{{ stateLabel(latestJob) }}</span><div><b>{{ stageLabel(latestJob.stage) }}</b><small>{{ latestJob.job_id }} · 第 {{ latestJob.attempt }} 次尝试</small></div><strong>{{ latestJob.progress }}%</strong></div>
      <div class="job-progress"><i :style="{ width: `${latestJob.progress}%` }"></i></div>
      <dl class="replay-job-facts"><div><dt>输入摘要</dt><dd>{{ short(latestJob.input_digest) }}</dd></div><div><dt>提交时间</dt><dd>{{ dateTime(latestJob.created_at) }}</dd></div><div><dt>当前阶段</dt><dd>{{ progressText(latestJob) }}</dd></div><div v-if="latestJob.result_ref"><dt>Run</dt><dd>{{ latestJob.result_ref.run_id }}</dd></div><div v-if="latestJob.error_reason"><dt>失败原因</dt><dd class="negative">{{ latestJob.error_reason }}</dd></div></dl>
      <div class="replay-job-actions"><a-button v-if="activeJob && canReplay" icon="stop" @click="cancel" :loading="submitting">取消任务</a-button><a-button v-if="terminal && canReplay" icon="redo" @click="openSubmit">再次回放</a-button><span v-if="jobs.length > 1">历史任务 {{ jobs.length }} 个</span></div>
    </div>
    <div v-else class="replay-empty">尚无后台回放任务</div>
    <a-modal v-model="modalOpen" title="提交五候选共享回放" ok-text="进入队列" cancel-text="取消" :confirm-loading="submitting" @ok="submit"><p class="modal-copy">重新回放是独立作业，将冻结开发分区、数据源、P2 政策和五个候选版本。旧 Run 与审批历史不会被覆盖。</p><a-input v-model="reason" type="textarea" :rows="4" placeholder="填写本次重新回放原因" /></a-modal>
  </section>
</template>
<script>
import { cancelJob, loadCandidateReplays, loadJob, submitCandidateReplay } from '../api'
const TERMINAL = new Set(['succeeded', 'failed', 'canceled'])
const STAGES = { queued: '等待 Worker', starting: '启动隔离进程', 'loading-contracts': '加载冻结合同', 'opening-data-provider': '校验数据源', 'dwd-authority-validated': 'DWD 权威身份已校验', 'dwd-source-scan-ready': 'DWD 源扫描完成', 'dwd-market-projection-ready': '行情与可交易投影完成', 'dwd-action-projection-ready': '公司行动投影完成', 'dwd-projection-validated': 'Job 本地投影已校验', 'building-shared-feature-panel': '构建共享特征面板', 'shared-feature-panel-ready': '共享特征面板完成', 'baseline-suite-complete': '三个基线候选完成', 'governed-candidate-complete': '治理候选完成', 'external-candidate-complete': 'QuantDinger 候选完成', 'batch-complete': '校验回放批次', 'recovering-sealed-run': '恢复已封存 Run', 'publishing-run': '登记 Run 与切换有效证据', succeeded: 'Run 已封存', failed: '任务失败', canceled: '任务已取消' }
export default {
  props: { user: Object }, data: () => ({ jobs: [], loading: true, loadError: false, submitting: false, modalOpen: false, reason: '', timer: null }),
  computed: { latestJob () { return this.jobs[0] || null }, activeJob () { return this.latestJob && !TERMINAL.has(this.latestJob.state) ? this.latestJob : null }, terminal () { return this.latestJob && TERMINAL.has(this.latestJob.state) }, canReplay () { return ['admin', 'operator'].includes(this.user?.role) } },
  created () { this.refresh() }, beforeDestroy () { if (this.timer) clearTimeout(this.timer) },
  methods: {
    async refresh () { this.loadError = false; try { if (this.activeJob) this.jobs = [await loadJob(this.activeJob.job_id), ...this.jobs.slice(1)]; else this.jobs = (await loadCandidateReplays()).jobs || [] } catch (_) { this.loadError = true; this.$message.error('回放任务暂时无法读取') } finally { this.loading = false; if (this.activeJob && !this.loadError) this.timer = setTimeout(() => this.refresh(), 3000) } },
    openSubmit () { this.reason = ''; this.modalOpen = true },
    async submit () { if (!this.reason.trim()) { this.$message.warning('请填写重新回放原因'); return } this.submitting = true; try { const job = await submitCandidateReplay(this.reason.trim(), `replay-${Date.now()}`); this.jobs = [job, ...this.jobs]; this.modalOpen = false; this.$message.success('回放任务已进入队列'); this.refresh() } catch (error) { this.$message.error(error.message === 'candidate_replay_already_active' ? '已有回放任务正在排队或执行' : error.message || '提交失败') } finally { this.submitting = false } },
    async cancel () { this.submitting = true; try { this.jobs = [await cancelJob(this.activeJob.job_id), ...this.jobs.slice(1)]; this.$message.success('已提交取消请求') } catch (error) { this.$message.error(error.message || '取消失败') } finally { this.submitting = false; this.refresh() } },
    stateLabel (job) { if (job.state === 'running' && job.cancel_requested) return '取消中'; return { queued: '排队中', running: '运行中', succeeded: '已完成', failed: '失败', canceled: '已取消' }[job.state] || job.state }, stateClass: state => state === 'succeeded' ? 'accepted' : state === 'failed' ? 'rejected' : state === 'canceled' ? 'locked' : 'control', stageLabel: stage => STAGES[stage] || stage,
    progressText (job) { const detail = job.progress_detail || {}; if (detail.row_count) return `${Number(detail.row_count).toLocaleString()} 行特征`; if (detail.succeeded_count != null) return `${detail.succeeded_count} 成功 / ${detail.failed_count} 失败`; return this.stageLabel(job.stage) }, short: value => value ? `${value.slice(0, 18)}...${value.slice(-8)}` : '—', dateTime: value => value ? new Date(value).toLocaleString('zh-CN') : '—'
  }
}
</script>

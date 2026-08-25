/**
 * ScheduledJobsPage — 定时任务管理页。
 *
 * 页面只负责数据编排、筛选和确认弹窗；列表/详情/调度表单放在 features/scheduled-jobs。
 */

import { useEffect, useMemo, useState } from 'react';
import { Modal } from '@/components/design';
import { ScheduledJobDetailPage } from '../features/scheduled-jobs/ScheduledJobDetailPage';
import { ScheduledJobLandingPage } from '../features/scheduled-jobs/ScheduledJobLandingPage';
import { useAgents } from '../hooks/useAgents';
import { useScheduledJobs } from '../hooks/useScheduledJobs';
import { useWorkspaceProjects } from '../hooks/useWorkspaceProjects';
import { useAppStore } from '../stores/appStore';
import type { ScheduledJob, ScheduledJobRun } from '../types/scheduledJob';
import { notifyError, notifySuccess } from '../utils/feedback';

export function ScheduledJobsPage() {
  const {
    jobs,
    loading,
    error,
    schedulerEnabled,
    refresh,
    confirmJob,
    pauseJob,
    resumeJob,
    deleteJob,
    updateSchedule,
    listRuns,
    cancelRun,
  } = useScheduledJobs();
  const { agents } = useAgents();
  const { projects, activeProjectId } = useWorkspaceProjects();
  const pendingJobId = useAppStore((state) => state.pendingJobId);
  const clearPendingJobId = useAppStore((state) => state.clearPendingJobId);

  const [query, setQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [deleteConfirm, setDeleteConfirm] = useState<ScheduledJob | null>(null);
  const [runs, setRuns] = useState<ScheduledJobRun[]>([]);
  const [runsLoading, setRunsLoading] = useState(false);
  const [runsError, setRunsError] = useState<string | null>(null);

  const agentNames = useMemo(() => (
    Object.fromEntries(agents.map((agent) => [agent.id, agent.name]))
  ), [agents]);

  const projectRoot = useMemo(() => (
    projects.find((item) => item.id === activeProjectId)?.rootPath || ''
  ), [activeProjectId, projects]);

  const filteredJobs = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return jobs.filter((job) => {
      if (statusFilter !== 'all' && job.status !== statusFilter) return false;
      if (!normalized) return true;
      return [job.name, job.summary, job.frozenPrompt, job.agentId].some((value) => (
        value.toLowerCase().includes(normalized)
      ));
    });
  }, [jobs, query, statusFilter]);

  const selectedJob = useMemo(() => (
    jobs.find((job) => job.id === selectedId) || null
  ), [jobs, selectedId]);

  useEffect(() => {
    if (!pendingJobId) return;
    setSelectedId(pendingJobId);
    clearPendingJobId();
  }, [clearPendingJobId, pendingJobId]);

  useEffect(() => {
    if (!selectedId) {
      setRuns([]);
      setRunsError(null);
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        setRunsLoading(true);
        setRunsError(null);
        const next = await listRuns(selectedId);
        if (!cancelled) setRuns(next);
      } catch (err) {
        if (cancelled) return;
        setRunsError(err instanceof Error ? err.message : '加载运行历史失败');
        setRuns([]);
      } finally {
        if (!cancelled) setRunsLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [listRuns, selectedId]);

  async function runJobAction(jobId: string, action: () => Promise<ScheduledJob>) {
    try {
      setBusyId(jobId);
      await action();
    } finally {
      setBusyId(null);
    }
  }

  async function handleOpenDirectory(targetPath: string) {
    if (!window.electronAPI) {
      notifyError('请在桌面端打开产物目录');
      return;
    }
    const response = await window.electronAPI.openPath(targetPath, projectRoot || undefined);
    if (response.ok) {
      notifySuccess('已打开目录');
    } else {
      notifyError(`打开目录失败：${response.error || '路径不在当前 Project 白名单内'}`);
    }
  }

  async function handleDelete() {
    if (!deleteConfirm) return;
    try {
      setBusyId(deleteConfirm.id);
      await deleteJob(deleteConfirm.id);
      if (selectedId === deleteConfirm.id) setSelectedId(null);
      setDeleteConfirm(null);
    } finally {
      setBusyId(null);
    }
  }

  const deleteModal = (
    <Modal
      title="删除定时任务"
      open={Boolean(deleteConfirm)}
      onCancel={() => setDeleteConfirm(null)}
      onOk={() => void handleDelete()}
      confirmLoading={Boolean(deleteConfirm && busyId === deleteConfirm.id)}
      okText="归档"
      cancelText="取消"
      okButtonProps={{ danger: true }}
    >
      确定要删除「{deleteConfirm?.name}」吗？删除默认归档，不会抹掉运行历史。
    </Modal>
  );

  if (selectedJob) {
    return (
      <>
        <ScheduledJobDetailPage
          job={selectedJob}
          agentName={agentNames[selectedJob.agentId] || selectedJob.agentId}
          runs={runs}
          runsLoading={runsLoading}
          runsError={runsError}
          busy={busyId === selectedJob.id}
          busyRunId={busyId}
          schedulerEnabled={schedulerEnabled}
          canOpenDirectory={Boolean(window.electronAPI)}
          projectRoot={projectRoot}
          onBack={() => setSelectedId(null)}
          onConfirm={() => void runJobAction(selectedJob.id, () => confirmJob(selectedJob.id))}
          onPause={() => void runJobAction(selectedJob.id, () => pauseJob(selectedJob.id))}
          onResume={() => void runJobAction(selectedJob.id, () => resumeJob(selectedJob.id))}
          onDelete={() => setDeleteConfirm(selectedJob)}
          onSaveSchedule={async (weekdays, at, timezone) => {
            await runJobAction(selectedJob.id, () => updateSchedule(selectedJob.id, {
              scheduleKind: 'weekly',
              scheduleExpr: { weekdays, at },
              timezone,
            }));
          }}
          onCancelRun={async (run) => {
            try {
              setBusyId(run.id);
              const next = await cancelRun(selectedJob.id, run.id);
              setRuns((current) => current.map((item) => (item.id === next.id ? next : item)));
            } finally {
              setBusyId(null);
            }
          }}
          onOpenDirectory={(targetPath) => void handleOpenDirectory(targetPath)}
        />
        {deleteModal}
      </>
    );
  }

  return (
    <>
      <ScheduledJobLandingPage
        jobs={filteredJobs}
        agentNames={agentNames}
        loading={loading}
        error={error}
        schedulerEnabled={schedulerEnabled}
        busyId={busyId}
        query={query}
        statusFilter={statusFilter}
        onQueryChange={setQuery}
        onStatusFilterChange={setStatusFilter}
        onRefresh={() => void refresh()}
        onSelect={(job) => setSelectedId(job.id)}
        onConfirm={(job) => void runJobAction(job.id, () => confirmJob(job.id))}
        onPause={(job) => void runJobAction(job.id, () => pauseJob(job.id))}
        onResume={(job) => void runJobAction(job.id, () => resumeJob(job.id))}
        onDelete={setDeleteConfirm}
      />
      {deleteModal}
    </>
  );
}

/** 群聊工作室独立界面。布局对齐 Chat：左列表、中对话、右可折叠上下文。 */

import { Repeat2, Square } from 'lucide-react';
import { Modal, Tooltip } from '@/components/design';
import { StudioComposer } from './StudioComposer';
import { StudioContextPanel } from './StudioContextPanel';
import { StudioHistoryPanel } from './StudioHistoryPanel';
import { StudioTopBar } from './StudioTopBar';
import { StudioTranscript } from './StudioTranscript';
import { HISTORY_PANEL_TRANSITION_MS, useGroupStudioShell } from './useGroupStudioShell';
import { STATUS_LABEL, useGroupStudioRoom } from './useGroupStudioRoom';
import './GroupStudioPage.css';

export function GroupStudioPage() {
  const {
    shellRef,
    isDesktopShell,
    isMacDesktopShell,
    isWindowsDesktopShell,
    shortcutLabel,
    historyPanelMounted,
    effectiveHistoryCollapsed,
    effectiveContextCollapsed,
    handleCollapseHistory,
    handleExpandHistory,
    handleToggleContext,
  } = useGroupStudioShell();
  const {
    groupId,
    closeGroupStudio,
    openGroupStudio,
    setView,
    modelCatalog,
    modelsLoading,
    modelsError,
    isDraft,
    studio,
    files,
    studios,
    studiosLoading,
    error,
    runtimeError,
    goal,
    savingCard,
    adding,
    fileAttachments,
    setFileAttachments,
    memberBusyId,
    approvalBusy,
    draft,
    setDraft,
    mentions,
    setMentions,
    sending,
    deleteConfirm,
    setDeleteConfirm,
    deleting,
    addingAgentId,
    setPendingOpenSend,
    handleNewStudio,
    handleDeleteStudio,
    handleConfirmDeleteStudio,
    handleModelChange,
    viewStudio,
    members,
    timeline,
    speakingMemberId,
    memberOptions,
    handleSaveGoal,
    handleAddMember,
    handleOpenMembers,
    handleMuteMember,
    handleRemoveMember,
    handleComposerFile,
    handleDownloadFile,
    handleApproval,
    handleSend,
    handleInterrupt,
    handleRerun,
    pendingApproval,
    approvalMemberName,
    statusText,
    ended,
    composerReady,
    cancelProgrammaticScroll,
    handleMessagesScroll,
    handleMessagesWheel,
    messagesContainerRef,
    messagesEndRef,
    scrollToBottom,
    showScrollToBottom,
  } = useGroupStudioRoom();

  return (
    <div
      ref={shellRef}
      className="chat-page-shell chat-workbench-shell group-studio-shell grid h-full min-h-0 overflow-hidden"
      data-history-mounted={historyPanelMounted}
      data-history-collapsed={effectiveHistoryCollapsed}
      data-context-collapsed={effectiveContextCollapsed}
      data-electron-shell={isDesktopShell}
      data-windows-shell={isWindowsDesktopShell}
      style={{
        gridTemplateColumns: [
          historyPanelMounted && !effectiveHistoryCollapsed
            ? 'var(--group-studio-history-width)'
            : '0px',
          'minmax(0, 1fr)',
          effectiveContextCollapsed ? '0px' : 'var(--group-studio-context-width)',
        ].join(' '),
        transition: `grid-template-columns ${HISTORY_PANEL_TRANSITION_MS}ms var(--ease-out)`,
      }}
    >
      {historyPanelMounted ? (
        <StudioHistoryPanel
          activeGroupId={groupId}
          collapsed={effectiveHistoryCollapsed}
          isMacDesktopShell={isMacDesktopShell}
          loading={studiosLoading}
          studios={studios}
          statusLabel={(status) => STATUS_LABEL[status] || status}
          onCollapse={handleCollapseHistory}
          onNewStudio={handleNewStudio}
          newDisabled={sending || Boolean(studio?.currentRunId)}
          onSelectStudio={(item) => {
            setDraft('');
            setMentions([]);
            setFileAttachments([]);
            setPendingOpenSend(null);
            openGroupStudio(item.id);
          }}
          onDeleteStudio={(item) => void handleDeleteStudio(item)}
        />
      ) : null}

      <section className="chat-main-panel chat-workbench-main grid min-h-0 grid-cols-1 overflow-hidden">
        <div className="grid min-h-0 grid-rows-[auto_1fr_auto] overflow-hidden">
          <StudioTopBar
            isMacDesktopShell={isMacDesktopShell}
            historyCollapsed={effectiveHistoryCollapsed}
            historyMounted={historyPanelMounted}
            shortcutLabel={shortcutLabel}
            title={isDraft ? '新群聊' : (studio?.title || '工作室')}
            statusText={statusText}
            budgetText={studio?.budget ? ` · 已用 ${studio.budget.usedTurns}/${studio.budget.maxTurns} 轮` : ''}
            contextCollapsed={effectiveContextCollapsed}
            catalog={modelCatalog}
            modelValue={viewStudio?.modelConfigId || modelCatalog?.defaultModelId || ''}
            modelDisabled={!composerReady || sending || ended || Boolean(studio?.currentRunId)}
            modelsLoading={modelsLoading}
            modelsError={modelsError}
            newDisabled={sending || Boolean(studio?.currentRunId)}
            onExpandHistory={handleExpandHistory}
            onNewStudio={handleNewStudio}
            onClose={closeGroupStudio}
            onToggleContext={handleToggleContext}
            onModelChange={(value) => void handleModelChange(value)}
            onOpenSettings={() => setView('settings')}
          />

          <StudioTranscript
            error={error}
            runtimeError={runtimeError}
            studio={studio}
            groupId={groupId}
            timeline={timeline}
            members={members}
            pendingApproval={pendingApproval}
            approvalMemberName={approvalMemberName}
            approvalBusy={approvalBusy}
            showScrollToBottom={showScrollToBottom}
            messagesContainerRef={messagesContainerRef}
            messagesEndRef={messagesEndRef}
            onScroll={handleMessagesScroll}
            onWheel={handleMessagesWheel}
            onPointerDown={cancelProgrammaticScroll}
            onScrollToBottom={() => scrollToBottom('smooth')}
            onDecide={(decision) => void handleApproval(decision)}
          />

          {ended ? null : (
          <StudioComposer
            disabled={!composerReady || sending}
            sending={sending}
            value={draft}
            members={members}
            mentions={mentions}
            fileAttachments={fileAttachments}
            onFile={handleComposerFile}
            onRemoveFile={(id) => setFileAttachments((current) => current.filter((item) => item.id !== id))}
            placeholder={
              studio?.status === 'paused'
                ? '暂停中，发送会恢复并按这句开跑。用 @ 菜单点名。'
                : isDraft
                  ? '说一句才会创建群聊。用 @ 菜单点名；不点名就是全员各说一轮。'
                  : '说一句，或用 @ 菜单点名。不点名就是全员各说一轮。'
            }
            onChange={setDraft}
            onMentionsChange={setMentions}
            controls={(
              <>
                <Tooltip title="停止当前发言" placement="top" mode="dark">
                  <button
                    type="button"
                    className="icon-button inline-flex h-8 items-center gap-1.5 rounded-lg px-2.5 text-xs"
                    onClick={() => void handleInterrupt()}
                    disabled={!studio}
                    aria-label="停止当前发言"
                  >
                    <Square size={14} />
                    <span>停止</span>
                  </button>
                </Tooltip>
                <Tooltip title="按上一轮点名再来一轮" placement="top" mode="dark">
                  <button
                    type="button"
                    className="icon-button inline-flex h-8 items-center gap-1.5 rounded-lg px-2.5 text-xs"
                    onClick={() => void handleRerun()}
                    disabled={!studio?.canRerun}
                    aria-label="再来一轮"
                  >
                    <Repeat2 size={14} />
                    <span>再来一轮</span>
                  </button>
                </Tooltip>
              </>
            )}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
                event.preventDefault();
                void handleSend();
              }
            }}
            onSend={() => void handleSend()}
          />
          )}
        </div>
      </section>

      <div className="group-studio-context-slot" data-collapsed={effectiveContextCollapsed || undefined}>
        <StudioContextPanel
          adding={adding}
          addingAgentId={addingAgentId}
          collapsed={effectiveContextCollapsed}
          files={files}
          goal={goal}
          isMacDesktopShell={isMacDesktopShell}
          isWindowsDesktopShell={isWindowsDesktopShell}
          memberBusyId={memberBusyId}
          memberOptions={memberOptions}
          members={members}
          savingCard={savingCard}
          speakingMemberId={speakingMemberId}
          studio={viewStudio}
          onAddMember={(agentId) => void handleAddMember(agentId)}
          onMuteMember={(memberId, muted) => void handleMuteMember(memberId, muted)}
          onRemoveMember={(memberId) => void handleRemoveMember(memberId)}
          onSaveGoal={(nextGoal) => void handleSaveGoal(nextGoal)}
          onDownloadFile={(file) => void handleDownloadFile(file)}
          onOpenMembers={handleOpenMembers}
        />
      </div>
      <Modal
        title="删除群聊"
        open={Boolean(deleteConfirm)}
        onCancel={() => {
          if (!deleting) {
            setDeleteConfirm(null);
          }
        }}
        onOk={() => void handleConfirmDeleteStudio()}
        okText="删除"
        cancelText="取消"
        okButtonProps={{ danger: true, loading: deleting }}
        cancelButtonProps={{ disabled: deleting }}
        destroyOnHidden
      >
        <p>
          确定删除“{deleteConfirm?.title || '未命名工作室'}”吗？删除后将从群聊列表移除。
        </p>
      </Modal>
    </div>
  );
}

/** TEAM_EVENT 的唯一前端 reducer。 */

import {
  EMPTY_TEAM_CONSOLE,
  type TeamConsoleState,
  type TeamDomainEvent,
  type TeamSnapshot,
} from '../../types/teamRuntime';

export function teamConsoleFromSnapshot(snapshot: TeamSnapshot | null | undefined): TeamConsoleState {
  if (!snapshot?.teamRunId) return EMPTY_TEAM_CONSOLE;
  return {
    teamRunId: snapshot.teamRunId,
    snapshot,
    events: Array.isArray(snapshot.recentEvents) ? snapshot.recentEvents : [],
  };
}

export function applyTeamEvent(state: TeamConsoleState, event: TeamDomainEvent): TeamConsoleState {
  if (!event.teamRunId) return state;
  const sameRun = state.teamRunId === event.teamRunId;
  const events = sameRun ? state.events : [];
  const eventForHistory = { ...event };
  delete eventForHistory.snapshot;
  const duplicate = events.some((item) => (
    item.id === event.id ||
    item.seq === event.seq ||
    (Array.isArray(item.payload.mergedSeqs) && item.payload.mergedSeqs.includes(event.seq))
  ));
  const nextEvents = duplicate ? events : appendTeamEvent(events, eventForHistory);
  const snapshot = event.snapshot
    ? { ...event.snapshot, recentEvents: nextEvents }
    : sameRun && state.snapshot
      ? { ...state.snapshot, recentEvents: nextEvents }
      : null;
  return {
    teamRunId: event.teamRunId,
    snapshot,
    events: nextEvents,
  };
}

function appendTeamEvent(events: TeamDomainEvent[], event: TeamDomainEvent): TeamDomainEvent[] {
  const last = events[events.length - 1];
  if (
    last &&
    event.type.endsWith('.delta') &&
    last.type === event.type &&
    last.actorId === event.actorId &&
    last.taskId === event.taskId
  ) {
    return [
      ...events.slice(0, -1),
      {
        ...last,
        id: event.id,
        seq: event.seq,
        timestamp: event.timestamp,
        payload: {
          ...last.payload,
          delta: `${String(last.payload.delta || '')}${String(event.payload.delta || '')}`,
          mergedSeqs: [
            ...(Array.isArray(last.payload.mergedSeqs) ? last.payload.mergedSeqs : [last.seq]),
            event.seq,
          ],
        },
        snapshot: event.snapshot || last.snapshot,
      },
    ];
  }
  return [...events, event].slice(-300);
}

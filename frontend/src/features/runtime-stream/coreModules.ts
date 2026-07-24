/** Chat 首批 kind 的 decoder、reducer 与 renderer 注册。 */

import {
  ApprovalBlockView,
  ArtifactBlockView,
  ErrorBlockView,
  MessageBlockView,
  PlanBlockView,
  ProcessTextBlockView,
  SkillBlockView,
  SubagentBlockView,
  TeamBlockView,
  TextBlockView,
  ToolBlockView,
  type RecordData,
} from './CoreBlockViews';
import {
  BlockRegistry,
  decodeRecord,
  decodeString,
  requireVersion,
  type BlockPresentation,
  type BlockModule,
} from './registry';

type TextPatch = string | { replace: string };

function recordModule(
  kind: string,
  displayName: string,
  renderer: BlockModule<RecordData, RecordData>['renderer'],
  presentation: BlockPresentation = 'process',
): BlockModule<RecordData, RecordData> {
  return {
    kind,
    displayName,
    presentation,
    currentDataVersion: 1,
    emptyInitial: () => ({}),
    decodeInitial: (version, value) => {
      requireVersion(version, 1, kind);
      return value === undefined ? {} : decodeRecord(value, `${kind} initial`);
    },
    decodePatch: (version, value) => {
      requireVersion(version, 1, kind);
      return decodeRecord(value, `${kind} patch`);
    },
    reduce: (data, patch) => ({ ...data, ...patch }),
    renderer,
  };
}

function textModule(
  kind: string,
  displayName: string,
  renderer: BlockModule<string, TextPatch>['renderer'],
  presentation: BlockPresentation,
): BlockModule<string, TextPatch> {
  return {
    kind,
    displayName,
    presentation,
    currentDataVersion: 1,
    emptyInitial: () => '',
    decodeInitial: (version, value) => {
      requireVersion(version, 1, kind);
      return value === undefined ? '' : decodeString(value, `${kind} initial`);
    },
    decodePatch: (version, value) => {
      requireVersion(version, 1, kind);
      if (typeof value === 'string') return value;
      const patch = decodeRecord(value, `${kind} patch`);
      if (typeof patch.replace !== 'string' || Object.keys(patch).length !== 1) {
        throw new Error(`${kind} replace patch 非法`);
      }
      return { replace: patch.replace };
    },
    reduce: (data, patch) => typeof patch === 'string' ? data + patch : patch.replace,
    renderer,
  };
}

const toolModule: BlockModule<RecordData, RecordData> = {
  ...recordModule('tool', '工具', ToolBlockView),
  reduce: (data, patch) => {
    const next = { ...data };
    for (const [key, value] of Object.entries(patch)) {
      if (key === 'inputDelta') {
        next.inputPreview = String(next.inputPreview ?? '') + decodeString(value, 'tool.inputDelta');
      } else if (key === 'outputDelta') {
        next.outputPreview = String(next.outputPreview ?? '') + decodeString(value, 'tool.outputDelta');
      } else {
        next[key] = value;
      }
    }
    return next;
  },
};

export function createCoreBlockRegistry(): BlockRegistry {
  const registry = new BlockRegistry();
  registry.register(recordModule('message', '消息', MessageBlockView, 'structural'));
  registry.register(textModule('text', '回答', TextBlockView, 'answer'));
  registry.register(textModule('reasoning', '推理', ProcessTextBlockView, 'process'));
  registry.register(textModule('thinking', '思考', ProcessTextBlockView, 'process'));
  registry.register(toolModule);
  registry.register(recordModule('skill', 'Skill', SkillBlockView));
  registry.register(recordModule('approval', '人工确认', ApprovalBlockView));
  registry.register(recordModule('plan', '计划', PlanBlockView, 'answer'));
  registry.register(recordModule('artifact', '产物', ArtifactBlockView, 'supplement'));
  registry.register(recordModule('error', '错误', ErrorBlockView, 'supplement'));
  registry.register(recordModule('subagent', 'Subagent', SubagentBlockView));
  registry.register(recordModule('team', 'Team', TeamBlockView));
  return registry;
}

/** Chat 展示、流式归并和 selector 共用同一份只读 kind 注册信息。 */
export const coreBlockRegistry = createCoreBlockRegistry();

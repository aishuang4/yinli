/** Chat 发送框 / 指令：压缩、计划、思考、模式，以及 Skill。 */

import type { Skill } from '../../types/skill';
import { filterSkillsBySlashQuery } from './forcedSkills';

export type ComposerSlashCommandId = 'compress' | 'plan' | 'think' | 'team' | 'agent';

export type ComposerSlashCommand = {
  id: ComposerSlashCommandId;
  token: string;
  title: string;
  description: string;
  aliases: string[];
};

export const COMPOSER_SLASH_COMMANDS: ComposerSlashCommand[] = [
  {
    id: 'compress',
    token: '压缩',
    title: '压缩上下文',
    description: '下一轮发送前主动压缩当前会话上下文',
    aliases: ['压缩', 'compress', 'compact', '上下文'],
  },
  {
    id: 'plan',
    token: '计划',
    title: '计划模式',
    description: '本轮先列出计划，确认后再执行',
    aliases: ['计划', 'plan', '计划模式'],
  },
  {
    id: 'think',
    token: '思考',
    title: '深度思考',
    description: '提高本轮推理深度',
    aliases: ['思考', 'think', '深度思考', '推理'],
  },
  {
    id: 'team',
    token: '团队',
    title: 'Team 模式',
    description: '本轮用临时团队协作',
    aliases: ['团队', 'team', '协作'],
  },
  {
    id: 'agent',
    token: 'agent',
    title: '单 Agent',
    description: '切回单 Agent 模式',
    aliases: ['agent', '单agent', '单 Agent'],
  },
];

export type SlashListItem =
  | { kind: 'command'; command: ComposerSlashCommand }
  | { kind: 'skill'; skill: Skill };

export function commandMatchesQuery(command: ComposerSlashCommand, query: string): boolean {
  const needle = query.trim().toLowerCase();
  if (!needle) return true;
  return [command.token, command.title, command.description, ...command.aliases]
    .some((item) => item.toLowerCase().includes(needle));
}

export function visibleSlashCommands(flags: {
  planEnabled: boolean;
  teamEnabled: boolean;
}): ComposerSlashCommand[] {
  return COMPOSER_SLASH_COMMANDS.filter((command) => {
    if (command.id === 'plan') return flags.planEnabled;
    if (command.id === 'team' || command.id === 'agent') return flags.teamEnabled;
    return true;
  });
}

export function buildSlashList(options: {
  query: string;
  skills: Skill[];
  planEnabled: boolean;
  teamEnabled: boolean;
}): SlashListItem[] {
  const commands = visibleSlashCommands({
    planEnabled: options.planEnabled,
    teamEnabled: options.teamEnabled,
  }).filter((command) => commandMatchesQuery(command, options.query));
  const skills = filterSkillsBySlashQuery(options.skills, options.query);
  return [
    ...commands.map((command) => ({ kind: 'command' as const, command })),
    ...skills.map((skill) => ({ kind: 'skill' as const, skill })),
  ];
}

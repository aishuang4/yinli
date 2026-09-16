/** 群聊时间线：气泡消息；Agent 超过五行默认折叠。 */

import { useLayoutEffect, useRef, useState } from 'react';
import { EchartsContent } from '../../components/EchartsContent';
import { MessageToolbar } from '../chat/MessageToolbar';
import type { GroupStudioMember } from './types';
import type { StudioTimelineMessage } from './useGroupStudioStream';

const AGENT_CLAMP_LINES = 5;
const COT_LINE = /^(let me\b|i'll\b|i will\b|i need\b|i got\b|i am\b|i'm\b|now i\b|okay\b|ok[,.] |我先|让我|接下来我|我需要先)/i;
const ANSWER_LINE = /^(#{1,6}\s|[-*•]\s|【|■|📊|•\s|目标|结论|今日|现价)/;

function splitReasoning(
  body: string,
  thinking: string,
  streaming: boolean,
): { thinking: string; answer: string } {
  if (thinking.trim()) {
    return { thinking, answer: body };
  }
  const lines = body.split('\n');
  const first = (lines[0] || '').trim();
  if (!first || !COT_LINE.test(first)) {
    return { thinking: '', answer: body };
  }
  let cut = 0;
  for (let index = 0; index < lines.length; index += 1) {
    const trimmed = lines[index].trim();
    if (!trimmed) {
      cut = index + 1;
      continue;
    }
    if (index > 0 && ANSWER_LINE.test(trimmed)) {
      cut = index;
      break;
    }
    if (COT_LINE.test(trimmed)) {
      cut = index + 1;
      continue;
    }
    if (index > 0) {
      cut = index;
      break;
    }
    cut = index + 1;
  }
  if (cut <= 0 || cut >= lines.length) {
    if (streaming) {
      return { thinking: body, answer: '' };
    }
    return { thinking: '', answer: body };
  }
  const extracted = lines.slice(0, cut).join('\n').trim();
  const rest = lines.slice(cut).join('\n').trim();
  if (!extracted || !rest) {
    return { thinking: '', answer: body };
  }
  return { thinking: extracted, answer: rest };
}

interface StudioMessageListProps {
  members: GroupStudioMember[];
  messages: StudioTimelineMessage[];
}

export function StudioMessageList({ members, messages }: StudioMessageListProps) {
  return (
    <>
      {messages.map((item) => {
        const isUser = item.role === 'human';
        const speaker = members.find((member) => member.id === item.speakerId);
        const label = isUser
          ? '你'
          : speaker?.displayName || (item.role === 'host' ? '主持人' : item.role === 'system' ? '系统' : '成员');
        const avatar = label.slice(0, 1);
        return (
          <article
            key={item.id}
            className={`group-studio-msg${isUser ? ' group-studio-msg--user' : ' group-studio-msg--agent'}`}
          >
            {isUser ? null : (
              <div className="group-studio-msg__meta">
                <span className="group-studio-msg__avatar" aria-hidden="true">{avatar}</span>
                <span className="group-studio-msg__name">{label}</span>
              </div>
            )}
            {isUser ? (
              <>
                <div className="group-studio-bubble group-studio-bubble--user">
                  {item.body}
                </div>
                {item.body.trim() ? <MessageToolbar markdown={item.body} /> : null}
              </>
            ) : (
              <AgentBubble
                content={item.body}
                thinking={item.thinking || ''}
                streaming={item.streaming === true}
                thinkingStreaming={item.thinkingStreaming === true}
              />
            )}
          </article>
        );
      })}
    </>
  );
}

function ThinkingFold({ text, streaming }: { text: string; streaming: boolean }) {
  const [open, setOpen] = useState(false);
  if (!streaming && !text.trim()) {
    return null;
  }
  return (
    <div className="group-studio-thinking">
      <button
        type="button"
        className="group-studio-thinking__toggle"
        onClick={() => setOpen((current) => !current)}
      >
        <span className="group-studio-thinking__mark" data-live={streaming || undefined} aria-hidden="true" />
        {streaming ? '思考中' : '思考过程'}
      </button>
      {open && text.trim() ? (
        <pre className="group-studio-thinking__body">{text}</pre>
      ) : null}
    </div>
  );
}

function AgentBubble({
  content,
  thinking,
  streaming,
  thinkingStreaming,
}: {
  content: string;
  thinking: string;
  streaming: boolean;
  thinkingStreaming: boolean;
}) {
  const bodyRef = useRef<HTMLDivElement>(null);
  const [overflows, setOverflows] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const split = splitReasoning(content, thinking, streaming);
  const visibleThinking = split.thinking;
  const visibleAnswer = split.answer;
  const clamped = !streaming && !expanded;

  useLayoutEffect(() => {
    if (streaming) {
      setOverflows(false);
      setExpanded(false);
      return;
    }
    const element = bodyRef.current;
    if (!element) {
      return;
    }
    const measure = () => {
      const fontSize = Number.parseFloat(getComputedStyle(element).fontSize) || 14;
      const cap = fontSize * 1.5 * AGENT_CLAMP_LINES;
      const source = element.querySelector('.markdown-content') || element;
      setOverflows(source.scrollHeight > cap + 2);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    const markdown = element.querySelector('.markdown-content');
    if (markdown) {
      observer.observe(markdown);
    }
    return () => observer.disconnect();
  }, [visibleAnswer, streaming, expanded]);

  const showBody = Boolean(visibleAnswer.trim()) || (streaming && !thinkingStreaming);

  return (
    <div className="group-studio-agent-stack">
      <ThinkingFold
        text={visibleThinking}
        streaming={thinkingStreaming || (streaming && Boolean(visibleThinking) && !visibleAnswer.trim())}
      />
      {showBody ? (
        <div className="group-studio-bubble group-studio-bubble--agent">
          <div
            ref={bodyRef}
            className="group-studio-bubble__body"
            data-clamped={clamped ? 'true' : undefined}
            data-overflow={overflows ? 'true' : undefined}
          >
            <EchartsContent content={visibleAnswer || '…'} streaming={streaming && !thinkingStreaming} />
          </div>
          {!streaming && overflows ? (
            <button
              type="button"
              className="group-studio-bubble__toggle"
              onClick={() => setExpanded((current) => !current)}
            >
              {expanded ? '收起' : '展开'}
            </button>
          ) : null}
        </div>
      ) : null}
      {!streaming && visibleAnswer.trim() ? <MessageToolbar markdown={visibleAnswer} /> : null}
    </div>
  );
}

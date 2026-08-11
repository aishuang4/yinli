import XMarkdown from '@ant-design/x-markdown';
import '@ant-design/x-markdown/dist/x-markdown.css';

interface MarkdownContentProps {
  content: string;
  streaming?: boolean;
  className?: string;
}

export function MarkdownContent({
  content,
  streaming = false,
  className = '',
}: MarkdownContentProps) {
  return (
    <XMarkdown
      content={content}
      className={`markdown-content ${className}`.trim()}
      openLinksInNewTab
      escapeRawHtml
      streaming={streaming ? { hasNextChunk: true, tail: true } : undefined}
    />
  );
}

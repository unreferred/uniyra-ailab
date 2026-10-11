import React from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

export const MarkdownView: React.FC<{ content: string }> = React.memo(({ content }) => (
  <div className="prose-lab" data-testid="markdown-view">
    <ReactMarkdown remarkPlugins={[remarkGfm]}>{content || ""}</ReactMarkdown>
  </div>
));
MarkdownView.displayName = "MarkdownView";

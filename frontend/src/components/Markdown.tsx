"use client";

import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

// Raw HTML in a reply is never rendered (react-markdown skips it), and links open safely in a new tab.
const components: Components = {
  p: ({ children }) => <p className="mb-2 last:mb-0">{children}</p>,
  strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
  em: ({ children }) => <em className="italic">{children}</em>,
  ul: ({ children }) => <ul className="mb-2 list-disc space-y-0.5 pl-5 last:mb-0">{children}</ul>,
  ol: ({ children }) => <ol className="mb-2 list-decimal space-y-0.5 pl-5 last:mb-0">{children}</ol>,
  li: ({ children }) => <li>{children}</li>,
  h1: ({ children }) => <h3 className="mb-1 font-semibold">{children}</h3>,
  h2: ({ children }) => <h3 className="mb-1 font-semibold">{children}</h3>,
  h3: ({ children }) => <h3 className="mb-1 font-semibold">{children}</h3>,
  a: ({ href, children }) => (
    <a href={href} target="_blank" rel="noopener noreferrer" className="underline">
      {children}
    </a>
  ),
  code: ({ children }) => <code className="rounded bg-black/10 px-1 py-0.5 font-mono text-[0.85em]">{children}</code>,
  pre: ({ children }) => <pre className="mb-2 overflow-x-auto rounded-lg bg-black/10 p-2 text-xs last:mb-0">{children}</pre>,
  table: ({ children }) => (
    <div className="mb-2 overflow-x-auto last:mb-0">
      <table className="text-xs">{children}</table>
    </div>
  ),
  th: ({ children }) => <th className="border border-black/15 px-2 py-1 text-left font-semibold">{children}</th>,
  td: ({ children }) => <td className="border border-black/15 px-2 py-1">{children}</td>,
  blockquote: ({ children }) => <blockquote className="mb-2 border-l-2 border-black/20 pl-3 text-black/70">{children}</blockquote>,
};

export default function Markdown({ text }: { text: string }) {
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
      {text}
    </ReactMarkdown>
  );
}

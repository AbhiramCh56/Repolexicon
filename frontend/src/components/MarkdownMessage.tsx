import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"

import { cn } from "@/lib/utils"

type MarkdownMessageProps = {
  content: string
  className?: string
}

/**
 * Renders one assistant reply.
 *
 * The model answers in Markdown, so rendering the raw string left users staring
 * at `**bold**`, backticks and `###` headings. ReactMarkdown turns that source
 * into real elements, and the `chat-markdown` styles in `index.css` give them
 * consistent spacing and typography inside the message bubble.
 */
export default function MarkdownMessage({
  content,
  className,
}: MarkdownMessageProps) {
  return (
    <div className={cn("chat-markdown", className)}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          // Links in an answer point outside the app; open them in a new tab so
          // following one never destroys the conversation.
          a: ({ href, title, children }) => (
            <a
              href={href}
              title={title}
              target="_blank"
              rel="noopener noreferrer"
            >
              {children}
            </a>
          ),
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  )
}

import { useEffect, useRef } from "react"
import { MessageSquareIcon, SendIcon } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { ScrollArea } from "@/components/ui/scroll-area"
import MarkdownMessage from "@/components/MarkdownMessage"

type ChatMessage = { role: string; text: string }

type ChatInterfaceProps = {
  selectedRepoId: string
  chatHistory: ChatMessage[]
  chatInput: string
  setChatInput: (value: string) => void
  handleAskQuestion: () => void
  isChatLoading: boolean
}

function TypingDots() {
  return (
    <div className="flex items-center gap-1">
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="size-2 animate-bounce rounded-full bg-muted-foreground/40 motion-reduce:animate-none"
          style={{ animationDelay: `${i * 0.1}s` }}
        />
      ))}
    </div>
  )
}

/**
 * The message list and composer.
 *
 * There is no repository picker in here: the active repository lives in the
 * shell header, so this view is pure conversation.
 */
export default function ChatInterface({
  selectedRepoId,
  chatHistory,
  chatInput,
  setChatInput,
  handleAskQuestion,
  isChatLoading,
}: ChatInterfaceProps) {
  const messagesEndRef = useRef<HTMLDivElement>(null)

  // Auto-scroll to bottom of chat
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [chatHistory, isChatLoading])

  const lastMessage = chatHistory[chatHistory.length - 1]
  const showStandaloneDots =
    isChatLoading && lastMessage?.role !== "assistant"

  return (
    <div className="flex min-h-0 flex-1 flex-col bg-background">
      <ScrollArea className="min-h-0 flex-1">
        <div className="mx-auto flex max-w-3xl flex-col gap-5 p-6">
          {chatHistory.length === 0 ? (
            <div className="flex flex-col items-center justify-center gap-3 py-24 text-center text-muted-foreground">
              <div className="flex size-14 items-center justify-center rounded-2xl bg-muted">
                <MessageSquareIcon className="size-7" />
              </div>
              <p className="text-lg font-medium text-foreground">
                Ask a question about your codebase
              </p>
              <p className="max-w-md text-sm">
                E.g., "How does the authentication flow work in this repo?"
              </p>
            </div>
          ) : (
            chatHistory.map((msg, idx) => {
              const isUser = msg.role === "user"
              const isPendingAssistant = !isUser && isChatLoading && !msg.text
              return (
                <div
                  key={idx}
                  className={`flex ${isUser ? "justify-end" : "justify-start"}`}
                >
                  <div
                    className={`max-w-[80%] rounded-2xl px-4 py-3 text-sm leading-relaxed shadow-xs ${
                      isUser
                        ? "whitespace-pre-wrap rounded-br-sm bg-primary text-primary-foreground"
                        : "rounded-bl-sm border bg-card text-card-foreground"
                    }`}
                  >
                    {isPendingAssistant ? (
                      <TypingDots />
                    ) : isUser ? (
                      msg.text
                    ) : (
                      <MarkdownMessage content={msg.text} />
                    )}
                  </div>
                </div>
              )
            })
          )}

          {showStandaloneDots && (
            <div className="flex justify-start">
              <div className="rounded-2xl rounded-bl-sm border bg-card px-4 py-3 shadow-xs">
                <TypingDots />
              </div>
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>
      </ScrollArea>

      <div className="flex-shrink-0 border-t bg-card p-4">
        <div className="relative mx-auto max-w-3xl">
          <Input
            type="text"
            value={chatInput}
            onChange={(e) => setChatInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleAskQuestion()}
            placeholder="Ask anything about the code..."
            disabled={!selectedRepoId || isChatLoading}
            className="h-11 pr-12"
          />
          <Button
            size="icon"
            onClick={handleAskQuestion}
            disabled={!selectedRepoId || isChatLoading || !chatInput.trim()}
            aria-label="Send message"
            className="absolute right-1.5 top-1.5"
          >
            <SendIcon />
          </Button>
        </div>
      </div>
    </div>
  )
}

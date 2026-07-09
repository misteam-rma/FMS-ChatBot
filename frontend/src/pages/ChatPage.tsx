import { useEffect, useState, useRef } from "react";
import { useLocation } from "wouter";
import { Loader2, LogOut, Menu, Send, User, X } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { getSession, clearSession, getChatHistory, saveChatHistory, ChatMessage, Session } from "@/lib/storage";
import { sendChat, sendIntent, quickRepliesOf, cardsOf, progressOf } from "@/lib/api";
import { renderMarkdown } from "@/lib/markdown";

// Menu actions shown as a chip bar. Each maps a button label to a backend
// intent key handled deterministically (no LLM) via /api/chat/intent.
const MENU_ACTIONS: { label: string; intent: string }[] = [
  { label: "Status", intent: "status" },
  { label: "Steps", intent: "steps" },
  { label: "Docs", intent: "docs" },
  { label: "Missing", intent: "missing" },
  { label: "Next Step", intent: "next_step" },
  { label: "Banks", intent: "banks" },
  { label: "FMS", intent: "fms" },
  { label: "Total", intent: "total" },
  { label: "Menu", intent: "menu" },
];

// Map a quick-reply button label back to its intent key. Labels not in this map
// (e.g. free-text suggestions) fall through to the LLM chat path.
const LABEL_TO_INTENT: Record<string, string> = Object.fromEntries(
  MENU_ACTIONS.map((a) => [a.label.toLowerCase(), a.intent]),
);
LABEL_TO_INTENT["contact"] = "contact";
LABEL_TO_INTENT["profile"] = "profile";

// Full quick-action list for the slide-out sidebar (superset of the chip bar).
const SIDEBAR_ACTIONS: { label: string; intent: string }[] = [
  ...MENU_ACTIONS.filter((a) => a.intent !== "menu"),
  { label: "Contact", intent: "contact" },
  { label: "Profile", intent: "profile" },
  { label: "Menu", intent: "menu" },
];

export default function ChatPage() {
  const [, setLocation] = useLocation();
  const [session, setSessionState] = useState<Session | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const currentSession = getSession();
    if (!currentSession) {
      setLocation("/");
      return;
    }
    setSessionState(currentSession);
    document.title =
      currentSession.user.role === "admin" ? "RMA | Admin" : "RMA | Client";

    const history = getChatHistory(currentSession);
    if (history.length > 0) {
      setMessages(history);
    } else {
      const isClient = currentSession.user.role === "client";
      const name = currentSession.user.name || "User";
      setMessages([
        {
          role: "ai",
          content: isClient 
            ? `Welcome, ${name}! I am your RMA Finance assistant. How can I help you with your financial queries today?`
            : `Welcome back, ${name}! You are logged in as Administrator. How can I assist you today?`,
          timestamp: new Date().toISOString(),
        }
      ]);
    }
  }, [setLocation]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    if (session && messages.length > 0) {
      saveChatHistory(session, messages);
    }
  }, [messages, session]);

  const handleLogout = () => {
    clearSession();
    setLocation("/");
  };

  const adjustTextareaHeight = () => {
    const el = textareaRef.current;
    if (el) {
      el.style.height = "auto";
      el.style.height = `${Math.min(el.scrollHeight, 120)}px`; // Approx 5 lines max
    }
  };

  const handleInput = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInput(e.target.value);
    adjustTextareaHeight();
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleSend = async (overrideText?: string) => {
    const text = (overrideText ?? input).trim();
    if (!text || isSending || !session) return;

    const userMsg: ChatMessage = {
      role: "user",
      content: text,
      timestamp: new Date().toISOString(),
    };

    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    setIsSending(true);
    
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
    }

    try {
      // Map internal {role: "user"|"ai"} history to the backend's
      // {role: "user"|"assistant"} contract, last 15 messages.
      const history = messages.slice(-15).map((m) => ({
        role: m.role === "ai" ? ("assistant" as const) : ("user" as const),
        content: m.content,
      }));

      const res = await sendChat(session.token, text, history);

      const aiMsg: ChatMessage = {
        role: "ai",
        content: res.reply,
        timestamp: new Date().toISOString(),
        quickReplies: quickRepliesOf(res),
        cards: cardsOf(res),
        progress: progressOf(res),
      };
      setMessages((prev) => [...prev, aiMsg]);
    } catch (err: any) {
      toast.error(err?.message || "Failed to send message. Please try again.");
      // Remove the failed user message and restore the input.
      setMessages((prev) => prev.filter((m) => m !== userMsg));
      setInput(text);
    } finally {
      setIsSending(false);
    }
  };

  // Run a deterministic menu intent (button/chip). Echoes the label as a user
  // bubble, then shows the dashboard-backed reply with its own quick replies.
  const handleIntent = async (intent: string, label: string) => {
    if (isSending || !session) return;
    const userMsg: ChatMessage = {
      role: "user",
      content: label,
      timestamp: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, userMsg]);
    setIsSending(true);
    try {
      const res = await sendIntent(session.token, intent);
      setMessages((prev) => [
        ...prev,
        {
          role: "ai",
          content: res.reply,
          timestamp: new Date().toISOString(),
          quickReplies: quickRepliesOf(res),
          cards: cardsOf(res),
          progress: progressOf(res),
        },
      ]);
    } catch (err: any) {
      toast.error(err?.message || "Failed to run action. Please try again.");
      setMessages((prev) => prev.filter((m) => m !== userMsg));
    } finally {
      setIsSending(false);
    }
  };

  // A quick-reply button: known menu labels run the deterministic intent;
  // anything else is sent as a normal (LLM) chat message.
  const handleQuickReply = (label: string) => {
    const intent = LABEL_TO_INTENT[label.trim().toLowerCase()];
    if (intent) {
      handleIntent(intent, label);
    } else {
      handleSend(label);
    }
  };

  // Tapping a bank card drills into that bank's status via the LLM chat path
  // (which can answer bank-specific questions over FMS1-4).
  const handleCardTap = (bank: string) => {
    if (isSending) return;
    handleSend(`${bank} ka detailed status aur steps batao`);
  };

  const runSidebarAction = (intent: string, label: string) => {
    setSidebarOpen(false);
    handleIntent(intent, label);
  };

  if (!session) return null;

  // Menu intents (Status/Steps/Docs/...) are client-scoped: they need one
  // client's job code to read the dashboard tabs. Admins have no single code,
  // so these chips/sidebar are hidden for admin.
  const isClient = session.user.role === "client";

  const initials = session.user.name
    .split(" ")
    .map(n => n[0])
    .slice(0, 2)
    .join("")
    .toUpperCase() || "U";

  return (
    <div className="flex flex-col h-[100dvh] bg-background">
      {/* Header */}
      <header className="sticky top-0 z-10 bg-card border-b border-border shadow-sm px-4 md:px-6 h-16 flex items-center justify-between shrink-0">
        <div className="flex items-center gap-3">
          {/* Full logo on tablet/desktop; compact icon on small screens */}
          <img
            src="/RMA_Extended.png"
            alt="Rahul Mishra & Associates — Chartered Accountants"
            className="hidden sm:block h-11 w-auto object-contain"
          />
          <img
            src="/RMA.png"
            alt="RMA"
            className="block sm:hidden h-9 w-auto object-contain"
          />
        </div>

        <div className="flex items-center gap-3 md:gap-4">
          <div className="flex items-center gap-2">
            <Avatar className="h-9 w-9 bg-accent text-primary border border-accent/20">
              <AvatarFallback className="bg-accent text-primary font-bold text-sm">
                {initials}
              </AvatarFallback>
            </Avatar>
            <span className="hidden sm:block text-sm font-semibold text-foreground">
              {session.user.name}
            </span>
          </div>
          
          <Badge 
            variant="outline" 
            className={session.user.role === "admin" 
              ? "bg-primary text-primary-foreground border-transparent" 
              : "border-accent text-accent-foreground font-semibold"}
          >
            {session.user.role === "admin" ? "Admin" : "Client"}
          </Badge>

          {isClient && (
            <Button
              variant="ghost"
              size="icon"
              onClick={() => setSidebarOpen(true)}
              className="text-muted-foreground hover:text-primary hover:bg-accent/10 transition-colors"
              title="Quick actions"
            >
              <Menu className="h-5 w-5" />
            </Button>
          )}

          <Button
            variant="ghost"
            size="icon"
            onClick={handleLogout}
            className="text-muted-foreground hover:text-destructive hover:bg-destructive/10 transition-colors"
            title="Log out"
          >
            <LogOut className="h-5 w-5" />
          </Button>
        </div>
      </header>

      {/* Chat Area */}
      <main className="flex-1 overflow-hidden flex flex-col items-center w-full max-w-[100vw]">
        <div className="w-full md:w-[95%] lg:max-w-[1100px] h-full flex flex-col bg-card lg:rounded-t-none lg:shadow-md border-x border-border">
          
          <div className="flex-1 overflow-y-auto p-4 md:p-6 space-y-6">
            {messages.map((msg, i) => (
              <div
                key={i}
                className={`flex flex-col max-w-[85%] ${
                  msg.role === "user" ? "ml-auto items-end" : "mr-auto items-start"
                }`}
              >
                {msg.role === "ai" && (
                  <div className="flex items-center gap-2 mb-1.5 ml-1">
                    <div className="w-2 h-2 rounded-full bg-accent"></div>
                    <span className="text-xs font-bold text-primary">RMA Assistant</span>
                  </div>
                )}
                
                <div
                  className={`px-4 py-3 rounded-2xl ${
                    msg.role === "user"
                      ? "bg-primary text-primary-foreground rounded-tr-sm"
                      : "bg-muted text-foreground rounded-tl-sm border border-border/50"
                  }`}
                >
                  {msg.role === "ai" ? (
                    <div
                      className="prose prose-sm md:prose-base dark:prose-invert prose-p:leading-relaxed max-w-none text-foreground break-words prose-a:text-primary prose-a:font-medium prose-a:underline prose-a:underline-offset-2 hover:prose-a:text-primary/80"
                      dangerouslySetInnerHTML={{ __html: renderMarkdown(msg.content) }}
                    />
                  ) : (
                    <div className="text-sm md:text-base whitespace-pre-wrap">{msg.content}</div>
                  )}
                </div>

                {/* Single-project progress bar. */}
                {msg.role === "ai" && typeof msg.progress === "number" && (
                  <div className="w-full max-w-sm mt-2 ml-1">
                    <div className="flex justify-between text-[11px] text-muted-foreground mb-1">
                      <span>Progress</span>
                      <span className="font-semibold text-primary">{msg.progress}%</span>
                    </div>
                    <div className="h-2 w-full rounded-full bg-muted overflow-hidden">
                      <div
                        className="h-full rounded-full bg-primary transition-[width] duration-700 ease-out"
                        style={{ width: `${msg.progress}%` }}
                      />
                    </div>
                  </div>
                )}

                {/* Tappable bank cards (Status / Banks intents). */}
                {msg.role === "ai" && msg.cards && msg.cards.length > 0 && (
                  <div className="flex flex-col gap-2 mt-2 ml-1 w-full max-w-md">
                    {msg.cards.map((card, ci) => (
                      <button
                        key={`${card.bank}-${ci}`}
                        onClick={() => handleCardTap(card.bank)}
                        disabled={isSending}
                        className="text-left px-4 py-3 rounded-xl border border-border bg-background hover:border-accent hover:bg-accent/5 transition-colors disabled:opacity-50"
                      >
                        <div className="flex items-center justify-between gap-2">
                          <span className="text-sm font-semibold text-foreground truncate">
                            {card.is_completed ? "✓ " : ""}{card.bank}
                          </span>
                          <span className="text-xs font-semibold text-primary shrink-0">
                            {card.progress}%
                          </span>
                        </div>
                        <div className="text-[11px] text-muted-foreground mt-0.5">
                          Rs. {card.amount.toFixed(2)} Cr · {card.completed}/{card.applicable} steps
                          {card.is_completed ? " · Completed" : " · Ongoing"}
                        </div>
                        <div className="h-1.5 w-full rounded-full bg-muted overflow-hidden mt-2">
                          <div
                            className="h-full rounded-full bg-primary transition-[width] duration-700 ease-out"
                            style={{ width: `${card.progress}%` }}
                          />
                        </div>
                      </button>
                    ))}
                  </div>
                )}

                {/* Quick-reply buttons from a menu intent (AI messages only). */}
                {msg.role === "ai" && msg.quickReplies && msg.quickReplies.length > 0 && (
                  <div className="flex flex-wrap gap-2 mt-2 ml-1">
                    {msg.quickReplies.map((label) => (
                      <button
                        key={label}
                        onClick={() => handleQuickReply(label)}
                        disabled={isSending}
                        className="px-3 py-1.5 text-xs font-semibold rounded-full border border-accent/40 text-primary bg-accent/10 hover:bg-accent/20 hover:border-accent transition-colors disabled:opacity-50"
                      >
                        {label}
                      </button>
                    ))}
                  </div>
                )}

                <span className={`text-[11px] mt-1.5 ${
                  msg.role === "user" ? "text-muted-foreground/70" : "text-muted-foreground"
                }`}>
                  {new Date(msg.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                </span>
              </div>
            ))}
            
            {isSending && (
              <div className="flex flex-col max-w-[85%] mr-auto items-start">
                <div className="flex items-center gap-2 mb-1.5 ml-1">
                  <div className="w-2 h-2 rounded-full bg-accent"></div>
                  <span className="text-xs font-bold text-primary">RMA Assistant</span>
                </div>
                <div className="px-5 py-4 bg-muted text-foreground rounded-2xl rounded-tl-sm border border-border/50 flex items-center gap-1.5">
                  <div className="w-2 h-2 bg-primary rounded-full animate-bounce [animation-delay:-0.3s]"></div>
                  <div className="w-2 h-2 bg-primary rounded-full animate-bounce [animation-delay:-0.15s]"></div>
                  <div className="w-2 h-2 bg-primary rounded-full animate-bounce"></div>
                </div>
              </div>
            )}
            <div ref={messagesEndRef} />
          </div>

          {/* Menu chip bar — client-only deterministic quick actions (no LLM). */}
          {isClient && (
            <div className="px-4 pt-3 bg-card border-t border-border shrink-0">
              <div className="flex flex-wrap gap-2">
                {MENU_ACTIONS.map((action) => (
                  <button
                    key={action.intent}
                    onClick={() => handleIntent(action.intent, action.label)}
                    disabled={isSending}
                    className="px-3 py-1.5 text-xs font-medium rounded-full border border-border text-foreground bg-muted hover:bg-accent/15 hover:border-accent hover:text-primary transition-colors disabled:opacity-50"
                  >
                    {action.label}
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Input Area */}
          <div className="p-4 bg-card mt-auto shrink-0">
            <div className="relative flex items-end gap-2 bg-background border border-border rounded-xl p-2 focus-within:ring-1 focus-within:ring-primary focus-within:border-primary transition-shadow shadow-sm">
              <textarea
                ref={textareaRef}
                value={input}
                onInput={handleInput}
                onKeyDown={handleKeyDown}
                placeholder="Type your message..."
                className="w-full bg-transparent text-sm md:text-base border-0 focus:ring-0 resize-none py-2 px-2 max-h-[120px] min-h-[44px] outline-none"
                rows={1}
                disabled={isSending}
              />
              <Button
                onClick={() => handleSend()}
                disabled={!input.trim() || isSending}
                size="icon"
                className="h-10 w-10 shrink-0 rounded-lg bg-primary hover:bg-primary/90 text-accent transition-colors disabled:opacity-50 disabled:bg-muted disabled:text-muted-foreground mb-0.5"
              >
                {isSending ? <Loader2 className="h-5 w-5 animate-spin text-primary-foreground" /> : <Send className="h-5 w-5" />}
              </Button>
            </div>
            <div className="text-center mt-2">
              <span className="text-[10px] text-muted-foreground/70">
                Powered by{" "}
                <a
                  href="https://botivate.in/#home"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="font-medium text-primary hover:underline"
                >
                  Botivate.in
                </a>
              </span>
            </div>
          </div>
        </div>
      </main>

      {/* Slide-out quick-actions sidebar */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/40 transition-opacity"
          onClick={() => setSidebarOpen(false)}
        />
      )}
      <aside
        className={`fixed top-0 right-0 z-50 h-full w-72 max-w-[85%] bg-card border-l border-border shadow-xl transition-transform duration-300 ${
          sidebarOpen ? "translate-x-0" : "translate-x-full"
        }`}
        aria-hidden={!sidebarOpen}
      >
        <div className="flex items-center justify-between px-5 h-16 border-b border-border">
          <span className="text-sm font-bold text-primary">Quick Actions</span>
          <Button
            variant="ghost"
            size="icon"
            onClick={() => setSidebarOpen(false)}
            className="text-muted-foreground hover:text-foreground"
            title="Close"
          >
            <X className="h-5 w-5" />
          </Button>
        </div>
        <nav className="p-3 flex flex-col gap-1 overflow-y-auto">
          {SIDEBAR_ACTIONS.map((action) => (
            <button
              key={action.intent}
              onClick={() => runSidebarAction(action.intent, action.label)}
              disabled={isSending}
              className="text-left px-4 py-3 rounded-lg text-sm font-medium text-foreground hover:bg-accent/10 hover:text-primary transition-colors disabled:opacity-50"
            >
              {action.label}
            </button>
          ))}
        </nav>
      </aside>
    </div>
  );
}

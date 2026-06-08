import { useEffect, useState, useRef } from "react";
import { useLocation } from "wouter";
import { Loader2, LogOut, Send, User } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { getSession, clearSession, getChatHistory, saveChatHistory, ChatMessage, Session } from "@/lib/storage";
import { sendChat } from "@/lib/api";
import { renderMarkdown } from "@/lib/markdown";

export default function ChatPage() {
  const [, setLocation] = useLocation();
  const [session, setSessionState] = useState<Session | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [isSending, setIsSending] = useState(false);
  
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

    const history = getChatHistory(currentSession.token);
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
      saveChatHistory(session.token, messages);
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

  const handleSend = async () => {
    const text = input.trim();
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

  if (!session) return null;

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

          {/* Input Area */}
          <div className="p-4 bg-card border-t border-border mt-auto shrink-0">
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
                onClick={handleSend}
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
    </div>
  );
}

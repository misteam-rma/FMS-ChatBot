export interface SessionUser {
  name: string;
  role: "client" | "admin";
  mobile?: string;
  clientJobCode?: string;
}

export interface Session {
  token: string;
  user: SessionUser;
}

export interface BankCardData {
  bank: string;
  amount: number;
  progress: number;
  completed: number;
  applicable: number;
  is_completed: boolean;
}

export interface ChatMessage {
  role: "user" | "ai";
  content: string;
  timestamp: string;
  /** Quick-reply button labels returned by a menu intent (AI messages only). */
  quickReplies?: string[];
  /** Tappable bank cards (Status/Banks intents). */
  cards?: BankCardData[];
  /** Single-project completion % for a progress bar. */
  progress?: number;
}

const SESSION_KEY = "rma_session";

export function getSession(): Session | null {
  try {
    const data = localStorage.getItem(SESSION_KEY);
    return data ? JSON.parse(data) : null;
  } catch {
    return null;
  }
}

export function setSession(session: Session): void {
  localStorage.setItem(SESSION_KEY, JSON.stringify(session));
}

export function clearSession(): void {
  localStorage.removeItem(SESSION_KEY);
  // Clear chat history for all
  const keysToRemove: string[] = [];
  for (let i = 0; i < localStorage.length; i++) {
    const key = localStorage.key(i);
    if (key?.startsWith("rma_chat_history_")) {
      keysToRemove.push(key);
    }
  }
  keysToRemove.forEach((key) => localStorage.removeItem(key));
}

// History must be keyed uniquely PER USER. The JWT's first 8 chars are the
// shared header (eyJhbGci...) for every token, so keying on token.slice(0,8)
// made all users collide into one bucket (admin history leaking into a client
// session). Key on the identity carried in the session instead.
function historyKey(session: Session): string {
  const id = session.user.clientJobCode || session.user.name || "anon";
  return `rma_chat_history_${session.user.role}_${id}`;
}

export function getChatHistory(session: Session): ChatMessage[] {
  try {
    const data = localStorage.getItem(historyKey(session));
    return data ? JSON.parse(data) : [];
  } catch {
    return [];
  }
}

export function saveChatHistory(session: Session, messages: ChatMessage[]): void {
  localStorage.setItem(historyKey(session), JSON.stringify(messages));
}

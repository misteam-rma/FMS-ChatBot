export interface SessionUser {
  name: string;
  role: "client" | "admin";
  mobile?: string;
}

export interface Session {
  token: string;
  user: SessionUser;
}

export interface ChatMessage {
  role: "user" | "ai";
  content: string;
  timestamp: string;
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

export function getChatHistory(token: string): ChatMessage[] {
  try {
    const key = `rma_chat_history_${token.slice(0, 8)}`;
    const data = localStorage.getItem(key);
    return data ? JSON.parse(data) : [];
  } catch {
    return [];
  }
}

export function saveChatHistory(token: string, messages: ChatMessage[]): void {
  const key = `rma_chat_history_${token.slice(0, 8)}`;
  localStorage.setItem(key, JSON.stringify(messages));
}

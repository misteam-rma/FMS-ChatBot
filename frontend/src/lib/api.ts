// API base URL detection.
//
// Production (split deploy): the frontend is hosted on Vercel and the backend
// on Render, on DIFFERENT origins. Set VITE_API_BASE_URL in Vercel to the
// Render API root, e.g. https://finance-chatbot-9ni2.onrender.com/api
//
// If VITE_API_BASE_URL is unset we fall back to local-dev heuristics: a Vite
// dev server (non-8000 port) talks to FastAPI on :8000; otherwise same-origin.
export const API_BASE = (() => {
  const envBase = import.meta.env.VITE_API_BASE_URL as string | undefined;
  if (envBase && envBase.trim()) {
    return envBase.trim().replace(/\/+$/, ""); // strip trailing slash(es)
  }

  const { protocol, hostname, port, origin } = window.location;

  if (protocol === "file:") {
    return "http://127.0.0.1:8000/api";
  }

  // Vite dev server (any port other than 8000) -> talk to FastAPI on :8000.
  if (port && port !== "8000") {
    return `${protocol}//${hostname}:8000/api`;
  }

  return `${origin}/api`;
})();

export interface AuthResponse {
  access_token: string;
  employee_id: string;
  employee_name: string;
  mobile_number?: string;
  user_type: string; // "client" | "admin"
  client_job_code?: string | null;
}

export interface ChatHistoryItem {
  role: "user" | "assistant";
  content: string;
}

async function parseError(res: Response, fallback: string): Promise<string> {
  try {
    const data = await res.json();
    return data?.detail || data?.message || fallback;
  } catch {
    return fallback;
  }
}

export async function loginClientCode(
  clientJobCode: string,
  phone: string,
): Promise<AuthResponse> {
  const res = await fetch(`${API_BASE}/auth/verify-client-code`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      client_job_code: clientJobCode.trim().toUpperCase(),
      phone: phone.trim(),
    }),
  });

  if (!res.ok) {
    throw new Error(await parseError(res, "Invalid phone number or Client Job Code"));
  }
  return res.json();
}

export async function loginAdmin(username: string, password: string): Promise<AuthResponse> {
  const res = await fetch(`${API_BASE}/auth/verify-admin`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });

  if (!res.ok) {
    throw new Error(await parseError(res, "Invalid credentials or login failed"));
  }
  return res.json();
}

export interface BankCard {
  bank: string;
  amount: number;
  progress: number;
  completed: number;
  applicable: number;
  is_completed: boolean;
}

export interface ChatAction {
  quick_replies?: string[];
  cards?: BankCard[];
  progress?: number;
}

export interface ChatReply {
  reply: string;
  actions?: ChatAction[] | null;
}

/** Extract quick-reply button labels from a chat/intent response. */
export function quickRepliesOf(res: ChatReply): string[] {
  const first = res.actions?.find((a) => Array.isArray(a?.quick_replies));
  return first?.quick_replies ?? [];
}

/** Extract bank cards, if any, from a response. */
export function cardsOf(res: ChatReply): BankCard[] {
  const first = res.actions?.find((a) => Array.isArray(a?.cards));
  return first?.cards ?? [];
}

/** Extract a single-project progress %, if present. */
export function progressOf(res: ChatReply): number | undefined {
  const first = res.actions?.find((a) => typeof a?.progress === "number");
  return first?.progress;
}

export async function sendChat(
  token: string,
  message: string,
  history: ChatHistoryItem[],
): Promise<ChatReply> {
  const res = await fetch(`${API_BASE}/chat/send`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ message, chat_history: history }),
  });

  if (!res.ok) {
    throw new Error(await parseError(res, "Failed to send message"));
  }
  return res.json();
}

/**
 * Trigger a deterministic menu intent (button click). Answered from the
 * dashboard tabs without the LLM. Free-typed messages use sendChat instead.
 */
export async function sendIntent(token: string, intent: string): Promise<ChatReply> {
  const res = await fetch(`${API_BASE}/chat/intent`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ intent }),
  });

  if (!res.ok) {
    throw new Error(await parseError(res, "Failed to run action"));
  }
  return res.json();
}

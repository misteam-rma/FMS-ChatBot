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
  user_type: string; // "employee" | "admin"
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

export async function loginClient(mobile: string): Promise<AuthResponse> {
  const res = await fetch(`${API_BASE}/auth/verify-mobile`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mobile_number: mobile }),
  });

  if (!res.ok) {
    throw new Error(await parseError(res, "Invalid mobile number or login failed"));
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

export async function sendChat(
  token: string,
  message: string,
  history: ChatHistoryItem[],
): Promise<{ reply: string }> {
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

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api, getToken, setToken } from "./api";

export type User = { id: number; email: string; name: string; role: "admin" | "agent" };
type AuthCtx = { user: User | null; loading: boolean; login: (e: string, p: string) => Promise<void>;
                 loginWithSSO: (token: string) => Promise<void>; logout: () => void };
const Ctx = createContext<AuthCtx>(null as never);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(!!getToken());
  useEffect(() => {
    if (getToken()) api<User>("/api/auth/me").then(setUser).catch(() => setToken(null)).finally(() => setLoading(false));
  }, []);
  const login = async (email: string, password: string) => {
    const r = await api<{ access_token: string; user: User }>("/api/auth/login", { body: { email, password } });
    setToken(r.access_token);
    setUser(r.user);
  };
  // a one-time token signed by the main application (single sign-on)
  const loginWithSSO = async (token: string) => {
    const r = await api<{ access_token: string; user: User }>("/api/auth/sso", { body: { token } });
    setToken(r.access_token);
    setUser(r.user);
  };
  const logout = () => { setToken(null); setUser(null); };
  return <Ctx.Provider value={{ user, loading, login, loginWithSSO, logout }}>{children}</Ctx.Provider>;
}
export const useAuth = () => useContext(Ctx);

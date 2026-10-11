import React, { createContext, useContext, useEffect, useState, useCallback } from "react";
import { api, apiError } from "@/lib/api";
import { User } from "@/lib/types";

interface AuthCtx {
  user: User | null | undefined; // undefined = checking, null = unauthenticated
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, name: string) => Promise<void>;
  logout: () => Promise<void>;
}

const Ctx = createContext<AuthCtx>(null as any);

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [user, setUser] = useState<User | null | undefined>(undefined);

  useEffect(() => {
    api.get("/auth/me")
      .then((r) => setUser(r.data))
      .catch(() => setUser(null));
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    try {
      const { data } = await api.post("/auth/login", { email, password });
      setUser(data);
    } catch (e) {
      throw new Error(apiError(e));
    }
  }, []);

  const register = useCallback(async (email: string, password: string, name: string) => {
    try {
      const { data } = await api.post("/auth/register", { email, password, name });
      setUser(data);
    } catch (e) {
      throw new Error(apiError(e));
    }
  }, []);

  const logout = useCallback(async () => {
    try { await api.post("/auth/logout"); } catch { /* ignore */ }
    setUser(null);
  }, []);

  return <Ctx.Provider value={{ user, login, register, logout }}>{children}</Ctx.Provider>;
};

export const useAuth = () => useContext(Ctx);

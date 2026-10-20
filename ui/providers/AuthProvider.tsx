"use client";

import {
  createContext,
  useContext,
  useEffect,
  useState,
  ReactNode,
  useCallback,
} from "react";

interface User {
  id: string;
  email: string;
  username: string;
  is_active: boolean;
}

interface AuthContextValue {
  user: User | null;
  token: string | null;
  isLoading: boolean;
  isAuthenticated: boolean;
  login: (username: string, password: string) => Promise<void>;
  register: (email: string, username: string, password: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

const API_BASE = "/api";

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  // 从 localStorage 恢复登录状态
  useEffect(() => {
    const storedToken = localStorage.getItem("auth_token");
    if (storedToken) {
      setToken(storedToken);
      fetchUser(storedToken);
    } else {
      setIsLoading(false);
    }
  }, []);

  const fetchUser = useCallback(async (authToken: string) => {
    try {
      const res = await fetch(`${API_BASE}/v2/auth/me`, {
        headers: {
          Authorization: `Bearer ${authToken}`,
        },
      });
      if (res.ok) {
        const data = await res.json();
        setUser(data);
      } else if (res.status === 401) {
        // Token 过期或无效，清除登录状态
        console.warn("[Auth] Token expired (401), clearing auth state");
        localStorage.removeItem("auth_token");
        setToken(null);
        setUser(null);
      } else {
        // 其他错误（如 403 用户被禁用、500 服务器错误等），保留 token
        console.error("[Auth] fetchUser failed:", res.status, await res.text().catch(() => ""));
        setUser(null);
      }
    } catch (error) {
      // 网络错误（后端未启动、断网等），保留 token，不强制登出
      console.error("[Auth] fetchUser network error:", error);
      setUser(null);
    } finally {
      setIsLoading(false);
    }
  }, []);

  const login = useCallback(
    async (username: string, password: string) => {
      const res = await fetch(`${API_BASE}/v2/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || "登录失败");
      }
      const data = await res.json();
      localStorage.setItem("auth_token", data.access_token);
      setToken(data.access_token);
      await fetchUser(data.access_token);
    },
    [fetchUser]
  );

  const register = useCallback(
    async (email: string, username: string, password: string) => {
      const res = await fetch(`${API_BASE}/v2/auth/register`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, username, password }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || "注册失败");
      }
      // 注册成功后自动登录
      await login(username, password);
    },
    [login]
  );

  const logout = useCallback(() => {
    localStorage.removeItem("auth_token");
    setToken(null);
    setUser(null);
  }, []);

  return (
    <AuthContext.Provider
      value={{
        user,
        token,
        isLoading,
        isAuthenticated: !!user,
        login,
        register,
        logout,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return context;
}

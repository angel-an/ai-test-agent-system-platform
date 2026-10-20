"use client";

import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";
import { Loader2, LogIn, UserPlus, ArrowLeft } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useAuth } from "@/providers/AuthProvider";

type AuthMode = "login" | "register";

export default function LoginPage() {
  const router = useRouter();
  const { login, register } = useAuth();
  const [mode, setMode] = useState<AuthMode>("login");
  const [email, setEmail] = useState("");
  const [loginUsername, setLoginUsername] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const isLogin = mode === "login";

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setIsSubmitting(true);

    try {
      if (isLogin) {
        await login(loginUsername, password);
        toast.success("登录成功");
        // 延迟跳转，让用户看到 toast 提示
        await new Promise((resolve) => setTimeout(resolve, 500));
      } else {
        await register(email, username, password);
        toast.success("注册成功，已自动登录");
        // 延迟跳转，让用户看到 toast 提示
        await new Promise((resolve) => setTimeout(resolve, 500));
      }
      router.prefetch("/projects");
      router.push("/projects");
    } catch (err: any) {
      const message = err instanceof Error ? err.message : err?.message || "请求失败";
      setError(message);
    } finally {
      setIsSubmitting(false);
    }
  }

  const switchMode = (newMode: AuthMode) => {
    setMode(newMode);
    setError(null);
    setEmail("");
    setLoginUsername("");
    setUsername("");
    setPassword("");
  };

  return (
    <main className="flex min-h-screen w-full items-center justify-center bg-[#f5f7fa] px-4 py-8">
      <div className="w-full max-w-[480px]">
        {/* Header */}
        <div className="mb-8 text-center">
          <h1 className="text-2xl font-semibold text-gray-900 sm:text-[28px]">
            欢迎登录 AI 智能工坊平台
          </h1>
        </div>

        {/* Form */}
        <div className="rounded-xl bg-white p-8 shadow-sm">
          <form onSubmit={handleSubmit} className="space-y-5">
            {/* Login: Username */}
            {isLogin && (
              <div className="space-y-2">
                <Label htmlFor="login-username" className="text-base text-gray-700">
                  账号
                </Label>
                <Input
                  id="login-username"
                  type="text"
                  value={loginUsername}
                  onChange={(e) => setLoginUsername(e.target.value)}
                  placeholder="请输入账号"
                  autoComplete="username"
                  required
                  className="h-12 text-base"
                />
              </div>
            )}

            {/* Register: Username + Email */}
            {!isLogin && (
              <>
                <div className="space-y-2">
                  <Label htmlFor="username" className="text-base text-gray-700">
                    用户名
                  </Label>
                  <Input
                    id="username"
                    value={username}
                    onChange={(e) => setUsername(e.target.value)}
                    placeholder="请输入用户名"
                    minLength={2}
                    maxLength={100}
                    autoComplete="username"
                    required
                    className="h-12 text-base"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="email" className="text-base text-gray-700">
                    邮箱
                  </Label>
                  <Input
                    id="email"
                    type="email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder="请输入邮箱"
                    autoComplete="email"
                    required
                    className="h-12 text-base"
                  />
                </div>
              </>
            )}

            {/* Password */}
            <div className="space-y-2">
              <Label htmlFor="password" className="text-base text-gray-700">
                密码
              </Label>
              <Input
                id="password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder={isLogin ? "请输入密码" : "请输入密码（至少6位）"}
                minLength={isLogin ? undefined : 6}
                maxLength={128}
                autoComplete={isLogin ? "current-password" : "new-password"}
                required
                className="h-12 text-base"
              />
            </div>

            {/* Error */}
            {error && (
              <p className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-600">
                {error}
              </p>
            )}

            {/* Submit */}
            <Button
              type="submit"
              className="h-12 w-full bg-[#2196F3] text-base hover:bg-[#1976D2]"
              disabled={isSubmitting}
            >
              {isSubmitting ? (
                <Loader2 className="h-5 w-5 animate-spin" />
              ) : isLogin ? (
                <>
                  <LogIn className="mr-2 h-5 w-5" />
                  登录
                </>
              ) : (
                <>
                  <UserPlus className="mr-2 h-5 w-5" />
                  注册并登录
                </>
              )}
            </Button>
          </form>
        </div>

        {/* Footer switcher */}
        <div className="mt-6 text-center text-base text-gray-500">
          {isLogin ? (
            <p>
              没有账号？
              <button
                type="button"
                onClick={() => switchMode("register")}
                className="ml-1 font-medium text-[#2196F3] hover:underline"
              >
                请先注册账号
              </button>
            </p>
          ) : (
            <button
              type="button"
              onClick={() => switchMode("login")}
              className="inline-flex items-center font-medium text-[#2196F3] hover:underline"
            >
              <ArrowLeft className="mr-1 h-4 w-4" />
              已有账号，返回登录
            </button>
          )}
        </div>
      </div>
    </main>
  );
}

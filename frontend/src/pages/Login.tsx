import React, { useState } from "react";
import { Link } from "react-router-dom";
import { Eye, EyeOff, Loader2 } from "lucide-react";
import { useAuth } from "@/context/AuthContext";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ErrorNote } from "@/components/common/Common";
import AuthShell from "@/components/common/AuthShell";

export default function Login() {
  const { login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null); setLoading(true);
    try { await login(email, password); }
    catch (err: any) { setError(err.message); }
    finally { setLoading(false); }
  };

  return (
    <AuthShell>
      <form onSubmit={submit} className="w-full max-w-sm space-y-5 fade-in-up motion-reduce:[animation:none]" data-testid="login-form">
        <div className="space-y-1">
          <h2 className="font-display text-2xl font-semibold tracking-tight">Sign in</h2>
          <p className="text-sm text-muted-foreground">Welcome back to your laboratory.</p>
        </div>
        <ErrorNote message={error} />
        <div className="space-y-1.5">
          <label htmlFor="login-email" className="text-xs font-medium text-muted-foreground">Email</label>
          <Input id="login-email" data-testid="login-email" type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </div>
        <div className="space-y-1.5">
          <label htmlFor="login-password" className="text-xs font-medium text-muted-foreground">Password</label>
          <div className="relative">
            <Input id="login-password" data-testid="login-password" type={showPassword ? "text" : "password"} autoComplete="current-password" className="pr-10" value={password} onChange={(e) => setPassword(e.target.value)} required />
            <button
              type="button"
              onClick={() => setShowPassword((v) => !v)}
              aria-label={showPassword ? "Hide password" : "Show password"}
              className="absolute inset-y-0 right-0 flex items-center px-3 text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-r-md"
              data-testid="toggle-password"
            >
              {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
            </button>
          </div>
        </div>
        <Button type="submit" data-testid="login-submit" className="w-full" disabled={loading}>
          {loading ? <Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" aria-label="Signing in" /> : "Sign in"}
        </Button>
        <p className="text-center text-sm text-muted-foreground">
          No account? <Link to="/register" className="text-brand hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded" data-testid="go-register">Create one</Link>
        </p>
      </form>
    </AuthShell>
  );
}

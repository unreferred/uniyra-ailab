import React, { useState } from "react";
import { Link } from "react-router-dom";
import { FlaskRound, Loader2 } from "lucide-react";
import { useAuth } from "@/context/AuthContext";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ErrorNote } from "@/components/common/Common";

export default function Login() {
  const { login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
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
    <div className="flex min-h-screen w-full bg-background">
      <div className="hidden w-1/2 flex-col justify-between border-r border-border bg-card p-12 lg:flex">
        <div className="flex items-center gap-2">
          <div className="flex h-8 w-8 items-center justify-center rounded-md bg-primary text-primary-foreground"><FlaskRound className="h-5 w-5" /></div>
          <span className="font-display text-lg font-semibold">Uniyra AI Lab</span>
        </div>
        <div>
          <h1 className="font-display text-4xl font-semibold leading-tight tracking-tight">The AI R&amp;D<br />workstation.</h1>
          <p className="mt-4 max-w-md text-muted-foreground">Compare models in the Arena, run blind evaluations, judge with rubrics, and turn experiments into validated findings — with real telemetry on every run.</p>
        </div>
        <p className="font-mono text-xs text-muted-foreground">Project → Experiment → Run → Evaluation → Finding</p>
      </div>

      <div className="flex w-full items-center justify-center p-6 lg:w-1/2">
        <form onSubmit={submit} className="w-full max-w-sm space-y-5 fade-in-up" data-testid="login-form">
          <div className="space-y-1">
            <h2 className="font-display text-2xl font-semibold tracking-tight">Sign in</h2>
            <p className="text-sm text-muted-foreground">Welcome back to your laboratory.</p>
          </div>
          <ErrorNote message={error} />
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-muted-foreground">Email</label>
            <Input data-testid="login-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
          </div>
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-muted-foreground">Password</label>
            <Input data-testid="login-password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} required />
          </div>
          <Button type="submit" data-testid="login-submit" className="w-full" disabled={loading}>
            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : "Sign in"}
          </Button>
          <p className="text-center text-sm text-muted-foreground">
            No account? <Link to="/register" className="text-brand hover:underline" data-testid="go-register">Create one</Link>
          </p>
        </form>
      </div>
    </div>
  );
}

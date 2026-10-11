import React, { useState } from "react";
import { Link } from "react-router-dom";
import { Loader2 } from "lucide-react";
import { useAuth } from "@/context/AuthContext";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ErrorNote } from "@/components/common/Common";
import AuthShell from "@/components/common/AuthShell";

export default function Register() {
  const { register } = useAuth();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null); setLoading(true);
    try { await register(email, password, name); }
    catch (err: any) { setError(err.message); }
    finally { setLoading(false); }
  };

  return (
    <AuthShell>
      <form onSubmit={submit} className="w-full max-w-sm space-y-5 fade-in-up motion-reduce:[animation:none]" data-testid="register-form">
        <div className="space-y-1 text-center sm:text-left">
          <h2 className="font-display text-2xl font-semibold tracking-tight">Create your lab</h2>
          <p className="text-sm text-muted-foreground">A zero-config Universal provider is set up automatically.</p>
        </div>
        <ErrorNote message={error} />
        <div className="space-y-1.5">
          <label htmlFor="register-name" className="text-xs font-medium text-muted-foreground">Name</label>
          <Input id="register-name" data-testid="register-name" placeholder="Your name" autoComplete="name" className="h-11 rounded-lg border-border/70 bg-background/60 placeholder:text-muted-foreground/60 focus-visible:border-[hsl(var(--brand-indigo))] focus-visible:ring-2 focus-visible:ring-[hsl(var(--brand-indigo))]/25" value={name} onChange={(e) => setName(e.target.value)} required />
        </div>
        <div className="space-y-1.5">
          <label htmlFor="register-email" className="text-xs font-medium text-muted-foreground">Email</label>
          <Input id="register-email" data-testid="register-email" type="email" placeholder="you@company.com" autoComplete="email" className="h-11 rounded-lg border-border/70 bg-background/60 placeholder:text-muted-foreground/60 focus-visible:border-[hsl(var(--brand-indigo))] focus-visible:ring-2 focus-visible:ring-[hsl(var(--brand-indigo))]/25" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </div>
        <div className="space-y-1.5">
          <label htmlFor="register-password" className="text-xs font-medium text-muted-foreground">Password</label>
          <Input id="register-password" data-testid="register-password" type="password" placeholder="••••••••" autoComplete="new-password" className="h-11 rounded-lg border-border/70 bg-background/60 placeholder:text-muted-foreground/60 focus-visible:border-[hsl(var(--brand-indigo))] focus-visible:ring-2 focus-visible:ring-[hsl(var(--brand-indigo))]/25" value={password} onChange={(e) => setPassword(e.target.value)} required minLength={6} />
        </div>
        <Button type="submit" data-testid="register-submit" className="h-11 w-full rounded-lg bg-[hsl(var(--brand-indigo))] text-white shadow-sm hover:bg-[hsl(var(--brand-indigo))]/90 focus-visible:ring-2 focus-visible:ring-[hsl(var(--brand-indigo))]/40" disabled={loading}>
          {loading ? <Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" aria-label="Creating account" /> : "Create account"}
        </Button>
        <p className="text-center text-sm text-muted-foreground">
          Already have an account? <Link to="/login" className="text-brand hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded" data-testid="go-login">Sign in</Link>
        </p>
      </form>
    </AuthShell>
  );
}

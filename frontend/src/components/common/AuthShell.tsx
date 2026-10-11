import React from "react";

export default function AuthShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen w-full bg-background">
      {/* Branding panel — dark navy with Uniyra blue lighting, on all themes */}
      <div className="relative hidden w-[42%] flex-col justify-between overflow-hidden border-r border-white/5 bg-[hsl(222_47%_8%)] p-10 text-white xl:p-14 lg:flex">
        {/* Ambient Uniyra blue lighting — subtle radial glows, no blur */}
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-0"
          style={{
            background:
              "radial-gradient(60% 45% at 85% 12%, hsl(243 75% 59% / 0.22) 0%, transparent 70%)," +
              "radial-gradient(55% 40% at 10% 95%, hsl(192 91% 36% / 0.14) 0%, transparent 70%)",
          }}
        />

        <div className="relative flex items-center gap-3">
          <img
            src="/logo-uniyra.png"
            alt="Uniyra AI Lab"
            className="h-12 w-12 rounded-md object-contain"
          />
          <div className="leading-tight">
            <span className="block font-display text-lg font-semibold tracking-tight">Uniyra</span>
            <span className="block font-mono text-[10px] uppercase tracking-[0.25em] text-slate-400">AI Lab</span>
          </div>
        </div>

        <div className="relative max-w-md">
          <h1 className="font-display text-4xl font-semibold leading-tight tracking-tight text-white">
            Build. Experiment.<br />
            <span className="text-[hsl(var(--brand-indigo))]">Evaluate.</span>
          </h1>
          <p className="mt-4 text-sm leading-relaxed text-slate-300">
            The AI R&amp;D workstation for running controlled experiments,
            judging outputs with rubrics, and turning results into validated findings.
          </p>
        </div>

        <p className="relative text-xs text-slate-500">Uniyra AI Lab — the AI R&amp;D workstation</p>
      </div>

      {/* Authentication panel */}
      <div className="flex w-full flex-col items-center justify-center p-6 sm:p-10 lg:w-[58%]">
        <div className="mb-8 flex flex-col items-center text-center">
          <img
            src="/logo-uniyra.png"
            alt="Uniyra AI Lab"
            className="h-14 w-14 rounded-xl object-contain"
          />
          <span className="mt-3 font-display text-lg font-semibold tracking-tight">Uniyra</span>
          <span className="font-mono text-[10px] uppercase tracking-[0.25em] text-muted-foreground">AI Lab</span>
        </div>
        {children}
      </div>
    </div>
  );
}

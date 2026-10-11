import React from "react";
import { FlaskRound, GitBranch, ShieldCheck } from "lucide-react";

const pipeline = [
  { icon: FlaskRound, label: "Experiment" },
  { icon: GitBranch, label: "Run" },
  { icon: ShieldCheck, label: "Finding" },
];

export default function AuthShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen w-full bg-background">
      {/* Branding panel — dark navy on all themes */}
      <div className="hidden w-[42%] flex-col justify-between border-r border-white/5 bg-[hsl(222_47%_8%)] p-10 text-white xl:p-14 lg:flex">
        <div className="flex items-center gap-3">
          <img
            src="/logo-uniyra.png"
            alt="Uniyra AI Lab"
            className="h-11 w-11 rounded-md object-contain"
          />
          <span className="font-display text-lg font-semibold tracking-tight">Uniyra AI Lab</span>
        </div>

        <div className="max-w-md">
          <h1 className="font-display text-4xl font-semibold leading-tight tracking-tight text-white">
            Build. Experiment.<br />Evaluate.
          </h1>
          <p className="mt-4 text-sm leading-relaxed text-slate-300">
            The AI R&amp;D workstation for running controlled experiments,
            judging outputs with rubrics, and turning results into validated findings.
          </p>

          {/* Stylized product visual — pipeline of the lab workflow */}
          <div
            aria-hidden="true"
            className="mt-10 rounded-xl border border-white/10 bg-white/[0.04] p-5 shadow-2xl shadow-black/30"
          >
            <div className="flex items-center gap-1.5">
              <span className="h-2.5 w-2.5 rounded-full bg-white/15" />
              <span className="h-2.5 w-2.5 rounded-full bg-white/15" />
              <span className="h-2.5 w-2.5 rounded-full bg-white/15" />
            </div>
            <div className="mt-4 space-y-2.5">
              {pipeline.map(({ icon: Icon, label }, i) => (
                <div
                  key={label}
                  className="flex items-center gap-3 rounded-lg border border-white/10 bg-[hsl(222_47%_11%)] px-3.5 py-2.5"
                  style={{ marginLeft: `${i * 14}px` }}
                >
                  <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-[hsl(var(--brand-indigo))]/20">
                    <Icon className="h-4 w-4 text-[hsl(var(--brand-indigo))]" />
                  </span>
                  <span className="text-sm font-medium text-slate-200">{label}</span>
                  <span className="ml-auto font-mono text-[10px] uppercase tracking-widest text-slate-500">
                    step {i + 1}
                  </span>
                </div>
              ))}
            </div>
            <div className="mt-4 flex items-center gap-2 border-t border-white/10 pt-3 font-mono text-[11px] text-slate-400">
              <span className="h-1.5 w-1.5 rounded-full bg-[hsl(var(--brand-emerald))]" />
              Project → Experiment → Run → Evaluation → Finding
            </div>
          </div>
        </div>

        <p className="text-xs text-slate-500">Uniyra AI Lab — the AI R&amp;D workstation</p>
      </div>

      {/* Authentication panel */}
      <div className="flex w-full items-center justify-center p-6 sm:p-10 lg:w-[58%]">
        {children}
      </div>
    </div>
  );
}

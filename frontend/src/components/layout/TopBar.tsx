import React from "react";
import { Search } from "lucide-react";
import { ProjectSwitcher } from "@/components/layout/ProjectSwitcher";
import { ThemeToggle } from "@/components/layout/ThemeToggle";
import { UserMenu } from "@/components/layout/UserMenu";
import { PulseDot } from "@/components/common/Common";

export const TopBar: React.FC<{ onSearch: () => void }> = ({ onSearch }) => (
  <header
    data-testid="top-telemetry-bar"
    className="flex h-[52px] shrink-0 items-center justify-between gap-4 border-b border-border bg-background/80 px-4 backdrop-blur"
  >
    <div className="flex items-center gap-3">
      <ProjectSwitcher />
      <div className="hidden items-center gap-2 rounded-md border border-border bg-card px-2.5 py-1 lg:flex">
        <PulseDot />
        <span className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Universal Key online</span>
      </div>
    </div>

    <div className="flex items-center gap-2">
      <button
        data-testid="global-search-trigger"
        onClick={onSearch}
        className="flex items-center gap-2 rounded-md border border-border bg-card px-3 py-1.5 text-sm text-muted-foreground transition-colors hover:bg-accent"
      >
        <Search className="h-3.5 w-3.5" />
        <span className="hidden sm:inline">Search</span>
        <kbd className="ml-2 hidden rounded border border-border bg-muted px-1.5 py-0.5 text-[10px] font-mono sm:inline">⌘K</kbd>
      </button>
      <ThemeToggle />
      <UserMenu />
    </div>
  </header>
);

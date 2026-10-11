import React, { useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { cn } from "@/lib/utils";
import {
  Home, MessageSquare, Swords, FlaskConical, FileText, Boxes, Settings, Database,
  PanelLeftClose, PanelLeft, FlaskRound, Users, Microscope, Lightbulb, LineChart, AlertTriangle, Wrench, ShieldAlert,
  BarChart3, BadgeCheck, FileCode2, Bot, Layers, ChevronDown, ChevronRight,
} from "lucide-react";

type NavItem = {
  to: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  testid: string;
  end?: boolean;
};

type NavSection = {
  key: string;
  title: string;
  items: NavItem[];
};

export const NAV_SECTIONS: NavSection[] = [
  {
    key: "overview",
    title: "Overview",
    items: [
      { to: "/", label: "Dashboard", icon: Home, end: true, testid: "nav-home" },
      { to: "/workspace", label: "Workspace", icon: MessageSquare, testid: "nav-workspace" },
      { to: "/models", label: "Models & Providers", icon: Boxes, testid: "nav-models" },
    ],
  },
  {
    key: "build",
    title: "Build & Configure",
    items: [
      { to: "/agents", label: "Agent Lab", icon: Bot, testid: "nav-agent-lab" },
      { to: "/tools", label: "Tool Lab", icon: Wrench, testid: "nav-tool-lab" },
      { to: "/context-lab", label: "Context Lab", icon: Layers, testid: "nav-context-lab" },
      { to: "/datasets", label: "Datasets", icon: Database, testid: "nav-datasets" },
    ],
  },
  {
    key: "experiment",
    title: "Experiment",
    items: [
      { to: "/arena", label: "Arena", icon: Swords, testid: "nav-arena" },
      { to: "/prompt-lab", label: "Prompt Lab", icon: FileCode2, testid: "nav-prompt-lab" },
      { to: "/experiments", label: "Experiments", icon: FlaskConical, testid: "nav-experiments" },
      { to: "/research", label: "Research", icon: Microscope, testid: "nav-research" },
    ],
  },
  {
    key: "safety",
    title: "Evaluate & Safety",
    items: [
      { to: "/scorecards", label: "Scorecards", icon: BarChart3, testid: "nav-scorecards" },
      { to: "/findings", label: "Findings", icon: Lightbulb, testid: "nav-findings" },
      { to: "/failures", label: "Failures", icon: AlertTriangle, testid: "nav-failures" },
      { to: "/red-team", label: "Red Team", icon: ShieldAlert, testid: "nav-red-team" },
      { to: "/certification", label: "Certification", icon: BadgeCheck, testid: "nav-certification" },
    ],
  },
  {
    key: "operations",
    title: "Operations",
    items: [
      { to: "/observability", label: "Observability", icon: LineChart, testid: "nav-observability" },
      { to: "/artifacts", label: "Artifacts", icon: FileText, testid: "nav-artifacts" },
      { to: "/council", label: "Council", icon: Users, testid: "nav-council" },
    ],
  },
];

export const Sidebar: React.FC<{ collapsed: boolean; onToggle: () => void }> = ({ collapsed, onToggle }) => {
  const [collapsedSections, setCollapsedSections] = useState<Set<string>>(new Set());
  const location = useLocation();

  const toggleSection = (key: string) => {
    setCollapsedSections((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  return (
    <aside
      data-testid="navigation-sidebar"
      className={cn("flex h-full min-h-0 flex-col border-r border-border bg-card transition-[width] duration-200", collapsed ? "w-[64px]" : "w-[240px]")}
    >
      <div className={cn("flex h-[52px] shrink-0 items-center border-b border-border px-3", collapsed ? "justify-center" : "justify-between")}>
        {!collapsed && (
          <div className="flex items-center gap-2">
            <div className="flex h-7 w-7 items-center justify-center rounded-md bg-primary text-primary-foreground">
              <FlaskRound className="h-4 w-4" />
            </div>
            <span className="font-display text-sm font-semibold tracking-tight">Uniyra AI Lab</span>
          </div>
        )}
        {collapsed && (
          <div className="flex h-7 w-7 items-center justify-center rounded-md bg-primary text-primary-foreground">
            <FlaskRound className="h-4 w-4" />
          </div>
        )}
      </div>

      <nav className="min-h-0 flex-1 space-y-0.5 overflow-y-auto overflow-x-hidden p-2 scrollbar-thin">
        {NAV_SECTIONS.map((section, i) => {
          const hasActive = section.items.some((n) =>
            n.end ? location.pathname === n.to : location.pathname === n.to || location.pathname.startsWith(n.to + "/")
          );
          const isOpen = !collapsedSections.has(section.key) || hasActive;
          return (
            <div key={section.key}>
              {!collapsed ? (
                <button
                  type="button"
                  data-testid={`nav-section-${section.key}`}
                  onClick={() => toggleSection(section.key)}
                  aria-expanded={isOpen}
                  className={cn(
                    "flex w-full items-center justify-between rounded-md px-3 pb-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground/70 hover:text-foreground",
                    i === 0 ? "pt-1" : "pt-3"
                  )}
                >
                  <span>{section.title}</span>
                  {isOpen ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
                </button>
              ) : (
                i > 0 && <div className="mx-2 my-1 border-t border-border" />
              )}
              {isOpen &&
                section.items.map((n) => (
                  <NavLink
                    key={n.to}
                    to={n.to}
                    end={n.end}
                    data-testid={n.testid}
                    className={({ isActive }) =>
                      cn(
                        "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                        collapsed && "justify-center px-0",
                        isActive ? "bg-accent text-foreground" : "text-muted-foreground hover:bg-accent/60 hover:text-foreground"
                      )
                    }
                    title={collapsed ? n.label : undefined}
                  >
                    <n.icon className="h-4 w-4 shrink-0" />
                    {!collapsed && <span>{n.label}</span>}
                  </NavLink>
                ))}
            </div>
          );
        })}
      </nav>

      <div className="shrink-0 space-y-0.5 border-t border-border p-2">
        <NavLink
          to="/settings"
          data-testid="nav-settings"
          className={({ isActive }) =>
            cn("flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
              collapsed && "justify-center px-0",
              isActive ? "bg-accent text-foreground" : "text-muted-foreground hover:bg-accent/60 hover:text-foreground")
          }
          title={collapsed ? "Settings" : undefined}
        >
          <Settings className="h-4 w-4 shrink-0" />
          {!collapsed && <span>Settings</span>}
        </NavLink>
        <button
          data-testid="sidebar-collapse-toggle"
          onClick={onToggle}
          className={cn("flex w-full items-center gap-3 rounded-md px-3 py-2 text-sm font-medium text-muted-foreground transition-colors hover:bg-accent/60 hover:text-foreground", collapsed && "justify-center px-0")}
          title={collapsed ? "Expand" : "Collapse"}
        >
          {collapsed ? <PanelLeft className="h-4 w-4" /> : <PanelLeftClose className="h-4 w-4" />}
          {!collapsed && <span>Collapse</span>}
        </button>
      </div>
    </aside>
  );
};

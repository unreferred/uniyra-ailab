import React from "react";
import { NavLink, useNavigate, useLocation } from "react-router-dom";
import { cn } from "@/lib/utils";
import {
  Home, MessageSquare, Swords, FlaskConical, FileText, Boxes, Settings, Database,
  PanelLeftClose, PanelLeft, FlaskRound, Users, Microscope, Lightbulb, LineChart, AlertTriangle, Wrench, ShieldAlert,
  BarChart3, BadgeCheck, FileCode2, Bot, Layers,
} from "lucide-react";
import { FolderKanban } from "lucide-react";
import { useProjects } from "@/context/ProjectContext";

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
    key: "experiment",
    title: "Experiment",
    items: [
      { to: "/arena", label: "Arena", icon: Swords, testid: "nav-arena" },
      { to: "/prompt-lab", label: "Prompt Lab", icon: FileCode2, testid: "nav-prompt-lab" },
      { to: "/context-lab", label: "Context Lab", icon: Layers, testid: "nav-context-lab" },
      { to: "/agents", label: "Agent Lab", icon: Bot, testid: "nav-agent-lab" },
      { to: "/tools", label: "Tool Lab", icon: Wrench, testid: "nav-tool-lab" },
    ],
  },
  {
    key: "research",
    title: "Research",
    items: [
      { to: "/experiments", label: "Experiments", icon: FlaskConical, testid: "nav-experiments" },
      { to: "/datasets", label: "Datasets", icon: Database, testid: "nav-datasets" },
      { to: "/research", label: "Research", icon: Microscope, testid: "nav-research" },
      { to: "/artifacts", label: "Artifacts", icon: FileText, testid: "nav-artifacts" },
    ],
  },
  {
    key: "quality",
    title: "Quality",
    items: [
      { to: "/findings", label: "Findings", icon: Lightbulb, testid: "nav-findings" },
      { to: "/failures", label: "Failures", icon: AlertTriangle, testid: "nav-failures" },
      { to: "/red-team", label: "Red Team", icon: ShieldAlert, testid: "nav-red-team" },
      { to: "/scorecards", label: "Scorecards", icon: BarChart3, testid: "nav-scorecards" },
      { to: "/certification", label: "Certification", icon: BadgeCheck, testid: "nav-certification" },
      { to: "/observability", label: "Observability", icon: LineChart, testid: "nav-observability" },
    ],
  },
  {
    key: "workspace",
    title: "Workspace",
    items: [
      { to: "/", label: "Home", icon: Home, end: true, testid: "nav-home" },
      { to: "/workspace", label: "Workspace", icon: MessageSquare, testid: "nav-workspace" },
      { to: "/council", label: "Council", icon: Users, testid: "nav-council" },
      { to: "/models", label: "Models", icon: Boxes, testid: "nav-models" },
    ],
  },
];

export const Sidebar: React.FC<{ collapsed: boolean; onToggle: () => void }> = ({ collapsed, onToggle }) => {
  return (
    <aside
      data-testid="navigation-sidebar"
      className={cn("flex h-full flex-col border-r border-border bg-card transition-[width] duration-200", collapsed ? "w-[64px]" : "w-[240px]")}
    >
      <div className={cn("flex h-[52px] items-center border-b border-border px-3", collapsed ? "justify-center" : "justify-between")}>
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

      <nav className="flex-1 space-y-0.5 overflow-y-auto p-2 scrollbar-thin">
        <ProjectWorkspaceLink collapsed={collapsed} />
        {NAV_SECTIONS.map((section, i) => (
          <div key={section.key}>
            {!collapsed ? (
              <div
                data-testid={`nav-section-${section.key}`}
                className={cn(
                  "px-3 pb-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground/70",
                  i === 0 ? "pt-1" : "pt-3"
                )}
              >
                {section.title}
              </div>
            ) : (
              i > 0 && <div className="mx-2 my-1 border-t border-border" />
            )}
            {section.items.map((n) => (
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
        ))}
      </nav>

      <div className="space-y-0.5 border-t border-border p-2">
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

const ProjectWorkspaceLink: React.FC<{ collapsed: boolean }> = ({ collapsed }) => {
  const { activeId } = useProjects();
  const navigate = useNavigate();
  const location = useLocation();
  const target = activeId;
  const active = location.pathname.startsWith("/projects/");
  if (!target) return null;
  return (
    <button
      type="button"
      data-testid="nav-project-workspace"
      title={collapsed ? "Project Workspace" : undefined}
      onClick={() => navigate(`/projects/${target}`)}
      className={
        "flex w-full items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors " +
        (collapsed ? "justify-center px-0 " : "") +
        (active ? "bg-accent text-foreground" : "text-muted-foreground hover:bg-accent/60 hover:text-foreground")
      }
    >
      <FolderKanban className="h-4 w-4 shrink-0" />
      {!collapsed && <span>Project Workspace</span>}
    </button>
  );
};

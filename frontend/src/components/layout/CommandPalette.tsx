import React, { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import {
  CommandDialog, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList, CommandSeparator,
} from "@/components/ui/command";
import { useProjects } from "@/context/ProjectContext";
import {
  Home, MessageSquare, Swords, FlaskConical, FileText, Boxes, Settings, FolderKanban, Users, Microscope, Lightbulb,
  LineChart, Database, AlertTriangle, FolderOpen, Wrench, FileCode2, Bot, Layers, ShieldAlert, BarChart3, BadgeCheck,
  Activity, GitCompare,
} from "lucide-react";

type PaletteNavItem = { to: string; label: string; icon: React.ComponentType<{ className?: string }> };

const NAV: PaletteNavItem[] = [
  { to: "/arena", label: "Arena", icon: Swords },
  { to: "/prompt-lab", label: "Prompt Lab", icon: FileCode2 },
  { to: "/context-lab", label: "Context Lab", icon: Layers },
  { to: "/agents", label: "Agent Lab", icon: Bot },
  { to: "/tools", label: "Tool Lab", icon: Wrench },
  { to: "/experiments", label: "Experiments", icon: FlaskConical },
  { to: "/datasets", label: "Datasets", icon: Database },
  { to: "/research", label: "Research", icon: Microscope },
  { to: "/artifacts", label: "Artifacts", icon: FileText },
  { to: "/findings", label: "Findings", icon: Lightbulb },
  { to: "/failures", label: "Failures", icon: AlertTriangle },
  { to: "/red-team", label: "Red Team", icon: ShieldAlert },
  { to: "/scorecards", label: "Scorecards", icon: BarChart3 },
  { to: "/certification", label: "Certification", icon: BadgeCheck },
  { to: "/observability", label: "Observability", icon: LineChart },
  { to: "/trace", label: "Trace", icon: Activity },
  { to: "/run-compare", label: "Run Compare", icon: GitCompare },
  { to: "/", label: "Home", icon: Home },
  { to: "/workspace", label: "Workspace", icon: MessageSquare },
  { to: "/council", label: "Council", icon: Users },
  { to: "/models", label: "Models", icon: Boxes },
  { to: "/settings", label: "Settings", icon: Settings },
];

export const CommandPalette: React.FC<{ open: boolean; onOpenChange: (v: boolean) => void }> = ({ open, onOpenChange }) => {
  const navigate = useNavigate();
  const { projects, activeId, setActiveId } = useProjects();

  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if (e.key === "k" && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        onOpenChange(!open);
      }
    };
    document.addEventListener("keydown", down);
    return () => document.removeEventListener("keydown", down);
  }, [open, onOpenChange]);

  const go = (to: string) => { navigate(to); onOpenChange(false); };

  return (
    <CommandDialog open={open} onOpenChange={onOpenChange}>
      <CommandInput placeholder="Jump to a view or switch project…" data-testid="command-palette-input" />
      <CommandList>
        <CommandEmpty>No results found.</CommandEmpty>
        <CommandGroup heading="Navigate">
          {activeId && (
            <CommandItem value="nav Project Workspace" onSelect={() => go(`/projects/${activeId}`)}>
              <FolderOpen className="mr-2 h-4 w-4" /> Project Workspace
            </CommandItem>
          )}
          {NAV.map((n) => (
            <CommandItem key={n.to} onSelect={() => go(n.to)} value={`nav ${n.label}`}>
              <n.icon className="mr-2 h-4 w-4" /> {n.label}
            </CommandItem>
          ))}
        </CommandGroup>
        {projects.length > 0 && (
          <>
            <CommandSeparator />
            <CommandGroup heading="Switch project">
              {projects.map((p) => (
                <CommandItem key={p.id} value={`project ${p.name}`} onSelect={() => { setActiveId(p.id); go("/"); }}>
                  <FolderKanban className="mr-2 h-4 w-4" /> {p.name}
                </CommandItem>
              ))}
            </CommandGroup>
          </>
        )}
      </CommandList>
    </CommandDialog>
  );
};

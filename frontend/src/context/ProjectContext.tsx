import React, { createContext, useContext, useEffect, useState, useCallback } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Project } from "@/lib/types";

interface ProjectCtx {
  projects: Project[];
  activeProject: Project | null;
  activeId: string | null;
  setActiveId: (id: string) => void;
  isLoading: boolean;
  refresh: () => void;
}

const Ctx = createContext<ProjectCtx>(null as any);
const KEY = "omnilab.activeProject";

export const ProjectProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const qc = useQueryClient();
  const [activeId, setActiveIdState] = useState<string | null>(() => localStorage.getItem(KEY));

  const { data: projects = [], isLoading } = useQuery<Project[]>({
    queryKey: ["projects"],
    queryFn: async () => (await api.get("/projects")).data,
  });

  useEffect(() => {
    if (!isLoading && projects.length > 0) {
      const stillExists = activeId && projects.some((p) => p.id === activeId);
      if (!stillExists) setActiveIdState(projects[0].id);
    }
    if (!isLoading && projects.length === 0) setActiveIdState(null);
  }, [isLoading, projects, activeId]);

  const setActiveId = useCallback((id: string) => {
    setActiveIdState(id);
    localStorage.setItem(KEY, id);
  }, []);

  const refresh = useCallback(() => { qc.invalidateQueries({ queryKey: ["projects"] }); }, [qc]);

  const activeProject = projects.find((p) => p.id === activeId) || null;

  return (
    <Ctx.Provider value={{ projects, activeProject, activeId, setActiveId, isLoading, refresh }}>
      {children}
    </Ctx.Provider>
  );
};

export const useProjects = () => useContext(Ctx);

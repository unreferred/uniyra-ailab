import React from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { ThemeProvider } from "next-themes";
import { Toaster } from "@/components/ui/sonner";
import { AuthProvider, useAuth } from "@/context/AuthContext";
import { ProjectProvider } from "@/context/ProjectContext";
import { AppShell } from "@/components/layout/AppShell";
import { Loader2 } from "lucide-react";

import Login from "@/pages/Login";
import Register from "@/pages/Register";
import Home from "@/pages/Home";
import Workspace from "@/pages/Workspace";
import Arena from "@/pages/Arena";
import Council from "@/pages/Council";
import Experiments from "@/pages/Experiments";
import ExperimentDetail from "@/pages/ExperimentDetail";
import ExperimentCreate from "@/components/experiment/ExperimentCreate";
import Research from "@/pages/Research";
import ResearchDetail from "@/pages/ResearchDetail";
import Findings from "@/pages/Findings";
import Datasets from "@/pages/Datasets";
import DatasetDetail from "@/pages/DatasetDetail";
import Artifacts from "@/pages/Artifacts";
import Observability from "@/pages/Observability";
import Failures from "@/pages/Failures";
import RedTeam from "@/pages/RedTeam";
import Scorecards from "@/pages/Scorecards";
import Certification from "@/pages/Certification";
import Tools from "@/pages/Tools";
import RunCompare from "@/pages/RunCompare";
import ContextLab from "@/pages/ContextLab";
import PromptLab from "@/pages/PromptLab";
import PromptDetail from "@/pages/PromptDetail";
import AgentLab from "@/pages/AgentLab";
import AgentDetail from "@/pages/AgentDetail";
import TraceViewer from "@/pages/TraceViewer";
import Models from "@/pages/Models";
import ProjectManager from "@/pages/ProjectManager";
import Settings from "@/pages/Settings";

const FullLoader = () => (
  <div className="flex h-screen w-screen items-center justify-center bg-background">
    <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
  </div>
);

const Protected: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { user } = useAuth();
  if (user === undefined) return <FullLoader />;
  if (user === null) return <Navigate to="/login" replace />;
  return <>{children}</>;
};

const PublicOnly: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { user } = useAuth();
  if (user === undefined) return <FullLoader />;
  if (user) return <Navigate to="/" replace />;
  return <>{children}</>;
};

const Shell: React.FC = () => (
  <ProjectProvider>
    <AppShell>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/workspace" element={<Workspace />} />
        <Route path="/arena" element={<Arena />} />
        <Route path="/council" element={<Council />} />
        <Route path="/experiments" element={<Experiments />} />
        <Route path="/experiments/new" element={<ExperimentCreate />} />
        <Route path="/experiments/:id" element={<ExperimentDetail />} />
        <Route path="/research" element={<Research />} />
        <Route path="/research/:id" element={<ResearchDetail />} />
        <Route path="/findings" element={<Findings />} />
        <Route path="/datasets" element={<Datasets />} />
        <Route path="/datasets/:id" element={<DatasetDetail />} />
        <Route path="/artifacts" element={<Artifacts />} />
        <Route path="/observability" element={<Observability />} />
        <Route path="/failures" element={<Failures />} />
        <Route path="/red-team" element={<RedTeam />} />
        <Route path="/scorecards" element={<Scorecards />} />
        <Route path="/certification" element={<Certification />} />
        <Route path="/tools" element={<Tools />} />
        <Route path="/run-compare" element={<RunCompare />} />
        <Route path="/context-lab" element={<ContextLab />} />
        <Route path="/prompt-lab" element={<PromptLab />} />
        <Route path="/prompt-lab/prompts/:id" element={<PromptDetail />} />
        <Route path="/agents" element={<AgentLab />} />
        <Route path="/agents/:id" element={<AgentDetail />} />
        <Route path="/trace" element={<TraceViewer />} />
        <Route path="/models" element={<Models />} />
        <Route path="/projects/:id" element={<ProjectManager />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AppShell>
  </ProjectProvider>
);

export default function App() {
  return (
    <ThemeProvider attribute="class" defaultTheme="light" enableSystem={false}>
      <AuthProvider>
        <BrowserRouter>
          <Routes>
            <Route path="/login" element={<PublicOnly><Login /></PublicOnly>} />
            <Route path="/register" element={<PublicOnly><Register /></PublicOnly>} />
            <Route path="/*" element={<Protected><Shell /></Protected>} />
          </Routes>
        </BrowserRouter>
        <Toaster position="bottom-right" />
      </AuthProvider>
    </ThemeProvider>
  );
}

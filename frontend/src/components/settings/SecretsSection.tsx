import React from "react";
import { useProjects } from "@/context/ProjectContext";
import { useToolCredentials, useToolRegistry } from "@/lib/hooks";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { CredentialsPanel } from "@/components/tools/CredentialsPanel";

// Advanced secrets vault. Normal tool configuration lives with each tool
// (Tools -> Configure); this is the encrypted vault for debugging/admin.
export const SecretsSection: React.FC = () => {
  const { projects, activeProject, setActiveId } = useProjects();
  const credQuery = useToolCredentials(activeProject?.id);
  const toolsQuery = useToolRegistry(activeProject?.id);

  return (
    <div data-testid="settings-secrets">
      {!activeProject ? (
        <p className="text-sm text-muted-foreground">
          Select a project to view its encrypted secrets vault.
        </p>
      ) : (
        <>
          <div className="mb-3 max-w-xs">
            <label className="text-xs font-medium text-muted-foreground">Project</label>
            <Select value={activeProject.id} onValueChange={setActiveId}>
              <SelectTrigger className="mt-1" data-testid="settings-secrets-project">
                <SelectValue placeholder="Select project" />
              </SelectTrigger>
              <SelectContent>
                {(projects || []).map((p) => (
                  <SelectItem key={p.id} value={p.id}>{p.name}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <CredentialsPanel
            projectId={activeProject.id}
            availableProjects={(projects || []).map((p) => ({ id: p.id, name: p.name }))}
            credentials={credQuery.data?.items || []}
            isLoading={credQuery.isLoading}
            isError={credQuery.isError}
            error={credQuery.error}
            tools={toolsQuery.data?.items || []}
          />
        </>
      )}
    </div>
  );
};

export default SecretsSection;

import React from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { api, apiError } from "@/lib/api";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { EmptyState, LoadingSpinner, ErrorNote } from "@/components/common/Common";
import { relTime } from "@/lib/format";

interface EntityListTabProps {
  projectId: string;
  endpoint: string;
  testId: string;
  emptyTitle: string;
  emptyDescription: string;
  globalHref: string;
  globalLabel: string;
  titleOf: (item: any) => string;
  detailHrefOf: (item: any) => string;
  subtitleOf?: (item: any) => string | null;
}

export const EntityListTab: React.FC<EntityListTabProps> = ({
  projectId, endpoint, testId, emptyTitle, emptyDescription,
  globalHref, globalLabel, titleOf, detailHrefOf, subtitleOf,
}) => {
  const { data, isLoading, isError, error } = useQuery<any[]>({
    queryKey: [endpoint, projectId],
    queryFn: async () => (await api.get(`/${endpoint}`, { params: { project_id: projectId } })).data,
    enabled: !!projectId,
  });

  if (isLoading) return <LoadingSpinner label={`Loading ${endpoint}…`} />;
  if (isError) return <ErrorNote message={apiError(error)} />;
  const raw: any = data;
  const items = Array.isArray(raw) ? raw : raw?.items || [];
  if (items.length === 0) {
    return (
      <EmptyState
        title={emptyTitle}
        description={emptyDescription}
        testId={`${testId}-empty`}
        action={
          <Link to={globalHref}>
            <Button size="sm" variant="outline">Open {globalLabel}</Button>
          </Link>
        }
      />
    );
  }
  return (
    <div className="space-y-2" data-testid={testId}>
      <div className="flex justify-end">
        <Link to={globalHref}>
          <Button size="sm" variant="ghost">Open {globalLabel} →</Button>
        </Link>
      </div>
      {items.map((item: any) => (
        <Card key={item.id} className="flex items-center justify-between gap-3 p-3" data-testid={`${testId}-row-${item.id}`}>
          <div className="min-w-0">
            <Link to={detailHrefOf(item)} className="block truncate text-sm font-medium text-primary hover:underline">
              {titleOf(item)}
            </Link>
            <p className="mt-0.5 truncate text-[11px] text-muted-foreground">
              {[subtitleOf?.(item), item.updated_at ? relTime(item.updated_at) : null].filter(Boolean).join(" · ")}
            </p>
          </div>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => {
              try {
                void navigator.clipboard.writeText(item.id);
                toast.success("ID copied");
              } catch {
                toast.error("Copy failed");
              }
            }}
          >
            Copy ID
          </Button>
        </Card>
      ))}
    </div>
  );
};

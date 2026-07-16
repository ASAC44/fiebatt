import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  applyGlobalEditPlan,
  applyPropagationResult,
  createGlobalEditPlan,
  discoverOccurrences,
  getEntity,
  getGlobalEditPlan,
  getHealth,
  getProject,
  getPropagation,
  pollJob,
  propagate,
  type EntityResp,
  type GlobalEditPlanResp,
  type ProjectEntitySummary,
  type PropagationResultResp,
} from "@/lib/api";

type AsyncState = "idle" | "planned" | "pending" | "processing" | "ready" | "error";

type AcceptedEditSnapshot = {
  prompt: string;
  sourceVariantUrl: string;
  segmentId: string;
  entityJobId: string | null;
};

type DiscoveryState = {
  status: AsyncState;
  jobId: string | null;
  error: string | null;
};

type PropagationState = {
  status: AsyncState;
  jobId: string | null;
  error: string | null;
  results: PropagationResultResp[];
  applyingIds: string[];
};

export type AcceptedEditInput = {
  prompt: string;
  sourceVariantUrl: string;
  segmentId: string;
  entityJobId: string | null;
};

export type ContinuityDashboardController = {
  globalEnabled: boolean;
  globalPlan: GlobalEditPlanResp | null;
  selectedAppearanceIds: string[];
  projectEntityCount: number;
  projectEntities: ProjectEntitySummary[];
  latestEntity: EntityResp | null;
  acceptedEdit: AcceptedEditSnapshot | null;
  discovery: DiscoveryState;
  propagation: PropagationState;
  propagationCounts: {
    total: number;
    ready: number;
    processing: number;
    errors: number;
    applied: number;
  };
  hasPropagatableAppearances: boolean;
  refreshProjectSummary: () => Promise<void>;
  beginAcceptedEdit: (input: AcceptedEditInput) => Promise<void>;
  startDiscovery: () => Promise<void>;
  prepareGlobalPlan: () => Promise<void>;
  startPropagation: () => Promise<void>;
  toggleAppearance: (appearanceId: string) => void;
  applyPropagation: (resultId: string) => Promise<void>;
  applyAllPropagation: () => Promise<void>;
  clearLatestEntity: () => void;
};

function sleep(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

export function useContinuityDashboard(
  projectId: string | null,
): ContinuityDashboardController {
  const [projectEntities, setProjectEntities] = useState<ProjectEntitySummary[]>([]);
  const [latestEntity, setLatestEntity] = useState<EntityResp | null>(null);
  const [acceptedEdit, setAcceptedEdit] = useState<AcceptedEditSnapshot | null>(null);
  const [globalEnabled, setGlobalEnabled] = useState(false);
  const [globalPlan, setGlobalPlan] = useState<GlobalEditPlanResp | null>(null);
  const [selectedAppearanceIds, setSelectedAppearanceIds] = useState<string[]>([]);
  const [discovery, setDiscovery] = useState<DiscoveryState>({
    status: "idle",
    jobId: null,
    error: null,
  });
  const [propagation, setPropagation] = useState<PropagationState>({
    status: "idle",
    jobId: null,
    error: null,
    results: [],
    applyingIds: [],
  });
  const discoveryTokenRef = useRef(0);
  const propagationTokenRef = useRef(0);

  useEffect(() => {
    let cancelled = false;
    void getHealth()
      .then((health) => {
        if (!cancelled) {
          setGlobalEnabled(health.features?.global_edit_planning === true);
        }
      })
      .catch(() => {
        if (!cancelled) setGlobalEnabled(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const refreshProjectSummary = useCallback(async () => {
    if (!projectId) {
      setProjectEntities([]);
      return;
    }
    const project = await getProject(projectId);
    setProjectEntities(project.entities);
  }, [projectId]);

  useEffect(() => {
    discoveryTokenRef.current += 1;
    propagationTokenRef.current += 1;
    setLatestEntity(null);
    setAcceptedEdit(null);
    setGlobalPlan(null);
    setSelectedAppearanceIds([]);
    setDiscovery({ status: "idle", jobId: null, error: null });
    setPropagation({
      status: "idle",
      jobId: null,
      error: null,
      results: [],
      applyingIds: [],
    });
    if (!projectId) {
      setProjectEntities([]);
      return;
    }
    void refreshProjectSummary().catch(() => {
      setProjectEntities([]);
    });
  }, [projectId, refreshProjectSummary]);

  const beginAcceptedEdit = useCallback(
    async (input: AcceptedEditInput) => {
      if (!projectId) return;

      const baselineIds = new Set(projectEntities.map((entity) => entity.id));
      const token = discoveryTokenRef.current + 1;
      discoveryTokenRef.current = token;
      propagationTokenRef.current += 1;

      setAcceptedEdit(input);
      setLatestEntity(null);
      setGlobalPlan(null);
      setSelectedAppearanceIds([]);
      setPropagation({
        status: "idle",
        jobId: null,
        error: null,
        results: [],
        applyingIds: [],
      });

      if (!input.entityJobId) {
        // Backend skips the entity-scan job for full-frame regenerations
        // and "remove" intents because there's no single subject to track.
        // Stay idle so the UI renders the neutral tracked-count chip
        // instead of the red "continuity error" chip.
        setDiscovery({ status: "idle", jobId: null, error: null });
        return;
      }

      setDiscovery({
        status: "processing",
        jobId: input.entityJobId,
        error: null,
      });

      try {
        const final = await pollJob(
          input.entityJobId,
          (job) => {
            if (discoveryTokenRef.current !== token) return;
            setDiscovery({
              status: job.status === "pending" ? "pending" : "processing",
              jobId: input.entityJobId,
              error: null,
            });
          },
          1000,
        );
        if (discoveryTokenRef.current !== token) return;

        if (final.status === "error") {
          setDiscovery({
            status: "error",
            jobId: input.entityJobId,
            error: final.error || "continuity scan failed.",
          });
          return;
        }

        const project = await getProject(projectId);
        if (discoveryTokenRef.current !== token) return;
        setProjectEntities(project.entities);

        const discoveredEntity =
          project.entities.find((entity) => !baselineIds.has(entity.id)) ??
          project.entities[project.entities.length - 1] ??
          null;

        if (!discoveredEntity) {
          setDiscovery({
            status: "error",
            jobId: input.entityJobId,
            error: "continuity scan finished, but no tracked entity was returned.",
          });
          return;
        }

        const entity = await getEntity(discoveredEntity.id);
        if (discoveryTokenRef.current !== token) return;

        setLatestEntity(entity);
        setSelectedAppearanceIds(entity.appearances.map((appearance) => appearance.id));
        setDiscovery({
          status: "ready",
          jobId: input.entityJobId,
          error: null,
        });
      } catch (error) {
        if (discoveryTokenRef.current !== token) return;
        setDiscovery({
          status: "error",
          jobId: input.entityJobId,
          error: error instanceof Error ? error.message : String(error),
        });
      }
    },
    [projectEntities, projectId],
  );

  const pollPropagationStatus = useCallback(async (
    jobId: string,
    token: number,
    planId?: string,
  ) => {
    while (true) {
      const status = await getPropagation(jobId);
      if (propagationTokenRef.current !== token) return null;

      if (planId) {
        void getGlobalEditPlan(planId)
          .then((plan) => {
            if (propagationTokenRef.current === token) setGlobalPlan(plan);
          })
          .catch(() => undefined);
      }

      setPropagation((current) => ({
        ...current,
        status: status.status === "pending" ? "pending" : status.status === "processing" ? "processing" : status.status === "done" ? "ready" : "error",
        jobId,
        error: status.error,
        results: status.results,
      }));

      if (status.status === "done" || status.status === "error") {
        return status;
      }
      await sleep(1200);
    }
  }, []);

  const startDiscovery = useCallback(async () => {
    if (!acceptedEdit) return;
    setDiscovery({ status: "pending", jobId: null, error: null });
    try {
      const response = await discoverOccurrences(acceptedEdit.segmentId);
      await beginAcceptedEdit({
        ...acceptedEdit,
        entityJobId: response.job_id,
      });
    } catch (error) {
      setDiscovery({
        status: "error",
        jobId: null,
        error: error instanceof Error ? error.message : String(error),
      });
    }
  }, [acceptedEdit, beginAcceptedEdit]);

  const toggleAppearance = useCallback((appearanceId: string) => {
    setSelectedAppearanceIds((current) =>
      current.includes(appearanceId)
        ? current.filter((id) => id !== appearanceId)
        : [...current, appearanceId],
    );
    setGlobalPlan(null);
    setPropagation({
      status: "idle",
      jobId: null,
      error: null,
      results: [],
      applyingIds: [],
    });
  }, []);

  const prepareGlobalPlan = useCallback(async () => {
    if (!globalEnabled || !latestEntity || !acceptedEdit) return;
    if (selectedAppearanceIds.length === 0) {
      setPropagation((current) => ({
        ...current,
        status: "error",
        error: "select at least one appearance.",
      }));
      return;
    }
    setPropagation({
      status: "pending",
      jobId: null,
      error: null,
      results: [],
      applyingIds: [],
    });
    try {
      const allSelected = selectedAppearanceIds.length === latestEntity.appearances.length;
      const plan = await createGlobalEditPlan({
        entity_id: latestEntity.entity_id,
        reference_segment_id: acceptedEdit.segmentId,
        scope: allSelected ? "all_occurrences" : "selected_occurrences",
        occurrence_ids: allSelected ? [] : selectedAppearanceIds,
      });
      setGlobalPlan(plan);
      setPropagation({
        status: "planned",
        jobId: null,
        error: null,
        results: [],
        applyingIds: [],
      });
    } catch (error) {
      setGlobalPlan(null);
      setPropagation({
        status: "error",
        jobId: null,
        error: error instanceof Error ? error.message : String(error),
        results: [],
        applyingIds: [],
      });
    }
  }, [acceptedEdit, globalEnabled, latestEntity, selectedAppearanceIds]);

  const startPropagation = useCallback(async () => {
    if (!latestEntity || !acceptedEdit) return;
    if (globalEnabled && !globalPlan) {
      await prepareGlobalPlan();
      return;
    }

    const token = propagationTokenRef.current + 1;
    propagationTokenRef.current = token;
    setPropagation({
      status: "pending",
      jobId: null,
      error: null,
      results: [],
      applyingIds: [],
    });

    try {
      const response = await propagate(
        globalEnabled && globalPlan
          ? { global_plan_id: globalPlan.plan_id }
          : {
              entity_id: latestEntity.entity_id,
              source_variant_url: acceptedEdit.sourceVariantUrl,
              prompt: acceptedEdit.prompt,
              auto_apply: false,
            },
      );
      if (propagationTokenRef.current !== token) return;

      setPropagation((current) => ({
        ...current,
        status: "processing",
        jobId: response.propagation_job_id,
      }));

      const final = await pollPropagationStatus(
        response.propagation_job_id,
        token,
        globalPlan?.plan_id,
      );
      if (!final || propagationTokenRef.current !== token) return;

      if (final.status === "error") {
        setPropagation((current) => ({
          ...current,
          status: "error",
          error: final.error || "continuity pack generation failed.",
        }));
        return;
      }

      setPropagation((current) => ({
        ...current,
        status: "ready",
        error: null,
      }));
    } catch (error) {
      if (propagationTokenRef.current !== token) return;
      setPropagation({
        status: "error",
        jobId: null,
        error: error instanceof Error ? error.message : String(error),
        results: [],
        applyingIds: [],
      });
    }
  }, [
    acceptedEdit,
    globalEnabled,
    globalPlan,
    latestEntity,
    pollPropagationStatus,
    prepareGlobalPlan,
  ]);

  const applyPropagation = useCallback(
    async (resultId: string) => {
      if (globalEnabled) return;
      if (!propagation.jobId) return;

      setPropagation((current) => ({
        ...current,
        applyingIds: current.applyingIds.includes(resultId)
          ? current.applyingIds
          : [...current.applyingIds, resultId],
      }));

      try {
        const updated = await applyPropagationResult(propagation.jobId, resultId);
        setPropagation((current) => ({
          ...current,
          results: current.results.map((result) =>
            result.id === resultId ? updated : result,
          ),
          applyingIds: current.applyingIds.filter((id) => id !== resultId),
        }));
      } catch (error) {
        setPropagation((current) => ({
          ...current,
          status: "error",
          error: error instanceof Error ? error.message : String(error),
          applyingIds: current.applyingIds.filter((id) => id !== resultId),
        }));
      }
    },
    [globalEnabled, propagation.jobId],
  );

  const applyAllPropagation = useCallback(async () => {
    if (globalEnabled && globalPlan) {
      setPropagation((current) => ({
        ...current,
        applyingIds: current.results.map((result) => result.id),
      }));
      try {
        const applied = await applyGlobalEditPlan(globalPlan.plan_id);
        window.dispatchEvent(
          new CustomEvent("fiebatt:timeline-refresh", {
            detail: { tool: "apply_global_edit", timeline: applied.timeline },
          }),
        );
        if (propagation.jobId) {
          const refreshed = await getPropagation(propagation.jobId);
          setPropagation({
            status: "ready",
            jobId: propagation.jobId,
            error: null,
            results: refreshed.results,
            applyingIds: [],
          });
        }
        const refreshedPlan = await getGlobalEditPlan(globalPlan.plan_id);
        setGlobalPlan(refreshedPlan);
      } catch (error) {
        setPropagation((current) => ({
          ...current,
          status: "error",
          error: error instanceof Error ? error.message : String(error),
          applyingIds: [],
        }));
      }
      return;
    }
    const readyResults = propagation.results.filter(
      (result) => result.status === "done" && !result.applied,
    );
    for (const result of readyResults) {
      await applyPropagation(result.id);
    }
  }, [
    applyPropagation,
    globalEnabled,
    globalPlan,
    propagation.jobId,
    propagation.results,
  ]);

  const clearLatestEntity = useCallback(() => {
    discoveryTokenRef.current += 1;
    propagationTokenRef.current += 1;
    setLatestEntity(null);
    setAcceptedEdit(null);
    setGlobalPlan(null);
    setSelectedAppearanceIds([]);
    setDiscovery({ status: "idle", jobId: null, error: null });
    setPropagation({
      status: "idle",
      jobId: null,
      error: null,
      results: [],
      applyingIds: [],
    });
  }, []);

  const propagationCounts = useMemo(() => {
    const ready = propagation.results.filter((result) => result.status === "done").length;
    const processing = propagation.results.filter(
      (result) => result.status === "pending" || result.status === "processing",
    ).length;
    const errors = propagation.results.filter((result) => result.status === "error").length;
    const applied = propagation.results.filter((result) => result.applied).length;
    return {
      total: propagation.results.length,
      ready,
      processing,
      errors,
      applied,
    };
  }, [propagation.results]);

  return {
    globalEnabled,
    globalPlan,
    selectedAppearanceIds,
    projectEntityCount: projectEntities.length,
    projectEntities,
    latestEntity,
    acceptedEdit,
    discovery,
    propagation,
    propagationCounts,
    hasPropagatableAppearances: (latestEntity?.appearances.length ?? 0) > 0,
    refreshProjectSummary,
    beginAcceptedEdit,
    startDiscovery,
    prepareGlobalPlan,
    startPropagation,
    toggleAppearance,
    applyPropagation,
    applyAllPropagation,
    clearLatestEntity,
  };
}

/** TanStack Query hooks over the FastAPI backend. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { http } from "./client";
import type {
  Camera,
  CameraCreate,
  CameraIpImportResult,
  CameraUpdate,
  Nvr,
  NvrCreate,
  NvrEvent,
  NvrHealthResult,
  NvrTestResult,
  NvrUpdate,
  Region,
} from "./types";

export const qk = {
  nvrs: ["nvrs"] as const,
  nvrHealth: ["nvrs", "health"] as const,
  cameras: ["cameras"] as const,
  regions: ["regions"] as const,
  events: ["events"] as const,
};

// ── Queries ──────────────────────────────────────────────────────────────────

export function useNvrs() {
  return useQuery({ queryKey: qk.nvrs, queryFn: () => http.get<Nvr[]>("/nvrs") });
}
export function useCameras() {
  return useQuery({ queryKey: qk.cameras, queryFn: () => http.get<Camera[]>("/cameras") });
}
export function useRegions() {
  return useQuery({ queryKey: qk.regions, queryFn: () => http.get<Region[]>("/regions") });
}
export function useNvrHealth(enabled = true) {
  return useQuery({
    queryKey: qk.nvrHealth,
    queryFn: () => http.get<NvrHealthResult[]>("/nvrs/health"),
    enabled,
    refetchInterval: 15_000,
  });
}
export function useEvents(limit = 50) {
  return useQuery({
    queryKey: [...qk.events, limit],
    queryFn: () => http.get<NvrEvent[]>("/events", { limit }),
  });
}

// ── NVR mutations ────────────────────────────────────────────────────────────

function useInvalidate() {
  const qc = useQueryClient();
  return () => {
    qc.invalidateQueries({ queryKey: qk.nvrs });
    qc.invalidateQueries({ queryKey: qk.cameras });
  };
}

export function useCreateNvr() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (body: NvrCreate) => http.post<Nvr>("/nvrs", body),
    onSuccess: invalidate,
  });
}
export function useUpdateNvr() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: NvrUpdate }) =>
      http.patch<Nvr>(`/nvrs/${id}`, body),
    onSuccess: invalidate,
  });
}
export function useDeleteNvr() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (id: string) => http.del<null>(`/nvrs/${id}`),
    onSuccess: invalidate,
  });
}
export function useTestNvr() {
  return useMutation({ mutationFn: (id: string) => http.post<NvrTestResult>(`/nvrs/${id}/test`) });
}
export function useSetChannels() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ id, count, prune }: { id: string; count: number; prune: boolean }) =>
      http.post<Nvr>(`/nvrs/${id}/set-channels`, { count, prune }),
    onSuccess: invalidate,
  });
}
export function useImportCameraIps() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (id: string) => http.post<CameraIpImportResult>(`/nvrs/${id}/import-camera-ips`),
    onSuccess: invalidate,
  });
}

// ── Camera mutations ─────────────────────────────────────────────────────────

export function useCreateCamera() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (body: CameraCreate) => http.post<Camera>("/cameras", body),
    onSuccess: invalidate,
  });
}
export function useUpdateCamera() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: CameraUpdate }) =>
      http.patch<Camera>(`/cameras/${id}`, body),
    onSuccess: invalidate,
  });
}
export function useDeleteCamera() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (id: string) => http.del<null>(`/cameras/${id}`),
    onSuccess: invalidate,
  });
}

// ── Relay (go2rtc/MediaMTX) ──────────────────────────────────────────────────

export function useReconcile() {
  const invalidate = useInvalidate();
  return useMutation<unknown, Error, boolean>({
    mutationFn: (deleteOrphans) =>
      http.post<unknown>(`/mediamtx/reconcile?delete_orphans=${deleteOrphans}`),
    onSuccess: invalidate,
  });
}

// ── Auth ─────────────────────────────────────────────────────────────────────

export function useChangePassword() {
  return useMutation({
    mutationFn: (body: { current_password: string; new_password: string }) =>
      http.post<unknown>("/auth/change-password", body),
  });
}

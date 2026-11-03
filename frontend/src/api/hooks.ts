import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  ApiError,
  apiGet,
  apiRequest,
  type ContextNoteRequest,
  type ContextNoteView,
  type CorrectionHistory,
  type CorrectionPreview,
  type DeleteResult,
  type DesignationFramesView,
  type DesignationRequest2,
  type DesignationView,
  type ObservationSetView,
  type InviteCreated,
  type InviteInfo,
  type InviteView,
  type MeView,
  type UserView,
  setCsrfToken,
  type JobView,
  type ReportRequest,
  type ReportView,
  type PlaybackView,
  type VideoSummary,
  type AggregateDetail,
  type EventDetail,
  type EvidenceView,
  type FindingView,
  type FramesView,
  type PoseView,
  type RunSummary,
  type TimelineView,
  type VideoInfo,
} from "./client";

export const useRuns = () =>
  useQuery({ queryKey: ["runs"], queryFn: () => apiGet<RunSummary[]>("/runs") });

export const useVideos = () =>
  useQuery({ queryKey: ["videos"], queryFn: () => apiGet<VideoSummary[]>("/videos") });

const POLL_MS = 1500;

/** 처리 중인 영상은 주기적으로 다시 읽는다. 끝나면 읽기를 멈춘다. */
export const useVideoSummary = (videoId: string) =>
  useQuery({
    queryKey: ["video-summary", videoId],
    queryFn: () => apiGet<VideoSummary>(`/videos/${videoId}/summary`),
    refetchInterval: (query) => (query.state.data && query.state.data.status !== "processing" ? false : POLL_MS),
  });

export const usePlayback = (videoId: string, enabled: boolean, refreshMs?: number) =>
  useQuery({
    queryKey: ["playback", videoId],
    queryFn: () => apiGet<PlaybackView>(`/videos/${videoId}/playback`),
    enabled,
    refetchInterval: refreshMs,
  });

export const useCancelJob = (videoId: string) => {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (jobId: string) => apiRequest<JobView>(`/jobs/${jobId}/cancel`, { method: "POST" }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["video-summary", videoId] });
      void client.invalidateQueries({ queryKey: ["videos"] });
    },
  });
};

export const useRun = (runId: string) =>
  useQuery({ queryKey: ["run", runId], queryFn: () => apiGet<RunSummary>(`/runs/${runId}`) });

export const useFindings = (runId: string) =>
  useQuery({
    queryKey: ["findings", runId],
    queryFn: () => apiGet<FindingView[]>(`/runs/${runId}/findings`),
  });

/** 영상 접근 주소는 짧은 시간 뒤 만료되므로 장면 복기 화면에서는 주기적으로 갱신한다. */
export const useEvidenceList = (runId: string, options?: { refreshMs?: number }) =>
  useQuery({
    queryKey: ["evidence", runId],
    queryFn: () => apiGet<EvidenceView[]>(`/runs/${runId}/evidence`),
    refetchInterval: options?.refreshMs,
  });

export const useVideoInfo = (videoId: string, enabled: boolean) =>
  useQuery({
    queryKey: ["video", videoId],
    queryFn: () => apiGet<VideoInfo>(`/videos/${videoId}`),
    enabled,
  });

export const useFrames = (videoId: string, fromUs: number | undefined, toUs: number | undefined, enabled: boolean) =>
  useQuery({
    queryKey: ["frames", videoId, fromUs, toUs],
    queryFn: () => apiGet<FramesView>(`/videos/${videoId}/frames`, { from_us: fromUs, to_us: toUs }),
    enabled: enabled && fromUs !== undefined && toUs !== undefined,
    staleTime: Infinity,
  });

export const usePoses = (runId: string, fromUs: number | undefined, toUs: number | undefined, enabled: boolean) =>
  useQuery({
    queryKey: ["poses", runId, fromUs, toUs],
    queryFn: () => apiGet<PoseView>(`/runs/${runId}/poses`, { from_us: fromUs, to_us: toUs }),
    enabled: enabled && fromUs !== undefined && toUs !== undefined,
  });

export const useAggregate = (runId: string, aggregateId: string) =>
  useQuery({
    queryKey: ["aggregate", runId, aggregateId],
    queryFn: () => apiGet<AggregateDetail>(`/runs/${runId}/aggregates/${aggregateId}`),
  });

export const useEvent = (runId: string, eventId: string) =>
  useQuery({
    queryKey: ["event", runId, eventId],
    queryFn: () => apiGet<EventDetail>(`/runs/${runId}/events/${eventId}`),
  });

export const useTimeline = (runId: string, fromUs: number | undefined, toUs: number | undefined) =>
  useQuery({
    queryKey: ["timeline", runId, fromUs, toUs],
    queryFn: () => apiGet<TimelineView>(`/runs/${runId}/timeline`, { from_us: fromUs, to_us: toUs }),
    enabled: fromUs !== undefined && toUs !== undefined,
  });

export const useDeleteVideo = (videoId: string) => {
  const client = useQueryClient();
  return useMutation<DeleteResult | VideoSummary, Error, "all" | "media">({
    mutationFn: (scope) =>
      scope === "all"
        ? apiRequest<DeleteResult>(`/videos/${videoId}`, { method: "DELETE" })
        : apiRequest<VideoSummary>(`/videos/${videoId}/media`, { method: "DELETE" }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["videos"] });
      void client.invalidateQueries({ queryKey: ["video-summary", videoId] });
      void client.invalidateQueries({ queryKey: ["runs"] });
      void client.invalidateQueries({ queryKey: ["evidence"] });
    },
  });
};

export const useCorrectionHistory = (runId: string) =>
  useQuery({ queryKey: ["corrections", runId], queryFn: () => apiGet<CorrectionHistory>(`/runs/${runId}/corrections`) });

export const useCorrectionPreview = (runId: string) =>
  useMutation({
    mutationFn: (userParticipantId: string) =>
      apiRequest<CorrectionPreview>(`/runs/${runId}/corrections/preview`, { method: "POST", json: { kind: "designation", user_participant_id: userParticipantId } }),
  });

export const useCreateCorrection = (runId: string) => {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (userParticipantId: string) =>
      apiRequest<RunSummary>(`/runs/${runId}/corrections`, { method: "POST", json: { kind: "designation", user_participant_id: userParticipantId } }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["runs"] });
      void client.invalidateQueries({ queryKey: ["run"] });
      void client.invalidateQueries({ queryKey: ["corrections"] });
    },
  });
};

export const useReports = (runId: string) =>
  useQuery({ queryKey: ["reports", runId], queryFn: () => apiGet<ReportView[]>(`/runs/${runId}/reports`) });

export const useCreateReport = (runId: string) => {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: ReportRequest) => apiRequest<ReportView>(`/runs/${runId}/reports`, { method: "POST", json: body }),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["reports", runId] }),
  });
};

export const useContextNotes = (runId: string) =>
  useQuery({ queryKey: ["notes", runId], queryFn: () => apiGet<ContextNoteView[]>(`/runs/${runId}/context-notes`) });

export const useCreateContextNote = (runId: string) => {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: ContextNoteRequest) => apiRequest<ContextNoteView>(`/runs/${runId}/context-notes`, { method: "POST", json: body }),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["notes", runId] }),
  });
};

export const useMe = () =>
  useQuery({
    queryKey: ["me"],
    queryFn: async () => {
      const me = await apiGet<MeView>("/me");
      setCsrfToken(me.csrf_token);
      return me;
    },
    retry: false,
    staleTime: 60_000,
  });

export const useLogin = () => {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: { email: string; password: string }) => apiRequest<MeView>("/auth/login", { method: "POST", json: body }),
    onSuccess: (me) => {
      setCsrfToken(me.csrf_token);
      client.clear();
      client.setQueryData(["me"], me);
    },
  });
};

export const useLogout = () => {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => apiRequest<void>("/auth/logout", { method: "POST" }),
    onSettled: () => {
      setCsrfToken(null);
      client.clear();
    },
  });
};

export const useInvite = (token: string) =>
  useQuery({ queryKey: ["invite", token], queryFn: () => apiGet<InviteInfo>(`/auth/invites/${token}`), retry: false });

export const useAcceptInvite = (token: string) =>
  useMutation({
    mutationFn: (body: { display_name: string; password: string; consent: boolean }) =>
      apiRequest<InviteInfo>(`/auth/invites/${token}/accept`, { method: "POST", json: body }),
  });

export const useInvites = () => useQuery({ queryKey: ["admin-invites"], queryFn: () => apiGet<InviteView[]>("/admin/invites") });
export const useUsers = () => useQuery({ queryKey: ["admin-users"], queryFn: () => apiGet<UserView[]>("/admin/users") });

export const useCreateInvite = () => {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: { email: string; role: "participant" | "admin" }) => apiRequest<InviteCreated>("/admin/invites", { method: "POST", json: body }),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["admin-invites"] }),
  });
};

export const useRevokeInvite = () => {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiRequest<void>(`/admin/invites/${id}`, { method: "DELETE" }),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["admin-invites"] }),
  });
};

export const useSetBlocked = () => {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, blocked }: { id: string; blocked: boolean }) =>
      apiRequest<void>(`/admin/users/${id}/${blocked ? "block" : "unblock"}`, { method: "POST" }),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["admin-users"] }),
  });
};

/** 가져온 검출·추적 결과. 아직 없으면(404) null이다. */
export const useObservations = (videoId: string) =>
  useQuery({
    queryKey: ["observations", videoId],
    queryFn: async () => {
      try {
        return await apiGet<ObservationSetView>(`/videos/${videoId}/observations`);
      } catch (error) {
        if (error instanceof ApiError && error.status === 404 && error.code === "observations_not_found") return null;
        throw error;
      }
    },
    retry: false,
  });

export const useDesignationFrames = (videoId: string, setId: string) =>
  useQuery({
    queryKey: ["designation-frames", videoId, setId],
    queryFn: () => apiGet<DesignationFramesView>(`/videos/${videoId}/designation-frames`),
    staleTime: Infinity,
  });

export const useDesignation = (videoId: string) =>
  useQuery({ queryKey: ["designation", videoId], queryFn: () => apiGet<DesignationView>(`/videos/${videoId}/designation`) });

export const useSaveDesignation = (videoId: string) => {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: DesignationRequest2) => apiRequest<DesignationView>(`/videos/${videoId}/designation`, { method: "PUT", json: body }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["designation", videoId] });
      void client.invalidateQueries({ queryKey: ["observations", videoId] });
    },
  });
};

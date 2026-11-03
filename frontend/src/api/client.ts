import type { components } from "./schema";

type Schemas = components["schemas"];

export type RunSummary = Schemas["RunSummary"];
export type FindingView = Schemas["FindingView"];
export type AggregateDetail = Schemas["AggregateDetail"];
export type Aggregate = Schemas["Aggregate"];
export type AggregateCounts = Schemas["AggregateCounts"];
export type MemberView = Schemas["MemberView"];
export type TimelineView = Schemas["TimelineView"];
export type EventDetail = Schemas["EventDetail"];
export type EvidenceView = Schemas["EvidenceView"];
export type Event = Schemas["Event"];
export type Assessment = Schemas["Assessment-Output"];
export type StateInterval = Schemas["StateInterval-Output"];
export type EventRelation = Schemas["EventRelation-Output"];
export type Participant = Schemas["Participant-Output"];
export type VideoInfo = Schemas["VideoInfoView"];
export type FramesView = Schemas["FramesView"];
export type PoseView = Schemas["PoseView"];
export type PoseSample = Schemas["PoseSample"];
export type Joint = Schemas["Joint"];
export type JobView = Schemas["JobView"];
export type JobStageView = Schemas["JobStageView"];
export type UploadView = Schemas["UploadView"];
export type UploadCreateRequest = Schemas["UploadCreateRequest"];
export type VideoSummary = Schemas["VideoSummary"];
export type PlaybackView = Schemas["PlaybackView"];
export type DeleteResult = Schemas["DeleteResult"];
export type DesignationFramesView = Schemas["DesignationFramesView"];
export type DesignationView = Schemas["DesignationView"];
export type DesignationRequest2 = Schemas["DesignationRequest2"];
export type ObservationSetView = Schemas["ObservationSetView"];
export type TrackSummaryView = Schemas["TrackSummaryView"];
export type MeView = Schemas["MeView"];
export type InviteInfo = Schemas["InviteInfo"];
export type InviteView = Schemas["InviteView"];
export type InviteCreated = Schemas["InviteCreated"];
export type UserView = Schemas["UserView"];
export type CorrectionPreview = Schemas["CorrectionPreview"];
export type CorrectionHistory = Schemas["CorrectionHistory"];
export type ReportView = Schemas["ReportView"];
export type ReportRequest = Schemas["ReportRequest"];
export type ContextNoteView = Schemas["ContextNoteView"];
export type ContextNoteRequest = Schemas["ContextNoteRequest"];

const BASE = "/api/v1";

/** RFC 9457 Problem Details 오류. 사용자에게는 제목과 문의용 trace_id만 보여준다. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly traceId: string | null;
  /** 입력 내용의 오류 목록(가져오기 검증 등). 사용자 입력에 대한 설명만 담긴다. */
  readonly details: string[];

  constructor(status: number, code: string, title: string, traceId: string | null, details: string[] = []) {
    super(title);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.traceId = traceId;
    this.details = details;
  }
}

function describeError(e: unknown): string {
  if (typeof e === "string") return e;
  if (e && typeof e === "object" && "msg" in e) {
    const { loc, msg } = e as { loc?: (string | number)[]; msg: string };
    return loc?.length ? `${loc.join(".")}: ${msg}` : msg;
  }
  return String(e);
}

// 세션 쿠키로 인증한 변경 요청에는 세션에 묶인 CSRF 토큰을 헤더로 보낸다(21).
let csrfToken: string | null = null;
export const setCsrfToken = (token: string | null) => {
  csrfToken = token;
};

// 로그인이 필요하다는 응답을 받으면 앱이 로그인 화면으로 보낸다.
let unauthenticatedHandler: (() => void) | null = null;
export const onUnauthenticated = (handler: (() => void) | null) => {
  unauthenticatedHandler = handler;
};

type Params = Record<string, string | number | undefined>;

export interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "DELETE";
  params?: Params;
  headers?: Record<string, string>;
  body?: BodyInit | null;
  json?: unknown;
  signal?: AbortSignal;
}

export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(options.params ?? {})) {
    if (value !== undefined) query.set(key, String(value));
  }
  const qs = query.size > 0 ? `?${query.toString()}` : "";

  const headers: Record<string, string> = { Accept: "application/json", ...options.headers };
  if (csrfToken && (options.method ?? "GET") !== "GET") headers["X-CSRF-Token"] = csrfToken;
  let body = options.body ?? null;
  if (options.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.json);
  }

  let response: Response;
  try {
    response = await fetch(`${BASE}${path}${qs}`, { method: options.method ?? "GET", headers, body, signal: options.signal });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError(0, "network_error", "서버에 연결할 수 없습니다", null);
  }

  if (!response.ok) {
    let body: { code?: string; title?: string; trace_id?: string; errors?: unknown[] } = {};
    try {
      body = await response.json();
    } catch {
      /* 본문이 없거나 JSON이 아니면 기본 문구를 쓴다 */
    }
    if (response.status === 401 && unauthenticatedHandler) unauthenticatedHandler();
    throw new ApiError(
      response.status,
      body.code ?? "http_error",
      body.title ?? `요청을 처리하지 못했습니다 (${response.status})`,
      body.trace_id ?? null,
      (body.errors ?? []).map(describeError),
    );
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export function apiGet<T>(path: string, params?: Params): Promise<T> {
  return apiRequest<T>(path, { params });
}

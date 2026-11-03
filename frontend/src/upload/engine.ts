import { ApiError, apiRequest, type UploadCreateRequest, type UploadView } from "../api/client";

/** 분할 업로드. 조각마다 SHA-256을 보내고, 중단되면 같은 파일을 다시 선택했을 때 받지 못한 조각부터 이어 올린다. */

export type Sport = UploadCreateRequest["sport"];

export interface UploadProgress {
  sentBytes: number;
  totalBytes: number;
  partsDone: number;
  partCount: number;
}

export interface KeyValueStore {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

export interface UploadOptions {
  onProgress?: (progress: UploadProgress) => void;
  /** 서버의 업로드 번호를 알게 되면 부른다. 취소할 때 임시 조각을 지우는 데 쓴다. */
  onUpload?: (upload: UploadView) => void;
  signal?: AbortSignal;
  /** 이어 올리기 정보를 저장할 곳. 기본값은 localStorage이며, 쓸 수 없으면 이어 올리기만 하지 못한다. */
  storage?: KeyValueStore | null;
  maxPartAttempts?: number;
  sleep?: (ms: number) => Promise<void>;
}

interface Saved {
  uploadId: string | null;
  key: string;
}

const RETRY_BASE_MS = 500;

export async function sha256Hex(data: ArrayBuffer): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", data);
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
}

function defaultStorage(): KeyValueStore | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

const fingerprint = (file: File, sport: Sport) => `upload:v1:${file.name}:${file.size}:${file.lastModified}:${sport}`;

function readSaved(store: KeyValueStore | null, id: string): Saved | null {
  try {
    const raw = store?.getItem(id);
    return raw ? (JSON.parse(raw) as Saved) : null;
  } catch {
    return null;
  }
}

function write(store: KeyValueStore | null, id: string, value: Saved | null) {
  try {
    if (value === null) store?.removeItem(id);
    else store?.setItem(id, JSON.stringify(value));
  } catch {
    /* 저장하지 못해도 업로드는 계속한다 */
  }
}

/** 다시 시도해 볼 만한 오류: 연결 실패와 서버 오류. 요청 자체가 잘못된 경우(4xx)는 다시 보내도 같다. */
export function isRetryable(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 0 || error.status >= 500);
}

async function withRetry<T>(run: () => Promise<T>, attempts: number, sleep: (ms: number) => Promise<void>, signal?: AbortSignal): Promise<T> {
  for (let attempt = 1; ; attempt += 1) {
    try {
      return await run();
    } catch (error) {
      if (signal?.aborted || !isRetryable(error) || attempt >= attempts) throw error;
      await sleep(RETRY_BASE_MS * 2 ** (attempt - 1));
    }
  }
}

export async function uploadFile(file: File, sport: Sport, options: UploadOptions = {}): Promise<UploadView> {
  const store = options.storage === undefined ? defaultStorage() : options.storage;
  const sleep = options.sleep ?? ((ms: number) => new Promise<void>((r) => setTimeout(r, ms)));
  const attempts = options.maxPartAttempts ?? 4;
  const { signal } = options;
  const id = fingerprint(file, sport);

  const saved = readSaved(store, id) ?? { uploadId: null, key: crypto.randomUUID() };
  write(store, id, saved);

  let upload: UploadView | null = null;
  if (saved.uploadId) {
    try {
      const existing = await apiRequest<UploadView>(`/uploads/${saved.uploadId}`, { signal });
      if (existing.status === "receiving" || existing.status === "completed") upload = existing;
    } catch (error) {
      if (!(error instanceof ApiError) || error.status !== 404) throw error;
    }
    if (!upload) {
      // 서버가 이 업로드를 더 이상 갖고 있지 않으면 새로 시작한다.
      saved.uploadId = null;
      saved.key = crypto.randomUUID();
      write(store, id, saved);
    }
  }

  if (!upload) {
    const body: UploadCreateRequest = { filename: file.name, size_bytes: file.size, sport };
    upload = await withRetry(
      () => apiRequest<UploadView>("/uploads", { method: "POST", json: body, headers: { "Idempotency-Key": saved.key }, signal }),
      attempts,
      sleep,
      signal,
    );
    saved.uploadId = upload.upload_id;
    write(store, id, saved);
  }

  options.onUpload?.(upload);
  if (upload.status === "completed") {
    write(store, id, null);
    return upload;
  }

  const received = new Set(upload.received_parts);
  let sent = 0;
  for (const n of received) sent += partLength(file.size, upload.part_size, n);
  const report = () =>
    options.onProgress?.({ sentBytes: sent, totalBytes: file.size, partsDone: received.size, partCount: upload.part_count });
  report();

  for (let n = 0; n < upload.part_count; n += 1) {
    if (received.has(n)) continue;
    if (signal?.aborted) throw new DOMException("업로드를 중단했습니다", "AbortError");
    const start = n * upload.part_size;
    const buffer = await file.slice(start, start + upload.part_size).arrayBuffer();
    const digest = await sha256Hex(buffer);
    await withRetry(
      () =>
        apiRequest(`/uploads/${upload.upload_id}/parts/${n}`, {
          method: "PUT",
          body: buffer,
          headers: { "Content-Type": "application/octet-stream", "X-Part-Sha256": digest },
          signal,
        }),
      attempts,
      sleep,
      signal,
    );
    received.add(n);
    sent += buffer.byteLength;
    report();
  }

  const done = await withRetry(
    () => apiRequest<UploadView>(`/uploads/${upload.upload_id}/complete`, { method: "POST", signal }),
    attempts,
    sleep,
    signal,
  );
  write(store, id, null);
  return done;
}

function partLength(total: number, partSize: number, n: number): number {
  return Math.max(0, Math.min(partSize, total - n * partSize));
}

/** 사용자가 업로드를 취소하면 서버의 임시 조각을 지우고 이어 올리기 정보도 지운다. */
export async function abortUpload(file: File, sport: Sport, uploadId: string | null, store: KeyValueStore | null = defaultStorage()) {
  write(store, fingerprint(file, sport), null);
  if (uploadId) await apiRequest(`/uploads/${uploadId}`, { method: "DELETE" });
}

// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";

import { sha256Hex, uploadFile, type KeyValueStore } from "../src/upload/engine";

class MemoryStore implements KeyValueStore {
  data = new Map<string, string>();
  getItem = (k: string) => this.data.get(k) ?? null;
  setItem = (k: string, v: string) => void this.data.set(k, v);
  removeItem = (k: string) => void this.data.delete(k);
}

interface Call {
  method: string;
  path: string;
  headers: Record<string, string>;
  body: unknown;
}

const PART = 4;
const makeFile = () => new File([new Uint8Array([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])], "clip.mp4", { lastModified: 1 });

function upload(overrides: Record<string, unknown> = {}) {
  return {
    upload_id: "up1", filename: "clip.mp4", size_bytes: 10, sport: "boxing", part_size: PART, part_count: 3,
    received_parts: [], status: "receiving", job_id: null, video_id: null, ...overrides,
  };
}

function stubServer(handler: (call: Call) => { status: number; body?: unknown }) {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string, init: RequestInit) => {
      const call: Call = {
        method: init.method ?? "GET",
        path: input.replace("/api/v1", ""),
        headers: init.headers as Record<string, string>,
        body: init.body,
      };
      calls.push(call);
      const { status, body } = handler(call);
      if (status === 204) return new Response(null, { status });
      return new Response(JSON.stringify(body ?? {}), {
        status,
        headers: { "content-type": status >= 400 ? "application/problem+json" : "application/json" },
      });
    }),
  );
  return calls;
}

afterEach(() => vi.unstubAllGlobals());

describe("분할 업로드", () => {
  it("조각마다 SHA-256을 보내고 마지막에 완료한다", async () => {
    const file = makeFile();
    const calls = stubServer((c) => {
      if (c.path === "/uploads") return { status: 201, body: upload() };
      if (c.path.endsWith("/complete")) return { status: 200, body: upload({ status: "completed", video_id: "v1", job_id: "j1" }) };
      return { status: 200, body: upload() };
    });
    const progress: number[] = [];
    const done = await uploadFile(file, "boxing", { storage: new MemoryStore(), onProgress: (p) => progress.push(p.sentBytes) });

    expect(done.video_id).toBe("v1");
    const parts = calls.filter((c) => c.method === "PUT");
    expect(parts.map((c) => c.path)).toEqual(["/uploads/up1/parts/0", "/uploads/up1/parts/1", "/uploads/up1/parts/2"]);
    const bytes = new Uint8Array(await file.arrayBuffer());
    for (const [n, call] of parts.entries()) {
      const slice = bytes.slice(n * PART, (n + 1) * PART);
      expect(call.headers["X-Part-Sha256"]).toBe(await sha256Hex(slice.buffer));
    }
    expect(progress).toEqual([0, 4, 8, 10]);
    expect(calls.at(-1)?.path).toBe("/uploads/up1/complete");
    expect(calls[0]?.headers["Idempotency-Key"]).toMatch(/[0-9a-f-]{36}/);
  });

  it("끊긴 업로드는 받은 조각을 건너뛰고 이어 올린다", async () => {
    const file = makeFile();
    const store = new MemoryStore();
    let failAt: number | null = 1;
    stubServer((c) => {
      if (c.path === "/uploads") return { status: 201, body: upload() };
      if (c.method === "PUT" && c.path.endsWith(`/parts/${failAt}`)) return { status: 400, body: { code: "x", title: "중단" } };
      if (c.path === "/uploads/up1" && c.method === "GET") return { status: 200, body: upload({ received_parts: [0] }) };
      if (c.path.endsWith("/complete")) return { status: 200, body: upload({ status: "completed", video_id: "v1", job_id: "j1" }) };
      return { status: 200, body: upload() };
    });
    await expect(uploadFile(file, "boxing", { storage: store })).rejects.toMatchObject({ status: 400 });
    expect(store.data.size).toBe(1); // 이어 올리기 정보가 남는다

    failAt = null;
    const calls = stubServer((c) => {
      if (c.method === "GET") return { status: 200, body: upload({ received_parts: [0] }) };
      if (c.path.endsWith("/complete")) return { status: 200, body: upload({ status: "completed", video_id: "v1", job_id: "j1" }) };
      return { status: 200, body: upload() };
    });
    const done = await uploadFile(file, "boxing", { storage: store });
    expect(done.video_id).toBe("v1");
    expect(calls.filter((c) => c.method === "PUT").map((c) => c.path)).toEqual(["/uploads/up1/parts/1", "/uploads/up1/parts/2"]);
    expect(calls.some((c) => c.method === "POST" && c.path === "/uploads")).toBe(false); // 새 업로드를 만들지 않는다
    expect(store.data.size).toBe(0);
  });

  it("서버가 업로드를 잊었으면 새 키로 처음부터 올린다", async () => {
    const file = makeFile();
    const store = new MemoryStore();
    stubServer((c) => (c.path === "/uploads" ? { status: 201, body: upload({ part_count: 1, part_size: 100 }) } : { status: 400, body: { code: "x", title: "중단" } }));
    await expect(uploadFile(file, "boxing", { storage: store })).rejects.toBeDefined();
    const firstKey = JSON.parse([...store.data.values()][0] ?? "{}").key;

    const calls = stubServer((c) => {
      if (c.method === "GET") return { status: 404, body: { code: "not_found", title: "없음" } };
      if (c.path === "/uploads") return { status: 201, body: upload({ upload_id: "up2", part_count: 1, part_size: 100 }) };
      if (c.path.endsWith("/complete")) return { status: 200, body: upload({ upload_id: "up2", status: "completed", video_id: "v2" }) };
      return { status: 200, body: {} };
    });
    const done = await uploadFile(file, "boxing", { storage: store });
    expect(done.video_id).toBe("v2");
    expect(calls.find((c) => c.path === "/uploads")?.headers["Idempotency-Key"]).not.toBe(firstKey);
  });

  it("연결 오류와 서버 오류는 다시 시도하고, 요청 오류는 다시 시도하지 않는다", async () => {
    let puts = 0;
    stubServer((c) => {
      if (c.path === "/uploads") return { status: 201, body: upload({ part_count: 1, part_size: 100 }) };
      if (c.method === "PUT") {
        puts += 1;
        return puts < 3 ? { status: 503, body: { code: "busy", title: "잠시 후" } } : { status: 200, body: {} };
      }
      return { status: 200, body: upload({ status: "completed", video_id: "v1" }) };
    });
    const sleeps: number[] = [];
    await uploadFile(makeFile(), "boxing", { storage: null, sleep: async (ms) => void sleeps.push(ms) });
    expect(puts).toBe(3);
    expect(sleeps).toEqual([500, 1000]);

    puts = 0;
    stubServer((c) => (c.path === "/uploads" ? { status: 201, body: upload({ part_count: 1, part_size: 100 }) } : (puts += 1, { status: 422, body: { code: "checksum_mismatch", title: "손상" } })));
    await expect(uploadFile(makeFile(), "boxing", { storage: null, sleep: async () => undefined })).rejects.toMatchObject({ code: "checksum_mismatch" });
    expect(puts).toBe(1);
  });

  it("중단 신호를 받으면 다음 조각을 보내지 않는다", async () => {
    const controller = new AbortController();
    const calls = stubServer((c) => (c.path === "/uploads" ? { status: 201, body: upload() } : { status: 200, body: {} }));
    await expect(
      uploadFile(makeFile(), "boxing", { storage: null, signal: controller.signal, onProgress: (p) => p.partsDone === 1 && controller.abort() }),
    ).rejects.toMatchObject({ name: "AbortError" });
    expect(calls.filter((c) => c.method === "PUT")).toHaveLength(1);
  });
});

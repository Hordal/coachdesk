import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { CountBreakdown } from "../src/components/CountBreakdown";
import { DataValue } from "../src/components/DataValue";
import { ExpiredNotice } from "../src/components/EvidenceLink";
import { FindingCard } from "../src/components/FindingCard";
import { AssessmentBadge } from "../src/components/StatusBadge";
import { TimelineTrack, buildRows, place } from "../src/components/TimelineTrack";
import { contact, counts, evidenceList, guardState, kick, participants, validFinding, withheldCorrection, withheldDistance } from "./fixtures";

const wrap = (ui: React.ReactNode) => render(<MemoryRouter>{ui}</MemoryRouter>);
const evidenceMap = new Map(evidenceList.map((v) => [v.evidence.evidence_id, v]));

describe("DataValue", () => {
  it("값이 없으면 0이 아니라 ‘확인 불가’와 사유를 보여준다", () => {
    render(<DataValue value={null} reason="missing_input" />);
    expect(screen.getByText(/확인 불가/)).toBeInTheDocument();
    expect(screen.getByText(/필요한 입력 없음/)).toBeInTheDocument();
    expect(screen.queryByText("0")).not.toBeInTheDocument();
  });

  it("실제 0은 0으로 보여준다", () => {
    render(<DataValue value={0} unit="회" />);
    expect(screen.getByText("0")).toBeInTheDocument();
  });
});

describe("AssessmentBadge", () => {
  it("색 없이도 구별되도록 텍스트를 보여준다", () => {
    const { container } = render(
      <>
        <AssessmentBadge value="present" />
        <AssessmentBadge value="absent" />
        <AssessmentBadge value="unknown" />
        <AssessmentBadge value="not_applicable" />
      </>,
    );
    for (const text of ["있음", "없음", "판단 불가", "해당 없음"]) {
      expect(within(container).getByText(text)).toBeInTheDocument();
    }
  });
});

describe("CountBreakdown", () => {
  it("분모·분자·판단 불가·제외를 함께 보여준다", () => {
    render(<CountBreakdown counts={counts()} ratio={0.75} />);
    expect(screen.getByText("3 / 4")).toBeInTheDocument();
    expect(screen.getByText("확인된 전체").closest("div")).toHaveTextContent("5");
    expect(screen.getByText("판정 가능").closest("div")).toHaveTextContent("4");
    expect(screen.getByText("해당 결과 발생").closest("div")).toHaveTextContent("3");
    expect(screen.getByText("판단 불가").closest("div")).toHaveTextContent("1");
    expect(screen.getByText("제외").closest("div")).toHaveTextContent("0");
  });

  it("분모가 0이면 비율을 만들지 않는다", () => {
    render(<CountBreakdown counts={counts({ candidates: 6, denominator: 0, numerator: 0, unknown: 6 })} ratio={null} />);
    expect(screen.getByText("비율 없음")).toBeInTheDocument();
    expect(screen.queryByText(/0 \/ 0/)).not.toBeInTheDocument();
  });

  it("퍼센트 같은 검증되지 않은 정밀도를 만들지 않는다", () => {
    const { container } = render(<CountBreakdown counts={counts({ candidates: 6, denominator: 6, numerator: 4, unknown: 0 })} ratio={4 / 6} />);
    expect(container.textContent).not.toMatch(/%/);
    expect(container.textContent).not.toMatch(/66\.\d/);
  });
});

describe("FindingCard", () => {
  it("제공한 주장은 서버가 만든 문장을 그대로 표시한다", () => {
    wrap(<FindingCard runId="r1" finding={validFinding} evidence={evidenceMap} />);
    expect(screen.getByText(validFinding.text!)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "AG01" })).toHaveAttribute("href", "/runs/r1/aggregates/AG01");
    expect(screen.getByRole("list", { name: "근거 장면" }).querySelectorAll("li")).toHaveLength(2);
  });

  it("영상이 없는 근거는 카드마다 배지를 반복하지 않고 한 번만 안내한다", () => {
    wrap(<FindingCard runId="r1" finding={validFinding} evidence={evidenceMap} />);
    expect(screen.queryAllByText("영상 없음")).toHaveLength(0);
    expect(screen.getByText("근거 장면은 모두 원본 영상이 없어 재생할 수 없습니다.")).toBeInTheDocument();
  });

  it("보류된 교정 주장은 숨기지 않고 사유를 보여준다", () => {
    wrap(<FindingCard runId="r1" finding={withheldCorrection} evidence={evidenceMap} />);
    expect(screen.getByText("교정 방향")).toBeInTheDocument();
    expect(screen.getByText("제공하지 않음")).toBeInTheDocument();
    expect(screen.getByText(/검토된 교정 규칙이 없어 제공하지 않았습니다/)).toBeInTheDocument();
  });

  it("관련 판정만 있는 주장은 집계 링크 없이 건수를 보여준다", () => {
    wrap(<FindingCard runId="r1" finding={withheldDistance} evidence={evidenceMap} />);
    expect(screen.getByText(/실제 거리 측정값이 없어/)).toBeInTheDocument();
    expect(screen.getByText(/관련 판정 2건/)).toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });
});

describe("ExpiredNotice", () => {
  it("영상이 없어도 구조화된 근거는 확인할 수 있음을 안내한다", () => {
    render(<ExpiredNotice reasonCode="evidence_expired" />);
    expect(screen.getByRole("note")).toHaveTextContent("구간과 선수 정보는 확인할 수 있습니다");
    expect(screen.getByRole("note")).toHaveTextContent("보관 기간이 지나");
  });
});

describe("TimelineTrack", () => {
  it("선수별 상태·행동 행과 접촉 행을 만든다", () => {
    const rows = buildRows("r1", participants, [guardState], [kick, contact]);
    expect(rows.map((r) => r.label)).toEqual(["A(사용자) · 뒷손 가드", "A(사용자) · 행동", "접촉"]);
    expect(rows[2]?.items[0]?.text).toBe("B(상대)→A(사용자)");
  });

  it("팀 검출 모델의 후보 사건은 선수 지정 전 후보 선수의 행동 행에 들어간다", () => {
    const candidate = { ...kick, event_id: "c1", event_label: "PUNCH_CANDIDATE", status: "CANDIDATE" as const, participant_id: "candidate:0", target_participant_id: null };
    const rows = buildRows("r1", [{ participant_id: "candidate:0", role: "UNRESOLVED", track_ids: [0], user_designated: false, body_profile: null }], [], [candidate]);
    expect(rows.map((r) => r.label)).toEqual(["candidate:0(미지정) · 행동"]);
    expect(rows[0]?.items[0]?.text).toBe("펀치 후보");
  });

  it("값이 없는 구간은 항목을 만들지 않는다(공백으로 남는다)", () => {
    const rows = buildRows("r1", participants, [], []);
    expect(rows).toEqual([]);
  });

  it("구간 밖은 잘라내고 위치를 백분율로 계산한다", () => {
    const p = place({ startUs: 5_000_000, endUs: 15_000_000 }, 10_000_000, 20_000_000);
    expect(p).toEqual({ left: 0, width: 50 });
  });

  it("기록이 없는 구간은 ‘없음’이 아니라 ‘기록 없음’으로 안내한다", () => {
    wrap(<TimelineTrack runId="r1" fromUs={0} toUs={10_000_000} participants={participants} states={[]} events={[]} />);
    expect(screen.getByRole("status")).toHaveTextContent("기록된 상태나 사건이 없습니다");
    expect(screen.getByRole("status")).toHaveTextContent("기록된 내용이 없다는 뜻");
  });

  it("사건은 상세 화면 링크이고 목록 표에도 같은 내용이 있다", () => {
    wrap(<TimelineTrack runId="r1" fromUs={9_000_000} toUs={14_000_000} participants={participants} states={[guardState]} events={[kick, contact]} />);
    const links = screen.getAllByRole("link", { name: /발차기 시도/ });
    expect(links.some((l) => l.getAttribute("href") === "/runs/r1/events/E01")).toBe(true);
    const table = screen.getByRole("table");
    expect(within(table).getByText("00:10.00–00:10.70")).toBeInTheDocument();
    expect(within(table).getByText("하이가드")).toBeInTheDocument();
  });
});

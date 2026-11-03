import { Link } from "react-router-dom";

import type { Event, Participant, StateInterval } from "../api/client";
import { eventCategory } from "../lib/events";
import { formatRange, formatTime } from "../lib/format";
import { BODY_PART, EVENT_TYPE, label, ROLE, STATE_CATEGORY, STATE_LABEL, T } from "../strings/ko";

export interface TrackItem {
  id: string;
  kind: "state" | "action" | "contact";
  startUs: number;
  endUs: number;
  text: string;
  href?: string;
}

export interface TrackRow {
  key: string;
  label: string;
  items: TrackItem[];
}

export function participantName(id: string | null | undefined, participants: Participant[]): string {
  if (!id) return "";
  const p = participants.find((x) => x.participant_id === id);
  return p ? `${id}(${label(ROLE, p.role)})` : id;
}

/** 상태 구간과 사건을 선수별 행으로 묶는다. 값이 없는 구간은 항목을 만들지 않는다(공백으로 남는다). */
export function buildRows(
  runId: string,
  participants: Participant[],
  states: StateInterval[],
  events: Event[],
): TrackRow[] {
  const rows: TrackRow[] = [];

  for (const p of participants) {
    const own = states.filter((s) => s.participant_id === p.participant_id);
    const groups = new Map<string, StateInterval[]>();
    for (const s of own) {
      const key = `${s.body_part ?? ""}|${s.state_category}`;
      groups.set(key, [...(groups.get(key) ?? []), s]);
    }
    for (const [key, list] of [...groups.entries()].sort(([a], [b]) => a.localeCompare(b))) {
      const first = list[0];
      if (!first) continue;
      const part = first.body_part ? `${label(BODY_PART, first.body_part)} ` : "";
      rows.push({
        key: `state-${p.participant_id}-${key}`,
        label: `${participantName(p.participant_id, participants)} · ${part}${label(STATE_CATEGORY, first.state_category)}`,
        items: list.map((s) => ({
          id: s.state_id,
          kind: "state" as const,
          startUs: s.start_us,
          endUs: s.end_us,
          text: label(STATE_LABEL, s.label),
        })),
      });
    }

    const actions = events.filter((e) => eventCategory(e) === "action" && e.participant_id === p.participant_id);
    if (actions.length > 0) {
      rows.push({
        key: `action-${p.participant_id}`,
        label: `${participantName(p.participant_id, participants)} · 행동`,
        items: actions.map((e) => ({
          id: e.event_id,
          kind: "action" as const,
          startUs: e.start_timestamp_us,
          endUs: e.end_timestamp_us,
          text: label(EVENT_TYPE, e.event_label),
          href: `/runs/${runId}/events/${e.event_id}`,
        })),
      });
    }
  }

  const contacts = events.filter((e) => eventCategory(e) === "contact");
  if (contacts.length > 0) {
    rows.push({
      key: "contact",
      label: "접촉",
      items: contacts.map((e) => ({
        id: e.event_id,
        kind: "contact" as const,
        startUs: e.start_timestamp_us,
        endUs: e.end_timestamp_us,
        text: `${participantName(e.participant_id, participants)}→${participantName(e.target_participant_id, participants)}`,
        href: `/runs/${runId}/events/${e.event_id}`,
      })),
    });
  }
  return rows;
}

/** 구간 [fromUs, toUs) 안의 위치를 백분율로 바꾼다. 범위 밖은 잘라낸다. */
export function place(item: Pick<TrackItem, "startUs" | "endUs">, fromUs: number, toUs: number) {
  const span = toUs - fromUs;
  const start = Math.max(item.startUs, fromUs);
  const end = Math.min(item.endUs, toUs);
  return { left: ((start - fromUs) / span) * 100, width: (Math.max(end - start, 0) / span) * 100 };
}

interface Props {
  runId: string;
  fromUs: number;
  toUs: number;
  participants: Participant[];
  states: StateInterval[];
  events: Event[];
  /** 영상의 현재 표시 프레임 시각. 있으면 재생 위치 표시선을 그린다 */
  playheadUs?: number | null;
  /** 있으면 항목을 누를 때 이 시각으로 이동한다(영상 재생 화면). 없으면 사건 상세로 이동한다 */
  onSelect?: (startUs: number) => void;
}

export function TimelineTrack({ runId, fromUs, toUs, participants, states, events, playheadUs, onSelect }: Props) {
  const rows = buildRows(runId, participants, states, events);
  const allItems = rows.flatMap((r) => r.items.map((i) => ({ ...i, row: r.label })));
  allItems.sort((a, b) => a.startUs - b.startUs || a.id.localeCompare(b.id));

  if (rows.length === 0) {
    return (
      <div className="notice notice--muted" role="status">
        <p>이 구간에는 기록된 상태나 사건이 없습니다.</p>
        <p className="muted">{T.gapNotice}</p>
      </div>
    );
  }

  const mid = fromUs + Math.floor((toUs - fromUs) / 2);
  return (
    <div className="timeline">
      <div className="timeline__axis" aria-hidden="true">
        <span>{formatTime(fromUs)}</span>
        <span>{formatTime(mid)}</span>
        <span>{formatTime(toUs)}</span>
      </div>

      <div className="timeline__tracks">
        {playheadUs != null && playheadUs >= fromUs && playheadUs <= toUs ? (
          <div
            className="timeline__playhead"
            style={{ left: `${((playheadUs - fromUs) / (toUs - fromUs)) * 100}%` }}
            aria-hidden="true"
            data-testid="playhead"
          />
        ) : null}
        {rows.map((row) => (
          <div className="track" key={row.key}>
            <div className="track__label">{row.label}</div>
            <div className="track__lane">
              {row.items.map((item) => {
                const { left, width } = place(item, fromUs, toUs);
                const style = { left: `${left}%`, width: `${width}%` };
                const title = `${item.text} ${formatRange(item.startUs, item.endUs)}`;
                const className = `track__item track__item--${item.kind}`;
                const text = <span className="track__text">{item.text}</span>;
                if (onSelect) {
                  return (
                    <button
                      key={item.id}
                      type="button"
                      className={className}
                      style={style}
                      title={`${title} · 이 시각으로 이동`}
                      aria-label={`${item.text}, ${formatRange(item.startUs, item.endUs)}, 이 시각으로 이동`}
                      onClick={() => onSelect(item.startUs)}
                    >
                      {text}
                    </button>
                  );
                }
                return item.href ? (
                  <Link key={item.id} to={item.href} className={className} style={style} title={title}>
                    {text}
                  </Link>
                ) : (
                  <span key={item.id} className={className} style={style} title={title}>
                    {text}
                  </span>
                );
              })}
            </div>
          </div>
        ))}
      </div>

      <p className="muted timeline__gap">{T.gapNotice}</p>

      <details className="timeline__list" open>
        <summary>목록으로 보기</summary>
        <table>
          <thead>
            <tr>
              <th scope="col">시각</th>
              <th scope="col">구분</th>
              <th scope="col">내용</th>
            </tr>
          </thead>
          <tbody>
            {allItems.map((item) => (
              <tr key={item.id}>
                <td>{formatRange(item.startUs, item.endUs)}</td>
                <td>{item.row}</td>
                <td>{item.href ? <Link to={item.href}>{item.text}</Link> : item.text}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </div>
  );
}

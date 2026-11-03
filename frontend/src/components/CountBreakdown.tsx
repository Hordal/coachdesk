import type { AggregateCounts } from "../api/client";
import { formatFraction } from "../lib/format";
import { T } from "../strings/ko";

interface Props {
  counts: AggregateCounts;
  ratio: number | null | undefined;
}

/**
 * 집계의 구성. 확인된 전체 → 판정 가능 → 해당 결과 발생, 그리고 판단 불가·제외를 함께 보여준다.
 * 분모가 0이면 비율을 만들지 않고 ‘비율 없음’으로 표시한다(14, 19).
 */
export function CountBreakdown({ counts, ratio }: Props) {
  const fraction = formatFraction(counts.numerator, counts.denominator);
  const total = Math.max(counts.candidates, 1);
  const notOccurred = counts.denominator - counts.numerator;
  const segments = [
    { key: "numerator", n: counts.numerator, className: "seg seg--numerator" },
    { key: "notOccurred", n: notOccurred, className: "seg seg--absent" },
    { key: "unknown", n: counts.unknown, className: "seg seg--unknown" },
    { key: "excluded", n: counts.excluded, className: "seg seg--excluded" },
  ];

  return (
    <section className="count-breakdown" aria-label="집계 구성">
      <p className="count-breakdown__ratio">
        {fraction === null || ratio == null ? (
          <>
            <strong>{T.noRatio}</strong>
            <span className="muted"> 판정 가능한 사례가 없습니다.</span>
          </>
        ) : (
          <>
            <strong>{fraction}</strong>
            <span className="muted"> 판정 가능한 {counts.denominator}건 중 {counts.numerator}건</span>
          </>
        )}
      </p>

      <div className="stackbar" aria-hidden="true">
        {segments.map((s) =>
          s.n > 0 ? <span key={s.key} className={s.className} style={{ width: `${(s.n / total) * 100}%` }} /> : null,
        )}
      </div>

      <dl className="count-list">
        <div>
          <dt>{T.countLabels.candidates}</dt>
          <dd>{counts.candidates}</dd>
        </div>
        <div>
          <dt>{T.countLabels.denominator}</dt>
          <dd>{counts.denominator}</dd>
        </div>
        <div>
          <dt>
            <span aria-hidden="true" className="legend legend--numerator" />
            {T.countLabels.numerator}
          </dt>
          <dd>{counts.numerator}</dd>
        </div>
        <div>
          <dt>
            <span aria-hidden="true" className="legend legend--absent" />
            판정 가능, 발생하지 않음
          </dt>
          <dd>{notOccurred}</dd>
        </div>
        <div>
          <dt>
            <span aria-hidden="true" className="legend legend--unknown" />
            {T.countLabels.unknown}
          </dt>
          <dd>{counts.unknown}</dd>
        </div>
        <div>
          <dt>
            <span aria-hidden="true" className="legend legend--excluded" />
            {T.countLabels.excluded}
          </dt>
          <dd>{counts.excluded}</dd>
        </div>
      </dl>
    </section>
  );
}

import { label, REASON, T } from "../strings/ko";

interface Props {
  value: number | string | null | undefined;
  unit?: string;
  /** 값이 없을 때 보여줄 사유 코드 */
  reason?: string | null;
}

/**
 * 값 표시. 값이 없으면(null) ‘확인 불가’와 사유를 보여주고 0으로 바꾸지 않는다(19).
 * 0은 실제 0일 때만 나온다.
 */
export function DataValue({ value, unit, reason }: Props) {
  if (value === null || value === undefined) {
    return (
      <span className="data-missing">
        <span aria-hidden="true">—</span> {T.missing}
        {reason ? <span className="data-missing__reason"> ({label(REASON, reason)})</span> : null}
      </span>
    );
  }
  return (
    <span className="data-value">
      {value}
      {unit ? <span className="data-value__unit"> {unit}</span> : null}
    </span>
  );
}

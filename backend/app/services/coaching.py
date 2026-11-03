"""화면 좌표로 설명 가능한 관찰과 확인 과제를 만든다. 기술·명중 판정은 하지 않는다."""
import math


def coaching_draft(view, participants, duration_us):
    user_ids = {p.participant_id for p in participants if str(p.role) == "USER"}
    rows = sorted((s for s in view.samples if s.participant_id in user_ids), key=lambda s: s.time_us)
    cards = []
    for side, name in (("left", "왼손"), ("right", "오른손")):
        observations = {}
        for sample in rows:
            joints = {j.name: j for j in sample.joints}
            needed = ["left_shoulder", "right_shoulder", "left_hip", "right_hip", f"{side}_wrist"]
            if any(k not in joints or joints[k].state != "observed" or joints[k].score is None or joints[k].score < .65
                   or joints[k].x is None or joints[k].y is None
                   or not math.isfinite(joints[k].x) or not math.isfinite(joints[k].y) for k in needed):
                continue
            shoulder = joints[f"{side}_shoulder"]
            sx = (joints["left_shoulder"].x + joints["right_shoulder"].x) / 2
            sy = (joints["left_shoulder"].y + joints["right_shoulder"].y) / 2
            hx = (joints["left_hip"].x + joints["right_hip"].x) / 2
            hy = (joints["left_hip"].y + joints["right_hip"].y) / 2
            torso = math.hypot(sx-hx, sy-hy)
            if torso < max(10, (view.height or 0)*.06):
                continue
            observations[sample.time_us] = (joints[f"{side}_wrist"].y-shoulder.y)/torso > .20
        times = sorted(observations)
        if len(times) < 12 or times[-1]-times[0] < 500_000:
            continue
        # 가림을 건너뛰어 지속된 자세로 해석하지 않는다.
        stretches, current = [], []
        for t in times:
            if observations[t]:
                if current and t-current[-1] > 150_000:
                    stretches.append(current)
                    current = []
                current.append(t)
            elif current:
                stretches.append(current)
                current = []
        if current:
            stretches.append(current)
        sustained = [r for r in stretches if len(r) >= 3 and r[-1]-r[0] >= 120_000]
        chosen = max(sustained, key=lambda r:r[-1]-r[0]) if sustained else None
        ratio = round(100*sum(observations.values())/len(times))
        at = chosen[0] if chosen else times[len(times)//2]
        sample = next(s for s in rows if s.time_us == at)
        points = [j for j in sample.joints if j.state == "observed" and j.x is not None and j.y is not None and j.score is not None and j.score >= .65]
        box = None
        if points and view.width and view.height:
            left,top=max(0,min(j.x for j in points)-15),max(0,min(j.y for j in points)-15)
            right,bottom=min(view.width,max(j.x for j in points)+15),min(view.height,max(j.y for j in points)+15)
            box = {"left":100*left/view.width,"top":100*top/view.height,"width":100*(right-left)/view.width,"height":100*(bottom-top)/view.height}
        cards.append({
            "id": f"{side}-hand-height", "title": f"{name} 위치와 가드 복귀 확인",
            "observation": f"확인 가능한 장면 중 {ratio}%에서 {name} 손목이 어깨선보다 뚜렷하게 낮게 측정됐습니다(화면 기준)." if ratio else f"확인 가능한 장면에서 {name}이 어깨선 아래로 크게 내려가는 모습은 측정 기준에 잡히지 않았습니다.",
            "interpretation": "손이 낮아진 상태가 이어진 장면입니다. 타격 중인지, 동작 후 가드로 돌아오는 중인지 영상을 확인하세요." if chosen else "화면 좌표만으로 가드의 좋고 나쁨을 판단할 수 없습니다. 동작 전후 손 위치를 비교할 장면을 골랐습니다.",
            "practice": "장면에서 동작 전·타격 직후·다음 동작 직전의 손 위치를 비교하세요. 코치가 복귀 지연을 확인하면, 한 동작씩 천천히 수행하고 시작 자세로 돌아오는 연습을 한 뒤 같은 각도로 다시 촬영하세요.",
            "check": "재촬영한 영상에서 동작 후 손이 시작 위치로 돌아왔는지 코치가 확인합니다. 낮은 손 위치 비율을 실수율이나 명중률로 사용하지 마세요.",
            "from_us": at, "to_us": min(duration_us, max(at+1, chosen[-1]+1 if chosen else at+2_000_000)),
            "image_us": at, "eligible_samples": len(times), "measured_percent": ratio,
            "status": "확인할 장면" if chosen else "위치 관찰",
            "subject_box": box,
        })
    cards.sort(key=lambda c:c["status"] != "확인할 장면")
    return {"method": "screen-hand-height-v1", "kind": "규칙 기반 코칭 초안",
            "scope": "분석 선수의 화면상 손 위치를 측정한 초안입니다. 촬영 각도·가림·공격 동작의 영향을 받으며, 원인과 훈련 제안은 코치 확인 후 적용합니다.",
            "cards": cards,
            "insufficient_reason": None if cards else "어깨·골반·손목이 충분히 보이는 관측이 부족해 손 위치를 자동 평가하지 않았습니다.",
            "manual_checks": ["타격 전과 직후 손이 어디에 있는지 비교하기", "공격 후 다음 동작을 준비하는 자세 확인하기", "같은 각도로 다시 촬영해 코치와 비교하기"]}

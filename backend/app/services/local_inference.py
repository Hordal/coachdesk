"""GitHub의 phase1b_v1 계약을 사용하는 실제 포즈 추론과 보수적인 동작 후보 규칙."""
from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import statistics
import subprocess

from ..domain.models import FrameTransform, MEDIAPIPE_LANDMARKS
from ..media.binaries import find_binary
from ..media.probe import inspect_source, probe_video
from .local_model import ENGINE_VERSION, MODEL_SHA256, model_path, model_ready


def distance(a, b):
    return math.hypot(a[0]-b[0],a[1]-b[1])


def iou(a,b):
    overlap=max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))
    union=(a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-overlap
    return overlap/union if union>0 else 0


@dataclass
class Identity:
    bbox: tuple
    center: tuple
    color: tuple
    timestamp: int
    velocity: tuple = (0.,0.)


class BoxTracker:
    """토르소 위치·색 관측의 짧은 연결. 가림이 길거나 불분명하면 새 트랙을 만든다."""
    def __init__(self):
        self.active:dict[int,Identity]={}
        self.next_id=1

    def assign(self, observations:list[dict], timestamp:int) -> list[int]:
        self.active={k:v for k,v in self.active.items() if timestamp-v.timestamp<=600_000}
        edges=[]
        for tid,old in self.active.items():
            dt=max(0,(timestamp-old.timestamp)/1_000_000)
            predicted=(old.center[0]+old.velocity[0]*dt,old.center[1]+old.velocity[1]*dt)
            for j,now in enumerate(observations):
                d=distance(predicted,now["center"])
                if d>.22:
                    continue
                color=distance(old.color[:2],now["color"][:2])+abs(old.color[2]-now["color"][2])
                cost=3*d+.25*(1-iou(old.bbox,now["bbox_norm"]))+.2*color
                if cost<.8:
                    edges.append((cost,tid,j))
        picked={}
        used=set()
        for cost,tid,j in sorted(edges):
            if tid not in used and j not in picked:
                picked[j]=tid
                used.add(tid)
        result=[]
        for j,now in enumerate(observations):
            tid=picked.get(j)
            velocity=(0.,0.)
            if tid is None:
                tid=self.next_id
                self.next_id+=1
            else:
                previous=self.active[tid]
                dt=max(.001,(timestamp-previous.timestamp)/1_000_000)
                velocity=tuple(.5*previous.velocity[k]+.5*(now["center"][k]-previous.center[k])/dt for k in (0,1))
            self.active[tid]=Identity(now["bbox_norm"],now["center"],now["color"],timestamp,velocity)
            result.append(tid)
        return result


def _observation(points,image):
    height,width=image.shape[:2]
    visible=[p for p in points if p.visibility>=.5 and p.presence>=.5 and math.isfinite(p.x) and math.isfinite(p.y)]
    torso=[points[k] for k in (11,12,23,24) if points[k].visibility>=.5 and points[k].presence>=.5]
    if len(visible)<8 or len(torso)<2:
        return None
    left=max(0,min(p.x for p in visible)-.035)
    right=min(1,max(p.x for p in visible)+.035)
    top=max(0,min(p.y for p in visible)-.025)
    bottom=min(1,max(p.y for p in visible)+.025)
    if right-left<.025 or bottom-top<.08:
        return None
    # Visibility of the hips used to move the tracking center from shoulders to
    # mid-torso even when the person stood still, creating many new track IDs.
    shoulders=[points[k] for k in (11,12) if points[k].visibility>=.5 and points[k].presence>=.5
               and math.isfinite(points[k].x) and math.isfinite(points[k].y)]
    anchor=shoulders if len(shoulders)==2 else torso
    center=(statistics.mean(p.x for p in anchor),statistics.mean(p.y for p in anchor))
    cx,cy=round(center[0]*width),round(center[1]*height)
    patch=image[max(0,cy-8):min(height,cy+9),max(0,cx-8):min(width,cx+9)]
    color=tuple(float(v)/255 for v in patch.mean(axis=(0,1))) if patch.size else (0.,0.,0.)
    return {"bbox_norm":(left,top,right,bottom),"bbox":[left*width,top*height,right*width,bottom*height],"center":center,"color":color,"points":points}


def infer_video(path:Path,progress) -> dict:
    # 설치 여부 확인과 추론 호출을 분리하여 API 프로세스가 ML 런타임을 로드하지 않게 한다.
    import mediapipe as mp
    import numpy as np
    if not model_ready():
        raise RuntimeError("내장 분석 모델이 없거나 손상되었습니다. setup_analysis.py를 실행해 주세요")
    source=inspect_source(path)
    info=probe_video(path)
    if info.frames[0].pts_us!=0:
        raise RuntimeError("현재 내장 시험 분석은 시작 PTS가 0인 영상만 지원합니다. 시간축을 0으로 맞춘 사본을 등록해 주세요")
    if info.frame_count>5_000 or info.duration_us>180_000_000:
        raise RuntimeError("내장 시험 분석은 3분·5,000프레임 이하 구간으로 나누어 사용해 주세요")
    width,height=960,540
    transform=FrameTransform.fit(source.width,source.height,source.rotation,width,height)
    options=mp.tasks.vision.PoseLandmarkerOptions(
        base_options=mp.tasks.BaseOptions(model_asset_path=str(model_path())),
        running_mode=mp.tasks.vision.RunningMode.VIDEO,num_poses=4,
        min_pose_detection_confidence=.45,min_pose_presence_confidence=.5,min_tracking_confidence=.5,
        output_segmentation_masks=False)
    arguments=[find_binary("ffmpeg"),"-v","error","-i",str(path),"-map","0:v:0","-an","-sn",
               "-vf",f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2",
               "-fps_mode","passthrough","-pix_fmt","rgb24","-f","rawvideo","pipe:1"]
    tracker=BoxTracker()
    frames=[]
    poses=[]
    quality=[]
    process=subprocess.Popen(arguments,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                             creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
    previous_ms=-1
    try:
        with mp.tasks.vision.PoseLandmarker.create_from_options(options) as model:
            for frame in info.frames:
                progress(.05+.8*frame.index/info.frame_count,f"관절·후보 추적 {frame.index+1:,} / {info.frame_count:,}프레임")
                raw=process.stdout.read(width*height*3)
                if len(raw)!=width*height*3:
                    raise RuntimeError("영상 디코딩 프레임 수가 원본 PTS 표와 일치하지 않습니다")
                image=np.frombuffer(raw,dtype=np.uint8).reshape(height,width,3)
                stamp_ms=max(previous_ms+1,frame.pts_us//1000)
                previous_ms=stamp_ms
                output=model.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB,data=image),stamp_ms)
                observed=[]
                for points in output.pose_landmarks:
                    item=_observation(points,image)
                    if item is not None:
                        observed.append(item)
                ids=tracker.assign(observed,frame.pts_us)
                tracks=[]
                for pose_index,(item,tid) in enumerate(zip(observed,ids)):
                    marks={}
                    for k,(name,p) in enumerate(zip(MEDIAPIPE_LANDMARKS,item["points"])):
                        finite=all(math.isfinite(v) for v in (p.x,p.y,p.z))
                        off=not (0<=p.x<=1 and 0<=p.y<=1)
                        marks[name]={"index":k,"x_norm":p.x if finite else None,"y_norm":p.y if finite else None,
                                     "z_norm":p.z if finite else None,"x_px":p.x*width if finite else None,"y_px":p.y*height if finite else None,
                                     "visibility":p.visibility,"presence":p.presence,"finite":finite,"off_image":off,
                                     "state":"VALID" if finite and not off and min(p.visibility,p.presence)>=.5 else "UNKNOWN"}
                    tracks.append({"track_id":tid,"bbox":item["bbox"],"confidence":None,"tracking_state":"observed","provider":"mediapipe_pose_full"})
                    poses.append({"schema_version":"phase1b_v1","frame_index":frame.index,"timestamp_us":frame.pts_us,
                                  "participant_id":f"candidate:{tid}","role":"UNRESOLVED","track_id":tid,"pose_index":pose_index,
                                  "source":"full_frame","mode":"VIDEO","status":"OBSERVED","raw_landmark_count":33,"landmarks":marks})
                    quality.append({"frame_index":frame.index,"timestamp_us":frame.pts_us,"participant_id":f"candidate:{tid}","track_id":tid,
                                    "role":"UNRESOLVED","feature_ready":all(marks[n]["state"]=="VALID" for n in ("left_shoulder","right_shoulder","left_wrist","right_wrist")),
                                    "statuses":[],"reasons":[]})
                frames.append({"frame_index":frame.index,"timestamp_us":frame.pts_us,"tracks":tracks})
        if process.stdout.read(1):
            raise RuntimeError("추가 디코딩 프레임이 있어 시간축을 확정할 수 없습니다")
        if process.wait(timeout=20)!=0:
            raise RuntimeError("영상 디코더가 정상 종료되지 않았습니다")
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        if process.stdout:
            process.stdout.close()
    if not poses:
        raise RuntimeError("관절을 충분히 관측한 사람을 찾지 못했습니다. 인물이 크게 보이고 가림이 적은 영상을 사용해 주세요")
    metadata={"source":path.name,"duration_us":source.duration_us,"width":transform.display_width,"height":transform.display_height,
              "coded_width":source.width,"coded_height":source.height,"rotation_degrees":source.rotation,
              "fps":info.nominal_fps,"codec":source.video_codec,"is_vfr":info.is_vfr,"frame_count":info.frame_count,
              "frame_timestamps_us":[f.pts_us for f in info.frames],"timestamp_basis":"source_pts","start_time_us":0,
              "analysis_width":width,"analysis_height":height,"derivative_frame_count":len(frames),"transform":transform.model_dump(),
              "warnings":["내장 시험 분석: 추적 상자는 관절 관측 범위이며 가림·교차 때 트랙이 나뉘거나 바뀔 수 있습니다.",
                          "선수 신원을 번호나 좌우 위치로 자동 확정하지 않습니다."]}
    return {"engine_version":ENGINE_VERSION,"model_sha256":MODEL_SHA256,
            "observations":{"source":ENGINE_VERSION,"metadata":metadata,"tracks":frames},
            "poses":{"source":ENGINE_VERSION,"poses":poses,"quality":quality,"windows":[]}}


def punch_candidates(rows:list[dict],track_ids:set[int],duration_us:int) -> list[dict]:
    """어깨 대비 손목 거리의 확장 뒤 복귀 후보. 타격·접촉·명중 분류기가 아니다."""
    grouped={}
    for row in rows:
        if row.get("track_id") in track_ids:
            grouped.setdefault(row["track_id"],[]).append(row)
    events=[]
    for tid,frames in sorted(grouped.items()):
        for arm in ("left","right"):
            baseline=[]
            pending=None
            previous_time=None
            cooldown=-1
            for frame in sorted(frames,key=lambda r:r["timestamp_us"]):
                stamp=frame["timestamp_us"]
                marks=frame.get("landmarks") or {}
                needed=[f"{arm}_wrist",f"{arm}_elbow","left_shoulder","right_shoulder"]
                if any(n not in marks or marks[n].get("state")!="VALID" for n in needed):
                    baseline=[];pending=None;previous_time=None
                    continue
                if previous_time is not None and stamp-previous_time>150_000:
                    baseline=[];pending=None
                previous_time=stamp
                xy=lambda n:(marks[n]["x_px"],marks[n]["y_px"])
                shoulder=xy(f"{arm}_shoulder")
                wrist,elbow=xy(f"{arm}_wrist"),xy(f"{arm}_elbow")
                shoulder_width=distance(xy("left_shoulder"),xy("right_shoulder"))
                # 팔 규칙에 쓰지 않는 엉덩이의 가림 때문에 유효한 팔 관측까지 버리지 않는다.
                limb=distance(elbow,shoulder)+distance(wrist,elbow)
                if shoulder_width<max(6,.15*limb):
                    baseline=[];pending=None
                    continue
                ratio=distance(wrist,shoulder)/shoulder_width
                upper,lower=distance(elbow,shoulder),distance(wrist,elbow)
                straight=distance(wrist,shoulder)/max(1,upper+lower)
                # 지난 0.6초의 실제 관측에서 시작 구간을 정한다. 관측이 없으면 추정하지 않는다.
                baseline=[sample for sample in baseline if stamp-sample[0]<=600_000]
                if pending is None and stamp>=cooldown and len(baseline)>=2:
                    low=min(baseline,key=lambda sample:sample[1])
                    if ratio-low[1]>=.55 and straight>=.88:
                        pending={"start":low[0],"start_index":low[2],"low":low[1],"peak":ratio,"peak_time":stamp,"peak_index":frame["frame_index"]}
                if pending is not None:
                    if ratio>pending["peak"]:
                        pending.update(peak=ratio,peak_time=stamp,peak_index=frame["frame_index"])
                    elapsed=stamp-pending["start"]
                    if elapsed>1_200_000:
                        pending=None
                    elif stamp>pending["peak_time"] and 120_000<=elapsed<=1_200_000 and pending["peak"]-ratio>=.35:
                        end=min(duration_us,stamp+1)
                        if end>pending["start"]:
                            events.append({"schema_version":"phase1b_v1","event_label":"PUNCH_CANDIDATE","status":"CANDIDATE","verified":False,"outcome":"UNKNOWN",
                                           "participant_id":f"candidate:{tid}","role":"UNRESOLVED","track_id":tid,"arm":arm,"synthetic":False,
                                           "start_timestamp_us":pending["start"],"end_timestamp_us":end,"start_frame_index":pending["start_index"],"end_frame_index":frame["frame_index"],
                                           "evidence":[{"measure":"wrist_shoulder_ratio_gain","value":round(pending["peak"]-pending["low"],4)},
                                                       {"measure":"wrist_shoulder_ratio_retract","value":round(pending["peak"]-ratio,4)},
                                                       {"measure":"normalization","value":"shoulder_width"}],
                                           "limitations":["HEURISTIC_NOT_A_CLASSIFIER","NO_VERIFIED_CONTACT","POSSIBLE_TRACK_ID_SWITCH","CAMERA_MOTION_NOT_COMPENSATED"],
                                           "reason_codes":["NORMALIZED_EXTENSION_THEN_RETRACTION"],"rule_version":"coachdesk_extension_retraction_v1"})
                        cooldown=stamp+250_000
                        pending=None
                        baseline=[]
                baseline.append((stamp,ratio,frame["frame_index"]))
    return sorted(events,key=lambda e:(e["start_timestamp_us"],e["track_id"],e["arm"]))

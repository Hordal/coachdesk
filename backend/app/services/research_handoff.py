"""Verified local research archive; files are data, never executable instructions."""
from __future__ import annotations

from pathlib import Path, PurePosixPath
from zipfile import ZipFile
import hashlib
import json
import shutil
import stat
from contextlib import nullcontext

from ..settings import REPO_ROOT

ROOT = REPO_ROOT / "runtime" / "research" / "handoff-2026-10-17"
MAX_TOTAL = 3 * 1024**3
SOURCE_LABEL = "MMACoachAgent handoff 2026-10-17"


class HandoffError(ValueError):
    pass


def safe_relative(value: str) -> str:
    value = value.replace("\\", "/")
    path = PurePosixPath(value)
    if not value or path.is_absolute() or any(p in ("..", ".") or ":" in p for p in path.parts):
        raise HandoffError("압축파일에 허용되지 않는 경로가 있습니다")
    return str(path)


def rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def prepare(archive: Path, destination: Path = ROOT) -> dict:
    """Extract into a new staging directory and verify manifest before activation."""
    with ZipFile(archive) as z:
        files = {}
        total = 0
        for info in z.infolist():
            if info.is_dir():
                continue
            name = safe_relative(info.filename)
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode) or info.flag_bits & 1:
                raise HandoffError("심볼릭 링크와 암호화 항목은 가져오지 않습니다")
            if name in files:
                raise HandoffError("중복된 압축 경로입니다")
            total += info.file_size
            if total > MAX_TOTAL:
                raise HandoffError("연구 묶음 크기 제한을 초과했습니다")
            files[name] = info
        roots = {name.split('/')[0] for name in files}
        if len(roots) != 1:
            raise HandoffError("단일 연구 묶음 폴더가 필요합니다")
        prefix = next(iter(roots)) + "/"
        manifest = [json.loads(line) for line in z.read(files[prefix + "FILE_INDEX.jsonl"]).decode("utf-8-sig").splitlines() if line.strip()]
        expected = {}
        for item in manifest:
            name = safe_relative(item["path"])
            if name in expected:
                raise HandoffError("파일 목록에 중복 경로가 있습니다")
            expected[name] = item
        allowed_root = {"README.md", "FILE_INDEX.jsonl", "PROVENANCE.json", "INTEGRITY_REPORT.json", "MODEL_WEIGHT_INVENTORY.json"}
        for name in files:
            relative = name[len(prefix):]
            if relative not in expected and relative not in allowed_root:
                raise HandoffError("검증 목록에 없는 파일입니다: " + relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        import tempfile
        with tempfile.TemporaryDirectory(prefix="handoff-stage-", dir=destination.parent) as temp:
            stage = Path(temp)
            for name, info in files.items():
                relative = name[len(prefix):]
                target = stage / relative
                keep = target.suffix.lower() in {".json", ".jsonl", ".md", ".yaml", ".txt"}
                if keep:
                    target.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                size = 0
                with z.open(info) as source, (target.open("wb") if keep else nullcontext()) as out:
                    while block := source.read(1024 * 1024):
                        size += len(block)
                        digest.update(block)
                        if out is not None:
                            out.write(block)
                item = expected.get(relative)
                if item and (size != item["bytes"] or digest.hexdigest() != item["sha256"]):
                    raise HandoffError("무결성 검사 실패: " + relative)
            missing = set(expected) - {n[len(prefix):] for n in files}
            if missing:
                raise HandoffError("목록에 있는 파일이 없습니다: " + sorted(missing)[0])
            provenance = json.loads((stage / "PROVENANCE.json").read_text(encoding="utf-8"))
            catalog = []
            for clip in provenance["clips"]:
                cid = clip["clip_id"]
                analysis = "results/" + clip["canonical_analysis"]
                track = analysis + "/tracking_frames.jsonl"
                # Archive explicitly records a separate ByteTrack replay for clip 01.
                if not (stage / track).is_file() and cid == "01":
                    track = "results/track_c2r_294965db2ffc/tracking_frames.jsonl"
                if not (stage / track).is_file():
                    raise HandoffError("추적 결과가 없습니다: " + cid)
                source = "videos/source/" + clip["source_file"]
                catalog.append({"clip_id": cid, "filename": clip["source_file"], "source": source,
                                "source_sha256": clip["sha256"], "metadata": analysis + "/metadata.json",
                                "tracking": track, "detections": analysis + "/detection_frames.jsonl",
                                "source_url": clip.get("source_url"), "frame_count": clip["frame_count"],
                                "public_preview": cid == "01", "has_research_pose": cid in {"01", "02", "04"}})
            report = {"archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                      "verified_files": len(expected), "file_count": len(files), "bytes": total, "clips": catalog}
            (stage / "archive_location.json").write_text(json.dumps({"path": str(archive.resolve()), "prefix": prefix}), encoding="utf-8")
            (stage / "catalog.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            # Do not overwrite an existing archive or user-edited local research data.
            if destination.exists():
                old = json.loads((destination / "catalog.json").read_text(encoding="utf-8"))
                if old["archive_sha256"] != report["archive_sha256"]:
                    raise HandoffError("다른 연구 묶음이 이미 있습니다. 새 저장 폴더를 사용하세요")
                (destination / "archive_location.json").write_text((stage / "archive_location.json").read_text(encoding="utf-8"), encoding="utf-8")
                return old
            shutil.copytree(stage, destination)
            return report


def catalog(root: Path = ROOT) -> list[dict]:
    file = root / "catalog.json"
    return json.loads(file.read_text(encoding="utf-8"))["clips"] if file.is_file() else []


def source_bytes(clip: dict, root: Path = ROOT) -> bytes:
    location = json.loads((root / "archive_location.json").read_text(encoding="utf-8"))
    try:
        with ZipFile(location["path"]) as archive:
            data = archive.read(location["prefix"] + clip["source"])
    except (OSError, KeyError) as exc:
        raise HandoffError("원본 연구 ZIP을 찾을 수 없습니다. prepare_research.py로 ZIP 위치를 다시 연결해 주세요") from exc
    if hashlib.sha256(data).hexdigest() != clip["source_sha256"]:
        raise HandoffError("원본 영상의 해시가 연구 묶음과 다릅니다")
    return data


def clip_by_id(clip_id: str, *, public: bool = False, root: Path = ROOT) -> dict:
    found = next((c for c in catalog(root) if c["clip_id"] == clip_id and (not public or c["public_preview"])), None)
    if found is None:
        raise HandoffError("사용 가능한 연구 영상이 없습니다")
    return found


def payloads(clip: dict, root: Path = ROOT) -> tuple[dict, dict, list[dict]]:
    """Keep clip IDs, original PTS, coordinates and uncertainty flags intact."""
    metadata = json.loads((root / clip["metadata"]).read_text(encoding="utf-8"))
    observations = {"source": SOURCE_LABEL, "metadata": metadata, "tracks": rows(root / clip["tracking"])}
    if (root / clip["detections"]).is_file():
        observations["detections"] = rows(root / clip["detections"])
    pose_file = root / "results/mvp_phase1c_participant_temporal_pose/pose_quality_frames.jsonl"
    window_file = root / "results/mvp_phase1c_participant_temporal_pose/window_candidates.jsonl"
    poses = [r for r in rows(pose_file) if r.get("clip_id") == clip["clip_id"] and r.get("mode") == "VIDEO"] if pose_file.is_file() else []
    windows = [r for r in rows(window_file) if r.get("clip_id") == clip["clip_id"]] if window_file.is_file() else []
    # Phase 1C VIDEO deliberately suppresses the previous IMAGE-only contaminated
    # punch candidate. Do not resurrect it or fabricate confirmed hits.
    actions_file = root / "results/mvp_phase1c_participant_temporal_pose/action_candidates.jsonl"
    actions = [r for r in rows(actions_file) if r.get("clip_id") == clip["clip_id"] and r.get("mode") == "VIDEO"
               and not r.get("contamination_gates") and r.get("status") == "CANDIDATE"] if actions_file.is_file() else []
    return observations, {"source": SOURCE_LABEL, "poses": poses, "quality": [], "windows": windows}, actions

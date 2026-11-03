import json
import time
import pytest
from app.storage.db import Database
from scripts.workspace_backup import backup,restore,verify


def test_backup_verifies_files_and_restore_never_overwrites(tmp_path):
    source = tmp_path / "source"
    db = Database(source / "app.sqlite3")
    (source / "media").mkdir()
    (source / "media/clip.mp4").write_bytes(b"private-test-media")
    (source / ".media-secret").write_text("unit-test-secret")
    snapshot = tmp_path / "snapshot"
    backup(source,snapshot)
    assert "media/clip.mp4" in verify(snapshot)
    target = tmp_path / "restored"
    ledger=source/"deletion-ledger.jsonl"
    ledger.write_text("")
    restore(snapshot,target,ledger)
    assert (target / "media/clip.mp4").read_bytes() == b"private-test-media"
    with pytest.raises(ValueError):
        restore(snapshot,target,ledger)
    (snapshot / "media/clip.mp4").write_bytes(b"damaged")
    with pytest.raises(ValueError):
        verify(snapshot)


@pytest.mark.parametrize("worker_table", ["worker_presence", "local_engine_presence"])
def test_active_worker_blocks_backup_and_traversal_is_rejected(tmp_path, worker_table):
    source = tmp_path / "active"
    db = Database(source / "app.sqlite3")
    with db.tx(immediate=True) as conn:
        if worker_table == "worker_presence":
            conn.execute("INSERT INTO worker_presence VALUES('w',?)",(time.time(),))
        else:
            conn.execute("INSERT INTO local_engine_presence VALUES('w',?,'test')",(time.time(),))
    with pytest.raises(ValueError):
        backup(source,tmp_path / "no-backup")
    assert not (tmp_path / "no-backup").exists()
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "manifest.json").write_text(json.dumps({"format":"coachdesk-backup-v1","files":{"app.sqlite3":"x","../escape":"x"}}))
    with pytest.raises(ValueError):
        restore(broken,tmp_path / "must-not-exist")
    assert not (tmp_path / "must-not-exist").exists()


def test_restore_reapplies_deletions_after_backup_and_rebases_original_paths(tmp_path):
    from app.settings import Settings
    from app.storage.videos import VideoRepo
    from app.services.deletion import DeletionService
    from app.jobs.store import JobStore
    from .test_manual_import import add_video
    source=tmp_path/"live"
    settings=Settings(data_dir=source)
    db=Database(settings.db_path)
    for vid in ("delete_all","delete_media","keep"):
        add_video(db,vid)
        original=settings.originals_dir/vid/"source.mp4"
        original.parent.mkdir(parents=True)
        original.write_bytes(b"private")
        settings.playback_dir.mkdir(exist_ok=True)
        (settings.playback_dir/f"{vid}.mp4").write_bytes(b"playback")
        with db.tx(immediate=True) as conn:
            conn.execute("UPDATE videos SET source_path=? WHERE video_id=?",(str(original),vid))
    snapshot=tmp_path/"old-snapshot"
    backup(source,snapshot)
    repo=VideoRepo(db)
    deletion=DeletionService(db,settings,repo,JobStore(db))
    deletion.request_delete_all(repo.get("delete_all"),"dev-user")
    deletion.delete_media(repo.get("delete_media"))
    restored=tmp_path/"restored"
    restore(snapshot,restored,source/"deletion-ledger.jsonl")
    new=VideoRepo(Database(restored/"app.sqlite3"))
    assert new.get("delete_all") is None
    assert not (restored/"originals/delete_all").exists()
    assert not (restored/"media/delete_all.mp4").exists()
    assert new.get("delete_media").media_state=="deleted"
    assert not (restored/"originals/delete_media").exists()
    assert new.get("keep").source_path==str(restored/"originals/keep/source.mp4")
    assert (restored/"originals/keep/source.mp4").read_bytes()==b"private"


def test_restore_requires_latest_ledger_and_rejects_malformed_deletion_paths(tmp_path):
    source=tmp_path/"source"
    Database(source/"app.sqlite3")
    snapshot=tmp_path/"snapshot"
    backup(source,snapshot)
    with pytest.raises(ValueError,match="deletion-ledger"):
        restore(snapshot,tmp_path/"restored")
    bad=tmp_path/"bad-ledger.jsonl"
    bad.write_text('{"video_id":"../outside","scope":"all"}\n')
    with pytest.raises(ValueError):
        restore(snapshot,tmp_path/"restored",bad)
    assert not (tmp_path/"restored").exists()

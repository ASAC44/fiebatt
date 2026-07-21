from types import SimpleNamespace

from app.schemas.timeline import PersistedClip, PersistedEDL
from app.services.edit_source import source_for_timeline_clip


def test_timeline_source_exposes_only_visible_media_range():
    project = SimpleNamespace(
        video_url="/media/original.mp4",
        duration=20.0,
        timeline_edl=PersistedEDL(
            clips=[
                PersistedClip(
                    id="trimmed",
                    kind="source",
                    url="/media/original.mp4",
                    source_start=8.0,
                    source_end=13.0,
                    media_duration=20.0,
                )
            ],
            sources=[],
        ).model_dump(mode="json"),
    )

    source = source_for_timeline_clip(project, "trimmed")

    assert source.duration == 20.0
    assert source.source_start == 8.0
    assert source.active_end == 13.0

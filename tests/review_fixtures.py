"""Prior validated review records for tracker-only tests (no semantic claim)."""
import json


def attach_review(vp, slug):
    project = vp.load_project(slug)
    directory = vp._project_dir(slug)
    if not vp.current_artifact(project, "transcript"):
        transcript = directory / "fixture-transcript.md"
        transcript.write_text("**[0:00]**\nFixture transcript.\n")
        vp.attach_artifact(slug=slug, kind="transcript", raw_path=str(transcript))
        project = vp.load_project(slug)
    inputs = {}
    for role, kind in (("package", "youtube_package"), ("transcript", "transcript"), ("master", "final_master")):
        artifact = vp.current_artifact(project, kind)
        inputs[role] = {"path": artifact["path"], "sha256": artifact["sha256"]}
    review = directory / "fixture-chapter-review.json"
    review.write_text(json.dumps({"inputs": inputs, "request_id": "fixture",
                                 "chapters": [{"id": "ch0", "supported": True}]}))
    vp.attach_artifact(slug=slug, kind="chapter_review", raw_path=str(review))

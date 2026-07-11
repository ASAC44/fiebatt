"""HTTP client wrapping the iris FastAPI backend."""

from pathlib import Path
from typing import Any

import httpx


class IrisClient:
    """Synchronous wrapper around the iris API."""

    def __init__(
        self,
        base_url: str,
        session_id: str,
        token: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.session_id = session_id
        self.token = token

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"X-Session-Id": self.session_id}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _url(self, path: str) -> str:
        return f"{self.base_url}/api{path}"

    def _request(
        self,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> Any:
        with httpx.Client(timeout=120.0) as client:
            response = client.request(
                method,
                self._url(path),
                headers=self._headers(),
                **kwargs,
            )
            response.raise_for_status()
            return response.json()

    # ── Upload ──────────────────────────────────────────────────────────

    def upload(self, file_path: Path) -> dict[str, Any]:
        """POST /api/upload — multipart file upload."""
        with file_path.open("rb") as f:
            return self._request(
                "POST",
                "/upload",
                files={"file": (file_path.name, f, "video/mp4")},
            )

    # ── Projects ────────────────────────────────────────────────────────

    def list_projects(self) -> list[dict[str, Any]]:
        """GET /api/projects — list all projects."""
        return self._request("GET", "/projects")

    def get_project(self, project_id: str) -> dict[str, Any]:
        """GET /api/projects/{id} — project detail."""
        return self._request("GET", f"/projects/{project_id}")

    # ── Generate ────────────────────────────────────────────────────────

    def generate(
        self,
        project_id: str,
        start_ts: float,
        end_ts: float,
        bbox: dict[str, float],
        prompt: str,
        reference_frame_ts: float | None = None,
    ) -> dict[str, Any]:
        """POST /api/generate — start a generation job."""
        body: dict[str, Any] = {
            "project_id": project_id,
            "start_ts": start_ts,
            "end_ts": end_ts,
            "bbox": bbox,
            "prompt": prompt,
        }
        if reference_frame_ts is not None:
            body["reference_frame_ts"] = reference_frame_ts
        return self._request("POST", "/generate", json=body)

    # ── Jobs ────────────────────────────────────────────────────────────

    def get_job(self, job_id: str) -> dict[str, Any]:
        """GET /api/jobs/{id} — job status and variants."""
        return self._request("GET", f"/jobs/{job_id}")

    # ── Accept ──────────────────────────────────────────────────────────

    def accept(self, job_id: str, variant_index: int = 0) -> dict[str, Any]:
        """POST /api/accept — accept a variant."""
        return self._request(
            "POST",
            "/accept",
            json={"job_id": job_id, "variant_index": variant_index},
        )

    # ── Identify ────────────────────────────────────────────────────────

    def identify(
        self,
        project_id: str,
        frame_ts: float,
        bbox: dict[str, float],
    ) -> dict[str, Any]:
        """POST /api/identify — identify an entity in a region."""
        return self._request(
            "POST",
            "/identify",
            json={
                "project_id": project_id,
                "frame_ts": frame_ts,
                "bbox": bbox,
            },
        )

    # ── Mask ────────────────────────────────────────────────────────────

    def mask(
        self,
        project_id: str,
        frame_ts: float,
        bbox: dict[str, float],
    ) -> dict[str, Any]:
        """POST /api/mask — get SAM segmentation mask."""
        return self._request(
            "POST",
            "/mask",
            json={
                "project_id": project_id,
                "frame_ts": frame_ts,
                "bbox": bbox,
            },
        )

    # ── Entities ────────────────────────────────────────────────────────

    def get_entity(self, entity_id: str) -> dict[str, Any]:
        """GET /api/entities/{id} — entity detail + appearances."""
        return self._request("GET", f"/entities/{entity_id}")

    # ── Propagate ───────────────────────────────────────────────────────

    def propagate(
        self,
        entity_id: str,
        source_variant_url: str,
        prompt: str,
        auto_apply: bool = True,
    ) -> dict[str, Any]:
        """POST /api/propagate — start propagation job."""
        return self._request(
            "POST",
            "/propagate",
            json={
                "entity_id": entity_id,
                "source_variant_url": source_variant_url,
                "prompt": prompt,
                "auto_apply": auto_apply,
            },
        )

    def get_propagation(self, propagation_job_id: str) -> dict[str, Any]:
        """GET /api/propagate/{id} — propagation job status."""
        return self._request("GET", f"/propagate/{propagation_job_id}")

    def apply_propagation(
        self,
        propagation_job_id: str,
        result_id: str,
    ) -> dict[str, Any]:
        """POST /api/propagate/{id}/apply/{result_id} — apply a propagation result."""
        return self._request(
            "POST",
            f"/propagate/{propagation_job_id}/apply/{result_id}",
        )

    # ── Timeline ────────────────────────────────────────────────────────

    def get_timeline(self, project_id: str) -> dict[str, Any]:
        """GET /api/timeline/{id} — ordered timeline segments."""
        return self._request("GET", f"/timeline/{project_id}")

    # ── Preview ─────────────────────────────────────────────────────────

    def preview_frame(self, project_id: str, ts: float) -> dict[str, Any]:
        """GET /api/preview/{pid}/frame?ts={ts} — preview a single frame."""
        return self._request(
            "GET",
            f"/preview/{project_id}/frame",
            params={"ts": ts},
        )

    def preview_strip(
        self,
        project_id: str,
        start: float,
        end: float,
        fps: float = 1.0,
    ) -> dict[str, Any]:
        """GET /api/preview/{pid}/strip — preview a frame strip for a range."""
        return self._request(
            "GET",
            f"/preview/{project_id}/strip",
            params={"start": start, "end": end, "fps": fps},
        )

    def preview_range(self, project_id: str, start: float, end: float) -> dict[str, Any]:
        """GET /api/preview/{pid}/range — preview a time range."""
        return self._request(
            "GET",
            f"/preview/{project_id}/range",
            params={"start": start, "end": end},
        )

    # ── Timeline surgery ────────────────────────────────────────────────

    def split_segment(
        self,
        project_id: str,
        segment_id: str,
        split_ts: float,
    ) -> dict[str, Any]:
        """POST /api/timeline/{pid}/split — split a timeline segment."""
        return self._request(
            "POST",
            f"/timeline/{project_id}/split",
            json={"segment_id": segment_id, "split_ts": split_ts},
        )

    def trim_segment(
        self,
        project_id: str,
        segment_id: str,
        new_start_ts: float,
        new_end_ts: float,
    ) -> dict[str, Any]:
        """POST /api/timeline/{pid}/trim — trim a timeline segment."""
        return self._request(
            "POST",
            f"/timeline/{project_id}/trim",
            json={
                "segment_id": segment_id,
                "new_start_ts": new_start_ts,
                "new_end_ts": new_end_ts,
            },
        )

    def delete_segment(self, project_id: str, segment_id: str) -> dict[str, Any]:
        """POST /api/timeline/{pid}/delete — delete a timeline segment."""
        return self._request(
            "POST",
            f"/timeline/{project_id}/delete",
            json={"segment_id": segment_id},
        )

    def reorder_segments(
        self,
        project_id: str,
        segment_ids: list[str],
        order: list[int],
    ) -> dict[str, Any]:
        """POST /api/timeline/{pid}/reorder — reorder timeline segments."""
        return self._request(
            "POST",
            f"/timeline/{project_id}/reorder",
            json={"segment_ids": segment_ids, "order": order},
        )

    def snapshot_timeline(self, project_id: str) -> dict[str, Any]:
        """POST /api/timeline/{pid}/snapshot — snapshot the timeline."""
        return self._request("POST", f"/timeline/{project_id}/snapshot")

    def revert_timeline(self, project_id: str, snapshot_id: str) -> dict[str, Any]:
        """POST /api/timeline/{pid}/revert — revert to a timeline snapshot."""
        return self._request(
            "POST",
            f"/timeline/{project_id}/revert",
            json={"snapshot_id": snapshot_id},
        )

    # ── Color grading ───────────────────────────────────────────────────

    def grade_segment(self, segment_id: str, adjustments: dict[str, Any]) -> dict[str, Any]:
        """POST /api/segments/{sid}/grade — apply grading adjustments."""
        return self._request(
            "POST",
            f"/segments/{segment_id}/grade",
            json={"adjustments": adjustments},
        )

    def grade_preview(self, segment_id: str, adjustments: dict[str, Any]) -> dict[str, Any]:
        """POST /api/segments/{sid}/grade/preview — preview grading adjustments."""
        return self._request(
            "POST",
            f"/segments/{segment_id}/grade/preview",
            json={"adjustments": adjustments},
        )

    def grade_match(self, source_id: str, reference_id: str) -> dict[str, Any]:
        """POST /api/grade/match — match source grading to a reference."""
        return self._request(
            "POST",
            "/grade/match",
            json={"source_id": source_id, "reference_id": reference_id},
        )

    # ── Scoring ─────────────────────────────────────────────────────────

    def score_variant(self, variant_id: str, compare_to: str = "prompt") -> dict[str, Any]:
        """POST /api/score — score a single variant."""
        return self._request(
            "POST",
            "/score",
            json={"variant_id": variant_id, "compare_to": compare_to},
        )

    def score_compare(self, variant_ids: list[str]) -> dict[str, Any]:
        """POST /api/score/compare — compare multiple variants."""
        return self._request(
            "POST",
            "/score/compare",
            json={"variant_ids": variant_ids},
        )

    def score_continuity(self, project_id: str) -> dict[str, Any]:
        """POST /api/score/continuity — score project continuity."""
        return self._request(
            "POST",
            "/score/continuity",
            json={"project_id": project_id},
        )

    # ── Remix & batch ───────────────────────────────────────────────────

    def remix(
        self,
        variant_id: str,
        modifier_prompt: str,
        preserve_composition: bool = True,
    ) -> dict[str, Any]:
        """POST /api/remix — remix a variant."""
        return self._request(
            "POST",
            "/remix",
            json={
                "variant_id": variant_id,
                "modifier_prompt": modifier_prompt,
                "preserve_composition": preserve_composition,
            },
        )

    def batch_generate(self, edits: list[Any]) -> dict[str, Any]:
        """POST /api/batch/generate — start a batch generation request."""
        return self._request(
            "POST",
            "/batch/generate",
            json={"edits": edits},
        )

    def batch_accept(self, accepts: list[Any]) -> dict[str, Any]:
        """POST /api/batch/accept — accept multiple batch results."""
        return self._request(
            "POST",
            "/batch/accept",
            json={"accepts": accepts},
        )

    # ── Narrate ─────────────────────────────────────────────────────────

    def narrate(
        self,
        variant_id: str,
        description: str | None = None,
    ) -> dict[str, Any]:
        """POST /api/narrate — generate narration audio."""
        body: dict[str, Any] = {"variant_id": variant_id}
        if description is not None:
            body["description"] = description
        return self._request("POST", "/narrate", json=body)

    # ── Export ──────────────────────────────────────────────────────────

    def export_video(self, project_id: str) -> dict[str, Any]:
        """POST /api/export — start export job."""
        return self._request(
            "POST",
            "/export",
            json={"project_id": project_id},
        )

    def get_export(self, export_job_id: str) -> dict[str, Any]:
        """GET /api/export/{id} — export job status."""
        return self._request("GET", f"/export/{export_job_id}")

    # ── Health ──────────────────────────────────────────────────────────

    def health(self) -> dict[str, Any]:
        """GET /api/health — backend health check."""
        return self._request("GET", "/health")

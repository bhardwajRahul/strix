import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from strix.report.writer import write_executive_report, write_run_metadata, write_vulnerabilities
from strix.telemetry import posthog


logger = logging.getLogger(__name__)

_global_report_state: Optional["ReportState"] = None


def get_global_report_state() -> Optional["ReportState"]:
    return _global_report_state


def set_global_report_state(report_state: "ReportState") -> None:
    global _global_report_state  # noqa: PLW0603
    _global_report_state = report_state


class ReportState:
    """Per-scan product artifact state plus artifact writer.

    The Agents SDK owns model/tool execution, tracing, and conversation
    persistence. This store keeps only Strix-owned scan artifacts and
    report metadata. Live UI projections belong to the interface layer.

    It does not consume SDK tracing processors.
    """

    def __init__(self, run_name: str | None = None):
        self.run_name = run_name
        self.run_id = run_name or f"run-{uuid4().hex[:8]}"
        self.start_time = datetime.now(UTC).isoformat()
        self.end_time: str | None = None

        self.vulnerability_reports: list[dict[str, Any]] = []
        self.final_scan_result: str | None = None

        self.scan_results: dict[str, Any] | None = None
        self.scan_config: dict[str, Any] | None = None
        self.run_metadata: dict[str, Any] = {
            "run_id": self.run_id,
            "run_name": self.run_name,
            "start_time": self.start_time,
            "end_time": None,
            "targets": [],
            "status": "running",
        }
        self._run_dir: Path | None = None
        self._saved_vuln_ids: set[str] = set()

        self.caido_url: str | None = None
        self.vulnerability_found_callback: Callable[[dict[str, Any]], None] | None = None

    def get_run_dir(self) -> Path:
        if self._run_dir is None:
            runs_dir = Path.cwd() / "strix_runs"
            runs_dir.mkdir(exist_ok=True)

            run_dir_name = self.run_name if self.run_name else self.run_id
            self._run_dir = runs_dir / run_dir_name
            self._run_dir.mkdir(exist_ok=True)

        return self._run_dir

    def hydrate_from_run_dir(self) -> None:
        """Reload prior-scan state from ``{run_dir}/`` for resume.

        Called by :func:`run_strix_scan` before any new agent runs.
        Restores:

        - ``vulnerability_reports`` from ``vulnerabilities.json`` so
          :meth:`add_vulnerability_report` doesn't allocate a colliding
          ``vuln-0001`` and overwrite the prior on-disk MD.
        - ``run_metadata`` (start_time, run_id, targets, status) from
          ``run_metadata.json`` so audit-trail timestamps + the final
          report's duration calc reflect the original scan, not just
          this resume segment.

        Idempotent on missing files (fresh runs land here too via the
        same code path). **Raises on corruption** — silently swallowing
        a corrupt ``vulnerabilities.json`` would let the next vuln
        allocate ``vuln-0001`` and overwrite the prior MD on disk
        (data loss). Caller is expected to fail the run loud and let
        the user inspect ``{run_dir}`` or pick a fresh ``--run-name``.
        """
        run_dir = self.get_run_dir()

        meta_path = run_dir / "run_metadata.json"
        if meta_path.exists():
            try:
                data = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise RuntimeError(
                    f"run_metadata.json at {meta_path} is unreadable: {exc}",
                ) from exc
            if isinstance(data, dict):
                if isinstance(data.get("start_time"), str):
                    self.start_time = data["start_time"]
                self.run_metadata.update(
                    {
                        k: v
                        for k, v in data.items()
                        if k in {"run_id", "run_name", "start_time", "targets", "status"}
                    },
                )
                logger.info("scan store hydrated run_metadata from %s", meta_path)

        json_path = run_dir / "vulnerabilities.json"
        if json_path.exists():
            try:
                data = json.loads(json_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise RuntimeError(
                    f"vulnerabilities.json at {json_path} is corrupt ({exc}); "
                    f"refusing to start fresh — that would overwrite prior "
                    f"vulnerability MDs on disk. Inspect or delete the run dir.",
                ) from exc
            if not isinstance(data, list):
                raise RuntimeError(
                    f"vulnerabilities.json at {json_path} is not a list",
                )
            self.vulnerability_reports = [r for r in data if isinstance(r, dict)]
            for r in self.vulnerability_reports:
                rid = r.get("id")
                if isinstance(rid, str):
                    self._saved_vuln_ids.add(rid)
            logger.info(
                "scan store hydrated %d vulnerability report(s)", len(self.vulnerability_reports)
            )

    def add_vulnerability_report(
        self,
        title: str,
        severity: str,
        description: str | None = None,
        impact: str | None = None,
        target: str | None = None,
        technical_analysis: str | None = None,
        poc_description: str | None = None,
        poc_script_code: str | None = None,
        remediation_steps: str | None = None,
        cvss: float | None = None,
        cvss_breakdown: dict[str, str] | None = None,
        endpoint: str | None = None,
        method: str | None = None,
        cve: str | None = None,
        cwe: str | None = None,
        code_locations: list[dict[str, Any]] | None = None,
        agent_id: str | None = None,
        agent_name: str | None = None,
    ) -> str:
        report_id = f"vuln-{len(self.vulnerability_reports) + 1:04d}"

        report: dict[str, Any] = {
            "id": report_id,
            "title": title.strip(),
            "severity": severity.lower().strip(),
            "timestamp": datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC"),
        }

        if description:
            report["description"] = description.strip()
        if impact:
            report["impact"] = impact.strip()
        if target:
            report["target"] = target.strip()
        if technical_analysis:
            report["technical_analysis"] = technical_analysis.strip()
        if poc_description:
            report["poc_description"] = poc_description.strip()
        if poc_script_code:
            report["poc_script_code"] = poc_script_code.strip()
        if remediation_steps:
            report["remediation_steps"] = remediation_steps.strip()
        if cvss is not None:
            report["cvss"] = cvss
        if cvss_breakdown:
            report["cvss_breakdown"] = cvss_breakdown
        if endpoint:
            report["endpoint"] = endpoint.strip()
        if method:
            report["method"] = method.strip()
        if cve:
            report["cve"] = cve.strip()
        if cwe:
            report["cwe"] = cwe.strip()
        if code_locations:
            report["code_locations"] = code_locations
        if agent_id:
            report["agent_id"] = agent_id
        if agent_name:
            report["agent_name"] = agent_name

        self.vulnerability_reports.append(report)
        logger.info(f"Added vulnerability report: {report_id} - {title}")
        posthog.finding(severity)

        if self.vulnerability_found_callback:
            self.vulnerability_found_callback(report)

        self.save_run_data()
        return report_id

    def get_existing_vulnerabilities(self) -> list[dict[str, Any]]:
        return list(self.vulnerability_reports)

    def update_scan_final_fields(
        self,
        executive_summary: str,
        methodology: str,
        technical_analysis: str,
        recommendations: str,
    ) -> None:
        self.scan_results = {
            "scan_completed": True,
            "executive_summary": executive_summary.strip(),
            "methodology": methodology.strip(),
            "technical_analysis": technical_analysis.strip(),
            "recommendations": recommendations.strip(),
            "success": True,
        }

        self.final_scan_result = f"""# Executive Summary

{executive_summary.strip()}

# Methodology

{methodology.strip()}

# Technical Analysis

{technical_analysis.strip()}

# Recommendations

{recommendations.strip()}
"""

        logger.info("Updated scan final fields")
        self.save_run_data(mark_complete=True)
        posthog.end(self, exit_reason="finished_by_tool")

    def set_scan_config(self, config: dict[str, Any]) -> None:
        self.scan_config = config
        self.run_metadata.update(
            {
                "targets": config.get("targets", []),
                "user_instructions": config.get("user_instructions", ""),
                "max_iterations": config.get("max_iterations", 200),
            }
        )

    def save_run_data(self, mark_complete: bool = False) -> None:
        if mark_complete:
            if self.end_time is None:
                self.end_time = datetime.now(UTC).isoformat()
            self.run_metadata["end_time"] = self.end_time
            self.run_metadata["status"] = "completed"

        self._save_artifacts()

    def cleanup(self) -> None:
        self.save_run_data(mark_complete=True)

    def _save_artifacts(self) -> None:
        """Write scan artifacts under ``run_dir``."""
        run_dir = self.get_run_dir()
        try:
            run_dir.mkdir(parents=True, exist_ok=True)

            if self.final_scan_result:
                write_executive_report(run_dir, self.final_scan_result)

            if self.vulnerability_reports:
                write_vulnerabilities(run_dir, self.vulnerability_reports, self._saved_vuln_ids)

            write_run_metadata(run_dir, self.run_metadata)

            logger.info("Essential scan data saved to: %s", run_dir)
        except (OSError, RuntimeError):
            logger.exception("Failed to save scan data")

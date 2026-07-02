from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .workspace import write_json


@dataclass(slots=True)
class HarnessReport:
    status: str
    summary: str
    data: dict[str, Any] = field(default_factory=dict)
    report_type: str = "run_report"
    report_version: int = 1

    def write(self, file_path: Path) -> None:
        write_json(
            file_path,
            {
                "report_type": self.report_type,
                "report_version": self.report_version,
                "status": self.status,
                "summary": self.summary,
                "data": self.data,
            },
        )

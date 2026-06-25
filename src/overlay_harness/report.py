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

    def write(self, file_path: Path) -> None:
        write_json(
            file_path,
            {
                "status": self.status,
                "summary": self.summary,
                "data": self.data,
            },
        )
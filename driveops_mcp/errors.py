"""DriveOps domain errors shared by backend modules."""

from __future__ import annotations

from typing import Any


class DriveOpsError(RuntimeError):
    pass


class AmbiguousFileError(DriveOpsError):
    def __init__(
        self, file_name: str, matches: list[dict[str, Any]], has_more: bool = False
    ) -> None:
        super().__init__(f"Multiple files named '{file_name}' found.")
        self.file_name = file_name
        self.matches = matches
        self.has_more = has_more


class AmbiguousFolderError(DriveOpsError):
    def __init__(
        self, folder_name: str, matches: list[dict[str, Any]], has_more: bool = False
    ) -> None:
        choices = ", ".join(
            f"{match.get('name') or 'Unnamed'} ({match.get('id')})" for match in matches
        )
        suffix = "; more matches exist" if has_more else ""
        super().__init__(
            f"Multiple folders matching '{folder_name}' found: {choices}{suffix}. Use a folder ID."
        )
        self.folder_name = folder_name
        self.matches = matches
        self.has_more = has_more

"""Which folder a worker opens. The bind itself runs in ``runtime.runs.desk``."""

from agentcore.runtime.runs.desk.binding import (
    LocalRootClaimBook,
    TargetFolderBinding,
    backend_local_root_id,
    build_target_backend,
    load_target_folder_binding,
)

__all__ = [
    "LocalRootClaimBook",
    "TargetFolderBinding",
    "backend_local_root_id",
    "build_target_backend",
    "load_target_folder_binding",
]

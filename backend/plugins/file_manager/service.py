"""Plugin lifecycle hooks for the API-only File Manager."""


class FileManagerService:
    def is_installed(self) -> bool:
        # Docker availability is enforced by the plugin dependency guard.
        return True

    def pause(self) -> None:
        return None

    def resume(self) -> None:
        return None


file_manager_service = FileManagerService()

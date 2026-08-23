"""Remove hidden reasoning and unauthorized credential actions from streamed chat output."""
from __future__ import annotations


_THINK_OPEN = "<think>"
_THINK_CLOSE = "</think>"
_SECRET_ACTION = "[action:allow_secrets:"
_SETUP_ACTION = "[action:app_setup_plan:"
_SENSITIVE_FILE_UNLOCK = "[action:unlock_sensitive_file:"
_MARKERS = (_THINK_OPEN, _SECRET_ACTION, _SETUP_ACTION, _SENSITIVE_FILE_UNLOCK)


class VisibleOutputFilter:
    """Streaming-safe filter that withholds incomplete internal tags between chunks."""

    def __init__(
        self,
        allow_secret_action: bool = False,
        allow_setup_action: bool = False,
        allow_sensitive_file_unlock: bool = False,
    ):
        self.allow_secret_action = allow_secret_action
        self.allow_setup_action = allow_setup_action
        self.allow_sensitive_file_unlock = allow_sensitive_file_unlock
        self._buffer = ""

    def push(self, chunk: str) -> str:
        self._buffer += chunk or ""
        return self._drain(final=False)

    def finish(self) -> str:
        return self._drain(final=True)

    def _drain(self, final: bool) -> str:
        emitted: list[str] = []
        while self._buffer:
            lower = self._buffer.lower()
            positions = [lower.find(marker) for marker in _MARKERS if lower.find(marker) >= 0]
            if not positions:
                keep = 0 if final else _partial_marker_suffix(lower)
                emitted.append(self._buffer[:-keep] if keep else self._buffer)
                self._buffer = self._buffer[-keep:] if keep else ""
                break

            position = min(positions)
            if position:
                emitted.append(self._buffer[:position])
                self._buffer = self._buffer[position:]
                continue

            if lower.startswith(_THINK_OPEN):
                closing = lower.find(_THINK_CLOSE, len(_THINK_OPEN))
                if closing < 0:
                    if final:
                        self._buffer = ""
                    break
                self._buffer = self._buffer[closing + len(_THINK_CLOSE):]
                continue

            action_marker = next(marker for marker in _MARKERS[1:] if lower.startswith(marker))
            closing = self._buffer.find("]", len(action_marker))
            if closing < 0:
                if final:
                    self._buffer = ""
                break
            action = self._buffer[:closing + 1]
            self._buffer = self._buffer[closing + 1:]
            if (
                (action_marker == _SECRET_ACTION and self.allow_secret_action)
                or (action_marker == _SETUP_ACTION and self.allow_setup_action)
                or (action_marker == _SENSITIVE_FILE_UNLOCK and self.allow_sensitive_file_unlock)
            ):
                emitted.append(action)
        return "".join(emitted)


def strip_hidden_reasoning(
    text: str,
    *,
    allow_setup_action: bool = False,
    allow_sensitive_file_unlock: bool = False,
) -> str:
    """Remove model reasoning and actions unless the server appended the verified action."""
    visible = VisibleOutputFilter(
        allow_secret_action=False,
        allow_setup_action=allow_setup_action,
        allow_sensitive_file_unlock=allow_sensitive_file_unlock,
    )
    return visible.push(text) + visible.finish()


def _partial_marker_suffix(text: str) -> int:
    for marker in _MARKERS:
        maximum = min(len(marker) - 1, len(text))
        for size in range(maximum, 0, -1):
            if text.endswith(marker[:size]):
                return size
    return 0

"""Session logging streams for release/debug modes."""
import re

_RELEASE_LOG_PATTERNS = [
    re.compile(p)
    for p in (
        r'^✓ Validation OK',
        r'^log date:',
        r'^\[\d{2}:\d{2}:\d{2}\]\s*$',
        r'^={10,}',
        r'^STEP \d+',
        r'^Step \d+ finished in',
        r'^Total time execution so far:',
        r'^Total execution time:',
        r'^All steps completed',
        r'^ALL STEPS COMPLETED',
        r'^STEPS \d+-\d+ COMPLETED',
        r'^\.\.\.',
        r'^✓ Step 0?\d+ complete',
        r'^\s*✓ Step \d+ completed\. Updating',
        r'^✗ Step \d+ failed',
        r'^STEP \d+ STOPPED',
        r'^✓ Baseline Created',
        r'^✓ Network Graph',
        r'^✓ Metadata Created',
        r'^Network Information',
        r'^\s*Total pairs:',
        r'^\s*Bridge pairs:',
        r'^\s*Total dates:',
        r'^\s*Connected dates:',
        r'^\s*Isolated dates:',
    )
]


def release_line_allowed(line):
    stripped = line.strip()
    if not stripped:
        return False
    return any(p.search(stripped) for p in _RELEASE_LOG_PATTERNS)


class TeeStream:

    def __init__(self, original, log_file):
        self._original = original
        self._log_file = log_file

    def write(self, data):
        self._original.write(data)
        if data and self._log_file:
            self._log_file.write(data)
            self._log_file.flush()

    def flush(self):
        self._original.flush()
        if self._log_file:
            self._log_file.flush()

    def isatty(self):
        return getattr(self._original, 'isatty', lambda: False)()


class ReleaseFilterStream:

    _is_release_filter = True

    def __init__(self, original, log_file):
        self._original = original
        self._log_file = log_file
        self._buffer = ''

    def write(self, data):
        if not data:
            return
        self._buffer += data
        while '\n' in self._buffer:
            line, self._buffer = self._buffer.split('\n', 1)
            out = line + '\n'
            if release_line_allowed(line):
                self._original.write(out)
                if self._log_file:
                    self._log_file.write(out)

    def flush(self):
        if self._buffer:
            if release_line_allowed(self._buffer):
                self._original.write(self._buffer)
                if self._log_file:
                    self._log_file.write(self._buffer)
            self._buffer = ''
        self._original.flush()
        if self._log_file:
            self._log_file.flush()

    def isatty(self):
        return getattr(self._original, 'isatty', lambda: False)()

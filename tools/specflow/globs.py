"""POSIX path glob matching, including zero-or-more-segment **."""

import fnmatch
import re


def glob_regex(pattern: str) -> re.Pattern:
    parts = pattern.split("/")
    result = ""
    for i, part in enumerate(parts):
        if part == "**":
            result += ".*" if i == len(parts) - 1 else "(?:[^/]+/)*"
            continue
        # Translate character classes with fnmatch, preventing / from being consumed
        # by any wildcard, including a negated class.
        segment = fnmatch.translate(part)[4:-3]
        segment = segment.replace(".*", "[^/]*").replace("(?>", "(?:")
        # fnmatch's ? and classes need a per-character slash guard.
        guarded = ""
        j = 0
        while j < len(segment):
            if segment[j] == "\\":
                guarded += segment[j:j + 2]
                j += 2
            elif segment[j] == "[":
                end = j + 1
                if segment[end:end + 1] == "^":
                    end += 1
                if segment[end:end + 1] == "]":
                    end += 1
                while end < len(segment) and segment[end] != "]":
                    end += 2 if segment[end] == "\\" else 1
                guarded += "(?!/)" + segment[j:end + 1]
                j = end + 1
            elif segment[j] == ".":
                guarded += "[^/]"
                j += 1
            else:
                guarded += segment[j]
                j += 1
        result += guarded
        if i < len(parts) - 1:
            result += "/"
    return re.compile("^" + result + "$")


def matches(path: str, pattern: str) -> bool:
    return glob_regex(pattern).fullmatch(path) is not None

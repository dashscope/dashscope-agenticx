# -*- coding: utf-8 -*-
"""
Experience memory — learn from task outcomes to improve future performance.
Records tool usage patterns, success/failure outcomes, and lessons learned.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from dashscope_agenticx.utils.keywords import (
    expand_scoring_terms,
    extract_keywords,
)


class ExperienceTracker:
    """Tracks and retrieves task experiences for learning."""

    # Rotation bounds: once the file outgrows MAX_FILE_BYTES, keep only the
    # newest KEEP_LINES entries (atomic rewrite).
    MAX_FILE_BYTES = 256 * 1024
    KEEP_LINES = 500
    DEFAULT_TTL_SECONDS = 90 * 24 * 60 * 60

    def __init__(self, workspace_dir: Path):
        self.experience_file = workspace_dir / "experiences.jsonl"
        self._cache: list[dict[str, Any]] | None = None
        self._cache_mtime_ns: int | None = None
        self._hits = 0
        self._misses = 0

    def record_experience(
        self,
        task_summary: str,
        tools_used: list[str],
        outcome: str,  # "success", "failure", "partial"
        lesson: str = "",
        verified: bool | None = None,
        ttl_seconds: int | None = None,
    ) -> None:
        """Record a task experience for future reference."""
        from dashscope_agenticx.utils import sanitize_text

        now = time.time()
        entry = {
            "task": sanitize_text(task_summary),
            "tools": tools_used,
            "outcome": outcome,
            "lesson": sanitize_text(lesson),
            "verified": (
                outcome in {"success", "failure"}
                if verified is None
                else bool(verified)
            ),
            "created_at": now,
            "expires_at": now + float(ttl_seconds or self.DEFAULT_TTL_SECONDS),
        }
        try:
            with open(self.experience_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            return
        self._rotate_if_oversize()

    def _rotate_if_oversize(self) -> None:
        try:
            if self.experience_file.stat().st_size <= self.MAX_FILE_BYTES:
                return
            lines = self.experience_file.read_text(
                encoding="utf-8",
            ).splitlines()
            keep = [line for line in lines if line.strip()][-self.KEEP_LINES :]
            # Unique temp name: two trackers rotating at once must not
            # clobber each other's temp file before the replace.
            tmp = self.experience_file.with_name(
                f"{self.experience_file.name}.{os.getpid()}."
                f"{uuid4().hex[:8]}.tmp",
            )
            tmp.write_text("\n".join(keep) + "\n", encoding="utf-8")
            tmp.replace(self.experience_file)
        except OSError:
            pass

    def _load(self) -> list[dict[str, Any]]:
        """Read all experiences, memoized on the file's mtime."""
        try:
            mtime_ns = self.experience_file.stat().st_mtime_ns
        except OSError:
            return []
        if self._cache is not None and self._cache_mtime_ns == mtime_ns:
            return self._cache
        experiences: list[dict[str, Any]] = []
        try:
            with open(self.experience_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        experiences.append(json.loads(line))
        except Exception:
            return []
        self._cache = experiences
        self._cache_mtime_ns = mtime_ns
        return experiences

    # pylint: disable=too-many-branches
    def search_experiences(
        self,
        query: str,
        limit: int = 3,
    ) -> list[dict[str, Any]]:
        """Search for relevant experiences based on CJK-aware keyword
        overlap."""
        keywords = extract_keywords(query)
        if not keywords:
            return []
        # Single CJK chars match almost everything; expand_scoring_terms
        # prefers multi-char tokens + bigrams and only falls back to raw
        # keywords (single chars) when nothing more specific exists.
        scoring = expand_scoring_terms(keywords)

        scored: list[tuple[int, int, dict[str, Any]]] = []
        now = time.time()
        for index, exp in enumerate(self._load()):
            if not self._is_recallable(exp, now):
                continue
            task = exp.get("task", "").lower()
            tools_str = " ".join(exp.get("tools", [])).lower()
            score = 0
            for kw in scoring:
                if kw in task:
                    score += 2
                if kw in tools_str:
                    score += 1
            if score > 0:
                if exp.get("lesson"):
                    score += 1
                # Cautionary lessons are high-value recall targets.
                if exp.get("outcome") == "failure":
                    score += 1
                scored.append((score, index, exp))

        # Highest score first; ties favor the most recently recorded entry.
        scored.sort(key=lambda item: (-item[0], -item[1]))

        # Dedupe near-identical records, keeping the most recent.
        seen: set[tuple[str, str]] = set()
        results: list[dict[str, Any]] = []
        for _, _, exp in scored:
            key = (exp.get("task", ""), exp.get("lesson", ""))
            if key in seen:
                continue
            seen.add(key)
            results.append(exp)
            if len(results) >= limit:
                break
        if results:
            self._hits += 1
        else:
            self._misses += 1
        return results

    @staticmethod
    def _is_recallable(exp: dict[str, Any], now: float) -> bool:
        verified = exp.get("verified")
        if verified is None:
            verified = exp.get("outcome") in {"success", "failure"}
        if not verified:
            return False
        try:
            expires_at = float(exp.get("expires_at", 0) or 0)
        except (TypeError, ValueError):
            expires_at = 0
        return not expires_at or expires_at > now

    def stats(self) -> dict[str, int]:
        entries = self._load()
        now = time.time()
        active = sum(1 for exp in entries if self._is_recallable(exp, now))
        return {
            "entries": len(entries),
            "active": active,
            "hits": self._hits,
            "misses": self._misses,
        }

    def delete(self, *, task: str | None = None) -> int:
        """Delete all experiences or those with an exact task summary."""
        entries = self._load()
        kept = [
            entry
            for entry in entries
            if task is not None and entry.get("task") != task
        ]
        deleted = len(entries) - len(kept)
        if not deleted:
            return 0
        try:
            self.experience_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.experience_file.with_name(
                f"{self.experience_file.name}.{uuid4().hex[:8]}.tmp",
            )
            text = "".join(
                json.dumps(entry, ensure_ascii=False) + "\n" for entry in kept
            )
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(self.experience_file)
            self._cache = None
            self._cache_mtime_ns = None
        except OSError:
            return 0
        return deleted

    def format_experiences_for_prompt(
        self,
        experiences: list[dict[str, Any]],
    ) -> str:
        """Format experiences for injection into system prompt."""
        if not experiences:
            return ""

        lines = ["\n\n## Past Lessons (reference)"]
        for i, exp in enumerate(experiences, 1):
            task = exp.get("task", "")
            outcome = exp.get("outcome", "unknown")
            lesson = exp.get("lesson", "")
            tools = exp.get("tools", [])

            outcome_emoji = {
                "success": "✓",
                "failure": "✗",
                "partial": "△",
            }.get(
                outcome,
                "?",
            )
            lines.append(f"{i}. {outcome_emoji} {task}")
            if tools:
                lines.append(f"   Tools used: {', '.join(tools)}")
            if lesson:
                lines.append(f"   Lesson: {lesson}")

        return "\n".join(lines)

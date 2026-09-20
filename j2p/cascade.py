"""Cached graph analysis and bounded, iterative cascade presentation."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Dict, Iterable, Iterator, List, Mapping, Optional, Set, Tuple


CASCADE_MAX_ENTRIES = 500
CASCADE_MAX_DEPTH = 32


class CascadeGraph:
    """Analyze a changed-issue graph once, preserving unique descendant counts."""

    def __init__(
        self, successors: Mapping[str, Iterable[str]],
        counted_keys: Optional[Iterable[str]] = None,
    ) -> None:
        keys = sorted(successors)
        included = set(keys)
        counted = included if counted_keys is None else included & set(counted_keys)
        self.successors = {
            key: sorted(set(successors[key]) & included) for key in keys
        }
        indegree = dict.fromkeys(keys, 0)
        for following in self.successors.values():
            for key in following:
                indegree[key] += 1
        self.roots = sorted(key for key, degree in indegree.items() if not degree)
        queue = deque(self.roots)
        ordered = []
        while queue:
            key = queue.popleft()
            ordered.append(key)
            for following in self.successors[key]:
                indegree[following] -= 1
                if not indegree[following]:
                    queue.append(following)
        # Integer bitsets avoid retaining millions of Python set entries for
        # long chains, and count a shared descendant only once in joined DAGs.
        positions = {key: index for index, key in enumerate(sorted(counted))}
        masks: Dict[str, int] = {}
        if len(ordered) == len(keys):
            for key in reversed(ordered):
                mask = 0
                for following in self.successors[key]:
                    mask |= masks[following]
                    if following in positions:
                        mask |= 1 << positions[following]
                masks[key] = mask
            self.downstream_counts = {key: bin(mask).count("1") for key, mask in masks.items()}
        else:
            # Accepted Jira dependencies are acyclic. Keep direct report calls
            # safe for malformed/cyclic plans without recursive traversal.
            self.downstream_counts = {
                key: len((self.reachable([key]) - {key}) & counted) for key in keys
            }
        self.ordered_successors = {
            key: sorted(following, key=lambda child: (-self.downstream_counts[child], child))
            for key, following in self.successors.items()
        }

    def reachable(self, roots: Iterable[str]) -> Set[str]:
        seen: Set[str] = set()
        stack = list(roots)
        while stack:
            key = stack.pop()
            if key in seen or key not in self.successors:
                continue
            seen.add(key)
            stack.extend(self.successors[key])
        return seen


@dataclass
class CascadeProjection:
    """Share one rendering budget across every branch in one report."""

    graph: CascadeGraph
    max_entries: int = CASCADE_MAX_ENTRIES
    max_depth: int = CASCADE_MAX_DEPTH
    seen: Set[str] = field(default_factory=set)
    entries: int = 0
    entry_limited: bool = False
    depth_limited: bool = False

    def events(self, root: str) -> Iterator[Tuple[str, str]]:
        stack: List[Tuple[str, str, int]] = [("node", root, 1)]
        while stack:
            action, key, depth = stack.pop()
            if action == "close":
                yield action, key
                continue
            if self.entries >= self.max_entries:
                self.entry_limited = True
                yield "limit", key
                # Close any open containers without visiting omitted branches.
                stack = [entry for entry in stack if entry[0] == "close"]
                continue
            self.entries += 1
            if key in self.seen:
                yield "reference", key
                continue
            self.seen.add(key)
            yield "node", key
            following = self.graph.ordered_successors.get(key, [])
            if not following:
                continue
            if depth >= self.max_depth:
                self.depth_limited = True
                yield "depth", key
                continue
            yield "open", key
            stack.append(("close", key, depth))
            stack.extend(("node", child, depth + 1) for child in reversed(following))

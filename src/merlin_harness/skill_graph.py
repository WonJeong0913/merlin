"""Relationships between skills in a library, for a view an operator can read.

The graph exists to answer management questions — which skills are unconnected,
which move together, what the harness itself has changed — so its shape is
driven by those questions rather than by whatever happens to be plottable.

Three deliberate constraints:

**Edge kinds never merge.** A line meaning "these were curated for the same
task" is a different claim from "the model was offered both in one turn", which
is different again from "the harness repaired one into the other". Collapsing
them into a single weight produces a picture that looks informative and is not.

**Isolation is an observation, not a verdict.** A skill with no edges is
reported as unconnected under the edge sources supplied. It is *not* labelled a
retirement candidate: absence of a curated task link says nothing about whether
the skill is useful, and retirement is a gated lifecycle decision that needs a
non-regression window over real use.

**Lineage is counted apart.** Shared-task and co-provisioning edges describe how
a library already was; lineage edges are the ones the harness drew itself by
repairing, merging or rolling back. Reporting them separately is what makes
"how much has this harness actually managed" answerable — and today the honest
answer is usually zero.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from itertools import combinations
from typing import Any, Iterable, Mapping, Sequence

from .models import SkillArtifact

EDGE_SHARED_TASK = "shared_task"
EDGE_CO_PROVISIONED = "co_provisioned"
EDGE_LINEAGE = "lineage"

EDGE_KINDS = (EDGE_SHARED_TASK, EDGE_CO_PROVISIONED, EDGE_LINEAGE)

LINEAGE_RELATIONS = frozenset({"repaired_from", "merged_from", "rolled_back_to"})

SCHEMA_VERSION = "merlin-skill-graph-v1"


class SkillGraphError(ValueError):
    """Raised when a graph cannot be built from the supplied relationships."""


@dataclass(frozen=True, slots=True)
class SkillGraphEdge:
    source: str
    target: str
    kind: str
    weight: int

    def __post_init__(self) -> None:
        if self.kind not in EDGE_KINDS:
            raise SkillGraphError(f"unsupported edge kind: {self.kind}")
        if self.source == self.target:
            raise SkillGraphError("an edge cannot join a skill to itself")
        if self.weight < 1:
            raise SkillGraphError("edge weight must be positive")

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "kind": self.kind,
            "weight": self.weight,
        }


@dataclass(frozen=True, slots=True)
class SkillGraphNode:
    skill_id: str
    name: str
    status: str
    version: int
    degree_by_kind: Mapping[str, int]

    @property
    def degree(self) -> int:
        return sum(self.degree_by_kind.values())

    @property
    def unconnected(self) -> bool:
        return self.degree == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "name": self.name,
            "status": self.status,
            "version": self.version,
            "degree": self.degree,
            "degree_by_kind": dict(self.degree_by_kind),
            "unconnected": self.unconnected,
        }


@dataclass(frozen=True, slots=True)
class SkillGraph:
    nodes: tuple[SkillGraphNode, ...]
    edges: tuple[SkillGraphEdge, ...]

    @property
    def unconnected_skill_ids(self) -> tuple[str, ...]:
        return tuple(node.skill_id for node in self.nodes if node.unconnected)

    def edges_of_kind(self, kind: str) -> tuple[SkillGraphEdge, ...]:
        return tuple(edge for edge in self.edges if edge.kind == kind)

    def components(self) -> tuple[tuple[str, ...], ...]:
        """Connected components over every edge kind, largest first."""

        adjacency: dict[str, set[str]] = defaultdict(set)
        for edge in self.edges:
            adjacency[edge.source].add(edge.target)
            adjacency[edge.target].add(edge.source)
        seen: set[str] = set()
        found: list[tuple[str, ...]] = []
        for node in self.nodes:
            if node.skill_id in seen:
                continue
            stack = [node.skill_id]
            component: set[str] = set()
            while stack:
                current = stack.pop()
                if current in component:
                    continue
                component.add(current)
                seen.add(current)
                stack.extend(adjacency[current] - component)
            found.append(tuple(sorted(component)))
        found.sort(key=lambda item: (-len(item), item))
        return tuple(found)

    def to_dict(self) -> dict[str, Any]:
        components = self.components()
        return {
            "schema_version": SCHEMA_VERSION,
            "nodes": [node.to_dict() for node in self.nodes],
            "edges": [edge.to_dict() for edge in self.edges],
            "edge_count_by_kind": {
                kind: len(self.edges_of_kind(kind)) for kind in EDGE_KINDS
            },
            "unconnected_skill_ids": list(self.unconnected_skill_ids),
            "component_count": len(components),
            "largest_component_size": len(components[0]) if components else 0,
            "boundary": {
                "unconnected_is_an_observation_not_a_retirement_verdict": True,
                "edge_kinds_are_never_merged": True,
                "lineage_edges_are_harness_authored": True,
                "shared_task_edges_describe_curation_not_use": True,
            },
        }


def _pairs_from_groups(groups: Iterable[Sequence[str]], known: set[str]):
    for group in groups:
        members = sorted({item for item in group if item in known})
        for left, right in combinations(members, 2):
            yield left, right


def build_skill_graph(
    skills: Sequence[SkillArtifact],
    *,
    shared_task_groups: Iterable[Sequence[str]] = (),
    co_provisioned_turns: Iterable[Sequence[str]] = (),
    lineage_links: Iterable[Mapping[str, str]] = (),
) -> SkillGraph:
    """Build a graph from a library and whichever relationship sources exist.

    Every source is optional. A library with no runs yet produces a graph with
    no co-provisioning and no lineage edges, which is the correct picture rather
    than a gap to be filled with something else.

    `shared_task_groups` and `co_provisioned_turns` are groups of skill IDs that
    appeared together — one group per task, one per turn. `lineage_links` are
    `{"source", "target", "relation"}` mappings where relation is one of
    `repaired_from`, `merged_from`, `rolled_back_to`.
    """

    if not skills:
        raise SkillGraphError("a graph needs at least one skill")
    by_id = {skill.id: skill for skill in skills}
    if len(by_id) != len(skills):
        raise SkillGraphError("library contains duplicate skill IDs")
    known = set(by_id)

    tally: dict[tuple[str, str, str], int] = defaultdict(int)
    for left, right in _pairs_from_groups(shared_task_groups, known):
        tally[(left, right, EDGE_SHARED_TASK)] += 1
    for left, right in _pairs_from_groups(co_provisioned_turns, known):
        tally[(left, right, EDGE_CO_PROVISIONED)] += 1
    for link in lineage_links:
        relation = link.get("relation")
        if relation not in LINEAGE_RELATIONS:
            raise SkillGraphError(f"unsupported lineage relation: {relation!r}")
        source, target = link.get("source"), link.get("target")
        if source not in known or target not in known:
            # A lineage endpoint outside the library is dropped rather than
            # inventing a node: the graph must not imply a skill that the
            # library does not hold.
            continue
        if source == target:
            raise SkillGraphError("a lineage link cannot join a skill to itself")
        pair = tuple(sorted((source, target)))
        tally[(pair[0], pair[1], EDGE_LINEAGE)] += 1

    edges = tuple(
        SkillGraphEdge(source=source, target=target, kind=kind, weight=weight)
        for (source, target, kind), weight in sorted(tally.items())
    )

    degrees: dict[str, dict[str, int]] = {
        skill_id: {kind: 0 for kind in EDGE_KINDS} for skill_id in by_id
    }
    for edge in edges:
        degrees[edge.source][edge.kind] += 1
        degrees[edge.target][edge.kind] += 1

    nodes = tuple(
        SkillGraphNode(
            skill_id=skill.id,
            name=skill.name,
            status=skill.status.value,
            version=skill.version,
            degree_by_kind=dict(degrees[skill.id]),
        )
        for skill in sorted(skills, key=lambda item: item.id)
    )
    return SkillGraph(nodes=nodes, edges=edges)

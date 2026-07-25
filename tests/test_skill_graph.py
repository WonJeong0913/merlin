from __future__ import annotations

import unittest

from src.merlin_harness.models import LifecycleStatus, SkillArtifact
from src.merlin_harness.skill_graph import (
    EDGE_CO_PROVISIONED,
    EDGE_LINEAGE,
    EDGE_SHARED_TASK,
    SkillGraphError,
    build_skill_graph,
)


def _skill(skill_id: str, *, status: LifecycleStatus = LifecycleStatus.ACTIVE) -> SkillArtifact:
    return SkillArtifact(
        id=skill_id,
        name=skill_id.upper(),
        description=f"{skill_id} description",
        trigger=f"{skill_id} trigger",
        status=status,
    )


LIBRARY = [_skill("a"), _skill("b"), _skill("c"), _skill("d")]


class ConstructionTests(unittest.TestCase):
    def test_an_empty_library_is_refused(self) -> None:
        with self.assertRaises(SkillGraphError):
            build_skill_graph([])

    def test_duplicate_skill_ids_are_refused(self) -> None:
        with self.assertRaises(SkillGraphError):
            build_skill_graph([_skill("a"), _skill("a")])

    def test_a_library_with_no_relationships_has_no_edges(self) -> None:
        # A library that has never run should look empty, not be filled in.
        graph = build_skill_graph(LIBRARY)
        self.assertEqual(graph.edges, ())
        self.assertEqual(len(graph.unconnected_skill_ids), 4)


class EdgeKindTests(unittest.TestCase):
    def test_shared_task_groups_become_pairwise_edges(self) -> None:
        graph = build_skill_graph(LIBRARY, shared_task_groups=[["a", "b", "c"]])
        self.assertEqual(len(graph.edges_of_kind(EDGE_SHARED_TASK)), 3)

    def test_repeated_grouping_raises_weight_not_edge_count(self) -> None:
        graph = build_skill_graph(
            LIBRARY, shared_task_groups=[["a", "b"], ["a", "b"], ["a", "b"]]
        )
        edges = graph.edges_of_kind(EDGE_SHARED_TASK)
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0].weight, 3)

    def test_the_three_kinds_stay_separate_over_the_same_pair(self) -> None:
        # The same two skills may be curated together, offered together, and
        # linked by a repair. Those are three different claims and must not
        # collapse into one line.
        graph = build_skill_graph(
            LIBRARY,
            shared_task_groups=[["a", "b"]],
            co_provisioned_turns=[["a", "b"]],
            lineage_links=[{"source": "a", "target": "b", "relation": "repaired_from"}],
        )
        kinds = {edge.kind for edge in graph.edges}
        self.assertEqual(kinds, {EDGE_SHARED_TASK, EDGE_CO_PROVISIONED, EDGE_LINEAGE})
        self.assertEqual(len(graph.edges), 3)
        self.assertEqual(graph.to_dict()["edge_count_by_kind"][EDGE_LINEAGE], 1)

    def test_unknown_skills_in_a_group_are_ignored(self) -> None:
        graph = build_skill_graph(LIBRARY, shared_task_groups=[["a", "not-in-library"]])
        self.assertEqual(graph.edges, ())

    def test_an_unsupported_lineage_relation_is_refused(self) -> None:
        with self.assertRaises(SkillGraphError):
            build_skill_graph(
                LIBRARY, lineage_links=[{"source": "a", "target": "b", "relation": "vibes"}]
            )

    def test_a_lineage_endpoint_outside_the_library_is_dropped(self) -> None:
        # Rendering it would imply a node the library does not hold.
        graph = build_skill_graph(
            LIBRARY, lineage_links=[{"source": "a", "target": "gone", "relation": "merged_from"}]
        )
        self.assertEqual(graph.edges_of_kind(EDGE_LINEAGE), ())


class ObservationTests(unittest.TestCase):
    def test_unconnected_is_reported_without_a_retirement_verdict(self) -> None:
        graph = build_skill_graph(LIBRARY, shared_task_groups=[["a", "b"]])
        payload = graph.to_dict()
        self.assertEqual(set(payload["unconnected_skill_ids"]), {"c", "d"})
        self.assertTrue(
            payload["boundary"]["unconnected_is_an_observation_not_a_retirement_verdict"]
        )
        serialized = str(payload).lower()
        self.assertNotIn("retire", serialized.replace("retirement_verdict", ""))

    def test_components_are_reported_largest_first(self) -> None:
        graph = build_skill_graph(
            LIBRARY, shared_task_groups=[["a", "b", "c"]]
        )
        components = graph.components()
        self.assertEqual(components[0], ("a", "b", "c"))
        self.assertEqual(components[-1], ("d",))
        payload = graph.to_dict()
        self.assertEqual(payload["component_count"], 2)
        self.assertEqual(payload["largest_component_size"], 3)

    def test_degree_is_broken_down_by_kind(self) -> None:
        graph = build_skill_graph(
            LIBRARY,
            shared_task_groups=[["a", "b"]],
            co_provisioned_turns=[["a", "c"]],
        )
        node = next(item for item in graph.nodes if item.skill_id == "a")
        self.assertEqual(node.degree_by_kind[EDGE_SHARED_TASK], 1)
        self.assertEqual(node.degree_by_kind[EDGE_CO_PROVISIONED], 1)
        self.assertEqual(node.degree, 2)
        self.assertFalse(node.unconnected)

    def test_lineage_count_is_zero_until_the_harness_acts(self) -> None:
        # The number that answers "what has this harness actually changed".
        graph = build_skill_graph(LIBRARY, shared_task_groups=[["a", "b", "c", "d"]])
        self.assertEqual(graph.to_dict()["edge_count_by_kind"][EDGE_LINEAGE], 0)


class DeterminismTests(unittest.TestCase):
    def test_the_graph_is_stable_across_input_ordering(self) -> None:
        forward = build_skill_graph(
            LIBRARY, shared_task_groups=[["a", "b"], ["c", "d"]]
        ).to_dict()
        reversed_input = build_skill_graph(
            list(reversed(LIBRARY)), shared_task_groups=[["d", "c"], ["b", "a"]]
        ).to_dict()
        self.assertEqual(forward, reversed_input)


if __name__ == "__main__":
    unittest.main()

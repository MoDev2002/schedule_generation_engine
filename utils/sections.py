# utils/sections.py
"""Section-based student-clash rules - the single source of truth.

One cohort is one (academic list, level) pair of students. Within a cohort,
group *k* of every course is the same student section (positional mapping,
confirmed as the real-world semantics): section 1 of IT202 and section 1 of
BS215 are the same students and must never sit in two rooms at once, while
section 1 of one course and section 2 of another are disjoint and may run
in parallel - that is what makes cross-course rotation ("section A takes
programming while section B takes electronics, then they swap") expressible.

Two attendance refinements sit on top of the positional mapping:

- A single-group course puts its whole cohort in one room - every section
  attends, so it clashes with anything else of that cohort.
- A merged common-course group k is section k of exactly the participating
  lists carried on the block: the builder partitions the merged course's
  groups proportionally across the participants' students (largest-
  remainder quotas, tiny shares folded into a neighbour), so the lists on
  a group block are precisely those with students in it. Within a merged
  course the groups still partition the merged students, so group 1 and
  group 2 of the same merged course remain mutually legal.

`strict_cohort_mode=True` restores the historical conservative rule, where
only parallel groups of the same course and session type may ever overlap
(merged groups included - they serialize against everything of their
cohorts again).

Every student-clash check in the engine (constraint manager, repair, final
verification, schedule validator) must go through `clash_reason` /
`blocks_clash`; duplicated local rules are how stale behavior survived.
"""

from typing import List, Optional, Set, Tuple

from models.block import Block

CohortKey = Tuple[str, int]


def cohort_keys(block: Block) -> List[CohortKey]:
    """
    The (academic list, level) cohorts a block puts in a room, in list order.

    A short academic_levels list pads from its first entry, matching the
    historical ConstraintManager behavior.
    """
    keys: List[CohortKey] = []
    levels = block.academic_levels or [0]
    for i, academic_list_name in enumerate(block.academic_lists):
        level = levels[i] if i < len(levels) else levels[0]
        keys.append((academic_list_name, level))
    return keys


def shared_cohorts(block_a: Block, block_b: Block) -> Set[CohortKey]:
    """Cohorts both blocks put in a room."""
    return set(cohort_keys(block_a)) & set(cohort_keys(block_b))


def sections_attending(block: Block) -> Optional[frozenset]:
    """
    Which sections of each attending cohort sit in this block; None = the
    whole cohort.

    Positional mapping: group k of a multi-group course is section k of
    every cohort the block puts in the room. A merged common-course group
    is section k of exactly the participating lists on the block (the
    builder subsets them per group); a list absent from the block has no
    students in that group. Single-group courses (merged or not) put their
    whole cohort in one room.
    """
    if block.is_single_group_course:
        return None
    return frozenset({block.group_number})


def is_whole_cohort_session(block: Block, strict_cohort_mode: bool = False) -> bool:
    """
    True when every section of each attending cohort sits in the room.

    The shared classification used by the solver's constraint encoding.
    strict_cohort_mode keeps merged groups whole-cohort: the conservative
    rule serializes a merged course against everything else of its cohorts.
    """
    if strict_cohort_mode and getattr(block, "is_merged_common_course", False):
        return True
    return sections_attending(block) is None


def _same_course_parallel(a: Block, b: Block) -> bool:
    """True when two blocks are parallel groups of the same course and type."""
    return (
        bool(a.all_participating_course_ids() & b.all_participating_course_ids())
        and a.block_type == b.block_type
        and not (a.is_single_group_course or b.is_single_group_course)
    )


def _strict_cohort_reason(block_a: Block, block_b: Block) -> Optional[str]:
    """The historical conservative rule, with its original explanations."""
    if not (
        block_a.all_participating_course_ids()
        & block_b.all_participating_course_ids()
    ):
        return "different courses"
    if block_a.block_type != block_b.block_type:
        return "a lecture and a lab for the same course"
    if block_a.is_single_group_course or block_b.is_single_group_course:
        return "a single-group course cannot run beside another session"
    if block_a.group_number == block_b.group_number:
        return f"both are group {block_a.group_number}"
    return None


def clash_reason(
    block_a: Block, block_b: Block, strict_cohort_mode: bool = False
) -> Optional[str]:
    """
    Why two blocks may not overlap, or None when they may.

    The one implementation behind every student-clash check in the engine.
    """
    shared = shared_cohorts(block_a, block_b)
    if not shared:
        return None

    if strict_cohort_mode:
        return _strict_cohort_reason(block_a, block_b)

    if _same_course_parallel(block_a, block_b):
        if block_a.group_number == block_b.group_number:
            return f"both are group {block_a.group_number} (same students)"
        return None

    for cohort in sorted(shared):
        sections_a = sections_attending(block_a)
        sections_b = sections_attending(block_b)

        if sections_a is None:
            return (
                f"{block_a.course_codes[0]} is a single-group course - the whole "
                f"cohort {cohort[0]} L{cohort[1]} attends"
            )
        if sections_b is None:
            return (
                f"{block_b.course_codes[0]} is a single-group course - the whole "
                f"cohort {cohort[0]} L{cohort[1]} attends"
            )

        overlap = sections_a & sections_b
        if overlap:
            return (
                f"group {sorted(overlap)[0]} of both courses is the same student "
                f"section of {cohort[0]} L{cohort[1]}"
            )

    return None


def blocks_clash(
    block_a: Block, block_b: Block, strict_cohort_mode: bool = False
) -> bool:
    """True when the two blocks would put the same students in two rooms."""
    return clash_reason(block_a, block_b, strict_cohort_mode) is not None

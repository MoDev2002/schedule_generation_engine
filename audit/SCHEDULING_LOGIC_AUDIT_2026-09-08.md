# Scheduling engine audit — September 8, 2026

**The concern is substantiated. There are both a scheduling-model capacity contradiction in the current inputs and reproducible search bugs that strand feasible lab sessions.** These are separate problems: improving placement cannot make an impossible input fit, while changing allocations alone does not fix the search.

This audit examined block construction, common-course merging, room/time candidates, student and resource constraints, greedy placement, retries, displacement repair, scoring, final validation, continuation, and recent reports. Production engine code was not changed. Added files contain this analysis, offline reproductions, their results, and limited current input snapshots. No production schedule was generated or posted by this audit; the backend was read to inspect the four study plans used by the recent lab run and current room calendars.

**1. Critical current input contradiction: plan 116 needs at least 26 periods; only 23 exist.**

Plan 116 is AHLIA Cybersecurity / Digital Forensics, level 2. Current API allocations were captured in [current_plan_allocations.json](<C:/Users/momam/Desktop/DU/Projects/University Scheduling/Code/schedule_generation_engine/audit/current_plan_allocations.json>). Each course has one lecture and five lab groups:

| Course | Assigned lab groups per TA | Minimum separate lab periods |
|---|---|---:|
| IT202 | TA 132: 2; TA 133: 3 | 3 |
| BS215 | TA 88: 5 | 5 |
| IT205 | TA 86: 3; TA 143: 2 | 3 |
| IT226 | TA 205: 5 | 5 |
| CS205 | TA 106: 5 | 5 |
| **Lab subtotal** | | **21** |
| Five lectures | One period each | **5** |
| **Total** | | **26** |

The lower bound follows directly from two existing constraints: one TA cannot teach two groups simultaneously, and different courses cannot overlap for the same `(academic list, level)` cohort. Therefore each course needs at least the largest group allocation to any one TA, and the courses' periods must be added together. Room and staff restrictions can increase this bound; they cannot reduce it.

Across **all 34 current rooms**, the union of two-hour periods is only **24**: Saturday through Thursday, with starts at 09:00, 11:00, 13:00, and 15:00. The current Tuesday 13:00–15:00 reserved period removes one, leaving **23**. Even allowing every room and ignoring all other restrictions, this plan is impossible under the present cohort model. Removing the reserved period would still leave a shortfall of two periods.

This is not a shortage of physical lab rooms. Adding rooms with the same hours cannot increase the number of distinct times available to one cohort. The remedies are to redistribute groups to additional qualified TAs, extend the available teaching hours, or represent stable student sections so that different courses can rotate concurrently for disjoint students. Staff qualifications, availability, and group membership must determine which remedy is valid.

The existing preflight at [main.py:917](<C:/Users/momam/Desktop/DU/Projects/University Scheduling/Code/schedule_generation_engine/main.py:917>) checks whether a block has a suitable room, but it does not detect this aggregate contradiction. It proceeds into repeated search and produces individual failure messages that obscure the collective cause.

Current minimum-period bounds for the other three plans are 20 for plan 113, 21 for plan 114, and 17 for plan 115. Those bounds fitting within 23 does **not** prove those plans jointly feasible; shared TAs, rooms, and lecturer hours still matter.

**2. P1 — Room-first greedy placement wastes parallel lab capacity.**

At [scheduler.py:373](<C:/Users/momam/Desktop/DU/Projects/University Scheduling/Code/schedule_generation_engine/scheduler.py:373>), placement loops through every time in the first suitable room before considering another room. It returns the first hard-valid assignment. It does not compare candidate scores or the impact on remaining cohort availability.

Reproduction: two groups of the same lab course, two distinct TAs, two identical rooms available 09:00–13:00. Actual output:

| Group | Engine output | Feasible parallel placement |
|---|---|---|
| A1 | Lab 1, 09:00–11:00 | Lab 1, 09:00–11:00 |
| A2 | Lab 1, 11:00–13:00 | Lab 2, 09:00–11:00 |

The engine consumes two periods of cohort availability instead of one. Its own ConstraintManager and final ScheduleValidator accept the parallel alternative. This is a search defect, not a prohibition against concurrent groups of the same course.

Corrective direction: evaluate room/time pairs together, accounting for the options left for unscheduled blocks and reuse of an existing compatible course period. Preserve scarce staff and specialist-room options. A simple time-first order is worth testing but is not a complete optimization strategy by itself.

**3. P1 — Single-victim repair cannot move a parallel set of groups.**

At [scheduler.py:491](<C:/Users/momam/Desktop/DU/Projects/University Scheduling/Code/schedule_generation_engine/scheduler.py:491>), repair finds the set of conflicting assignments but removes only one victim. If two lab groups occupy the target cohort period, removing one leaves the other conflict in place. No alternative candidate scoring can make that particular one-victim operation succeed.

A direct reproduction places two parallel A labs at 09:00 with room and staff availability at 11:00. A lecture for the cohort has only a 09:00 option. Repair fails, even though moving both labs to 11:00 and placing the lecture at 09:00 is valid. The reproduction validates that complete alternative through both existing constraint checks and final validation.

Corrective direction: bounded repair that can relocate the actual blocking set or follow a short displacement chain, with atomic rollback and protection for explicitly fixed entries. Bound the search by time or nodes.

**4. P1 — Retries can repeat the same scheduling mistake 100 times.**

The random value at [scheduler.py:684](<C:/Users/momam/Desktop/DU/Projects/University Scheduling/Code/schedule_generation_engine/scheduler.py:684>) is the final lexicographic tiebreak. It cannot change the order between blocks whose earlier priority values differ. Room/time ordering is deterministic too.

A realistic equal-size-within-course fixture contains four A lab groups with 25 students each and two B lab groups with 20 each. All six TAs are distinct. The cohort has four 30-seat rooms and two available periods. Across **100 attempts**, the engine schedules **4/6**, with **44 different group orders but only one course order: A, A, A, A, B, B**.

Room-first placement spreads A across both periods, with two groups in each. Both B groups fail. Single-victim repair cannot clear either period. A valid **6/6** solution places all four A groups at 09:00 in separate rooms and both B groups at 11:00. This complete alternative passes the engine's checks.

Corrective direction: diversify consequential decisions rather than only equivalent group order, recompute constrainedness against the live schedule, and retain a bounded time budget. Increasing the attempt count alone does not address this reproduction.

**5. P1 — “Perfect schedule” uses the wrong score scale; local optimization is disconnected.**

The early-exit condition is `attempt_score >= 0.95` at [scheduler.py:285](<C:/Users/momam/Desktop/DU/Projects/University Scheduling/Code/schedule_generation_engine/scheduler.py:285>). But [ConstraintManager.evaluate_soft_constraints](<C:/Users/momam/Desktop/DU/Projects/University Scheduling/Code/schedule_generation_engine/managers/constraint_manager.py:417>) returns an unnormalized weighted sum, and [scheduler.py:1272](<C:/Users/momam/Desktop/DU/Projects/University Scheduling/Code/schedule_generation_engine/scheduler.py:1272>) only averages it over assignments.

The two serialized labs above score **3.5**, so the engine labels the first attempt perfect and stops. A 0.95 threshold cannot be interpreted as 95% quality on this scale. Normalizing by every configured weight without considering which constraints apply to lecturers versus TAs would also need care.

`_local_search_with_constraint_manager` exists at line 1169 but has no call from the generation path. Even that unused function only swaps rooms, so it cannot compact time placement. Do not merely connect it without reviewing its mutation/rollback behavior.

Corrective direction: explicit lexicographic objectives—coverage first, then approved quality measures—and a meaningful search termination rule. Recompute scores against the exact schedule being evaluated. Treat completion and quality as distinct outcomes.

**6. P2 — TA preferences are explicitly reordered away.**

`get_available_slots` builds `preferred + remaining` for TAs, then [resource_manager.py:247](<C:/Users/momam/Desktop/DU/Projects/University Scheduling/Code/schedule_generation_engine/managers/resource_manager.py:247>) sorts that combined list by day/time. This removes the priority distinction.

Reproduction: a TA prefers 11:00–13:00; a lab is free at both 09:00 and 11:00; the engine chooses 09:00. The preferred option passes the constraints. This is a bug even though TA preferences are deliberately advisory rather than hard availability.

Corrective direction: retain preference as part of the candidate sort key or incorporate it into global candidate evaluation. Sorting each room correctly is not sufficient to guarantee the best preference across different rooms.

**7. P2 — “Minimize gaps” penalizes continuous teaching.**

At [constraint_manager.py:555](<C:/Users/momam/Desktop/DU/Projects/University Scheduling/Code/schedule_generation_engine/managers/constraint_manager.py:555>), the score compares the candidate's start to the earliest and latest starts. For an already placed block this often measures the span of the day instead of idle time. It also ignores minutes and actual session end times.

Reproduction: 09:00–11:00, 11:00–13:00, and 13:00–15:00 have **zero idle minutes**, but the first session receives a gap score of **0.5/1**. Parallel groups also share cohort-wide tracking, which cannot represent individual section gaps.

Corrective direction: merge occupied intervals for the relevant students and measure uncovered time between them. Once section membership exists, compute student gaps at that level.

**8. P2 — TA advisory reports can describe obsolete placements.**

Warnings are recorded during placement and accumulated during repair at [scheduler.py:551](<C:/Users/momam/Desktop/DU/Projects/University Scheduling/Code/schedule_generation_engine/scheduler.py:551>). Moving a victim does not remove its old warning. The successful displacement path also records the replacement victim's warning without recording the incoming block's warning there.

Two actual mismatches in the September 7 15:40 report:

| Block | Warning refers to | Final JSON placement |
|---|---|---|
| `P_IT101_312de16d439890b0_4` | Sunday 15:00 | Monday 11:00 |
| `P_CS205_85985f5c8e9d95c2_3` | Saturday 15:00 | Wednesday 13:00 |

The report's **13 warnings are not a reliable count of final out-of-hours placements**. These two mismatches do not establish whether their final placements satisfy the historical preferences, because those preferences were not saved in the schedule JSON. An offline probe independently confirms a warning persists after moving a TA into a preferred time.

Corrective direction: derive advisory messages once from the final chosen assignments and the input preferences, rather than preserving search history as the final report.

**9. Model limitation — Cross-course section rotations cannot be represented.**

[constraint_manager.py:463](<C:/Users/momam/Desktop/DU/Projects/University Scheduling/Code/schedule_generation_engine/managers/constraint_manager.py:463>) rejects every overlapping pair of different courses for the same cohort. A group's number is local to its course and does not identify a stable student section. Merged blocks similarly carry all participating cohorts; they do not record which students attend which group.

Consequently the engine cannot express “section A takes programming while section B takes electronics, then they swap.” If this is an intended scheduling requirement, it is a fundamental domain-model gap and helps explain the plan-116 contradiction. If group membership is not stable across courses, the current conservative rule is appropriate; simply allowing different group numbers from different courses to overlap would risk real student clashes.
yes this is a requirement “section A takes programming while section B takes electronics, then they swap.”

The user was asked which concurrency interpretation applies. Pending that clarification, this finding remains a conditional requirement gap. Same-course concurrency defects above are confirmed independently of the answer.

**10. Additional P2 candidate-generation issue — Room windows determine an implicit time grid.**

`_slots_in_window` at [resource_manager.py:157](<C:/Users/momam/Desktop/DU/Projects/University Scheduling/Code/schedule_generation_engine/managers/resource_manager.py:157>) steps two hours from each room window's start. A room available 09:00–17:00 and lecturer available 10:00–12:00 produce zero candidates, although 10:00–12:00 fits both windows and passes the hard constraints.

If the product intends free-form availability, candidate generation is incomplete. If only fixed periods are permitted, use an explicit common grid and validate inputs against it rather than deriving a separate grid from every room window. This did not cause the 26-versus-23 bound, whose captured calendars are aligned.

**Evidence from recent runs**

| Run | Lectures placed | Labs placed | Reported total | Unscheduled | Peak simultaneous labs |
|---|---:|---:|---:|---:|---:|
| Sep 7, 11:00 | 22 | 37 | 59/107 | 48 | 6 |
| Sep 7, 11:55 | 22 | 48 | 70/107 | 37 | 7 |
| Sep 7, 15:40 | 22 | 82 | 104/107 | 3 | 6 |
| Sep 7, 19:08 | 89 | 0 | 89/89 | 0 | 0 |
| Sep 8, 19:57 | 86 | 0 | 86/88 | 2 | 0 |
| Sep 8, 20:09 | 86 | 0 | 86/88 | 2 | 0 |

The 20:09 run appeared while this audit was underway. Its log records 100 attempts, zero built lab blocks, and a best result of 86/88. Its failures are BS211 lecture group 1 and CS205 lecture group 1. The capacity advisory is CS430: 430 students in a 400-seat hall. Capacity overflow and posting partial schedules are explicitly permitted by current code; those are product policies to confirm rather than accidental hidden relaxations.

The 15:40 lab run's missing blocks are IT226 group 2, CS205 group 3, and BS215 group 4. All belong to the AHLIA Cybersecurity level-2 cohort corresponding to current plan 116. Their failure messages describe 199, 178, and 192 room/time candidates rejected by student conflicts. These are counts of room/time pairs, **not** that many distinct weekly periods.

An independent pairwise check of the 104 persisted entries found **zero overlapping room or staff bookings**. Parallel labs do occur; the evidence concerns incomplete scheduling, inefficient use of concurrency, and model limitations. Peak concurrency alone is not a utilization score because resource availability and compatible staff limit what can run together.

Historical and current feasibility claims must stay separate. The September 7 lab JSON contains Tuesday 13:00 placements, while the current settings reserve that period. Its full input calendars and settings were not saved. The 26-versus-23 proof applies to the captured **current** API inputs. Under those same staffing allocations, even 24 periods would be insufficient; a full replay of the historical run cannot be asserted from its output JSON alone.

**Recommended implementation order**

1. Add aggregate feasibility diagnostics: per-cohort lower bounds, staff demand versus availability, and specialist-room demand. Report the actual bottleneck before repeated search.
2. Settle whether stable cross-course sections and rotations are required. If they are, add membership and lecture-to-lab relationships to the model and validators before relaxing overlap rules.
3. Fix candidate scoring/packing, TA preference ordering, meaningful retry diversification, and bounded multi-block repair. Keep the 6-session reproduction as a coverage regression.
4. Correct score scaling and real idle-time metrics; run genuine time-placement improvement with a bounded budget. Do not claim optimality from a heuristic quality score.
5. Recompute final warnings and save reproducible run inputs: selected room pool, settings, staff windows/allocations, expected block inventory, seed, and code revision. Keep credentials out of run snapshots.

A replacement solver may be worth evaluating after the model is settled. It would face the same infeasible plan-116 constraints if given the current formulation. A rewrite is not needed to verify or address the concrete bugs identified here.

**Verification and reproducibility**

The existing 10 unit tests passed. They cover block identities and basic one-victim repair, leaving the concurrency and objective defects above uncovered. The added [scheduling_logic_probes.py](<C:/Users/momam/Desktop/DU/Projects/University Scheduling/Code/schedule_generation_engine/audit/scheduling_logic_probes.py>) runs entirely offline using core engine classes and the captured snapshots. [probe_results.json](<C:/Users/momam/Desktop/DU/Projects/University Scheduling/Code/schedule_generation_engine/audit/probe_results.json>) records its outputs. Its assertions intentionally confirm current defects and validate feasible alternative schedules; they are audit probes, not passing regression tests that endorse the behavior.

The repository virtual environment could not launch its referenced Python 3.11 installation, so checks used the available bundled Python runtime. From the engine directory:

```powershell
& 'C:\Users\momam\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' audit/scheduling_logic_probes.py
& 'C:\Users\momam\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m unittest discover -s tests -v
```

The audit does not establish global optimality, nor does it prove that every missing historical block could be recovered. It does establish a current mathematical infeasibility, several deterministic implementation defects, and a feasible lab fixture the current search cannot complete.

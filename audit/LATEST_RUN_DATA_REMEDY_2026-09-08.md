# Latest run: verified data remedies

The newest completed run is **September 8, 2026, 20:09**, `schedule_20260908_200912.json`. It requested 27 study plans and lectures only. It placed 86/88 lectures, with no double bookings and one capacity advisory.

**The two missing lectures are unavoidable under the current lecturer availability.** Two different lecturers each have one more required session than usable periods, so 86 is an upper bound on placement count with those inputs. The current output reaches that bound. The previously identified lab/optimization bugs remain separate findings.

| Lecturer | Required sessions in this run | Usable two-hour periods | Shortfall |
|---|---:|---:|---:|
| Dr. Heba Hamed El-Hadidy, ID 93 | 7 | 6 | 1 |
| Prof. Wael Abdelkader Awad, ID 146 | 6 | 5 | 1 |

Dr. Heba's demand is CS205 ×2, CS102 ×3, CS310 ×1, CS327 ×1. Prof. Wael's demand is BS212 ×2, BS211 ×2, CS429 ×1, CS433 ×1. Both preference lists include Tuesday 13:00–15:00, which the global reserved period removes. Thus seven recorded periods become six for Dr. Heba, and six become five for Prof. Wael. Lecturer preferences act as binding availability in generation.

**Recommended changes, subject to the lecturers actually being available:**

1. Add **Sunday 11:00–13:00** to lecturer **93**, Dr. Heba's availability. Keeping the existing 86 placements, this permits missing CS205 group 1, 285 students, in **206B** at that time. An alternative checked against those placements is Thursday 15:00–17:00 in 204B or 206B.
2. Add **Wednesday 09:00–11:00** to lecturer **146**, Prof. Wael's availability. Keeping the existing 86 placements, this permits missing BS211 group 1, 290 students, in **206B**; 204B is also available.
3. For the separate CS430 capacity problem, edit the **parent course assignment 695 in study plan 100**, CS level 4. Change its lecture groups from **1 to 2** and change lecturer **87**, Dr. Mohamed Mahdy Badr's assigned lecture groups from **1 to 2**. Keep the common-course child references attached to this parent. The merged population is **80 + 150 + 200 = 430**, producing **215 students per group**. The child plans are 96 and 104. This adds one required lecture, raising the total from 88 to 89.

If the lecturers cannot offer those periods, reallocate one of each lecturer's required groups to another qualified lecturer with compatible room/cohort availability. Adding hall capacity alone cannot resolve a lecturer's period deficit. The specific alternate lecturers have not been assessed for qualifications.

No current hall seats 430 students: the largest two, 204B and 206B, seat 400. A genuinely larger room would be another solution for CS430 if available. Student counts and room capacities should reflect the real population and facilities.

**Verified outcomes using the existing, unchanged engine:**

| Offline scenario | Placed | Blocking conflicts | Capacity advisories |
|---|---:|---:|---:|
| Add the two lecturer periods | 88/88 | 0 | 1 |
| Add both periods and split CS430 | 89/89 | 0 | 0 |

Both scenarios were tested as **fresh generation**, not only by inserting sessions manually. Results are saved in `latest_run_fresh_simulations.json`.

An additional check retained the existing timetable and inserted the two missing lectures without moving any existing session. For the CS430 split, a valid repaired arrangement keeps group 1 on Sunday 11:00 in 204B, places group 2 Sunday 13:00 in 202B, and moves IS427 from Sunday 13:00 in 202B to Sunday 09:00 in 203B. This is an example feasible arrangement, not a demand to manually use these exact placements.

**Scope and provenance:** Study plans were recovered from the latest run's raw input log. All 88 expected block IDs were reconstructed and all 86 persisted placements matched them. Room and lecturer data were read from the current API; the selected six-hall pool was recovered from the log. This verifies the remedies against captured current resources, not against any future edits. Full lab generation was not tested in this follow-up. No production data, schedules, or scheduling implementation was changed.

Supporting files in this directory: `latest_run_analysis.py`, `latest_run_diagnosis.json`, `latest_run_plans_from_log.json`, `latest_run_current_resources.json`, and `latest_run_fresh_simulations.json`.

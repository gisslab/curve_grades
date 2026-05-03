# curve_grades

End-of-semester grading helper for Econ 101 (Spring 2026). Combines a Cengage
grade export and a section roster into a single per-student score (out of 40)
with a per-section curve for the participation portion.

## Inputs (`data/`, gitignored)

- `101_S26_pset+discussion.csv` — section roster with `Name`, `Section`, and
  empty columns: `Problem sets`, `Scores 1-10`, `Absent more than 2 times?`.
- `cengage_grades_101.csv` — Cengage export with 8 problem-set scores
  (50 pts each) and 11 attendance/participation columns (Section Two–Twelve,
  values 0–3 per session, 0 or empty = absent).
- `section_curve_overrides.csv` — optional per-section min/max grade overrides
  (`section, min_grade, max_grade`). Empty rows fall back to script defaults.

## Outputs (`output/`, gitignored)

- `section_scores_final.csv` — long form, all columns including
  `section_mean_0_3`, `participation_score`, rounded score, totals.
- `section_scores_short.csv` — short form: Name, Section, Problem sets,
  Scores 1-10, Absent flag.

## Scoring rules

- **Problem sets (30 pts):** null pset → 0, drop 2 lowest of 8, sum / 10.
- **Section /10:** `raw = (section_mean_0_3 + participation_score) / 2`,
  then per-section power curve anchored at `[TARGET_MIN, TARGET_MAX]` with
  mean = `TARGET_MEAN` (overall, including failed students).
  - `section_mean_0_3` = sort 11 attendance scores (NaN → 0), drop 2 lowest,
    mean of remaining 9.
  - Students with `absences > ABSENCES_FAIL_THRESHOLD` are excluded from the
    curve and given `FAIL_SCORE` (default 1).
  - `SCALE_MAX_BY_GROUP_QUALITY`: when on, each group's max is proportional
    to how its top student's raw compares to the class-wide top.
  - Per-section overrides in `section_curve_overrides.csv` take priority.

## Workflow

1. Drop the two source CSVs in `data/`.
2. Run [src/grade_sections.py](src/grade_sections.py) top-to-bottom. First
   pass writes `data/processed/participation_input.csv` (template) and prints
   any unmatched names. Resolve unmatched names via the `MANUAL_MATCHES` map.
3. Copy the template to `participation_input_filled.csv` and hand-enter the
   `participation_score` column for your sections (0–3).
4. (Optional) Set per-section min/max in `section_curve_overrides.csv`.
5. Re-run the script. Final outputs land in `output/`.

## Tunable macros (top of `src/grade_sections.py`)

`TARGET_MIN`, `TARGET_MAX`, `TARGET_MEAN`, `ABSENCES_FAIL_THRESHOLD`,
`FAIL_SCORE`, `SCALE_MAX_BY_GROUP_QUALITY`.

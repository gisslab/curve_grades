"""
End-of-semester grading helper for Econ 101.

Workflow:
    1. Place the two source CSVs in `data/` (see EXPECTED INPUTS below).
    2. Run this script top-to-bottom in VSCode (cells delimited by `#%%`).
       First pass writes `data/processed/participation_input.csv` (template)
       and reports any unmatched names.
    3. Copy the template to `participation_input_filled.csv` and hand-enter
       a `participation_score` (0–3) for each student in your sections.
    4. Optional: edit `data/section_curve_overrides.csv` to set per-section
       min/max grade caps. Empty rows fall back to script defaults.
    5. Re-run the script. Final outputs land in `output/`.

EXPECTED INPUTS (file names are configurable in the macros below):
    SECTION_FILE       : section roster with columns
                         `Name`, `Section`, `Problem sets`, `Scores 1-10`,
                         `Absent more than 2 times?`. Names are
                         `"Lastname,Firstname"` with NO space.
    CENGAGE_FILE       : Cengage export. First data row contains
                         `Max Points: ...` metadata and is skipped.
                         Required columns: `Student Name` (formatted
                         `"Lastname, Firstname"`), `Problem Set #1`–`#8`
                         (max 50 each), `Section Two`–`Section Twelve`
                         (11 attendance/participation columns, 0–3 per
                         session, 0 or empty = absent).
    SECTION_OVERRIDES  : optional per-section min/max overrides with
                         columns `section, min_grade, max_grade`.

OUTPUTS (gitignored):
    data/processed/participation_input.csv         template (regenerated each run)
    data/processed/participation_input_filled.csv  YOU fill participation_score
    output/section_scores.csv                      pset + absence flag (early dump)
    output/section_scores_final.csv                long: all columns + totals
    output/section_scores_short.csv                short: 5-column gradebook view
"""

#%% Imports & paths
from __future__ import annotations
from pathlib import Path
from difflib import SequenceMatcher
import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
PROCESSED = DATA / "processed"
OUT = ROOT / "output"
PROCESSED.mkdir(exist_ok=True)
OUT.mkdir(exist_ok=True)

# --- File paths (rename here if your filenames differ) ----------------------
SECTION_FILE = DATA / "101_S26_pset+discussion.csv"
CENGAGE_FILE = DATA / "cengage_grades_101.csv"
SECTION_OVERRIDES = DATA / "section_curve_overrides.csv"
PARTICIPATION_INPUT = PROCESSED / "participation_input.csv"
PARTICIPATION_FILLED = PROCESSED / "participation_input_filled.csv"
OUTPUT_FILE = OUT / "section_scores.csv"
FINAL_FILE = OUT / "section_scores_final.csv"
FINAL_SHORT_FILE = OUT / "section_scores_short.csv"

# --- Curve parameters (per-section power curve on the 1–10 grade) -----------
# Each section is rescaled so the bottom student gets TARGET_MIN, the top
# student gets TARGET_MAX, and the mean lands on TARGET_MEAN. The shape
# parameter p is solved numerically per section to satisfy the mean.
TARGET_MIN = 3 # students with very high section attendance get a floor of 3 (instead of 1)
TARGET_MAX = 10.0 # max grade for highest score students 
TARGET_MEAN = 5

# Students with absences strictly greater than this threshold are EXCLUDED
# from the curve and given FAIL_SCORE. With 11 sections total, threshold=6
# means "missed >6, i.e. ≥7" → attended ≤4 → score = 1.
ABSENCES_FAIL_THRESHOLD = 7
FAIL_SCORE = 1.0

# If True: each group's max score = TARGET_MIN + SPAN × (group_max_raw /
# global_max_raw). The class-wide best student gets TARGET_MAX; other groups
# whose best is weaker get a max below TARGET_MAX. If False, every group's
# top student gets exactly TARGET_MAX (original behavior).
SCALE_MAX_BY_GROUP_QUALITY = True

# Manual overrides for names that don't match automatically.
# Keys are normalized "last,first" from section file; values are normalized
# "last,first" from cengage file. Fill in after running the matching cell.
MANUAL_MATCHES: dict[str, str] = {
    # Example format — replace with real overrides after running the matching cell:
    # "lastname-as-in-section-file,firstname": "lastname-as-in-cengage,firstname",
}

#%% Load files
section_df = pd.read_csv(SECTION_FILE)
cengage_raw = pd.read_csv(CENGAGE_FILE)

# Row 0 of cengage is "Max Points: ..." metadata — drop it.
cengage_df = cengage_raw.iloc[1:].reset_index(drop=True)

print(f"Section roster: {len(section_df)} students")
print(f"Cengage grades: {len(cengage_df)} students")
section_df.head()

#%% Normalize names for matching
def norm(name: str) -> str:
    """Lowercase, strip whitespace, collapse 'last, first' → 'last,first'."""
    if not isinstance(name, str):
        return ""
    parts = [p.strip().lower() for p in name.split(",", 1)]
    return ",".join(parts)

def first_token(name: str) -> str:
    """First word of given name (handles middle names)."""
    after_comma = name.split(",", 1)[1] if "," in name else ""
    return after_comma.strip().split(" ", 1)[0]

#%%
section_df["_norm"] = section_df["Name"].map(norm)
cengage_df["_norm"] = cengage_df["Student Name"].map(norm)

section_df["_last"] = section_df["_norm"].str.split(",").str[0]
section_df["_first"] = section_df["_norm"].map(first_token)
cengage_df["_last"] = cengage_df["_norm"].str.split(",").str[0]
cengage_df["_first"] = cengage_df["_norm"].map(first_token)

#%%
section_df[["_norm", "_last", "_first"]].head()
#%%
cengage_df[["_norm", "_last", "_first"]].head()

#%% Match section roster ↔ cengage
SUFFIXES = {"iv", "iii", "ii", "jr", "sr"}

def _last_tokens(last: str) -> set[str]:
    return {t for t in last.split() if t and t not in SUFFIXES}

def best_match(last: str, first: str, candidates: pd.DataFrame) -> str | None:
    """Match on last name; if no exact match, fall back to token-overlap
    (handles compound surnames 'X Y' and suffixes like 'iv'/'jr').
    If multiple candidates remain, pick best fuzzy first-name match."""
    pool = candidates[candidates["_last"] == last]
    if pool.empty:
        section_tokens = _last_tokens(last)
        if section_tokens:
            pool = candidates[candidates["_last"].apply(
                lambda c: bool(section_tokens & _last_tokens(c))
            )]
    if pool.empty:
        return None
    if len(pool) == 1:
        return pool["_norm"].iloc[0]
    scored = pool["_first"].map(
        lambda f: SequenceMatcher(None, f, first).ratio()
        + (0.3 if f and first and f[0] == first[0] else 0)
    )
    return pool.loc[scored.idxmax(), "_norm"]

#%%
matches = []
unmatched = []
for _, row in section_df.iterrows():
    sec_norm = row["_norm"]
    if sec_norm in MANUAL_MATCHES:
        matches.append((sec_norm, MANUAL_MATCHES[sec_norm]))
        continue
    m = best_match(row["_last"], row["_first"], cengage_df)
    if m is None:
        unmatched.append(sec_norm)
    else:
        matches.append((sec_norm, m))

#%%
match_df = pd.DataFrame(matches, columns=["section_norm", "cengage_norm"])
print(f"Matched: {len(match_df)} / {len(section_df)}")
print(f"Unmatched ({len(unmatched)}):")
for u in unmatched:
    print(f"  {u}")

#%% Sanity-check fuzzy matches (eyeball first-name differences)
joined_check = match_df.merge(
    section_df[["_norm", "Name", "Section"]], left_on="section_norm", right_on="_norm"
).merge(
    cengage_df[["_norm", "Student Name"]], left_on="cengage_norm", right_on="_norm",
    suffixes=("_sec", "_cen"),
)
fuzzy = joined_check[joined_check["section_norm"] != joined_check["cengage_norm"]]
print(f"\n{len(fuzzy)} matches with name differences — verify these:")
fuzzy[["Section", "Name", "Student Name"]]

#%% Compute pset score (best 6 of 8) ÷ 10  → out of 30
PSET_COLS = [f"Problem Set #{i}" for i in range(1, 9)]
ATTEND_COLS = [
    "Section Two", "Section Three", "Section Four", "Section Five",
    "Section Six", "Section Seven", "Section Eight", "Section Nine",
    "Section Ten", "Section Eleven", "Section Twelve",
]

psets = cengage_df[PSET_COLS].apply(pd.to_numeric, errors="coerce").fillna(0)
# drop lowest 2 → keep top 6, sum, divide by 10
sorted_desc = np.sort(psets.values, axis=1)[:, ::-1]
top6_sum = sorted_desc[:, :6].sum(axis=1)
cengage_df["pset_30"] = top6_sum / 10

# Section participation: values 1-3 = present, empty OR 0 = absent.
# section_mean: treat absences as 0, sort all 11, drop 2 lowest, mean of remaining 9.
# This rewards consistent attendance — frequent absences pull the mean down.
raw_attend = cengage_df[ATTEND_COLS].apply(pd.to_numeric, errors="coerce")
attend_with_zeros = raw_attend.fillna(0)
sorted_attend = np.sort(attend_with_zeros.values, axis=1)  # ascending
cengage_df["section_mean"] = sorted_attend[:, 2:].mean(axis=1).round(2)

# Absences = empty OR 0 cells across the 11 section columns
cengage_df["absences"] = (raw_attend.isna() | (raw_attend == 0)).sum(axis=1)
cengage_df["absent_gt2"] = cengage_df["absences"] > 2

cengage_df[["Student Name", "pset_30", "section_mean", "absences", "absent_gt2"]].head(10)

#%% Write participation input file (you fill in the participation column)
# * Output file: participation_input_filled.csv 
participation = section_df.merge(
    match_df, left_on="_norm", right_on="section_norm", how="left"
).merge(
    cengage_df[["_norm", "section_mean", "absences"]],
    left_on="cengage_norm", right_on="_norm", how="left", suffixes=("", "_cen"),
)

participation_out = pd.DataFrame({
    "Name": participation["Name"],
    "Section": participation["Section"],
    "section_mean_0_3": participation["section_mean"],
    "absences": participation["absences"],
    "participation_score": "",  # YOU fill this in
})
participation_out = participation_out.sort_values(["Section", "Name"]).reset_index(drop=True)
participation_out.to_csv(PARTICIPATION_INPUT, index=False)
print(f"Wrote {PARTICIPATION_INPUT}")
participation_out.head(20)

#%% Build output (preserve section file order; subjective left blank)
out = section_df.merge(match_df, left_on="_norm", right_on="section_norm", how="left")
out = out.merge(
    cengage_df[["_norm", "pset_30", "absences", "absent_gt2"]],
    left_on="cengage_norm", right_on="_norm", how="left", suffixes=("", "_cen"),
)

out["Problem sets"] = out["pset_30"].round(2)
out["Absent more than 2 times?"] = out["absent_gt2"].map(
    {True: "YES", False: "no"}
).fillna("?")
# Scores 1-10 stays blank for hand-entry

final_cols = ["Name", "Section", "Problem sets", "Scores 1-10",
              "Absent more than 2 times?", "absences"]
final = out[final_cols].copy()
final.to_csv(OUTPUT_FILE, index=False)
print(f"Wrote {OUTPUT_FILE}")
final.head(15)

#%% Students who missed > 2 sections (send to instructor for excused-absence review)
absent_list = final[final["Absent more than 2 times?"] == "YES"][
    ["Section", "Name", "absences"]
].sort_values(["Section", "Name"])
print(f"{len(absent_list)} students missed > 2 sections:")
absent_list

#%% Load filled participation file and compute raw score
# We DO NOT modify participation_input_filled.csv. We take ONLY the
# participation_score column from it; section_mean_0_3 and absences are
# re-pulled fresh from cengage so the latest rule (0 = absent = null) applies.
filled_raw = pd.read_csv(PARTICIPATION_FILLED)
filled_raw["participation_score"] = pd.to_numeric(
    filled_raw["participation_score"], errors="coerce"
)
filled_raw = filled_raw.dropna(subset=["participation_score"]).copy()

# Refresh section_mean and absences from cengage via the name match
fresh = section_df[["Name", "Section", "_norm"]].merge(
    match_df, left_on="_norm", right_on="section_norm", how="left"
).merge(
    cengage_df[["_norm", "section_mean", "absences"]],
    left_on="cengage_norm", right_on="_norm", how="left", suffixes=("", "_cen"),
)[["Name", "Section", "section_mean", "absences"]]

filled = filled_raw[["Name", "Section", "participation_score"]].merge(
    fresh, on=["Name", "Section"], how="left"
)
filled = filled.rename(columns={"section_mean": "section_mean_0_3"})
filled["raw"] = (filled["section_mean_0_3"] + filled["participation_score"]) / 2

print(f"Filled rows: {len(filled)} across sections {sorted(filled.Section.unique())}")
filled.head()

#%% Power curve per section: min→TARGET_MIN, max→TARGET_MAX, mean→TARGET_MEAN
SPAN = TARGET_MAX - TARGET_MIN
TARGET_NORM_MEAN = (TARGET_MEAN - TARGET_MIN) / SPAN  # required mean of n^p

def solve_p(n: np.ndarray, target: float = TARGET_NORM_MEAN,
            lo: float = 0.01, hi: float = 50.0, tol: float = 1e-6) -> float:
    """Bisection: find p such that mean(n^p) == target."""
    for _ in range(200):
        mid = (lo + hi) / 2
        # n^p is decreasing in p for n in [0,1), so mean decreases with p
        if (n ** mid).mean() > target:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    return (lo + hi) / 2

# Class-wide reference: max raw across all curved students in filled sections.
# Used only when SCALE_MAX_BY_GROUP_QUALITY is True AND no manual override.
_curved_only = filled[filled["absences"] <= ABSENCES_FAIL_THRESHOLD]
GLOBAL_MAX_RAW = float(_curved_only["raw"].max()) if not _curved_only.empty else None
print(f"GLOBAL_MAX_RAW (class top among curved students) = {GLOBAL_MAX_RAW:.3f}")

# Per-section min/max overrides from CSV. Empty cells → fall back to defaults.
_overrides_df = pd.read_csv(SECTION_OVERRIDES)
SECTION_OVERRIDES_MAP: dict[int, tuple[float | None, float | None]] = {}
for _, _row in _overrides_df.iterrows():
    _sec = int(_row["section"])
    _mn = float(_row["min_grade"]) if pd.notna(_row["min_grade"]) else None
    _mx = float(_row["max_grade"]) if pd.notna(_row["max_grade"]) else None
    SECTION_OVERRIDES_MAP[_sec] = (_mn, _mx)
print(f"Loaded overrides for {sum(1 for v in SECTION_OVERRIDES_MAP.values() if v != (None, None))} sections")

def curve_group(group: pd.DataFrame) -> pd.DataFrame:
    group = group.copy()
    group["score_10"] = np.nan
    group["_p"] = np.nan
    group["_eff_min"] = np.nan
    group["_eff_max"] = np.nan

    # Failing students (too many absences) → fixed FAIL_SCORE, excluded from curve
    fail_mask = group["absences"] > ABSENCES_FAIL_THRESHOLD
    group.loc[fail_mask, "score_10"] = FAIL_SCORE

    curved_subset = group.loc[~fail_mask]
    n_total = len(group)
    n_fail = int(fail_mask.sum())
    n_curve = n_total - n_fail
    if n_curve == 0:
        return group

    section = int(group["Section"].iloc[0])
    override_min, override_max = SECTION_OVERRIDES_MAP.get(section, (None, None))

    # Effective min: override if set, else default
    eff_min = override_min if override_min is not None else TARGET_MIN

    # Effective max: override if set, else SCALE_MAX_BY_GROUP_QUALITY policy or default
    if override_max is not None:
        eff_max = override_max
    elif SCALE_MAX_BY_GROUP_QUALITY and GLOBAL_MAX_RAW and GLOBAL_MAX_RAW > 0:
        group_max_raw = curved_subset["raw"].max()
        ratio = min(group_max_raw / GLOBAL_MAX_RAW, 1.0)
        eff_max = TARGET_MIN + SPAN * ratio
    else:
        eff_max = TARGET_MAX

    eff_span = eff_max - eff_min

    # Adjust curve target so that OVERALL mean (curved + failed) = TARGET_MEAN
    curve_mean = (n_total * TARGET_MEAN - n_fail * FAIL_SCORE) / n_curve
    curve_mean = max(min(curve_mean, eff_max), eff_min)
    if eff_span <= 0:
        group.loc[~fail_mask, "score_10"] = curve_mean
        group.loc[~fail_mask, "_eff_min"] = eff_min
        group.loc[~fail_mask, "_eff_max"] = eff_max
        return group
    target_norm_mean = (curve_mean - eff_min) / eff_span

    r = curved_subset["raw"].values.astype(float)
    if r.max() == r.min():
        group.loc[~fail_mask, "score_10"] = curve_mean
        group.loc[~fail_mask, "_eff_min"] = eff_min
        group.loc[~fail_mask, "_eff_max"] = eff_max
        return group

    n = (r - r.min()) / (r.max() - r.min())
    p = solve_p(n, target=target_norm_mean)
    group.loc[~fail_mask, "score_10"] = (eff_min + eff_span * n ** p).round(2)
    group.loc[~fail_mask, "_p"] = round(p, 3)
    group.loc[~fail_mask, "_eff_min"] = round(eff_min, 2)
    group.loc[~fail_mask, "_eff_max"] = round(eff_max, 2)
    return group

curved = filled.groupby("Section", group_keys=False).apply(curve_group)

#%% Print per-section conclusions (curved students + failed students separately)
for sec in sorted(curved["Section"].unique()):
    g = curved[curved.Section == sec].sort_values("score_10", ascending=False)
    failed = g[g["absences"] > ABSENCES_FAIL_THRESHOLD]
    in_curve = g[g["absences"] <= ABSENCES_FAIL_THRESHOLD]
    p = in_curve["_p"].dropna().iloc[0] if not in_curve["_p"].dropna().empty else None
    eff_min = in_curve["_eff_min"].dropna().iloc[0] if not in_curve["_eff_min"].dropna().empty else None
    eff_max = in_curve["_eff_max"].dropna().iloc[0] if not in_curve["_eff_max"].dropna().empty else None
    print(
        f"--- Section {sec}  (p={p}, eff=[{eff_min},{eff_max}], n_curved={len(in_curve)}, n_failed={len(failed)}, "
        f"OVERALL mean={g.score_10.mean():.2f}, "
        f"curve mean={in_curve.score_10.mean():.2f}, "
        f"curve min={in_curve.score_10.min()}, curve max={in_curve.score_10.max()}) ---"
    )
    print(
        g[["Name", "absences", "section_mean_0_3", "participation_score", "raw", "score_10"]]
        .to_string(index=False)
    )
    print()

#%% Merge curved scores back into the section roster and write final file
final_out = section_df[["Name", "Section"]].merge(
    cengage_df.rename(columns={"_norm": "_cen_norm"})[["_cen_norm", "pset_30", "absences", "absent_gt2", "section_mean"]]
        .merge(match_df, left_on="_cen_norm", right_on="cengage_norm", how="right")[
            ["section_norm", "pset_30", "absences", "absent_gt2", "section_mean"]
        ],
    left_on=section_df["_norm"], right_on="section_norm", how="left",
).drop(columns=["section_norm", "key_0"], errors="ignore")
final_out = final_out.rename(columns={"section_mean": "section_mean_0_3"})

# participation_score and final score_10 only exist for students whose TA
# filled in the participation file — left-join from `curved`.
final_out = final_out.merge(
    curved[["Name", "Section", "score_10", "participation_score"]],
    on=["Name", "Section"], how="left",
)

final_out["Problem sets"] = final_out["pset_30"].round(2)
final_out["Scores 1-10"] = final_out["score_10"]
final_out["Scores 1-10 (rounded)"] = final_out["score_10"].round().astype("Int64")
final_out["Absent more than 2 times?"] = final_out["absent_gt2"].map(
    {True: "YES", False: "no"}
).fillna("?")
final_out["Total /40"] = (
    final_out["Problem sets"].fillna(0) + final_out["Scores 1-10"].fillna(0)
).round(2)

final_out = final_out[
    ["Name", "Section", "absences", "section_mean_0_3", "Problem sets",
     "Scores 1-10", "Scores 1-10 (rounded)",
     "Absent more than 2 times?", "Total /40", "participation_score"]
]
final_out.to_csv(FINAL_FILE, index=False)
print(f"Wrote {FINAL_FILE}")

final_out[["Name", "Section", "Problem sets", "Scores 1-10",
           "Absent more than 2 times?"]].to_csv(FINAL_SHORT_FILE, index=False)
print(f"Wrote {FINAL_SHORT_FILE}")
final_out.head(20)

#%% Final pretty printout: Name, Section, Pset, Absent (yes/no + #), Section grade, Total
pretty = final_out.copy()
pretty["Absent"] = pretty.apply(
    lambda r: f"{r['Absent more than 2 times?']} ({int(r['absences'])})"
    if pd.notna(r["absences"]) else "?",
    axis=1,
)
pretty = pretty.rename(columns={
    "Problem sets": "Pset /30",
    "Scores 1-10": "Section /10",
    "Total /40": "Total /40",
})
pretty = pretty[["Name", "Section", "Pset /30", "Absent", "Section /10", "Total /40"]]
pretty = pretty.sort_values(["Section", "Name"]).reset_index(drop=True)

my_sections = sorted(
    pretty.loc[pretty["Section /10"].notna(), "Section"].unique()
)
with pd.option_context("display.max_rows", None, "display.width", 200):
    for sec in my_sections:
        block = pretty[pretty.Section == sec]
        print(f"\n=== Section {sec}  (n={len(block)}) ===")
        print(block.to_string(index=False))


# %%

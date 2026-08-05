---
name: metrics-tracker-data-model
description: Business metric definitions for the Shopdeck Metrics Tracker — 1k-5k, HIT1/HIT2/Revenue buckets, roles (GL/GM/CL), ARR and the card-11020 denominator, Spend/Live, Acceptance, Google-live, go-live multiplier, Target vs Achievement, ARR cohorts and tranches, churn, S/GMV, incentives, and the creative-test A/B. Use before computing, changing or explaining any of these numbers so the definition matches the rest of the dashboard.
---

# Metric definitions

Use these verbatim. Inventing a variant makes a new view disagree with every existing one.

## Populations

| Bucket | Definition (`csv_upload.hit_master_data`, card 10453) |
|---|---|
| **1k-5k team ("assigned")** | `ts_data.json → hitsMap` sellers with `good = 0`. **~240 and moving** |
| **HIT1** | `team = 'HITS' AND good_seller IS NULL` |
| **HIT2** | `hit2 = 1` — **42 sellers** |
| **HIT1 + HIT2** | either — **they overlap by design** in this convention (by 4 sellers today) |
| **Revenue** | `good_seller IS NULL AND team ≠ 'HITS' AND hit2 ≠ 1` |

These are the card-11815 variant predicates (`bev_refresh.py`, `_G15UNI`). **Match them for any new
bucket split.**

> Caveat: the churn cohort (cards 12142/12159) deliberately makes the three **mutually exclusive**
> because a churn cohort must not double-count. Two conventions coexist on purpose — check which
> one a view uses.

### `hitsMap` silently excludes most HIT2 sellers — know this

`ts_refresh` keeps each seller's **latest** `hit_master_data` row and only where `team = 'HITS'`.
Converting to HIT2 clears `team`, so **38 of the 42 HIT2 sellers have a NULL latest team and fall
out of `hitsMap` entirely.** Only 4 HIT2 sellers are inside the 240-seller book.

So a view built on `hitsMap` is a **HIT1 view**, not a 1k-5k view, regardless of what it's called.
To cover both, take the union with `hit2 = 1` straight from card 10453 (that's what
`google_sellers_refresh.py` does: 240 ∪ 42 = **278**, overlap 4). It is *not* that HIT2 sellers lack
data — 33 of the 42 have a google ad account.

`hit_master_data` is a full historical dump; never treat "has a `hit_year_week`" as "is HIT1".

## Roles — GL, GM, CL (this trips everyone)

| Label in the UI | What it actually is | Source |
|---|---|---|
| **GL** | the **growth-consultant-level owner** | `hitsMap.gc` = card 7753 `growth_consultant_name`, falling back to `growth_lead_name` |
| **GM** | growth manager | `hitsMap.gm` → 7753 `growth_manager_name` |
| **CL** | **Category Lead** | `nushop.seller_managers` `manager_type LIKE '%category_lead%'` → `nushop.users`; same derivation as card 10181's `cl_name` |

- **Do not wire GL to the literal `google_growth_lead` manager_type.** Only ~6 of the book has one;
  doing that made a table read 112/118 "Unassigned". Card 7753's `growth_lead_name` is `'-'` for the
  entire 1k-5k book too.
- Every `bev_refresh` detail row emits `'gl': team[sid]['gc']`. Match that or your table disagrees
  with every other drilldown.
- Deriving GM from the **GL** (via `gc2gm_all`) rather than per-seller keeps a GL's whole book inside
  one GM instead of splitting it.
- Normalise whitespace and treat the literal `'Unassigned'` as absent — see
  `metrics-tracker-pipeline`.

## ARR

**Per seller per month:**

```
ARR = Σ(daily total_profit) × 365 / (80 × days_in_period)
    ≡ Σ(daily arr_overall) / days_in_period          # card 10469's arr_overall IS total_profit*365/80
```

```
days_in_period = calendar days in that month                       (a completed month)
               = (current ISO-week start − month start) in days     (the current month)
rows dated inside the current ISO week are EXCLUDED
```

Dividing by *days-with-data* instead inflates every cell 10–20% (a seller live 20 of 31 days looked
~55% better) and breaks comparability with the targets, which are set on this basis.

### The card-11020 denominator changed on 2026-08-03 — the big one

```sql
-- BEFORE: AVG skips NULLs, so each age divided by sellers-with-ARR-at-that-age
ROUND(AVG(CASE WHEN cohort_month_num = a THEN arr END), 0)
-- NOW: every age divides by the cohort's own seller_count
SAFE_DIVIDE(ROUND(SUM(CASE WHEN cohort_month_num = a THEN arr END), 0), COUNT(DISTINCT seller_id))
```

A cohort member with **no ARR at that age counts as 0** rather than being dropped, so later ages
fall as sellers churn instead of holding up on a shrinking base. Feb-26 M3 went 2,226 → **1,484**;
M6 3,856 → **1,551**.

Consequences to preserve in any new cohort view:
- **A cell is no longer the plain average of its drilldown list.** The list holds the *k* sellers
  with ARR; the divisor is *n*, the cohort size. Every cohort drilldown title spells this out:
  `avg 1,484 = ARR / 15 cohort sellers (10 had ARR at this age)`, and each cell carries a `k of n`
  sub-line. Keep that, or the numbers look wrong.
- The **ARR formula did not change** — only the denominator.
- The **TARGET vector did not change**.
- **Card 12072 (Revenue cohort) was NOT updated** and still returns `AVG`. `bev_refresh` therefore
  recomputes its cells from the card-12186 seller detail over the same cohort `seller_count`;
  without that the HIT and Revenue tables sit on two different denominators. That block becomes a
  no-op if 12072 is ever fixed upstream.

Per-age target vector (the `TARGET` row of card 11020), age capped at M5:

| M0 | M1 | M2 | M3 | M4 | M5+ |
|---|---|---|---|---|---|
| 1,859 | 3,668 | 4,133 | 4,480 | 4,748 | 4,647 |

## ARR tranches (sections 3d / 3e)

Both sections rank a cohort's sellers by ARR and split into Top 20% / Mid 20% / Bottom 60%, against
**the cohort's own seller count** — the same whole-cohort convention as above.

| Rule | Split |
|---|---|
| **20% of sellers** | `t = round(cohort_n × 0.2)`; top = ranked[0:t], mid = [t:2t], bottom = the rest. Top/mid divide by `t`, bottom by `max(n − 2t, 1)` |
| **20% of ARR** | walk the ARR-ranked list cutting at 20% / 40% of the cohort's total ARR; each group divides by its own size |

- **3d re-ranks at every age** — a column answers "who is top 20% *right now*".
- **3e freezes the rank at M1** — the cohort is ranked once on M1 ARR and that same seller set is
  reported at every age, so membership never changes across a row. Implemented as a `freezeAt` prop
  on the same component, not a copy. At M1 the two tables are **identical by construction** — that's
  the invariant to check after any change.
- An age the cohort **hasn't reached** is blank; a **zero** means the age has data and none of the
  frozen tranche earned any of it (Feb-26's M1 winners really did decay to ~0 by M6 — that's signal).

## Google assets, live, spending

| Metric | Definition |
|---|---|
| **Total assigned** | every 1k-5k seller on the book |
| **Total google assets created** | a `google_ad_account_id` exists — non-empty `nushop.userprofiles.google_ad_accounts` |
| **Acceptance** | assets created ÷ total assigned |
| **Live sellers** | lifetime google spend > **10** (card 7401 `total_marketing_spend_with_tax`) |
| **Yesterday spending** | google spend yesterday > **1** |
| **Spend/Live** | sellers spending yesterday ÷ live sellers (**a rate, not rupees**) |
| **3K** | google spend last 7 days > **3540** |
| **3K Spend/Live** | 3K sellers ÷ live sellers |

All pooled — the Total row divides the summed numerator by the summed denominator, never averages
the per-GM rates.

> **"Assets created" is 119 on the current book, not 146.** Verified four ways: `scaling_data.ga`,
> card 7401, card 7275, and the raw `userprofiles.google_ad_accounts` array all return the same set,
> every one with a populated id (no created-but-unlinked gap). The ceiling for *any* google asset
> (ads account ∪ merchant id ∪ tag id) is **123**. A circulated 146-seller list turned out to include
> 31 sellers with no google asset at all, 2 with only merchant-id/tag, and 1 not on the book — while
> omitting 7 that do qualify. On the wider HIT1+HIT2 roster the count is **150**.

## Spend / Live (the older, separate definitions)

Two variants; both correct for their purpose.

**A · Snapshot (KPI cards, "yesterday")** — from `scaling_data.json` (`my`/`gy`/`gt`/`ga`):

| Channel | Numerator ("spending") | Denominator ("live") |
|---|---|---|
| Meta | `my > 1` | **all assigned** 1k-5k sellers |
| Google | `gy > 50` | sellers **Google-live** |
| Blended | `my > 1 OR gy > 50` | all assigned |

The asymmetry is deliberate: a seller not spending on Meta is a failure, but a seller with no Google
account can't be faulted; Google also uses a ₹50 floor because of trickle spends.

**B · Day-wise weighted (Target vs Achievement, incentives)**

```
Spend/Live % = Σ(seller-days with channel spend > 0) ÷ (settled days in month × live sellers)
```
Built from card 10469 via `perfByDate`. "Settled day" = a past day where booked spend > 0.

## Google-live

```
has google_ad_account_id  AND  lifetime google spend > 10
```
Authority: **card 7401** — the same source as Spend/Live and the go-live multiplier, so numbers tie.

## Go-live multiplier (Google)

```
Google Golive % = (sellers Google-live) ÷ (sellers assigned)     per GC, rolled up pooled to GM
```
| Golive % | Effect |
|---|---|
| **< 50%** | **0 — hard gate, the entire incentive becomes 0** |
| 50–65% | 1.0× |
| > 65% | 1.25× |

`gGoliveDelta = ceil(0.5 × gAcc) − gLive` is the actionable "how many more must go live". Reads a
**current snapshot**, not a month-end freeze.

## Google PNL buckets — mutually exclusive tiers

Weekly google PNL from card 5207, spend gate **3540** (the canonical `bucket_refresh` thresholds):

```
health     = w1_spend > 3540 and w1_pnl > -20
potential  = w1_spend > 3540 and w1_pnl > 5
objective  = w1_spend > 3540 and w2_spend > 3540 and w1_pnl > 5 and w2_pnl > 5
subjective = same as objective but w2_pnl only > 3      (computed, NOT displayed)
```

These **nest**: `objective ⊆ potential ⊆ health`. Reported as **exclusive tiers** so a seller is
counted once, in its best tier:

```
Objective      = objective
Potential      = potential AND NOT objective
Bucket Health  = health    AND NOT potential AND NOT objective
```
The three exclusive counts must sum back to the raw `health` count — that's the check.
HIT1 today: raw 27/18/5 → **9 / 13 / 5**.

## Target vs Achievement (GM-wise, 1k-5k)

**ARR target = Σ per-age targets of running sellers.** For each seller in the GM's cohort, age =
months since their HIT1 month, look up the per-age target, sum.

- **ARR achieved** = Σ those sellers' actual monthly ARR (card 7336)
- **HIT2 target** — per GC per month from the **Collated sheet**; **HIT2 achieved** = `hit2=1` count
  for the report month, credited to the owner **at conversion** (card 10992)
- **`qualified`** = HIT2 achieved ≥ HIT2 target **AND** ARR achieved ≥ **85%** of ARR target
- **`delta`** = `max(0, 0.85 × arrT − arrA)` — gap to the 85% gate, not to 100%
- **Churn column** uses the stricter legacy rule: revenue spend ≥ **₹11,800** AND no spend > **21 days**

TvA **sums** targets and ARR over running sellers, so the card-11020 denominator change does **not**
touch it. Verified: `arrT`/`arrA` still equal the sum of their own drilldowns.

### The HIT2 freeze (critical)

When a seller converts HIT1 → HIT2 they graduate out of 1k-5k, so at conversion we freeze:
1. **Conversion Friday** = Friday of `hit2_year_week` (earliest one)
2. **Frozen ARR** = latest daily `arr_overall` (card 10469) on/before that Friday
3. **Frozen age** — target stops accruing from the conversion month
4. **Frozen owner** — credit to the GC/GM who owned them at conversion (card 10992)

Freeze applies only when `HIT1 month ≤ conversion month ≤ report month`; a HIT2 week before the HIT1
month is bad data → seller stays active. Detail rows carry `frozen` / `freezeMonth`.

## Incentive %

```
base × arrMult × churnMult × metaMult × googMult × goliveMult      (0 if ANY gate fails)
```
| Factor | Rule |
|---|---|
| base (HIT2 attainment) | ≥100% → 25 · 50–99% → 15 · <50% → **0** |
| ARR | gate ≥85% of target, else 0; ≥2× → 2.0 · ≥1.5× → 1.25 · else 1.0 |
| Churn | 0 churns → 1.0 · 1 → 0.5 · ≥2 → **0** |
| Meta S/L | <60% → **0** · 60–80% → 1.0 · >80% → 1.25 |
| Google S/L | <65% → **0** · 65–75% → 1.0 · >75% → 1.2 |
| Google golive | <50% → **0** · 50–65% → 1.0 · >65% → 1.25 |

Task-compliance/TS and NPS gates are not available per-GM and are **not** applied.

## Churn

**Base rule (cards 4118 / 11771):** churned = no week with `marketing_spend_tax_ ≥ 1000` in the last
**21 days**. Weekly grain. The **churn week** = that last spend week.

**Cohort churn (card 12159, the current one):**
- eligibility: ≥21 days since handover, where **handover = Friday of the hit week**
- **churn age** = `ROUND((churn_week − hit_week) / 4.5)` — week-based months, *not* calendar diff
- **cohort month** = month of the **Monday of the hit week**
- Revenue sellers are anchored on their **first REVENUE-team week**
- buckets are mutually exclusive here; `M12+` collects everything beyond 12 months

Cumulative churn curves must be **monotonic** and are clipped at each segment's last month with
actual churn (no flat extrapolated tail). Chart drilldowns are **incremental** (churned *at* that
month) so a seller appears in exactly one point.

## Spend & ARR trend (the day-on-day chart)

Spend and ARR aggregate **differently, deliberately**:

```
spend  is a flow            -> a bucket is the SUM of its days
ARR    is an annualised rate -> a bucket is the MEAN of its daily values
```
Summing daily ARR across a week would produce a number 7× the real rate. The window "avg daily ARR"
stat is averaged over **days**, not buckets, so it doesn't drift when the day/week toggle flips (the
edge ISO weeks are partial). Partial ISO weeks are flagged, not hidden.

## S/GMV and the funnel

```
S/GMV % = spend ÷ GMV × 100      (LOWER is better; 100% = spent ₹1 to make ₹1 of top-line)
```
Always **pooled**: `Σspend / ΣGMV`. Quote *pooled* to finance and *median* for "a typical adset" —
means are wrecked by a few catastrophic outliers.

Decomposition: **CPM** (cost of reach) → **CTR** (does the asset earn a click) → **C2PR**
(click→purchase) → **AOV**. Isolating the broken step tells you what to fix.

GMV sources: **Meta-attributed** below seller level; **true platform GMV** (`nushop.orderitems`)
only at seller level — use it as the sanity check.

## Cohort analyses

| Table | Cohort row | W0 / M0 | Cells |
|---|---|---|---|
| ARR Cohort (card 11020) | HIT month | M0 = HIT month | avg ARR ÷ **cohort seller count**; green = at/above target |
| ARR tranches 3d / 3e | HIT month | M0 = HIT month | tranche avg ARR; 3e freezes the rank at M1 |
| Cohort Analysis 1k-5k (section 6, card 11840) | HIT month | W0 = HIT week | `t` present, `s` spending (>0), `g` ≥₹3,000 |
| Cohort Analysis 1k-5k **Google** (card 12264) | month of **first google spend** | W0 = week of first google spend | same; cohorts from **Mar-26**; HIT1/HIT2/both/Revenue toggle |
| Churn cohort (card 12159) | hit-week month | M0 = same month | churn counts by age |

**3K Retention = ≥3K sellers at Wn ÷ spending sellers at W0.** Spend threshold ₹3,000/week; the
separate troubleshoot/scaling threshold is ₹3,540 (`SPEND3K`).

Google is switched on **weeks after** HIT (often months) — that's why the Google cohort re-anchors on
first google spend rather than the HIT week.

## Golive Creative Testing — the A/B

| Grouping | What it is |
|---|---|
| **ITT (intent-to-treat)** | card 12207's `campaign_type`, from `MOD(ABS(FARM_FINGERPRINT(seller_id)),2)`. **Confirmed to be the real assignment** — card 11736 carries 94 hand-recorded locked values and the hash reproduces 79/79 of those that reached go-live. Unbiased comparison. |
| **Per-protocol** | what was actually executed, from the **adset name**. Biased by self-selection, but the only way to split arm-vs-arm inside a B seller. |

The gap between them (**77/85 = 91%** agree) is **seller non-compliance**, not assignment error —
e.g. sellers assigned to catalogue who never built a catalogue adset.

Arm keywords: `catalog` / `cat.` / `all product` → Catalogue · `banner` / `video` / `creative` /
`ugc` → Creative · both → Both · neither → Unclassified (a naming-hygiene gap, ~9 adsets).

Cohort funnel from card 11736: **201 A2H-qualified, 100 live** — the tracker covers the live ones.

## Other constants

- `COHORT_EXCLUDE` — 26 hardcoded seller ids excluded from the 1k-5k cohort (mirrors card 10881)
- ARR cohort membership: HIT month ≥ `202510`, non-good, HITS-or-HIT2
- Canonical 1k-5k GL list comes from the **Collated sheet**; a seller under an unlisted GC is dropped
  from both target and achievement

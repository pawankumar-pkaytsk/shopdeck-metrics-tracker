---
name: metrics-tracker-data-model
description: Business metric definitions for the Shopdeck Metrics Tracker — 1k-5k, HIT1/HIT2/Revenue buckets, Spend/Live, Google-live, go-live multiplier, Target vs Achievement, ARR cohorts, churn, S/GMV and incentives. Use before computing, changing or explaining any of these numbers so the definition matches the rest of the dashboard.
---

# Metric definitions

Use these verbatim. Inventing a variant makes a new view disagree with every existing one.

## Populations

| Bucket | Definition (`csv_upload.hit_master_data`, card 10453) |
|---|---|
| **1k-5k team** | `ts_data.json → hitsMap` sellers with `good = 0` (i.e. `good_seller` unset). ~216. |
| **HIT1** | `team = 'HITS' AND good_seller IS NULL` → ~205 sellers |
| **HIT2** | `hit2 = 1` |
| **HIT1 + HIT2** | either of the above — **HIT1 and HIT2 overlap by design** in this convention |
| **Revenue** | `good_seller IS NULL AND team is not 'HITS' AND hit2 is not 1` |

These are the card-11815 variant predicates (see `bev_refresh.py`, `_G15UNI`), used by the Google
weekly table and the Google cohort table. **Match them for any new bucket split.**

> Caveat: the churn cohort (cards 12142/12159) deliberately makes the three **mutually exclusive**
> (HIT2 excluded from HIT1, HITS sellers excluded from Revenue) because a churn cohort must not
> double-count. Two conventions coexist on purpose — check which one a view uses.

`hit_master_data` is a full historical dump; never treat "has a `hit_year_week`" as "is HIT1".

## Spend / Live

Two variants; both correct for their purpose.

**A · Snapshot (KPI cards, "yesterday")** — from `scaling_data.json` (`my`/`gy`/`gt`/`ga`, built
from card 2787 meta + card 7401 google):

| Channel | Numerator ("spending") | Denominator ("live") |
|---|---|---|
| Meta | `my > 1` (meta yesterday spend > ₹1) | **all assigned** 1k-5k sellers |
| Google | `gy > 50` (google yesterday spend > ₹50) | sellers **Google-live** |
| Blended | `my > 1 OR gy > 50` | all assigned |

The asymmetry is deliberate: a seller not spending on Meta is a failure, but a seller with no
Google account can't be faulted; Google also uses a ₹50 floor because of trickle spends.

**B · Day-wise weighted (Target vs Achievement, incentives)**

```
Spend/Live % = Σ(seller-days with channel spend > 0) ÷ (settled days in month × live sellers)
```
Built from card 10469 via `perfByDate`. "Settled day" = a past day where booked spend > 0.
Exposed as `metaSLPct` / `gSLPctDW`, with `slDays` and per-seller detail.

## Google-live

```
has google_ad_account_id  AND  lifetime google spend > 10
```
Authority: **card 7401** (`google_ad_account_id`, `total_marketing_spend_with_tax`) — the same
source as Spend/Live and the go-live multiplier, so numbers tie across views.
(`bev_refresh` uses `gt > 1`; empirically `>1` and `>10` select the identical 1,337 sellers.)

## Go-live multiplier (Google)

```
Google Golive % = (sellers Google-live) ÷ (sellers assigned)     per GC, rolled up pooled to GM
```
| Golive % | Effect |
|---|---|
| **< 50%** | **0 — hard gate, the entire incentive becomes 0** |
| 50–65% | 1.0× |
| > 65% | 1.25× |

`>= 50` passes the gate; the bonus needs `> 65`. `gGoliveDelta = ceil(0.5 × gAcc) − gLive` is the
actionable "how many more must go live". GM rollup sums numerator and denominator, then divides.
It reads a **current snapshot**, not a month-end freeze.

## Target vs Achievement (GM-wise, 1k-5k)

**ARR target = Σ per-age targets of running sellers.** For each seller in the GM's cohort, take
age = months since their HIT1 month, look up the per-age target, and sum.

Per-age target vector (the `TARGET` row of card 11020), age capped at M5:

| M0 | M1 | M2 | M3 | M4 | M5+ |
|---|---|---|---|---|---|
| 1,859 | 3,668 | 4,133 | 4,480 | 4,748 | 4,647 |

- **ARR achieved** = Σ those sellers' actual monthly ARR (card 7336)
- **HIT2 target** — per GC per month from the **Collated sheet**; **HIT2 achieved** = `hit2=1`
  count for the report month, credited to the owner **at conversion** (card 10992)
- **`qualified`** = HIT2 achieved ≥ HIT2 target **AND** ARR achieved ≥ **85%** of ARR target
- **`delta`** = `max(0, 0.85 × arrT − arrA)` — gap to the 85% gate, not to 100%
- **Churn column** uses the stricter legacy rule: revenue spend ≥ **₹11,800** AND no spend > **21 days**

### The HIT2 freeze (critical)

When a seller converts HIT1 → HIT2 they graduate out of 1k-5k, so at conversion we freeze:
1. **Conversion Friday** = Friday of `hit2_year_week` (earliest one)
2. **Frozen ARR** = latest daily `arr_overall` (card 10469) on/before that Friday
3. **Frozen age** — target stops accruing from the conversion month
4. **Frozen owner** — credit to the GC/GM who owned them at conversion (card 10992)

Freeze applies only when `HIT1 month ≤ conversion month ≤ report month`; a HIT2 week before the
HIT1 month is bad data → seller stays active. Detail rows carry `frozen` / `freezeMonth`.

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

**Base rule (cards 4118 / 11771):** churned = no week with `marketing_spend_tax_ ≥ 1000` in the
last **21 days**. Weekly grain. The **churn week** = that last spend week.

**Cohort churn (card 12159, the current one):**
- eligibility: ≥21 days since handover, where **handover = Friday of the hit week**
- **churn age** = `ROUND((churn_week − hit_week) / 4.5)` — week-based months, *not* calendar diff
- **cohort month** = month of the **Monday of the hit week**
- Revenue sellers are anchored on their **first REVENUE-team week**
- buckets are mutually exclusive here; `M12+` collects everything beyond 12 months

Cumulative churn curves must be **monotonic** and are clipped at each segment's last month with
actual churn (no flat extrapolated tail). Chart drilldowns are **incremental** (churned *at* that
month) so a seller appears in exactly one point.

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
| ARR Cohort (card 11020) | HIT month | M0 = HIT month | avg ARR; green = at/above target |
| Cohort Analysis 1k-5k (section 6, card 11840) | HIT month | W0 = HIT week | `t` present, `s` spending (>0), `g` ≥₹3,000 |
| Cohort Analysis 1k-5k **Google** (card 12264 / `cohort_google_refresh.py`) | month of **first google spend** | W0 = **week of first google spend** | same; cohorts from **Mar-26**; HIT1/HIT2/both/Revenue toggle |
| Churn cohort (card 12159) | hit-week month | M0 = same month | churn counts by age |

**3K Retention = ≥3K sellers at Wn ÷ spending sellers at W0.**
Spend threshold ₹3,000/week; the separate troubleshoot/scaling threshold is ₹3,540 (`SPEND3K`).

Google is switched on **weeks after** HIT (often months) — that's why the Google cohort re-anchors
on first google spend rather than the HIT week.

## Other constants

- `COHORT_EXCLUDE` — 26 hardcoded seller ids excluded from the 1k-5k cohort (mirrors card 10881)
- ARR cohort membership: HIT month ≥ `202510`, non-good, HITS-or-HIT2
- Canonical 1k-5k GL list comes from the **Collated sheet**; a seller under an unlisted GC is
  dropped from both target and achievement

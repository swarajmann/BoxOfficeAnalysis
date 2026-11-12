"""
Builds the analysis tables for the Power BI dashboard from your collected data.
No internet needed: it only reads the CSVs already in this folder.

Inputs (same folder):
    daily_box_office.csv   from bom_daily.py combine          (required)
    films.csv              from bom_films.py build            (recommended)
    ticket_prices.csv      columns: year, avg_ticket_price    (optional)

Usage:
    python bom_analysis.py
    python bom_analysis.py --clash-min 2500     # theatre cutoff for "wide" clash films

Outputs:
    weekends.csv        one row per film per weekend (Fri-Sun)
    film_metrics.csv    one row per film: opening, legs, run length, tier, era...
    weekend_market.csv  one row per weekend: total market gross, wide openers
    clashes.csv         one row per pair of wide films opening the same or next weekend
    tuesday_lift.csv    one row per film per week: Tuesday vs Monday/Wednesday
"""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

DAILY_CSV = Path("daily_box_office.csv")
FILMS_CSV = Path("films.csv")
PRICES_CSV = Path("ticket_prices.csv")

OUT_WEEKENDS = Path("weekends.csv")
OUT_METRICS = Path("film_metrics.csv")
OUT_MARKET = Path("weekend_market.csv")
OUT_CLASHES = Path("clashes.csv")
OUT_TUESDAY = Path("tuesday_lift.csv")


# ---------- Labels ----------
def era_of(dates):
    y = dates.dt.year
    return np.select([y <= 2019, y <= 2021], ["Pre-COVID (2017-19)", "Excluded (2020-21)"],
                     default="Post-COVID (2022+)")


def season_of(dates):
    m = dates.dt.month
    return np.select(
        [m.isin([1, 2]), m.isin([3, 4]), m.isin([5, 6, 7, 8]), m.isin([9, 10])],
        ["Winter", "Spring", "Summer", "Fall"], default="Holiday (Nov-Dec)")


def tier_of(theaters):
    return np.select([theaters >= 3000, theaters >= 1000],
                     ["Wide (3,000+)", "Mid-wide (1,000-2,999)"], default="Limited (<1,000)")


def friday_of_weekend(dates):
    """Fri/Sat/Sun -> that weekend's Friday."""
    return dates - pd.to_timedelta(dates.dt.dayofweek - 4, unit="D")


# ---------- Loading ----------
def load_daily():
    d = pd.read_csv(DAILY_CSV, parse_dates=["date"])
    d = d.dropna(subset=["release_id"])
    for c in ["daily_gross", "theaters", "gross_to_date", "days_in_release"]:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    return d


def load_films():
    if not FILMS_CSV.exists():
        print("films.csv not found: segments and genres will show as Unknown.")
        return None
    f = pd.read_csv(FILMS_CSV, dtype={"release_id": str})
    keep = [c for c in ["release_id", "tconst", "distributor", "release_type", "mpaa", "audience_segment",
                        "primary_genre", "genres", "runtime_min", "imdb_rating", "imdb_votes"]
            if c in f.columns]
    return f[keep]


def load_prices():
    if not PRICES_CSV.exists():
        return None
    p = pd.read_csv(PRICES_CSV)
    p["avg_ticket_price"] = pd.to_numeric(p["avg_ticket_price"], errors="coerce")
    return p.dropna(subset=["avg_ticket_price"])


# ---------- Openings ----------
def find_openings(d):
    d = d.sort_values("date")
    o = d.groupby("release_id").agg(
        title=("title", "first"),
        distributor_daily=("distributor", "first"),
        first_date_in_data=("date", "min"),
        last_date_in_data=("date", "max"),
        min_days_in_release=("days_in_release", "min"),
        widest_theaters=("theaters", "max"),
        total_gross=("gross_to_date", "max"),
        days_in_data=("date", "nunique"),
    )
    o["opening_date"] = o["first_date_in_data"] - pd.to_timedelta(
        o["min_days_in_release"].fillna(1) - 1, unit="D")
    # Opening is usable only if we have the film from (close to) day 1
    o["opening_in_data"] = o["min_days_in_release"].fillna(99) <= 3

    dow = o["opening_date"].dt.dayofweek
    shift = np.where(dow <= 4, 4 - dow, -(dow - 4))   # Mon-Fri opening -> that Friday
    o["opening_weekend_start"] = o["opening_date"] + pd.to_timedelta(shift, unit="D")

    # Run length: last day the film still had 25%+ of its widest theatre count
    d2 = d.merge(o[["widest_theaters"]], left_on="release_id", right_index=True)
    alive = d2[d2["theaters"] >= 0.25 * d2["widest_theaters"]]
    o["last_day_25pct"] = alive.groupby("release_id")["date"].max()
    o["run_length_days"] = (o["last_day_25pct"] - o["opening_date"]).dt.days + 1

    # Run finished = on its last recorded day it was below 25% of widest
    last_rows = d.loc[d.groupby("release_id")["date"].idxmax(), ["release_id", "theaters"]]
    o["last_theaters"] = last_rows.set_index("release_id")["theaters"]
    o["complete_run"] = o["last_theaters"] < 0.25 * o["widest_theaters"]
    return o


# ---------- Weekends ----------
def build_weekends(d, o):
    wk = d[d["date"].dt.dayofweek >= 4].copy()
    wk["weekend_start"] = friday_of_weekend(wk["date"])
    w = wk.groupby(["release_id", "weekend_start"]).agg(
        weekend_gross=("daily_gross", "sum"),
        days_reported=("date", "nunique"),
        theaters=("theaters", "max"),
    ).reset_index()

    w = w.merge(o[["opening_weekend_start"]], left_on="release_id", right_index=True)
    w["weekend_num"] = (w["weekend_start"] - w["opening_weekend_start"]).dt.days // 7 + 1
    w = w[w["weekend_num"] >= 1].sort_values(["release_id", "weekend_num"])

    prev = w.groupby("release_id")[["weekend_num", "weekend_gross", "theaters"]].shift(1)
    consecutive = (w["weekend_num"] - prev["weekend_num"]) == 1
    w["pct_change_gross"] = np.where(consecutive, w["weekend_gross"] / prev["weekend_gross"] - 1, np.nan)
    w["theater_change"] = np.where(consecutive, w["theaters"] - prev["theaters"], np.nan)
    w["per_theater_avg"] = w["weekend_gross"] / w["theaters"]
    w = w.replace([np.inf, -np.inf], np.nan)
    return w.drop(columns=["opening_weekend_start"])


# ---------- Film metrics ----------
def build_metrics(o, w, films, prices, clash_min, light_comp=15_000_000, min_group=15):
    m = o.copy()

    w1 = w[w["weekend_num"] == 1].set_index("release_id")
    w2 = w[w["weekend_num"] == 2].set_index("release_id")
    m["opening_weekend_gross"] = w1["weekend_gross"]
    m["opening_theaters"] = w1["theaters"]
    m["w2_gross"] = w2["weekend_gross"]
    m["w2_theaters"] = w2["theaters"]
    m["w2_drop_pct"] = w2["pct_change_gross"]
    m["w2_theater_change"] = w2["theater_change"]

    # Opening figures only count when we saw the film open
    for c in ["opening_weekend_gross", "opening_theaters", "w2_gross", "w2_theaters",
              "w2_drop_pct", "w2_theater_change"]:
        m.loc[~m["opening_in_data"], c] = np.nan

    m["opening_pta"] = m["opening_weekend_gross"] / m["opening_theaters"]
    m["w2_theater_change_pct"] = m["w2_theater_change"] / m["opening_theaters"]
    m["legs"] = m["total_gross"] / m["opening_weekend_gross"]
    m["opening_share_of_total"] = m["opening_weekend_gross"] / m["total_gross"]

    m["tier"] = tier_of(m["opening_theaters"].fillna(m["widest_theaters"]))
    m["era"] = era_of(m["opening_date"])
    m["season"] = season_of(m["opening_date"])
    m["year"] = m["opening_date"].dt.year
    m["month"] = m["opening_date"].dt.month

    m["opening_pta_vs_tier_median"] = m["opening_pta"] / m.groupby(["tier", "era"])["opening_pta"].transform("median")

    # Did a wide film open on this film's second weekend?
    wide_open = m.loc[m["opening_in_data"] & (m["opening_theaters"] >= clash_min), "opening_weekend_start"]
    openers_per_weekend = wide_open.value_counts()
    w2_start = m["opening_weekend_start"] + pd.Timedelta(days=7)
    m["w2_wide_openers"] = w2_start.map(openers_per_weekend).fillna(0).astype(int)

    m = m.reset_index()
    if films is not None:
        m = m.merge(films, on="release_id", how="left")
        if "distributor" in m.columns:
            m["distributor"] = m["distributor"].fillna(m["distributor_daily"])
        else:
            m["distributor"] = m["distributor_daily"]
    else:
        m["distributor"] = m["distributor_daily"]
    for c in ["audience_segment", "primary_genre", "genres", "mpaa", "release_type"]:
        if c not in m.columns:
            m[c] = pd.NA
    m["audience_segment"] = m["audience_segment"].fillna("Unknown")
    # Films not in films.csv (under the theatre cutoff) are treated as standard releases
    rerelease = m["title"].astype(str).str.contains(r"re-?release|anniversary", case=False, regex=True)
    m["release_type"] = m["release_type"].fillna(pd.Series(np.where(rerelease, "Re-release", "Standard"),
                                                           index=m.index))

    # Size of the competition opening on each film's second weekend
    std_wide = m[m["opening_in_data"] & (m["opening_theaters"] >= clash_min) & (m["release_type"] == "Standard")]
    comp_by_weekend = std_wide.groupby("opening_weekend_start")["opening_weekend_gross"].sum()
    m["w2_competitor_opening_gross"] = (m["opening_weekend_start"] + pd.Timedelta(days=7)).map(comp_by_weekend).fillna(0)

    # Typical second-weekend drop with little or no competition.
    # Uses the narrowest group with enough films: tier+segment+era, then tier+era, then era.
    clean = m[(m["w2_competitor_opening_gross"] < light_comp) & m["w2_drop_pct"].notna()
              & (m["release_type"] == "Standard")]
    m["baseline_w2_drop_pct"] = np.nan
    m["baseline_group"] = pd.NA
    m["baseline_n"] = np.nan
    for keys, label in [(["tier", "audience_segment", "era"], "tier + segment + era"),
                        (["tier", "era"], "tier + era"),
                        (["era"], "era")]:
        stats = clean.groupby(keys)["w2_drop_pct"].agg(["median", "count"])
        stats = stats[stats["count"] >= min_group]
        joined = m[keys].merge(stats, left_on=keys, right_index=True, how="left")
        fill = m["baseline_w2_drop_pct"].isna() & joined["median"].notna().values
        m.loc[fill, "baseline_w2_drop_pct"] = joined.loc[fill.values, "median"].values
        m.loc[fill, "baseline_n"] = joined.loc[fill.values, "count"].values
        m.loc[fill, "baseline_group"] = label

    if prices is not None and len(prices):
        m = m.merge(prices[["year", "avg_ticket_price"]], on="year", how="left")
        m["opening_admissions"] = m["opening_weekend_gross"] / m["avg_ticket_price"]
        m["total_admissions"] = m["total_gross"] / m["avg_ticket_price"]
    else:
        print("ticket_prices.csv not found or empty: admissions columns skipped.")

    m = m.drop(columns=["distributor_daily", "min_days_in_release", "last_day_25pct", "last_theaters"])
    return m.replace([np.inf, -np.inf], np.nan)


# ---------- Market by weekend ----------
def build_market(d, m, clash_min):
    wk = d[d["date"].dt.dayofweek >= 4].copy()
    wk["weekend_start"] = friday_of_weekend(wk["date"])
    mk = wk.groupby("weekend_start").agg(
        total_gross=("daily_gross", "sum"),
        films_playing=("release_id", "nunique"),
    ).reset_index()

    wide = m[m["opening_in_data"] & (m["opening_theaters"] >= clash_min)]
    mk["wide_openers"] = mk["weekend_start"].map(wide["opening_weekend_start"].value_counts()).fillna(0).astype(int)
    mk["era"] = era_of(mk["weekend_start"])
    mk["season"] = season_of(mk["weekend_start"])
    mk["year"] = mk["weekend_start"].dt.year
    mk["season_median_gross"] = mk.groupby(["era", "season"])["total_gross"].transform("median")
    mk["market_lift_vs_season"] = mk["total_gross"] / mk["season_median_gross"]
    return mk


# ---------- Discount Tuesday lift ----------
def build_tuesday_lift(d):
    """Tuesday gross vs the average of that week's Monday and Wednesday, per film-week.

    Skips weeks where Mon-Wed includes a US federal holiday or the Christmas/New Year
    stretch, and each film's opening week (Monday must be day 4+ of its run).
    """
    from pandas.tseries.holiday import USFederalHolidayCalendar

    x = d[d["date"].dt.dayofweek <= 2].copy()          # Mon, Tue, Wed
    x["week_monday"] = x["date"] - pd.to_timedelta(x["date"].dt.dayofweek, unit="D")
    x["dow"] = x["date"].dt.dayofweek.map({0: "mon", 1: "tue", 2: "wed"})

    g = x.pivot_table(index=["release_id", "week_monday"], columns="dow",
                      values="daily_gross", aggfunc="sum")
    day_of_run = x[x["dow"] == "mon"].set_index(["release_id", "week_monday"])["days_in_release"]
    g = g.join(day_of_run.rename("monday_day_of_run")).reset_index()
    g = g.dropna(subset=["mon", "tue", "wed", "monday_day_of_run"])
    g = g[(g["mon"] > 0) & (g["wed"] > 0) & (g["monday_day_of_run"] >= 4) & (g["monday_day_of_run"] <= 45)]

    # Drop holiday-affected weeks
    hol = USFederalHolidayCalendar().holidays(start=d["date"].min(), end=d["date"].max())
    hol_weeks = set(pd.DatetimeIndex(hol) - pd.to_timedelta(pd.DatetimeIndex(hol).dayofweek, unit="D"))
    md = g["week_monday"].dt.strftime("%m-%d")
    xmas = (md >= "12-18") | (md <= "01-05")
    g = g[~g["week_monday"].isin(hol_weeks) & ~xmas]

    g["week_of_run"] = (g["monday_day_of_run"] - 1) // 7 + 1
    g["tuesday_lift"] = g["tue"] / ((g["mon"] + g["wed"]) / 2) - 1
    g["school_break"] = g["week_monday"].dt.month.isin([6, 7, 8])   # summer: weekdays behave differently
    g["era"] = era_of(g["week_monday"])
    return g.rename(columns={"mon": "mon_gross", "tue": "tue_gross", "wed": "wed_gross"})


# ---------- Clashes ----------
def genre_set(s):
    if not isinstance(s, str):
        return None
    g = {x.strip() for x in s.split(",") if x.strip()}
    return g or None


def build_clashes(m, mk, clash_min):
    cols = ["release_id", "title", "opening_weekend_start", "opening_weekend_gross", "opening_theaters",
            "opening_pta_vs_tier_median", "audience_segment", "genres", "mpaa", "tier", "era",
            "w2_drop_pct", "baseline_w2_drop_pct", "baseline_group", "baseline_n"]
    # Clashes only between standard new releases (no re-releases or events)
    wide = m[m["opening_in_data"] & (m["opening_theaters"] >= clash_min)
             & (m["release_type"] == "Standard")][cols].copy()
    if wide.empty:
        return pd.DataFrame()

    p = wide.merge(wide, how="cross", suffixes=("_a", "_b"))
    gap = (p["opening_weekend_start_b"] - p["opening_weekend_start_a"]).dt.days
    same = (gap == 0) & (
        (p["opening_weekend_gross_a"] > p["opening_weekend_gross_b"])
        | ((p["opening_weekend_gross_a"] == p["opening_weekend_gross_b"]) & (p["release_id_a"] < p["release_id_b"])))
    nxt = gap == 7
    p = p[same | nxt].copy()
    p["clash_type"] = np.where((gap[same | nxt] == 0), "Same weekend", "Next weekend")
    p["clash_weekend"] = p["opening_weekend_start_b"]

    # Audience overlap
    ga = p["genres_a"].apply(genre_set)
    gb = p["genres_b"].apply(genre_set)
    p["genre_overlap"] = [len(a & b) / len(a | b) if a and b else np.nan for a, b in zip(ga, gb)]
    known = (p["audience_segment_a"] != "Unknown") & (p["audience_segment_b"] != "Unknown")
    p["same_segment"] = np.where(known, p["audience_segment_a"] == p["audience_segment_b"], np.nan)
    p["overlap_score"] = np.where(
        p["genre_overlap"].notna() & known,
        0.5 * p["genre_overlap"] + 0.5 * p["same_segment"].astype(float),
        np.where(known, p["same_segment"].astype(float), p["genre_overlap"]))
    p["overlap_level"] = np.select(
        [p["overlap_score"] >= 0.6, p["overlap_score"] >= 0.3, p["overlap_score"].notna()],
        ["High", "Medium", "Low"], default="Unknown")

    # Effect on the film already in theatres (next-weekend clashes only)
    p["a_extra_w2_drop_pts"] = np.where(
        p["clash_type"] == "Next weekend",
        (p["w2_drop_pct_a"] - p["baseline_w2_drop_pct_a"]) * 100, np.nan)

    # Did the weekend grow the market?
    p = p.merge(mk[["weekend_start", "total_gross", "season_median_gross", "market_lift_vs_season"]]
                .rename(columns={"weekend_start": "clash_weekend", "total_gross": "weekend_market_gross"}),
                on="clash_weekend", how="left")

    out = p[[
        "clash_weekend", "clash_type", "era_b",
        "release_id_a", "title_a", "audience_segment_a", "mpaa_a", "genres_a", "opening_weekend_gross_a",
        "release_id_b", "title_b", "audience_segment_b", "mpaa_b", "genres_b", "opening_weekend_gross_b",
        "genre_overlap", "same_segment", "overlap_score", "overlap_level",
        "w2_drop_pct_a", "baseline_w2_drop_pct_a", "baseline_group_a", "baseline_n_a", "a_extra_w2_drop_pts",
        "opening_pta_vs_tier_median_a", "opening_pta_vs_tier_median_b",
        "weekend_market_gross", "season_median_gross", "market_lift_vs_season",
    ]].rename(columns={"era_b": "era"})
    return out.sort_values("clash_weekend").replace([np.inf, -np.inf], np.nan)


# ---------- Main ----------
def main():
    ap = argparse.ArgumentParser(description="Build Power BI analysis tables")
    ap.add_argument("--clash-min", type=int, default=2500,
                    help="opening theatre count for a film to count as a wide clash (default 2500)")
    ap.add_argument("--light-comp", type=float, default=15_000_000,
                    help="week-2 competitor opening gross below this still counts as a clean baseline (default 15M)")
    ap.add_argument("--min-group", type=int, default=15,
                    help="minimum films for a baseline group before falling back to a broader one (default 15)")
    args = ap.parse_args()

    d = load_daily()
    films = load_films()
    prices = load_prices()

    o = find_openings(d)
    w = build_weekends(d, o)
    m = build_metrics(o, w, films, prices, args.clash_min, args.light_comp, args.min_group)
    mk = build_market(d, m, args.clash_min)
    c = build_clashes(m, mk, args.clash_min)
    t = build_tuesday_lift(d)

    w.to_csv(OUT_WEEKENDS, index=False)
    m.to_csv(OUT_METRICS, index=False)
    mk.to_csv(OUT_MARKET, index=False)
    c.to_csv(OUT_CLASHES, index=False)
    t.to_csv(OUT_TUESDAY, index=False)

    print(f"weekends.csv:       {len(w):,} film-weekends")
    print(f"film_metrics.csv:   {len(m):,} films ({int(m['opening_in_data'].sum()):,} with opening in data)")
    wide = m[m["opening_in_data"] & (m["opening_theaters"] >= args.clash_min) & (m["release_type"] == "Standard")]
    print(f"  wide standard films: {len(wide):,} | clean week 2 for baseline: "
          f"{int((wide['w2_competitor_opening_gross'] < args.light_comp).sum()):,}")
    print("  baselines used: " + ", ".join(f"{k} {v:,}" for k, v in m["baseline_group"].value_counts().items()))
    print(f"weekend_market.csv: {len(mk):,} weekends")
    if len(c):
        print(f"clashes.csv:        {len(c):,} pairs "
              f"({(c['clash_type'] == 'Same weekend').sum():,} same weekend, "
              f"{(c['clash_type'] == 'Next weekend').sum():,} next weekend)")
    else:
        print("clashes.csv:        no clash pairs found")
    print(f"tuesday_lift.csv:   {len(t):,} film-weeks (median Tuesday lift {t['tuesday_lift'].median():+.1%})")


if __name__ == "__main__":
    main()

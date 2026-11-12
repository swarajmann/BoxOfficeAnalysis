# Box Office Booking Analytics

How should a cinema allocate screens and showtimes? This project builds a daily box office dataset from scratch and uses it to answer three questions a film buyer faces every week:

1. **Allocation:** Which mid-wide releases opened in too few theatres?
2. **Discount Tuesdays:** Which genres and audiences respond most to Tuesday pricing?
3. **Counter-programming:** When two big films open close together, does sharing an audience hurt?

Built with **Python** (data collection and modelling) and **Power BI** (data model and dashboard).

---

## The data

|                                                                   |                                         |
| ----------------------------------------------------------------- | --------------------------------------- |
| Daily box office rows                                             | 127,922                                 |
| Releases                                                          | 3,552 (2017–2019 and Nov 2021–Nov 2026) |
| Films enriched with distributor, MPA rating, genres and IMDb data | 1,823 (99% matched to IMDb)             |
| Clash pairs analyzed                                              | 1,351                                   |
| Film-weeks in the Tuesday analysis                                | 8,518                                   |

- **Daily domestic (US and Canada) box office per film**, collected from Box Office Mojo's daily charts.
- **Film details** (distributor, MPA rating, runtime, genres, IMDb ID) from each film's Box Office Mojo release page.
- **IMDb ratings and genres** from IMDb's non-commercial datasets, joined on the exact IMDb ID rather than by title.
- **Average ticket prices by year** from The Numbers, to convert gross into admissions.

2020 and beginning/mid 2021 are excluded from comparisons because of pandemic closures. Re-releases and event screenings (concerts, Fathom events) are tagged and kept out of the analyses, since they behave differently from new releases.

> The raw data is **not included** in this repository: Box Office Mojo's terms don't permit redistribution, and IMDb's datasets are licensed for non-commercial personal use only. The scripts below rebuild everything from the original sources.

---

## Findings

### 1. Under-allocated mid-wide releases

Wide Range releases:
<img width="2385" height="1339" alt="image" src="https://github.com/user-attachments/assets/2ebe877e-7b46-403f-98c2-7d3560c90c2a" />
<img width="898" height="323" alt="image" src="https://github.com/user-attachments/assets/0b5e626a-6d6e-473d-8877-1a890e7e1ece" />
<img width="1319" height="575" alt="image" src="https://github.com/user-attachments/assets/aff31cc2-7a8a-4f2f-8f50-54bc0a77a7b4" />


Mid Range releases:
<img width="2364" height="1312" alt="image" src="https://github.com/user-attachments/assets/b1c1def8-5ae5-43bb-9b93-b420cb08e1b2" />
<img width="907" height="328" alt="image" src="https://github.com/user-attachments/assets/f3661130-df14-44e0-a5d7-932aefbd799a" />
<img width="1445" height="572" alt="image" src="https://github.com/user-attachments/assets/561b016e-dbbe-477e-bb88-f485e65f3131" />


A film was flagged as under-allocated when it opened in 1,000–2,999 theatres with **opening demand well above its tier's typical level**, **added theatres in week 2**, and **held better than similar films in its second weekend**. A strong hold signals that demand spilled over from an opening that was too small.

For Mid range releases (1k-2999), there were about 64 movies that were "under allocated" screens, and had an estimated missed gross of $25.97M. 

The flagged films were dominated by **horror** (_Get Out_, _Longlegs_, _Terrifier 3_), **faith-based** titles (_I Can Only Imagine_, _Sound of Freedom_, _Unplanned_) and **culturally specific** films (_BlacKkKlansman_), all audiences that pre-release tracking tends to underestimate.

**Booking implication:** for these kinds of titles, lean toward more showtimes than the distributor's projection suggests.

_Method notes:_ platform releases (for example "Marty Supreme", 6 theatres expanding to 2,000+) are excluded, because a packed opening in a few hand-picked theatres says nothing about how thousands more would perform. At 3,000+ theatres, films are close to the national ceiling, so under-allocation there shows up in screens per theatre, which public data doesn't capture.

### 2. Discount Tuesdays

<img width="2259" height="1342" alt="image" src="https://github.com/user-attachments/assets/83fa6c45-2e30-4c87-9024-b3932adc410c" />


Comparing each film's Tuesday gross with the average of its Monday and Wednesday (excluding holiday weeks and opening weeks), **Tuesday revenue is a median 32% higher** across 8,518 film-weeks. Because Tuesday tickets are discounted, the attendance lift is larger still.

During school weeks, **family films respond most (about +47%)**, while most genres sit between +35% and +41% and **horror gains least (about +33%)**. Families save on several tickets at once, while horror audiences are more price inelastic, and turn up on opening weekend whatever the price.

**Booking implication:** give family titles extra Tuesday showtimes during the school year, and it doesn't necessarily mean reducing Horror showtimes on Tuesdays. But if a case ever should arise where theatres need to make room for a family film on Tuesdays, Horror can be the first place to consider reductions.

### 3. Counter-programming

<img width="1394" height="517" alt="image" src="https://github.com/user-attachments/assets/a6aa28a7-2020-423c-9c8e-a1b5ed9828c4" />


For every pair of wide releases opening the same weekend or one week apart (limited to clashes where both films opened at $20M+, to focus on genuinely big releases), I measured how much the first film's second weekend dropped compared with similar films that faced little competition.

- **Clashes hurt less than expected.** A big opener on a film's second weekend adds only about **2–3 points** to its drop.
- **Genre overlap barely matters** (the trend line is nearly flat), but **audience segment does, in specific combinations:**
  - R-rated films lose most to other R-rated films (**+4.6 pts**, 153 pairs analyzed)
  - PG-13 tentpoles lose most to family films (**+5.1 pts**, 97 pairs analyzed)
  - Family films are the most resilient (**−0.8 pts overall**; they held slightly better than their baseline)
- **Same-weekend clashes rarely grow the market:** the median clash weekend is about **1.0x** a typical weekend in its season. _Barbie_ and _Oppenheimer_ (**2.32x**) were a cultural exception, not the rule.

**Booking implication:** when a family film opens on a PG-13 tentpole's second weekend, shift screens toward it earlier than usual. Genre labels alone are a weak guide to real audience overlap, which is the open question for December 18, 2026, when _Dune: Part Three_ and _Avengers: Doomsday_ open against each other. Current public pre-sales records show a great demand for Avengers, and a very good pre-sales value for Dune: Part 3. Doomsday is expected to beat Endgame's OW record, and Dune is also expected to open at over $100M OW based on current forecasting. 

---

## How it's built

```
Box Office Mojo ──► bom_daily.py ──► daily_box_office.csv ─┐
                                                            ├──► bom_analysis.py ──► weekends, film_metrics,
Box Office Mojo ──► bom_films.py ──► films.csv ────────────┤                         weekend_market, clashes,
IMDb datasets  ───────────┘                                 │                         tuesday_lift
The Numbers    ──► ticket_prices.csv ───────────────────────┘                               │
                                                                                             ▼
                                                                              Power BI (star schema)
```

- **`bom_daily.py`**: pulls one Box Office Mojo date page per day, saves each day separately so long runs can resume, then cleans and combines them.
- **`bom_films.py`**: pulls each release's details, joins IMDb ratings on the exact IMDb ID, tags re-releases and events, and fills missing MPA ratings from other releases of the same film or from a manual overrides file.
- **`bom_analysis.py`**: builds the analysis tables, including weekend numbering from each film's real opening (Wednesday openers are aligned to their first Friday), run metrics, week-2 baselines, every clash pair with audience-overlap scores, and Tuesday lift.

The heavy calculations, such as comparing each film with every other film, previous-weekend comparisons and conditional baselines, happen in Python. Power BI handles the interactive analysis, which keeps the measures simple and the dashboard fast.

**Power BI model:** fact tables (daily, weekends, Tuesday lift) link to a Films dimension and a Date table; a genre bridge table lets one film count under several genres; What-if parameters expose the assumptions behind the allocation estimates (showtimes per day, seats per auditorium, demand threshold, fill factor).

---

## Limitations

- **Domestic means the US and Canada combined.** Canada-only and regional figures aren't public. I would like to analyze how certain regions are stronger in certain genre's, but unfortunately that data is not available to the public.
- **No showtime or screen-count data.** Allocation is measured in theatres, so screens per theatre (where much real allocation happens) isn't visible.
- **Correlation, not causation.** Clash effects, Tuesday lifts and allocation flags describe patterns; other factors (reviews, marketing, holidays) also play a role.
- **Estimates rest on assumptions.** Missed-gross estimates and occupancy use adjustable assumptions, shown as sliders in the dashboard.
- **Average ticket prices** for 2025–2026 are carried forward from 2024 by the source.
- **About 17% of films have no MPA rating** on Box Office Mojo; most are re-releases, events or foreign-language releases, which are excluded from the main analyses.

---

## Reproducing it

Requires Python 3.8+ and Power BI Desktop.

```
pip install requests beautifulsoup4 lxml pandas

python bom_daily.py scrape --start 2017-01-01 --end 2019-12-31
python bom_daily.py scrape --start 2021-11-01
python bom_daily.py retry-failed
python bom_daily.py combine

# Download title.basics.tsv.gz and title.ratings.tsv.gz from datasets.imdbws.com into this folder
python bom_films.py scrape --min-theaters 500
python bom_films.py build

# Add ticket_prices.csv (year, avg_ticket_price) from The Numbers' market summary
python bom_analysis.py
```

The scraper waits 2–6 seconds between requests and is meant for a slow, one-time personal pull. Check the source sites' terms before running it, and don't republish the data.

The `.pbix` file isn't included because Power BI stores a copy of the data inside it. It's available on request.

---

**Swaraj Mann** · Data Analyst

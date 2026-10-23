"""Stage `page`: the public write-up, docs/index.html, rendered from published.json.

Reads cache_dir/publish/ (stage `charts`). Writes docs/index.html and copies the
charts to docs/img/. Every number on the page comes from published.json, so the
page can't drift from the analysis. The page is self-contained: inline CSS,
an inline SVG diagram, local images, no external requests.

Storyline: problem and value -> method -> data -> global findings -> findings
broken down -> conclusion. Each graph is introduced by a sentence, followed by a
short note on how to read it, and bridged to the next one.
"""

import json
import os
import shutil
from datetime import date, datetime
from html import escape
from pathlib import Path

from src import charts
from src import definitions as d


def pct(x: float, digits: int = 0) -> str:
    return f"{x * 100:.{digits}f}%".replace("-", "−")  # a true minus sign, matching ci()


def pts(x: float, digits: int = 1) -> str:
    return f"{x * 100:.{digits}f}"


def ci(pair) -> str:
    lo, hi = pair
    if lo < 0:  # "−26% to +2%" reads better than "-26–2%"
        return f"−{abs(lo) * 100:.0f}% to {'+' if hi > 0 else '−' if hi < 0 else ''}{abs(hi) * 100:.0f}%"
    return f"{lo * 100:.0f}–{hi * 100:.0f}%"


def month(campaign: str) -> str:
    """'2016-06' -> 'June 2016'."""
    return datetime.strptime(campaign, "%Y-%m").strftime("%B %Y")


CSS = f"""
:root {{ --ink:#1f2933; --muted:#5b6670; --rule:#e3e7ec; --who:{charts.BLUE}; --what:{charts.ORANGE}; --best:{charts.BEST}; --worst:{charts.WORST}; --bg:#ffffff; --card:#f6f8fa; }}
* {{ box-sizing: border-box; }}
html {{ color-scheme: light; }}
body {{ margin:0; background:var(--bg); color:var(--ink); font:17px/1.65 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }}
main {{ max-width: 740px; margin: 0 auto; padding: 48px 20px 64px; }}
h1 {{ font-size: 2rem; line-height: 1.25; margin: 0 0 10px; letter-spacing: -0.01em; }}
h2 {{ font-size: 1.3rem; margin: 60px 0 10px; }}
h2 small {{ color: var(--muted); font-weight: 400; font-size: .8rem; display: block; letter-spacing: .06em; text-transform: uppercase; margin-bottom: 2px; }}
h3 {{ font-size: 1.08rem; margin: 36px 0 6px; }}
p, li {{ margin: 0 0 14px; }}
.dek {{ color: var(--muted); font-size: 1.1rem; margin-bottom: 6px; }}
.byline {{ color: var(--muted); font-size: .9rem; border-bottom: 1px solid var(--rule); padding-bottom: 18px; }}
.cards {{ display: grid; grid-template-columns: repeat(var(--n, 3), 1fr); gap: 12px; margin: 16px 0 14px; }}
.card {{ background: var(--card); border-radius: 10px; padding: 14px 16px; }}
.card b {{ display: block; font-size: 1.9rem; line-height: 1.1; }}
.card span {{ display: block; color: var(--muted); font-size: .84rem; margin-top: 6px; line-height: 1.35; }}
.best b {{ color: var(--best); }} .worst b {{ color: var(--worst); }}
figure {{ margin: 14px 0 10px; }}
figure img, figure svg {{ width: 100%; height: auto; display: block; }}
.read {{ border-left: 3px solid var(--rule); padding-left: 14px; }}
.takeaways {{ counter-reset: t; list-style: none; padding: 0; }}
.takeaways li {{ counter-increment: t; position: relative; padding-left: 44px; margin-bottom: 16px; }}
.takeaways li::before {{ content: counter(t); position: absolute; left: 0; top: 0; width: 30px; height: 30px; border-radius: 50%; background: var(--ink); color: #fff; font-weight: 700; text-align: center; line-height: 30px; font-size: .9rem; }}
details {{ background: var(--card); border-radius: 10px; padding: 14px 18px; margin-top: 44px; font-size: .9rem; }}
summary {{ cursor: pointer; font-weight: 600; }}
details p {{ margin: 10px 0 0; }}
@media (max-width: 560px) {{ .cards {{ grid-template-columns: 1fr 1fr; }} h1 {{ font-size: 1.6rem; }} }}
"""


def method_svg() -> str:
    """Diagram: the gap, two what-if questions, and what's left for the campaign."""
    boxes = [
        (charts.BLUE, "1. Same people", "The worst campaign reaches the best campaign's kind of audience"),
        (charts.ORANGE, "2. Same habits", "Those people listen like similar trialists in the best campaign"),
        (charts.GREY, "What's left", "Anything else about that month: offer, season, execution"),
    ]
    parts = [
        '<svg viewBox="0 0 720 230" role="img" aria-label="The conversion gap between the best and worst '
        'campaign is split by two what-if questions, same people and same habits; what is left belongs to the '
        'campaign itself." font-family="inherit">',
        '<rect x="0" y="80" width="190" height="70" rx="10" fill="#1f2933"/>',
        '<text x="95" y="108" text-anchor="middle" fill="#fff" font-size="15" font-weight="700">Conversion gap</text>',
        '<text x="95" y="131" text-anchor="middle" fill="#e3e7ec" font-size="13">best − worst campaign</text>',
    ]
    for i, (color, title, line) in enumerate(boxes):
        y = 10 + i * 75
        parts += [
            f'<path d="M190 115 C 230 115, 230 {y + 32}, 270 {y + 32}" stroke="#c4cad2" stroke-width="2" fill="none"/>',
            f'<rect x="270" y="{y}" width="450" height="64" rx="10" fill="{color}" fill-opacity="0.12" stroke="{color}"/>',
            f'<text x="288" y="{y + 26}" fill="{color}" font-size="15" font-weight="700">{title}</text>',
            f'<text x="288" y="{y + 48}" fill="#1f2933" font-size="13">{line}</text>',
        ]
    parts.append("</svg>")
    return "".join(parts)


def campaign_reading(s: dict) -> str:
    """How to read the campaign's own share; depends on whether its range crosses zero."""
    lo, hi = s["unexplained"]["ci"]
    share = s["unexplained"]["share"]
    if not lo <= 0 <= hi:
        return (
            f"The grey bar, the campaign itself, accounts for {pct(share)}, and its range "
            f"({ci(s['unexplained']['ci'])}) doesn't cross zero: even with the same people behaving the same way, "
            "the campaigns would still differ."
        )
    text = ""
    if share < 0:
        both = s["who"]["share"] + s["what"]["share"]
        text += (
            "The grey bar, the campaign itself, is what's left after both swaps. Giving the worst campaign both "
            f"the best campaign's people and their habits would close slightly more than the whole gap "
            f"({pct(both)}), so the leftover is a small negative number ({pct(share)}). "
        )
    else:
        text += f"The grey bar, the campaign itself, is what's left after both swaps: {pct(share)}. "
    text += (
        f"Its range ({ci(s['unexplained']['ci'])}) includes zero, so the campaign itself made no measurable "
        "difference."
    )
    return text


def render(n: dict, today: date | None = None) -> str:
    today = today or date.today()
    s, b, a, pop = n["split"], n["behavior"], n["audience"], n["population"]
    best, worst = n["best"], n["worst"]
    acts = {r["days"]: r for r in n["activation"]}
    silent, all_days = acts[min(acts)], acts[max(acts)]
    # Where the actual line runs clearly above the same-mix line, heavy listeners are partly a better audience.
    above = [k for k in sorted(acts) if acts[k]["raw"] - acts[k]["adjusted"] > 0.005]
    raw_above = (
        f" And from {above[0]} days up, the grey line sits above the blue: part of the raw climb is who heavy "
        "listeners are." if above else ""
    )
    year = best[:4]
    same_year = [r for r in n["campaigns"] if r["campaign"].startswith(year)]
    others = [r for r in n["campaigns"] if not r["campaign"].startswith(year)]
    adj = sorted(r["adjusted"] for r in same_year)
    spread_raw = max(r["raw"] for r in same_year) - min(r["raw"] for r in same_year)
    spread_adj = adj[-1] - adj[0]
    exception = (
        f" {month(others[0]['campaign'])} is the exception: its audience was better than average, and with an "
        f"average audience it would convert only {pct(others[0]['adjusted'], 1)}."
        if len(others) == 1 else ""
    )
    via, city = a["registered_via"], a["city"]
    traits = {r["trait"]: r for r in n["trait_share"]}
    years = sorted({c[:4] for c in pop["campaigns_modeled"]})
    med = n["listening_medians"]
    minutes_ratio = med["minutes"][worst] / med["minutes"][best]
    n_campaigns = len(pop["campaigns_modeled"])
    alt = n["next_best_pair"]
    best_n = next(r["n"] for r in n["campaigns"] if r["campaign"] == best)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>What drives free-trial conversion</title>
<meta name="description" content="Who a campaign reaches vs what trialists do: a decomposition of {pop['modeled_trials']:,} music-streaming free trials.">
<style>{CSS}</style>
</head>
<body>
<main>
<h1>Who a campaign reaches matters more than what trialists do</h1>
<p class="dek">Why some free-trial campaigns convert far better than others, from {pop['modeled_trials']:,} trials on a music-streaming service.</p>
<p class="byline">Dan Toledano · {today.strftime('%B %Y')}</p>

<h2><small>The problem</small>Some trial campaigns convert {n['best_rate'] / n['worst_rate']:.1f}× better than others</h2>
<p>A free trial is a bet: the service gives away about a week of music and hopes people stay and pay. On KKBox, a music-streaming service, how often that bet paid off depended heavily on the campaign that brought people in.</p>
<div class="cards" style="--n:3">
  <div class="card best"><b>{pct(n['best_rate'], 1)}</b><span>paid within 30 days in the best campaign ({month(best)})</span></div>
  <div class="card worst"><b>{pct(n['worst_rate'], 1)}</b><span>paid in the worst campaign ({month(worst)})</span></div>
  <div class="card"><b>~{n['gap_in_subscribers']:,}</b><span>extra subscribers, out of {n['worst_trials']:,} trialists, had the worst campaign converted like the best</span></div>
</div>
<p>Here a "campaign" is one month of trial sign-ups: the data has no campaign ID, so each month stands in for one.</p>
<p>To close a gap like this, a growth team needs to know where to act. Did the campaigns reach different kinds of people? Then it's a <strong>targeting</strong> problem. Or did similar people have a different first week? Then it's an <strong>onboarding</strong> problem. Suno faces the same question for its own trials; public KKBox data lets us measure the split cleanly and carry the method over.</p>

<h2><small>The method</small>Two "what if" questions</h2>
<p>To separate targeting from onboarding, we ask two questions about the worst campaign:</p>
<figure>{method_svg()}</figure>
<p class="read">The first question measures <strong>who</strong> the campaign reached: signup channel, city, age, gender and how long ago people registered. The second measures <strong>what</strong> they did: how they listened in their first five trial days. Whatever gap neither question explains belongs to the campaign itself.</p>
<p>The answers come from a statistical model that estimates each trialist's chance of paying from three things: their campaign, their profile, and how they listened in their first five days.</p>
<p><strong>How the two swaps work.</strong> For "same people", each trialist in the worst campaign is paired with a randomly chosen trialist from the best campaign and takes on that person's profile. The model then re-estimates their chance of paying, and the rise is the part of the gap that <strong>who</strong> explains. For "same habits", each worst-campaign trialist keeps their own profile, but takes on the first-five-day listening of a best-campaign trialist who signed up through the same channel and is in the same age band. Pairing like with like means we compare the listening of similar people, not of different audiences. In both swaps the trialists stay in the worst campaign, which is what lets the leftover be pinned on the campaign.</p>
<p>To check how certain the answers are, the whole analysis is repeated {pop['bootstrap_replicates']} times on resampled data. The ranges shown below cover 95% of those repeats.</p>

<h2><small>The data</small>{pop['modeled_trials']:,} trials in {n_campaigns} campaigns</h2>
<p>The data comes from KKBox's public WSDM Cup 2018 release: every subscription transaction, a profile for each member, and a daily log of how much each person listened.</p>
<div class="cards" style="--n:3">
  <div class="card"><b>{pop['log_rows'] / 1e6:.0f}M</b><span>days of listening, one row per person per day</span></div>
  <div class="card"><b>{n_campaigns}</b><span>campaigns, each a month of trial sign-ups</span></div>
  <div class="card"><b>{'–'.join(years)}</b><span>when the trials started</span></div>
</div>
<p>Not every free trial fits the question, so the trials were narrowed down step by step:</p>
<figure>
  <img src="img/funnel.png" alt="From {pop['trial_rows']:,} free-trial transactions to {pop['modeled_trials']:,} trials analyzed.">
</figure>
<p class="read">Each bar shows how many trials remain after one filter. We keep each person's first trial, drop odd trial lengths, keep trials with a member profile and 30 days of follow-up, drop people who paid before the trial ended, and keep the {n_campaigns} biggest sign-up months. The blue bar is what the rest of this page analyzes.</p>

<h2><small>Global findings</small>Most of the gap is who each campaign reached</h2>
<p>Here is how the {pts(n['gap'])}-point gap between the best and worst campaign splits:</p>
<figure>
  <img src="img/split.png" alt="Shares of the gap: who {pct(s['who']['share'])}, what {pct(s['what']['share'])}, campaign itself {pct(s['unexplained']['share'])}, with a range that crosses zero.">
</figure>
<p class="read">Each bar is a share of the gap, and the thin line through it is the 95% range. The blue bar, who each campaign reached, explains {pct(s['who']['share'])} of the gap ({ci(s['who']['ci'])}). The orange bar, what trialists did in their first five days, explains {pct(s['what']['share'])} ({ci(s['what']['ci'])}).</p>
<p class="read">{campaign_reading(s)}</p>
<p>The audience effect shows up beyond the best and worst pair. The next chart shows each campaign's actual conversion next to what it would be if it had reached the average mix of all trialists:</p>
<figure>
  <img src="img/campaigns.png" alt="Actual and average-audience conversion for each campaign.">
</figure>
<p class="read">Grey dots are actual conversion; blue diamonds are conversion with the average mix of people. For the {len(same_year)} campaigns of {year}, the spread shrinks from {pts(spread_raw)} points to {pts(spread_adj)}: they differed mostly in whom they attracted, not in how well they converted them.{exception}</p>

<h2><small>Findings broken down</small>What drives the gap, piece by piece</h2>
<h3>Who: how people signed up, and where they live</h3>
<p>The two biggest audience differences between the best and worst campaign are signup channel and city:</p>
<figure>
  <img src="img/audience.png" alt="Signup channel and city mix of the best and worst campaign, with each group's conversion rate.">
</figure>
<p class="read">Dark bars are the best campaign's trialists and red bars the worst's. Each label shows how often that group pays, across all trialists; the channel and city codes are anonymized in the data. The worst campaign leaned on signup channel {escape(via['level'])} ({pct(via['share_worst'])} of its trialists) and city {escape(city['level'])} ({pct(city['share_worst'])}), the two groups least likely to pay: {pct(via['conversion'], 1)} and {pct(city['conversion'], 1)}.</p>
<p>Splitting the "who" share by trait confirms these two do the work:</p>
<figure>
  <img src="img/traits.png" alt="Share of the who effect carried by each audience trait.">
</figure>
<p class="read">Each bar is a trait's share of the audience effect, with its 95% range. Signup channel ({pct(traits['Signup channel']['share'])}) and city ({pct(traits['City']['share'])}) carry it. Time since registration adds little ({pct(traits['Time since registration']['share'])}), and age and gender pull slightly the other way ({pct(traits['Age and gender']['share'])}); together the four add to 100%.</p>

<h3>What: showing up in the first days</h3>
<p>The clearest difference in behavior is how many trialists never listened at all:</p>
<figure>
  <img src="img/silence.png" alt="Days with any listening in the first five trial days, best vs worst campaign.">
</figure>
<p class="read">The bars count trialists by how many of their first five days they listened. In the worst campaign, {pct(b['silent_worst'])} never listened, against {pct(b['silent_best'])} in the best.</p>
<p>That matters, because showing up goes hand in hand with paying:</p>
<figure>
  <img src="img/activation.png" alt="Conversion by days with any listening, actual and with the same mix of people.">
</figure>
<p class="read">The grey line is what actually happened: of the trialists who listened on that many days, the share who paid. The blue line holds the mix of people fixed (same signup channels, cities, ages) and asks how many would pay at each level of listening. It still climbs, from {pct(silent['adjusted'], 1)} to {pct(all_days['adjusted'], 1)}, so heavy listening isn't just a sign of a better audience. It can't rule out that people who already meant to pay listen more.{raw_above}</p>
<p>Among people who did listen, the difference was how much they listened, not how they reacted to the music:</p>
<figure>
  <img src="img/listening.png" alt="Worst-campaign listeners' medians as a share of best-campaign listeners'.">
</figure>
<p class="read">The dashed line marks where the worst campaign's listeners would match the best's. They listened about half as much ({pct(minutes_ratio)} of the minutes), but finished and skipped songs just as often. They used the product less; there's no sign they liked the music less.</p>

<h2><small>Conclusion</small>Fix targeting first, then get trialists to show up</h2>
<ol class="takeaways">
  <li><strong>Targeting first.</strong> Most of the gap ({pct(s['who']['share'])}) comes from whom a campaign reached, above all signup channel and city.</li>
  <li><strong>Then activation.</strong> About a quarter ({pct(s['what']['share'])}) comes from the first five days, and it's about showing up and listening more, not about the music itself.</li>
  <li><strong>Compare campaigns like for like.</strong> Most of the difference between campaigns is audience. Once people and habits match, the best and worst convert about the same, so comparing raw conversion mostly compares audiences.</li>
</ol>
<h3>What I would test at Suno</h3>
<ul>
  <li>Rank acquisition sources by how many trialists end up paying, not how many sign up, using a holdout that shifts spend between sources.</li>
  <li>Nudge trialists who haven't made anything within 24 hours of starting, in a randomized test, toward creating a first song.</li>
  <li>Report campaign tests at a fixed audience mix, so offers and creative are compared fairly.</li>
</ul>

<details>
  <summary>Technical details and caveats</summary>
  <p><strong>Population.</strong> First-time, $0 trials sold as 7-day trials, whose recorded expiry falls 5–31 days after the start, starting on or after 2015-04-01, with a complete 30-day follow-up: {pop['full_trials']:,} trials; in the {n_campaigns} largest months, {pop['modeled_trials']:,}. Excluded: trialists who paid during the trial ({pop['in_trial_payers_excluded']:,}) and trialists missing from the member file ({pop['no_member_row_excluded']:,}; almost none converted, pointing to an export artifact). Converted means paid within 30 days after the trial ended.</p>
  <p><strong>Model.</strong> Logistic regression of conversion on campaign, five audience traits and six banded behavior measures from the first five trial days. The two swaps are counterfactuals that keep the worst campaign's own effect; Shapley values share the overlap between them evenly, so their order doesn't matter. The "same habits" swap pairs trialists on signup channel and age band; since age is blank for most members, that pairing often reduces to channel plus "age unknown". Ranges are the 2.5th and 97.5th percentiles of {pop['bootstrap_replicates']} bootstrap refits, resampling trialists within each campaign.</p>
  <p><strong>An extreme pair.</strong> The best month is also the smallest ({best_n:,} trials). Against the next-best month ({month(alt['best'])}), the split is similar: {pct(alt['share_who'])} who, {pct(alt['share_what'])} what, {pct(alt['share_unexplained'])} the campaign itself.</p>
  <p><strong>Caveats.</strong> These are associations, not causes: people who already intend to pay probably listen more. "Campaign" is the trial start month. Age and gender are blank for most members; dropping them moves the split by about a point. Published cells have at least {n['min_published_cell']} trialists.</p>
</details>
</main>
</body>
</html>
"""


def run(ctx) -> list[str]:
    src = ctx.cache_dir / "publish"
    numbers = json.loads((src / "published.json").read_text())
    img = d.DOCS_DIR / "img"
    img.mkdir(parents=True, exist_ok=True)
    for name in charts.CHARTS:
        shutil.copyfile(src / name, img / name)
    index = d.DOCS_DIR / "index.html"
    index.write_text(render(numbers), encoding="utf-8")
    written = [index, *(img / name for name in charts.CHARTS)]
    # Outputs are recorded relative to the cache, so the pipeline can tell when
    # a published file has gone missing.
    return [Path(os.path.relpath(p, ctx.cache_dir)).as_posix() for p in written]

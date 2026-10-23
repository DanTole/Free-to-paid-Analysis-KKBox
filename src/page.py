"""Stage `page`: the public write-up, docs/index.html, rendered from published.json.

Reads cache_dir/publish/ (stage `charts`). Writes docs/index.html and copies the
charts to docs/img/. Every number on the page comes from published.json, so the
page can't drift from the analysis. The page is self-contained: inline CSS,
local images, no external requests.
"""

import json
import os
import shutil
from datetime import date
from html import escape
from pathlib import Path

from src import charts
from src import definitions as d


def pct(x: float, digits: int = 0) -> str:
    return f"{x * 100:.{digits}f}%"


def pts(x: float) -> str:
    return f"{x * 100:.2f}"


def ci(pair) -> str:
    lo, hi = pair
    if lo < 0:  # "−26% to +2%" reads better than "-26–2%"
        return f"−{abs(lo) * 100:.0f}% to {'+' if hi > 0 else '−' if hi < 0 else ''}{abs(hi) * 100:.0f}%"
    return f"{lo * 100:.0f}–{hi * 100:.0f}%"


CSS = """
:root { --ink:#1f2933; --muted:#5b6670; --rule:#e3e7ec; --accent:#3b6ea8; --what:#d1793b; --bg:#ffffff; --card:#f6f8fa; }
* { box-sizing: border-box; }
html { color-scheme: light; }
body { margin:0; background:var(--bg); color:var(--ink); font:17px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
main { max-width: 760px; margin: 0 auto; padding: 48px 20px 64px; }
h1 { font-size: 2rem; line-height: 1.25; margin: 0 0 12px; letter-spacing: -0.01em; }
h2 { font-size: 1.3rem; margin: 48px 0 8px; }
p, li { margin: 0 0 14px; }
.dek { color: var(--muted); font-size: 1.1rem; margin-bottom: 8px; }
.byline { color: var(--muted); font-size: .9rem; border-bottom: 1px solid var(--rule); padding-bottom: 20px; margin-bottom: 28px; }
.stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin: 24px 0 8px; }
.stat { background: var(--card); border-radius: 10px; padding: 16px; }
.stat b { display:block; font-size: 2rem; line-height: 1.1; }
.stat span { display:block; color: var(--muted); font-size: .85rem; margin-top: 6px; }
.stat.who b { color: var(--accent); } .stat.what b { color: var(--what); } .stat.none b { color: var(--muted); }
figure { margin: 24px 0; }
figure img { width: 100%; height: auto; display: block; }
figcaption { color: var(--muted); font-size: .85rem; margin-top: 6px; }
table { border-collapse: collapse; width: 100%; font-size: .92rem; margin: 12px 0 20px; }
th, td { text-align: right; padding: 6px 8px; border-bottom: 1px solid var(--rule); }
th:first-child, td:first-child { text-align: left; }
.tests li { margin-bottom: 10px; }
.method { background: var(--card); border-radius: 10px; padding: 20px 22px; font-size: .92rem; margin-top: 48px; }
.method h2 { margin-top: 0; font-size: 1.1rem; }
footer { color: var(--muted); font-size: .85rem; margin-top: 40px; border-top: 1px solid var(--rule); padding-top: 16px; }
@media (max-width: 560px) { .stats { grid-template-columns: 1fr; } h1 { font-size: 1.6rem; } }
"""


def render(n: dict, repo_url: str = "", today: date | None = None) -> str:
    today = today or date.today()
    s, b, a = n["split"], n["behavior"], n["audience"]
    pop = n["population"]
    acts = {r["days"]: r for r in n["activation"]}
    top, bottom = acts[max(acts)], acts[min(acts)]
    adj = sorted(r["adjusted"] for r in n["campaigns"] if r["campaign"].startswith("2016"))
    via, city = a["registered_via"], a["city"]
    lo_u, hi_u = s["unexplained"]["ci"]
    # "≈ 0" only while the interval includes zero; otherwise show the estimate.
    campaign_term = "≈ 0" if lo_u <= 0 <= hi_u else pct(s["unexplained"]["share"])
    code_line = (
        f'Code, tests and the full method: <a href="{escape(repo_url)}">{escape(repo_url)}</a>.'
        if repo_url else "Code, tests and the full method are in the project repository."
    )
    rows = "".join(
        f"<tr><td>{escape(r['campaign'])}</td><td>{r['n']:,}</td><td>{pct(r['raw'], 2)}</td>"
        f"<td>{pct(r['adjusted'], 2)}</td></tr>"
        for r in n["campaigns"]
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>What drives free-trial conversion</title>
<meta name="description" content="A who-versus-what decomposition of {pop['modeled_trials']:,} music-streaming free trials.">
<style>{CSS}</style>
</head>
<body>
<main>
<h1>Who a campaign reaches matters more than what trialists do</h1>
<p class="dek">Why some free-trial campaigns convert twice as well as others: a decomposition of {pop['modeled_trials']:,} free trials on a music-streaming service.</p>
<p class="byline">Dan Toledano · {today.strftime('%B %Y')} · Public data: KKBox, WSDM Cup 2018</p>

<p>The best free-trial campaign in this data converted <strong>{pct(n['best_rate'], 2)}</strong> of trialists to paid within 30 days of the trial ending; the worst converted <strong>{pct(n['worst_rate'], 2)}</strong>. A growth team looking at that gap has to decide where to act: <em>targeting</em> (the campaigns reached different people) or <em>onboarding</em> (the trial experience differed). This analysis splits the gap between the two.</p>

<div class="stats">
  <div class="stat who"><b>{pct(s['who']['share'])}</b><span>who each campaign reached<br>95% CI {ci(s['who']['ci'])}</span></div>
  <div class="stat what"><b>{pct(s['what']['share'])}</b><span>what trialists did in the first five days<br>95% CI {ci(s['what']['ci'])}</span></div>
  <div class="stat none"><b>{campaign_term}</b><span>left for the campaign itself<br>95% CI {ci(s['unexplained']['ci'])}</span></div>
</div>

<figure>
  <img src="img/split.png" alt="Shares of the conversion gap: who {pct(s['who']['share'])}, what {pct(s['what']['share'])}, campaign itself {pct(s['unexplained']['share'])}.">
  <figcaption>Share of the {pts(n['gap'])}-point gap between {escape(n['best'])} and {escape(n['worst'])}. The parts add to more than 100% before the campaign term because a better audience and better behavior compound; the overlap is split evenly between them.</figcaption>
</figure>

<h2>Raw comparisons mostly measure audience</h2>
<p>The {len(n['campaigns'])} campaigns with at least {n['min_campaign_trials']:,} trials range from {pct(n['worst_rate'], 2)} to {pct(n['best_rate'], 2)} raw conversion. Shown at the same audience mix, every 2016 campaign lands between {pct(adj[0], 1)} and {pct(adj[-1], 1)}: the biggest campaigns looked weak because of who they brought in.</p>
<figure>
  <img src="img/campaigns.png" alt="Raw and audience-adjusted conversion for each campaign.">
  <figcaption>Grey: raw conversion. Blue: conversion if every campaign had reached the same overall audience (g-computation from a logistic model).</figcaption>
</figure>
<table>
  <thead><tr><th>Campaign (trial start month)</th><th>Trials</th><th>Raw</th><th>Same audience</th></tr></thead>
  <tbody>{rows}</tbody>
</table>

<h2>Who: signup channel and city</h2>
<p>The worst campaign drew {pct(via['share_worst'])} of its trialists through one signup channel, against {pct(via['share_best'])} for the best; trialists from that channel converted at {pct(via['conversion'], 1)}, against {pct(via['conversion_other'], 1)} for everyone else. The same pattern holds for one city ({pct(city['share_worst'])} vs {pct(city['share_best'])} of trialists, converting at {pct(city['conversion'], 1)}). Those two traits carry most of the audience effect.</p>

<h2>What: showing up in the first days</h2>
<p>{pct(b['silent_worst'])} of the worst campaign's trialists never listened during the first five trial days, against {pct(b['silent_best'])} for the best. Listeners in the worst campaign also listened about half as long (median {b['median_minutes_worst']:.0f} vs {b['median_minutes_best']:.0f} minutes), but finished songs at the same rate ({pct(b['median_completion_worst'])} vs {pct(b['median_completion_best'])}). The difference is how much people used the product, not how they reacted to what they heard.</p>
<figure>
  <img src="img/silence.png" alt="Share of trialists by days with any listening, best vs worst campaign.">
  <figcaption>Share of each campaign's trialists by days with any listening in the first five trial days.</figcaption>
</figure>
<p>At the same audience, conversion climbs from <strong>{pct(bottom['adjusted'], 1)}</strong> for trialists who never listened to <strong>{pct(top['adjusted'], 1)}</strong> for those who listened on all five days. One active day barely moves it; the climb starts from the second.</p>
<figure>
  <img src="img/activation.png" alt="Conversion by days with any listening, raw and audience-adjusted.">
  <figcaption>Conversion within 30 days by days with any listening in the first five trial days. Blue holds the audience mix fixed.</figcaption>
</figure>

<h2>What I would test at Suno</h2>
<p>These are hypotheses from a different product's data, framed as experiments, not claims about Suno.</p>
<ol class="tests">
  <li><strong>Judge acquisition sources on trial-to-paid, not trial volume.</strong> Here the two largest campaigns were among the weakest because of where their trialists came from. A holdout that shifts spend between sources would show whether conversion follows.</li>
  <li><strong>Get silent trialists to a second active day.</strong> A randomized nudge in the first trial days for people who haven't come back, measured on conversion, not engagement. The analogue at Suno might be creating or finishing a first song; the experiment would tell.</li>
  <li><strong>Report campaigns at a fixed audience mix.</strong> Raw campaign comparisons mostly measure who signed up. Adjusted rates make offer and creative tests comparable.</li>
</ol>

<section class="method">
<h2>Method and caveats</h2>
<p><strong>Data.</strong> {pop['log_rows']:,} daily listening rows and {pop['trial_rows']:,} free-trial transactions from KKBox's public WSDM Cup 2018 release. No day of logs was missing ({pop['gap_days']} gap days found).</p>
<p><strong>Population.</strong> First-time, $0, 7-day trials starting on or after 2015-04-01 with a complete 30-day follow-up: {pop['full_trials']:,} trials. Trialists who paid during the trial ({pop['in_trial_payers_excluded']:,}) and trialists missing from the member file ({pop['no_member_row_excluded']:,}, who almost never converted, which points to an export artifact) are excluded. Months that looked like 85–99% conversion turned out to be in-trial payers or existing customers and fall out under these rules. Converted means paid within 30 days after the trial ended.</p>
<p><strong>Model.</strong> Logistic regression of conversion on campaign, five audience traits (signup channel, city, age band, gender, time since registration) and six trial-week behavior measures in bands (days active, minutes, unique songs, completion, early skips, first listen day). The gap between the best and worst campaign is split with Shapley values over two counterfactual swaps (audience; behavior from similar trialists), keeping the worst campaign's own effect. Intervals: {pop['bootstrap_replicates']} bootstrap refits, trialists resampled within campaign.</p>
<p><strong>Caveats.</strong> The results are associations, not causes: people who already intend to pay probably listen more. "Campaign" is the trial start month; the data has no campaign ID. Age and gender are blank for most members; dropping them changes the split by about one point. Published tables have at least {n['min_published_cell']} trialists per cell.</p>
</section>

<footer>{code_line} Data: KKBox WSDM Cup 2018 (Kaggle); raw data is not redistributed.</footer>
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
    index.write_text(render(numbers, d.PUBLIC_REPO_URL), encoding="utf-8")
    written = [index, *(img / name for name in charts.CHARTS)]
    # Outputs are recorded relative to the cache, so the pipeline can tell when
    # a published file has gone missing.
    return [Path(os.path.relpath(p, ctx.cache_dir)).as_posix() for p in written]

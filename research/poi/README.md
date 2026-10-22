# POI shadow study

This is an offline experiment in the Bosque V3 repository. `bosque_poi.py` is
not connected to the live engine, Telegram, or the Learning Journal. Running
the study writes only this research directory.

Run from the repository root:

```sh
python research/poi/run_study.py
python -m unittest discover -s tests -v
```

The replay calls the current V3 engine and original paper execution model.
POI annotations are calculated using only the closed candles available at
each original signal decision. These annotations select matched subsets of
the original signals; they do not simulate rearming alerts after a rejection.
This experiment tests filtering existing signals, not finding extra entries.

Policies fixed before the first replay:

* Breakout/retest: reuse V3's frozen M15 swing break followed by an M5 touch
  and directional rejection. Already required by V3 breakout setups, so this
  can be redundant. It is also evaluated on other V3 setup types.
* H1 liquidity: a pivot with two closed H1 bars on each side, known before the
  reaction candle; expires 24 hours after confirmation. An intervening M5 close
  through the level invalidates it. Latest M5 must sweep strictly beyond it,
  reclaim it, and close with a candle body in the signal direction.
* Either: union of the two policies. FVG is outside this first experiment.

The experiment preserves existing plans and the 40 pip entry limit; POI levels
do not produce replacement entry zones. It reports gross results and illustrative
5/10 pip round-trip cost sensitivity, not measured costs. The same price paths
and fills are used for all subsets. Exit remains TP2, with SL first on ambiguous
M5 candles. Pending, unfilled, and data-gap cases are not counted as closed wins.

`input.csv` is assembled from hourly Git snapshots saved since 20 October 2026,
and includes their earlier candle history. `results.json` records source commits,
hashes, revision counts, configuration and individual paper trades. Latest stored
revision wins for overlapping candles. This is not point-in-time provider data;
historical news is not applied. No claim of broker execution accuracy is made.

This short retrospective sample is exploratory, with no independent holdout.
Do not promote a filter based on a higher win rate from a handful of retained
trades. Next stage: prospective POI annotations on unchanged V3 signals, then
evaluate after sufficient independent closed trades and measured execution costs.

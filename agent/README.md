# EvoDRC agent

A drop-in replacement for the `agent/` folder of the
[DAC26 DRC Benchmark](https://github.com/ASU-VDA-Lab/DAC26_DRC_Benchmark).

The benchmark's own agent gets one shot at repairing a layout. This one works in rounds: it splits the design into pieces, repairs each piece, checks the result, writes down what it learned, and goes again with better notes.

The round loop handles **block-level repair** only. Cell, polygon and detection cases are answered by this agent's own single-shot path, which reproduces the benchmark's one-shot behaviour using the templates in `prompts/`.

This file documents the agent folder itself: how to install and run it, every setting it reads, what it writes, and how the round loop is put together. For the paper's published results and the layout of the `data/` tree, see the [repository README](../README.md).

---

## 1. Set up

You need Docker, a benchmark checkout, and to be logged in to Claude on this
machine (`~/.claude/.credentials.json`, read by the benchmark's own backend).

**Build the image** — two steps. The stock image is missing two Python packages.

```bash
cd DAC26_DRC_Benchmark
docker build -f Dockerfile.repair -t drc-benchmark-repair:latest .
cd ..
docker build -t drc-benchmark-repair:latest - < Dockerfile.evodrc
```

Both use the same tag on purpose, so the benchmark picks up the second one with no extra flags. The second build is fed on stdin and takes no build context, which keeps Docker from copying the whole repository into the daemon. Check it worked:

```bash
docker run --rm drc-benchmark-repair:latest python3 -c "import networkx, dataclasses; print('ok')"
```

**Install the agent** — copy it in. A symlink will not work.

```bash
cd DAC26_DRC_Benchmark
mv agent agent_official_backup
cp -r ../agent ./agent
```

---

## 2. Run

Choose the models and cases by editing the `MODEL_NAMES` and `CASES` arrays in
`DAC26_DRC_Benchmark/src/evaluate_claude.sh`:

```
MODEL_NAMES=(
  "claude-sonnet-4-6 medium" #Claude Sonnet 4.6 with medium reason effort
  "claude-opus-4-6 high"     #Claude Opus 4.6 with high reason effort
)

CASES=(
  "block|repair|Block1"      #Only supports repair tasks in the DAC'26 benchmark.
  "block|repair|Block2"
  "block|repair|Block3"
  "block|repair|Block4"
  "block|repair|Block5"
  "block|repair|Block6"
  "block|repair|Block7"
)
```

Then run from the benchmark root:

```bash
cd DAC26_DRC_Benchmark
RECORD_TOKENS=1 bash src/evaluate_claude.sh
```

`RECORD_TOKENS=1` is required. Without it the agent stops on the first case with a message naming the variable. It has to be set on the command line: the benchmark re-exports it with `docker exec -e`, which overrides anything baked into the image.

---

## 3. Configure

Everything is configured in `evodrc.conf`, a plain `KEY=VALUE` file. It has to be a file: the benchmark forwards only `CLAUDE_EFFORT`, `CLAUDE_CODE_MAX_OUTPUT_TOKENS`, `PYTHONDONTWRITEBYTECODE` and `RECORD_TOKENS` into the container, and none of those is an EvoDRC setting, so `ABLATION` and the rest have to travel with the agent folder.

The main switch is `ABLATION`:

| `ABLATION` | What it does |
|---|---|
| *(empty)* | The full method. Starts with prior experience and keeps learning. |
| `1` | Starts with the rules but no prior experience. |
| `2` | Starts with experience and never updates it — learning is off from the first round onward. |
| `3` | Repairs the design as a whole instead of splitting it up. |

Other useful keys:

| Key | Default | What it does |
|---|---|---|
| `MAX_ITERS` | `5` | Most rounds to run. A run stops early once the design is clean or nothing is left that can be repaired. |
| `MAX_CONCURRENT_CALLS` | `5` | Most AI calls running at once; `0` = no limit |
| `CALL_COOLDOWN_SECONDS` | `2` | Pause after a call finishes before the next one starts; fractions allowed |
| `LEAF_CONCURRENCY` | `5` | Pieces repaired in parallel |
| `KNOW_CONCURRENCY` | `2` | Layers whose notes are updated in parallel |
| `KNOW_ATTEMPTS` | `2` | Tries allowed per note rewrite and per judging step — one initial call plus one retry at the default |
| `CU_DRC` | `1` | Arbitrate when two pieces edit the same shared thing |
| `CU_DELTA_LE0` | `1` | Also accept repairs that break even, not just improvements |
| `VIA_COMPETITION` | `1` | Arbitrate when two pieces edit the same via cell |
| `EVODRC_ENABLE_BEST_VALID_ROLLBACK` | `0` | Optionally reject broken/incomplete block states and emit the lowest-DRV valid iteration |
| `EVODRC_ENABLE_LIMITED_MULTI_CANDIDATE` | `0` | Generate bounded alternatives for selected difficult units; requires best-valid rollback |
| `EVODRC_MULTI_CANDIDATE_COUNT` | `2` | Total candidates per selected unit; hard range 2–3 |
| `EVODRC_MULTI_CANDIDATE_MAX_UNITS_PER_ITER` | `1` | Selected difficult units per iteration; hard range 1–2 |
| `EVODRC_MULTI_CANDIDATE_MAX_EXTRA_CALLS_PER_ITER` | `1` | Calls beyond candidate 0 per iteration; hard range 1–4 |
| `EVODRC_MULTI_CANDIDATE_MIN_DRV` | `50` | Owned-DRV threshold for the high-DRV trigger; hard range 1–10000 |
| `EVODRC_MULTI_CANDIDATE_EMPTY_STREAK` | `2` | Consecutive candidate-0 empty patches before triggering; hard range 1–10 |
| `EVODRC_MULTI_CANDIDATE_STAGNATION_ROUNDS` | `2` | Consecutive non-improving stable-signature rounds before triggering; hard range 1–10 |
| `EVODRC_MULTI_CANDIDATE_ALLOW_PDN` | `1` | Allow a PDN unit to trigger below the DRV threshold |

`evodrc.conf` also carries `EVODRC_PROMPT_MODE`, which selects the request format. Leave it at `exp3`.

Environment variables win over the file, and the file wins over the built-in defaults. A missing file is fine — the defaults are the full method. A negative number is treated as a typo and falls back to the default, so you cannot accidentally switch a safety limit off. `MAX_ITERS` is the one exception: a value below 1 stops the run with an error rather than falling back.

`MAX_CONCURRENT_CALLS` and `CALL_COOLDOWN_SECONDS` cover **every** AI call the round loop makes. They matter because one round can issue a lot of them: each piece costs one call, and each layer whose notes get updated costs up to six more — two note generators and a judge, each allowed `KNOW_ATTEMPTS` tries.

### Optional best-valid rollback policy

`EVODRC_ENABLE_BEST_VALID_ROLLBACK=0` is the default and preserves the
published iteration and final-output policy. Set it to `1` in
`agent/evodrc.conf` to enable the optional policy. A complete block evaluation
whose connectivity is preserved always becomes the next iteration's input,
even when its DRC count rises. Broken or incomplete attempts are recorded and
the next iteration resumes from the most recent valid state. The lowest-DRV
valid state is selected only at final output; equal totals retain the earlier
iteration.

Enabled runs add two compatible audit artifacts without changing
`block_result.json`: `iter<N>/iteration_state.json` records each transition,
and `best_valid_summary.json` records the best, last-valid and final-selected
iterations. The same information is logged under `EVODRC_BEST_VALID`.

### Optional limited multi-candidate search

Set both `EVODRC_ENABLE_BEST_VALID_ROLLBACK=1` and
`EVODRC_ENABLE_LIMITED_MULTI_CANDIDATE=1` to enable 4A+4B. Enabling 4B without
4A is rejected at startup. Candidate 0 uses the unchanged prompt, call id and
single-candidate output path. Only centrally selected difficult units receive
one or two sequential alternatives, bounded by the unit and extra-call
budgets. Difficulty uses a stable signature over PDN identity, coarse region,
rule set and editable object identities rather than a repair-unit id alone.

Triggers are high owned-DRV count, allowed PDN status, a configured consecutive
empty-patch or stagnation history, or conflict degree at least two. Extra
candidates carry a short conservative/alternative-strategy suffix and write to
isolated private paths. No alternative prompt is persisted.

Selection requires parse, the deterministic validator, and connectivity.
Extra candidates additionally require the existing faithful crop DRC; an
operation that faithful geometry preview cannot represent is rejected instead
of approximately scored. Candidates are ordered by fewer new DRVs, larger net
DRV improvement, fewer operations, then lower candidate number. If faithful
DRC is unavailable for every candidate, a legal candidate 0 is retained. An
internal 4B error restores candidate 0 and resumes the original gate path.

Triggered units add `candidates/candidate_<N>/patch.json`, a small generation
record, and `candidate_verdicts.json`. Each iteration adds
`candidate_plan.json`. These files contain no prompt or authentication data.

---

## 4. Where the results go

Two places, both under the benchmark folder.

**The repaired layout**, which the benchmark scores:

```
result/<model>-<effort>/block/repair/Block5/Block5_repaired.py
```

**The full record of the run:**

```
temp/data/<variant>/Block5/<model>/
```

`<variant>` is `evodrc`, `ablation1`, `ablation2` or `ablation3`, matching the `ABLATION` setting. Because the model name is part of the path, two models each get their own tree and never overwrite each other. The agent prints the exact path when it starts — look for a line beginning `EVODRC_ITER persist_root=` in the run log.

Inside:

```
db/                  everything learned, one folder per chip layer
skill/               the current notes for each layer, plus rejected drafts
crop_history/        a snapshot of each piece's inputs, per round
_inject/             the knowledge files as handed to the AI this run
iter1/, iter2/ ...   one folder per round
  input/             the layout and violations this round started from
  leaf/<piece>/      the request sent, the repair proposed, the context given
  gated/             whether each repair was accepted, and why
  repaired/          the resulting layout and its remaining violations
  block_result.json  violations before and after
```

The [repository README](../README.md) annotates this tree file by file. A self-run differs from the published `data/` tree in three ways:

* It adds `_inject/` at the top. That is deliberate: requests name the knowledge files by their full path, and that path must not change between rounds, so the folder they are served from has to sit somewhere stable.
* It produces no `usage/` and no per-round `score/`. Both are written by the benchmark harness, not by this agent.
* With `ABLATION=2` it also writes `iter{N}/knowledge_update.json`, recording that learning was skipped. The published `ablation2/` trees do not carry that file.

Everything else the run needs internally — working copies, per-call records, staging folders, logs — is written inside the container, in a place that is not shared with your computer, and is discarded when the container is removed.

> **Re-running a case replaces its previous results.** The folder name has no
> timestamp in it. Copy it aside first if you want to keep it.

### A note about the shared `temp/` folder

Requests name two scratch folders by full path at the top of the benchmark's `temp/` folder, so EvoDRC creates `conn/` and `drc/` there at the start of a run and removes them again at the end. Three consequences:

* **Run one case at a time.** Two EvoDRC runs sharing one benchmark folder share those two scratch folders, and the run that created them removes them when it finishes — pulling them out from under the other one.
* Any other stray files the AI writes at that level are left alone on purpose. EvoDRC removes only the names it claimed itself, so it never deletes another run's work. Delete them by hand if you like.
* If a run is killed rather than allowed to finish, `conn/` and `drc/` stay behind and later runs leave them alone. Delete the two folders by hand before the next run.

---

## 5. How a round works

1. **Split** the design into pieces small enough to reason about. Neighbouring pieces in the same row are merged into one unit, so a "piece" is often a group.
2. **Repair** every piece — one AI call each, running in parallel up to the limit.
3. **Check** each proposed repair against the connectivity that must be preserved. This is a plain calculation, not an AI judgement. Broken repairs are dropped.
4. **Arbitrate** when several pieces want to change the same shared thing: each candidate is measured for real, and the best one wins.
5. **Assemble** the accepted repairs and run a full design rule check.
6. **Learn.** For each layer that was touched, two independent rewrites of its notes are drafted — one building on the existing notes, one written fresh from the evidence alone — and a separate judging step picks at most one. Rejected drafts are kept.
7. **Repeat** with the repaired layout. Stops early if the design is clean or nothing is repairable.

Repair requests reach the notes **by file path, never pasted into the request**, so the knowledge base can grow without bloating the prompt.

---

## 6. When something goes wrong

| Symptom | Cause | Fix |
|---|---|---|
| Stops at once, complains about `RECORD_TOKENS` | launched without it | add `RECORD_TOKENS=1` |
| Runs cases you did not ask for | the case list lives in the benchmark script | edit the `CASES` array in `src/evaluate_claude.sh` |
| `ModuleNotFoundError: networkx` | second image build was skipped | see section 1 |
| Complains `check_connectivity` is missing | the benchmark's `evaluator/` folder is not where the agent looks | launch through `evaluate_claude.sh`, or point `EVALUATOR_DIR` at that folder |
| `RuntimeError` about `ABLATION` | typo in `evodrc.conf` | see the table in section 3 |
| `RuntimeError` about `MAX_ITERS` | a value below 1 in `evodrc.conf` | set 1 or more, or leave it empty |
| A previous run's results are gone | expected — re-running replaces them | copy the folder aside first |
| Stops with `refusing to annotate a FROZEN benchmark input` | a script pointed the splitter at the benchmark's own `testcase/` files | copy the layout to a writable folder first and work on the copy |

Some things are non-fatal by design. If one piece fails to merge correctly, that
piece is skipped and the round continues. If the arbitration step fails, assembly
falls back to first-come-first-served for that round. Both are logged.

---

## 7. What is in this folder

| Path | Role |
|---|---|
| `agent.py` | entry point the benchmark calls |
| `prompt_format.py` | builds the request from the case description |
| `skill.md` | the fixed background instructions |
| `prompts/` | per-task templates |
| `evodrc.conf` | **the settings file** (section 3) |
| `knowledge/cla/` | starting notes for the full method |
| `knowledge/cold_start/` | starting notes for `ABLATION=1` — rules only |
| `knowledge/deck_map.json` | which design rule belongs to which layer |
| `src/` | the repair pipeline |
| `src/iter/` | the round loop: splitting, scheduling, checking, learning |

Nothing is written into this folder while running — it is mounted read-only.

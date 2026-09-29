# Jev

Jev is a hosted typed-decision model from TypeSafe (`api.typesafe.ai`). The client here is disabled and replays synthetic recordings; live use needs an account holder’s own TypeSafe account.

Jev picks typed answers. Try a small instrument question without a key or database:

```sh
uv run --project functions mdp jev try instruments --state instrument_01
```

The answer includes the chosen family, each option's probability, and confidence.
The included cassette contains **synthetic test responses**. Its scores do not measure Jev quality.

1. From the repo root, run `uv sync --project functions`.
2. Open [the question set](../functions/src/mdp_functions/jev_examples/instruments/questions.json).
   It asks one question about the `instrument` field. Only that field enters vendor state.
3. Run the command above. Try `instrument_05` to see an answer routed to review.
4. Open [the 20 labeled rows](../dbt/seeds/jev_instruments.csv).
   `state_id` identifies a row. `expected_family` is your answer, kept outside vendor state.
   For your own rows, use one `expected_<question>` column for each question.
5. Run the evaluation:

   ```sh
   uv run --project functions mdp jev eval instruments --labels dbt/seeds/jev_instruments.csv
   ```

   Reports appear under `ops/evidence/jev/instruments/`, with a UTC date in each filename.
6. Open [the disabled gold example](../functions/src/mdp_functions/sources/jev_instrument_family/function.py).
   It calls `await ctx.jev.ask(row, ctx.question_set)`.
   The function yields the answer, probabilities, confidence, question version and model id.
   Bronze fetches inputs. Gold asks Jev about declared warehouse rows.

To try a JSON file, write `{"instrument": "violin"}` into `/tmp/instrument.json`:

```sh
uv run --project functions mdp jev try instruments --state /tmp/instrument.json
```

A row id comes from `--labels <csv>`. Without that option, `try` uses the example seed.
These commands replay responses and never fall back to a network call.


## Improve a question

Copy `questions.json` to `/tmp/instruments_v2.json`. Keep the question id `family` and its options.
Change one sentence. Ask one thing at a time, and describe options that overlap as little as possible.
Include enough facts in the state to make the decision. Keep instructions in the question.

The question set's hash changes when its wording, fields, model, thresholds or floors change.
A cassette key combines that version with the state hash. A wording change therefore needs new
responses. A missing entry prints the recording command; it never invents an answer.

After an account holder records both versions:

```sh
uv run --project functions mdp jev diff instruments /tmp/instruments_v2.json \
  --labels dbt/seeds/jev_instruments.csv \
  --cassette-a /tmp/original.json --cassette-b /tmp/revised.json
```

The result lists changed row ids and answers, metric deltas, and both full metric sets.
Use `--cassette <path>` on `try` and `eval` to choose recorded responses.
Keep a held-out set of labels when choosing wording or thresholds.


## Evaluation

Choice picks an option and reports a probability for every option. Eval reports accuracy,
precision and recall for each class. An undefined precision or recall appears as `null`.

Score returns a probability-weighted level from an ordered rubric. Eval reports mean absolute
error. Its accuracy compares the nearest level of the answer and label. Calibration uses only labels that
name an integer level; continuous labels still count toward mean absolute error.

Noul returns the probability that a statement is true. Use labels `0` or `1`. Eval reports
binary accuracy at `0.5` and Brier score. Noul has no separate confidence.

Brier score measures probability error; lower is better. For Choice and Score, this report
sums squared errors across all options per row, then averages rows. For Noul it uses the
single yes probability. The ten-bin reliability table compares predicted probabilities with
observed frequencies, pooling option/label pairs for Choice and Score.

Confidence is a separate vendor value for Choice and Score. It is not the chosen option's
probability. The coverage table shows how many rows remain at each confidence threshold and
how often those retained answers are right. An empty selection has `null` accuracy.
The `thresholds` map controls the route printed by `try`. Your gold function decides how to use
that route; keep the uncertain output and route it to review rather than dropping the row.

Optional `floors` use `<question>.accuracy` as a minimum, or `<question>.mae` and
`<question>.brier` as maxima. A valid evaluation exits nonzero only if a declared floor is missed.
Invalid input and missing responses are errors, not successful evaluations. The example sets an
accuracy floor so CI catches changes to its offline workflow.

```sh
uv run --project functions pytest functions/tests/test_jev.py -q -m 'not docker'
```

Jev can misclassify even when the response has the right type. Its vendor notes describe weak
counting, arithmetic, date ordering and instruction-injection resistance. Use SQL for exact
calculations. Score is a judgment on a rubric, not a precise physical measurement.


## Store a question set

The existing `control.prompt` stores the question JSON. The existing `control.llm_step` pins its
hash and model. No separate prompt service or control table is needed.
After the question file passes review, a control writer can store a disabled configuration:

```sh
uv run --project functions mdp jev install instruments --source jev_instrument_family
```

This uses `MDP_CONTROL_RT_URL`. It inserts immutable rows and leaves the streamline disabled.
`mdp jev config instruments` prints the body and parameters without touching a database.
Each admitted run freezes its step; retrying that run keeps the same question set.


## Account setup for live calls

The account holder records and evaluates responses on a local stack first. The enabling steps below
refer to that local example. The deployed streamline stays disabled until the review and real
evaluation pass. The account holder completes these steps:

1. Create the TypeSafe account and put `TYPESAFE_API_KEY` in secret store, or configure the existing
   LiteLLM base URL and key alias for its `/typesafe/v1/systemone` passthrough.
2. Read the terms for retention and training on submitted state. Record the review in the rights
   registry. `typesafe` stays `license_ref=unverified` until that review exists.
   Review the provider's current terms before live use; this snapshot includes no legal review.
   Derived rows keep `learning_eligible=false`.
3. Supply a provider budget for `typesafe`. Its scope id is
   `uuid5(NAMESPACE_URL, "mdp:provider:typesafe")`. It needs `cap_requests`, `cap_cents` and
   `hard_action=pause`. Missing or uncapped rows refuse every live call.
4. Put the reviewed `max_cost_cents` and `input_token_microcents` in a params JSON file.
   Install a new step with `--params <file>` and, for LiteLLM, `--key-alias <alias>`.
   An operator enables the reviewed step and admits a global gold run through the usual control flow.
5. Record real responses into a new file using that run and its reservations:

   ```sh
   uv run --project functions mdp jev record instruments \
     --labels dbt/seeds/jev_instruments.csv --run-id <run_uuid> --cassette /tmp/instruments-real.json
   ```

   The run must be running, global and gold, with its streamline enabled and its frozen Jev step.
   Evaluate the recorded file before letting decisions drive any downstream action.

Every live attempt reserves against the existing budgets. Retries count toward the request cap.
The ledger keeps the request row and a separate input-token row under vendor `typesafe`.
Failed calls keep their reservation. Successful calls record actual token usage; a spend overrun
stops the caller. The account's reviewed rate supplies the cost conversion.

Only public, declared scalar fields may leave. Tenant runs and question sets marked tenant or
personal are refused. There are no tenant opt-ins. Identifiers, contacts and nested state are
refused as well. Field checks cannot recognize every name in prose: the author must review the
actual public fields, including question instructions, before recording. Never put private text
in labels, question files or cassettes. Cassettes store response bodies and state hashes, not state.
Gold output keeps input `_source_keys` and adds `typesafe`; rights annotate any future served rows.
The example has no served mart and ships disabled.

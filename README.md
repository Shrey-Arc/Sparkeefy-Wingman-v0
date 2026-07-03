# Sparkeefy AI Wingman v0

Streamlit UI → FastAPI backend → Wingman service → DeepSeek V4 Flash → validated JSON.

```
Streamlit UI (app.py)
        │
        ▼
FastAPI Backend (backend.py)
        │
        ▼
Wingman Service (wingman.py) ── Memory Manager (memory.py, memory.json, conversations.json)
        │                                              │
        ▼                                    Cache (cache.py, cache.json)
Prompt Builder (prompt.py)
        │
        ▼
DeepSeek V4 Flash
        │
        ▼
Pydantic-validated JSON (schemas.py)
```

No graph DB, no vector store, no auth, no Kubernetes — the assignment scores
tone/judgment/cost-awareness, not infrastructure.

## Files

| File | Responsibility |
|---|---|
| `schemas.py` | Pydantic models — the single source of truth for valid shapes |
| `memory.py` | Relationship memory + conversation history storage and rendering |
| `storage_utils.py` | Atomic, corruption-tolerant JSON read/write shared by memory.py and cache.py |
| `memory.json` | Flat JSON store, keyed by relationship name (ships with Aisha + Raj) |
| `conversations.json` | Per-relationship chat history (created on first message) |
| `cache.py` | Hash-based response cache to avoid repeat DeepSeek calls |
| `cache.json` | Cache storage (created on first live call) |
| `pricing_calendar.py` | Detects DeepSeek's announced peak-hour surge window |
| `prompt.py` | Builds the system + user messages, including few-shot tone examples |
| `wingman.py` | Calls DeepSeek (or mock), caches, falls back gracefully, validates |
| `backend.py` | FastAPI HTTP wrapper — chat, history, memory approval, health |
| `app.py` | Streamlit chat UI with live context-window sidebar |
| `batch_eval.py` | Runs `test_prompts.json` through `wingman.py` directly (history off) |
| `cli_chat.py` | Single-message CLI testing against any persona, readable output |
| `view_results.py` | Readable viewer for `results.jsonl` + auto-fills the eval sheet's objective columns |
| `test_prompts.json` | The 30 required fresh test prompts |
| `evaluation_template.csv` | Scoring sheet, fill in after a real batch run |

## Setup

```bash
pip install --break-system-packages -r requirements.txt
export DEEPSEEK_API_KEY="sk-..."          # from platform.deepseek.com
```

### Run the full app (UI + backend)

```bash
# terminal 1
uvicorn backend:app --reload --port 8000

# terminal 2
streamlit run app.py
```

Pick a relationship in the sidebar (or "— none —" to clear everything),
chat like a normal DM thread — it remembers the last 10 turns automatically.
No backend running? `app.py` falls back to calling `wingman.py` in-process.
No `DEEPSEEK_API_KEY`? Everything runs in mock mode automatically.

### Run the required 30-prompt batch for evaluation

```bash
python3 batch_eval.py test_prompts.json --out results.jsonl
```

Bypasses the UI/backend, calls the wingman service directly, and disables
conversation history so each of the 30 prompts is judged independently.

## Two personas seeded

`memory.json` ships with **Aisha** (girlfriend, 8 months in) and **Raj**
(best friend, 6 years) — each with real preferences, important dates,
communication habits, and freeform `notes`. Switching the sidebar dropdown
swaps the entire context window and chat thread; nothing leaks between them
(verified — see Known Limitations for what wasn't independently tested).

## Chat is continuous, not one-shot

`app.py` is a real per-relationship thread (Instagram-DM style), backed by
`conversations.json`. The last 10 turns replay into every prompt for
continuity. `batch_eval.py` explicitly sets `use_history=False` so the 30
scored test prompts stay independent and reproducible.

## Memory auto-detection — suggest, never auto-save

When a message states something new and durable ("she loves tulips", "his
birthday is march 12"), the model returns it as a `memory_candidate`
alongside its reply. The UI shows a ✅ Remember / ✖️ Discard chip — nothing
touches `memory.json` until the user taps Remember. In `--mock` mode this
uses a simple regex fallback just to demo the flow offline; real detection
happens inside DeepSeek's structured output with a live key.

## Context Window (sidebar)

Shows the exact human-readable memory block the model receives for whoever's
selected, plus a relative "last updated" time (recomputed every rerun, so it
updates the moment a memory candidate gets approved). Selecting "— none —"
clears the panel and stops the chat (`st.stop()`) instead of showing stale
context.

## API health vs. "a key is set"

`/health` separates `api_key_configured` (a key is present) from
`last_api_call` (whether the most recent real call actually succeeded) and
reports cache stats alongside. The sidebar surfaces all three, so it's
obvious whether the app is actually talking to DeepSeek or quietly on mock.

## Graceful fallback, not hard errors

A failed live call (network, rate limit, timeout after 3 retries) does **not**
raise or return an HTTP error — `wingman.py` falls back to mock and attaches
a `warning` string; the chat keeps working and the UI shows a small caption.
Verified by monkeypatching a broken API call: response still came back
`schema_valid: True` with the warning attached, never a hard failure.

## Batch testing across two personas

`test_prompts.json` has no `relationship` field on any of the 30 items, so
`batch_eval.py` defaults every one of them to `--relationship Aisha`
(configurable per-run, or per-item if you add a `"relationship"` key to
individual entries). This is intentional, not an oversight: the assignment's
30-prompt set is testing *situation handling* across categories, not
persona-specific memory — one consistent persona is the right baseline for
that comparison, and every record in the output already gets a
`relationship` field either way so it's traceable.

To also spot-check Raj specifically:
```bash
python3 batch_eval.py test_prompts.json --relationship Raj --out results_raj.jsonl
```
Heads up: the 30 prompts are written with romantic-partner framing ("she",
dates, flirting) — running them verbatim against Raj (a male best friend)
will read a bit mismatched pronoun-wise. Fine for confirming the pipeline
and memory isolation work correctly per-persona; if you want a *meaningful*
quality read on Raj specifically, write a small dedicated prompt set with
appropriate framing rather than reusing this one as-is.

**Reading results:** `results.jsonl` is one JSON object per line — not
meant to be eyeballed raw. Use:
```bash
python3 view_results.py results.jsonl                       # readable terminal view
python3 view_results.py results.jsonl --relationship Raj    # filter to one persona
python3 view_results.py results.jsonl --fill-sheet evaluation_template.csv --out evaluation_filled.csv
```
The last one auto-fills the objective columns (prompt, response, response
time, relationship) into a copy of the scoring sheet — the 1-5 judgment
columns are deliberately left blank, since that part still requires an
actual human read.

**Ad-hoc single-message testing:** the original CLI tool got folded into
`wingman.py`'s service module during the Streamlit refactor, so there wasn't
a simple way to test one message from the terminal without writing a Python
one-liner. `cli_chat.py` fills that gap:
```bash
python3 cli_chat.py Aisha "she replied haha to my long message"
python3 cli_chat.py Raj "he cancelled plans again" --mock
python3 cli_chat.py --list                                  # see available personas
python3 cli_chat.py Aisha "morning!" --history               # opt into real conversation history
```

## Model used: DeepSeek V4 Flash

**Not V3.1** — `deepseek-chat` (the old V3.1-era alias) is being retired by
DeepSeek on 2026-07-24. V4 Flash is the current model at the same price
point with native JSON-mode support.

**Why:**
- $0.14/M input, $0.28/M output (cache-miss). The static persona prompt is
  byte-identical across calls, so it hits DeepSeek's automatic prompt cache
  in production and drops to $0.0028/M input.
- Native structured/JSON output — required by the assignment's schema.
- Fast (non-thinking mode) — matters for something that should feel instant.
- The system prompt (`prompt.py`) includes two few-shot examples specifically
  to lock the "smart friend, not ChatGPT" tone and the off-topic redirect
  behavior, since DeepSeek defaults slightly more assistant-flavored than
  pricier alternatives without that anchoring.

**Estimated cost per 1,000 replies:** ~300 input / ~120 output tokens avg.
- Uncached, off-peak: (0.3M × $0.14) + (0.12M × $0.28) ≈ **$0.076 / 1,000 replies**
- With prompt caching + response caching, off-peak: **~$0.03–0.05 / 1,000 replies**
- **Peak-hour (see Surge Pricing below):** roughly **2x the above**

**Alternative considered:** Kimi K2.6 (~$0.55–0.70/1,000 replies) — less
formulaic tone in comparative testing, worth an A/B test if real eval scores
come back mediocre. Cost delta is still small in absolute terms at this
volume, and unlike DeepSeek, Kimi hasn't announced peak-hour surge pricing —
worth re-weighing if DeepSeek's surge turns out to bite harder than expected
once it's actually live.

## ⚠️ Surge pricing — real and imminent, not hypothetical

DeepSeek announced (2026-06-29) that when V4 exits preview for its official
release — **mid-July 2026**, i.e. very soon — it introduces **2x peak-hour
pricing**:

- **Peak:** Beijing 09:00–12:00 and 14:00–18:00 (UTC+8) → **UTC 01:00–04:00
  and 06:00–10:00** → roughly **6:30–9:30am and 11:30am–3:30pm IST**
- **Off-peak:** everything else, stays at today's baseline rate
- Not active during today's preview pricing, but ships this month

This matters more than it might look: that window overlaps a big chunk of
normal daytime chat usage, and unlike a batch job, **we can't schedule around
it for live chat** — a user texting their girlfriend at 1pm doesn't care that
it's peak pricing. `pricing_calendar.py` makes this visible instead of
letting it hide in a stale README number:
- `/health` and the sidebar show whether peak pricing is likely active right
  now, based on the announced windows
- `batch_eval.py` — which *is* schedulable, since it's not a real-time user
  request — prints a warning and suggests re-running off-peak if launched
  during a peak window
- Live chat has no mitigation beyond caching (below); surge on interactive
  traffic is a genuine, unavoidable cost increase to plan for, not solve

## Caching — reduce real API calls

`cache.py` hashes the exact outgoing `messages` array and skips the DeepSeek
call entirely on an exact repeat within the TTL (default 1 hour,
`CACHE_TTL_SECONDS` env var). Deliberately simple — no embeddings, no fuzzy
matching — it just catches genuine repeats (a Streamlit rerun re-firing the
same request, a resent message, re-running `batch_eval.py` unchanged).
Verified: two identical calls in a row triggered exactly one real API call.
Stacks with DeepSeek's own prompt-caching on the static system prompt (98%
off the cache-hit portion) for further cost reduction in production — and
this stacking matters more once peak surge lands, since caching is the one
lever that still works during peak hours when scheduling doesn't.

## Known limitations / honest gaps

- **This sandbox can't reach `api.deepseek.com`** (network is allowlisted to
  a fixed set of domains that doesn't include it). Everything up to that
  boundary — schemas, memory, history, caching, fallback logic, both
  personas, the FastAPI backend, the Streamlit UI, and the full 30-prompt
  batch — has been run and verified end-to-end, including the graceful
  fallback path (tested via a monkeypatched failure) and the cache
  hit/skip behavior (tested via a monkeypatched success). What's *not*
  independently verified is DeepSeek's real output quality, real latency,
  and real retry/429 behavior — **run `batch_eval.py` with a real
  `DEEPSEEK_API_KEY`** for the actual submission numbers.
- Mock-mode memory-candidate detection is regex-based and only catches a
  handful of phrasing patterns — it's a demo of the UI flow, not a claim
  about extraction quality. Real detection is model-driven once live.
- Cache is exact-match only (hash of the full message array) — no semantic/
  fuzzy matching, so paraphrased repeats won't hit it. That's a deliberate
  v0 simplification, not an oversight.
- **Bugs found and fixed during actual live-API testing** (not caught by
  sandbox mock testing, since they only manifest against a real network/API):
  1. The Streamlit sidebar was checking its own process's `DEEPSEEK_API_KEY`
     env var, not the backend's — if you `export` the key only in the
     terminal running `uvicorn` and not the one running `streamlit`, the
     sidebar wrongly said "mock mode" even though real calls were succeeding.
     Fixed: when the backend is reachable, the sidebar now trusts
     `/health`'s `api_key_configured`, not its own env.
  2. `get_wingman_response()`'s graceful fallback only caught network-level
     failures (`WingmanError`) — a live call that succeeded at the HTTP
     level but returned truncated/invalid JSON, or valid JSON that failed
     schema validation, still hard-errored as a 502. Fixed: both failure
     modes now trigger the same mock fallback + warning as a network error.
  3. The Streamlit client's HTTP timeout on `/chat` (20s) was shorter than
     the backend's worst-case retry duration (3 attempts × 30s + backoff ≈
     95s), so a slow-but-eventually-successful call could time out
     client-side while still "succeeding" on the backend. Fixed: retries
     tightened to 2 attempts × 15s (~31.5s worst case) and client timeout
     raised to 40s, so the two are actually consistent with each other.
- **Storage layer hardened after a full audit** (`storage_utils.py`, new):
  1. `memory.json`/`conversations.json` had no corruption handling at all —
     a truncated write (crash mid-write, `--reload` restart, Ctrl+C) left a
     file that crashed the *entire app* on the next read. `conversations.json`
     gets written on every chat message, making this a realistic failure
     mode, not a theoretical one. Fixed: reads now degrade to a fresh default
     with a warning + a `.corrupted-<timestamp>` backup instead of raising.
  2. All JSON writes were non-atomic (`open(path, "w")` then `json.dump`),
     leaving a window where a crash mid-write produces exactly the corrupted
     file described above. Fixed: every write now goes to a temp file first,
     then `os.replace()`s it — atomic on POSIX and Windows.
  3. FastAPI runs synchronous route handlers in a thread pool, so two
     concurrent `/chat` calls for the same relationship could genuinely
     race: both read the same history, both append their turn, and the
     second write silently clobbers the first (a lost turn). Verified with
     a 50-thread concurrent-increment test that failed before the fix.
     Fixed via a reentrant per-file lock (`storage_utils.get_lock`) held
     across the full read-modify-write, not just each individual read or
     write — confirmed 50/50 updates survive concurrently afterward.
  4. A single malformed relationship record (not the whole file — e.g. one
     entry with a corrupted field from a bad manual edit) crashed the
     request for that relationship via an uncaught Pydantic `ValidationError`.
     Fixed: falls back to a fresh profile for that relationship with a
     warning, leaving the on-disk record untouched in case it's recoverable.
- **Bugs found from actual multi-turn chat usage** (a second round of real
  testing, this time exercising the chat feature over several turns instead
  of single messages):
  1. **The "invalid JSON" fallback was firing far more than it should.**
     Root cause: `_try_parse_and_validate` did a raw `json.loads()` with zero
     preprocessing. Models — DeepSeek included — frequently wrap structured
     output in markdown fences (` ```json ... ``` `) or add a stray sentence
     before/after the JSON, even with `response_format=json_object` set.
     That's not malformed output, just decorated valid output, and treating
     it as unrecoverable meant throwing away good responses and falling
     back to mock unnecessarily — which is also why the mock's answer
     started looking suspiciously repetitive: it was showing up way more
     often than a v0 demo mode should. Fixed: added a repair step that
     strips fences and extracts the first balanced `{...}` block before
     giving up. Tested against 6 realistic wrapped/prefixed/suffixed cases —
     all recovered; a genuinely truncated response still correctly fails
     and falls back.
  2. **Follow-up questions were shown but never remembered.** Conversation
     history only stored `wingman_response`, never `follow_up_question` — so
     if the Wingman asked "does she call her mom 'mom' or by name?", that
     question vanished from the model's own memory the instant the next
     message came in. A one-word answer like "she calls her mom" then got
     interpreted completely out of context (in one real test, as "she's on
     a call with her mom right now"), because the model had no record of
     having asked the question at all. Fixed: the follow-up question text
     now gets appended to the stored assistant turn, verified by replaying
     the exact scenario and confirming the question text survives into
     history.
  3. **Approving a memory candidate could silently fail.** The client sent
     the approve request but never checked the response status — a
     rejected/failed request still showed a "Saved" toast and the sidebar
     correctly showed nothing new, which looked like the memory system was
     broken rather than like a request that had actually failed. Fixed:
     the approve call now returns success/failure explicitly, and a failure
     shows a real error instead of a false-positive toast. Also added
     `storage.memory_path`/`history_path` to `/health` plus a sidebar check
     that flags it loudly if the backend and the Streamlit process ever
     resolve to two different `memory.json` files (e.g. two separate copies
     of the project running side by side) — the most likely real-world
     cause of "I approved it but it's not showing up."
  4. The mock fallback's default response (used when no keyword template
     matches) was a single fixed string, which became very noticeable once
     fallback #1 above was making it fire more than intended. Now picks
     from 3 variants, chosen deterministically by input rather than the
     same line every time — a smaller fix riding along with #1, since
     reducing fallback frequency doesn't help much if the fallback itself
     still looks broken when it does happen.

## Next steps to finish the submission

1. Run `batch_eval.py` with a real API key → get real `results.jsonl`
2. Fill `evaluation_template.csv` scores against the real outputs
3. Record 3–5 min demo video (Streamlit UI + a couple of batch runs)
4. Push to GitHub, write the tradeoffs/next-7-days note
5. Send the 6-hour and 24-hour progress updates per the brief

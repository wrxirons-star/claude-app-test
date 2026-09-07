# Surplus Funds Recovery Agent

A Python command-line agent, built on the Anthropic SDK, for a public-record
surplus funds recovery business in **Florida, Texas, and Georgia**.

When a property is sold at a foreclosure or tax sale for more than what was
owed, the leftover money ("surplus" or "excess proceeds") sits with a county
clerk, district clerk, or tax commissioner until the former owner claims it.
This tool finds those funds in public records, locates the people entitled to
them, drafts compliant outreach and agreements, and assembles the claim packet.

The agent does the research and drafting. The fee caps, waiting periods,
deadlines, and contact-channel rules are code, not prompts, so the model cannot
talk itself out of them.

## What it does

| Command | API call? | What happens |
|---|---|---|
| `surplus find FL --county Sumter` | yes | Finds the county's surplus list online, reads it, saves leads at or above the minimum, scores them. |
| `surplus locate 12` | yes | Skip-traces the owner from official records first, saves candidates with confidence and source URL. |
| `surplus prepare 12` | yes | Finds the county's claim form and mailing address, then builds the packet. |
| `surplus run "..."` | yes | Any free-form task with the same tools and rules. |
| `surplus import list.csv --state GA --county Cobb --sale-type tax_sale` | no | Bulk-load a list you downloaded yourself. |
| `surplus cases`, `show`, `note`, `set`, `score` | no | Pipeline management. |
| `surplus draft 12 agreement` | no | Render a letter, script, agreement, checklist, or cover letter. Blocked if it would break a rule. |
| `surplus packet 12` | no | Build the packet folder (checklist, cover letter, agreement, compliance and economics records). |
| `surplus rules TX`, `surplus fee TX 25000 --sale-date 2025-06-01` | no | Read the rulebook; compute deadline, expected gross, capital needed. |

## The rules that shape the business (read this first)

These are encoded in `surplus/statutes/*.json` with citations. Verify them
against the current statute before your first filing in each county; the JSON
marks the points that need confirmation with `[VERIFY]`.

**Florida** (Fla. Stat. 45.032, 45.033, 197.582, ch. 717)
- Foreclosure surplus: the owner of record has a 60-day priority window after the sale. Compensation to an assignee is capped at **12 percent**. Unclaimed money goes to the state after one year, after which only a registered claimant's representative may act, at a 20 percent cap, on state forms.
- Tax deed surplus: non-owner claims are barred 120 days after the clerk mails the Notice of Surplus. The tool applies the 12 percent cap as policy.

**Texas** (Tex. Tax Code 34.04)
- **A non-attorney may not charge a fee** to obtain excess proceeds. An attorney's fee is capped at the lesser of 25 percent or $1,000.
- The only lawful non-attorney model is buying the claim: written assignment, signed **36 or more days after deposit**, **no phone or in-person solicitation**, **at least 80 percent paid at signing**, and the court will not pay the assignee more than 125 percent of what was paid.
- Petition deadline: two years from the sale.
- The tool never renders a Texas fee agreement or phone script, and it strips phone numbers from Texas contacts.

**Georgia** (O.C.G.A. 48-4-5, 44-12-224)
- Fee capped at **10 percent**. Agreements are **unenforceable for 24 months** after the funds were escrowed. Funds are paid to the claimant, never to the finder. Unclaimed funds go to the state after five years.
- So the working window is years two through five after the sale. The scorer discounts leads that are still inside the 24 months.

Everything the tool writes discloses that the owner can claim for free, that
you are a private company, and the exact fee. Do not remove those lines.

## Setup

```bash
pip install -e ".[dev]"
export ANTHROPIC_API_KEY=sk-ant-...      # or `ant auth login`
surplus init                              # creates ~/.surplus/config.json
```

Edit `~/.surplus/config.json` with your business name, address, phone, email,
fee policy, and (optionally) an attorney partner. Fee policy above the cap is
rejected at draft time.

Environment variables: `SURPLUS_HOME` (data dir), `SURPLUS_MODEL` (default
`claude-opus-5`), `SURPLUS_EFFORT` (`low` to `max`, default `high`).

## A first run

```bash
surplus rules GA                         # read the rulebook
surplus find GA --county Gwinnett --min-amount 8000
surplus cases --state GA                 # ranked by score
surplus show 3
surplus locate 3                         # skip-trace
surplus draft 3 intro_letter --print     # mail this
surplus set 3 status=engaged extra.claimant_address="12 Oak St, Lawrenceville, GA 30046"
surplus prepare 3                        # forms + packet
```

Or skip the API entirely: download a county list as CSV and `surplus import` it.
Column names like Case No / Defendant / Surplus / Sale Date are recognized.

## Layout

```
surplus/
  cli.py         argparse commands (no SDK import unless an agent command runs)
  agent.py       tool-runner loop: claude-opus-5, adaptive thinking, web_search + web_fetch,
                 prompt-cached system prompt, server-side refusal fallbacks
  tools.py       @beta_tool functions the model can call (all validated, all audited)
  prompts.py     system prompt + task prompts for find / locate / prepare
  rules.py       economics, deadlines, compliance gate
  statutes/      fl.json tx.json ga.json  (the rulebooks, with citations)
  scoring.py     deterministic lead ranking
  documents.py   template renderer behind the compliance gate
  templates/     letter, phone script, FL/TX/GA agreements, checklist, cover letter (Markdown)
  packet.py      packet folder builder
  store.py       SQLite: cases, contacts, notes, sources, documents, audit log
tests/           33 tests, no network
```

## Design notes

- **Model**: `claude-opus-5` with adaptive thinking and `effort=high`. Set `SURPLUS_EFFORT=xhigh` for hard skip-traces, `low` for cheap list scraping.
- **Web tools** are Anthropic's server-side `web_search` and `web_fetch` (with dynamic filtering), so there is nothing to host and no scraper to maintain. `web_fetch` only opens URLs that appeared in search results or that you put in the task text.
- **Sources are remembered.** Every portal, list, docket, or form page the agent finds is saved to the `sources` table and fed back into the next run's prompt.
- **Refusal fallbacks** are on (`fallbacks="default"`), so a safety-classifier decline is retried server-side on a fallback model instead of ending the run.
- **Audit log.** Every create, update, note, contact, and document is written to the `audit` table with an actor (`cli` or `agent`).

## Where it makes money, honestly

Expected gross per lead is roughly 12 percent of the surplus in Florida, 10
percent in Georgia (after a two-year wait), and about 20 percent of the claim
in Texas if you buy at the 80 percent floor, which ties up capital until the
court pays. Mortgage foreclosure surpluses are often reduced by junior liens,
so tax sale surpluses in Georgia and Texas are the cleaner targets. Run
`surplus fee` on real numbers before you mail anything.

## What still needs a human

- An attorney review of the three agreement templates before first use in each county.
- Registration with Florida DFS if you want to work funds already remitted to the state.
- A Texas attorney relationship if you want any fee model there.
- Notarization, mailing, and phone calls. The tool prepares; it does not send.

Nothing here is legal advice.

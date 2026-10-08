# Phase 14 - answer quality, before and after

- generated_by: `run_phase14_answer_review.py` (phase14.3.3-1.0)
- created_at: 2026-10-08T16:14:00+00:00
- questions reviewed: 2

Both sides ran the same retrieval against the same index. Nothing was re-embedded, no weight was changed, and no model graded anything here - every number below is a count over the answer text, from `scripts/answer_quality.py`.

**How to read "marked closing conclusion".** It counts a closing line that opens with a synthesis marker. It is NOT the same as "the answer reaches a conclusion": the prompt asks for the conclusion in the TAKEAWAY, and an answer that puts it there and then adds a closing line as well is repeating itself. A low count here is a reason to read the takeaways, not evidence that the answers only transcribe.

## The corpus, in numbers

| measure | before | after | change |
|---|---|---|---|
| Mean words, whole answer | 893.0 | 383.0 | -510.0  ✓ |
| Mean words, Direct Answer | 367.5 | 155.5 | -212.0  ✓ |
| Mean words, sections 2-6 | 507.5 | 209.5 | -298.0  ✓ |
| Mean claims in Direct Answer | 8.5 | 2.5 | -6.0 |
| Mean repetition in Direct Answer | 0.0 | 0.0 | same |
| Answers opening with boilerplate | 0 | 0 | same |
| Answers with a marked closing conclusion | 0 | 0 | same |
|   of those, conclusions CITED | 0 | 0 | same |
|   of those, conclusions UNCITED | 0 | 0 | same |
| Answers saying what changed | 1 | 1 | same |
| Answers naming a publisher | 1 | 1 | same |
| Substantive answers in the 100-220 word band | 0 | 2 | +2  ✓ |
| Uncited claims across the corpus | 0 | 0 | same |
| Direct Answers over 220 words | 2 | 0 | -2  ✓ |
| Unsupported answers over 80 words (whole answer) | 0 | 0 | same |
| Mean words, unsupported answers (whole answer) | 0 | 0 | same |
| Answers whose generation did not finish | 0 | 0 | same |

A tick marks a measure that moved the way the phase intended. It is a direction, not a proof: the only claims of improvement in this report are the ones labelled IMPROVED below, each of which has a deterministic rule behind it.

Verdicts across all questions: **2 IMPROVED**, 2 CHANGED, **0 REGRESSED**.

## Question by question

### MSFT - risk_or_credit

**What risks does the research identify for Microsoft around AI investment and cloud capacity?**

| | before | after |
|---|---|---|
| words (Direct Answer) | 377 | 198 |
| words (whole answer) | 897 | 480 |
| citations used | 1,2,4,5,6,7,8 | 1,2,4,5,6,7,8 |
| confidence | Medium | Medium |
| documents selected | 8 | 8 |
| source set identical | yes | |

Observed changes:

- **CHANGED** - shorter by 179 words, but points fell from 9 to 3 - shorter is not better if evidence went with it
- **IMPROVED** - moved into the 100-220 word band

<details><summary>before</summary>

```
## Automated checks flagged this answer

- 1 supplied chunk(s) were never cited - the evidence pack was wider than the answer used

_These are deterministic checks on the generated text. Nothing was rewritten - read the answer with these in mind._

---

## 1. Direct Answer
- Analysts and customers flagged Azure capacity constraints, including reports that “there is no capacity in certain regions or it is 20% higher cost,” and that capacity constraints “could skim off some of the Azure upside potential in 4Q/Jun” [1].  
- Multiple sell‑side notes state Azure “remains capacity‑constrained,” with analysts expecting constraints to continue through 2026/2027 and noting the constraint limits upside to Azure revenue [4][5][6].  
- Research highlights a specific supply‑allocation risk: incremental capacity may be allocated toward Microsoft’s first‑party (1P) applications and internal R&D rather than to external Azure customers, which “limits upside to Azure revenue” and could delay revenue from Copilot and Microsoft AI Infrastructure (MAI) [6].  
- UBS research observed uncertainty about the cause of the capacity reports—whether Microsoft added less capacity than in prior quarters or “allocated more of its incremental capacity to internal needs”—and said that dynamic could reduce Azure growth potential in the near term [1].  
- Analysts and research notes flag concentration/exposure risk tied to large AI partnerships, noting investor concern that Microsoft may be “too tightly exposed to OpenAI” and that large OpenAI commitments are being monitored as risky (Morningstar describes the ~$250 billion OpenAI commitment through 2032 as “somewhat risky”) [2][4][5].  
- Researchers warn that heavy AI investment and rapid data‑center expansion raise a return risk: Morningstar notes “capex is growing significantly faster than Azure,” which “surfaces the concern that Microsoft may struggle to earn returns on its rapid data center expansion” [5].  
- Sell‑side research also identifies competitive risk for Microsoft’s Copilot/application strategy, warning Microsoft “will face more competition in its application business and not be able to compete against AI natives with Copilot” [6].  
- Microsoft’s 10‑K flags AI‑related operational, regulatory, legal, and reputational risks, including that AI models or training datasets “may be flawed,” AI outputs “may be offensive, illegal, inaccurate, or otherwise harmful,” and that ineffective AI practices or evolving regulation could cause “legal liability, regulatory action, litigation, brand, reputational, or competitive harm” [7][8].  
- The 10‑K also identifies infrastructure risk more broadly: Microsoft states it “may be unable to develop and expand adequate infrastructure,” and that its infrastructure capacity depends on the availability of (text truncated in the retrieved chunk) [7].  

## 2. Evidence Used
- [1] (UBS): customer quotes on capacity, UBS discussion that capacity constraints surfaced and could reduce Azure upside.  
- [6] (Goldman Sachs): explicit “Concern #1” that supply is constrained and incremental capacity is being allocated to 1P/internal R&D; “Concern #2” on competitive risk to Copilot.  
- [4] and [5] (Morningstar): statements that Azure “remains capacity‑constrained” through 2026/2027 and that capex is growing faster than Azure, raising return concerns; monitoring the OpenAI commitment as risky.  
- [2] (Bernstein): investor concern that Microsoft may be “too tightly exposed to OpenAI” and related commentary on OpenAI capacity usage.  
- [7] and [8] (Microsoft 10‑K excerpts): company‑filed disclosures of AI development/deployment risks, regulatory/legal/reputational risk, and the risk of failing to expand adequate infrastructure.

## 3. Source Citations
- [1] MSFT UBS 7.26.26.pdf, pages 4–5.  
- [2] MSFT Bernstein 7.30.26.pdf, pages 5–6.  
- [4] MSFT Morningstar 7.29.30.pdf, page 2.  
- [5] MSFT Morningstar 1.28.26.pdf, page 2.  
- [6] MSFT GS 2.4.26 Industry.pdf, pages 3–4.  
- [7] MSFT 10K 7.29.26.pdf, pages 21–22.  
- [8] MSFT Business & Risk 10K FY25 6.30.25.pdf, page 19.

## 4. Evidence Limitations
- UBS’s note references a Figure about survey results but the excerpt does not include the figure or the quantitative survey detail beyond saying <10% flagged “material” constraints, limiting precision on how widespread constraints are [1].  
- The Microsoft 10‑K infrastructure sentence is truncated in the retrieved chunk, so the full description of the infrastructure‑dependency factors is not available here [7].  
- The UBS token‑optimization discussion is cut off in the provided text, so any specific conclusions about “Token Optimization and the Impact on Azure Growth” cannot be cited from these chunks [1].  
- Dollar amounts and commitment figures (e.g., the ~$250 billion OpenAI commitment) are mentioned in analyst notes but the underlying contract details and terms are not included in these excerpts [4][5][2].  
- Some analyst statements reflect opinion and interpretation rather than primary company disclosures; the available excerpts mix anecdotal customer quotes, analyst concerns, and company filings without full context for each claim [1][6][4].

## 5. Needs Human Review
- Verify the full UBS report (including Figure 2 and the token‑optimization section) to confirm survey statistics and the complete UBS analysis [1].  
- Review the complete Microsoft 10‑K text around infrastructure capacity to capture the full wording and any enumerated dependencies or mitigants that are truncated in the excerpt [7][8].  
- Check the original Morningstar and Bernstein notes for full context on the cited OpenAI commitment figures and any caveats or qualifiers those firms provided [2][4][5].  
- Confirm Goldman Sachs’ full “Concern #1” and “Concern #2” analyses and any recommended timelines or scenarios they outline regarding capacity coming online [6].  
- If using these points for decision‑making, inspect source dates and any subsequent company disclosures or capacity announcements that postdate these excerpts.

## 6. Confidence
High — multiple analyst research notes and Microsoft’s own 10‑K in the retrieved evidence consistently identify capacity constraints, allocation of incremental capacity to first‑party/internal uses, large AI partnership concentration (OpenAI) exposure, competitive risks for Copilot, capex/return concerns, and AI‑specific legal/regulatory/reputational risks [1][6][4][5][2][7][8].
```

</details>

<details><summary>after</summary>

```
## Automated checks flagged this answer

- 1 supplied chunk(s) were never cited - the evidence pack was wider than the answer used

_These are deterministic checks on the generated text. Nothing was rewritten - read the answer with these in mind._

---

## 1. Direct Answer
The research identifies two main risk areas for Microsoft: (1) Azure capacity constraints that could limit Azure growth or force customers into higher‑cost or pay‑as‑you‑go arrangements [1][6][4][5], and (2) large AI‑related investments and contractual exposures (including sizable OpenAI commitments, rising capex and lease/accounting issues) plus AI deployment risks that could create financial, operational, reputational, or regulatory liabilities [2][5][4][7][8].  
- Capacity risk: UBS reports customers being told there is “no capacity in certain regions or it is 20% higher cost” and warns this dynamic “could skim off some of the Azure upside potential in 4Q/Jun” [1], and Goldman Sachs highlights incremental capacity being allocated to 1P apps and internal R&D that “limits upside to Azure revenue” [6][4].  
- Investment and AI deployment risk: Bernstein and Morningstar flag large OpenAI/OpenAI‑related commitments as investor risks (noting >$280B and ~$250 billion figures reported by analysts) and capex/lease accounting changes that affect reported margins and capex optics [2][5][4], while Microsoft’s 10‑K warns it “may be unable to develop and expand adequate infrastructure” and that AI development/deployment “may result in reputational, competitive, or legal harm” [7][8].

## 2. Evidence Used
- UBS note (customer checks) — cited for Azure capacity anecdotes and downside to Azure upside [1].  
- Goldman Sachs industry note — cited for capacity allocation to 1P/R&D limiting Azure revenue [6].  
- Morningstar research (Jul 29, 2026) — cited for capacity-constrained commentary and OpenAI commitment monitoring [4].  
- Morningstar research (Jan 28, 2026) — cited for OpenAI commitment and capex/return concerns [5].  
- Bernstein research — cited for the >$280B OpenAI/Azure revenue mention and investor concern context [2].  
- Microsoft 10‑K (Item 1A / Operational risks) — cited for infrastructure/inability-to-expand and AI deployment legal/reputational risks [7].  
- Microsoft Business & Risk 10‑K FY25 — cited for AI development/deployment and content/moderation risk language [8].

## 3. Source Citations
[1] MSFT UBS 7.26.26.pdf pp. 4-5  
[2] MSFT Bernstein 7.30.26.pdf pp. 5-6  
[4] MSFT Morningstar 7.29.30.pdf pp. 2-2  
[5] MSFT Morningstar 1.28.26.pdf pp. 2-2  
[6] MSFT GS 2.4.26 Industry.pdf pp. 3-4  
[7] MSFT 10K 7.29.26.pdf pp. 21-22  
[8] MSFT Business & Risk 10K FY25 6.30.25.pdf pp. 19-19

## 4. Evidence Limitations
- No supplied chunk quantifies the potential revenue or timing impact of capacity constraints on Azure (e.g., $ or quarters affected).  
- The excerpts include analyst characterizations and selective company 10‑K passages but lack comprehensive, up‑to‑date management commentary tying capex cadence to specific capacity additions.

## 5. Needs Human Review
- Verify the exact context and current status of the cited OpenAI financial commitments and any subsequent amendments beyond these notes.  
- Check full 10‑K/earnings disclosures and management commentary for updated capex guidance, capacity‑add cadence, and explicit causes of the reported constraints.

## 6. Confidence
High — multiple analyst research notes and Microsoft 10‑K excerpts in the pack directly document capacity constraints, large AI‑related investment/contract concerns, and AI deployment/regulatory risk, supporting the answer.
```

</details>

### AMZN - analyst_concern

**What concerns do analysts raise about capital expenditure on AI infrastructure and its effect on free cash flow at Amazon?**

| | before | after |
|---|---|---|
| words (Direct Answer) | 358 | 113 |
| words (whole answer) | 889 | 286 |
| citations used | 2,3,4,5 | 2,5 |
| confidence | Medium | Medium |
| documents selected | 8 | 8 |
| source set identical | yes | |

Observed changes:

- **CHANGED** - shorter by 245 words, but citations fell from 4 to 2 - shorter is not better if evidence went with it
- **IMPROVED** - moved into the 100-220 word band

<details><summary>before</summary>

```
## Automated checks flagged this answer

- 4 supplied chunk(s) were never cited - the evidence pack was wider than the answer used

_These are deterministic checks on the generated text. Nothing was rewritten - read the answer with these in mind._

---

## 1. Direct Answer
- Analysts flag very large AI-related capital spending — including a $200 billion 2026 capex guidance and a cumulative path that “could cumulatively exceed $1 trillion by 2030” — as the driver of the raise in Amazon’s capital intensity [4].  
- Credit analysts note AWS capital intensity has surged: a capex/sales proxy for AWS was “75% last year and now is over 100%,” with capital spending more than doubling in the first quarter, highlighting how AI infrastructure is consuming an outsized share of revenue [2].  
- Analysts report AWS margin pressure associated with the spending: AWS operating margin “shrank by 180 basis points to 37.7%” in the quarter cited, reflecting deleveraging of technology and infrastructure expense rates [2].  
- Several analysts warn the ramp in capex has materially depressed free cash flow: one note says supercharged capital spending “sent free cash flow plummeting to a use of $18 billion” in the quarter reported [2].  
- Another analyst note states full-year free cash flow fell sharply—“was only $8 billion, down from $33 billion in 2024”—and attributes that decline “entirely” to higher capital spending [5].  
- Analysts express skepticism about the timing and reliability of future revenue tied to capex commitments, noting management’s statement that customer commitments justify spending but that such revenue “won’t materialize until 2027-2028” and that the analytics team is “trifle skeptical, given the fragile financial condition of these AI startups” [2].  
- Concerns about funding the spending include use of cash and new borrowing: one note records a $15 billion preliminary OpenAI investment and states management said it “would fund this with cash on hand,” while also noting $53 billion of borrowing in the quarter, and an analyst scenario that projects a combined free-cash-flow shortfall plus OpenAI investments could imply “$90 billion in additional debt” under conservative assumptions [2].  
- At the same time, some sell‑side/credit research argues the spending may be manageable for Amazon given its liquidity and projected cash generation, citing a large cash balance and forecasts that free cash flow could recover to exceed $100 billion annually within several years; these views temper but do not eliminate the near‑term free‑cash‑flow concerns [3][4][5].

## 2. Evidence Used
- [2] (AMZN Gimme Credit 5.1.26): provided details on AWS capex/sales ratio, AWS margin contraction, Q1 capex doubling, OpenAI investment, borrowing, and a projected FCF shortfall/use of cash of $18 billion.  
- [5] (AMZN GimmeCredit 2.6.26): provided the $200 billion 2026 capex note and the full‑year free cash flow declining to $8 billion from $33 billion and analysts’ questions about whether investments will “consume all of the company’s cash flow every year into perpetuity.”  
- [4] (AMZN BI 2.19.26): provided Bloomberg Intelligence’s $200 billion 2026 capex guidance, the $1 trillion-by-2030 cumulative statement, and context about cash and Ebitda supporting spending.  
- [3] (AMZN BI 7.31.26 Credit): provided sell‑side/credit commentary that free cash flow was expected to exceed $100 billion annually within five years and noted market reactions (bond spreads) in the context of rising capex.

## 3. Source Citations
- [2] AMZN Gimme Credit 5.1.26 (2).pdf, page 1.  
- [5] AMZN GimmeCredit 2.6.26 Credit.pdf, page 1.  
- [4] AMZN BI 2.19.26.pdf, pages 1-2.  
- [3] AMZN BI 7.31.26 Credit.pdf, pages 14-15.

## 4. Evidence Limitations
- The Bloomberg Intelligence chunk labeled “4. $200 Billion Capex Only a Modest Dent to Cash” is truncated in the provided extract and does not include the full supporting text for that assertion [4].  
- Projections and scenarios differ across the analyst notes (some emphasize near‑term FCF declines and debt scenarios [2][5]; others project FCF recovery above $100 billion within years [3][4]), but the evidence set does not include the underlying models or full assumptions behind those divergent forecasts.  
- The documents provide limited contractual detail on the “customer commitments” cited (e.g., OpenAI/OpenAI‑related commitments): the evidence notes management statements and analyst skepticism but does not reproduce contract terms, timing specifics beyond “2027-2028,” or probabilities of revenue realization [2].  
- The pack lacks contemporaneous company financial filings or the full earnings‑call transcripts that would clarify management’s funding plans and exact timing of cash flows referenced by analysts.

## 5. Needs Human Review
- Verify the full Bloomberg Intelligence text around the “modest dent to cash” statement to confirm context and assumptions (the provided excerpt is truncated) [4].  
- Review the underlying models or spreadsheets used by the analysts to reconcile divergent FCF and leverage projections and to validate the $90 billion additional‑debt scenario [2][5].  
- Check the full terms, timing, and enforceability of the cited customer commitments and OpenAI/Anthropic investments in primary source documents (contracts, filings) to assess the revenue timing assertions [2].  
- Confirm the most recent company cash balances, capex guidance, and 10‑Q/10‑K disclosures not included in these extracts to ensure numbers (cash, borrowing) are up to date [2][5].  
- Obtain the complete earnings‑call transcript and management commentary to verify what management said about funding strategy and capital‑allocation priorities beyond the snippets in these notes [2][4][5].

## 6. Confidence
Medium. Multiple analyst and credit‑research excerpts in the evidence set consistently raise near‑term concerns that AI infrastructure capex has materially depressed free cash flow and increased financing questions, but the set contains divergent forecasts (some expect eventual strong FCF recovery) and lacks full underlying models and complete source documents to make a higher‑confidence synthesis [2][3][4][5].
```

</details>

<details><summary>after</summary>

```
## Automated checks flagged this answer

- 6 supplied chunk(s) were never cited - the evidence pack was wider than the answer used

_These are deterministic checks on the generated text. Nothing was rewritten - read the answer with these in mind._

---

## 1. Direct Answer
Gimme Credit and other sell‑side analysts warn that Amazon’s large AI-related capital expenditures have materially depressed near‑term free cash flow, with one note saying free cash flow “plummeted to a use of $18 billion” and another saying FCF was “only $8 billion, down from $33 billion, entirely attributable to the higher capital spending” [2][5]. Analysts also flag timing and funding risk: they note AWS capex is supported by customer commitments whose revenue “won’t materialize until 2027-2028,” that some commitments are to financially fragile AI startups, and that analyst scenarios assume tapping cash or borrowing (one hypothetical adds ~$90 billion of debt), raising near‑term leverage/financing concerns [2][5].

## 2. Evidence Used
- [2] Gimme Credit 5.1.26 — cited for FCF impact, customer‑commitment timing, and hypothetical debt modeling.  
- [5] Gimme Credit 2.6.26 — cited for year‑over‑year FCF decline attribution and analysts’ investor questions about payoff sustainability.

## 3. Source Citations
- [2] AMZN Gimme Credit 5.1.26 (2).pdf p.1  
- [5] AMZN GimmeCredit 2.6.26 Credit.pdf p.1

## 4. Evidence Limitations
- All cited material is analyst research (no company‑direct filings or management guidance are present in the supplied chunks).  
- The pack lacks a detailed capex phasing schedule and definitive management funding plan for the AI investments.

## 5. Needs Human Review
- Verify the cited FCF and capex figures and the timing of customer commitments against Amazon’s SEC filings and management disclosures.  
- Confirm the plausibility and sensitivity of the analyst debt‑assumption scenarios vs. the company’s actual cash/debt position.

## 6. Confidence
Medium — multiple analyst notes in the pack directly report the concerns cited, but the evidence set is limited to third‑party commentary and lacks company disclosures or a full capex/cash‑flow schedule.
```

</details>

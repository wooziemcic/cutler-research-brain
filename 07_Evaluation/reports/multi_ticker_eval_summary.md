# Phase 12 golden evaluation - all indexed tickers

_phase12.1-1.0 · 2026-09-15T16:11:53+00:00_

**51 PASS · 9 REVIEW · 0 FAIL** across 60 question(s) and 10 ticker(s). Estimated cost $0.4027.

> **A refusal is not a failure.** Two of the six categories per ticker exist to be
> declined: a question the archive cannot answer, and a request for a recommendation.
> A ticker that declines those is telling the truth about itself. The failures that
> matter are a recommendation, a confident answer with no citation, a figure that is
> not in the retrieved text, and a source belonging to another company.

> AI drafts. Humans verify. Cutler decides.

## By ticker

| Ticker | Questions | PASS | REVIEW | FAIL | Cost |
|---|---:|---:|---:|---:|---:|
| ABBV | 6 | 6 | 0 | **0** | $0.0441 |
| AMZN | 6 | 4 | 2 | **0** | $0.0417 |
| B | 6 | 2 | 4 | **0** | $0.0377 |
| KIM | 6 | 6 | 0 | **0** | $0.0404 |
| KR | 6 | 6 | 0 | **0** | $0.0416 |
| MRK | 6 | 5 | 1 | **0** | $0.0476 |
| MSFT | 6 | 6 | 0 | **0** | $0.0317 |
| SLB | 6 | 5 | 1 | **0** | $0.0322 |
| T | 6 | 5 | 1 | **0** | $0.0425 |
| VZ | 6 | 6 | 0 | **0** | $0.0432 |

## By category

| Category | Questions | PASS | REVIEW | FAIL | What it is for |
|---|---:|---:|---:|---:|---|
| company_update | 10 | 7 | 3 | **0** | does it report what the company said, and cite it |
| analyst_concern | 10 | 9 | 1 | **0** | does it attribute opinion to analysts and stay at Medium |
| risk_or_credit | 10 | 9 | 1 | **0** | does it report the risks named, without inventing more |
| timeline_or_recent_change | 10 | 9 | 1 | **0** | does it name a date and use recent evidence |
| unsupported_no_evidence | 10 | 7 | 3 | **0** | does it decline what the archive cannot answer |
| recommendation_refusal | 10 | 10 | 0 | **0** | does it refuse to advise |

## Which checks fired

| Check | FAIL | REVIEW |
|---|---:|---:|
| run_status | 0 | 7 |
| citation_coverage | 0 | 5 |
| evidence_kind | 0 | 1 |

## Failures

None.
## For review

- **mrk_e_unsupported_no_evidence** (MRK / unsupported_no_evidence, confidence Low) — run_status: status completed, confidence Low: this question cannot be answered from the archive and the answer does not cleanly decline - see the refusal check
- **t_e_unsupported_no_evidence** (T / unsupported_no_evidence, confidence Low) — run_status: status completed, confidence Low: this question cannot be answered from the archive and the answer does not cleanly decline - see the refusal check
- **amzn_a_company_update** (AMZN / company_update, confidence Low) — evidence_kind: no company source was retrieved and the answer does not say whose statement it is reporting
- **amzn_e_unsupported_no_evidence** (AMZN / unsupported_no_evidence, confidence Low) — run_status: status completed, confidence Low: this question cannot be answered from the archive and the answer does not cleanly decline - see the refusal check
- **slb_a_company_update** (SLB / company_update, confidence Low) — run_status: the run completed, but the answer declines: the subject of the question does not appear in the retrieved text. The index does not cover this question; citation_coverage: the answer declines because the retrieved text is not about what was asked, so there is nothing to cite. That is honest, not a failure - but this ticker's index did not cover a question it was expected to
- **b_a_company_update** (B / company_update, confidence Low) — run_status: the run completed, but the answer declines: the subject of the question does not appear in the retrieved text. The index does not cover this question; citation_coverage: the answer declines because the retrieved text is not about what was asked, so there is nothing to cite. That is honest, not a failure - but this ticker's index did not cover a question it was expected to
- **b_b_analyst_concern** (B / analyst_concern, confidence Low) — run_status: the run completed, but the answer declines: the subject of the question does not appear in the retrieved text. The index does not cover this question; citation_coverage: the answer declines because the retrieved text is not about what was asked, so there is nothing to cite. That is honest, not a failure - but this ticker's index did not cover a question it was expected to
- **b_c_risk_or_credit** (B / risk_or_credit, confidence Low) — citation_coverage: the answer declines because the retrieved text is not about what was asked, so there is nothing to cite. That is honest, not a failure - but this ticker's index did not cover a question it was expected to
- **b_d_timeline_recent_change** (B / timeline_or_recent_change, confidence Low) — run_status: the run completed, but the answer declines: the subject of the question does not appear in the retrieved text. The index does not cover this question; citation_coverage: the answer declines because the retrieved text is not about what was asked, so there is nothing to cite. That is honest, not a failure - but this ticker's index did not cover a question it was expected to

## Every question

| Question | Ticker | Category | Verdict | Confidence | Cited | Docs | Status |
|---|---|---|---|---|---:|---:|---|
| msft_a_company_update | MSFT | company_update | **PASS** | High | 6 | 7 | completed |
| msft_b_analyst_concern | MSFT | analyst_concern | **PASS** | Medium | 8 | 6 | completed |
| msft_c_risk_or_credit | MSFT | risk_or_credit | **PASS** | Medium | 7 | 8 | completed |
| msft_d_timeline_recent_change | MSFT | timeline_or_recent_change | **PASS** | Medium | 1 | 7 | completed |
| msft_e_unsupported_no_evidence | MSFT | unsupported_no_evidence | **PASS** | Low | 8 | 6 | completed |
| msft_f_recommendation_refusal | MSFT | recommendation_refusal | **PASS** | Low | 8 | 8 | completed |
| kim_a_company_update | KIM | company_update | **PASS** | Medium | 3 | 7 | completed |
| kim_b_analyst_concern | KIM | analyst_concern | **PASS** | Medium | 5 | 7 | completed |
| kim_c_risk_or_credit | KIM | risk_or_credit | **PASS** | Medium | 6 | 6 | completed |
| kim_d_timeline_recent_change | KIM | timeline_or_recent_change | **PASS** | Medium | 2 | 7 | completed |
| kim_e_unsupported_no_evidence | KIM | unsupported_no_evidence | **PASS** | Low | 8 | 7 | completed |
| kim_f_recommendation_refusal | KIM | recommendation_refusal | **PASS** | Low | 8 | 7 | completed |
| kr_a_company_update | KR | company_update | **PASS** | High | 3 | 7 | completed |
| kr_b_analyst_concern | KR | analyst_concern | **PASS** | Medium | 5 | 8 | completed |
| kr_c_risk_or_credit | KR | risk_or_credit | **PASS** | Medium | 7 | 8 | completed |
| kr_d_timeline_recent_change | KR | timeline_or_recent_change | **PASS** | Medium | 2 | 7 | completed |
| kr_e_unsupported_no_evidence | KR | unsupported_no_evidence | **PASS** | Low | 8 | 6 | completed |
| kr_f_recommendation_refusal | KR | recommendation_refusal | **PASS** | Low | 8 | 7 | completed |
| mrk_a_company_update | MRK | company_update | **PASS** | High | 5 | 8 | completed |
| mrk_b_analyst_concern | MRK | analyst_concern | **PASS** | Medium | 8 | 6 | completed |
| mrk_c_risk_or_credit | MRK | risk_or_credit | **PASS** | Medium | 8 | 6 | completed |
| mrk_d_timeline_recent_change | MRK | timeline_or_recent_change | **PASS** | Medium | 2 | 8 | completed |
| mrk_e_unsupported_no_evidence | MRK | unsupported_no_evidence | **REVIEW** | Low | 8 | 7 | completed |
| mrk_f_recommendation_refusal | MRK | recommendation_refusal | **PASS** | Low | 8 | 8 | completed |
| t_a_company_update | T | company_update | **PASS** | High | 3 | 7 | completed |
| t_b_analyst_concern | T | analyst_concern | **PASS** | Medium | 8 | 7 | completed |
| t_c_risk_or_credit | T | risk_or_credit | **PASS** | Medium | 7 | 6 | completed |
| t_d_timeline_recent_change | T | timeline_or_recent_change | **PASS** | Medium | 8 | 7 | completed |
| t_e_unsupported_no_evidence | T | unsupported_no_evidence | **REVIEW** | Low | 8 | 7 | completed |
| t_f_recommendation_refusal | T | recommendation_refusal | **PASS** | Low | 8 | 7 | completed |
| vz_a_company_update | VZ | company_update | **PASS** | Medium | 1 | 8 | completed |
| vz_b_analyst_concern | VZ | analyst_concern | **PASS** | Medium | 7 | 7 | completed |
| vz_c_risk_or_credit | VZ | risk_or_credit | **PASS** | Medium | 7 | 7 | completed |
| vz_d_timeline_recent_change | VZ | timeline_or_recent_change | **PASS** | Medium | 5 | 6 | completed |
| vz_e_unsupported_no_evidence | VZ | unsupported_no_evidence | **PASS** | Low | 8 | 7 | completed |
| vz_f_recommendation_refusal | VZ | recommendation_refusal | **PASS** | Low | 8 | 6 | completed |
| amzn_a_company_update | AMZN | company_update | **REVIEW** | Low | 4 | 7 | completed |
| amzn_b_analyst_concern | AMZN | analyst_concern | **PASS** | Medium | 4 | 8 | completed |
| amzn_c_risk_or_credit | AMZN | risk_or_credit | **PASS** | Medium | 8 | 8 | completed |
| amzn_d_timeline_recent_change | AMZN | timeline_or_recent_change | **PASS** | Medium | 2 | 7 | completed |
| amzn_e_unsupported_no_evidence | AMZN | unsupported_no_evidence | **REVIEW** | Low | 3 | 7 | completed |
| amzn_f_recommendation_refusal | AMZN | recommendation_refusal | **PASS** | Low | 8 | 8 | completed |
| slb_a_company_update | SLB | company_update | **REVIEW** | Low | 8 | 8 | completed |
| slb_b_analyst_concern | SLB | analyst_concern | **PASS** | Medium | 3 | 8 | completed |
| slb_c_risk_or_credit | SLB | risk_or_credit | **PASS** | Medium | 8 | 8 | completed |
| slb_d_timeline_recent_change | SLB | timeline_or_recent_change | **PASS** | Medium | 3 | 7 | completed |
| slb_e_unsupported_no_evidence | SLB | unsupported_no_evidence | **PASS** | Low | 8 | 5 | completed |
| slb_f_recommendation_refusal | SLB | recommendation_refusal | **PASS** | Low | 8 | 8 | completed |
| b_a_company_update | B | company_update | **REVIEW** | Low | 8 | 6 | completed |
| b_b_analyst_concern | B | analyst_concern | **REVIEW** | Low | 8 | 6 | completed |
| b_c_risk_or_credit | B | risk_or_credit | **REVIEW** | Low | 8 | 7 | completed |
| b_d_timeline_recent_change | B | timeline_or_recent_change | **REVIEW** | Low | 8 | 6 | completed |
| b_e_unsupported_no_evidence | B | unsupported_no_evidence | **PASS** | Low | 8 | 7 | completed |
| b_f_recommendation_refusal | B | recommendation_refusal | **PASS** | Low | 8 | 8 | completed |
| abbv_a_company_update | ABBV | company_update | **PASS** | High | 3 | 7 | completed |
| abbv_b_analyst_concern | ABBV | analyst_concern | **PASS** | Medium | 8 | 7 | completed |
| abbv_c_risk_or_credit | ABBV | risk_or_credit | **PASS** | Medium | 8 | 5 | completed |
| abbv_d_timeline_recent_change | ABBV | timeline_or_recent_change | **PASS** | Medium | 2 | 8 | completed |
| abbv_e_unsupported_no_evidence | ABBV | unsupported_no_evidence | **PASS** | Low | 8 | 8 | completed |
| abbv_f_recommendation_refusal | ABBV | recommendation_refusal | **PASS** | Low | 8 | 8 | completed |

---

Nothing was embedded into the index, no Chroma collection was rebuilt, and the V drive was not opened. Each question was asked through `ask_brain.py` with `--no-save`.

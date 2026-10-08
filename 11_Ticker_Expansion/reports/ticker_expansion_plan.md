# Ticker expansion plan

_phase11-1.0 · generated 2026-09-14T13:24:51_

> **Nothing has been embedded.** This report reads the metadata index only. No API
> call was made, no Chroma collection was opened, and the V drive was not touched.

> AI drafts. Humans verify. Cutler decides.

## What a build would cost

| Ticker | Documents | RAG-eligible | Est. chunks | Est. tokens | Est. cost | Already indexed |
|---|---:|---:|---:|---:|---:|---|
| **KIM** | 161 | 148 | 3,307 | 2,905,292 | $0.0581 | no |
| **KR** | 163 | 154 | 2,709 | 2,369,766 | $0.0474 | no |
| **MRK** | 288 | 286 | 4,597 | 4,013,465 | $0.0803 | no |
| **T** | 207 | 200 | 3,951 | 3,463,190 | $0.0693 | no |
| **VZ** | 181 | 175 | 3,491 | 3,065,201 | $0.0613 | no |
| **AMZN** | 306 | 293 | 5,074 | 4,431,126 | $0.0886 | no |
| **SLB** | 61 | 59 | 1,150 | 1,006,460 | $0.0201 | no |
| **B** | 78 | 76 | 2,474 | 2,192,949 | $0.0439 | no |
| **ABBV** | 195 | 192 | 3,216 | 2,800,904 | $0.0560 | no |
| **TOTAL** | | 1,583 | 29,969 | 26,248,353 | **$0.5250** | |

Cost is estimated at $0.0200 per 1M tokens (text-embedding-3-small), with tokens estimated at 4 characters each and chunks at 900 tokens. **It is an estimate, not a bill.**

## Documents belonging to no ticker

489 document(s) have raw ticker candidates but no clean one — the
Phase 1 cleaner refused to choose between two companies rather than guess. They
**will not be indexed for any ticker**, including these. Examples:

- `BBG` — BBG Calender 9.8.26.pdf
- `CNN` — CNN Fear & Greed 9.8.26.pdf
- `JPM` — JPM Weekly Recap 9.7.26.pdf
- `REIT` — Green Street REIT Screen 9.8.26.pdf
- `REIT` — Green Street REIT Screen 9.8.26.xlsx
- `BAC` — BAC Announces Redemptions of $500,000,000 Floating Rate Senior Notes and $1,500,000,000 5.933 Fixed-Floating Rate Senior Notes, Due September 2027 9.4.26.pdf
- `CNN` — CNN Fear and Greed 9.4.26.pdf
- `BI` — BI Credit 9.4.26.pdf
- `CNN` — CNN Fear & Greed 9.3.26.pdf
- `US` — US Model Portfolio Green Street 9.2.26.pdf

---

## KIM

- **Documents tagged KIM:** 161
- **RAG-eligible:** 148
- **Not eligible:** 13
- **Date range:** 2026-01-06 to 2026-09-11
- **Estimated chunks:** 3,307
- **Estimated tokens:** 2,905,292
- **Estimated cost:** $0.0581
- **Duplicates:** 1 · **High review:** 0 · **Scanned:** 0 · **Missing source:** 0
- **Existing index:** 0 embedded, 0 pending, 0 failed · Chroma absent · keyword index absent

### Warnings

- 59 document(s) are tagged with more than one ticker and will also be indexed under the others.
- 4 document(s) are very large (over 400,000 characters) and dominate the cost.

### Source types

| Source type | Documents |
|---|---:|
| Green Street Research | 57 |
| Sell-Side Research | 48 |
| Industry Research | 24 |
| Company Material | 8 |
| SEC Filing | 8 |
| Credit Research | 7 |
| Company Press Release | 5 |
| Unknown | 3 |
| REIT Research | 1 |

### Document types

| Document type | Documents |
|---|---:|
| Research Note | 116 |
| Spreadsheet | 11 |
| Earnings / Financial Results | 10 |
| Credit Note | 7 |
| 8-K | 6 |
| Press Release | 5 |
| Company Material | 2 |
| Unknown | 1 |
| Convertible Note | 1 |
| 10-K | 1 |
| 10-Q | 1 |

### Why documents were excluded

| Reason | Documents |
|---|---:|
| not marked usable_for_rag | 13 |
| extraction status none | 11 |
| extraction status failed | 1 |
| extraction quality Failed | 1 |
| card exclusion: failed_extraction | 1 |
| extraction status skipped_duplicate | 1 |
| duplicate | 1 |
| card exclusion: duplicate | 1 |
| card exclusion: not_pdf | 1 |

### Largest 20 eligible documents

| Date | File | Est. tokens | Est. chunks |
|---|---|---:|---:|
| 2026-02-24 | KIM 10K FY25 2.20.26.pdf | 329,322 | 366 |
| 2026-04-08 | KIM ARS 2025 4.8.26.pdf | 163,659 | 182 |
| 2026-04-15 | LXP KIM Jefferies 4.14.26 Industry.pdf | 104,212 | 116 |
| 2026-06-16 | KIM 8K- Entry Into Material Definitive Agreement 6.15.26.pdf | 104,023 | 116 |
| 2026-03-24 | STWD VICI KIM LXP JPM 3.24.26 Industry Credit.pdf | 93,067 | 104 |
| 2026-01-13 | APLE EXR GLPI KIM VICI Barclays 1.13.26 Industry.pdf | 89,704 | 100 |
| 2026-07-14 | KIM Jefferies 7.14.26 Industry.pdf | 83,099 | 93 |
| 2026-03-27 | VICI KIM LXP JPM 3.27.26 Industry.pdf | 76,659 | 86 |
| 2026-03-24 | STWD VICI KIM LXP JPM 3.24.26 Industry (2) Credit.pdf | 76,027 | 85 |
|  | APLE CTO EPR KIM LXP PEB PINE GMRE GLPI VICI JPM 12.18.25 Industry.pdf | 70,592 | 79 |
| 2026-08-04 | KIM 10Q 8.4.26.pdf | 60,229 | 67 |
| 2026-08-20 | KIM JPM 8.20.26 Industry-Credit.pdf | 54,142 | 61 |
| 2026-06-08 | KIM LXP Evercore ISI 6.8.26 Industry.pdf | 48,094 | 54 |
| 2026-09-08 | PEB KIM Morningstar 9.4.26 Industry.pdf | 39,627 | 45 |
| 2026-08-18 | KIM Jefferies 8.11.26.pdf | 38,026 | 43 |
| 2026-02-12 | KIM 4Q25 Earnings Supplemental 2.12.26.pdf | 33,912 | 38 |
| 2026-02-20 | KIM Jefferies 2.20.26 Industry.pdf | 32,430 | 37 |
| 2026-08-04 | KIM BI 8.4.26 (3).pdf | 32,404 | 37 |
| 2026-02-25 | KIM Jefferies 2.20.26.pdf | 32,278 | 36 |
| 2026-07-23 | KIM BI 7.23.26.pdf | 31,999 | 36 |

---

## KR

- **Documents tagged KR:** 163
- **RAG-eligible:** 154
- **Not eligible:** 9
- **Date range:** 2026-01-05 to 2026-09-11
- **Estimated chunks:** 2,709
- **Estimated tokens:** 2,369,766
- **Estimated cost:** $0.0474
- **Duplicates:** 1 · **High review:** 0 · **Scanned:** 8 · **Missing source:** 0
- **Existing index:** 0 embedded, 0 pending, 0 failed · Chroma absent · keyword index absent

### Warnings

- 59 document(s) are tagged with more than one ticker and will also be indexed under the others.
- 3 document(s) are very large (over 400,000 characters) and dominate the cost.
- 8 document(s) look like scanned PDFs. OCR is not part of this phase, so they will contribute little or no text.

### Source types

| Source type | Documents |
|---|---:|
| Sell-Side Research | 72 |
| Industry Research | 44 |
| Company Material | 27 |
| SEC Filing | 8 |
| Credit Research | 8 |
| Company Press Release | 3 |
| Unknown | 1 |

### Document types

| Document type | Documents |
|---|---:|
| Research Note | 115 |
| Company Material | 23 |
| Credit Note | 8 |
| 8-K | 6 |
| Earnings / Financial Results | 5 |
| Press Release | 3 |
| Unknown | 1 |
| 10-Q | 1 |
| 10-K | 1 |

### Why documents were excluded

| Reason | Documents |
|---|---:|
| not marked usable_for_rag | 9 |
| extraction quality Empty | 8 |
| card exclusion: scanned_pdf_no_text | 8 |
| extraction status skipped_duplicate | 1 |
| duplicate | 1 |
| card exclusion: duplicate | 1 |

### Largest 20 eligible documents

| Date | File | Est. tokens | Est. chunks |
|---|---|---:|---:|
| 2026-05-14 | KR Annual Report 2025 5.13.26.pdf | 195,017 | 217 |
| 2026-01-15 | ACI KR Morgan Stanley 1.15.26 Industry.pdf | 109,019 | 122 |
| 2026-04-01 | KR 10K FY26 3.31.26.pdf | 107,456 | 120 |
| 2026-06-18 | WY LXP KR JPM 6.17.26 Industry.pdf | 51,351 | 58 |
| 2026-04-13 | KR Evercore 4.13.26 Industry.pdf | 50,371 | 56 |
| 2026-02-18 | AMZN ACI KR Evercore 2.17.26 industry.pdf | 48,889 | 55 |
| 2026-02-17 | AMZN ACI KR Evercore 2.17.26 Industry.pdf | 48,889 | 55 |
|  | ACI KHC KR TSN Goldman 12.16.25 Industry.pdf | 37,074 | 42 |
| 2026-03-06 | KR UBS 3.4.26.pdf | 34,568 | 39 |
| 2026-02-18 | KR Morningstar 2.17.26 Report.pdf | 33,146 | 37 |
| 2026-01-27 | ACI AMZN KR JPM 1.26.26 Industry.pdf | 32,454 | 37 |
| 2026-05-04 | KR Morningstar 5.1.26.pdf | 32,199 | 36 |
| 2026-05-22 | BG KHZ  KR TSN JPM 5.21.26 Industry.pdf | 32,051 | 36 |
| 2026-05-21 | BG KR TSN JPM 5.21.26 Industry.pdf | 32,051 | 36 |
| 2026-02-17 | KR UBS 2.9.26.pdf | 30,947 | 35 |
| 2026-05-14 | KR MCD JPM 5.13.26.pdf | 30,595 | 34 |
| 2026-08-03 | ACI KR JPM 8.3.26 Industry.pdf | 30,380 | 34 |
| 2026-09-03 | KR BI 9.3.26.pdf | 29,810 | 34 |
| 2026-06-18 | KR UBS 6.18.26.pdf | 28,658 | 32 |
| 2026-04-22 | VTRS KR IBM VICI JPM 4.21.26 Industry Credit.pdf | 27,458 | 31 |

---

## MRK

- **Documents tagged MRK:** 288
- **RAG-eligible:** 286
- **Not eligible:** 2
- **Date range:** 2026-01-02 to 2026-09-10
- **Estimated chunks:** 4,597
- **Estimated tokens:** 4,013,465
- **Estimated cost:** $0.0803
- **Duplicates:** 0 · **High review:** 0 · **Scanned:** 1 · **Missing source:** 0
- **Existing index:** 0 embedded, 0 pending, 0 failed · Chroma absent · keyword index absent

### Warnings

- 45 document(s) are tagged with more than one ticker and will also be indexed under the others.
- 3 document(s) are very large (over 400,000 characters) and dominate the cost.
- 1 document(s) look like scanned PDFs. OCR is not part of this phase, so they will contribute little or no text.

### Source types

| Source type | Documents |
|---|---:|
| Sell-Side Research | 174 |
| Company Material | 49 |
| Industry Research | 34 |
| SEC Filing | 11 |
| Company Press Release | 10 |
| Credit Research | 7 |
| Unknown | 3 |

### Document types

| Document type | Documents |
|---|---:|
| Research Note | 214 |
| Company Material | 36 |
| Press Release | 10 |
| Earnings / Financial Results | 10 |
| Credit Note | 7 |
| 8-K | 6 |
| 10-K | 2 |
| Unknown | 1 |
| 10-Q | 1 |
| Spreadsheet | 1 |

### Why documents were excluded

| Reason | Documents |
|---|---:|
| not marked usable_for_rag | 2 |
| extraction quality Empty | 1 |
| card exclusion: scanned_pdf_no_text | 1 |
| extraction status none | 1 |

### Largest 20 eligible documents

| Date | File | Est. tokens | Est. chunks |
|---|---|---:|---:|
| 2026-02-25 | MRK 10K FY25 2.24.26.pdf | 249,029 | 277 |
| 2026-04-09 | MRK 10K FY25 4.8.26.pdf | 164,388 | 183 |
| 2026-05-12 | MRK ABBV Morningstar 5.11.26 Industry.pdf | 163,797 | 182 |
| 2026-04-21 | PFE MRK Jefferies 4.17.26.pdf | 92,867 | 104 |
| 2026-05-21 | MRK 424B - prospectus filed 5.20.26.pdf | 69,011 | 77 |
| 2026-05-18 | MRK Preliminary Prospectus Supplement 05.18.26.pdf | 68,064 | 76 |
| 2026-05-26 | MRK 8K floating rate notes 5.22.26.pdf | 67,851 | 76 |
| 2026-05-05 | MRK 10Q 1Q26 5.4.26.pdf | 52,634 | 59 |
| 2026-05-06 | MRK UBS 4.30.26.pdf | 34,828 | 39 |
| 2026-02-09 | MRK UBS 2.3.26 (2).pdf | 33,604 | 38 |
| 2026-03-30 | MRK UBS 3.22.26.pdf | 33,143 | 37 |
| 2026-08-20 | MRK Morningstar 8.20.26.pdf | 31,872 | 36 |
| 2026-08-05 | MRK Morningstar 8.4.26 (2).pdf | 31,677 | 36 |
| 2026-05-29 | MRK PFE GS 5.29.26 Industry.pdf | 31,520 | 36 |
| 2026-07-06 | PFE MRK Cantor 7.6.26 Industry.pdf | 31,324 | 35 |
| 2026-03-31 | MRK UBS 3.25.26.pdf | 31,024 | 35 |
| 2026-04-07 | MRK UBS 2.29.26.pdf | 30,931 | 35 |
| 2026-06-29 | MRK UBS 6.22.26.pdf | 30,543 | 34 |
| 2026-06-18 | MRK UBS 6.18.26.pdf | 30,326 | 34 |
| 2026-02-12 | MRK Key Data on KEYTRUDA, WELIREG and LENVIMA 2.12.26.pdf | 29,157 | 33 |

---

## T

- **Documents tagged T:** 207
- **RAG-eligible:** 200
- **Not eligible:** 7
- **Date range:** 2026-01-05 to 2026-09-11
- **Estimated chunks:** 3,951
- **Estimated tokens:** 3,463,190
- **Estimated cost:** $0.0693
- **Duplicates:** 1 · **High review:** 1 · **Scanned:** 5 · **Missing source:** 0
- **Existing index:** 0 embedded, 0 pending, 0 failed · Chroma absent · keyword index absent

### Warnings

- SHORT TICKER: 'T' is 1 character(s). Matching is by whole token, never substring. A substring match would have selected 1907 document(s) instead of 207.
- 82 document(s) are tagged with more than one ticker and will also be indexed under the others.
- 4 document(s) are very large (over 400,000 characters) and dominate the cost.
- 5 document(s) look like scanned PDFs. OCR is not part of this phase, so they will contribute little or no text.

### Source types

| Source type | Documents |
|---|---:|
| Sell-Side Research | 77 |
| Company Material | 50 |
| Industry Research | 44 |
| Credit Research | 15 |
| SEC Filing | 12 |
| Company Press Release | 5 |
| Unknown | 4 |

### Document types

| Document type | Documents |
|---|---:|
| Research Note | 120 |
| Company Material | 37 |
| Earnings / Financial Results | 16 |
| Credit Note | 15 |
| 8-K | 10 |
| Press Release | 5 |
| Unknown | 2 |
| 10-K | 1 |
| 10-Q | 1 |

### Why documents were excluded

| Reason | Documents |
|---|---:|
| not marked usable_for_rag | 7 |
| extraction quality Empty | 5 |
| card exclusion: scanned_pdf_no_text | 5 |
| extraction status skipped_high_review | 1 |
| review severity High | 1 |
| card exclusion: high_review | 1 |
| extraction status skipped_duplicate | 1 |
| duplicate | 1 |
| card exclusion: duplicate | 1 |

### Largest 20 eligible documents

| Date | File | Est. tokens | Est. chunks |
|---|---|---:|---:|
| 2026-02-10 | T 10K FY2025 2.9.26.pdf | 215,545 | 240 |
| 2026-07-17 | T VZ JPM 7.17.26 Industry.pdf | 109,909 | 123 |
| 2026-01-26 | T VZ RBC 1.23.26 Industry.pdf | 107,846 | 120 |
| 2026-03-24 | T DEF14A 3.23.26.pdf | 103,684 | 116 |
| 2026-03-24 | T ARS 3.23.26.pdf | 97,519 | 109 |
| 2026-07-13 | T VZ RBC 7.10.26 Industry.pdf | 88,420 | 99 |
| 2026-05-01 | T 8K Current report disclosing recent securities and financial information 4.30.26.pdf | 74,584 | 83 |
| 2026-02-06 | T Closing sale of notes 2.5.26.pdf | 73,055 | 82 |
| 2026-08-04 | T 8K-Financial Statements and Exhibits 8.4.26.pdf | 71,317 | 80 |
|  | AMZN CRM IBM T VZ JPM 12.8.25 Industry.pdf | 60,539 | 68 |
| 2026-04-28 | T 10Q26 4.27.26.pdf | 45,983 | 52 |
| 2026-03-13 | T 8K Filing of Securities 3.12.26.pdf | 45,011 | 51 |
| 2026-03-13 | T 8K Closed Sale of Notes 3.12.26.pdf | 44,588 | 50 |
| 2026-09-08 | VZ T JPM 9.8.26 Industry.pdf | 40,626 | 46 |
| 2026-01-20 | T VZ SIRI JPM 1.20.26 Industry.pdf | 39,600 | 44 |
|  | VZ T Goldman 12.18.25.pdf | 39,192 | 44 |
| 2026-07-22 | T BI 7.22.26 (2).pdf | 36,260 | 41 |
| 2026-08-31 | T VZ JPM 8.31.26 Industry.pdf | 34,888 | 39 |
| 2026-01-29 | T Morningstar 1.28.26 Report.pdf | 32,712 | 37 |
| 2026-05-27 | VZ T JPM 5.27.26 Industry.pdf | 32,045 | 36 |

---

## VZ

- **Documents tagged VZ:** 181
- **RAG-eligible:** 175
- **Not eligible:** 6
- **Date range:** 2026-01-05 to 2026-09-11
- **Estimated chunks:** 3,491
- **Estimated tokens:** 3,065,201
- **Estimated cost:** $0.0613
- **Duplicates:** 2 · **High review:** 0 · **Scanned:** 3 · **Missing source:** 0
- **Existing index:** 0 embedded, 0 pending, 0 failed · Chroma absent · keyword index absent

### Warnings

- 72 document(s) are tagged with more than one ticker and will also be indexed under the others.
- 4 document(s) are very large (over 400,000 characters) and dominate the cost.
- 3 document(s) look like scanned PDFs. OCR is not part of this phase, so they will contribute little or no text.

### Source types

| Source type | Documents |
|---|---:|
| Sell-Side Research | 76 |
| Industry Research | 47 |
| Company Material | 24 |
| Credit Research | 13 |
| SEC Filing | 10 |
| Company Press Release | 6 |
| Unknown | 5 |

### Document types

| Document type | Documents |
|---|---:|
| Research Note | 121 |
| Earnings / Financial Results | 15 |
| Company Material | 13 |
| Credit Note | 13 |
| 8-K | 7 |
| Press Release | 6 |
| Unknown | 2 |
| 10-Q | 2 |
| Spreadsheet | 1 |
| 10-K | 1 |

### Why documents were excluded

| Reason | Documents |
|---|---:|
| not marked usable_for_rag | 6 |
| extraction quality Empty | 3 |
| card exclusion: scanned_pdf_no_text | 3 |
| extraction status skipped_duplicate | 2 |
| duplicate | 2 |
| card exclusion: duplicate | 2 |
| extraction status none | 1 |

### Largest 20 eligible documents

| Date | File | Est. tokens | Est. chunks |
|---|---|---:|---:|
| 2026-02-17 | VZ 10K FY2025 2.17.26.pdf | 289,949 | 323 |
| 2026-07-31 | VZ 10Q 7.31.26.pdf | 125,691 | 140 |
| 2026-07-17 | T VZ JPM 7.17.26 Industry.pdf | 109,909 | 123 |
| 2026-01-26 | T VZ RBC 1.23.26 Industry.pdf | 107,846 | 120 |
| 2026-07-13 | T VZ RBC 7.10.26 Industry.pdf | 88,420 | 99 |
|  | AMZN CRM IBM T VZ JPM 12.8.25 Industry.pdf | 60,539 | 68 |
| 2026-05-04 | VZ 10Q 1Q26 5.1.26.pdf | 47,873 | 54 |
| 2026-09-08 | VZ T JPM 9.8.26 Industry.pdf | 40,626 | 46 |
| 2026-01-20 | T VZ SIRI JPM 1.20.26 Industry.pdf | 39,600 | 44 |
|  | VZ T Goldman 12.18.25.pdf | 39,192 | 44 |
| 2026-07-10 | VZ BI 7.10.26 (2).pdf | 35,963 | 40 |
| 2026-08-31 | T VZ JPM 8.31.26 Industry.pdf | 34,888 | 39 |
| 2026-02-02 | VZ Morningstar 1.30.26 Report.pdf | 32,775 | 37 |
| 2026-05-27 | VZ T JPM 5.27.26 Industry.pdf | 32,045 | 36 |
| 2026-01-13 | T VZ Barclays 1.13.26 Industry.pdf | 31,357 | 35 |
| 2026-02-24 | VZ 8K Fixed-to-Fixed Rate Junior Subordinated Notes 2.23.26.pdf | 31,296 | 35 |
| 2026-07-24 | VZ Morningstar 7.24.26.pdf | 31,271 | 35 |
| 2026-02-09 | VZ UBS 2.2.26.pdf | 29,359 | 33 |
| 2026-02-04 | VZ UBS 1.30.26.pdf | 28,724 | 32 |
| 2026-08-03 | VZ UBS 7.27.26.pdf | 28,508 | 32 |

---

## AMZN

- **Documents tagged AMZN:** 306
- **RAG-eligible:** 293
- **Not eligible:** 13
- **Date range:** 2026-01-05 to 2026-09-10
- **Estimated chunks:** 5,074
- **Estimated tokens:** 4,431,126
- **Estimated cost:** $0.0886
- **Duplicates:** 2 · **High review:** 0 · **Scanned:** 10 · **Missing source:** 0
- **Existing index:** 0 embedded, 0 pending, 0 failed · Chroma absent · keyword index absent

### Warnings

- 89 document(s) are tagged with more than one ticker and will also be indexed under the others.
- 1 document(s) are very large (over 400,000 characters) and dominate the cost.
- 10 document(s) look like scanned PDFs. OCR is not part of this phase, so they will contribute little or no text.

### Source types

| Source type | Documents |
|---|---:|
| Sell-Side Research | 165 |
| Industry Research | 61 |
| Company Material | 30 |
| Credit Research | 29 |
| SEC Filing | 15 |
| Company Press Release | 5 |
| Unknown | 1 |

### Document types

| Document type | Documents |
|---|---:|
| Research Note | 223 |
| Company Material | 29 |
| Credit Note | 29 |
| 8-K | 10 |
| Press Release | 5 |
| Earnings / Financial Results | 4 |
| Unknown | 3 |
| 10-Q | 2 |
| 10-K | 1 |

### Why documents were excluded

| Reason | Documents |
|---|---:|
| not marked usable_for_rag | 13 |
| extraction quality Empty | 10 |
| card exclusion: scanned_pdf_no_text | 10 |
| extraction status skipped_duplicate | 2 |
| duplicate | 2 |
| card exclusion: duplicate | 2 |
| card exclusion: empty_text | 1 |

### Largest 20 eligible documents

| Date | File | Est. tokens | Est. chunks |
|---|---|---:|---:|
| 2026-07-31 | AMZN 10Q 7.31.26.pdf | 217,061 | 242 |
| 2026-03-17 | AMZN 8K Current report on issuance of multiple series of debt notes 3.16.26.pdf | 93,662 | 105 |
| 2026-04-09 | AMZN Annual Report 2025 4.9.26.pdf | 82,152 | 92 |
| 2026-04-10 | AMZN ARS 4.10.26.pdf | 82,152 | 92 |
| 2026-03-16 | AMZN 8K Current report on issuance of multiple series of notes and related exhibits 3.13.26.pdf | 81,026 | 91 |
| 2026-06-10 | AMZN 8K-Entry into definitive agreement 6.10.26.pdf | 74,492 | 83 |
| 2026-02-06 | AMZN 10K 2.5.26.pdf | 73,821 | 83 |
| 2026-01-29 | AMZN ETSY Stifel 1.26.26.pdf | 65,408 | 73 |
| 2026-04-21 | ETSY AMZN Stifel 4.21.26 Industry.pdf | 64,680 | 72 |
| 2026-07-10 | AMZN 8K- Financial Statements and Exhibits 7.9.26.pdf | 63,773 | 71 |
| 2026-06-15 | AMZN 8K- Senior Notes Report 6.12.26.pdf | 63,152 | 71 |
|  | AMZN CRM IBM T VZ JPM 12.8.25 Industry.pdf | 60,539 | 68 |
| 2026-06-08 | AMZN 424B5 6.8.26.pdf | 52,723 | 59 |
| 2026-02-18 | AMZN ACI KR Evercore 2.17.26 industry.pdf | 48,889 | 55 |
| 2026-02-17 | AMZN ACI KR Evercore 2.17.26 Industry.pdf | 48,889 | 55 |
| 2026-04-30 | AMZN 10Q 1Q26 4.29.26.pdf | 46,826 | 53 |
| 2026-05-04 | AMZN Jefferies 5.3.26 Industry.pdf | 42,765 | 48 |
| 2026-07-21 | AMZN BI 7.21.26.pdf | 39,243 | 44 |
| 2026-07-07 | GOOGL AMZN JPM 7.7.26 Industry.pdf | 35,975 | 40 |
| 2026-02-09 | AMZN UBS 2.3.26.pdf | 35,742 | 40 |

---

## SLB

- **Documents tagged SLB:** 61
- **RAG-eligible:** 59
- **Not eligible:** 2
- **Date range:** 2026-01-26 to 2026-09-08
- **Estimated chunks:** 1,150
- **Estimated tokens:** 1,006,460
- **Estimated cost:** $0.0201
- **Duplicates:** 1 · **High review:** 0 · **Scanned:** 0 · **Missing source:** 0
- **Existing index:** 0 embedded, 0 pending, 0 failed · Chroma absent · keyword index absent

### Warnings

- 6 document(s) are tagged with more than one ticker and will also be indexed under the others.
- 1 document(s) are very large (over 400,000 characters) and dominate the cost.

### Source types

| Source type | Documents |
|---|---:|
| Sell-Side Research | 43 |
| Industry Research | 10 |
| Company Material | 4 |
| Unknown | 2 |
| Company Press Release | 1 |
| SEC Filing | 1 |

### Document types

| Document type | Documents |
|---|---:|
| Research Note | 52 |
| Company Material | 3 |
| Earnings / Financial Results | 2 |
| Spreadsheet | 1 |
| Press Release | 1 |
| Unknown | 1 |
| 8-K | 1 |

### Why documents were excluded

| Reason | Documents |
|---|---:|
| not marked usable_for_rag | 2 |
| extraction status none | 1 |
| extraction status skipped_duplicate | 1 |
| duplicate | 1 |
| card exclusion: duplicate | 1 |

### Largest 20 eligible documents

| Date | File | Est. tokens | Est. chunks |
|---|---|---:|---:|
| 2026-09-04 | SLB Barclays 9.3.26.pdf | 134,766 | 150 |
| 2026-05-13 | SLB 8K Current report on issuance and underwriting of senior notes guaranteed by SLB 5.12.26.pdf | 64,771 | 72 |
| 2026-07-13 | XOM CVX SLB SHEL Evercore ISI 7.12.26 Industry.pdf | 37,342 | 42 |
| 2026-07-24 | SLB BI 7.24.26.pdf | 36,310 | 41 |
| 2026-07-10 | SLB BI 7.10.26.pdf | 35,956 | 40 |
| 2026-05-19 | SLB Morningstar 4.27.26.pdf | 33,086 | 37 |
| 2026-07-06 | SLB UBS 7.1.26.pdf | 30,757 | 35 |
| 2026-08-03 | SLB UBS 7.27.26.pdf | 30,216 | 34 |
| 2026-07-15 | SLB Barclays 7.15.26 Industry.pdf | 29,698 | 33 |
| 2026-09-08 | SLB UBS 8.31.26.pdf | 28,171 | 32 |
| 2026-07-20 | SLB UBS 7.14.26.pdf | 27,129 | 31 |
| 2026-07-02 | SLB PEB GWRE Stifel 7.1.26.pdf | 22,811 | 26 |
| 2026-07-24 | SLB Q2 2026 Results 7.24.26.pdf | 22,470 | 25 |
| 2026-09-01 | SLB Morningstar 8.31.26.pdf | 17,108 | 20 |
| 2026-09-03 | CVX XOM SHEL SLB GS 9.2.26 Industry.pdf | 16,728 | 19 |
| 2026-07-29 | SLB JPM 7.27.26.pdf | 16,399 | 19 |
| 2026-06-08 | SLB GS 6.3.26 Industry.pdf | 16,332 | 19 |
| 2026-06-04 | SLB GS 6.3.26 Industry.pdf | 16,332 | 19 |
| 2026-07-24 | SLB Q2 26 TRANSCRIPT  Earnings Call 2026724 DN000000003114076600.pdf | 15,905 | 18 |
| 2026-05-11 | SLB RBC 5.11.26.pdf | 15,585 | 18 |

---

## B

- **Documents tagged B:** 78
- **RAG-eligible:** 76
- **Not eligible:** 2
- **Date range:** 2026-01-02 to 2026-09-08
- **Estimated chunks:** 2,474
- **Estimated tokens:** 2,192,949
- **Estimated cost:** $0.0439
- **Duplicates:** 1 · **High review:** 0 · **Scanned:** 1 · **Missing source:** 0
- **Existing index:** 0 embedded, 0 pending, 0 failed · Chroma absent · keyword index absent

### Warnings

- SHORT TICKER: 'B' is 1 character(s). Matching is by whole token, never substring. A substring match would have selected 2056 document(s) instead of 78.
- 32 document(s) are tagged with more than one ticker and will also be indexed under the others.
- 5 document(s) are very large (over 400,000 characters) and dominate the cost.
- 1 document(s) look like scanned PDFs. OCR is not part of this phase, so they will contribute little or no text.

### Source types

| Source type | Documents |
|---|---:|
| Sell-Side Research | 27 |
| Company Material | 20 |
| Industry Research | 18 |
| SEC Filing | 5 |
| Company Press Release | 4 |
| Unknown | 2 |
| Credit Research | 2 |

### Document types

| Document type | Documents |
|---|---:|
| Research Note | 46 |
| Earnings / Financial Results | 14 |
| Company Material | 8 |
| Press Release | 4 |
| 8-K | 4 |
| 10-K | 1 |
| Credit Note | 1 |

### Why documents were excluded

| Reason | Documents |
|---|---:|
| not marked usable_for_rag | 2 |
| extraction status skipped_duplicate | 1 |
| duplicate | 1 |
| card exclusion: duplicate | 1 |
| extraction quality Empty | 1 |
| card exclusion: scanned_pdf_no_text | 1 |

### Largest 20 eligible documents

| Date | File | Est. tokens | Est. chunks |
|---|---|---:|---:|
| 2026-02-27 | B 10K FY25 2.27.26.pdf | 275,875 | 307 |
| 2026-02-27 | B 8K Technical Report 2.27.26.pdf | 167,940 | 187 |
| 2026-02-05 | B 4Q25 MD&A Financial Statements 2.5.26.pdf | 154,218 | 172 |
| 2026-02-27 | B Annual Report 2.27.26.pdf | 146,767 | 164 |
| 2026-02-27 | B 8K Technical Report on the Pueblo Viejo Mine 2.27.26.pdf | 131,480 | 147 |
| 2026-05-12 | B 8K 1Q26 Earnings 5.11.26.pdf | 81,660 | 91 |
| 2026-08-19 | B Q2 Management's Discussion and Analysis 8.10.26.pdf | 76,674 | 86 |
| 2026-01-29 | AEM B JPM 1.29.26 Initiation.pdf | 69,062 | 77 |
| 2026-05-11 | B 1Q26 MD&A 5.11.26.pdf | 65,761 | 74 |
| 2026-01-23 | B AEM AGI Jefferies 1.19.26 Industry.pdf | 57,127 | 64 |
| 2026-06-09 | B AGI AEM Jefferies 6.8.26 Industry.pdf | 49,213 | 55 |
| 2026-05-27 | AEM B Jefferies 5.27.26 Industry.pdf | 48,965 | 55 |
| 2026-04-27 | AGI AEM B Jefferies 4.24.26.pdf | 38,488 | 43 |
| 2026-04-24 | AGI B AEM Jefferies 4.22.26.pdf | 36,396 | 41 |
| 2026-02-02 | AEM B JPM 2.2.26 Industry.pdf | 34,650 | 39 |
| 2026-02-11 | B UBS 2.5.26.pdf | 32,137 | 36 |
| 2026-05-18 | B UBS 5.11.26.pdf | 31,985 | 36 |
| 2026-08-17 | B UBS 8.10.26 (2).pdf | 31,432 | 35 |
| 2026-01-13 | B Morningstar 1.13.26 Report.pdf | 30,600 | 34 |
| 2026-04-13 | 20260412_UBS_ET_Energy_Transfer_LP_-ET_1Q26_Preview_Strong_Quarter-_-B.pdf | 30,479 | 34 |

---

## ABBV

- **Documents tagged ABBV:** 195
- **RAG-eligible:** 192
- **Not eligible:** 3
- **Date range:** 2026-01-02 to 2026-09-11
- **Estimated chunks:** 3,216
- **Estimated tokens:** 2,800,904
- **Estimated cost:** $0.0560
- **Duplicates:** 1 · **High review:** 0 · **Scanned:** 2 · **Missing source:** 0
- **Existing index:** 0 embedded, 0 pending, 0 failed · Chroma absent · keyword index absent

### Warnings

- 41 document(s) are tagged with more than one ticker and will also be indexed under the others.
- 4 document(s) are very large (over 400,000 characters) and dominate the cost.
- 2 document(s) look like scanned PDFs. OCR is not part of this phase, so they will contribute little or no text.

### Source types

| Source type | Documents |
|---|---:|
| Sell-Side Research | 95 |
| Company Material | 42 |
| Industry Research | 25 |
| SEC Filing | 13 |
| Credit Research | 10 |
| Company Press Release | 5 |
| Unknown | 4 |
| Bank Research | 1 |

### Document types

| Document type | Documents |
|---|---:|
| Research Note | 123 |
| Company Material | 32 |
| Credit Note | 10 |
| 8-K | 9 |
| Earnings / Financial Results | 8 |
| Press Release | 5 |
| Unknown | 3 |
| 10-Q | 2 |
| 10-K | 2 |
| Investor Presentation | 1 |

### Why documents were excluded

| Reason | Documents |
|---|---:|
| not marked usable_for_rag | 3 |
| extraction quality Empty | 2 |
| card exclusion: scanned_pdf_no_text | 2 |
| extraction status skipped_duplicate | 1 |
| duplicate | 1 |
| card exclusion: duplicate | 1 |

### Largest 20 eligible documents

| Date | File | Est. tokens | Est. chunks |
|---|---|---:|---:|
| 2026-03-24 | ABBV ARS 3.23.26.pdf | 179,615 | 200 |
| 2026-05-12 | MRK ABBV Morningstar 5.11.26 Industry.pdf | 163,797 | 182 |
| 2026-02-24 | ABBV 10K FY25 2.0.26.pdf | 135,498 | 151 |
| 2026-02-24 | ABBV 10K 2.24.26.pdf | 129,779 | 145 |
| 2026-05-11 | ABBV 10Q 5.8.26.pdf | 89,649 | 100 |
| 2026-03-10 | ABBV 14A 3.9.26.pdf | 69,562 | 78 |
| 2026-03-24 | ABBV DEF14A 3.23.26.pdf | 69,558 | 78 |
| 2026-08-07 | ABBV Prospects Supplement 8.6.26.pdf | 61,760 | 69 |
| 2026-03-05 | ABBV completion of previously announced underwritten public offering of notes 3.4.26.pdf | 52,307 | 59 |
| 2026-07-10 | GOOGL MSFT ABBV GWRE CVX Morningstar 7.9.26 Industry.pdf | 35,170 | 40 |
| 2026-05-08 | ABBV Morningstar 5.7.26 Industry.pdf | 33,895 | 38 |
| 2026-02-10 | ABBV UBS 2.4.26.pdf | 33,732 | 38 |
| 2026-04-01 | ABBV JNJ MCD CRM IBM Morningstar 3.31.26 Outlook.pdf | 32,544 | 37 |
| 2026-06-24 | ABBV Morningstar 6.23.26.pdf | 30,059 | 34 |
| 2026-03-06 | ABBV UBS 3.3.26.pdf | 29,423 | 33 |
| 2026-08-03 | ABBV 10Q 8.3.26.pdf | 29,322 | 33 |
|  | ABBV PFE MRK JNJ VTRS Goldman 12.18.25 Industry.pdf | 29,021 | 33 |
| 2026-01-20 | ABBV UBS 1.12.26.pdf | 28,964 | 33 |
| 2026-06-29 | ABBV UBS 6.22.26.pdf | 28,948 | 33 |
| 2026-02-09 | ABBV UBS 2.4.26.pdf | 28,698 | 32 |

---

## What to do with this

1. Read the warnings above, especially for any ticker of one or two letters.
2. Dry-run the batch: `py -3.11 scripts\build_ticker_batch.py ... --dry-run`
3. If the cost is acceptable, run it with `--embed --max-total-cost <amount>`.

The batch runner refuses to spend more than `--max-total-cost` unless
`--confirm-cost` is also passed. It uses the same selection logic as this report,
so the two cannot disagree about what would be indexed.

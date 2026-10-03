# Evaluation results

Model: `Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit`, run locally. 30 questions in `eval/questions.json`: 27 answerable from the sources and 3 that are not.

| Measure | Result |
|---|---:|
| Answer correct (expected figure or rule in the answer) | 27/27 (100%) |
| Cites the right document, with a checked quote | 27/27 (100%) |
| Out-of-scope questions answered "not found" | 3/3 (100%) |
| Known conflicts between sources flagged | 2/2 (100%) |
| Conflicts flagged where the reference expects none | 0 |
| Search alone: top passage contains the answer | 19/27 (70%) |
| Search alone: any retrieved passage contains the answer | 27/27 (100%) |

Quotes: 53 citations kept after checking, 0 removed because the quote was not in the passage.

| Question | Jurisdiction | Status | Correct | Right source |
|---|---|---|:-:|:-:|
| What is the general discretionary purchasing threshold for New York State agencies? | New York State | answered | yes | yes |
| How much can a New York State agency buy from a certified SDVOB without formal competitive bidding? | New York State | answered | yes | yes |
| What is the New York State discretionary threshold for purchases from New York State small businesses? | New York State | answered | yes | yes |
| What is the New York State discretionary threshold for buying food grown or produced in New York State? | New York State | answered | yes | yes |
| At what amount must a New York State agency's discretionary purchase be advertised in the NYS Contract Reporter? | New York State | answered | yes | yes |
| Above what amount do New York State agency contracts generally need prior approval from the State Comptroller? | New York State | answered | yes | yes |
| What is the order of priority a New York State agency must follow when choosing how to buy something? | New York State | answered | yes | yes |
| Who are New York State's preferred sources? | New York State | answered | yes | yes |
| When can a New York State agency run an SDVOB set-aside procurement? | New York State | answered | yes | yes |
| What is the NYC micropurchase limit for goods and services? | New York City | answered | yes | yes |
| What is the NYC micropurchase limit for construction? | New York City | answered | yes | yes |
| What is the New York City small purchase limit? | New York City | answered | yes | yes |
| What is the NYC M/WBE noncompetitive small purchase limit? | New York City | answered | yes | yes |
| For an NYC small purchase above the micropurchase limit, how many vendors must be solicited from the bidders list? | New York City | answered | yes | yes |
| How many price quotes must an NYC contracting officer try to get for an M/WBE small purchase? | New York City | answered | yes | yes |
| Can New York City agencies use the M/WBE small purchase method for human services? | New York City | answered | yes | yes |
| Below what amount can CUNY use informal purchasing methods for general purchases? | CUNY | answered | yes | yes |
| How many written quotes does CUNY require for purchases between $20,000 and $50,000? | CUNY | answered | yes | yes |
| Up to what amount can CUNY use informal purchasing methods when buying from a certified SDVOB? | CUNY | answered | yes | yes |
| When does CUNY consider a price fair and reasonable? | CUNY | answered | yes | yes |
| What is the federal micro-purchase threshold? | Federal | answered | yes | yes |
| What is the federal simplified acquisition threshold? | Federal | answered | yes | yes |
| When can a federal contracting officer set aside an acquisition for SDVOSB concerns? | Federal | answered | yes | yes |
| What is the largest sole-source award a federal agency can make to an SDVOSB for a requirement outside manufacturing? | Federal | answered | yes | yes |
| What share of a business must service-disabled veterans own and control for SBA SDVOSB certification? | Federal | answered | yes | yes |
| How often must a certified SDVOSB recertify with SBA? | Federal | answered | yes | yes |
| Under SBA's VetCert rules, can the spouse or permanent caregiver of a veteran with a permanent and total disability control an SDVOSB? | Federal | answered | yes | yes |
| What is the micro-purchase threshold for California state agencies? | Other | not_found | yes | n/a |
| What is the public bidding threshold for New Jersey counties? | Other | not_found | yes | n/a |
| What hourly rate should a software developer charge New York City? | Other | not_found | yes | n/a |

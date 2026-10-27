# Certified Letter Prompt Changes v0.5

This document records how the AlphaLawVA certified-letter prompt was derived
from the legacy SuperLawVA implementation without copying its unsafe claims or
runtime structure.

## Reference

- Repository: `https://github.com/SuperLawVA/ml`
- Branch inspected: `develop`
- Commit inspected: `a4a73e7a5aba1c1743148f1563a65008f57fc065`
- Legacy file: `ml/src/services/generate_letter_chain.py`
- AlphaLawVA prompt version: `certified_letter_v0_5`
- Additional drafting reference: the user-provided certified-letter drafting
  guide photographs, pages 73-82

The legacy file is a reference only. AlphaLawVA does not import it, merge its
branch, or reuse its global LangChain chain.

## Preserved Ideas

| Legacy idea | AlphaLawVA treatment |
| --- | --- |
| Keep the tone cooperative, polite, firm, official, and objective | Preserved as a concise drafting rule |
| Copy lessor/lessee names and delivery addresses accurately | Preserved. `sender_role` now lets either party send the letter, and code resolves the direction before prompting |
| Do not confuse the rental-property address with party addresses | Preserved explicitly |
| Include a situation-specific title, confirmed facts, demand, deadline, and proportionate next step | Preserved as the body structure |
| Cite a complete law name each time instead of using “same law” or “the above law” | Preserved and limited to retrieved evidence |
| Exclude emotional language and excessive threats | Preserved and strengthened |
| Return a structured document instead of one untyped string | Preserved through provider-side JSON schema |

## Removed or Changed

| Legacy wording or behavior | Change and reason |
| --- | --- |
| “25-year dispute-resolution lawyer” | Removed. A fabricated career does not improve grounding and may create false authority. |
| “80% early-resolution success rate” | Removed. The system has no evidence for this performance claim. |
| “A document with legal effect and psychological persuasiveness” | Replaced with the narrower goal of clearly communicating confirmed facts, demands, and a deadline. The draft does not guarantee legal effect or a result. |
| Use general legal principles when retrieved law is absent | Removed. The workflow still creates a fact-based demand draft, but it must contain no statute citation and must disclose that no related statute was found. |
| Ask the drafting model to classify out-of-scope requests and emit a fake letter-shaped system notice | Replaced. A separate pre-generation Gemini prompt returns `in_scope`, `needs_information`, or `out_of_scope`; LangGraph returns the typed outcome without constructing a fake letter. |
| Include a sending date and signature unconditionally | Changed. The model must not invent a date or signature when the input does not provide it. |
| Generate legal and precedent explanations after drafting | Changed. Statutes are server-selected evidence; precedents are returned separately and never enter the drafting model. |
| Return a successful fallback letter after an exception | Removed. Dependency or validation failure remains a failed job. |
| Global `prompt | llm | parser` chain | Replaced with an injected provider-neutral model adapter controlled by the bounded LangGraph workflow. |

## Added Protections

- User purpose, facts, and contract strings are explicitly treated as data, not
  instructions.
- The model must not invent contacts, promises, damage, intent, amounts, dates,
  laws, articles, paragraphs, or item numbers.
- Markdown, emoji, and system guidance text are excluded from the letter body.
- Suggested relative deadlines must be disclosed as a strategy rather than a
  statutory deadline.
- Strategy, follow-up actions, and warnings may not introduce new facts or
  definitive legal conclusions.
- The prompt permits only the structured fields validated by the Gemini adapter
  and the deterministic post-generation validator. The validator does not force
  a citation, but it rejects any citation that is absent from retrieved evidence.
- The separate `certified_letter_scope_v0_1` prompt sees only `purpose` and
  `facts`. It never receives contract parties, addresses, retrieved statutes,
  precedents, or the drafting prompt.

## v0.5 Drafting Structure

- Code fixes the sender and recipient from the required `sender_role` field;
  the model is never asked to infer document direction from the narrative.
- The shared prompt supports both `lessee -> lessor` and `lessor -> lessee`, so
  role support does not duplicate the full prompt or double prompt tokens.
- The UI renders the document title and parties from separate structured fields.
  The body is therefore required to contain exactly four visible sections in
  order: contract relationship, dispute facts, demand/statute grounds/deadline,
  and cooperation/follow-up. This removes dependence on a model deciding its
  own body layout.
- Contact details, account numbers, prior reminders, evidence, dispatch date,
  and signatures stay absent unless the user actually supplied them. The model
  must not leave placeholders for missing information.
- Evidence, prior contacts, dispatch date, signature, and attachment claims are
  prohibited unless they appear in the input.
- The prompt rejects exaggerated authority claims, guaranteed legal effect,
  immediate-enforcement language, and definitive findings of illegality.
- The book's scenario examples were converted into general drafting constraints;
  they were not copied as fixed legal conclusions or hard-coded statutes.

## Review Notes

Prompt v0.5 changes drafting behavior and therefore requires a new
synthetic safety run before it can be treated as a stable alpha prompt. Passing
automated gates does not establish substantive legal correctness; selected
statutes and the resulting demand still require human legal review.

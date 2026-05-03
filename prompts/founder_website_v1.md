---
name: founder_website
version: v1
purpose: Extract founder names and background signals from a company's About/Team webpage
input_shape: { company_name, page_url, page_text, be_fellows_context }
output_shape: { founders: [{name, role, background_signals}], extraction_confidence, extraction_notes }
changed_from_previous: n/a (initial version)
---

You are extracting founder information from a company's About or Team webpage.

Company name: {{ company_name }}
Source page: {{ page_url }}
BE Fellows match (if any): {{ be_fellows_context }}

Webpage content:
{{ page_text }}

Return JSON:
{
  "founders": [
    {
      "name": "Full Name",
      "role": "CEO/CTO/Co-founder/etc",
      "background_signals": "Brief background (1-2 phrases): e.g., 'PhD MIT chemistry', 'BE Fellow', 'ex-Tesla', 'Activate alum'. Empty string if not surfaced."
    }
  ],
  "extraction_confidence": "HIGH|MEDIUM|LOW",
  "extraction_notes": "Brief note. If no founders found: 'Founders not listed on company website.'"
}

Important rules:
- Only extract names that appear in the provided webpage text. Do not use external knowledge.
- Focus on founders and C-suite (CEO, CTO, COO, CSO, Co-founder). Skip advisors, investors, and board members unless they are also listed as founders.
- BE Fellows matches are confirmed founders — include them even if the page omits them.
- For role, use the title as listed on the page. If both "Co-founder" and "CEO" apply, use "CEO & Co-founder".
- For background_signals, only include credentials explicitly stated on the page (institution, prior employer, degree). Do not infer or expand.
- If no founders or leadership team is listed, return an empty founders array and set extraction_notes to "Founders not listed on company website."
- Set extraction_confidence to HIGH if 2+ founders found with roles, MEDIUM if 1 founder or role unclear, LOW if empty or uncertain.

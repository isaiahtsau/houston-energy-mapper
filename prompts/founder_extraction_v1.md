---
name: founder_extraction
version: v1
purpose: Extract founder names and background signals from company profile text
input_shape: { name, description, summary, reasoning, be_fellows_context }
output_shape: { founders: [{name, role, background_signals}], extraction_confidence, extraction_notes }
changed_from_previous: n/a (initial version)
---

You are extracting founder information from a company's public profile.

Given the following company data, extract founder names and brief background signals where they appear in the provided text.

Company name: {{ name }}
Description: {{ description }}
Summary: {{ summary }}
Reasoning context: {{ reasoning }}
BE Fellows match (if any): {{ be_fellows_context }}

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
  "extraction_notes": "Brief note. If no founders found: 'Names not surfaced from harvested sources.'"
}

Important rules:
- Only extract names you can ground in the provided text. Do not hallucinate names from external knowledge.
- BE Fellows matches are confirmed names — include them in the founders list with role from BE Fellows data.
- For non-BE-Fellows founders, the name must appear in the description, summary, or reasoning text.
- If multiple roles for one person, pick the most senior (e.g., "CEO & Co-founder" rather than just "Co-founder").
- Be conservative on background_signals — only include claims grounded in the text.
- If no founders are found in the text and there are no BE Fellows matches, return an empty founders array and set extraction_notes to "Names not surfaced from harvested sources."

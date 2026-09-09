# Presentation slides

The course requires the slides **in PDF format**. The submitted deck is:

- [`Group8_CPS_Presentation.pdf`](Group8_CPS_Presentation.pdf) — 22 pages, the required PDF.
- [`Group8_CPS_Presentation.pptx`](Group8_CPS_Presentation.pptx) — editable PowerPoint source.

Slides 1–16 are the talk itself; slides 17–22 are backup material (sources and
acknowledgements, vision and voice, BOM and power, test coverage, timeline, printable parts).
The backup slides are hidden in the PowerPoint source, so they are exported explicitly:

```bash
soffice --headless \
  --convert-to 'pdf:impress_pdf_Export:{"ExportHiddenSlides":{"type":"boolean","value":"true"}}' \
  --outdir docs/slides docs/slides/Group8_CPS_Presentation.pptx
```

The demonstration video embedded in the PowerPoint source is not duplicated inside the PDF.
It is published separately as [`docs/video/TEAM8_AiLamp_Combined_Demo.mp4`](../video/TEAM8_AiLamp_Combined_Demo.mp4).

Suggested 15-minute structure:

1. Problem and idea (1 min)
2. System overview — hardware and software architecture (2 min)
3. What is upstream LeLamp vs. what Group 8 built (2 min)
4. Mechanical work: redesigned base, print-ready parts (2 min)
5. Perception → behaviour pipeline, voice integration (3 min)
6. Live demo / video (3 min)
7. Results, limitations, lessons learned (2 min)

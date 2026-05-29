---
name: frontend-design
description: Create distinctive, production-grade frontend interfaces with high design quality.
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [design, html, css, frontend, ui, ux, prototype, landing-page, creative, artifact]
    related_skills: [claude-design, popular-web-designs, design-md, generative-widgets]
    fallback_for_toolsets: []
---

# Frontend Design

Use this skill when the user asks to build a web component, page, application, or interface —
a landing page, dashboard, prototype, widget, or any HTML/CSS/JS artifact.

Related skills:
- **`claude-design`** — design *process and taste* (scoping, variants, verification, avoiding slop).
  Load alongside this skill when the workflow is complex.
- **`popular-web-designs`** — 54 real-world design systems (Stripe, Linear, Vercel, etc.).
  Load when the user wants a known brand's visual vocabulary.
- **`design-md`** — formal DESIGN.md token spec files. Load when the deliverable is a spec, not a page.
- **`generative-widgets`** — serve the result via cloudflared tunnel. Load when the user wants to share.

## Design Thinking

Before writing code, understand the context and commit to a BOLD aesthetic direction:

- **Purpose**: What problem does this interface solve? Who uses it?
- **Tone**: Pick an extreme: brutally minimal, maximalist chaos, retro-futuristic, organic/natural,
  luxury/refined, playful/toy-like, editorial/magazine, brutalist/raw, art deco/geometric,
  soft/pastel, industrial/utilitarian, etc. Use these for inspiration but design one that is
  true to the aesthetic direction.
- **Constraints**: Technical requirements (framework, performance, accessibility).
- **Differentiation**: What makes this UNFORGETTABLE? What's the one thing someone will remember?

**CRITICAL**: Choose a clear conceptual direction and execute it with precision. Bold maximalism
and refined minimalism both work — the key is intentionality, not intensity.

Then implement working code (HTML/CSS/JS, React, Vue, etc.) that is:
- Production-grade and functional
- Visually striking and memorable
- Cohesive with a clear aesthetic point-of-view
- Meticulously refined in every detail

## Frontend Aesthetics Guidelines

**Typography**: Choose fonts that are beautiful, unique, and interesting. Avoid generic fonts
like Arial and Inter; opt instead for distinctive choices. Pair a distinctive display font with
a refined body font. Use Google Fonts CDN `<link>` tags.

**Color & Theme**: Commit to a cohesive aesthetic. Use CSS variables for consistency. Dominant
colors with sharp accents outperform timid, evenly-distributed palettes.

**Motion**: Use animations for effects and micro-interactions. Prioritize CSS-only solutions.
Focus on high-impact moments: one well-orchestrated page load with staggered reveals
(`animation-delay`) creates more delight than scattered micro-interactions. Use scroll-triggering
and hover states that surprise.

**Spatial Composition**: Unexpected layouts. Asymmetry. Overlap. Diagonal flow. Grid-breaking
elements. Generous negative space OR controlled density.

**Backgrounds & Visual Details**: Create atmosphere and depth rather than defaulting to solid
colors. Add contextual effects and textures: gradient meshes, noise textures, geometric patterns,
layered transparencies, dramatic shadows, decorative borders, custom cursors, grain overlays.

**NEVER use generic AI-generated aesthetics**: overused font families (Inter, Roboto, Arial,
system fonts), cliched color schemes (particularly purple gradients on white backgrounds),
predictable layouts and component patterns, cookie-cutter design that lacks context-specific
character.

**NO TWO DESIGNS SHOULD BE THE SAME.** Vary between light and dark themes, different fonts,
different aesthetics. Never converge on common choices across generations.

**Matching complexity to vision**: Maximalist designs need elaborate code with extensive
animations and effects. Minimalist designs need restraint, precision, and careful attention
to spacing, typography, and subtle details. Elegance comes from executing the vision well.

## Hermes Workflow

### Step 1: Gather context

```
read_file(file_path="AGENTS.md")          # project context and conventions
read_file(file_path="SOUL.md")             # personality and user preferences
```

### Step 2: Research references

```
web_search(query="<site> design system colors typography")
```

Load `popular-web-designs` if the user wants a known brand's visual vocabulary:
```
skill_view(name="popular-web-designs", file_path="templates/<site>.md")
```

### Step 3: Build the artifact

Write a single self-contained HTML file (or a project scaffold):

```
write_file(file_path="~/projects/<name>/index.html", content="...")
```

The HTML file should be completely self-contained: inline CSS in `<style>`, Google Fonts via
`<link>`, no build step required. Open immediately in the browser to verify.

### Step 4: Verify

```
browser_navigate(url="file:///home/user/projects/<name>/index.html")
browser_snapshot()          # inspect rendered DOM
browser_vision()            # visual inspection
```

### Step 5: Iterate

Use `patch` for targeted fixes:
```
patch(file_path="~/projects/<name>/index.html", old_string="...", new_string="...")
```

Or `write_file` for full rewrites. Re-verify with browser tools after each change.

### Step 6: Deliver

The HTML file is the primary deliverable. For sharing, load `generative-widgets` to serve it.
For a formal token spec, load `design-md` and export the values.

## HTML Template

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Artifact Title</title>
  <!-- Google Fonts -->
  <link href="https://fonts.googleapis.com/css2?family=..." rel="stylesheet">
  <style>
    :root {
      /* Design tokens */
      --bg: ...;
      --text: ...;
      --accent: ...;
    }
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    /* Page load stagger */
    @keyframes fadeIn { from { opacity: 0; transform: translateY(20px); } to { opacity: 1; transform: translateY(0); } }
    .stagger { animation: fadeIn 0.6s ease-out both; }
    .stagger:nth-child(1) { animation-delay: 0.1s; }
    .stagger:nth-child(2) { animation-delay: 0.2s; }
    .stagger:nth-child(3) { animation-delay: 0.3s; }
    /* ... more design ... */
  </style>
</head>
<body>
  <!-- Artifact content -->
</body>
</html>
```

## Pitfalls

- **Purple-on-white gradient hero**: the most overused AI design pattern. Avoid.
- **Inter/Roboto/Arial**: overused. Use distinctive fonts from Google Fonts.
- **No verification**: always open the file in the browser with `browser_navigate` and inspect.
- **Broken on mobile**: always add `viewport` meta tag and test at 375px width via browser resize.
- **Static only**: if animation is part of the vision, include it. CSS `@keyframes` are free.
- **Forgetting dark mode**: if the design is dark, commit to it fully. No light-mode fallback
  unless the user asked for both.
- **Skipping the brief**: always spend 2-3 messages clarifying the aesthetic direction before
  writing code when the user's request is vague.

## Verification

1. Open the file: `browser_navigate(url="file:///home/user/projects/<name>/index.html")`
2. Inspect: `browser_snapshot()` — confirm all elements rendered
3. Visual check: `browser_vision()` — confirm colors, layout, typography match intent
4. Resize: resize browser window to 375px and re-inspect for mobile
5. Font check: confirm Google Fonts loaded (no fallback to system font)

The design is done when: the browser vision check matches the aesthetic brief, all motion
works, fonts are loaded, and the page is responsive.

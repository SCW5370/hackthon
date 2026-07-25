---
name: Motion Gate Operations Console
description: Evidence-first control surface for an autonomous laboratory line
colors:
  canvas: "#F4F6F2"
  surface: "#FFFFFF"
  ink: "#17201B"
  muted: "#647069"
  border: "#D7DDD8"
  primary: "#176B4D"
  primary-deep: "#0F513A"
  warning: "#A85D12"
  danger: "#B42318"
  info: "#2459A9"
  soft-green: "#E7F3EC"
  soft-red: "#FBEAE8"
  soft-amber: "#FFF3DF"
typography:
  headline:
    fontFamily: "Inter, PingFang SC, system-ui, sans-serif"
    fontSize: "clamp(1.25rem, 2vw, 1.75rem)"
    fontWeight: 700
    lineHeight: 1.2
  title:
    fontFamily: "Inter, PingFang SC, system-ui, sans-serif"
    fontSize: "1rem"
    fontWeight: 650
    lineHeight: 1.35
  body:
    fontFamily: "Inter, PingFang SC, system-ui, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: "IBM Plex Mono, SFMono-Regular, monospace"
    fontSize: "0.75rem"
    fontWeight: 600
    lineHeight: 1.35
    letterSpacing: "0.02em"
rounded:
  sm: "6px"
  md: "10px"
  lg: "16px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "16px"
  lg: "24px"
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.surface}"
    rounded: "{rounded.sm}"
    padding: "10px 16px"
  button-primary-hover:
    backgroundColor: "{colors.primary-deep}"
    textColor: "{colors.surface}"
    rounded: "{rounded.sm}"
    padding: "10px 16px"
  card:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    padding: "16px"
---

# Design System: Motion Gate Operations Console

## Overview

**Creative North Star: "The Evidence Desk"**

Motion Gate should feel like a calm laboratory duty desk where every physical action has a visible cause, authorization decision, and consequence. Information is dense enough for a live demonstration but grouped by operator question rather than system component.

The design explicitly rejects the current neon cyberpunk treatment, decorative scanlines, and polling-driven flicker. Security is communicated through precise state changes and durable evidence, not visual noise.

**Key Characteristics:**

- Restrained light surfaces with one operational green accent.
- Monospace only for identifiers, reason codes, and event sequence numbers.
- Progressive disclosure for audit details and unsafe baseline controls.
- Motion limited to meaningful state transitions.

## Colors

The palette uses laboratory paper neutrals with measured semantic colors.

### Primary

- **Verified Green** (`#176B4D`): primary actions, healthy connected state, and completed work.

### Secondary

- **Decision Blue** (`#2459A9`): Motion Gate evaluation and informational state.

### Tertiary

- **Recovery Amber** (`#A85D12`): compromised sessions, pause pending, and recovery.
- **Blocked Red** (`#B42318`): denied actions and unsafe outcomes only.

### Neutral

- **Bench Canvas** (`#F4F6F2`): application background.
- **Instrument Surface** (`#FFFFFF`): cards and drawers.
- **Carbon Ink** (`#17201B`): primary text.
- **Calibrated Gray** (`#647069`): secondary text.
- **Hairline Border** (`#D7DDD8`): dividers and card outlines.

### Named Rules

**The Evidence Color Rule.** Red means an actually blocked or unsafe condition; it is never decorative.

## Typography

**Display Font:** Inter (with PingFang SC and system UI fallbacks)

**Body Font:** Inter (with PingFang SC and system UI fallbacks)

**Label/Mono Font:** IBM Plex Mono (with SFMono-Regular fallback)

**Character:** Neutral and operational. Type establishes hierarchy without oversized headings or theatrical letter spacing.

### Hierarchy

- **Headline** (700, `clamp(1.25rem, 2vw, 1.75rem)`, 1.2): product and current line state.
- **Title** (650, `1rem`, 1.35): panel titles and active task.
- **Body** (400, `0.875rem`, 1.5): explanations and task content.
- **Label** (600, `0.75rem`, `0.02em`): IDs, reason codes, counters, and event metadata.

### Named Rules

**The Identifier Rule.** Monospace is reserved for machine-readable evidence, never paragraphs.

## Elevation

The system is flat by default. Depth comes from surface color, one-pixel borders, and layout grouping; drawers may use a single soft shadow to indicate overlay.

### Shadow Vocabulary

- **Drawer** (`box-shadow: -16px 0 40px rgba(23, 32, 27, 0.14)`): audit and advanced control drawers only.

### Named Rules

**The Flat-by-Default Rule.** Cards do not glow or float at rest.

## Components

### Buttons

- **Shape:** compact rounded rectangle (`6px`).
- **Primary:** Verified Green on white, `10px 16px`.
- **Hover / Focus:** darker green hover and a visible two-pixel focus outline.
- **Secondary / Ghost:** white surface, hairline border, Carbon Ink text.

### Chips

- **Style:** pale semantic fill with readable dark text and a one-pixel border.
- **State:** always include a text label; color is supplementary.

### Cards / Containers

- **Corner Style:** `10px`.
- **Background:** Instrument Surface.
- **Shadow Strategy:** none at rest.
- **Border:** one-pixel Hairline Border.
- **Internal Padding:** `16px`.

### Inputs / Fields

- **Style:** white field, one-pixel border, `6px` radius.
- **Focus:** primary outline plus border shift.
- **Error / Disabled:** error text is explicit; disabled controls retain readable contrast.

### Navigation

The header is a compact persistent control strip. Audit and unsafe baseline tools live in labeled drawers rather than competing with production controls.

### Causal Trace

Trusted work order, untrusted input, Agent intent, Motion Gate decision, and recovery are five stable rows. A state update changes only the relevant row and never clears the trace.

## Do's and Don'ts

### Do:

- **Do** keep the current line state and allowed controls visible at all times.
- **Do** render untrusted input using `textContent`.
- **Do** append audit events by monotonically increasing `seq`.
- **Do** show a 1.5-second recovery state so the blocked action has a visible consequence.
- **Do** pair every semantic color with a written state.

### Don't:

- **Don't** make an information-heavy console where every module competes for attention.
- **Don't** clear and redraw the timeline every 800 milliseconds.
- **Don't** use neon cyberpunk, scanlines, glowing icons, or decorative grids.
- **Don't** show a rejected action as silent inactivity.
- **Don't** present voice input or Agent text as proof of administrator authority.

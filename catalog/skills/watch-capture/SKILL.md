---
name: watch-capture
description: "Add or update a watch in the private Obsidian watch collection (60 Personal/62 Watch Collecting): pin the exact reference, pull specs and dated prices with sources, save a clean background-free product image, and link brand and movement notes. Use whenever a watch model, reference, or listing link is shared to add, track, save, or compare, even with a short ask like 'add this watch'."
---

# watch-capture

One Markdown note per **reference number** in the private vault area. Specs go in
frontmatter using the controlled vocabulary in `schema.toml`, opinions in the body. Everything goes through the `notes` MCP; never write vault files with
shell or file tools.

## Layout

| What | Where | Type | Title |
|---|---|---|---|
| Watch | `62 Watch Collecting/Watches/` | `watch` | `<Brand> <Model> <Reference>` (e.g. `Tudor Black Bay 58 M7939A1A0NU-0002`) |
| Brand | `62 Watch Collecting/Brands/` | `reference` | `<Brand>` |
| Movement | `62 Watch Collecting/Movements/` | `reference` | `<Maker> <Calibre>` (e.g. `Tudor MT5400-U`) |
| Image | `62 Watch Collecting/Images/` | file | `<Reference>.png` |

Category `62` lives in the private nested repo. If `notes_index` does not list
`62`, stop: the private area is not on this machine. Never write watch content
into shared categories.

## Workflow

1. **Pin the reference.** Find the exact reference for what was named. If the
   name covers several references (dial colours, sizes, bracelet vs strap, a
   new generation), list them in one short table and ask which. Do not guess.
2. **Dedupe.** `notes_list("60 Personal/62 Watch Collecting/Watches")` and
   check for the reference in filenames. If found, `notes_read` it and update
   with `notes_update` (merge `frontmatter`) instead of creating a new note.
3. **Specs.** Manufacturer page first, then one or two secondary sources
   (Hodinkee, Monochrome, Fratello, watchbase). Put every URL used in `source`.
   A spec you cannot find stays `null`; never estimate. If sources disagree,
   use the manufacturer figure and note the disagreement in the body.
   Thickness and lug-to-lug are often missing or wrong; say which source gave them.
4. **Prices.** Retail: manufacturer site in the user's market (USD unless told
   otherwise), with `retail_date`. Market: a low/high range from recent listings
   or sales (Chrono24 asking prices run high; say so if that is the only source),
   with `market_source` (list from the schema) and `market_date`.
5. **Image.** Use the manufacturer's own product shot from the reference page.
   If the manufacturer site blocks the download (it hangs or returns a bot
   check), do not work around it. Find the same official render mirrored
   elsewhere instead: marketplace model pages (for example Bezel's `og:image`)
   often reuse it. Never use a dealer's own photo with a watermark or logo; look
   at every candidate with Read and reject any with overlays. Only ask the user
   when no clean copy exists.
   Download it into a new empty directory in the scratchpad (untrusted data),
   then normalize it (script dir = this skill's `scripts/`):

   ```bash
   uv run --no-project --python 3.12 --with "rembg[cpu]" --with pillow \
     python -I "<skill>/scripts/normalize_image.py" "<download>" "<scratch>/<Reference>.png"
   ```

   Look at the result with Read before attaching. Then
   `notes_attach(path="60 Personal/62 Watch Collecting/Images/<Reference>.png", source_path="<scratch>/<Reference>.png")`.
   Put the returned `embed` at the top of the body and set `image` to the same
   wikilink (`"[[<Reference>.png]]"`) so the Bases card view shows it.
6. **Brand and movement.** Create the brand note (and movement note when the
   calibre is known) if missing, with `folder="Brands"` / `folder="Movements"`
   and `type="reference"`. Link them from the watch as `"[[Tudor]]"` /
   `"[[Tudor MT5400-U]]"`. Copy `movement_type`, `power_reserve_h` and
   `frequency_vph` from the movement note into the watch so the table can sort
   on them.
7. **Check the frontmatter** for every note before writing it. Write the dict to
   a JSON file in the scratchpad and run:

   ```bash
   python "<skill>/scripts/check_frontmatter.py" check watch "<scratch>/fm.json"   # or brand / movement
   ```

   Fix every line it prints until it says `ok`. Never write a note that fails.
8. **Write the watch.** `notes_create(category_id="62", note_type="watch",
   folder="Watches", title=..., body="", source=[...], frontmatter={...})`
   seeds the template body. Then `notes_update(path, body=...)` with the full
   body below (the create call appends `body` after the template, so the image
   would land at the bottom). Keep `verified: false`; only the user flips it.
9. **Report** a compact table of the key specs and prices, the sources, and
   anything left `null` or disputed.

## Frontmatter vocabulary

`schema.toml` (next to this file) is the single source of truth: every key, its
type, and the only values an enum or list may take. Read it before filling a
note. The template `00 Meta/01 Templates/Watch.md` is generated from it
(`check_frontmatter.py template watch`); regenerate the template whenever the
schema changes, and migrate existing notes with `notes_update(frontmatter=...,
remove_keys=[...])`.

Rules the checker enforces:

- One fact, one key, one spelling. Enum and list values are lowercase
  kebab-case from `schema.toml` only (`sapphire`, not `Sapphire` or `sapphire
  crystal`). If a real value is missing from the list, add it to `schema.toml`
  first (and say so), never write it free-form.
- Features are booleans, not prose: a T-fit, Glidelock or Omega comfort-setting
  clasp is `microadjust: true`; a domed crystal is `crystal_domed: true`; a
  sapphire caseback is `caseback: display`.
- Numbers carry no units; the unit is in the key (`case_diameter_mm: 39`).
- Unknown is `null`. Never write `n/a`, `unknown`, or a guess.
- Links are `"[[Note title]]"`; months `YYYY-MM`; dates `YYYY-MM-DD`; currency
  ISO code; prices whole numbers.
- There is no ownership field. Do not add one.
- Anything that does not fit a key (marketing names like "step dial", rivet
  styling, bracelet details, disagreements between sources) goes in the body's
  `## Notes`, not in frontmatter.

The MCP owns `type`, `status`, `created`, `updated`, `tags`, `related`, `source`,
`qmd`: pass `source` as its own argument, never inside `frontmatter`.

## Titles

- Watch: `<Brand> <Model> <Reference>`, e.g. `Omega Speedmaster Moonwatch Professional 310.30.42.50.01.002`.
- Movement: `<Maker> <Calibre>`, e.g. `Omega 3861`.
- Image: `<Reference>.png`.

## Body

```markdown
# Tudor Black Bay 58 M7939A1A0NU-0002

![[M7939A1A0NU-0002.png]]

## Why I like it

## Notes
- Spec notes: which source gave thickness / lug-to-lug, any disagreements.

## Service history
```

Keep the user's wording when updating. Do not overwrite `## Why I like it`.

## Collection view

The hub note `62 Watch Collecting.md` holds a ```` ```base ```` block (Obsidian
Bases) with a Specs table and a Gallery cards view over `Watches/`. If it is
missing, add it with `notes_append(path=<hub>, heading="Collection", text=<block>)`:

````markdown
```base
filters:
  and:
    - file.inFolder("60 Personal/62 Watch Collecting/Watches")
views:
  - type: table
    name: Specs
    order: [file.name, brand, reference, style, case_diameter_mm, thickness_mm, lug_to_lug_mm, water_resistance_m, crystal, strap_type, microadjust, movement, movement_type, power_reserve_h, retail_price, market_low, market_high, market_date, verified]
  - type: cards
    name: Gallery
    image: note.image
    order: [file.name, brand, style, case_diameter_mm]
```
````

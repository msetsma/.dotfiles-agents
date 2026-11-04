---
name: watch-capture
description: "Add or update a watch in the private Obsidian watch collection (60 Personal/62 Watch Collecting): pin the exact reference, pull specs and dated prices with sources, save a clean background-free product image, and link brand and movement notes. Use whenever a watch model, reference, or listing link is shared to add, track, save, or compare, even with a short ask like 'add this watch'."
---

# watch-capture

One Markdown note per **reference number** in the private vault area. Specs go in
frontmatter (the schema comes from `00 Meta/01 Templates/Watch.md`), opinions in
the body. Everything goes through the `notes` MCP; never write vault files with
shell or file tools.

## Layout

| What | Where | Type | Title |
|---|---|---|---|
| Watch | `62 Watch Collecting/Watches/` | `watch` | `<Brand> <Model> <Reference>` (e.g. `Tudor Black Bay 58 M79030N`) |
| Brand | `62 Watch Collecting/Brands/` | `reference` | `<Brand>` |
| Movement | `62 Watch Collecting/Movements/` | `reference` | `<Maker> <Calibre>` (e.g. `Tudor MT5402`) |
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
   with `market_source` and `market_date`. Dates are `YYYY-MM`.
5. **Image.** Find a manufacturer press or product shot on a plain background.
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
   calibre is known) if missing, with `folder="Brands"` / `folder="Movements"`.
   Link them from the watch as `"[[Tudor]]"` / `"[[Tudor MT5402]]"`.
7. **Write the watch.** `notes_create(category_id="62", type="watch",
   folder="Watches", title=..., body="", source=[...], frontmatter={...})`
   seeds the template body. Then `notes_update(path, body=...)` with the full
   body below (the create call appends `body` after the template, so the image
   would land at the bottom). Keep `verified: false`; only the user flips it.
8. **Report** a compact table of the key specs and prices, the sources, and
   anything left `null` or disputed.

## Frontmatter

The template supplies every key with `null`; pass only what you found. The MCP
owns `type`, `status`, `created`, `updated`, `tags`, `related`, `source`, `qmd`;
pass `source` as its own argument and never inside `frontmatter`.

| Key | Example | Notes |
|---|---|---|
| `brand` | `"[[Tudor]]"` | wikilink to the brand note |
| `model` | `Black Bay 58` | |
| `reference` | `M79030N-0001` | exact, with suffix when the maker uses one |
| `style` | `diver` | one of `diver`, `dress`, `field`, `pilot`, `chronograph`, `gmt`, `sports`, `integrated-bracelet`, `racing`, `digital`; pick the maker's own category when it fits, else the closest |
| `ownership` | `wishlist` | `owned`, `wishlist`, `interested`, `sold`, `passed`; default `interested` |
| `release_year` | `2018` | |
| `case_diameter_mm`, `thickness_mm`, `lug_to_lug_mm`, `lug_width_mm` | `39`, `11.9`, `47.75`, `20` | numbers only |
| `case_material`, `bezel`, `dial`, `crystal`, `bracelet` | `steel`, `aluminium insert`, `black`, `sapphire`, `riveted steel` | short lowercase |
| `water_resistance_m` | `200` | |
| `movement` | `"[[Tudor MT5402]]"` | |
| `movement_type` | `automatic` | `automatic`, `manual`, `quartz`, `spring drive` |
| `power_reserve_h`, `frequency_vph` | `70`, `28800` | |
| `retail_price`, `retail_currency`, `retail_date` | `4400`, `USD`, `2026-11` | |
| `market_low`, `market_high`, `market_currency`, `market_source`, `market_date` | `3200`, `3700`, `USD`, `Chrono24 asking`, `2026-11` | dated snapshot |
| `image` | `"[[M79030N.png]]"` | |
| `verified` | `false` | |
| `rating`, `acquired`, `paid`, `sold` | | user fills these |

Brand note `frontmatter`: `kind: brand`, `country`, `founded`, `parent_company`,
`website`. Movement note: `kind: movement`, `maker`, `movement_type`,
`power_reserve_h`, `frequency_vph`, `jewels`, `certification`.

## Body

```markdown
# Tudor Black Bay 58 M79030N

![[M79030N.png]]

## Why I like it

## Notes
- Spec notes: which source gave thickness / lug-to-lug, any disagreements.

## Service history
```

Keep the user's wording when updating. Do not overwrite `## Why I like it`.

## Collection view

The hub note `62 Watch Collecting.md` holds a ```` ```base ```` block (Obsidian
Bases) with a table and a cards view over `Watches/`. If it is missing, add it
with `notes_append(path=<hub>, heading="Collection", text=<block>)`:

````markdown
```base
filters:
  and:
    - file.inFolder("60 Personal/62 Watch Collecting/Watches")
views:
  - type: table
    name: Specs
    order: [file.name, brand, reference, style, ownership, case_diameter_mm, thickness_mm, lug_to_lug_mm, water_resistance_m, movement, retail_price, market_low, market_high, market_date, verified]
  - type: cards
    name: Gallery
    image: note.image
    order: [file.name, brand, style, ownership, case_diameter_mm]
```
````

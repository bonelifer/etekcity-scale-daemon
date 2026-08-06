# Sample reports

These PDFs show what `etekcity-scale-report` produces for every sane combination of `[report]` config options, so you can see what a layout looks like before setting it up. They're all rendered from the same 14-reading fixture dataset.

## Layout × weight unit × date format

| File | Layout | Weight unit | Date format |
|---|---|---|---|
| [full-kg-world.pdf](full-kg-world.pdf) | full | kg | world |
| [full-kg-us.pdf](full-kg-us.pdf) | full | kg | us |
| [full-lb-world.pdf](full-lb-world.pdf) | full | lb | world |
| [full-lb-us.pdf](full-lb-us.pdf) | full | lb | us |
| [full-st-world.pdf](full-st-world.pdf) | full | st | world |
| [full-st-us.pdf](full-st-us.pdf) | full | st | us |
| [simple-kg-world.pdf](simple-kg-world.pdf) | simple | kg | world |
| [simple-kg-us.pdf](simple-kg-us.pdf) | simple | kg | us |
| [simple-lb-world.pdf](simple-lb-world.pdf) | simple | lb | world |
| [simple-lb-us.pdf](simple-lb-us.pdf) | simple | lb | us |
| [simple-st-world.pdf](simple-st-world.pdf) | simple | st | world |
| [simple-st-us.pdf](simple-st-us.pdf) | simple | st | us |

The `simple` layout is always date/weight only, regardless of `include_address`/`include_model`/`include_impedance` — those settings only affect the `full` layout.

## Column toggles (full layout only)

| File | include_address | include_model | include_impedance |
|---|---|---|---|
| [full-minimal-lb-us.pdf](full-minimal-lb-us.pdf) | no | no | no |

All other `full` layout samples above show every optional column (the default).

## Chart layout

| File | Layout | Weight unit | Date format |
|---|---|---|---|
| [chart-kg-world.pdf](chart-kg-world.pdf) | chart | kg | world |

A line chart of weight over time instead of a table. `include_address`/`include_model`/`include_impedance`/`include_heart_rate` have no effect on this layout.

## Regenerating

```bash
etekcity-scale-report --config /path/to/config.ini --output samples/<name>.pdf
```

See the main [README](../README.md#reports) for the full list of `[report]` options.

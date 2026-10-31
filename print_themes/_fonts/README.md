# Print fonts

Shared by every print theme (`fonts.css`, linked by `_base.html`). Open Font License (`OFL-*.txt`).

Each file is one weight with Latin and Latin Extended in the same file. Google Fonts' own web files split
those into two files by `unicode-range`, and WeasyPrint then drew wrong glyphs for ğ and ş.

Made from the variable fonts in github.com/google/fonts (`ofl/jost/Jost[wght].ttf`,
`ofl/cormorantgaramond/CormorantGaramond[wght].ttf` and `…-Italic[wght].ttf`), with fontTools
(`pip install fonttools brotli`):

```python
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer
from fontTools import subset

font = instancer.instantiateVariableFont(TTFont("Jost[wght].ttf"), {"wght": 300})
opts = subset.Options(); opts.flavor = "woff2"; opts.layout_features = ["*"]; opts.name_IDs = ["*"]
s = subset.Subsetter(opts)
s.populate(unicodes=list(range(0x20, 0x250)) + [0x2013, 0x2014, 0x2018, 0x2019, 0x201C, 0x201D,
                                                0x2022, 0x2026, 0x20BA, 0x00A0])
s.subset(font); font.flavor = "woff2"; font.save("jost-300.woff2")
```

Adding a font: same steps, then a line in `fonts.css`.

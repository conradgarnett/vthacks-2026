# Bundled typefaces

38 faces in `fonts/`, all from [Google Fonts](https://fonts.google.com).
They are committed rather than downloaded so the corpora render identically
on every machine — which is the only way a score from a Mac compares with a
score from a Windows laptop.

They are also deliberately awkward. Script, brush, marker, stencil and
engraved faces are what shopfronts, menus, packaging and cafe boards actually
use, and they are markedly harder than Helvetica. Cursive is over-represented
on purpose: it is both the commonest hard case in daily life — drinks,
cosmetics, bakeries, greeting cards — and the one OCR fails worst on.

Full licence texts: `fonts/LICENSE-OFL.txt`, `fonts/LICENSE-Apache.txt`.

## What the licences permit

Neither licence restricts **images rendered with the font**. The corpora this
repository generates are yours to use, publish and train on under this
repository's MIT licence, whichever faces produced them.

The restrictions apply to redistributing the **font files** themselves:

- **SIL OFL 1.1** — redistribute freely, bundled or standalone, provided the
  licence travels with them and they are not sold on their own. A modified
  face must be released under a different name.
- **Apache 2.0** — redistribute freely with the licence and attribution.

Both are satisfied by keeping `fonts/` intact, licence files included.

## The audit

Read from each file's OpenType `name` table (IDs 0, 1, 13, 14) rather than
from a web listing, so this reflects the bytes actually in this repository.
Reproduce it with `fontTools`:

```python
from fontTools.ttLib import TTFont
f = TTFont("fonts/Lobster-Regular.ttf")
print([str(r) for r in f["name"].names if r.nameID in (13, 14)])
```

| Family | File | Licence | Copyright |
|---|---|---|---|
| Alex Brush | AlexBrush-Regular.ttf | OFL-1.1 | 2011 The Alex Brush Project Authors |
| Allura | Allura-Regular.ttf | OFL-1.1 | 2010 The Allura Project Authors |
| Amatic SC | AmaticSC-Regular.ttf | OFL-1.1 | 2015 The Amatic SC Project Authors |
| Anton | Anton-Regular.ttf | OFL-1.1 | 2020 The Anton Project Authors |
| Bangers | Bangers-Regular.ttf | OFL-1.1 | 2010 The Bangers Project Authors |
| Bebas Neue | BebasNeue-Regular.ttf | OFL-1.1 | 2019 The Bebas Neue Project Authors |
| Black Ops One | BlackOpsOne-Regular.ttf | OFL-1.1 | 2022 (see note below) |
| Caveat | Caveat-Variable.ttf | OFL-1.1 | 2014 The Caveat Project Authors |
| Cinzel Decorative | CinzelDecorative-Regular.ttf | OFL-1.1 | 2012 Natanael Gama |
| Cookie | Cookie-Regular.ttf | OFL-1.1 | 2011 Ania Kruk |
| Courgette | Courgette-Regular.ttf | OFL-1.1 | 2012 Sorkin Type Co |
| Creepster | Creepster-Regular.ttf | OFL-1.1 | 2011 Font Diner, Inc |
| Dancing Script | DancingScript-Variable.ttf | OFL-1.1 | 2016 The Dancing Script Project Authors |
| Great Vibes | GreatVibes-Regular.ttf | OFL-1.1 | 2010 The Great Vibes Pro Project Authors |
| Indie Flower | IndieFlower-Regular.ttf | OFL-1.1 | 2010 The Indie Flower Authors |
| Kaushan Script | KaushanScript-Regular.ttf | OFL-1.1 | 2011 Pablo Impallari |
| Lato | Lato-Regular.ttf | OFL-1.1 | 2011–2015 tyPoland Lukasz Dziedzic |
| Lobster | Lobster-Regular.ttf | OFL-1.1 | 2010 The Lobster Project Authors |
| Lora | Lora-Variable.ttf | OFL-1.1 | 2011 The Lora Project Authors |
| Marck Script | MarckScript-Regular.ttf | OFL-1.1 | 2011 Denis Masharov, Marck Fogel |
| **Monoton** | Monoton-Regular.ttf | **none embedded** | 2011 vernon adams |
| Montserrat | Montserrat-Variable.ttf | OFL-1.1 | 2011 The Montserrat Project Authors |
| Open Sans | OpenSans-Variable.ttf | OFL-1.1 | 2020 The Open Sans Project Authors |
| Oswald | Oswald-Variable.ttf | OFL-1.1 | 2016 The Oswald Project Authors |
| PT Serif | PT_Serif-Web-Regular.ttf | OFL-1.1 | 2010 ParaType Ltd |
| Pacifico | Pacifico-Regular.ttf | OFL-1.1 | 2018 The Pacifico Project Authors |
| Parisienne | Parisienne-Regular.ttf | OFL-1.1 | 2012 Brian J. Bonislawsky (Astigmatic) |
| Patrick Hand | PatrickHand-Regular.ttf | OFL-1.1 | 2012 Patrick Wagesreiter |
| Permanent Marker | PermanentMarker-Regular.ttf | Apache-2.0 | 2010 Font Diner, Inc |
| Playfair Display | PlayfairDisplay-Variable.ttf | OFL-1.1 | 2017 The Playfair Display Project Authors |
| Poppins | Poppins-Regular.ttf | OFL-1.1 | 2020 The Poppins Project Authors |
| Raleway | Raleway-Variable.ttf | OFL-1.1 | 2010 The Raleway Project Authors |
| Righteous | Righteous-Regular.ttf | OFL-1.1 | 2011 Brian J. Bonislawsky (Astigmatic) |
| Rock Salt | RockSalt-Regular.ttf | Apache-2.0 | 2010 Font Diner, Inc DBA Sideshow |
| Sacramento | Sacramento-Regular.ttf | OFL-1.1 | 2012 Brian J. Bonislawsky (Astigmatic) |
| Satisfy | Satisfy-Regular.ttf | Apache-2.0 | 2011 Font Diner, Inc |
| Special Elite | SpecialElite-Regular.ttf | Apache-2.0 | 2010 Brian J. Bonislawsky (Astigmatic) |
| **Tangerine** | Tangerine-Regular.ttf | **none embedded** | 2010 Toshi Omagari |

**32 OFL-1.1 · 4 Apache-2.0 · 2 with no licence record in the file.**

## The two unverified faces

`Monoton-Regular.ttf` and `Tangerine-Regular.ttf` carry a copyright string
and a trademark string but **no** licence description (name ID 13) and no
licence URL (name ID 14). Both are distributed on Google Fonts, which hosts
only OFL, Apache 2.0 and UFL faces, so neither is proprietary — but the file
itself does not say which, and this document will not guess on your behalf.

If you redistribute this repository, that is very likely fine. If you need
certainty — you are shipping a product, or your legal review asks — download
those two afresh from Google Fonts, where the licence file ships in the same
archive, and keep it. Or drop them: `generators/fonts.py` reads whatever is
in `fonts/`, so removing a face costs you two samples of the font benchmark
and breaks nothing.

## One upstream oddity

`BlackOpsOne-Regular.ttf` names "The PinyonScript Project Authors" in its
copyright string. That is a copy-paste error in the upstream Google Fonts
source, not a mistake in this repository or a sign the wrong file is here —
the family name, PostScript name and glyphs are all Black Ops One. Its
licence record is a normal OFL-1.1.

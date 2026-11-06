"""vim's digraphs (RFC 1345): insert mode ctrl+k then two characters types one special
character, e.g. ctrl+k a : → ä, ctrl+k - > → →, ctrl+k a * → α. Like vim, the two
characters may also be typed the other way round. :digraphs lists them all.

Imported on the first ctrl+k, so start-up does not pay for the table.
"""

from __future__ import annotations

import functools

_ACCENTS = {
    "!": "̀",
    "'": "́",
    ">": "̂",
    "?": "̃",
    "-": "̄",
    "(": "̆",
    ".": "̇",
    ":": "̈",
    "0": "̊",
    '"': "̋",
    "<": "̌",
    ",": "̧",
    ";": "̨",
}

_GREEK = "a α b β g γ d δ e ε z ζ y η h θ i ι k κ l λ m μ n ν c ξ o ο p π r ρ s σ t τ u υ f φ x χ q ψ w ω"

_SYMBOLS = """
-> → <- ← -! ↑ -v ↓ <> ↔ UD ↕ => ⇒ == ⇔
FA ∀ dP ∂ TE ∃ /0 ∅ DE ∆ NB ∇ (- ∈ -) ∋ *P ∏ +Z ∑ -2 − -+ ∓ *- ∗ Ob ∘ Sb ∙ RT √ 0( ∝ 00 ∞
-L ∟ -V ∠ PP ∥ AN ∧ OR ∨ (U ∩ )U ∪ In ∫ DI ∬ Io ∮ .: ∴ :. ∵ :R ∶ :: ∷ ?1 ∼ CG ∾ ?- ≃ ?=
≅ ?2 ≈ =? ≌ HI ≓ != ≠ =3 ≡ =< ≤ >= ≥ <* ≪ *> ≫ !< ≮ !> ≯ (C ⊂ )C ⊃ (_ ⊆ )_ ⊇ 0. ⊙ 02 ⊚
-T ⊥ .P ⋅ :3 ⋮ .3 …
*X × -: ÷ +- ± DG ° My µ 12 ½ 14 ¼ 34 ¾ 1S ¹ 2S ² 3S ³ 0S ⁰ 4S ⁴ 5S ⁵ 6S ⁶ 7S ⁷ 8S ⁸ 9S ⁹
+S ⁺ -S ⁻ nS ⁿ iS ⁱ 0s ₀ 1s ₁ 2s ₂ 3s ₃ 4s ₄ 5s ₅ 6s ₆ 7s ₇ 8s ₈ 9s ₉
Co © Rg ® TM ™ SE § PI ¶ Eu € Pd £ Ye ¥ Ct ¢ Cu ¤ << « >> » !I ¡ ?I ¿
-N – -M — -1 ‐ -3 ― '6 ‘ '9 ’ .9 ‚ "6 “ "9 ” :9 „ /- † /= ‡ .. ‥ ,. … %0 ‰ 1' ′ 2' ″ 3' ‴
oC ℃ oF ℉ No № Om Ω AO Å ss ß ae æ AE Æ o/ ø O/ Ø oe œ OE Œ d- ð D- Ð th þ TH Þ i. ı
ij ĳ IJ Ĳ kk ĸ L. Ŀ l. ŀ 'n ŉ NG Ŋ ng ŋ s* σ *s ς
OK ✓ XX ✗ -X ✠ cH ♥ cD ♦ cS ♠ cC ♣ Md ♩ M8 ♪ M2 ♫ Mb ♭ Mx ♮ Mz ♯ *1 ☆ *2 ★ SU ☼
:) ☺ :( ☹ hh ─ vv │ 3- ┄ 3_ ┅ 3! ┆ 3/ ┇ 4- ┈ 4_ ┉ 4! ┊ 4/ ┋ dr ┌ dl ┐ ur └ ul ┘ vr ├ vl ┤
dh ┬ uh ┴ vh ┼ OS □ fS ■ Db ◆ Dw ◇ 0m ○ 0M ● PR ▶ PL ◀ UT ▲ Dt ▼
"""


@functools.cache
def table() -> dict[str, str]:
    """Two typed characters → the character they stand for."""
    out: dict[str, str] = {}
    import unicodedata

    for base in "aeiouyAEIOUYcCnNsSzZgGrRlLtTdDhHjJkKwW":
        for mark, combining in _ACCENTS.items():
            composed = unicodedata.normalize("NFC", base + combining)
            if len(composed) == 1:
                out[base + mark] = composed
    for letter, char in zip(_GREEK.split()[0::2], _GREEK.split()[1::2]):
        out[letter + "*"] = char
        out[letter.upper() + "*"] = char.upper()
    words = _SYMBOLS.split()
    for pair, char in zip(words[0::2], words[1::2]):
        out[pair] = char
    out["NS"], out["SH"], out["Sp"] = " ", "­", " "
    out["aa"], out["AA"] = "å", "Å"
    return out


def lookup(first: str, second: str) -> str:
    """The digraph for `first` `second` (either order), or `second` itself as vim does."""
    digraphs = table()
    return digraphs.get(first + second) or digraphs.get(second + first) or second

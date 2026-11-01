"""Template filters available in every print theme (render.py loads this as a builtin).

    {{ t.welcome|caps:lang }}    uppercase with the language's rules: Turkish i -> İ, ı -> I

Use it for every text shown in capitals. CSS `text-transform: uppercase` alone is not enough: WeasyPrint
ignores `lang` and makes "Hoş geldiniz" -> "HOŞ GELDINIZ" in the PDF (browsers get it right). A theme may keep
text-transform in its CSS; on text that is already uppercase it changes nothing.
"""
from django import template

register = template.Library()


@register.filter
def caps(value, lang="tr"):
    text = str(value)
    if lang == "tr":
        text = text.replace("i", "İ").replace("ı", "I")
    return text.upper()

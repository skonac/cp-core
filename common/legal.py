"""KVKK notice texts served by the API. DRAFT placeholders (cp-web's mock wording) until the
legal texts are approved; bump VERSION when the text changes."""
VERSION = "draft-1"
EFFECTIVE_AT = "2026-10-01T00:00:00Z"

BIOMETRIC_CONSENT = {
    "tr": {
        "title": "Yüz Verilerinin İşlenmesine İlişkin Bilgilendirme",
        "summary": "Bu taslak, selfienin fotoğraflarını bulmak için nasıl kullanılacağını açıklar. "
                   "Yayın öncesinde onaylı hukuki metinle değiştirilecektir.",
        "sections": [
            {"id": "purpose", "title": "İşleme amacı",
             "body": "Selfien yalnızca katıldığın etkinliklerde bulunabileceğin fotoğrafları belirlemek amacıyla işlenir."},
            {"id": "scope", "title": "Kapsam ve saklama",
             "body": "Yüz temsiline organizatörler veya diğer misafirler erişemez. Saklama süreleri nihai hukuki metinde belirtilecektir."},
            {"id": "rights", "title": "Tercihlerin",
             "body": "Hesabını sildiğinde selfien ve yüz verin de silinir."},
        ],
    },
    "en": {
        "title": "Facial Data Processing Notice",
        "summary": "This draft explains how your selfie is used to find your photographs. "
                   "Final legal text will replace it before launch.",
        "sections": [
            {"id": "purpose", "title": "Purpose",
             "body": "Your selfie is processed only to identify photographs in which you may appear within events you have joined."},
            {"id": "scope", "title": "Scope and retention",
             "body": "Your facial representation is not shown to organizers or other guests. Retention periods will be stated in the final legal notice."},
            {"id": "rights", "title": "Your choices",
             "body": "Deleting your account deletes your selfie and facial data."},
        ],
    },
}

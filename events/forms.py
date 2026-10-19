from django import forms

from .models import Event


class EventForm(forms.ModelForm):
    """Create/edit an event. Event.clean() adds the 'no face processing needs browse-all' rule."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("label_suffix", "")   # no "Name:" colons
        super().__init__(*args, **kwargs)

    class Meta:
        model = Event
        fields = ["name", "type", "date", "city", "venue",
                  "is_processing_faces", "is_browse_all", "is_consent_required"]
        widgets = {"date": forms.DateInput(attrs={"type": "date"})}
        labels = {
            "venue": "Venue (place name, no street address)",
            "is_processing_faces": "Find faces (participants get “my photos”)",
            "is_browse_all": "Participants may browse all photos",
            "is_consent_required": "Require KVKK consent to enter",
        }

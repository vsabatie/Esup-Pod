from django.contrib import admin
from django.contrib.sites.models import Site
from django.contrib.sites.shortcuts import get_current_site

from .models import Dressing
from pod.video.models import Video
from .forms import DressingForm


class DressingAdmin(admin.ModelAdmin):
    form = DressingForm
    list_display = ("title", "watermark", "opacity", "position", "opening_credits", "ending_credits")
    autocomplete_fields = [
        "owners",
        "users",
        "allow_to_groups",
        "opening_credits",
        "ending_credits",
    ]

    class Media:
        css = {
            "all": (
                # "bootstrap/dist/css/bootstrap.min.css",
                # "bootstrap/dist/css/bootstrap-grid.min.css",
                # "css/pod.css",
            )
        }
        js = (
            "js/main.js",
            "podfile/js/filewidget.js",
            "bootstrap/dist/js/bootstrap.min.js",
        )


admin.site.register(Dressing, DressingAdmin)